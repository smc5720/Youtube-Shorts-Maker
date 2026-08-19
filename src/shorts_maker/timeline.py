"""실측 길이 → 확정 타임라인과 `voice.mp3` (PRD 7.5.1·7.5.2, 이슈 #16).

여기서 `scenes.json`이 확정 상태가 된다. 자막·렌더러는 확정 상태만 입력으로 받으므로
(퀴즈 스펙 4장) 이 단계를 지나지 않은 장면 목록은 이후 단계에 넘길 수 없다.

**세 가지가 한 계산에 매달려 있어 한 모듈이다.** `narration_offset`은 앞선 장면들의 확정
`duration` 누계에서 나오고, `voice.mp3`는 그 오프셋에 세그먼트를 놓아 만든다.

- **이 단계도 타입을 모른다.** 분기 조건은 `narrate` 플래그와 `caption` 필드의 존재뿐이고
  `role` 목록을 하드코딩하지 않는다 (PRD 7.4.1, 7.5.1). 낭독을 `hook`에 붙이기로 결정하면
  바뀌는 것은 장면 템플릿의 플래그 하나다.
- **길이를 다시 재지 않는다.** `audio_duration`은 #15가 `ffprobe`로 재서 기록한 값이고,
  측정 경계는 하나여야 한다 (`tts/speech.py`). 여기서 다시 재면 같은 파일에 두 값이 생긴다.
- **경고는 실행을 멈추지 않는다.** 목표치와 크게 벌어진 장면도, 총 길이가 45~60초를 벗어난
  결과도 산출물은 그대로 남긴다 — 자동으로 줄이면 사람이 검수할 대상이 소리 없이 바뀐다
  (PRD 7.5.1, 6.3). 조정은 `config`로 사람이 한다.
- **효과음과 배경음악을 섞지 않는다.** `voice.mp3`는 낭독만 담은 트랙이고, 나머지는 렌더
  단계의 별도 트랙이다 (PRD 7.5.2, #23, #35).
- **입구가 둘이다** (#77). `finalize`는 실측에서 길이를 확정하고, `place_narration`은 이미
  정해진 길이 위에 오프셋만 다시 매긴다 — 재생성이 **사람이 얹은 길이를 반영한** 트랙을
  만들어야 하기 때문이고, 그 길이는 확정 검증이 거부하는 값일 수 있다 (PRD 14.1).
"""

from __future__ import annotations

import copy
import logging
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import PACKAGE_LOGGER
from .schemas.scenes import DURATION_DIGITS, validate_scenes, validate_scenes_final

if TYPE_CHECKING:
    from .config import Config

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.timeline")

VOICE_TRACK = "voice.mp3"
"""합성 트랙의 파일명 (PRD 7.5.2). `scenes.json`에 담기지 않으므로 스키마가 아니라 여기가
단일 진실 공급원이고, 렌더러(#19)가 단일 오디오 입력으로 이 이름을 읽는다."""

TARGET_RATIO = 2.0
"""확정값이 목표치의 이 배수를 넘거나 역수 미만이면 경고한다 (PRD 7.5.1).

목표치는 장면 템플릿이 넣은 검증 기준일 뿐 길이를 정하지 않는다. 이 배수를 벗어난 것은
"길이가 틀렸다"가 아니라 **프롬프트가 너무 긴 문장을 만들었거나 합성이 실패했다는 신호**다.
"""

TOTAL_RANGE_SEC = (45.0, 60.0)
"""목표 총 길이 (PRD 6.3). config 키가 아니다 — 쇼츠 포맷의 규격이고, 이 범위를 벗어났을 때
사람이 조정하는 것은 `quiz.question_count`나 문장 길이다."""

FFMPEG_TIMEOUT_SEC = 300
"""합성 트랙 하나를 만드는 상한. 무음 위에 세그먼트를 얹는 작업이라 초 단위로 끝나며,
넘겼다면 기다릴 것이 아니라 환경 문제다."""

_SAMPLE_RATE = 44100
_CHANNEL_LAYOUT = "stereo"
"""합성 트랙의 출력 규격. 세그먼트는 provider가 정한 규격(edge_tts는 24kHz 모노)으로
오므로 섞기 전에 여기로 맞춘다 — 규격이 다른 입력을 그대로 섞으면 필터가 거부한다."""


class TimelineError(Exception):
    """타임라인을 확정하거나 합성 트랙을 만드는 데 실패했다."""


@dataclass(frozen=True)
class Placement:
    """`voice.mp3` 안에서 세그먼트 하나가 놓이는 자리."""

    audio: Path
    offset_sec: float


