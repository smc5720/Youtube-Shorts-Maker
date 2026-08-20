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
- **레벨을 여기서 정하지 않는다.** `sfx_volume`과 `voice_volume`은 `project.json`의 `audio`
  섹션에서 온다 (PRD 7.10). 번들 효과음이 낭독보다 peak 9.5dB / RMS 9~10dB 아래로 정규화돼
  있어(#18) 기본값 1.0에서 추가 감쇠가 필요하지 않다 — 실측은 이슈 #23에 있다.
- **효과음이 하나도 없고 낭독 게인이 1이면 #19~#22와 같은 명령이 나온다.** 리미터도 그때는
  붙지 않는다. 낭독 하나를 원본 레벨로 흘리는 체인에는 넘칠 것이 없고, 거기에 리미터를 끼우면
  이유 없이 오디오 전체가 필터를 하나 더 지난다. **넘칠 수 있는 경우가 #81에서 하나 늘었다** —
  트랙을 더하는 것(효과음)과 낭독 게인을 1 위로 올리는 것 둘이고, 어느 쪽이든 리미터가 붙는다.
- **배경음악도 이 층에서 얹는다** (#35). 낭독 구간의 감쇠(ducking)를 **검출로 하지 않는다** —
  낭독이 어디에 있는지는 확정 `scenes.json`의 `narration_offset` + `audio_duration`으로 이미
  알고 있고, `sidechaincompress`는 그 정보를 버리고 낭독 트랙을 실시간으로 들어 감쇠를 다시
  추정하는 필터라 결과가 소재에 따라 흔들린다. 효과음을 `adelay` + 프레임 번호로 놓는 것과
  같은 이유다 (#23).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
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

MUSIC_LABEL = "music"
"""배경음악 가지의 라벨 (#35). 낭독·효과음과 함께 `amix`로 들어간다."""

MUSIC_FILE_EXTENSIONS = (".mp3", ".m4a", ".wav")
"""배경음악으로 받는 사용자 파일의 확장자 (PRD 8장, 이슈 #35).

**이 표가 목록의 유일한 소유자다.** 앱은 백엔드를 지나 조회하고(`api.method_presets`) 자기
목록을 들지 않는다 — `video_renderer.BACKGROUND_FILE_KINDS`와 같은 이유이고, 갈리면 앱이 받은
파일을 렌더가 거부한다.

**좁게 시작한 이유도 같다 — 실패 시점이다.** FFmpeg는 동봉하지 않고 PATH에서 찾으므로 사용자
빌드가 무엇을 디코드하는지 알 수 없고, 넓게 열면 수십 초 걸리는 렌더 **중간에** 실패한다
(PRD 14.1). 배경 파일과 달리 `kind`를 함께 보내지 않는 것은 음악에 종류가 없기 때문이다 —
확장자가 정하는 것이 없으므로 표가 목록 하나로 끝난다.

순서가 화면의 순서다 — 앱이 다시 정렬하면 목록 순서가 두 곳에서 정해진다.
"""

UNITY_GAIN = 1.0
"""게인을 걸지 않은 상태. 두 트랙 모두 이 값이 "원본 레벨 그대로"이고, 그래서 필터를 아예
넣지 않는 것이 그 사실의 표현이다 (#23, #81)."""

LIMITER_CEILING = 0.891
"""리미터 상한 (≈-1 dBFS). 기본 설정에서는 동시 피크가 -2.3 dBFS라 걸리지 않는다 — 이 값이
막는 것은 사람이 볼륨을 올린 경우와 낭독 레벨이 다른 TTS provider다.

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
class Music:
    """섞을 배경음악 하나 (#35).

    **값 넷이 한 객체인 이유는 셋이 나머지 하나 없이는 뜻이 없기 때문이다.** 감쇠량과 전환
    시간은 "무엇을 감쇠하는가"가 정해진 뒤에만 의미가 있고, 음악이 없는 실행에서는 그 값들이
    명령에 닿지 않는다 — 인자 넷을 나란히 받으면 "음악은 없는데 감쇠량은 0.2"라는 상태가
    호출부마다 표현될 수 있다.

    경로는 **이미 풀린 절대 경로다.** run 디렉터리 기준 상대 경로를 푸는 것은
    `video_renderer._source_file` 하나이고(배경 파일과 같은 자리), 이 모듈은 파일이 있는지도
    보지 않는다.
    """

    path: Path
    """음악 파일. 확장자 검사는 `check_music_format`이 렌더 시작 전에 한다."""

    volume: float
    """음악 트랙의 선형 게인 (`project.json`의 `audio.music_volume`).

    **자동 정규화(loudnorm)를 쓰지 않는다** — 사용자 파일마다 레벨이 다른 문제는 이 값으로
    다루고, 넘치는 것은 믹스 끝의 리미터가 잡는다. 정규화를 넣으면 낭독 레벨까지 움직인다
    (#23).
    """

    duck: float
    """낭독 구간에서 음악에 곱하는 배수 (`audio.music_duck`). 0이면 완전 무음, 1이면 감쇠 없음.

    **`volume`과 곱셈으로 겹친다** — 이 값은 음악 자신의 레벨에 대한 상대량이므로, 사람이
    음악을 키워도 낭독 아래로 내려가는 비율은 그대로다.
    """

    fade_sec: float
    """감쇠가 걸리고 풀리는 데 쓰는 시간 (`audio.music_duck_fade_sec`).

    **0을 받지 않는다** (`_check_music`). 계단 전환은 파형의 불연속이라 클릭으로 들리고, 그것은
    설정으로 고를 값이 아니라 결함이다 — 최종 mp4 실측에서 계단 전환의 샘플 간 최대 변화량이
    정상 구간의 8.6배였고 램프는 1.2~1.4배였다 (#35).
    """


@dataclass(frozen=True)
class AudioChain:
    """렌더 명령에 넣을 오디오 부분."""

    steps: tuple[str, ...]
    """`-filter_complex`에 이어 붙을 필터 단계. 마지막이 `[audio]`를 낸다."""

    inputs: tuple[str, ...] = field(default=())
    """효과음 입력 인자. 낭독 입력 뒤에 이 순서로 붙는다."""


def voice_only(volume: float = UNITY_GAIN) -> AudioChain:
    """낭독만 담은 체인. 효과음이 없을 때와 `sfx_volume`이 0일 때의 결과다.

    `apad`는 합성 트랙이 프레임 정렬 길이보다 반 프레임쯤 짧을 수 있어 필요하다 (#19).

    Args:
        volume: 낭독 선형 게인 (`audio.voice_volume`, #81). **1 위로 올릴 때만 리미터가
            붙는다** — 원본 레벨이나 그 아래로 내린 트랙 하나는 넘칠 수 없고, 거기에 리미터를
            끼우면 이유 없이 오디오 전체가 필터를 하나 더 지난다.
    """
    chain = f"{_voice(volume)},apad"
    if volume > UNITY_GAIN:
        chain += f",{_limiter()}"
    return AudioChain(steps=(f"[{VOICE_STREAM}]{chain}[{OUTPUT_LABEL}]",))


def build(
    scenes: Mapping[str, Any],
    frame_spans: Sequence[tuple[int, int]],
    *,
    fps: int,
    sfx_volume: float,
    voice_volume: float = UNITY_GAIN,
    music: Music | None = None,
) -> AudioChain:
    """장면 목록의 `sfx`와 배경음악을 낭독 위에 얹는 체인을 만든다.

    Args:
        scenes: **확정 상태** `scenes.json` 내용. 입구 검증은 부르는 쪽(`video_renderer`)이
            이미 했다.
        frame_spans: 장면별 (시작 프레임, 끝 프레임). `video_renderer.align`이 소유한다.
        fps: 프레임 번호를 시각으로 바꾸는 분모.
        sfx_volume: 효과음 선형 게인 (`project.json`의 `audio.sfx_volume`). 0이면 효과음
            입력과 필터를 아예 만들지 않는다 — 게인 0으로 섞어 두면 명령만 길어진다.
        voice_volume: 낭독 선형 게인 (`audio.voice_volume`, #81). **두 게인을 한 인자로 묶지
            않는다** — 이름이 `volume` 하나였을 때 어느 트랙의 값인지가 호출부에서만 보였다.
        music: 섞을 배경음악 (#35). `None`이면 음악 입력도 필터도 만들지 않는다 —
            **효과음까지 없으면 #19~#22와 정확히 같은 명령이 나온다.**

    Raises:
        AudioMixError: 장면 수와 구간 수가 어긋나거나, `sfx` 값이 번들에 없는 이름이거나,
            음악 값이 계약 밖일 때. **인코딩을 시작하기 전에 걸린다** (#20의 폰트 검증과 같은
            자리).
    """
    for key, value in (("sfx_volume", sfx_volume), ("voice_volume", voice_volume)):
        if value < 0:
            raise AudioMixError(f"audio.{key}는 0 이상이어야 한다. 받은 값: {value}")

    cue_list = [] if sfx_volume == 0 else cues(scenes, frame_spans, fps=fps)
    if not cue_list and music is None:
        return voice_only(voice_volume)

    # 같은 소리를 여러 번 놓아도 입력은 하나다. 디코드를 나누는 것은 `asplit`이고, 입력을
    # 이름마다 하나로 묶으면 비프 3개에 파일을 세 번 열지 않는다.
    order: list[str] = []
    for cue in cue_list:
        if cue.name not in order:
            order.append(cue.name)

    inputs: list[str] = []
    steps: list[str] = [f"[{VOICE_STREAM}]{_voice(voice_volume)}[voice]"]
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
        if sfx_volume != UNITY_GAIN:
            chain += f",volume={sfx_volume:g}"
        if len(branches) > 1:
            chain += f",asplit={len(branches)}"
        steps.append(f"[{index}:a]{chain}" + "".join(f"[{name}]" for name in branches))

        for branch, frame in zip(branches, frames, strict=True):
            # `adelay`는 밀리초를 받는다. 프레임 번호에서 직접 환산해야 장면 시작 시각을
            # `duration`으로 다시 누적하는 경로가 생기지 않는다 (PRD 7.7).
            steps.append(f"[{branch}]adelay={round(frame * 1000 / fps)}:all=1[{branch}d]")
            labels.append(f"[{branch}d]")

    if music is not None:
        # **음악 입력은 효과음 뒤다** (#35). 앞에 끼우면 효과음 인덱스가 전부 밀려, 음악을
        # 지정한 실행과 지정하지 않은 실행의 명령이 효과음 자리에서도 달라진다.
        inputs += ["-i", str(music.path)]
        steps.append(
            f"[{FIRST_SFX_INPUT + len(order)}:a]"
            + _music_chain(music, scenes, frame_spans, fps=fps)
            + f"[{MUSIC_LABEL}]"
        )
        labels.append(f"[{MUSIC_LABEL}]")

    steps.append(
        # `normalize=0`이라야 입력 수로 음량을 나누지 않는다 (`timeline.mix_voice_track`과
        # 같은 이유). `duration=longest`는 마지막 효과음이 낭독보다 늦게 끝나는 경우를 담고,
        # `apad`가 그 뒤를 프레임 정렬 길이까지 메운다 — 총 길이는 렌더의 `-t`가 끊는다.
        "".join(labels)
        + f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad,"
        + f"{_limiter()}[{OUTPUT_LABEL}]"
    )

    LOGGER.debug(
        "효과음 %d개 배치 — 효과음 게인 %g, 낭독 게인 %g, 입력 %d개",
        len(cue_list), sfx_volume, voice_volume, len(order),
    )
    return AudioChain(steps=tuple(steps), inputs=tuple(inputs))


def check_music_format(value: str) -> None:
    """배경음악 경로의 확장자를 확인한다 (#35). **파일이 있는지는 보지 않는다.**

    `video_renderer.background_kind`와 같은 자리다 — 이름만으로 답할 수 있어야 앱이 파일을
    고르는 순간에 거부할 수 있고, 없는 파일은 경로를 푸는 쪽이 경로와 함께 말한다.

    Raises:
        AudioMixError: 받지 않는 확장자일 때. 받는 형식을 함께 말한다.
    """
    if Path(value).suffix.lower() not in MUSIC_FILE_EXTENSIONS:
        raise AudioMixError(
            f"배경음악으로 받지 않는 형식이다: {value}. "
            f"받는 형식: {', '.join(MUSIC_FILE_EXTENSIONS)}"
        )


def _music_chain(
    music: Music,
    scenes: Mapping[str, Any],
    frame_spans: Sequence[tuple[int, int]],
    *,
    fps: int,
) -> str:
    """음악 입력 하나에 걸 필터 체인 — 규격 변환 → 게인 → 낭독 구간 감쇠.

    **게인이 감쇠 앞이다.** `Music.duck`이 음악 자신의 레벨에 대한 배수이므로, 뒤에 곱하는
    쪽이 사람이 음악을 키웠을 때도 같은 비율을 유지한다 (`_voice`의 게인 자리와 같은 규칙).

    라이선스 확인 책임을 여기서 run.log에 남긴다 (PRD 8장). **대화형 확인을 두지 않는다** —
    배치 실행이 거기서 멈춘다. 경로를 손으로 적은 것이 사용자의 확인 행위이고, 남는 것은 그
    사실의 기록이다.
    """
    _check_music(music)
    windows = narration_windows(scenes, frame_spans, fps=fps)
    LOGGER.info(
        "배경음악 %s — 게인 %g, 낭독 구간 %d곳에서 %g배로 낮춘다(전환 %.2f초). "
        "이 파일의 라이선스 확인 책임은 경로를 지정한 사용자에게 있다 (PRD 8장)",
        music.path, music.volume, len(windows), music.duck, music.fade_sec,
    )

    chain = _format()
    if music.volume != UNITY_GAIN:
        chain += f",volume={music.volume:g}"
    duck = _duck(windows, music, fps=fps)
    if duck:
        chain += f",{duck}"
    return chain


def _check_music(music: Music) -> None:
    """음악 값 셋이 계약 안에 있는지 (#35).

    스키마가 이미 보지만 앱이 만든 프로젝트와 사람이 손으로 고친 `project.json`이 직접
    들어온다 (`video_renderer._motion_strength`와 같은 이유). **인코딩 전에 걸린다.**

    Raises:
        AudioMixError: 게인이 음수이거나, 감쇠 배수가 0~1 밖이거나, 전환 시간이 0 이하일 때.
    """
    if music.volume < 0:
        raise AudioMixError(
            f"audio.music_volume은 0 이상이어야 한다. 받은 값: {music.volume}"
        )
    if not 0 <= music.duck <= UNITY_GAIN:
        # 1 위는 "낭독 구간에서 음악을 키운다"는 뜻이 되고, 그것은 ducking이 아니라 반대
        # 동작이다 — 계약이 막지 않으면 값과 이름이 갈린다.
        raise AudioMixError(
            f"audio.music_duck은 0 이상 1 이하여야 한다. 받은 값: {music.duck}"
        )
    if music.fade_sec <= 0:
        raise AudioMixError(
            "audio.music_duck_fade_sec은 0보다 커야 한다 — 계단 전환은 클릭으로 들린다. "
            f"받은 값: {music.fade_sec}"
        )


def narration_windows(
    scenes: Mapping[str, Any], frame_spans: Sequence[tuple[int, int]], *, fps: int
) -> list[tuple[int, int]]:
    """낭독이 들리는 프레임 구간들 (#35). 순서는 장면 순서다.

    **검출이 아니라 계산이다.** `narration_offset`은 영상 시작 기준 시각이고
    (`timeline._place`, `captions.py`가 같은 값을 큐 시작으로 쓴다) `audio_duration`은 #15가
    실측해 기록한 세그먼트 길이라, 낭독 구간은 두 필드의 합으로 이미 정해져 있다.

    초를 프레임으로 바꾸는 것은 `_frames`와 같은 반올림이고, 구간 끝은 **영상 끝을 넘지
    않는다** — 사람이 줄인 길이(#82)에서 낭독이 영상보다 길어질 수 있고, 그 뒤로 감쇠를
    이어 갈 프레임이 없다.

    **`narration_offset`은 오버라이드를 얹기 전의 값이다** (#77의 갈림과 같은 자리). 사람이
    장면 길이를 고친 run에서는 `voice.mp3`의 실제 배치가 이 값과 다를 수 있고, 그 상태를
    "낡음"으로 표시하는 것은 앱(`review.timeline_stale`)이다 — 여기서 맞추려 들면 오버라이드
    규칙을 아는 자리가 하나 더 생긴다.
    """
    scene_list = list(scenes["scenes"])
    if len(frame_spans) != len(scene_list):
        raise AudioMixError(
            f"장면 {len(scene_list)}개에 구간이 {len(frame_spans)}개다 — "
            "프레임 정렬과 장면 목록이 어긋났다"
        )

    limit = frame_spans[-1][1] if frame_spans else 0
    windows: list[tuple[int, int]] = []
    for scene in scene_list:
        offset, length = scene.get("narration_offset"), scene.get("audio_duration")
        if offset is None or length is None:
            # 낭독이 없는 장면(`countdown`·`hook`·`cta`)과, 오디오 필드가 채워지기 전의
            # 장면 목록이 여기 온다 — 감쇠할 구간이 없는 것이 정상 상태다.
            continue
        start = min(_frames(float(offset), fps), limit)
        end = min(start + _frames(float(length), fps), limit)
        if end > start:
            windows.append((start, end))
    return windows


def _duck(
    windows: Sequence[tuple[int, int]], music: Music, *, fps: int
) -> str:
    """낭독 구간에서 음악을 낮추는 `volume` 한 단계. 낮출 것이 없으면 빈 문자열.

    **`enable`이 아니라 표현식이다.** 이 필터는 timeline을 지원하지만(`enable` 옵션이 있다)
    그것으로 켜고 끄면 감쇠가 계단이라 전환마다 파형이 끊긴다 — 실측에서 샘플 간 최대 변화량이
    정상 구간의 8.6배였다. `volume`의 값 자체가 표현식을 받고(`eval=frame`) `t`를 쓸 수 있으므로
    구간을 사다리꼴로 적어 램프를 만든다 (같은 실측에서 1.2~1.4배).

    **`eval`을 빼면 감쇠가 아예 걸리지 않는다** — 기본값이 `once`라 표현식이 첫 프레임에서 한 번
    평가되고 그 값이 트랙 전체에 걸린다. 경고가 없고 레벨만 조금 달라져 있다.

    **시각을 프레임 단위로 적는다** (`t*fps`). 초로 반올림한 값을 적으면 감쇠가 한 프레임
    늦게 시작할 수 있고, 경계의 단일 진실 공급원은 프레임 번호다 (`overlay._enable`).
    빼기가 음수 리터럴이 되지 않게 `t*fps-a+f` 꼴을 유지한다 — `t*30--3`은 파싱되지 않는다.

    값 검증은 `_check_music`이 이미 했다 — 이 함수가 만드는 것은 표현식뿐이다.
    """
    if not windows or music.duck == UNITY_GAIN:
        # 낭독이 없는 장면 목록과 감쇠 없음. **필터를 넣지 않는 것이 그 사실의 표현이다**
        # (`_voice`의 게인 1과 같은 규칙).
        return ""

    fade = max(1, _frames(music.fade_sec, fps))
    merged = _merge(windows, gap=2 * fade)
    shape = _window_term(merged[0], fade, fps=fps)
    for window in merged[1:]:
        # 겹치지 않는 구간들이므로 합이 아니라 최대다. 합으로 적으면 붙은 두 구간에서 감쇠가
        # 두 번 곱해져 음악이 계약보다 더 내려간다.
        shape = f"max({shape}\\,{_window_term(window, fade, fps=fps)})"
    depth = UNITY_GAIN - music.duck
    return f"volume='1-{depth:g}*{shape}':eval=frame"


def _merge(
    windows: Sequence[tuple[int, int]], *, gap: int
) -> list[tuple[int, int]]:
    """`gap` 프레임보다 가까운 구간들을 하나로 합친다.

    **전환 둘이 들어가지 못하는 틈은 틈이 아니다.** 램프가 올라가는 도중에 다시 내려가면
    음악이 제 레벨로 돌아오지 못하고 출렁이기만 하는데, 그것은 두 문제 사이의 짧은 간격에서
    바로 나온다 (기본값에서 전환 0.25초 두 번 = 0.5초).
    """
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start - merged[-1][1] < gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    return merged


def _window_term(window: tuple[int, int], fade: int, *, fps: int) -> str:
    """구간 하나의 사다리꼴 — 감쇠가 0에서 1까지 올랐다 내려온다.

    `a`에서 감쇠가 이미 완전히 걸려 있어야 하므로 램프는 `a - fade`에서 시작하고, 풀리는
    쪽은 `b`부터 `b + fade`까지다. 두 램프의 최소값을 [0, 1]로 자르면 사다리꼴이 된다.
    """
    start, end = window
    rise = f"t*{fps}-{start}+{fade}"
    fall = f"{end}+{fade}-t*{fps}"
    return f"min(max(min({rise}\\,{fall})/{fade}\\,0)\\,1)"


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


def _voice(volume: float) -> str:
    """낭독 입력을 규격에 맞추고 게인을 건다 (#81).

    **게인은 규격 변환 다음, 지연·믹스 앞이다.** 효과음이 `volume`을 `asplit` 앞에 두는 것과
    같은 자리라, 트랙 하나에 게인이 한 번만 걸린다.
    """
    chain = _format()
    if volume != UNITY_GAIN:
        chain += f",volume={volume:g}"
    return chain


def _limiter() -> str:
    """최종 단의 리미터. 낭독만 있는 체인과 믹스가 같은 문자열을 쓴다 (#23, #81)."""
    return f"alimiter=limit={LIMITER_CEILING}:level=disabled:latency=true"
