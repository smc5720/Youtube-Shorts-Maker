"""효과음 믹싱 — 이슈 #23의 완료 조건.

**두 층으로 갈라져 있다** (`test_video_renderer.py`와 같은 이유). 트리거 시각과 필터 문자열은
FFmpeg 없이 확인하고, 레벨·클리핑·실제 배치 시각은 진짜 FFmpeg로 렌더해 측정한다. 후자는
FFmpeg가 없는 환경에서 건너뛴다.

**소리가 놓이는 프레임을 오버레이에서 다시 계산하지 않는다.** 기대값을
`overlay.countdown_windows`와 `overlay.ANSWER_ONSET_SEC`에서 가져오는 것이 이 파일의 요점이다 —
숫자 하나를 손으로 적으면 그림과 소리가 갈리는 회귀를 잡지 못한다.
"""

from __future__ import annotations

import array
import logging
import math
import re
import shutil
import subprocess
import wave
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import audio_mix, overlay, video_renderer
from shorts_maker.assets import sfx_path
from shorts_maker.audio_mix import AudioMixError, build, cues, voice_only
from shorts_maker.schemas.project import (
    DEFAULT_MUSIC_DUCK,
    DEFAULT_MUSIC_DUCK_FADE_SEC,
    DEFAULT_MUSIC_VOLUME,
    DEFAULT_VOICE_VOLUME,
)
from shorts_maker.video_renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    FPS,
    OUTPUT_NAME,
    RenderError,
    align,
    render,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg가 없다 — 명령 생성 테스트만 돈다",
)

COUNTDOWN_SFX = "beep"
ANSWER_SFX = "correct"
"""장면 템플릿이 내는 이름 (`types/quiz/scene_template.py`). **타입 패키지를 import하지 않는다**
— 이 모듈은 공통 파이프라인이고 경계 테스트가 그것을 강제한다 (`test_type_boundary.py`)."""

NARRATION_PEAK_DBFS = -4.81
"""낭독 실측 peak — edge_tts `ko-KR-SunHiNeural`(기본 음성)로 한국어 한 문장을 합성해
`astats`로 쟀다 (이슈 #23). 효과음이 이보다 충분히 아래인지 보는 기준이다."""

LEVEL_MARGIN_DB = 8.0
"""낭독 대비 효과음의 최소 여유. 대사 우선 믹스의 통상 범위(8~12dB) 하한이고, 기본값에서
실측 여유는 9.5dB다 — 이 값은 기본 게인이나 소스 파일이 커지면 깨지는 하한선이다."""

AAC_OVERSHOOT_DB = 0.5
"""리미터 상한 대비 허용 초과분. 최종 컨테이너는 AAC라 디코드한 파형이 인코딩 전 피크를
조금 넘는다 — 리미터가 걸렸는지 보는 검사이므로 손실 압축의 몫을 남긴다.

실측(#23) — 리미터 출력은 정확히 -1.00 dBFS이고, 그것을 AAC로 인코딩해 디코드하면 톤에서
**0.1dB**, 광대역 노이즈에서 **2.6dB** 넘는다. 아래 시험 트랙이 노이즈가 아니라 톤인 이유가
이것이다. 노이즈로 바꾸면 이 상수가 아니라 인코더의 오버슈트를 재게 된다."""


def scenes_with(*roles: tuple[str, float, dict[str, Any]]) -> dict[str, Any]:
    """확정 검증을 통과하는 장면 목록. (역할, 길이, 추가 필드)를 순서대로 받는다."""
    scenes: list[dict[str, Any]] = []
    for index, (role, duration, extra) in enumerate(roles):
        scene: dict[str, Any] = {"role": role, "duration": round(duration, 3)}
        if role == "countdown":
            scene |= {"question_id": 1, "heading": "질문", "seconds": int(duration)}
        elif role == "answer":
            scene |= {
                "question_id": 1,
                "heading": "질문",
                "text": "정답",
                "narrate": True,
                "target_duration": 3.0,
                "audio": f"audio/seg-{index:03d}.mp3",
                "audio_duration": 0.5,
                "narration_offset": 0.3,
            }
        else:
            scene |= {"text": "문구"}
        scenes.append(scene | extra)
    return {"schema_version": 1, "type": "quiz", "scenes": scenes}


def quiz_block() -> dict[str, Any]:
    """hook → countdown(3초) → answer. 두 종류의 효과음이 한 번에 들어간다."""
    return scenes_with(
        ("hook", 2.5, {}),
        ("countdown", 3.0, {"sfx": COUNTDOWN_SFX}),
        ("answer", 2.0, {"sfx": ANSWER_SFX}),
    )


def chain_for(
    scenes: dict[str, Any],
    *,
    sfx_volume: float = 1.0,
    voice_volume: float = 1.0,
    music: audio_mix.Music | None = None,
) -> audio_mix.AudioChain:
    aligned = align(scenes)
    return build(
        scenes,
        aligned.frame_spans,
        fps=aligned.fps,
        sfx_volume=sfx_volume,
        voice_volume=voice_volume,
        music=music,
    )


def delays(chain: audio_mix.AudioChain) -> list[int]:
    """체인이 만든 `adelay` 밀리초 값. 순서는 필터 단계 순서다."""
    return [
        int(found.group(1))
        for step in chain.steps
        if (found := re.search(r"adelay=(\d+):all=1", step))
    ]


# --- 트리거 시각 (FFmpeg 없이) -----------------------------------------------


def test_a_beep_lands_on_every_digit_transition() -> None:
    """숫자가 갈리는 프레임과 같은 값이어야 한다 — 기대값을 오버레이에서 가져온다."""
    scenes = quiz_block()
    aligned = align(scenes)
    span = aligned.frame_spans[1]

    found = cues(scenes, aligned.frame_spans, fps=aligned.fps)

    beeps = [cue.frame for cue in found if cue.name == COUNTDOWN_SFX]
    assert beeps == [
        start for _, (start, _) in overlay.countdown_windows(3, span, fps=aligned.fps)
    ]
    assert len(beeps) == 3


