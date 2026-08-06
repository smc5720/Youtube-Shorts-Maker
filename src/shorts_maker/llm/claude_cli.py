"""로컬 `claude` CLI를 헤드리스(`-p`)로 부르는 adapter — MVP 기본 provider (스파이크 #1).

**격리 플래그가 이 모듈의 존재 이유의 절반이다.** 프로젝트 디렉터리에서 그냥 `claude -p`를
부르면 `CLAUDE.md`·스킬 목록·툴 정의·MCP 설정이 프롬프트에 실린다. 실측으로 `"1+1은?"`
한 줄이 $0.071이었고, 아래 플래그를 붙이면 같은 호출이 $0.004다 (스파이크 2.1).
비용이 한 자릿수 배로 갈리는 것을 호출자가 매번 기억해야 하는 형태로 두지 않는다 —
`ISOLATION_FLAGS`는 명령줄 조립에 무조건 들어가고, 이 클래스에는 그것을 끄거나 덮어쓸
인자가 없다.

**스키마 강제는 CLI가 한다.** `--json-schema`를 주면 CLI가 검증까지 마친 값을 응답의
`structured_output`에 넣어 준다. 스파이크 4.3에서 33회 전부 성공했지만 표본이 작아
드문 실패를 배제하지 못하므로 실패 경로는 남긴다.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .provider import LOGGER, LLMError, LLMResult

PROVIDER_NAME = "claude_cli"
DEFAULT_BINARY = "claude"

ISOLATION_FLAGS: tuple[str, ...] = (
    "--safe-mode",
    "--tools",
    "",
    "--disable-slash-commands",
    "--strict-mcp-config",
    "--setting-sources",
    "",
    "--no-session-persistence",
)
"""프로젝트 컨텍스트를 벗겨내는 플래그 (스파이크 2.1). 하나라도 빠지면 비용이 튄다.

