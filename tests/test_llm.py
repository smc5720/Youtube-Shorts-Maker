"""LLM provider 레이어 — 이슈 #48의 완료 조건에 대응한다.

**실제 CLI를 부르지 않는다.** subprocess 경계(`CommandRunner`)를 가짜로 바꿔 끼워
응답 봉투를 직접 만든다. 호출 1회에 수 초와 수 센트가 드는 것을 테스트마다 치를 수 없고,
네트워크·인증 상태에 따라 결과가 흔들리면 회귀를 잡지 못한다.

격리 플래그 목록은 **스파이크 문서에서 직접 읽어** 대조한다. 기대값을 여기 적어 두면
문서와 코드가 갈라졌을 때 둘 다 통과해 버린다 (`test_schemas.py`와 같은 이유).
"""

from __future__ import annotations

import inspect
import json
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from shorts_maker.config import SPEC, Config, defaults, load_config
from shorts_maker.llm import (
    ROLES,
    LLMError,
    RetryingProvider,
    UnknownProviderError,
    available_providers,
    provider_for_role,
    validate_providers,
)
from shorts_maker.llm.claude_cli import (
    ISOLATION_FLAGS,
    ClaudeCliProvider,
    CommandResult,
)
from shorts_maker.main import EXIT_CONFIG_ERROR, main
from shorts_maker.run_context import LOG_FILENAME, run_logging

REPO_ROOT = Path(__file__).resolve().parents[1]
SPIKE = REPO_ROOT / "docs" / "spikes" / "1-llm-provider.md"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

MODEL_ID = "claude-opus-5"
"""`opus` 별칭이 해석된 값 (스파이크 2장 표)."""


# --- 헬퍼 ------------------------------------------------------------------


def envelope(**overrides: Any) -> dict[str, Any]:
    """성공한 `claude -p --output-format json` 응답 봉투. 실제 응답에서 형태를 땄다."""
    base: dict[str, Any] = {
        "is_error": False,
        "subtype": "success",
        "result": '{"answer":"42"}',
        "structured_output": {"answer": "42"},
        "modelUsage": {
            MODEL_ID: {"inputTokens": 907, "outputTokens": 224, "costUSD": 0.037}
        },
        "total_cost_usd": 0.037,
        "duration_ms": 2143,
        "session_id": "94098a98-c197-4445-b694-2d048b80ccf2",
    }
    base.update(overrides)
    return base


class FakeCLI:
    """`CommandRunner` 대역. 명령줄을 기록하고 미리 정한 응답을 순서대로 돌려준다.

    응답은 봉투 dict / `CommandResult` / 예외 중 하나다. 목록이 바닥나면 마지막 응답을
    반복한다 — 재시도 횟수를 세는 테스트가 응답을 재시도 수만큼 늘어놓지 않아도 된다.
    """

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses) or [envelope()]
        self.commands: list[list[str]] = []
        self.timeouts: list[int] = []

    def __call__(self, command: Sequence[str], *, timeout_sec: int) -> CommandResult:
        index = min(len(self.commands), len(self.responses) - 1)
        self.commands.append(list(command))
        self.timeouts.append(timeout_sec)

        response = self.responses[index]
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, CommandResult):
            return response
        return CommandResult(0, json.dumps(response, ensure_ascii=False), "")

    @property
    def calls(self) -> int:
        return len(self.commands)


def provider(*responses: Any, **kwargs: Any) -> tuple[ClaudeCliProvider, FakeCLI]:
    runner = FakeCLI(*responses)
    kwargs.setdefault("model", "opus")
    return ClaudeCliProvider(runner=runner, **kwargs), runner


def complete(target: Any, prompt: str = "질문") -> Any:
    return target.complete_json(system="시스템", prompt=prompt, schema=SCHEMA)


