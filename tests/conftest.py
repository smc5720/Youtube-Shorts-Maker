"""테스트 공용 픽스처.

**어떤 테스트도 실제 LLM을 부르지 않는다.** 호출 1회에 수 초와 수 센트가 들고, 결과가
네트워크·인증 상태에 따라 흔들리면 회귀를 잡지 못한다 (`test_llm.py`와 같은 이유).

`test_llm.py`는 subprocess 경계를 가짜로 바꾸지만, 여기서는 그보다 한 단계 위인 **provider
레지스트리**를 바꾼다. claude CLI 응답 봉투를 흉내 낼 필요 없이 파이프라인이 실제로 쓰는
값(스키마를 만족하는 dict)만 만들면 되기 때문이다. 재시도·호출 기록은 `RetryingProvider`가
그대로 씌우므로 그 경로도 함께 돈다.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from shorts_maker.llm import registry
from shorts_maker.llm.claude_cli import PROVIDER_NAME
from shorts_maker.llm.provider import LLMResult

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