`--tools`와 `--setting-sources`의 빈 문자열은 값이다 — "아무것도 없음"을 넘긴다.
"""

_STDERR_EXCERPT = 400
"""오류 메시지에 붙일 stderr 길이. 전문을 넣으면 로그가 사용할 수 없게 길어진다."""


@dataclass(frozen=True)
class CommandResult:
    """외부 명령 1회의 결과."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """subprocess 경계. 테스트가 여기를 가짜로 바꿔 끼워 CLI 없이 전체를 돈다.

    시간 초과는 `subprocess.TimeoutExpired`로 알린다 — 이 계약을 자체 예외로 바꾸면
    기본 구현과 가짜 구현이 서로 다른 경로를 타서 매핑을 검증할 수 없다.
    """

    def __call__(self, command: Sequence[str], *, timeout_sec: int) -> CommandResult: ...


def run_command(command: Sequence[str], *, timeout_sec: int) -> CommandResult:
    """`subprocess.run`으로 명령을 실행한다.

    `timeout`을 넘기면 `subprocess.run`이 자식 프로세스를 kill하고 `TimeoutExpired`를
    던진다 — 시간이 지난 CLI가 뒤에 남아 토큰을 더 쓰지 않는다.
    """
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # 모델 출력이 깨져도 파싱 오류로 원인을 보여주는 편이 낫다
        timeout=timeout_sec,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class ClaudeCliProvider:
    """`claude -p` 1회 호출 = `complete_json` 1회.

    생성자 인자는 아래 넷이 전부다. **명령줄에 무언가를 더 넣는 인자를 추가하지 않는다** —
    그 순간 격리 플래그를 우회하는 길이 열린다 (`tests/test_llm.py`가 인자 목록을 고정한다).
    """

    name = PROVIDER_NAME

    def __init__(
        self,
        *,
        model: str,
        binary: str = DEFAULT_BINARY,
        timeout_sec: int = 600,
        runner: CommandRunner = run_command,
    ) -> None:
        self.model = model
        self.binary = binary
        self.timeout_sec = timeout_sec
        self._runner = runner

    def complete_json(
        self, *, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> LLMResult:
        command = self.build_command(system=system, prompt=prompt, schema=schema)

        started = time.monotonic()
        try:
            completed = self._runner(command, timeout_sec=self.timeout_sec)
        except subprocess.TimeoutExpired as error:
            # 재시도하지 않는다. 이미 timeout_sec만큼 기다렸고 같은 조건에서 다시 부르면
            # 사용자 대기 시간만 배가된다. 한도가 좁으면 llm.timeout_sec를 올린다.
            raise LLMError(
                f"claude CLI가 {self.timeout_sec}초 안에 끝나지 않아 프로세스를 종료했다. "
                f"llm.timeout_sec를 확인한다",
                retryable=False,
            ) from error
        except FileNotFoundError as error:
            raise LLMError(
                f"claude CLI를 실행할 수 없다: {self.binary!r}를 찾을 수 없다. "
                f"llm.providers.claude_cli.binary를 확인한다",
                retryable=False,
            ) from error
        latency_ms = int((time.monotonic() - started) * 1000)

        envelope = self._envelope(completed)
        self._check_success(envelope, completed)

        output = envelope.get("structured_output")
        if not isinstance(output, dict):
            raise LLMError(
                "응답에 스키마를 만족하는 structured_output이 없다 — 모델이 스키마를 "
                f"맞추지 못했다. 받은 값: {output!r}"
            )

        # CLI가 보고한 API 소요 시간. latency_ms와의 차이가 프로세스 기동 오버헤드이며,
        # 호출 횟수를 줄일 가치가 있는지(스파이크 3장) 판단하는 근거가 된다.
        LOGGER.debug(
            "claude CLI api=%sms wall=%dms 세션=%s",
            envelope.get("duration_ms"),
            latency_ms,
            envelope.get("session_id"),
        )

        # `result`는 모델이 낸 원문이다. 구조화 출력을 쓰면 대개 structured_output과 같은
        # 내용이지만, 다르게 나오는 경우를 감사할 수 있도록 손대지 않고 그대로 남긴다.
        reported_raw = envelope.get("result")
        if not isinstance(reported_raw, str):
            reported_raw = json.dumps(output, ensure_ascii=False)

        return LLMResult(
            data=output,
            raw=reported_raw,
            model=_resolved_model(envelope.get("modelUsage")),
            cost_usd=_cost(envelope.get("total_cost_usd")),
            latency_ms=latency_ms,
        )

    def build_command(
        self, *, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> list[str]:
        """실행할 명령줄. 테스트가 이 결과를 직접 검사한다.

        인자를 리스트로 넘기고 셸을 거치지 않으므로, 프롬프트에 `--tools all` 같은 문자열이
        들어 있어도 플래그로 해석되지 않는다.
        """
        return [
            self.binary,
            "-p",
            prompt,
            "--model",
            self.model,
            *ISOLATION_FLAGS,
            # 기본 시스템 프롬프트를 대체한다. --append-system-prompt를 쓰면 벗겨낸
            # 기본 프롬프트가 다시 실린다.
            "--system-prompt",
            system,
            "--json-schema",
            json.dumps(schema, ensure_ascii=False),
            "--output-format",
            "json",
        ]

    def _envelope(self, completed: CommandResult) -> dict[str, Any]:
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise LLMError(
                f"claude CLI 응답이 JSON이 아니다 (종료 코드 {completed.returncode}) — {error}. "
                f"stdout: {completed.stdout[:_STDERR_EXCERPT]!r} "
                f"stderr: {completed.stderr[:_STDERR_EXCERPT]!r}"
            ) from error

        if not isinstance(envelope, dict):
            raise LLMError(f"claude CLI 응답의 최상위가 매핑이 아니다: {envelope!r}")
        return envelope

    def _check_success(self, envelope: dict[str, Any], completed: CommandResult) -> None:
        if envelope.get("is_error") or envelope.get("subtype") != "success":
            reason = envelope.get("api_error_status") or envelope.get("result") or ""
            raise LLMError(
                f"claude CLI 호출이 실패했다 (종료 코드 {completed.returncode}, "
                f"subtype={envelope.get('subtype')!r}) — {str(reason)[:_STDERR_EXCERPT]}"
            )


def _resolved_model(usage: Any) -> str:
    """`modelUsage` 키에서 실제 모델 ID를 읽는다.

    CLI 응답에 모델 ID를 담는 자리는 여기뿐이다. 없으면 우리가 아는 모양의 응답이
    아니므로 실패로 본다 — 요청한 별칭(`opus`)을 대신 기록하면 run.log가 조용히
    거짓을 남긴다. `structured_output`이 없을 때와 같은 처리다.
    """
    if not isinstance(usage, dict) or not usage:
        raise LLMError(f"응답에 modelUsage가 없어 실제 모델 ID를 확인할 수 없다: {usage!r}")

    if len(usage) > 1:
        # 폴백 모델처럼 여러 모델이 섞이면 출력 토큰이 가장 많은 쪽을 응답의 주인으로 본다.
        LOGGER.warning("응답에 모델이 여럿이다: %s", ", ".join(sorted(usage)))

    return max(usage, key=lambda model: _output_tokens(usage[model]))


def _output_tokens(entry: Any) -> int:
    if isinstance(entry, dict) and isinstance(entry.get("outputTokens"), int):
        return entry["outputTokens"]
    return 0


def _cost(reported: Any) -> float | None:
    """`total_cost_usd`를 읽는다. 보고하지 않으면 `None`이다 — 0.0으로 채우지 않는다."""
    if isinstance(reported, bool) or not isinstance(reported, (int, float)):
        return None
    return float(reported)


def create(*, model: str, options: Mapping[str, Any], timeout_sec: int) -> ClaudeCliProvider:
    """`llm.providers.claude_cli` 섹션에서 adapter를 만든다. 레지스트리가 부른다."""
    binary = options.get("binary") or DEFAULT_BINARY
    return ClaudeCliProvider(model=model, binary=str(binary), timeout_sec=timeout_sec)