def test_the_answer_sound_fires_once_when_the_answer_appears() -> None:
    """`overlay.ANSWER_ONSET_SEC`은 등장색이 켜지는 시각이자 효과음 시각이다 (확정 스펙 5.4)."""
    scenes = quiz_block()
    aligned = align(scenes)
    start = aligned.frame_spans[2][0]

    found = [cue for cue in cues(scenes, aligned.frame_spans, fps=aligned.fps)
             if cue.name == ANSWER_SFX]

    assert len(found) == 1
    assert found[0].frame == start + round(overlay.ANSWER_ONSET_SEC * aligned.fps)


def test_a_scene_without_sfx_makes_no_sound() -> None:
    """hook에는 `sfx`가 없다 — 소리는 카운트다운 장면이 시작한 뒤부터다."""
    scenes = quiz_block()
    aligned = align(scenes)
    countdown_start = aligned.frame_spans[1][0]

    found = cues(scenes, aligned.frame_spans, fps=aligned.fps)

    assert found and all(cue.frame >= countdown_start for cue in found)


def test_an_unknown_role_with_sfx_sounds_at_the_scene_start() -> None:
    """역할 목록을 여기서 닫지 않는다 — 새 역할이 `sfx`를 들면 장면 시작이 기본이다."""
    scenes = scenes_with(("hook", 2.5, {"sfx": ANSWER_SFX}))
    aligned = align(scenes)

    found = cues(scenes, aligned.frame_spans, fps=aligned.fps)

    assert [cue.frame for cue in found] == [0]


def test_a_countdown_without_seconds_warns_and_stays_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """앱이 만든 프로젝트는 확정 검증을 지나지 않을 수 있다 (`overlay.draw_countdown`과 같다)."""
    scenes = quiz_block()
    scenes["scenes"][1]["seconds"] = 0

    with caplog.at_level(logging.WARNING):
        found = cues(scenes, align(scenes).frame_spans, fps=FPS)

    assert [cue.name for cue in found] == [ANSWER_SFX]
    assert "비프를 놓지 않는다" in caplog.text


def test_a_short_countdown_places_only_the_beeps_that_fit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenes = quiz_block()
    scenes["scenes"][1]["duration"] = 1.5

    with caplog.at_level(logging.WARNING):
        found = cues(scenes, align(scenes).frame_spans, fps=FPS)

    assert len([cue for cue in found if cue.name == COUNTDOWN_SFX]) == 2
    assert "비프 2개만" in caplog.text


def test_a_scene_shorter_than_the_onset_stays_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """사람이 길이를 줄인 `scenes.json`에서 트리거가 다음 장면으로 새지 않는다."""
    scenes = scenes_with(("hook", 0.1, {"sfx": ANSWER_SFX}))
    scenes["scenes"][0]["role"] = "answer"
    scenes["scenes"][0]["text"] = "정답"

    with caplog.at_level(logging.WARNING):
        found = cues(scenes, align(scenes).frame_spans, fps=FPS)

    assert found == []
    assert "놓지 않는다" in caplog.text


def test_a_span_count_that_does_not_match_the_scenes_is_an_error() -> None:
    scenes = quiz_block()

    with pytest.raises(AudioMixError, match="프레임 정렬"):
        cues(scenes, ((0, 30),), fps=FPS)


# --- 필터 체인 (FFmpeg 없이) -------------------------------------------------


def test_the_delays_come_from_the_frame_numbers() -> None:
    """장면 시작 시각을 `duration`으로 다시 누적하지 않는다 (PRD 7.7)."""
    scenes = quiz_block()
    aligned = align(scenes)
    found = cues(scenes, aligned.frame_spans, fps=aligned.fps)

    chain = chain_for(scenes)

    assert delays(chain) == [round(cue.frame * 1000 / aligned.fps) for cue in found]


def test_one_input_carries_a_sound_used_several_times() -> None:
    """비프 3개에 파일을 세 번 열지 않는다 — 디코드를 나누는 것은 `asplit`이다."""
    chain = chain_for(quiz_block())

    assert chain.inputs.count("-i") == 2
    assert str(sfx_path(COUNTDOWN_SFX)) in chain.inputs
    assert "asplit=3" in " ".join(chain.steps)


def test_the_sfx_inputs_start_after_the_voice_input() -> None:
    """입력 0은 배경, 1은 낭독이다. 인덱스가 어긋나면 낭독을 효과음으로 지연시킨다."""
    chain = chain_for(quiz_block())

    assert f"[{audio_mix.FIRST_SFX_INPUT}:a]" in " ".join(chain.steps)
    assert f"[{audio_mix.VOICE_STREAM}]" in chain.steps[0]


def test_the_mix_keeps_the_levels_and_pads_to_the_end() -> None:
    graph = " ".join(chain_for(quiz_block()).steps)

    assert "amix=inputs=5:normalize=0:duration=longest" in graph
    assert "apad" in graph
    assert graph.endswith(f"[{audio_mix.OUTPUT_LABEL}]")


def test_the_limiter_neither_auto_levels_nor_delays() -> None:
    """`level` 기본값은 auto level이고, `latency`를 빼면 5ms 늦는다 (#23 실측)."""
    graph = " ".join(chain_for(quiz_block()).steps)

    assert f"alimiter=limit={audio_mix.LIMITER_CEILING}" in graph
    assert "level=disabled" in graph
    assert "latency=true" in graph


def test_the_gain_is_applied_once_before_the_split() -> None:
    graph = " ".join(chain_for(quiz_block(), sfx_volume=0.5).steps)

    assert graph.count("volume=0.5") == 2  # 효과음 두 종류에 각각 한 번
    assert graph.index("volume=0.5") < graph.index("asplit=3")


def test_the_default_gain_adds_no_volume_filter() -> None:
    """기본값에서 추가 감쇠가 필요하지 않다 — 필터를 넣지 않는 것이 그 사실의 표현이다."""
    assert "volume=" not in " ".join(chain_for(quiz_block()).steps)


