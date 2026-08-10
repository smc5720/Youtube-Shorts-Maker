"""테스트 공용 픽스처.

**어떤 테스트도 실제 LLM이나 TTS를 부르지 않는다.** LLM은 호출 1회에 수 초와 수 센트가
들고, TTS는 비공식 엔드포인트로 나간다 (스파이크 #2 5.1). 결과가 네트워크·인증 상태에
따라 흔들리면 회귀가 아니라 엔드포인트 상태를 측정하게 된다.

`test_llm.py`·`test_tts.py`는 subprocess와 스트림 경계를 가짜로 바꾸지만, 여기서는 그보다
한 단계 위인 **provider 레지스트리**를 바꾼다. 응답 봉투를 흉내 낼 필요 없이 파이프라인이
실제로 쓰는 값만 만들면 되기 때문이다. 재시도·캐시·로그는 `RetryingProvider`와
`SpeechSynthesizer`가 그대로 씌우므로 그 경로도 함께 돈다.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from shorts_maker.config import SPEC, Setting
from shorts_maker.llm import registry
from shorts_maker.llm.claude_cli import PROVIDER_NAME
from shorts_maker.llm.provider import LLMResult
from shorts_maker.tts import registry as tts_registry
from shorts_maker.tts import speech as speech_module
from shorts_maker.tts.edge_tts import PROVIDER_NAME as TTS_PROVIDER_NAME

STUB_MODEL = "stub-model-1"
"""응답이 보고하는 실제 모델 ID. 요청한 별칭(`opus`)과 달라야 로그 검증이 의미가 있다."""


class StubLLM:
    """등록된 provider를 대신하는 가짜.

    기본 동작은 **요청받은 JSON Schema를 만족하는 값을 만드는 것**이다 — claude CLI가
    `--json-schema`로 하는 일과 같다 (스파이크 2.2). 덕분에 호출부 테스트가 문제 수 같은
    값을 매번 손으로 맞추지 않아도 되고, 넘긴 스키마가 실제로 쓸 수 있는 모양인지도
    함께 검증된다.

    특정 응답이 필요하면 `reply()`로 미리 넣는다. 넣은 것은 순서대로 소비되고, 바닥나면
    다시 스키마에서 만든 값을 돌려준다.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        """호출 1회당 `{"system", "prompt", "schema", "model", "role"}`."""

        self._replies: list[Any] = []

    def reply(self, *payloads: Any) -> None:
        """다음 호출들이 돌려줄 값. `LLMError`를 넣으면 그 호출이 실패한다."""
        self._replies.extend(payloads)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def factory(self, *, model: str, options: Mapping[str, Any], timeout_sec: int) -> StubLLM:
        """`ProviderFactory` 자리에 끼워지는 생성자. 인스턴스를 공유해 호출을 한곳에 모은다."""
        self.model = model
        self.options = options
        self.timeout_sec = timeout_sec
        return self

    # --- LLMProvider ------------------------------------------------------

    name = PROVIDER_NAME
    model = "stub"

    def complete_json(
        self, *, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> LLMResult:
        self.calls.append(
            {"system": system, "prompt": prompt, "schema": schema, "model": self.model}
        )

        payload = self._replies.pop(0) if self._replies else from_schema(schema)
        if isinstance(payload, BaseException):
            raise payload

        return LLMResult(
            data=payload,
            raw="stub",
            model=STUB_MODEL,
            cost_usd=0.0,
            latency_ms=1,
        )


def from_schema(schema: Mapping[str, Any], name: str = "") -> Any:
    """JSON Schema를 만족하는 값 하나를 만든다. 필수 필드만 채운다."""
    kinds = schema.get("type")
    kind = kinds[0] if isinstance(kinds, list) else kinds

    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object":
        properties = schema.get("properties", {})
        return {
            key: from_schema(properties[key], key) for key in schema.get("required", properties)
        }
    if kind == "array":
        item = schema.get("items", {})
        return [from_schema(item, name) for _ in range(max(schema.get("minItems", 1), 1))]
    if kind == "integer" or kind == "number":
        return int(schema.get("minimum", 1))
    if kind == "boolean":
        return True

    text = f"{name} 값".strip() if name else "값"
    limit = schema.get("maxLength")
    return text[:limit] if limit is not None else text


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubLLM]:
    """등록된 provider를 `StubLLM`으로 바꾼다. 실제 claude CLI를 부르지 않는다."""
    stub = StubLLM()
    monkeypatch.setitem(registry.BUILTIN_PROVIDERS, PROVIDER_NAME, stub.factory)  # type: ignore[arg-type]
    yield stub


STUB_SEGMENT_SEC = 2.5
"""가짜 ffprobe가 보고할 세그먼트 길이. 실측 경계에서 온 값임을 알아볼 수 있는 값이다."""


class StubTTS:
    """등록된 TTS provider를 대신하는 가짜. 네트워크로 나가지 않는다.

    실제 오디오가 아닌 바이트를 쓰므로 길이 측정(`ffprobe`)도 함께 가짜로 바꿔야 한다 —
    두 경계는 `SpeechSynthesizer` 안에서 붙어 있다.
    """

    name = TTS_PROVIDER_NAME
    supports_word_timings = False

    def __init__(self) -> None:
        self.calls: list[str] = []
        """합성한 문장. 재실행이 무엇을 다시 합성했는지 이 목록이 답한다."""

        self.voice = "stub-voice"
        self.error: BaseException | None = None
        """지정하면 모든 합성이 이것으로 실패한다."""

    def synthesize(self, text: str, destination: Path) -> None:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        destination.write_bytes(b"stub-audio")
        return None

    def factory(self, *, voice: str, options: Mapping[str, Any], timeout_sec: int) -> StubTTS:
        self.voice = voice
        return self

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def stub_tts(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[StubTTS]:
    """등록된 TTS provider와 길이 측정을 가짜로 바꾼다.

    캐시 경로도 함께 옮긴다. 기본값 `.cache/tts`는 **실행 디렉터리 기준 상대 경로**라
    (PRD 7.5.2) 그대로 두면 테스트가 저장소에 가짜 오디오를 쌓는다. `tmp_path` 밑에
    두지 않는 이유는 그 디렉터리가 테스트에서 `--out`으로 쓰이기 때문이다 — 캐시가
    run 디렉터리 옆에 생기면 run 개수를 세는 쪽이 그것까지 센다.
    """
    stub = StubTTS()
    monkeypatch.setitem(tts_registry.BUILTIN_PROVIDERS, TTS_PROVIDER_NAME, stub.factory)  # type: ignore[arg-type]
    monkeypatch.setattr(speech_module.subprocess, "run", _fake_ffprobe)
    monkeypatch.setitem(
        SPEC["tts"],
        "cache_dir",
        Setting(str(tmp_path_factory.mktemp("tts-cache")), "str", nullable=True),
    )
    yield stub


def _fake_ffprobe(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, f"{STUB_SEGMENT_SEC}\n", "")
