"""효과음 믹싱 — 이슈 #23의 완료 조건.

**두 층으로 갈라져 있다** (`test_video_renderer.py`와 같은 이유). 트리거 시각과 필터 문자열은
FFmpeg 없이 확인하고, 레벨·클리핑·실제 배치 시각은 진짜 FFmpeg로 렌더해 측정한다. 후자는
FFmpeg가 없는 환경에서 건너뛴다.

**소리가 놓이는 프레임을 오버레이에서 다시 계산하지 않는다.** 기대값을
`overlay.countdown_windows`와 `overlay.ANSWER_ONSET_SEC`에서 가져오는 것이 이 파일의 요점이다 —
숫자 하나를 손으로 적으면 그림과 소리가 갈리는 회귀를 잡지 못한다.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import audio_mix, overlay, video_renderer
from shorts_maker.assets import sfx_path
from shorts_maker.audio_mix import AudioMixError, build, cues, voice_only
from shorts_maker.schemas.project import DEFAULT_VOICE_VOLUME
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
    scenes: dict[str, Any], *, sfx_volume: float = 1.0, voice_volume: float = 1.0
) -> audio_mix.AudioChain:
    aligned = align(scenes)
    return build(
        scenes,
        aligned.frame_spans,
        fps=aligned.fps,
        sfx_volume=sfx_volume,
        voice_volume=voice_volume,
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
    """`project.build`가 만드는 초기 상태와 같은 모양."""
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


def levels(path: Path) -> dict[str, float]:
    """`astats`의 전체 peak·RMS. 측정 명령은 `assets/sfx/CREDITS.md`와 같은 계열이다."""
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path), "-af",
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
        video_renderer.build_audio(project, quiz_block(), timeline=align(quiz_block()))


def test_a_project_without_the_voice_gain_renders_at_the_original_level() -> None:
    """**없는 것이 계약 위반이 아니다** (#81) — 이 필드가 생기기 전의 run 디렉터리가 열린다.

    `sfx_volume`과 갈리는 이유는 계약의 성격이 아니라 도입 시점뿐이고, 그때의 뜻은
    `DEFAULT_VOICE_VOLUME`(원본 레벨)이다.
    """
    project = project_with()
    assert "voice_volume" not in project["audio"]
    scenes = quiz_block()

    chain = video_renderer.build_audio(project, scenes, timeline=align(scenes))

    assert chain == chain_for(scenes, voice_volume=DEFAULT_VOICE_VOLUME)


def test_the_voice_gain_reaches_the_chain_from_the_project() -> None:
    """`project.json` → 렌더러 경로. config에서 다시 읽으면 앱의 편집이 무시된다 (PRD 7.10)."""
    scenes = quiz_block()

    chain = video_renderer.build_audio(
        project_with(voice_volume=0.25), scenes, timeline=align(scenes)
    )

    assert "volume=0.25" in chain.steps[0]


def test_a_negative_voice_gain_in_the_project_is_a_render_error() -> None:
    scenes = quiz_block()

    with pytest.raises(RenderError, match="audio.voice_volume"):
        video_renderer.build_audio(
            project_with(voice_volume=-1.0), scenes, timeline=align(scenes)
        )