# --- 낭독 볼륨 (#81) ---------------------------------------------------------


def test_the_voice_gain_lands_on_the_voice_branch_only() -> None:
    """게인이 낭독 입력에만 걸린다 — 효과음 가지에 얹히면 두 트랙이 함께 움직인다."""
    chain = chain_for(quiz_block(), voice_volume=0.5)

    voice_step = chain.steps[0]
    assert voice_step.startswith(f"[{audio_mix.VOICE_STREAM}]")
    assert "volume=0.5" in voice_step
    assert "volume=0.5" not in " ".join(chain.steps[1:])


def test_the_default_voice_gain_adds_no_volume_filter() -> None:
    """게인 1은 원본 레벨이고, 필터를 넣지 않는 것이 그 사실의 표현이다."""
    assert "volume=" not in voice_only(audio_mix.UNITY_GAIN).steps[0]
    assert voice_only() == voice_only(1)


def test_a_lowered_voice_gain_needs_no_limiter() -> None:
    """트랙 하나를 원본 레벨 아래로 내린 체인에는 넘칠 것이 없다."""
    chain = voice_only(0.3)

    assert "volume=0.3" in chain.steps[0]
    assert "alimiter" not in chain.steps[0]


def test_a_voice_gain_above_one_brings_the_limiter() -> None:
    """**#23의 규칙이 넓어진 자리다** — 효과음이 없어도 게인이 1을 넘으면 클리핑이 가능하다."""
    chain = voice_only(2.0)

    assert "volume=2" in chain.steps[0]
    assert f"alimiter=limit={audio_mix.LIMITER_CEILING}" in chain.steps[0]
    assert "level=disabled" in chain.steps[0] and "latency=true" in chain.steps[0]


def test_the_voice_gain_survives_when_the_sfx_are_off() -> None:
    """`sfx_volume: 0`이 낭독 게인까지 지우지 않는다 — 두 값은 서로 무관하다."""
    chain = chain_for(quiz_block(), sfx_volume=0, voice_volume=0.4)

    assert chain == voice_only(0.4)
    assert "volume=0.4" in chain.steps[0]


def test_a_negative_voice_gain_says_which_key_is_wrong() -> None:
    with pytest.raises(AudioMixError, match="audio.voice_volume"):
        chain_for(quiz_block(), voice_volume=-0.1)


def test_zero_volume_gives_the_voice_only_chain() -> None:
    """게인 0으로 섞어 두면 명령만 길어진다."""
    chain = chain_for(quiz_block(), sfx_volume=0)

    assert chain == voice_only()
    assert chain.inputs == ()


def test_a_scene_list_without_sfx_gives_the_voice_only_chain() -> None:
    """효과음이 없으면 #19~#22와 같은 명령이 나온다 — 리미터도 붙지 않는다."""
    chain = chain_for(scenes_with(("hook", 2.5, {}), ("cta", 3.0, {})))

    assert chain == voice_only()
    assert "alimiter" not in " ".join(chain.steps)


def test_a_negative_gain_is_an_error() -> None:
    with pytest.raises(AudioMixError, match="0 이상"):
        chain_for(quiz_block(), sfx_volume=-1.0)


def test_an_unknown_sound_lists_the_bundled_names() -> None:
    scenes = quiz_block()
    scenes["scenes"][1]["sfx"] = "tension"

    with pytest.raises(AudioMixError, match="beep"):
        chain_for(scenes)


# --- 실제 렌더 (FFmpeg 필요) -------------------------------------------------


def project_with(**audio: Any) -> dict[str, Any]:
    """`project.build`가 만드는 초기 상태와 같은 모양.

    **음악 값 셋(`music_volume`·`music_duck`·`music_duck_fade_sec`)이 기본 모양에 없다** —
    스키마에서 선택이므로 이 필드가 생기기 전에 만들어진 run 디렉터리가 그대로 렌더까지 가고,
    그 상태를 여러 테스트가 기대값으로 쓴다 (#35).
    """
    return {
        "schema_version": 1,
        "type": "quiz",
        "language": "ko",
        "scenes": "scenes.json",
        "background": {"kind": "preset", "value": "deep_navy"},
        "audio": {"voice": None, "music": None, "sfx_volume": 1.0, **audio},
        "render": {
            "width": CANVAS_WIDTH,
            "height": CANVAS_HEIGHT,
            "fps": FPS,
            "output": OUTPUT_NAME,
            "caption_style": "impact_yellow",
            "font_path": None,
            "cta_punch": "구독 · 좋아요",
            "cta_tail": "매일 새 상식 퀴즈",
            "caption_onset_sec": 0.90,
        },
    }


LOUD_GAIN_DB = 18
LOUD_FLOOR_DBFS = -6.0
"""리미터 시험용 낭독 트랙의 게인과, 시험이 성립하는 하한.

`sine` 소스의 출력은 **-18 dBFS**이므로(#23 실측) 여기에 18dB를 더해 거의 꽉 찬 트랙을 만든다.
빌드가 그 소스 레벨을 바꾸면 게인 상수가 어긋나는데, 그때 조용한 트랙으로 통과하지 않도록
쓰는 쪽이 하한을 확인한다 — 하한 위라면 효과음 4배(-2.3 dBFS)를 더한 합이 1.0을 넘는다.
"""


def loud_voice_track(run_dir: Path, *, total_sec: float) -> Path:
    """거의 0 dBFS까지 찬 낭독 트랙. 효과음을 4배로 얹으면 합이 1.0을 넘는 조건이다."""
    track = run_dir / "voice.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", f"{total_sec:.3f}",
         "-i", "sine=frequency=220:sample_rate=44100", "-af",
         f"aformat=channel_layouts=stereo,volume={LOUD_GAIN_DB}dB", str(track)],
        check=True,
        capture_output=True,
    )
    return track