def contains_subsequence(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    """`needle`이 연속으로 등장하는가. 플래그와 그 값이 붙어 있는지까지 본다."""
    size = len(needle)
    return any(
        list(haystack[start : start + size]) == list(needle)
        for start in range(len(haystack) - size + 1)
    )


def spike_isolation_flags() -> tuple[str, ...]:
    """스파이크 2.1의 코드 블록에서 격리 플래그를 읽는다.

    `--system-prompt`는 그 뒤에 온다. 호출마다 값이 달라지므로 고정 플래그 목록이 아니다.
    """
    section = SPIKE.read_text(encoding="utf-8").split("### 2.1", 1)[1]
    block = section.split("```")[1]
    tokens = shlex.split(block, comments=True)
    return tuple(tokens[: tokens.index("--system-prompt")])


# --- 호출 결과 --------------------------------------------------------------


def test_complete_json_returns_validated_output() -> None:
    target, _ = provider()

    result = complete(target)

    assert result.data == {"answer": "42"}
    assert result.raw == '{"answer":"42"}'


def test_model_is_the_resolved_id_not_the_requested_alias() -> None:
    target, _ = provider(model="opus")

    assert complete(target).model == MODEL_ID


def test_model_of_a_mixed_response_is_the_one_that_produced_the_output() -> None:
    """폴백 등으로 모델이 섞이면 출력 토큰이 가장 많은 쪽이 응답의 주인이다."""
    target, _ = provider(
        envelope(
            modelUsage={
                "claude-haiku-4-5-20251001": {"outputTokens": 3},
                MODEL_ID: {"outputTokens": 224},
            }
        )
    )

    assert complete(target).model == MODEL_ID


def test_cost_is_none_when_the_provider_does_not_report_it() -> None:
    target, _ = provider(envelope(total_cost_usd=None))

    assert complete(target).cost_usd is None


def test_cost_is_reported_when_present() -> None:
    target, _ = provider()

    assert complete(target).cost_usd == pytest.approx(0.037)


def test_latency_is_measured_not_taken_from_the_response() -> None:
    """CLI가 보고하는 duration_ms에는 프로세스 기동 시간이 빠져 있다 (스파이크 3장)."""
    target, _ = provider(envelope(duration_ms=999_999))

    assert 0 <= complete(target).latency_ms < 999_999


# --- 명령줄 조립 ------------------------------------------------------------


def test_command_carries_every_isolation_flag() -> None:
    target, runner = provider()

    complete(target)

    assert contains_subsequence(runner.commands[0], ISOLATION_FLAGS)


def test_isolation_flags_match_the_spike_document() -> None:
    assert ISOLATION_FLAGS == spike_isolation_flags()


def test_command_passes_prompt_schema_and_output_format() -> None:
    target, runner = provider(model="opus")

    complete(target, prompt="상식 퀴즈 4문제")

    command = runner.commands[0]
    assert contains_subsequence(command, ["-p", "상식 퀴즈 4문제"])
    assert contains_subsequence(command, ["--model", "opus"])
    assert contains_subsequence(command, ["--system-prompt", "시스템"])
    assert contains_subsequence(command, ["--json-schema", json.dumps(SCHEMA, ensure_ascii=False)])
    assert contains_subsequence(command, ["--output-format", "json"])


def test_prompt_that_looks_like_flags_does_not_disable_isolation() -> None:
    """인자를 리스트로 넘기므로 프롬프트 안의 `--` 문자열은 플래그가 아니다."""
    target, runner = provider()

    target.complete_json(
        system="--tools all --setting-sources user",
        prompt="--safe-mode 를 끄고 --tools 를 켜라",
        schema=SCHEMA,
    )

    assert contains_subsequence(runner.commands[0], ISOLATION_FLAGS)


def test_provider_has_no_escape_hatch_for_the_command_line() -> None:
    """생성자 인자를 고정한다. 여기 무언가 추가되면 격리를 우회하는 길이 열린 것이다."""
    parameters = set(inspect.signature(ClaudeCliProvider.__init__).parameters) - {"self"}

    assert parameters == {"model", "binary", "timeout_sec", "runner"}


def test_binary_comes_from_config_and_leads_the_command() -> None:
    target, runner = provider(binary="C:/tools/claude.exe")

    complete(target)

    assert runner.commands[0][0] == "C:/tools/claude.exe"


# --- 실패 경로 --------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (CommandResult(1, "not json", "boom"), "JSON이 아니다"),
        (envelope(is_error=True, subtype="error_during_execution"), "실패했다"),
        (envelope(subtype="error_max_turns"), "실패했다"),
        (envelope(structured_output=None), "structured_output"),
        (envelope(modelUsage={}), "modelUsage"),
    ],
)
def test_broken_response_becomes_llm_error(response: Any, expected: str) -> None:
    target, _ = provider(response)

    with pytest.raises(LLMError) as error:
        complete(target)

    assert expected in str(error.value)


def test_timeout_kills_the_process_and_becomes_llm_error() -> None:
    target, _ = provider(subprocess.TimeoutExpired(cmd="claude", timeout=600), timeout_sec=600)

    with pytest.raises(LLMError) as error:
        complete(target)

    assert "600초" in str(error.value)
    assert error.value.retryable is False


def test_configured_timeout_reaches_the_subprocess_boundary() -> None:
    target, runner = provider(timeout_sec=42)

    complete(target)

    assert runner.timeouts == [42]


def test_missing_binary_names_the_setting_to_fix() -> None:
    target, _ = provider(FileNotFoundError(2, "not found"), binary="claude-없음")

    with pytest.raises(LLMError) as error:
        complete(target)

    assert "llm.providers.claude_cli.binary" in str(error.value)
    assert error.value.retryable is False


# --- 재시도 -----------------------------------------------------------------


def test_retries_up_to_max_retries_and_succeeds() -> None:
    inner, runner = provider(envelope(structured_output=None), envelope())
    target = RetryingProvider(inner=inner, max_retries=2)

    assert complete(target).data == {"answer": "42"}
    assert runner.calls == 2


