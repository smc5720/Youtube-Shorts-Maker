"""Edge TTS adapter — MVP 기본 provider (스파이크 #2).

**`boundary="WordBoundary"`를 명시하는 것이 이 모듈에서 가장 쉽게 잃는 것이다.**
기본값은 `SentenceBoundary`이고, 문장 단위로 합성하는 이 파이프라인에서는 문장 경계가
문장 전체 하나뿐이라 이벤트가 쓸모없어진다. 스파이크에서도 처음에 이걸 놓쳐 이벤트가
0개로 측정됐다 (4.7). 그래서 `BOUNDARY`는 상수이고 이 클래스에는 그것을 바꿀 인자가 없다.

**edge-tts는 비공식 엔드포인트다** (스파이크 5.1). 예고 없이 깨질 수 있으므로 네트워크
계열 실패는 재시도 대상으로 두고, 막혔을 때의 승격 경로(Azure AI Speech, 같은 음성 이름)는
스파이크 5.2에 있다.

`edge_tts` import는 함수 안에서 한다. 모듈을 설치하지 않은 환경에서도 레지스트리 조회와
설정 검증이 돌아야 하고(`validate_tts_provider`는 이름만 본다), 부재는 첫 합성에서
재시도하지 않는 `TTSError`로 드러난다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from .provider import TTSError, WordTiming

PROVIDER_NAME = "edge_tts"

BOUNDARY = "WordBoundary"
"""명시하지 않으면 `SentenceBoundary`가 되어 이벤트가 쓸모없어진다 (스파이크 4.7)."""

HNS_PER_SEC = 10_000_000
"""edge-tts는 오프셋과 지속시간을 100나노초 단위로 준다."""


class SpeechStream(Protocol):
    """합성 경계. 테스트가 여기를 가짜로 바꿔 끼워 네트워크 없이 전체를 돈다.

    청크를 모아서 돌려준다 — 세그먼트 하나는 수 초짜리 MP3라 스트리밍으로 얻을 것이 없고,
    모아 두면 오디오가 하나도 오지 않은 경우를 파일을 쓰기 전에 알 수 있다.

    시간 초과는 `TimeoutError`로 알린다. 이 계약을 자체 예외로 바꾸면 기본 구현과 가짜
    구현이 서로 다른 경로를 타서 매핑을 검증할 수 없다 (`llm/claude_cli.CommandRunner`와
    같은 기준).
    """

    def __call__(
        self, *, text: str, voice: str, timeout_sec: int
    ) -> Sequence[Mapping[str, Any]]: ...


def stream_speech(
    *, text: str, voice: str, timeout_sec: int
) -> Sequence[Mapping[str, Any]]:
    """`edge_tts.Communicate`의 스트림을 끝까지 모아 돌려준다."""
    import edge_tts  # 지연 import — 위 독스트링 참고

    async def collect() -> list[Mapping[str, Any]]:
        communicate = edge_tts.Communicate(text, voice, boundary=BOUNDARY)
        chunks: list[Mapping[str, Any]] = []
        async with asyncio.timeout(timeout_sec):
            try:
                async for chunk in communicate.stream():
                    chunks.append(chunk)
            except TimeoutError as error:
                # 소켓 타임아웃도 `TimeoutError`다 (3.10에서 OSError 계열과 합쳐졌다).
                # 우리 마감은 취소로 도착해 이 블록을 지나지 않으므로 여기 오는 것은
                # 네트워크 쪽이고, 그대로 흘려보내면 **재시도하지 않는** 실패로 둔갑한다.
                raise ConnectionError(f"합성 스트림이 응답하지 않는다: {error!r}") from error
        return chunks

    return asyncio.run(collect())


class EdgeTTSProvider:
    """문장 하나 = 합성 1회. 음성 이름은 받은 문자열을 그대로 넘긴다."""

    name = PROVIDER_NAME
    supports_word_timings = True

    def __init__(
        self,
        *,
        voice: str,
        timeout_sec: int = 60,
        stream: SpeechStream = stream_speech,
    ) -> None:
        self.voice = voice
        self.timeout_sec = timeout_sec
        self._stream = stream

    def synthesize(self, text: str, destination: Path) -> tuple[WordTiming, ...] | None:
        chunks = self._collect(text)

        audio = bytearray()
        timings: list[WordTiming] = []
        for chunk in chunks:
            kind = chunk.get("type")
            if kind == "audio":
                audio.extend(chunk["data"])
            elif kind == BOUNDARY:
                timings.append(
                    WordTiming(
                        text=str(chunk["text"]),
                        offset_sec=chunk["offset"] / HNS_PER_SEC,
                        duration_sec=chunk["duration"] / HNS_PER_SEC,
                    )
                )

        # 빈 파일을 남기지 않는다. 남기면 ffprobe가 길이를 못 읽어 재시도하지 않는 오류가
        # 되고, 정작 원인(빈 응답)은 재시도하면 풀릴 수 있는 것이다.
        if not audio:
            raise TTSError(
                f"{PROVIDER_NAME}가 오디오를 하나도 내려주지 않았다 (청크 {len(chunks)}개)"
            )

        destination.write_bytes(bytes(audio))
        return tuple(timings)

    def _collect(self, text: str) -> Sequence[Mapping[str, Any]]:
        try:
            return self._stream(text=text, voice=self.voice, timeout_sec=self.timeout_sec)
        except ImportError as error:
            raise TTSError(
                f"{PROVIDER_NAME} 모듈을 불러올 수 없다: {error}. `pip install edge-tts`",
                retryable=False,
            ) from error
        except TimeoutError as error:
            # 이미 timeout_sec만큼 기다렸다. 다시 불러도 같은 조건이므로 대기 시간만 배가된다.
            raise TTSError(
                f"{PROVIDER_NAME} 합성이 {self.timeout_sec}초 안에 끝나지 않았다. "
                f"tts.timeout_sec를 확인한다",
                retryable=False,
            ) from error
        except TTSError:
            raise
        except Exception as error:
            # 비공식 엔드포인트라 어떤 예외가 올라오는지 고정할 수 없다 (스파이크 5.1).
            # 성질을 특정할 수 없는 실패는 재시도 대상으로 둔다 — 네트워크 순단이 가장
            # 흔한 원인이고, 영구적 실패면 재시도 후 같은 원인이 메시지에 남는다.
            raise TTSError(
                f"{PROVIDER_NAME} 합성 호출이 실패했다 ({type(error).__name__}): {error}"
            ) from error


def create(*, voice: str, options: Mapping[str, Any], timeout_sec: int) -> EdgeTTSProvider:
    """레지스트리가 부른다. `edge_tts`는 여는 설정 키가 없어 `options`가 항상 비어 있다."""
    return EdgeTTSProvider(voice=voice, timeout_sec=timeout_sec)