_LEVEL = re.compile(r"(Peak|RMS) level dB:\s*(-?\d+(?:\.\d+)?|-?inf)")


def levels(path: Path, *, span: tuple[float, float] | None = None) -> dict[str, float]:
    """`astats`의 peak·RMS. 측정 명령은 `assets/sfx/CREDITS.md`와 같은 계열이다.

    `span`을 주면 그 구간만 잰다 (#35의 ducking 검증 — 낭독 구간과 그 밖을 같은 파일에서
    비교해야 하므로 구간이 필요하다).
    """
    window = [] if span is None else ["-ss", f"{span[0]:.3f}", "-to", f"{span[1]:.3f}"]
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", *window, "-i", str(path), "-af",
         "astats=measure_perchannel=none:measure_overall=Peak_level+RMS_level",
         "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    found = {kind: float(value) for kind, value in _LEVEL.findall(completed.stderr)}
    assert {"Peak", "RMS"} <= set(found), completed.stderr[-2000:]
    return found


def sound_onsets(path: Path, *, total_sec: float) -> list[float]:
    """소리가 시작하는 시각들. 무음 구간이 끝나는 지점이 트리거다.

    **스트림 끝의 표시는 버린다.** `silencedetect`는 무음으로 끝난 파일의 마지막 구간도
    `silence_end`로 닫으므로, 그 값을 트리거로 세면 항상 하나가 더 나온다.
    """
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path), "-af",
         "silencedetect=noise=-50dB:d=0.05", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    found = [
        float(line.split("silence_end:")[1].split("|")[0])
        for line in completed.stderr.splitlines()
        if "silence_end" in line
    ]
    return [onset for onset in found if onset < total_sec - 1 / FPS]


@needs_ffmpeg
def test_the_sounds_land_within_a_frame_of_the_computed_times(tmp_path: Path) -> None:
    """계산한 프레임과 실제 파형의 시각이 한 프레임(33ms) 안에서 맞는지."""
    scenes = quiz_block()
    aligned = align(scenes)
    expected = [
        cue.frame / aligned.fps
        for cue in cues(scenes, aligned.frame_spans, fps=aligned.fps)
    ]

    output = render(project_with(), scenes, run_dir=tmp_path)

    onsets = sound_onsets(output, total_sec=aligned.total_sec)
    assert len(onsets) == len(expected)
    for detected, wanted in zip(onsets, expected, strict=True):
        assert detected == pytest.approx(wanted, abs=1 / FPS)


@needs_ffmpeg
def test_the_sounds_stay_below_the_narration() -> None:
    """기본 게인에서 효과음이 낭독보다 8dB 이상 낮다 (#18의 정규화 + #23의 실측)."""
    for name in (COUNTDOWN_SFX, ANSWER_SFX):
        peak = levels(sfx_path(name))["Peak"]

        assert NARRATION_PEAK_DBFS - peak >= LEVEL_MARGIN_DB


@needs_ffmpeg
def test_turning_the_sfx_off_leaves_the_audio_untouched(tmp_path: Path) -> None:
    """`sfx_volume: 0`의 결과가 효과음 없는 장면 목록의 결과와 같은 레벨이어야 한다."""
    scenes = quiz_block()
    for name in ("off", "plain"):
        (tmp_path / name).mkdir()
    silent = render(project_with(sfx_volume=0), scenes, run_dir=tmp_path / "off")
    for scene in scenes["scenes"]:
        scene.pop("sfx", None)
    plain = render(project_with(), scenes, run_dir=tmp_path / "plain")

    measured = levels(silent)
    assert measured["Peak"] == pytest.approx(levels(plain)["Peak"], abs=0.01)
    assert measured["RMS"] == pytest.approx(levels(plain)["RMS"], abs=0.01)


@needs_ffmpeg
def test_the_narration_keeps_its_level_when_sounds_are_added(tmp_path: Path) -> None:
    """`normalize=0`이라야 입력 수로 음량을 나누지 않는다 — 효과음을 얹어도 낭독이 줄지 않는다."""
    scenes = quiz_block()
    for name in ("mixed", "voice"):
        (tmp_path / name).mkdir()
    total = align(scenes).total_sec
    for name in ("mixed", "voice"):
        loud_voice_track(tmp_path / name, total_sec=total)
    with_sfx = render(
        project_with(voice="voice.mp3"), scenes, run_dir=tmp_path / "mixed"
    )
    for scene in scenes["scenes"]:
        scene.pop("sfx", None)
    alone = render(project_with(voice="voice.mp3"), scenes, run_dir=tmp_path / "voice")

    mixed, plain = levels(with_sfx), levels(alone)
    # 낭독은 그대로 남고(피크가 낮아지지 않는다) 효과음만큼 에너지가 늘어난다.
    assert mixed["Peak"] >= plain["Peak"] - AAC_OVERSHOOT_DB
    assert mixed["RMS"] > plain["RMS"]


@needs_ffmpeg
def test_a_loud_gain_still_does_not_clip(tmp_path: Path) -> None:
    """리미터가 없으면 낭독 + 효과음 합이 0 dBFS를 넘는다."""
    scenes = quiz_block()
    track = loud_voice_track(tmp_path, total_sec=align(scenes).total_sec)
    # 시험 조건을 확인한다. 낭독이 조용하면 리미터가 걸리지 않아 통과가 의미를 잃는다.
    assert levels(track)["Peak"] >= LOUD_FLOOR_DBFS
    project = project_with(voice="voice.mp3", sfx_volume=4.0)

    output = render(project, scenes, run_dir=tmp_path)

    ceiling = 20 * math.log10(audio_mix.LIMITER_CEILING)
    assert levels(output)["Peak"] <= ceiling + AAC_OVERSHOOT_DB


def silent_block() -> dict[str, Any]:
    """효과음이 없는 장면 목록. **낭독 게인만 재려면 필요하다** — 효과음이 있으면 낭독을
    내렸을 때 피크가 그쪽에서 정해져 게인이 얼마나 걸렸는지가 측정에서 사라진다."""
    scenes = quiz_block()
    for scene in scenes["scenes"]:
        scene.pop("sfx", None)
    return scenes


