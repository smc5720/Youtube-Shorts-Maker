"""합성 결과를 확정하는 공통 레이어 — 실측 길이와 캐시.

`TTSProvider`는 텍스트를 오디오 파일로 바꾸는 것까지만 한다. **그 파일이 몇 초인지는
여기가 정한다.** provider가 보고하는 값을 쓰지 않는 것이 스파이크 5.3의 요구이고,
`duration`은 자막·오버레이·효과음이 전부 매달리는 값이라(PRD 7.5.1) 측정 방법이 한
군데여야 한다.

- **컨테이너 값(`ffprobe format=duration`)을 쓴다.** 디코딩 값과의 차이가 최대 0.008초로
  30fps 한 프레임(0.033초)보다 작고, 디코딩보다 훨씬 빠르다 (스파이크 4.1).
- **캐시는 run 디렉터리 밖에 둔다.** run마다 새 디렉터리이므로 안에 두면 반복 실행 비용이
  줄지 않는다 (PRD 13 "같은 입력으로 반복 실행"). 같은 문장을 3회 합성해 길이가 완전히
  동일했으므로 캐시 적중과 미적중이 다른 결과를 내지 않는다 (스파이크 4.2).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .provider import LOGGER, TTSError, TTSProvider, WordTiming, excerpt

DurationProbe = Callable[[Path], float]
"""길이 측정 경계. 테스트가 여기를 바꿔 끼워 ffprobe 없이 돈다."""

PROBE_TIMEOUT_SEC = 30
"""ffprobe 한 번의 상한. 로컬 파일 헤더만 읽으므로 넘길 일이 없고, 넘겼다면 환경 문제다."""

AUDIO_SUFFIX = ".mp3"
SIDECAR_SUFFIX = ".json"


@dataclass(frozen=True)
class Speech:
    """확정된 세그먼트 하나. `duration`을 계산하는 #16의 입력이다."""

    path: Path
    duration_sec: float
    """실측 길이. `ffprobe`가 잰 값이며 provider 보고값이 아니다."""

    word_timings: tuple[WordTiming, ...] | None
    """provider가 주지 않으면 `None`. 빈 튜플은 "주는데 하나도 없었다"는 뜻이다."""


def probe_duration(path: Path) -> float:
    """`ffprobe`로 오디오 길이를 잰다.

    Raises:
        TTSError: ffprobe가 없거나, 파일에서 길이를 읽지 못했을 때. 둘 다 다시 부른다고
            달라지지 않으므로 재시도 대상이 아니다.
    """
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SEC,
        )
    except FileNotFoundError as error:
        raise TTSError(
            "ffprobe를 찾을 수 없다. FFmpeg를 설치하고 PATH에 넣는다", retryable=False
        ) from error
    except subprocess.TimeoutExpired as error:
        raise TTSError(
            f"ffprobe가 {PROBE_TIMEOUT_SEC}초 안에 끝나지 않았다: {path}", retryable=False
        ) from error

    try:
        return float(completed.stdout.strip())
    except ValueError as error:
        raise TTSError(
            f"합성 결과의 길이를 읽지 못했다: {path} — ffprobe 출력 "
            f"{completed.stdout.strip()!r} stderr {completed.stderr.strip()!r}",
            retryable=False,
        ) from error