VoiceMixer = Callable[[Sequence[Placement], Path, float], None]
"""합성 경계. 테스트가 여기를 바꿔 끼워 FFmpeg 없이 배치 결과를 확인한다."""


@dataclass(frozen=True)
class _Timing:
    """확정 공식의 상수들 (PRD 7.5.1 표)."""

    lead_in: float
    tail: float
    min_duration: float
    caption_onset: float
    reading_cps: float

    @classmethod
    def from_config(cls, config: Config) -> _Timing:
        reading_cps = float(config.get("timing.reading_cps"))
        if reading_cps <= 0:
            # 0이면 자막 하한이 무한이 된다. 설정 로더는 범위를 모르므로(config.py) 값을
            # 실제로 나누는 쪽이 본다.
            raise TimelineError(
                f"timing.reading_cps는 0보다 커야 한다. 받은 값: {reading_cps}"
            )
        return cls(
            lead_in=float(config.get("timing.lead_in_sec")),
            tail=float(config.get("timing.tail_sec")),
            min_duration=float(config.get("timing.min_duration_sec")),
            caption_onset=float(config.get("timing.caption_onset_sec")),
            reading_cps=reading_cps,
        )


def mix_voice_track(
    placements: Sequence[Placement], destination: Path, total_sec: float
) -> None:
    """세그먼트를 각자의 오프셋에 놓고 나머지를 무음으로 채운 트랙을 만든다 (PRD 7.5.2).

    무음을 첫 입력으로 두고 세그먼트를 `adelay`로 밀어 얹는다. 장면마다 무음을 잘라 이어
    붙이는 방법보다 오프셋이 직접 드러나고, 총 길이가 첫 입력 하나로 정해진다.

    Raises:
        TimelineError: FFmpeg가 없거나 실패했을 때.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    length = f"{total_sec:.3f}"

    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        # 무음 바닥. `-t`가 붙으므로 총 길이가 여기서 정해진다.
        "-f",
        "lavfi",
        "-t",
        length,
        "-i",
        f"anullsrc=r={_SAMPLE_RATE}:cl={_CHANNEL_LAYOUT}",
    ]
    for placement in placements:
        command += ["-i", str(placement.audio)]

    steps: list[str] = []
    labels: list[str] = []
    for number, placement in enumerate(placements, start=1):
        # `adelay`는 밀리초를 받는다. 오프셋도 밀리초 자리로 기록되므로 잘려 나가는 값이 없다.
        delay_ms = round(placement.offset_sec * 1000)
        steps.append(
            f"[{number}:a]"
            f"aformat=sample_rates={_SAMPLE_RATE}:channel_layouts={_CHANNEL_LAYOUT},"
            f"adelay={delay_ms}:all=1[seg{number}]"
        )
        labels.append(f"[seg{number}]")
    steps.append(
        # `normalize=0`이라야 입력 수로 음량을 나누지 않는다. 세그먼트는 겹치지 않으므로
        # 그대로 더해도 원본 레벨을 넘지 않는다. `duration=first`는 무음 바닥의 길이를 쓴다.
        f"[0:a]{''.join(labels)}"
        f"amix=inputs={len(placements) + 1}:normalize=0:duration=first[voice]"
    )

    command += [
        "-filter_complex",
        ";".join(steps),
        "-map",
        "[voice]",
        "-t",
        length,
        str(destination),
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FFMPEG_TIMEOUT_SEC,
            # **stdin을 막는다.** ffmpeg는 stdin을 조작 입력으로 읽으므로, 앱이 띄운
            # 백엔드에서 그대로 두면 프로토콜 줄을 가져가고 이 호출이 끝나지 않는다
            # (스파이크 #25 7장, #27에서 실제로 밟았다).
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as error:
        raise TimelineError(
            "ffmpeg를 찾을 수 없다. FFmpeg를 설치하고 PATH에 넣는다"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise TimelineError(
            f"ffmpeg가 {FFMPEG_TIMEOUT_SEC}초 안에 끝나지 않았다: {destination}"
        ) from error

    if completed.returncode != 0:
        raise TimelineError(
            f"{destination.name}을 만들지 못했다 — ffmpeg 종료 코드 "
            f"{completed.returncode}, stderr {completed.stderr.strip()!r}"
        )


def finalize(
    scenes: Mapping[str, Any],
    *,
    run_dir: Path,
    config: Config,
    mix: VoiceMixer = mix_voice_track,
) -> dict[str, Any]:
    """`duration`·`narration_offset`을 확정하고 `voice.mp3`를 만든다.

    입력을 바꾸지 않는다. 낭독 장면이 하나도 없으면 합성 트랙을 만들지 않고 길이만 확정한다
    — `voice.mp3`는 세그먼트가 1개 이상일 때의 산출물이며, 없는 것이 실패가 아니다
    (PRD 6.2 표).

    Args:
        scenes: `scenes.json` 내용. 낭독 장면에 #15가 채운 오디오 필드가 있어야 한다.
        run_dir: 이번 run의 출력 디렉터리. 세그먼트 경로와 합성 트랙의 기준이다.
        config: `timing.*` 값을 읽는다.
        mix: 합성 경계. 기본은 FFmpeg 호출이다.

    Raises:
        TimelineError: 확정에 필요한 필드가 없거나 합성 트랙을 만들지 못했을 때.
        SchemaError: 입력이 스키마를 만족하지 않거나 확정 결과가 확정 검증을 통과하지
            못했을 때.
    """
    # #15와 같은 이유로 입구에서 검증한다. 이 함수는 CLI 말고도 앱 백엔드와 테스트가 직접
    # 부르고, 깨진 장면 목록으로 시작하면 잘못된 오프셋을 가진 `voice.mp3`가 남는다.
    validate_scenes(scenes)

    timing = _Timing.from_config(config)
    updated = copy.deepcopy(dict(scenes))

    for index, scene in enumerate(updated["scenes"]):
        duration = _duration_of(scene, index, timing)
        scene["duration"] = duration
        _warn_if_off_target(index, scene, duration)

    placements, total_sec = _place(updated, run_dir=run_dir, timing=timing)
    _warn_if_off_range(total_sec)

    # 트랙을 만들기 전에 확정 상태를 확인한다. 계약을 어긴 장면 목록으로 만든 `voice.mp3`는
    # 어느 장면의 오디오인지 알 수 없는 파일이다.
    validate_scenes_final(updated)

    _mix_or_skip(placements, run_dir / VOICE_TRACK, total_sec, mix=mix)
    return updated


def place_narration(
    scenes: Mapping[str, Any],
    *,
    run_dir: Path,
    config: Config,
    destination: Path,
    mix: VoiceMixer = mix_voice_track,
) -> dict[str, Any]:
    """이미 정해진 `duration` 위에 낭독 오프셋을 다시 매기고 합성 트랙을 만든다 (#77).

    **`finalize`와 갈리는 것은 길이를 정하는 주체다.** 그쪽은 실측 오디오에서 `duration`을
    확정하고, 이쪽은 받은 길이를 그대로 쓴다 — 재생성이 **사람이 얹은 길이를 반영한
    타임라인으로** `voice.mp3`와 자막을 만들어야 렌더가 그리는 시각과 낭독이 맞는다
    (PRD 14.1, `video_renderer.apply_scene_overrides`).

    **확정 검증을 하지 않는다.** 얹은 길이는 낭독보다 짧을 수 있고(그래서 `scenes.json`이
    아니라 `project.json`에 산다) 장면 사본에는 스키마가 모르는 오버레이 키가 붙어 있을 수
    있다. 검증은 `scenes.json`에 쓰는 값에만 걸리며 그 자리는 `finalize`다.

    Args:
        scenes: 모든 장면에 `duration`이 있는 목록. 확정 상태에 편집을 얹은 사본이다.
        run_dir: 세그먼트 경로의 기준.
        config: `timing.lead_in_sec`을 읽는다.
        destination: 합성 트랙을 쓸 자리. **`run_dir / VOICE_TRACK`이 아닐 수 있다** —
            재생성은 전부 성공했을 때만 산출물을 바꿔 끼운다.
        mix: 합성 경계. 기본은 FFmpeg 호출이다.

    Returns:
        `narration_offset`을 다시 매긴 사본. 자막이 이 목록으로 타임코드를 만든다.

    Raises:
        TimelineError: 길이가 없는 장면이 있거나 합성 트랙을 만들지 못했을 때.
    """
    timing = _Timing.from_config(config)
    updated = copy.deepcopy(dict(scenes))
    placements, total_sec = _place(updated, run_dir=run_dir, timing=timing)
    _mix_or_skip(placements, destination, total_sec, mix=mix)
    return updated


def _place(
    scenes: dict[str, Any], *, run_dir: Path, timing: _Timing
) -> tuple[list[Placement], float]:
    """`duration`대로 낭독 오프셋을 매긴다. 받은 장면 목록을 제자리에서 고친다.

    **길이를 정하지 않는다.** 여기 오는 시점에 모든 장면의 `duration`은 이미 정해져 있고,
    그 값이 실측에서 왔는지 사람이 얹은 것인지는 이 함수의 관심이 아니다 — 그 갈림이
    `finalize`와 `place_narration` 둘을 나누는 유일한 지점이다.
    """
    placements: list[Placement] = []
    start_sec = 0.0
    for index, scene in enumerate(scenes["scenes"]):
        duration = scene.get("duration")
        if duration is None:
            raise TimelineError(
                f"scenes[{index}]: duration이 없다 — 길이가 정해진 장면 목록이어야 한다"
            )

        if scene.get("narrate"):
            offset_sec = round(start_sec + timing.lead_in, DURATION_DIGITS)
            scene["narration_offset"] = offset_sec
            placements.append(
                Placement(audio=run_dir / scene["audio"], offset_sec=offset_sec)
            )

        # 누계도 매번 반올림한다. 반올림한 길이를 그대로 더하지 않으면 뒤쪽 오프셋에
        # 부동소수점 잔여가 쌓여 기록값이 앞선 duration의 합과 어긋난다.
        start_sec = round(start_sec + float(duration), DURATION_DIGITS)

    return placements, start_sec


def _mix_or_skip(
    placements: Sequence[Placement], destination: Path, total_sec: float, *, mix: VoiceMixer
) -> None:
    if not placements:
        # 낭독 장면이 없으면 배치할 것이 없다. 무음 트랙을 만들어 두면 렌더러가 "있으면
        # 쓴다"는 규칙으로 무음을 입힌다 (PRD 6.2 표).
        LOGGER.debug("낭독 장면이 없어 %s를 만들지 않았다", destination.name)
        return

    mix(placements, destination, total_sec)
    LOGGER.debug(
        "%s 생성 — 총 %.3f초에 세그먼트 %d개", destination.name, total_sec, len(placements)
    )


def _duration_of(scene: Mapping[str, Any], index: int, timing: _Timing) -> float:
    """장면 하나의 확정 길이 (PRD 7.5.1).

    낭독이 없는 장면은 초안의 확정값을 그대로 둔다 — 실측할 오디오가 없으므로 보정 대상이
    아니고, `countdown`은 숫자 전환이 정수 초에 맞아야 한다.
    """
    if not scene.get("narrate"):
        duration = scene.get("duration")
        if duration is None:
            raise TimelineError(
                f"scenes[{index}]: 낭독이 없는 장면인데 duration이 없다 — 실측할 오디오가 "
                "없으므로 장면 템플릿이 확정값을 넣어야 한다"
            )
        return round(float(duration), DURATION_DIGITS)

    missing = [key for key in ("audio", "audio_duration") if key not in scene]
    if missing:
        raise TimelineError(
            f"scenes[{index}]: 낭독 장면인데 {', '.join(missing)}이 없다 — "
            "세그먼트 합성을 거치지 않은 장면 목록이다"
        )

    candidates = [
        timing.lead_in + float(scene["audio_duration"]) + timing.tail,
        timing.min_duration,
    ]
    caption = scene.get("caption")
    if caption:
        # 자막 읽기 하한. 앞항이 `lead_in`이 아니라 `caption_onset`인 이유는 자막이 장면
        # 시작이 아니라 그 시각에 뜨기 때문이다 (D1 확정 스펙 4장).
        candidates.append(
            timing.caption_onset + len(caption) / timing.reading_cps + timing.tail
        )
    return round(max(candidates), DURATION_DIGITS)


def _warn_if_off_target(index: int, scene: Mapping[str, Any], duration: float) -> None:
    """확정값이 목표치에서 크게 벌어졌는지 (PRD 7.5.1).

    **`WARNING`이라 `--verbose` 여부와 무관하게 콘솔과 `run.log` 양쪽에 남는다.**
    """
    target = scene.get("target_duration")
    if target is None:
        return
    target = float(target)

    if duration > target * TARGET_RATIO:
        verdict = f"{TARGET_RATIO:.0f}배를 넘는다 — 문장이 너무 길거나 합성이 늘어졌다"
    elif duration < target / TARGET_RATIO:
        verdict = f"{1 / TARGET_RATIO:g}배 미만이다 — 합성이 잘렸는지 확인한다"
    else:
        return

    LOGGER.warning(
        "scenes[%d](%s): 확정 %.2f초가 목표 %.2f초의 %s",
        index,
        scene["role"],
        duration,
        target,
        verdict,
    )


def _warn_if_off_range(total_sec: float) -> None:
    """총 길이가 목표 범위를 벗어났는지 (PRD 6.3). 렌더를 막지 않는다."""
    low, high = TOTAL_RANGE_SEC
    if low <= total_sec <= high:
        return

    LOGGER.warning(
        "확정 총 길이 %.2f초가 목표 범위 %.0f~%.0f초를 벗어난다 — 문제 수나 문장 길이를 "
        "config로 조정한다 (렌더는 막지 않는다)",
        total_sec,
        low,
        high,
    )