@needs_ffmpeg
def test_lowering_the_voice_gain_lowers_the_narration(tmp_path: Path) -> None:
    """슬라이더 100 → 50(게인 0.5)이 최종 mp4의 낭독을 6dB 내린다 (#81 완료 조건)."""
    scenes = silent_block()
    total = align(scenes).total_sec
    for name in ("full", "half"):
        (tmp_path / name).mkdir()
        loud_voice_track(tmp_path / name, total_sec=total)

    full = render(project_with(voice="voice.mp3"), scenes, run_dir=tmp_path / "full")
    half = render(
        project_with(voice="voice.mp3", voice_volume=0.5), scenes, run_dir=tmp_path / "half"
    )

    wanted = 20 * math.log10(2)  # 게인 0.5 = -6.02dB
    loud, quiet = levels(full), levels(half)
    assert loud["Peak"] - quiet["Peak"] == pytest.approx(wanted, abs=AAC_OVERSHOOT_DB)
    assert loud["RMS"] - quiet["RMS"] == pytest.approx(wanted, abs=AAC_OVERSHOOT_DB)


@needs_ffmpeg
def test_a_loud_voice_gain_does_not_clip_without_any_sfx(tmp_path: Path) -> None:
    """**효과음이 없어도 리미터가 붙는다** (#81). 넘칠 수 있는 경우가 하나 늘었다."""
    scenes = silent_block()
    track = loud_voice_track(tmp_path, total_sec=align(scenes).total_sec)
    # 시험 조건을 확인한다. 낭독이 조용하면 4배로도 넘지 않아 통과가 의미를 잃는다.
    assert levels(track)["Peak"] >= LOUD_FLOOR_DBFS

    output = render(
        project_with(voice="voice.mp3", voice_volume=4.0), scenes, run_dir=tmp_path
    )

    ceiling = 20 * math.log10(audio_mix.LIMITER_CEILING)
    assert levels(output)["Peak"] <= ceiling + AAC_OVERSHOOT_DB


@needs_ffmpeg
def test_the_stream_count_does_not_change_when_sounds_are_added(tmp_path: Path) -> None:
    """오디오 스트림은 항상 정확히 하나다 (PRD 7.7)."""
    output = render(project_with(), quiz_block(), run_dir=tmp_path)

    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.split() == ["video", "audio"]


@needs_ffmpeg
def test_an_unknown_sound_stops_before_encoding(tmp_path: Path) -> None:
    """오버레이의 폰트 검증과 같은 자리다 — 인코딩 비용을 쓰기 전에 걸린다."""
    scenes = quiz_block()
    scenes["scenes"][1]["sfx"] = "tension"

    with pytest.raises(RenderError, match="효과음"):
        render(project_with(), scenes, run_dir=tmp_path)

    assert not (tmp_path / OUTPUT_NAME).exists()


def test_a_project_without_the_gain_says_which_key_is_missing(tmp_path: Path) -> None:
    """앱이 만든 프로젝트나 사람이 고친 파일이 스키마를 지나지 않고 들어올 수 있다."""
    project = project_with()
    del project["audio"]["sfx_volume"]

    with pytest.raises(RenderError, match="audio.sfx_volume"):
        video_renderer.build_audio(
            project, quiz_block(), timeline=align(quiz_block()), run_dir=tmp_path
        )


def test_a_project_without_the_voice_gain_renders_at_the_original_level(
    tmp_path: Path,
) -> None:
    """**없는 것이 계약 위반이 아니다** (#81) — 이 필드가 생기기 전의 run 디렉터리가 열린다.

    `sfx_volume`과 갈리는 이유는 계약의 성격이 아니라 도입 시점뿐이고, 그때의 뜻은
    `DEFAULT_VOICE_VOLUME`(원본 레벨)이다.
    """
    project = project_with()
    assert "voice_volume" not in project["audio"]
    scenes = quiz_block()

    chain = video_renderer.build_audio(
        project, scenes, timeline=align(scenes), run_dir=tmp_path
    )

    assert chain == chain_for(scenes, voice_volume=DEFAULT_VOICE_VOLUME)


def test_the_voice_gain_reaches_the_chain_from_the_project(tmp_path: Path) -> None:
    """`project.json` → 렌더러 경로. config에서 다시 읽으면 앱의 편집이 무시된다 (PRD 7.10)."""
    scenes = quiz_block()

    chain = video_renderer.build_audio(
        project_with(voice_volume=0.25), scenes, timeline=align(scenes), run_dir=tmp_path
    )

    assert "volume=0.25" in chain.steps[0]


def test_a_negative_voice_gain_in_the_project_is_a_render_error(tmp_path: Path) -> None:
    scenes = quiz_block()

    with pytest.raises(RenderError, match="audio.voice_volume"):
        video_renderer.build_audio(
            project_with(voice_volume=-1.0),
            scenes,
            timeline=align(scenes),
            run_dir=tmp_path,
        )


# --- 배경음악과 ducking (#35) -------------------------------------------------


NARRATION_START = 3.3
NARRATION_SEC = 2.0
"""음악 테스트가 쓰는 낭독 구간. **장면 한가운데다** — 감쇠 구간과 그 밖을 같은 파일에서
비교하려면 앞뒤로 전환(기본 0.25초)이 들어갈 여유가 있어야 한다."""

MUSIC_NAME = "music.mp3"

MUSIC = audio_mix.Music(
    path=Path(MUSIC_NAME),
    volume=DEFAULT_MUSIC_VOLUME,
    duck=DEFAULT_MUSIC_DUCK,
    fade_sec=DEFAULT_MUSIC_DUCK_FADE_SEC,
)
"""기본값으로 만든 음악 하나. 경로는 필터 문자열에만 쓰이므로 파일이 없어도 된다 —
존재 확인은 `video_renderer._source_file`의 몫이다."""