@dataclass(frozen=True)
class SpeechCache:
    """`(provider, voice, text)` 해시로 오디오와 사이드카를 남긴다.

    사이드카에 실측 길이와 단어 타이밍을 함께 둔다. 오디오만 캐시하면 적중할 때마다
    `ffprobe`를 다시 불러야 하고, 그러면 캐시가 "합성을 건너뛰는 것"에서 멈춘다.

    **캐시 쓰기 실패는 실행을 멈추지 않는다.** 캐시는 속도를 위한 것이고, 디렉터리에 쓸
    수 없다고 해서 만들어진 오디오를 버릴 이유가 없다.
    """

    directory: Path

    def key(self, *, provider: str, voice: str, text: str) -> str:
        """세 값 전부가 들어간다. voice만 바꾼 재실행이 이전 오디오를 재사용하면 안 된다."""
        digest = hashlib.sha256("\n".join((provider, voice, text)).encode("utf-8"))
        return digest.hexdigest()

    def load(
        self, *, provider: str, voice: str, text: str, destination: Path
    ) -> Speech | None:
        """적중하면 오디오를 `destination`에 복사하고 확정된 결과를 돌려준다.

        사이드카가 깨졌거나 오디오가 없으면 미적중으로 본다 — 캐시 손상이 실행을 막지
        않는다.
        """
        name = self.key(provider=provider, voice=voice, text=text)
        audio_path = self.directory / f"{name}{AUDIO_SUFFIX}"
        sidecar_path = self.directory / f"{name}{SIDECAR_SUFFIX}"
        if not audio_path.is_file() or not sidecar_path.is_file():
            return None

        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            duration_sec = float(sidecar["duration_sec"])
            timings = _timings_from(sidecar.get("word_timings"))
            shutil.copyfile(audio_path, destination)
        except (OSError, ValueError, TypeError, KeyError) as error:
            LOGGER.debug("TTS 캐시를 쓸 수 없어 다시 합성한다 (%s): %s", name, error)
            return None

        LOGGER.debug("TTS 캐시 적중 %s 문장=%s", name, excerpt(text))
        return Speech(path=destination, duration_sec=duration_sec, word_timings=timings)

    def store(self, speech: Speech, *, provider: str, voice: str, text: str) -> None:
        name = self.key(provider=provider, voice=voice, text=text)
        sidecar: dict[str, Any] = {
            # 캐시를 사람이 열어 봤을 때 어떤 문장인지 알 수 있어야 한다. 해시는 되돌릴 수 없다.
            "provider": provider,
            "voice": voice,
            "text": text,
            "duration_sec": speech.duration_sec,
            "word_timings": _timings_to(speech.word_timings),
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(speech.path, self.directory / f"{name}{AUDIO_SUFFIX}")
            (self.directory / f"{name}{SIDECAR_SUFFIX}").write_text(
                json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as error:
            LOGGER.warning("TTS 캐시에 쓰지 못했다 (%s): %s", self.directory, error)


def _timings_from(raw: Any) -> tuple[WordTiming, ...] | None:
    if raw is None:
        return None
    return tuple(
        WordTiming(
            text=str(item["text"]),
            offset_sec=float(item["offset_sec"]),
            duration_sec=float(item["duration_sec"]),
        )
        for item in raw
    )


def _timings_to(timings: tuple[WordTiming, ...] | None) -> list[dict[str, Any]] | None:
    if timings is None:
        return None
    return [
        {"text": item.text, "offset_sec": item.offset_sec, "duration_sec": item.duration_sec}
        for item in timings
    ]


class SpeechSynthesizer:
    """호출 지점이 실제로 쓰는 자리 — 텍스트 하나를 확정된 `Speech` 하나로 바꾼다.

    `#15`가 장면 인덱스로 `destination`을 정하고, 여기서는 그 경로에 파일이 놓이고
    길이가 확정되는 것까지만 책임진다.
    """

    def __init__(
        self,
        *,
        provider: TTSProvider,
        cache: SpeechCache | None = None,
        measure: DurationProbe = probe_duration,
    ) -> None:
        self.provider = provider
        self.cache = cache
        self._measure = measure

    def synthesize(self, text: str, destination: Path) -> Speech:
        """`text`를 합성해 `destination`에 놓고 실측 길이와 함께 돌려준다.

        Raises:
            TTSError: 합성이 재시도를 다 쓰고도 실패했거나 길이를 재지 못했을 때.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        provider_name, voice = self.provider.name, self.provider.voice

        if self.cache is not None:
            cached = self.cache.load(
                provider=provider_name, voice=voice, text=text, destination=destination
            )
            if cached is not None:
                return cached

        timings = self.provider.synthesize(text, destination)
        speech = Speech(
            path=destination,
            duration_sec=self._measure(destination),
            word_timings=timings,
        )

        if self.cache is not None:
            self.cache.store(speech, provider=provider_name, voice=voice, text=text)
        return speech
