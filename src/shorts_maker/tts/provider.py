"""TTS provider 계약 — 모든 adapter가 지키는 형태와, 모든 합성이 지나는 재시도·기록.

스파이크 #2 5.3이 뽑아 둔 다섯 항목이 이 모듈의 형태를 정한다. `llm/provider.py`와
같은 구조다 — 계약(Protocol) + 재시도 래퍼.

- **길이는 계약에 없다.** provider가 보고하는 값을 받지 않는다. 합성된 파일을 공통 코드가
  `ffprobe`로 재고(`speech.py`), 컨테이너 값과 디코딩 값의 차이가 0.008초로 확인됐다
  (스파이크 4.1). provider마다 보고 정확도가 다르므로 계약에 넣으면 adapter가 거짓말할
  자리가 생긴다.
- **단어 타이밍은 선택 기능이다.** `supports_word_timings`로 조회하고, 주지 못하는
  provider는 `None`을 남긴다. 필수로 요구하면 provider 교체가 막히고, 아예 없으면
  스파이크 4.7의 이득(전사 없이 word-level 타임코드)을 못 쓴다.
- **음성 이름은 provider 문자열을 그대로 통과시킨다.** 자체 어휘로 매핑하면 Azure 승격이
  "같은 음성 이름"이라 싸다는 이득이 사라진다 (스파이크 5.2).
- SSML은 넣지 않는다. Edge TTS가 쓸 수 없다 (스파이크 5.1).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .. import PACKAGE_LOGGER

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.tts")
"""`shorts_maker` 로거의 자식이라 `run_logging`이 붙인 핸들러로 그대로 흘러간다."""

_TEXT_EXCERPT = 40
"""오류 메시지에 붙일 문장 길이. 어느 문장에서 실패했는지 알아볼 만큼이면 된다."""


class TTSError(Exception):
    """음성 합성이 실패했다.

    `retryable`이 실패의 성질을 구분한다. `LLMError.retryable`과 같은 기준이다 —
    네트워크 오류나 빈 응답은 다시 부르면 달라질 수 있지만, 모듈이 없거나 타임아웃을
    넘긴 것은 같은 조건에서 같은 결과가 나온다.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class WordTiming:
    """합성 엔진이 보고한 토큰 하나의 타이밍.

    `text`는 **정규화된 발음이 아니라 원문 토큰**이다 (스파이크 4.7). 자막 텍스트로는
    그대로 쓸 수 있지만 발음 확인에는 쓸 수 없다.
    """

    text: str
    offset_sec: float
    """세그먼트 시작 기준 오프셋(초)."""

    duration_sec: float


class TTSProvider(Protocol):
    """음성 합성 1회를 담당한다. vendor 중립 계약이다.

    입력은 텍스트 하나, 출력은 오디오 파일 하나 — 세그먼트 단위다 (스파이크 5.3,
    PRD 7.5.2의 `audio/seg-{index:03d}.mp3`에 대응).
    """

    name: str
    """provider 이름 (`tts.provider`에 쓰는 값)."""

    voice: str
    """provider가 쓰는 음성 이름. 그대로 통과시킨 문자열이다."""

    supports_word_timings: bool
    """단어 타이밍을 주는가. false면 `synthesize`가 `None`을 돌려준다."""

    def synthesize(self, text: str, destination: Path) -> tuple[WordTiming, ...] | None:
        """`text`를 합성해 `destination`에 쓴다.

        **길이를 돌려주지 않는다.** 돌려주는 것은 단어 타이밍뿐이고, 그마저 선택이다.
        파일을 만들지 못하면 `TTSError`를 던진다 — 빈 파일을 남기지 않는다.
        """
        ...


@dataclass(frozen=True)
class RetryingProvider:
    """adapter를 감싸 재시도하고 합성 1회를 로그에 남긴다.

    실패를 삼키지 않는다. `max_retries`를 다 쓰면 **어떤 문장에서 몇 회 시도 후 왜
    실패했는지**를 담은 `TTSError`를 던진다 — 세그먼트가 10개 안팎이라 문장을 특정하지
    않으면 로그를 뒤져야 한다.
    """

    inner: TTSProvider
    max_retries: int

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def voice(self) -> str:
        return self.inner.voice

    @property
    def supports_word_timings(self) -> bool:
        return self.inner.supports_word_timings

    def synthesize(self, text: str, destination: Path) -> tuple[WordTiming, ...] | None:
        attempts = self.max_retries + 1
        last_error: TTSError | None = None
        tried = 0

        for attempt in range(1, attempts + 1):
            tried = attempt
            started = time.monotonic()
            try:
                timings = self.inner.synthesize(text, destination)
            except TTSError as error:
                last_error = error
                if not error.retryable:
                    LOGGER.warning(
                        "TTS 합성 실패 (%d/%d, 재시도하지 않는다): %s", attempt, attempts, error
                    )
                    break
                LOGGER.warning("TTS 합성 실패 (%d/%d): %s", attempt, attempts, error)
                continue

            LOGGER.info(
                "TTS 합성 provider=%s voice=%s 지연=%dms 시도=%d/%d 문장=%s",
                self.name,
                self.voice,
                int((time.monotonic() - started) * 1000),
                attempt,
                attempts,
                excerpt(text),
            )
            return timings

        raise TTSError(
            f"{self.name} 합성이 {tried}회 시도 후 실패했다 — 문장 {excerpt(text)}, "
            f"마지막 원인: {last_error}",
            retryable=False,
        ) from last_error


def excerpt(text: str) -> str:
    """로그·오류 메시지에 넣을 문장 발췌."""
    if len(text) <= _TEXT_EXCERPT:
        return repr(text)
    return repr(text[:_TEXT_EXCERPT] + "…")