RAMP_STEP_LIMIT = 2.0
"""감쇠 전환의 샘플 간 최대 변화량이 정상 구간의 몇 배까지 허용되는가.

**계단 전환이면 여기서 걸린다** — 필터 출력 실측에서 계단은 8.6배, 램프는 1.2배였다 (#35).
최종 컨테이너는 AAC라 디코드한 파형에 인코더의 몫이 얹히므로 그만큼 여유를 뒀다.
"""

WINDOW_TERM = "min(max(min("
"""감쇠 구간 하나가 만드는 사다리꼴의 머리 (`audio_mix._window_term`). 표현식에 이것이 몇 번
있는지가 구간 수다 — 합쳐졌는지를 그 수로 본다."""


def music_block() -> dict[str, Any]:
    """낭독이 한가운데 있는 장면 목록. 효과음은 없다 — 음악만 남겨 레벨을 재기 위함이다."""
    return scenes_with(
        ("hook", 3.0, {}),
        (
            "answer",
            4.0,
            {"audio_duration": NARRATION_SEC, "narration_offset": NARRATION_START},
        ),
    )


def narrated_pair(second_offset: float) -> dict[str, Any]:
    """낭독 장면 둘. 두 번째 낭독의 시작 시각으로 두 구간의 간격을 정한다."""
    return scenes_with(
        ("answer", 3.0, {"audio_duration": 2.0, "narration_offset": 0.3}),
        ("answer", 3.0, {"audio_duration": 2.0, "narration_offset": second_offset}),
    )


def music_file(
    run_dir: Path, *, total_sec: float, gain_db: float = 0.0, name: str = MUSIC_NAME
) -> Path:
    """음악 대역 — 330Hz 톤. **노이즈를 쓰지 않는다** (#23 실측): AAC 오버슈트가 2.6dB까지
    나서 리미터가 아니라 인코더를 재게 된다."""
    track = run_dir / name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", f"{total_sec:.3f}",
         "-i", "sine=frequency=330:sample_rate=44100", "-af",
         f"aformat=channel_layouts=stereo,volume={gain_db}dB", str(track)],
        check=True,
        capture_output=True,
    )
    return track


def max_sample_step(path: Path, span: tuple[float, float]) -> int:
    """구간의 샘플 간 최대 변화량 (16bit 모노로 디코드해 잰다).

    **클릭은 레벨이 아니라 파형의 불연속이라** `astats`의 peak·RMS에 드러나지 않는다 —
    감쇠가 계단이면 전환 프레임 하나에서 값이 튀고 그 크기가 이 값이다.
    """
    decoded = path.with_name(f"{path.stem}-{span[0]:.2f}-{span[1]:.2f}.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{span[0]:.3f}", "-to", f"{span[1]:.3f}",
         "-i", str(path), "-ac", "1", "-c:a", "pcm_s16le", str(decoded)],
        check=True,
        capture_output=True,
    )
    with wave.open(str(decoded)) as handle:
        samples = array.array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))
    assert len(samples) > 1, decoded
    return max(abs(after - before) for before, after in zip(samples, samples[1:]))


def duck_windows(scenes: dict[str, Any]) -> list[tuple[int, int]]:
    aligned = align(scenes)
    return audio_mix.narration_windows(scenes, aligned.frame_spans, fps=aligned.fps)


def music_graph(scenes: dict[str, Any], **fields: Any) -> str:
    """음악 가지의 필터 단계. 기본값에서 `fields`만 바꿔 얹는다."""
    music = replace(MUSIC, **fields)
    return next(
        step
        for step in chain_for(scenes, music=music).steps
        if step.endswith(f"[{audio_mix.MUSIC_LABEL}]")
    )


def test_the_duck_window_comes_from_the_narration_fields() -> None:
    """검출이 아니라 계산이다 — `narration_offset` + `audio_duration`이 구간을 정한다."""
    start = round(NARRATION_START * FPS)

    assert duck_windows(music_block()) == [(start, start + round(NARRATION_SEC * FPS))]


def test_a_scene_list_without_narration_has_no_window() -> None:
    """`countdown`·`hook`·`cta`만 있는 목록에는 감쇠할 구간이 없다."""
    assert duck_windows(scenes_with(("hook", 2.5, {}), ("cta", 3.0, {}))) == []


def test_a_window_never_runs_past_the_video() -> None:
    """사람이 줄인 길이(#82)에서 낭독이 영상보다 길어질 수 있다 — 그 뒤에는 프레임이 없다."""
    scenes = music_block()
    scenes["scenes"][1]["audio_duration"] = 30.0
    scenes["scenes"][1]["duration"] = 30.0

    total = align(scenes).total_frames
    assert duck_windows(scenes) == [(round(NARRATION_START * FPS), total)]


def test_the_duck_expression_names_the_window_and_the_fade() -> None:
    """구간과 전환이 프레임 번호로 적힌다 (`overlay._enable`과 같은 이유)."""
    graph = music_graph(music_block())
    start = round(NARRATION_START * FPS)
    end = start + round(NARRATION_SEC * FPS)
    fade = round(MUSIC.fade_sec * FPS)

    assert f"t*{FPS}-{start}+{fade}" in graph
    assert f"{end}+{fade}-t*{FPS}" in graph
    # 표현식이 매 프레임 다시 평가돼야 램프가 생긴다 — 기본값은 한 번뿐이다.
    assert "eval=frame" in graph
    assert f"1-{1 - MUSIC.duck:g}*" in graph


def test_neighbouring_narrations_become_one_window() -> None:
    """전환 둘이 들어가지 못하는 틈에서는 음악이 제 레벨로 돌아오지 못하고 출렁이기만 한다."""
    fade = MUSIC.fade_sec
    # 첫 낭독은 0.3~2.3초다. 틈이 전환 둘(0.5초)보다 좁으면 한 구간이 된다.
    close = music_graph(narrated_pair(2.3 + fade))
    apart = music_graph(narrated_pair(2.3 + 4 * fade))

    assert close.count(WINDOW_TERM) == 1
    assert apart.count(WINDOW_TERM) == 2
    # 구간이 둘일 때 겹치는 것은 합이 아니라 최대다 — 합이면 붙은 자리에서 두 번 곱해진다.
    assert "max(" in apart


