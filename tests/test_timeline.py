"""타임라인 확정과 `voice.mp3` — 이슈 #16의 완료 조건.

**합성 트랙은 두 층에서 확인한다.** 대부분의 테스트는 `mix` 경계를 기록용 대역으로 바꿔
끼워 "어느 세그먼트가 어느 오프셋에 놓이라고 했는가"만 본다. 그 지시가 실제 파일로
옮겨지는지는 아래 `--- 실제 FFmpeg` 절이 진짜 오디오로 확인하며, FFmpeg가 없는 환경에서는
건너뛴다.

여기서 만드는 장면 목록은 **타입 어휘를 담지 않는다.** 이 모듈이 보는 것은 `narrate`
플래그와 `caption` 필드의 존재뿐이다 (PRD 7.4.1, 7.5.1).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from shorts_maker.config import Config, defaults, load_config
from shorts_maker.schemas import SchemaError
from shorts_maker.schemas.scenes import (
    SCHEMA_VERSION,
    segment_path,
    validate_scenes_final,
)
from shorts_maker.shorts_types import DEFAULT_TYPE
from shorts_maker.timeline import (
    TOTAL_RANGE_SEC,
    VOICE_TRACK,
    Placement,
    TimelineError,
    finalize,
    mix_voice_track,
)

LEAD_IN = 0.3
TAIL = 0.5
MIN_DURATION = 1.2
CAPTION_ONSET = 0.9
READING_CPS = 12.0
"""`config.example.yaml`의 기본값. 아래 기대값은 전부 이 값에서 손으로 계산한 것이다."""

HOOK_SEC = 2.5
COUNTDOWN_SEC = 3
CTA_SEC = 3.0
"""비낭독 장면의 초안 확정값 (D1 확정 스펙 3장). 보정 대상이 아니므로 그대로 남아야 한다."""


def config(**overrides: Any) -> Config:
    return load_config(overrides=overrides, search_from=Path("없는-디렉터리"))


class Recorder:
    """`VoiceMixer` 대역. 어떤 배치를 지시받았는지 기록한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[Placement], Path, float]] = []

    def __call__(
        self, placements: Any, destination: Path, total_sec: float
    ) -> None:
        self.calls.append((list(placements), destination, total_sec))

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def offsets(self) -> list[float]:
        return [placement.offset_sec for placement in self.calls[-1][0]]


@dataclass
class Narrated:
    """낭독 장면 하나를 만드는 재료. `caption`이 있으면 읽기 하한이 걸린다."""

    audio_duration: float = 2.5
    target_duration: float = 3.0
    caption: str | None = None


def synthesized(*narrated: Narrated) -> dict[str, Any]:
    """#15를 지난 상태의 장면 목록 — 오디오 필드는 있고 `duration`은 아직 없다.

    낭독 장면 사이에 낭독 없는 장면이 끼어 세그먼트 번호가 연속하지 않는다.
    """
    scenes: list[dict[str, Any]] = [
        {"role": "hook", "text": "후킹", "duration": HOOK_SEC}
    ]
    for item in narrated:
        index = len(scenes)
        scene: dict[str, Any] = {
            "role": "answer",
            "text": "정답",
            "narrate": True,
            "target_duration": item.target_duration,
            "audio": segment_path(index),
            "audio_duration": item.audio_duration,
        }
        if item.caption is not None:
            scene["caption"] = item.caption
        scenes.append(scene)
        scenes.append(
            {"role": "countdown", "seconds": COUNTDOWN_SEC, "duration": float(COUNTDOWN_SEC)}
        )
    scenes.append({"role": "cta", "text": "마무리", "duration": CTA_SEC})
    return {"schema_version": SCHEMA_VERSION, "type": DEFAULT_TYPE, "scenes": scenes}


def durations(scenes: dict[str, Any]) -> list[float]:
    return [scene["duration"] for scene in scenes["scenes"]]


# --- 낭독 장면의 길이 --------------------------------------------------------


