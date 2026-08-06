"""LLM provider 계약 — 모든 adapter가 지키는 형태와, 모든 호출이 지나는 재시도·기록.

스파이크 #1 5장의 스케치를 그대로 구현한다. 결정 세 가지가 이 모듈의 형태를 정한다.

- **진입점은 `complete_json` 하나다.** 자유 텍스트용 `complete_text()`를 만들지 않는다.
  #9·#10·#13이 전부 구조화 출력을 요구하므로, JSON을 유일한 입구로 두면 새 adapter가
  스키마 강제를 각자 해결하도록 강제된다. 자유 텍스트가 실제로 필요한 타입이 생기면
  그때 추가한다.
- **`cost_usd`는 `None`을 허용한다.** claude CLI는 비용을 보고하지만 로컬 실행
  (Ollama 등)은 보고할 것이 없다. 필수로 만들면 그런 adapter가 거짓말을 하게 된다.
- **재시도와 호출 기록은 adapter가 아니라 `RetryingProvider`가 한다.** adapter마다
  구현하면 새 adapter가 조용히 빠뜨린다. `registry.provider_for_role()`이 항상 이
  래퍼를 씌워 돌려주므로 호출자도 adapter도 신경 쓸 것이 없다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .. import PACKAGE_LOGGER

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.llm")
"""`shorts_maker` 로거의 자식이라 `run_logging`이 붙인 핸들러로 그대로 흘러간다."""


class LLMError(Exception):
    """LLM 호출이 실패했다.

    `retryable`이 실패의 성질을 구분한다. 모델이 스키마를 못 맞추거나 응답 형태가
    이상한 것은 다시 부르면 달라질 수 있지만, 바이너리가 없거나 타임아웃을 넘긴 것은
    같은 조건에서 같은 결과가 나온다 — 후자를 재시도하면 사용자 대기 시간만
    `max_retries + 1`배가 된다.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class LLMResult:
    """호출 1회의 결과."""

    data: dict[str, Any]
    """스키마를 통과한 파싱 결과."""

    raw: str
    """모델이 낸 원문. 디버깅·감사용이며 파이프라인이 파싱하지 않는다."""

    model: str
    """응답이 보고한 **실제 모델 ID**. `opus` 같은 별칭이 아니다.

    별칭은 시간이 지나면 다른 모델을 가리킨다. run.log만 보고 "이 문제를 만든 게
    무엇인가"에 답하려면 해석된 값이 필요하다.
    """

    cost_usd: float | None
    """provider가 보고한 비용. 보고하지 않으면 `None`."""

    latency_ms: int
    """호출 시작부터 결과를 받기까지의 실측 벽시계 시간.

    provider가 보고하는 API 소요 시간이 아니다. CLI adapter는 호출당 수 초의 프로세스
    기동 오버헤드를 가지며(스파이크 #1 3장), 그것도 파이프라인이 실제로 기다리는 시간이다.
    """


class LLMProvider(Protocol):
    """LLM 호출 1회를 담당한다. vendor 중립 계약이다."""

    name: str
    """provider 이름 (`llm.generator.provider`에 쓰는 값)."""

    model: str
    """요청한 모델. 별칭일 수 있다 — 해석된 값은 `LLMResult.model`이다."""

    def complete_json(
        self, *, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> LLMResult:
        """`schema`를 만족하는 JSON을 받아 온다.

        스키마 강제는 adapter의 책임이다. 만족하는 결과를 만들지 못하면 `LLMError`를
        던진다 — 반쪽짜리 dict를 돌려주지 않는다.
        """
        ...


@dataclass(frozen=True)
class RetryingProvider:
    """adapter를 감싸 재시도하고 호출 1회를 로그에 남긴다.

    실패를 삼키지 않는다. `max_retries`를 다 쓰면 마지막 원인을 포함한 `LLMError`를
    던진다 — 호출자가 "몇 번 시도했고 왜 실패했나"를 예외 메시지만 보고 알 수 있어야 한다.
    """

    inner: LLMProvider
    max_retries: int

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    def complete_json(
        self, *, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> LLMResult:
        attempts = self.max_retries + 1
        last_error: LLMError | None = None
        tried = 0

        for attempt in range(1, attempts + 1):
            tried = attempt
            try:
                result = self.inner.complete_json(system=system, prompt=prompt, schema=schema)
            except LLMError as error:
                last_error = error
                if not error.retryable:
                    LOGGER.warning("LLM 호출 실패 (%d/%d, 재시도하지 않는다): %s", attempt, attempts, error)
                    break
                LOGGER.warning("LLM 호출 실패 (%d/%d): %s", attempt, attempts, error)
                continue

            # 이 한 줄이 "왜 이 문제가 나왔지"에 답하는 근거다 (모델 ID·비용·지연).
            LOGGER.info(
                "LLM 호출 provider=%s 모델=%s 비용=%s 지연=%dms 시도=%d/%d",
                self.name,
                result.model,
                _format_cost(result.cost_usd),
                result.latency_ms,
                attempt,
                attempts,
            )
            return result

        raise LLMError(
            f"{self.name} 호출이 {tried}회 시도 후 실패했다 — 마지막 원인: {last_error}",
            retryable=False,
        ) from last_error


def _format_cost(cost_usd: float | None) -> str:
    return "미보고" if cost_usd is None else f"${cost_usd:.4f}"