def test_a_duck_of_one_adds_no_expression() -> None:
    """감쇠 없음. 필터를 넣지 않는 것이 그 사실의 표현이다 (`_voice`의 게인 1과 같은 규칙)."""
    graph = music_graph(music_block(), volume=1.0, duck=1.0)

    assert "eval=frame" not in graph
    assert "volume=" not in graph


def test_a_scene_list_without_narration_adds_no_expression() -> None:
    """감쇠할 구간이 없으면 음악은 게인만 지난다."""
    graph = music_graph(scenes_with(("hook", 2.5, {}), ("cta", 3.0, {})))

    assert "eval=frame" not in graph
    assert f"volume={MUSIC.volume:g}" in graph


def test_the_music_gain_lands_before_the_duck() -> None:
    """감쇠 배수는 음악 자신의 레벨에 대한 상대량이다 — 게인이 뒤에 오면 비율이 달라진다."""
    graph = music_graph(music_block())

    assert graph.index(f"volume={MUSIC.volume:g}") < graph.index("eval=frame")


def test_the_music_input_comes_after_the_sfx() -> None:
    """앞에 끼우면 효과음 인덱스가 전부 밀린다."""
    scenes = quiz_block()
    with_music = chain_for(scenes, music=MUSIC)
    without = chain_for(scenes)

    assert with_music.inputs[: len(without.inputs)] == without.inputs
    assert with_music.inputs[-2:] == ("-i", MUSIC_NAME)
    # 효과음 두 종류 뒤가 음악이다. 효과음 단계도 그대로 남는다 (마지막 `amix`만 늘어난다).
    assert f"[{audio_mix.FIRST_SFX_INPUT + 2}:a]" in with_music.steps[-2]
    assert with_music.steps[: len(without.steps) - 1] == without.steps[:-1]


def test_music_alone_still_gets_mixed_and_limited() -> None:
    """트랙이 늘면 리미터가 붙는다 (#23·#81의 규칙이 넓어지는 자리)."""
    chain = chain_for(music_block(), sfx_volume=0, music=MUSIC)

    graph = " ".join(chain.steps)
    assert "amix=inputs=2:normalize=0:duration=longest" in graph
    assert f"alimiter=limit={audio_mix.LIMITER_CEILING}" in graph


def test_a_bad_music_value_says_which_key_is_wrong() -> None:
    """스키마를 지나지 않은 `project.json`이 직접 들어온다 (`_motion_strength`와 같은 이유)."""
    for field, value, key in (
        ("volume", -0.1, "audio.music_volume"),
        ("duck", 1.5, "audio.music_duck"),
        ("fade_sec", 0.0, "audio.music_duck_fade_sec"),
    ):
        with pytest.raises(AudioMixError, match=re.escape(key)):
            chain_for(music_block(), music=replace(MUSIC, **{field: value}))


def test_an_unknown_music_format_lists_the_accepted_ones() -> None:
    """확장자만으로 답한다 — 앱이 파일을 고르는 순간에 거부할 수 있어야 한다."""
    audio_mix.check_music_format("C:/music/bed.MP3")  # 대소문자를 가리지 않는다

    with pytest.raises(AudioMixError, match=r"\.mp3"):
        audio_mix.check_music_format("bed.ogg")


# --- 프로젝트 → 렌더 경로 (#35) ----------------------------------------------


def test_a_project_without_music_makes_the_same_command_as_before(tmp_path: Path) -> None:
    """음악을 지정하지 않은 실행의 명령이 이 기능이 생기기 전과 같다 (#35 완료 조건).

    비교 대상은 **음악 키가 아예 없는 프로젝트**다 — 그것이 #34까지의 `project.json` 모양이고,
    `music: null` + 값 셋이 그 명령과 한 글자도 다르지 않아야 한다.
    """
    scenes = quiz_block()
    aligned = align(scenes)

    def command(project: dict[str, Any]) -> list[str]:
        audio = video_renderer.build_audio(
            project, scenes, timeline=aligned, run_dir=tmp_path
        )
        return video_renderer.build_command(
            project, run_dir=tmp_path, total_sec=aligned.total_sec, audio=audio
        )

    before = project_with()
    del before["audio"]["music"]
    after = project_with(
        music=None,
        music_volume=DEFAULT_MUSIC_VOLUME,
        music_duck=DEFAULT_MUSIC_DUCK,
        music_duck_fade_sec=DEFAULT_MUSIC_DUCK_FADE_SEC,
    )

    assert command(after) == command(before)


def test_the_music_values_reach_the_chain_from_the_project(tmp_path: Path) -> None:
    """`project.json` → 렌더러 경로. config에서 다시 읽으면 앱의 편집이 무시된다 (PRD 7.10)."""
    scenes = music_block()
    (tmp_path / MUSIC_NAME).touch()
    project = project_with(
        music=MUSIC_NAME, music_volume=0.5, music_duck=0.2, music_duck_fade_sec=0.5
    )

    chain = video_renderer.build_audio(
        project, scenes, timeline=align(scenes), run_dir=tmp_path
    )

    graph = " ".join(chain.steps)
    assert "volume=0.5" in graph
    assert "1-0.8*" in graph
    assert f"/{round(0.5 * FPS)}" in graph


def test_a_project_with_only_the_music_path_uses_the_defaults(tmp_path: Path) -> None:
    """**없는 것이 계약 위반이 아니다** — 값 셋이 생기기 전의 run 디렉터리에 경로만 적힌다."""
    scenes = music_block()
    (tmp_path / MUSIC_NAME).touch()
    project = project_with(music=MUSIC_NAME)
    assert "music_volume" not in project["audio"]

    chain = video_renderer.build_audio(
        project, scenes, timeline=align(scenes), run_dir=tmp_path
    )

    assert chain == chain_for(scenes, music=replace(MUSIC, path=tmp_path / MUSIC_NAME))