def test_narrated_duration_is_the_measured_length_plus_padding(tmp_path: Path) -> None:
    result = finalize(
        synthesized(Narrated(audio_duration=2.5)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    assert result["scenes"][1]["duration"] == pytest.approx(LEAD_IN + 2.5 + TAIL)


def test_narrated_duration_is_longer_than_the_audio(tmp_path: Path) -> None:
    """낭독보다 짧으면 음성이 잘린다 — 확정 검증도 이것을 본다."""
    result = finalize(
        synthesized(Narrated(audio_duration=1.8), Narrated(audio_duration=5.88)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    for scene in result["scenes"]:
        if scene.get("narrate"):
            assert scene["duration"] > scene["audio_duration"]


def test_a_very_short_narration_gets_the_minimum(tmp_path: Path) -> None:
    """`min_duration`이 걸리는 경로. 기본값에서는 한국어 낭독으로 닿지 않는다 (#16 배경)."""
    result = finalize(
        synthesized(Narrated(audio_duration=0.1)),
        run_dir=tmp_path,
        config=config(**{"timing.min_duration_sec": 2.0}),
        mix=Recorder(),
    )

    assert result["scenes"][1]["duration"] == pytest.approx(2.0)


def test_duration_is_recorded_in_milliseconds(tmp_path: Path) -> None:
    """프레임 경계 정렬은 렌더러가 한다. 여기서는 밀리초 자리까지만 남긴다 (PRD 7.5.1)."""
    result = finalize(
        synthesized(Narrated(audio_duration=2.3456)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    assert result["scenes"][1]["duration"] == 3.146


def test_target_duration_survives_finalization(tmp_path: Path) -> None:
    """목표치와 확정값을 나중에도 비교할 수 있어야 한다 (앱 편집 UI, 사후 검수)."""
    result = finalize(
        synthesized(Narrated(target_duration=3.0)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    assert result["scenes"][1]["target_duration"] == 3.0
    assert result["scenes"][1]["duration"] != 3.0


# --- 자막 읽기 하한 ----------------------------------------------------------


def test_a_long_caption_stretches_a_short_narration(tmp_path: Path) -> None:
    """60자 해설 + 짧은 낭독 — 낭독만으로는 읽을 시간이 나오지 않는 경우 (PRD 7.5.1)."""
    caption = "가" * 60

    result = finalize(
        synthesized(Narrated(audio_duration=1.8, caption=caption)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    expected = CAPTION_ONSET + 60 / READING_CPS + TAIL  # 6.4초
    assert result["scenes"][1]["duration"] == pytest.approx(expected)
    assert expected > LEAD_IN + 1.8 + TAIL  # 하한이 실제로 이긴 경우다


def test_the_reading_floor_starts_at_the_caption_onset_not_the_lead_in(
    tmp_path: Path,
) -> None:
    """`lead_in`으로 계산하면 실제 표시 시간이 0.6초 모자란다 (D1 확정 스펙 4장)."""
    caption = "가" * 60
    scenes = synthesized(Narrated(audio_duration=1.8, caption=caption))

    result = finalize(scenes, run_dir=tmp_path, config=config(), mix=Recorder())

    duration = result["scenes"][1]["duration"]
    assert duration == pytest.approx(CAPTION_ONSET + 60 / READING_CPS + TAIL)
    assert duration > LEAD_IN + 60 / READING_CPS + TAIL


def test_a_short_caption_does_not_shorten_the_scene(tmp_path: Path) -> None:
    """하한은 하한이다. 자막이 짧다고 낭독 기준 길이를 깎지 않는다."""
    result = finalize(
        synthesized(Narrated(audio_duration=2.5, caption="짧다")),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    assert result["scenes"][1]["duration"] == pytest.approx(LEAD_IN + 2.5 + TAIL)


def test_the_floor_branches_on_the_caption_field_not_the_role(tmp_path: Path) -> None:
    """`role`로 분기하면 같은 필드를 쓰는 다른 타입이 같은 보장을 못 받는다 (PRD 7.4.1)."""
    caption = "가" * 60
    scenes = synthesized(Narrated(audio_duration=1.8, caption=caption))
    scenes["scenes"][1]["role"] = "question"  # `answer`가 아니어도 하한이 걸린다

    result = finalize(scenes, run_dir=tmp_path, config=config(), mix=Recorder())

    assert result["scenes"][1]["duration"] == pytest.approx(
        CAPTION_ONSET + 60 / READING_CPS + TAIL
    )


def test_a_zero_reading_speed_is_rejected_with_the_key_name(tmp_path: Path) -> None:
    """0이면 하한이 무한이 된다. 설정 로더는 범위를 모른다 (config.py)."""
    with pytest.raises(TimelineError, match="timing.reading_cps"):
        finalize(
            synthesized(Narrated(caption="해설")),
            run_dir=tmp_path,
            config=config(**{"timing.reading_cps": 0.0}),
            mix=Recorder(),
        )


# --- 비낭독 장면은 보정하지 않는다 -------------------------------------------


def test_fixed_length_scenes_keep_the_draft_values(tmp_path: Path) -> None:
    """실측할 오디오가 없으므로 보정 대상이 아니다 (PRD 7.5.1)."""
    result = finalize(
        synthesized(Narrated()), run_dir=tmp_path, config=config(), mix=Recorder()
    )

    scenes = result["scenes"]
    assert scenes[0]["duration"] == HOOK_SEC  # hook
    assert scenes[3]["duration"] == CTA_SEC  # cta


def test_countdown_duration_stays_equal_to_seconds(tmp_path: Path) -> None:
    """숫자 전환이 정수 초에 맞아야 한다. 확정 검증도 이 일치를 강제한다."""
    result = finalize(
        synthesized(Narrated()), run_dir=tmp_path, config=config(), mix=Recorder()
    )

    countdown = result["scenes"][2]
    assert countdown["duration"] == countdown["seconds"] == COUNTDOWN_SEC


def test_fixed_length_scenes_get_no_offset(tmp_path: Path) -> None:
    result = finalize(
        synthesized(Narrated()), run_dir=tmp_path, config=config(), mix=Recorder()
    )

    for scene in result["scenes"]:
        if not scene.get("narrate"):
            assert "narration_offset" not in scene


def test_a_fixed_length_scene_without_a_duration_names_the_scene(tmp_path: Path) -> None:
    """채워 줄 주체가 없다 — 장면 템플릿이 넣어야 하는 값이다."""
    scenes = synthesized(Narrated())
    del scenes["scenes"][0]["duration"]

    with pytest.raises(TimelineError, match=r"scenes\[0\]"):
        finalize(scenes, run_dir=tmp_path, config=config(), mix=Recorder())


# --- 낭독 오프셋 -------------------------------------------------------------


def test_offset_is_the_accumulated_duration_plus_lead_in(tmp_path: Path) -> None:
    result = finalize(
        synthesized(Narrated(), Narrated()),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    scenes = result["scenes"]
    running = 0.0
    for index, scene in enumerate(scenes):
        if scene.get("narrate"):
            assert scene["narration_offset"] == pytest.approx(running + LEAD_IN), index
        running += scene["duration"]


def test_the_narration_fits_inside_its_own_scene(tmp_path: Path) -> None:
    """인접 장면 구간으로 넘치면 다음 장면에 남의 목소리가 깔린다."""
    result = finalize(
        synthesized(Narrated(audio_duration=5.88), Narrated(audio_duration=1.8)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    scenes = result["scenes"]
    start = 0.0
    for scene in scenes:
        if scene.get("narrate"):
            end = scene["narration_offset"] + scene["audio_duration"]
            assert scene["narration_offset"] >= start
            assert end <= start + scene["duration"]
        start += scene["duration"]


def test_offsets_do_not_drift_over_many_scenes(tmp_path: Path) -> None:
    """반올림 잔여가 쌓이면 기록된 오프셋이 앞선 duration의 합과 어긋난다."""
    result = finalize(
        synthesized(*[Narrated(audio_duration=0.1 * step) for step in range(1, 13)]),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    running = 0.0
    for scene in result["scenes"]:
        if scene.get("narrate"):
            assert scene["narration_offset"] == round(running + LEAD_IN, 3)
        running = round(running + scene["duration"], 3)


# --- 확정 상태 ---------------------------------------------------------------


def test_the_result_passes_the_final_validation(tmp_path: Path) -> None:
    result = finalize(
        synthesized(Narrated(), Narrated()),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    validate_scenes_final(result)


def test_the_input_is_not_modified(tmp_path: Path) -> None:
    """호출자가 확정 전 상태를 계속 들고 있을 수 있다 (앱 백엔드, 재실행)."""
    scenes = synthesized(Narrated())

    finalize(scenes, run_dir=tmp_path, config=config(), mix=Recorder())

    assert "duration" not in scenes["scenes"][1]
    assert "narration_offset" not in scenes["scenes"][1]


def test_a_broken_input_stops_before_writing_anything(tmp_path: Path) -> None:
    scenes = synthesized(Narrated())
    del scenes["scenes"][1]["target_duration"]
    recorder = Recorder()

    with pytest.raises(SchemaError):
        finalize(scenes, run_dir=tmp_path, config=config(), mix=recorder)

    assert recorder.call_count == 0


def test_a_narrated_scene_without_audio_names_the_missing_field(tmp_path: Path) -> None:
    """세그먼트 합성을 거치지 않은 장면 목록이다 — 길이를 정할 근거가 없다."""
    scenes = synthesized(Narrated())
    del scenes["scenes"][1]["audio_duration"]
    recorder = Recorder()

    with pytest.raises(TimelineError, match="audio_duration"):
        finalize(scenes, run_dir=tmp_path, config=config(), mix=recorder)

    assert recorder.call_count == 0


# --- 합성 트랙 지시 ----------------------------------------------------------


def test_every_segment_is_placed_at_its_recorded_offset(tmp_path: Path) -> None:
    recorder = Recorder()

    result = finalize(
        synthesized(Narrated(), Narrated()),
        run_dir=tmp_path,
        config=config(),
        mix=recorder,
    )

    placements, destination, total_sec = recorder.calls[0]
    narrated = [scene for scene in result["scenes"] if scene.get("narrate")]
    assert destination == tmp_path / VOICE_TRACK
    assert total_sec == pytest.approx(sum(durations(result)))
    assert [placement.audio for placement in placements] == [
        tmp_path / scene["audio"] for scene in narrated
    ]
    assert recorder.offsets == [scene["narration_offset"] for scene in narrated]


def test_no_narrated_scene_creates_no_voice_track(tmp_path: Path) -> None:
    """세그먼트가 1개 이상일 때의 산출물이다. 없는 것이 실패가 아니다 (PRD 6.2 표)."""
    recorder = Recorder()

    result = finalize(synthesized(), run_dir=tmp_path, config=config(), mix=recorder)

    assert recorder.call_count == 0
    assert not (tmp_path / VOICE_TRACK).exists()
    validate_scenes_final(result)


# --- 경고 -------------------------------------------------------------------


def test_a_scene_far_over_its_target_warns_without_failing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """60자 해설이 읽기 하한에 걸려 6.4초가 되는 경우 — 목표 3.0초의 2배를 넘는다."""
    caplog.set_level("WARNING")

    result = finalize(
        synthesized(Narrated(audio_duration=1.8, caption="가" * 60)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    assert result["scenes"][1]["duration"] == pytest.approx(6.4)
    warning = "\n".join(record.getMessage() for record in caplog.records)
    assert "scenes[1]" in warning
    assert "목표 3.00초" in warning


def test_a_scene_far_under_its_target_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """합성이 잘렸을 때의 신호다. 확정값이 목표의 절반 미만인 경우."""
    caplog.set_level("WARNING")

    finalize(
        synthesized(Narrated(audio_duration=0.2, target_duration=3.0)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    warning = "\n".join(record.getMessage() for record in caplog.records)
    assert "scenes[1]" in warning
    assert "잘렸는지" in warning


def test_a_scene_within_the_target_band_says_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("WARNING")

    finalize(
        synthesized(Narrated(audio_duration=2.5, target_duration=3.0)),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    # 총 길이 경고는 따로 걸린다 (장면 하나짜리 입력이다). 장면 경고만 걸러 본다.
    assert [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("scenes[")
    ] == []


def test_a_total_outside_the_range_warns_and_still_builds_the_track(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """자동으로 줄이지 않는다 — 사람이 검수할 대상이 소리 없이 바뀐다 (PRD 7.5.1)."""
    caplog.set_level("WARNING")
    recorder = Recorder()

    result = finalize(
        synthesized(Narrated()), run_dir=tmp_path, config=config(), mix=recorder
    )

    total = sum(durations(result))
    assert total < TOTAL_RANGE_SEC[0]
    warning = "\n".join(record.getMessage() for record in caplog.records)
    assert "목표 범위 45~60초" in warning
    assert recorder.call_count == 1  # 렌더를 막지 않는다


def test_a_total_inside_the_range_says_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("WARNING")

    # hook 2.5 + (질문 3.3 + 카운트다운 3) × 7 + cta 3.0 = 49.6초
    result = finalize(
        synthesized(*[Narrated() for _ in range(7)]),
        run_dir=tmp_path,
        config=config(),
        mix=Recorder(),
    )

    total = sum(durations(result))
    assert TOTAL_RANGE_SEC[0] <= total <= TOTAL_RANGE_SEC[1]
    assert "목표 범위" not in "\n".join(record.getMessage() for record in caplog.records)


# --- 설정 -------------------------------------------------------------------


def test_the_formula_reads_every_timing_key(tmp_path: Path) -> None:
    """기본값에 의존하지 않는다 — config로 바꾼 값이 실제로 공식에 들어간다."""
    result = finalize(
        synthesized(Narrated(audio_duration=1.0, caption="가" * 24)),
        run_dir=tmp_path,
        config=config(
            **{
                "timing.lead_in_sec": 1.0,
                "timing.tail_sec": 2.0,
                "timing.min_duration_sec": 0.5,
                "timing.caption_onset_sec": 3.0,
                "timing.reading_cps": 6.0,
            }
        ),
        mix=Recorder(),
    )

    scene = result["scenes"][1]
    assert scene["duration"] == pytest.approx(3.0 + 24 / 6.0 + 2.0)  # 자막 하한이 이긴다
    assert scene["narration_offset"] == pytest.approx(HOOK_SEC + 1.0)


def test_the_new_timing_keys_have_the_documented_defaults() -> None:
    """PRD 7.5.1 표와 D1 확정 스펙 4장의 기준값."""
    timing = defaults()["timing"]

    assert timing["caption_onset_sec"] == CAPTION_ONSET
    assert timing["reading_cps"] == READING_CPS


# --- 실제 FFmpeg ------------------------------------------------------------

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg가 PATH에 없다",
)

FRAME_SEC = 1 / 30
"""30fps 한 프레임. 합성 트랙 길이의 허용 오차다 (PRD 6.3의 fps)."""

SILENCE_FLOOR_DB = -50
"""이보다 조용한 구간을 무음으로 본다. 세그먼트는 사인파이므로 여유가 크다."""


def tone(path: Path, duration_sec: float) -> None:
    """세그먼트 대역. edge_tts 출력과 같은 24kHz 모노 mp3로 만든다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"sine=f=440:r=24000:d={duration_sec}",
            "-ac", "1", str(path),
        ],
        check=True,
    )


def probed_duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(completed.stdout.strip())


def sound_spans(path: Path) -> list[tuple[float, float]]:
    """소리가 있는 구간 목록. `silencedetect`가 보고한 무음의 여집합이다."""
    completed = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", str(path),
            "-af", f"silencedetect=noise={SILENCE_FLOOR_DB}dB:d=0.05",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    marks = re.findall(r"silence_(start|end): (\d+(?:\.\d+)?)", completed.stderr)

    spans: list[tuple[float, float]] = []
    opened: float | None = None
    for kind, value in marks:
        if kind == "end":  # 무음이 끝났다 = 소리가 시작됐다
            opened = float(value)
        elif opened is not None:
            spans.append((opened, float(value)))
            opened = None
    return spans


@ffmpeg_required
def test_the_voice_track_length_matches_the_finalized_total(tmp_path: Path) -> None:
    """렌더러가 영상 길이의 기준으로 삼는 값이다 — 어긋나면 뒤쪽이 잘리거나 남는다."""
    placements = [
        Placement(audio=tmp_path / "seg-a.mp3", offset_sec=0.3),
        Placement(audio=tmp_path / "seg-b.mp3", offset_sec=5.3),
    ]
    tone(placements[0].audio, 2.5)
    tone(placements[1].audio, 3.0)
    destination = tmp_path / VOICE_TRACK

    mix_voice_track(placements, destination, 10.0)

    assert probed_duration(destination) == pytest.approx(10.0, abs=FRAME_SEC)


@ffmpeg_required
def test_every_segment_lands_on_its_offset_and_stays_there(tmp_path: Path) -> None:
    """무음이 아닌 구간이 정확히 세그먼트 자리에만 있어야 한다 (PRD 7.5.2)."""
    placements = [
        Placement(audio=tmp_path / "seg-a.mp3", offset_sec=0.3),
        Placement(audio=tmp_path / "seg-b.mp3", offset_sec=5.3),
    ]
    tone(placements[0].audio, 2.5)
    tone(placements[1].audio, 3.0)
    destination = tmp_path / VOICE_TRACK

    mix_voice_track(placements, destination, 10.0)

    spans = sound_spans(destination)
    assert len(spans) == 2
    assert spans[0] == pytest.approx((0.3, 2.8), abs=FRAME_SEC)
    assert spans[1] == pytest.approx((5.3, 8.3), abs=FRAME_SEC)


@ffmpeg_required
def test_the_finalized_timeline_produces_a_matching_track(tmp_path: Path) -> None:
    """확정과 합성을 한 번에 — 오프셋 기록과 파일 안의 실제 자리가 같아야 한다."""
    scenes = synthesized(Narrated(audio_duration=2.5), Narrated(audio_duration=1.8))
    for scene in scenes["scenes"]:
        if scene.get("narrate"):
            tone(tmp_path / scene["audio"], scene["audio_duration"])

    result = finalize(scenes, run_dir=tmp_path, config=config())

    total = sum(durations(result))
    narrated = [scene for scene in result["scenes"] if scene.get("narrate")]
    assert probed_duration(tmp_path / VOICE_TRACK) == pytest.approx(total, abs=FRAME_SEC)
    assert [start for start, _ in sound_spans(tmp_path / VOICE_TRACK)] == pytest.approx(
        [scene["narration_offset"] for scene in narrated], abs=FRAME_SEC
    )


def test_a_missing_ffmpeg_is_reported_with_the_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr("shorts_maker.timeline.subprocess.run", missing)

    with pytest.raises(TimelineError, match="ffmpeg를 찾을 수 없다"):
        mix_voice_track(
            [Placement(audio=tmp_path / "seg.mp3", offset_sec=0.0)],
            tmp_path / VOICE_TRACK,
            1.0,
        )


def test_a_failed_ffmpeg_run_reports_the_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "Invalid argument")

    monkeypatch.setattr("shorts_maker.timeline.subprocess.run", failing)

    with pytest.raises(TimelineError, match="Invalid argument"):
        mix_voice_track(
            [Placement(audio=tmp_path / "seg.mp3", offset_sec=0.0)],
            tmp_path / VOICE_TRACK,
            1.0,
        )
