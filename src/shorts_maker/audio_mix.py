"""확정 `scenes.json` → 최종 오디오 필터 체인 (PRD 7.7, 이슈 #23).

`voice.mp3`는 낭독만 담고(`timeline.py`), **효과음은 렌더 패스에서 얹는다.** 중간 파일을
만들지 않으므로 PRD 6.2 표에 산출물이 늘지 않고, 오디오 스트림은 계속 정확히 하나다.

- **오버레이와 대칭이다.** `overlay.build`가 (장면, 프레임 구간) → `drawtext` 목록을 만드는
  것처럼, 여기는 (장면, 프레임 구간) → (추가 입력 인자, 오디오 필터 단계)를 만든다. 프레임
  경계는 `video_renderer.align()`이 계속 소유하고 이 모듈은 받아 쓴다.
- **카운트다운 비프의 박자는 `overlay.countdown_windows`가 소유한다.** 숫자가 갈리는 프레임과
  소리가 놓이는 프레임이 같아야 하고, 두 곳에서 각자 세면 반올림 하나로 그림과 소리가 갈린다.
  정답 효과음의 트리거 시각도 같은 이유로 `overlay.ANSWER_ONSET_SEC`을 읽는다 — 등장색이
  켜지는 프레임과 같은 값이다 (D1 확정 스펙 5.4).
- **레벨을 여기서 정하지 않는다.** `sfx_volume`은 `project.json`의 `audio` 섹션에서 온다
  (PRD 7.10). 번들 효과음이 낭독보다 peak 9.5dB / RMS 9~10dB 아래로 정규화돼 있어(#18)
  기본값 1.0에서 추가 감쇠가 필요하지 않다 — 실측은 이슈 #23에 있다.
- **효과음이 하나도 없으면 #19~#22와 같은 명령이 나온다.** 리미터도 그때는 붙지 않는다.
  클리핑 위험은 트랙을 더하면서 생기는 것이고, 낭독 하나뿐인 체인에 리미터를 끼우면 이유 없이
  오디오 전체가 필터를 하나 더 지난다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import PACKAGE_LOGGER, overlay
from .assets import AssetError, sfx_path

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.audio_mix")

SAMPLE_RATE = 44100
CHANNEL_LAYOUT = "stereo"
"""최종 오디오 규격. `timeline._SAMPLE_RATE`와 같은 값이라 `voice.mp3`는 리샘플되지 않고,
번들 효과음은 48kHz(#18)이므로 섞기 전에 여기로 맞춘다 — 규격이 다른 입력을 그대로 섞으면
필터가 거부한다."""

VOICE_STREAM = "1:a"
OUTPUT_LABEL = "audio"
"""낭독 입력과 최종 출력 라벨. 입력 0은 배경 영상이고 효과음은 2번부터 붙는다
(`video_renderer.build_command`)."""

FIRST_SFX_INPUT = 2

LIMITER_CEILING = 0.891
"""리미터 상한 (≈-1 dBFS). 기본 설정에서는 동시 피크가 -2.3 dBFS라 걸리지 않는다 — 이 값이
막는 것은 사람이 `sfx_volume`을 올린 경우와 낭독 레벨이 다른 TTS provider다.

**`level=disabled`와 `latency=true`가 둘 다 필요하다** (#23 실측). `level`의 기본값 `true`는
auto level이라 최종 오디오가 0dB로 정규화되어 낭독 레벨이 달라지고, `latency`를 빼면
attack(5ms)만큼 오디오 전체가 늦는다 — 30fps 한 프레임의 15%이므로 영상과 어긋나지는 않지만,
#16이 1ms 정확도를 확인한 경로에 이유 없는 지연을 남기지 않는다.
"""


class AudioMixError(Exception):
    """오디오 체인을 만들 수 없다. `video_renderer`가 `RenderError`로 옮긴다."""


@dataclass(frozen=True)
class Cue:
    """효과음 하나가 놓이는 자리."""

    name: str
    """`scenes.json`의 `sfx` 값이자 번들 파일의 stem (#18)."""

    frame: int
    """영상 시작 기준 프레임 번호. 초로 반올림하지 않는 이유는 오버레이의 `enable` 구간과
    같다 — 프레임 번호가 경계의 단일 진실 공급원이다 (`video_renderer.Timeline`)."""


@dataclass(frozen=True)
class AudioChain:
    """렌더 명령에 넣을 오디오 부분."""

    steps: tuple[str, ...]
    """`-filter_complex`에 이어 붙을 필터 단계. 마지막이 `[audio]`를 낸다."""

    inputs: tuple[str, ...] = field(default=())
    """효과음 입력 인자. 낭독 입력 뒤에 이 순서로 붙는다."""


def voice_only() -> AudioChain:
    """낭독만 담은 체인. 효과음이 없을 때와 `sfx_volume`이 0일 때의 결과다.

    `apad`는 합성 트랙이 프레임 정렬 길이보다 반 프레임쯤 짧을 수 있어 필요하다 (#19).
    """
    return AudioChain(steps=(f"[{VOICE_STREAM}]{_format()},apad[{OUTPUT_LABEL}]",))


def build(
    scenes: Mapping[str, Any],
    frame_spans: Sequence[tuple[int, int]],
    *,
    fps: int,
    volume: float,
) -> AudioChain:
    """장면 목록의 `sfx`를 낭독 위에 얹는 체인을 만든다.

    Args:
        scenes: **확정 상태** `scenes.json` 내용. 입구 검증은 부르는 쪽(`video_renderer`)이
            이미 했다.
        frame_spans: 장면별 (시작 프레임, 끝 프레임). `video_renderer.align`이 소유한다.
        fps: 프레임 번호를 시각으로 바꾸는 분모.
        volume: 효과음 선형 게인 (`project.json`의 `audio.sfx_volume`). 0이면 효과음 입력과
            필터를 아예 만들지 않는다 — 게인 0으로 섞어 두면 명령만 길어진다.

    Raises:
        AudioMixError: 장면 수와 구간 수가 어긋나거나, `sfx` 값이 번들에 없는 이름일 때.
            **인코딩을 시작하기 전에 걸린다** (#20의 폰트 검증과 같은 자리).
    """
    if volume < 0:
        raise AudioMixError(f"audio.sfx_volume은 0 이상이어야 한다. 받은 값: {volume}")

    cue_list = [] if volume == 0 else cues(scenes, frame_spans, fps=fps)
    if not cue_list:
        return voice_only()

    # 같은 소리를 여러 번 놓아도 입력은 하나다. 디코드를 나누는 것은 `asplit`이고, 입력을
    # 이름마다 하나로 묶으면 비프 3개에 파일을 세 번 열지 않는다.
    order: list[str] = []
    for cue in cue_list:
        if cue.name not in order:
            order.append(cue.name)

    inputs: list[str] = []
    steps: list[str] = [f"[{VOICE_STREAM}]{_format()}[voice]"]
    labels: list[str] = ["[voice]"]

    for position, name in enumerate(order):
        index = FIRST_SFX_INPUT + position
        try:
            inputs += ["-i", str(sfx_path(name))]
        except AssetError as error:
            raise AudioMixError(f"효과음을 찾을 수 없다 — {error}") from error

        frames = [cue.frame for cue in cue_list if cue.name == name]
        branches = [f"sfx{index}_{number}" for number in range(len(frames))]

        chain = _format()
        if volume != 1:
            chain += f",volume={volume:g}"
        if len(branches) > 1:
            chain += f",asplit={len(branches)}"
        steps.append(f"[{index}:a]{chain}" + "".join(f"[{name}]" for name in branches))

        for branch, frame in zip(branches, frames, strict=True):
            # `adelay`는 밀리초를 받는다. 프레임 번호에서 직접 환산해야 장면 시작 시각을
            # `duration`으로 다시 누적하는 경로가 생기지 않는다 (PRD 7.7).
            steps.append(f"[{branch}]adelay={round(frame * 1000 / fps)}:all=1[{branch}d]")
            labels.append(f"[{branch}d]")

    steps.append(
        # `normalize=0`이라야 입력 수로 음량을 나누지 않는다 (`timeline.mix_voice_track`과
        # 같은 이유). `duration=longest`는 마지막 효과음이 낭독보다 늦게 끝나는 경우를 담고,
        # `apad`가 그 뒤를 프레임 정렬 길이까지 메운다 — 총 길이는 렌더의 `-t`가 끊는다.
        "".join(labels)
        + f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad,"
        + f"alimiter=limit={LIMITER_CEILING}:level=disabled:latency=true[{OUTPUT_LABEL}]"
    )

    LOGGER.debug(
        "효과음 %d개 배치 — 게인 %g, 입력 %d개", len(cue_list), volume, len(order)
    )
    return AudioChain(steps=tuple(steps), inputs=tuple(inputs))


def cues(
    scenes: Mapping[str, Any], frame_spans: Sequence[tuple[int, int]], *, fps: int
) -> list[Cue]:
    """`sfx`가 있는 장면을 트리거 목록으로 편다. 순서는 장면 순서다.

    역할마다 놓는 방식이 다르다 (퀴즈 스펙 2장).

    - `countdown`: 숫자마다 하나. 박자는 `overlay.countdown_windows`가 정한다.
    - `answer`: 정답이 등장하는 순간 하나 (`overlay.ANSWER_ONSET_SEC`).
    - 그 밖의 역할: 장면 시작에 하나. **`role` 목록을 여기서 닫지 않는다** — 새 역할이
      `sfx`를 들면 장면 시작에 울리는 것이 기본이고, 다른 시각이 필요할 때 그 이슈가 분기를
      추가한다.

    Raises:
        AudioMixError: 장면 수와 구간 수가 어긋날 때.
    """
    scene_list = list(scenes["scenes"])
    if len(frame_spans) != len(scene_list):
        raise AudioMixError(
            f"장면 {len(scene_list)}개에 구간이 {len(frame_spans)}개다 — "
            "프레임 정렬과 장면 목록이 어긋났다"
        )

    found: list[Cue] = []
    for index, (scene, span) in enumerate(zip(scene_list, frame_spans, strict=True)):
        name = scene.get("sfx")
        if not name:
            continue
        where = f"scenes[{index}]"

        if scene["role"] == "countdown":
            found += _countdown_cues(str(name), scene.get("seconds"), span, fps=fps, where=where)
            continue

        offset = overlay.ANSWER_ONSET_SEC if scene["role"] == "answer" else 0.0
        frame = span[0] + _frames(offset, fps)
        if frame >= span[1]:
            # 확정 검증을 지난 장면은 최소 `min_duration_sec`(1.2초)이라 여기 오지 않는다.
            # 사람이 길이를 줄인 `scenes.json`에서는 트리거가 다음 장면으로 새는 것보다
            # 소리가 없는 편이 낫다.
            LOGGER.warning(
                "%s: 장면이 %.2f초라 효과음 시각(%.2f초)이 장면을 넘는다 — 놓지 않는다",
                where, (span[1] - span[0]) / fps, offset,
            )
            continue
        found.append(Cue(name=str(name), frame=frame))

    return found


def _countdown_cues(
    name: str, seconds: Any, span: tuple[int, int], *, fps: int, where: str
) -> list[Cue]:
    """숫자가 갈리는 프레임마다 하나. 첫 숫자가 뜨는 순간도 전환이다 (확정 스펙 5.3)."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
        # 오버레이도 같은 이유로 그리지 않는다 (`draw_countdown`). 그쪽 경고와 문구가 다른
        # 것은 사람이 어느 쪽이 빠졌는지 알아야 하기 때문이다.
        LOGGER.warning(
            "%s의 seconds가 1 이상의 정수가 아니다 — 비프를 놓지 않는다: %r", where, seconds
        )
        return []

    windows = overlay.countdown_windows(seconds, span, fps=fps)
    if len(windows) < seconds:
        LOGGER.warning(
            "%s: 장면 길이가 seconds(%d초)보다 짧아 비프 %d개만 놓는다",
            where, seconds, len(windows),
        )
    return [Cue(name=name, frame=start) for _, (start, _) in windows]


def _frames(seconds: float, fps: int) -> int:
    """장면 시작 기준 초 → 프레임 수. `overlay._Painter._frames`와 같은 반올림이다 —
    소리와 그림이 같은 프레임에 놓이려면 환산이 한 규칙이어야 한다."""
    return max(0, round(seconds * fps))


def _format() -> str:
    """입력을 최종 오디오 규격으로 맞추는 필터."""
    return f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts={CHANNEL_LAYOUT}"