def test_an_unsupported_music_format_stops_before_encoding(tmp_path: Path) -> None:
    """오버레이의 폰트 검증·효과음 이름 검증과 같은 자리다 (#35 완료 조건)."""
    with pytest.raises(RenderError, match="받지 않는 형식"):
        render(project_with(music="bed.ogg"), music_block(), run_dir=tmp_path)

    assert not (tmp_path / OUTPUT_NAME).exists()


def test_a_missing_music_file_says_the_path(tmp_path: Path) -> None:
    """형식을 먼저 보고 경로를 나중에 푼다 — 없는 파일은 경로와 함께 말한다."""
    with pytest.raises(RenderError, match=re.escape(str(tmp_path / MUSIC_NAME))):
        render(project_with(music=MUSIC_NAME), music_block(), run_dir=tmp_path)


def test_the_music_path_is_logged_with_the_licence_note(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """run.log에 경로와 라이선스 확인 책임이 남는다 (PRD 8장을 지키는 방식)."""
    scenes = music_block()
    (tmp_path / MUSIC_NAME).touch()

    with caplog.at_level(logging.INFO):
        video_renderer.build_audio(
            project_with(music=MUSIC_NAME),
            scenes,
            timeline=align(scenes),
            run_dir=tmp_path,
        )

    assert MUSIC_NAME in caplog.text
    assert "라이선스" in caplog.text


# --- 실제 렌더 (FFmpeg 필요) -------------------------------------------------


@needs_ffmpeg
def test_the_music_drops_while_the_narration_plays(tmp_path: Path) -> None:
    """확정 `scenes.json`에서 계산한 두 구간의 실측 값으로 확인한다 (#35 완료 조건).

    낭독 트랙을 얹지 않는다 — 최종 오디오에 음악만 남아야 감쇠량이 측정에서 드러난다.
    """
    scenes = music_block()
    total = align(scenes).total_sec
    music_file(tmp_path, total_sec=total)

    output = render(
        project_with(music=MUSIC_NAME, music_volume=1.0), scenes, run_dir=tmp_path
    )

    fade = DEFAULT_MUSIC_DUCK_FADE_SEC
    quiet = levels(
        output, span=(NARRATION_START + fade, NARRATION_START + NARRATION_SEC - fade)
    )
    loud = levels(output, span=(0.5, NARRATION_START - 2 * fade))
    wanted = -20 * math.log10(DEFAULT_MUSIC_DUCK)
    assert loud["RMS"] - quiet["RMS"] == pytest.approx(wanted, abs=0.5)
    assert loud["Peak"] - quiet["Peak"] == pytest.approx(wanted, abs=0.5)


@needs_ffmpeg
def test_the_duck_ramps_instead_of_stepping(tmp_path: Path) -> None:
    """전환 구간의 샘플 간 최대 변화량으로 확인한다 (#35 완료 조건).

    기준선은 **같은 소재를 감쇠 없이(`music_duck: 1`) 렌더한 것**이다 — 같은 인코더를 지나야
    AAC의 몫이 양쪽에서 같다.
    """
    scenes = music_block()
    total = align(scenes).total_sec
    for name in ("ducked", "plain"):
        (tmp_path / name).mkdir()
        music_file(tmp_path / name, total_sec=total)
    ducked = render(
        project_with(music=MUSIC_NAME, music_volume=1.0),
        scenes,
        run_dir=tmp_path / "ducked",
    )
    plain = render(
        project_with(music=MUSIC_NAME, music_volume=1.0, music_duck=1.0),
        scenes,
        run_dir=tmp_path / "plain",
    )

    steady = max_sample_step(plain, (1.0, 1.8))
    onset = max_sample_step(ducked, (NARRATION_START - 0.4, NARRATION_START + 0.4))
    release = max_sample_step(
        ducked,
        (NARRATION_START + NARRATION_SEC - 0.4, NARRATION_START + NARRATION_SEC + 0.4),
    )
    assert onset <= steady * RAMP_STEP_LIMIT
    assert release <= steady * RAMP_STEP_LIMIT


@needs_ffmpeg
def test_loud_music_does_not_clip(tmp_path: Path) -> None:
    """음악이 붙어 트랙이 늘어도 최종 peak가 리미터 상한을 넘지 않는다 (#35 완료 조건)."""
    scenes = music_block()
    total = align(scenes).total_sec
    loud_voice_track(tmp_path, total_sec=total)
    track = music_file(tmp_path, total_sec=total, gain_db=LOUD_GAIN_DB)
    # 시험 조건을 확인한다. 두 트랙이 조용하면 합이 넘지 않아 통과가 의미를 잃는다.
    assert levels(track)["Peak"] >= LOUD_FLOOR_DBFS

    output = render(
        project_with(
            voice="voice.mp3", music=MUSIC_NAME, music_volume=1.0, music_duck=1.0
        ),
        scenes,
        run_dir=tmp_path,
    )

    ceiling = 20 * math.log10(audio_mix.LIMITER_CEILING)
    assert levels(output)["Peak"] <= ceiling + AAC_OVERSHOOT_DB


@needs_ffmpeg
def test_music_shorter_than_the_video_is_not_looped(tmp_path: Path) -> None:
    """이음새가 들리는 것보다 조용해지는 쪽이 낫다 (#35의 범위 밖 결정) — 뒤가 무음이다."""
    scenes = music_block()
    total = align(scenes).total_sec
    music_file(tmp_path, total_sec=2.0)

    output = render(
        project_with(music=MUSIC_NAME, music_volume=1.0), scenes, run_dir=tmp_path
    )

    head = levels(output, span=(0.5, 1.5))["RMS"]
    tail = levels(output, span=(total - 1.5, total - 0.5))["RMS"]
    assert head - tail > 40