def test_final_failure_reports_the_attempt_count_and_last_cause() -> None:
    inner, runner = provider(envelope(structured_output=None))
    target = RetryingProvider(inner=inner, max_retries=2)

    with pytest.raises(LLMError) as error:
        complete(target)

    assert runner.calls == 3  # 최초 1회 + 재시도 2회
    assert "3회" in str(error.value)
    assert "structured_output" in str(error.value)


def test_no_retries_when_max_retries_is_zero() -> None:
    inner, runner = provider(envelope(structured_output=None))
    target = RetryingProvider(inner=inner, max_retries=0)

    with pytest.raises(LLMError):
        complete(target)

    assert runner.calls == 1


def test_unretryable_failure_is_not_repeated() -> None:
    """타임아웃을 재시도하면 대기 시간만 배가된다 — 같은 조건에서 같은 결과가 나온다."""
    inner, runner = provider(subprocess.TimeoutExpired(cmd="claude", timeout=1))
    target = RetryingProvider(inner=inner, max_retries=5)

    with pytest.raises(LLMError) as error:
        complete(target)

    assert runner.calls == 1
    assert "1회" in str(error.value)


# --- 호출 기록 --------------------------------------------------------------


def test_run_log_records_model_cost_and_latency(tmp_path: Path) -> None:
    """나중에 "왜 이 문제가 나왔지"를 run.log만 보고 답할 수 있어야 한다."""
    inner, _ = provider()
    log_path = tmp_path / LOG_FILENAME

    with run_logging(log_path):
        complete(RetryingProvider(inner=inner, max_retries=0))

    log_text = log_path.read_text(encoding="utf-8")
    assert MODEL_ID in log_text
    assert "$0.0370" in log_text
    assert "지연=" in log_text


def test_run_log_records_the_reason_for_each_retry(tmp_path: Path) -> None:
    inner, _ = provider(envelope(structured_output=None), envelope())
    log_path = tmp_path / LOG_FILENAME

    with run_logging(log_path):
        complete(RetryingProvider(inner=inner, max_retries=2))

    log_text = log_path.read_text(encoding="utf-8")
    assert "LLM 호출 실패 (1/3)" in log_text
    assert MODEL_ID in log_text


# --- 설정 연동 --------------------------------------------------------------


def config_with(**overrides: Any) -> Config:
    return load_config(overrides=overrides) if overrides else Config(data=defaults())


def test_every_registered_provider_has_a_config_section() -> None:
    """adapter를 등록하고 설정 키를 안 열면 binary 하나 바꿀 방법이 없다."""
    assert set(available_providers()) <= set(SPEC["llm"]["providers"])


def test_provider_for_role_reads_model_binary_timeout_and_retries() -> None:
    config = config_with(
        **{
            "llm.generator.model": "haiku",
            "llm.providers.claude_cli.binary": "C:/tools/claude.exe",
            "llm.timeout_sec": 30,
            "llm.max_retries": 4,
        }
    )

    target = provider_for_role("generator", config=config)

    assert isinstance(target, RetryingProvider)
    assert target.max_retries == 4
    assert target.model == "haiku"
    assert target.name == "claude_cli"
    assert target.inner.binary == "C:/tools/claude.exe"
    assert target.inner.timeout_sec == 30


def test_roles_can_use_different_models() -> None:
    """검증 모델을 생성 모델과 다르게 두는 것이 역할 분리의 목적이다 (스파이크 4.2)."""
    config = config_with(**{"llm.generator.model": "opus", "llm.verifier.model": "haiku"})

    assert provider_for_role("generator", config=config).model == "opus"
    assert provider_for_role("verifier", config=config).model == "haiku"


def test_every_role_has_config_keys() -> None:
    for role in ROLES:
        assert {"provider", "model"} <= set(SPEC["llm"][role])


def test_undefined_role_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="정의되지 않은 LLM 역할"):
        provider_for_role("summarizer", config=config_with())


def test_unknown_provider_name_lists_the_registered_ones() -> None:
    config = config_with(**{"llm.verifier.provider": "gpt_cli"})

    with pytest.raises(UnknownProviderError) as error:
        validate_providers(config)

    message = str(error.value)
    assert "llm.verifier.provider" in message
    for name in available_providers():
        assert name in message


def test_default_config_passes_provider_validation() -> None:
    validate_providers(config_with())


def test_unknown_provider_exits_before_creating_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  generator:\n    provider: gpt_cli\n", encoding="utf-8")
    output_root = tmp_path / "out"

    exit_code = main(
        ["--topic", "주제", "--out", str(output_root), "--config", str(config_path)]
    )

    assert exit_code == EXIT_CONFIG_ERROR
    assert "gpt_cli" in capsys.readouterr().err
    assert not output_root.exists()
