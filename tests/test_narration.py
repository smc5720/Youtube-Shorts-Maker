"""낭독 세그먼트 합성 — 이슈 #15의 완료 조건.

**실제로 합성하지 않는다.** 두 경계를 가짜로 바꿔 끼운다 — provider(`TTSProvider`)와 길이
측정(`DurationProbe`). `test_tts.py`가 같은 자리를 바꾸는 것과 같은 방식이며, 덕분에
네트워크도 `ffprobe`도 없이 재실행 규칙까지 확인할 수 있다.

여기서 만드는 장면 목록은 **타입 어휘를 담지 않는다.** 이 모듈이 보는 것은 `narrate`
플래그 하나이고, 어떤 콘텐츠에서 나온 장면인지는 모른다 (PRD 7.4.1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shorts_maker.narration import MANIFEST_NAME, synthesize_segments
from shorts_maker.schemas.scenes import (
    SCHEMA_VERSION,
    segment_path,
    validate_scenes,
    validate_scenes_final,
)
from shorts_maker.schemas import SchemaError
from shorts_maker.shorts_types import DEFAULT_TYPE
from shorts_maker.tts import SpeechCache, SpeechSynthesizer, TTSError, WordTiming

PROBED_SEC = 7.5
"""가짜 측정값. provider는 길이를 보고하지 않으므로 값이 들어오는 경로는 측정 경계뿐이다."""

FIRST = "세계에서 가장 긴 강은?"
SECOND = "나일강"


class StubTTS:
    """`TTSProvider` 대역. 합성한 문장을 순서대로 기록한다."""

    name = "stub_tts"
    supports_word_timings = True

    def __init__(self, voice: str = "stub-voice") -> None:
        self.voice = voice
        self.calls: list[str] = []
        self.error: BaseException | None = None

    def synthesize(self, text: str, destination: Path) -> tuple[WordTiming, ...] | None:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        destination.write_bytes(f"stub-audio:{text}".encode("utf-8"))
        return None

    @property
    def call_count(self) -> int:
        return len(self.calls)


class StubProbe:
    """`DurationProbe` 대역. 잰 파일을 기록한다."""

    def __init__(self, duration_sec: float = PROBED_SEC) -> None:
        self.duration_sec = duration_sec
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> float:
        self.paths.append(path)
        return self.duration_sec


def synthesizer(
    provider: StubTTS | None = None,
    probe: StubProbe | None = None,
    cache: SpeechCache | None = None,
) -> tuple[SpeechSynthesizer, StubTTS, StubProbe]:
    stub = provider or StubTTS()
    measure = probe or StubProbe()
    return SpeechSynthesizer(provider=stub, cache=cache, measure=measure), stub, measure


def draft(*narrated: str) -> dict[str, Any]:
    """낭독 장면 사이에 낭독 없는 장면이 끼는 초안. 세그먼트 번호가 연속하지 않는다."""
    scenes: list[dict[str, Any]] = [{"role": "hook", "text": "후킹", "duration": 2.5}]
    for text in narrated:
        scenes.append(
            {"role": "question", "text": text, "narrate": True, "target_duration": 3.0}
        )
        scenes.append({"role": "countdown", "seconds": 3, "duration": 3.0})
    scenes.append({"role": "cta", "text": "마무리", "duration": 3.0})
    return {"schema_version": SCHEMA_VERSION, "type": DEFAULT_TYPE, "scenes": scenes}


def narrated_indices(scenes: dict[str, Any]) -> list[int]:
    return [index for index, scene in enumerate(scenes["scenes"]) if scene.get("narrate")]


def segment_files(run_dir: Path) -> list[str]:
    return sorted(path.name for path in (run_dir / "audio").glob("seg-*.mp3"))


# --- 세그먼트와 장면의 대응 --------------------------------------------------


def test_one_segment_per_narrated_scene(tmp_path: Path) -> None:
    target, stub, _ = synthesizer()

    result = synthesize_segments(draft(FIRST, SECOND), run_dir=tmp_path, synthesizer=target)

    assert stub.calls == [FIRST, SECOND]
    assert len(segment_files(tmp_path)) == len(narrated_indices(result)) == 2


def test_segment_number_is_the_scene_index(tmp_path: Path) -> None:
    """낭독이 아닌 장면을 건너뛰므로 번호가 연속하지 않는다 (PRD 7.5.2)."""
    target, _, _ = synthesizer()

    result = synthesize_segments(draft(FIRST, SECOND), run_dir=tmp_path, synthesizer=target)

    assert narrated_indices(result) == [1, 3]
    assert segment_files(tmp_path) == ["seg-001.mp3", "seg-003.mp3"]
    for index in (1, 3):
        assert result["scenes"][index]["audio"] == segment_path(index)


def test_scenes_without_narration_get_no_audio_fields(tmp_path: Path) -> None:
    """낭독 오디오가 없는 장면에 오디오 필드가 붙으면 확정 검증이 반려한다."""
    target, _, _ = synthesizer()

    result = synthesize_segments(draft(FIRST), run_dir=tmp_path, synthesizer=target)

    for scene in result["scenes"]:
        if not scene.get("narrate"):
            assert not {"audio", "audio_duration", "narration_offset"} & set(scene)


def test_stale_audio_fields_are_dropped_when_narration_is_turned_off(
    tmp_path: Path,
) -> None:
    """사람이 `narrate`를 뺀 장면에 이전 실행의 오디오 필드가 남아 있는 경우."""
    scenes = draft(FIRST)
    scenes["scenes"][1] = {
        "role": "question",
        "text": FIRST,
        "target_duration": 3.0,
        "audio": segment_path(1),
        "audio_duration": 4.0,
    }
    target, stub, _ = synthesizer()

    result = synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)

    assert stub.call_count == 0
    assert "audio" not in result["scenes"][1]


def test_measured_duration_comes_from_the_probe(tmp_path: Path) -> None:
    """provider 보고값을 쓰는 경로가 없다 — 계약에 길이 칸 자체가 없다 (#14)."""
    target, _, probe = synthesizer()

    result = synthesize_segments(draft(FIRST), run_dir=tmp_path, synthesizer=target)

    assert result["scenes"][1]["audio_duration"] == PROBED_SEC
    assert probe.paths == [tmp_path / segment_path(1)]


def test_measured_duration_is_recorded_in_milliseconds(tmp_path: Path) -> None:
    """#16은 실측값이 아니라 여기 기록된 값을 읽어 `duration`을 확정한다."""
    target, _, _ = synthesizer(probe=StubProbe(3.14159))

    result = synthesize_segments(draft(FIRST), run_dir=tmp_path, synthesizer=target)

    assert result["scenes"][1]["audio_duration"] == 3.142


def test_empty_audio_is_reported_as_a_synthesis_failure(tmp_path: Path) -> None:
    """스키마 위반("0보다 커야 한다")으로 둔갑하면 원인이 가려진다."""
    target, _, _ = synthesizer(probe=StubProbe(0.0))

    with pytest.raises(TTSError, match="비어 있다"):
        synthesize_segments(draft(FIRST), run_dir=tmp_path, synthesizer=target)


# --- 상태: 초안도 확정도 아닌 중간 -------------------------------------------


def test_the_result_passes_validation_but_is_not_finalized(tmp_path: Path) -> None:
    """`duration`과 `narration_offset`은 #16의 몫이다."""
    target, _, _ = synthesizer()

    result = synthesize_segments(draft(FIRST), run_dir=tmp_path, synthesizer=target)

    validate_scenes(result)
    with pytest.raises(SchemaError):
        validate_scenes_final(result)


def test_the_input_is_not_modified(tmp_path: Path) -> None:
    """호출자가 초안을 계속 들고 있을 수 있다 (앱 백엔드, 재실행)."""
    scenes = draft(FIRST)
    target, _, _ = synthesizer()

    synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)

    assert "audio" not in scenes["scenes"][1]


def test_a_broken_draft_stops_before_any_synthesis(tmp_path: Path) -> None:
    """만들다 만 오디오를 run 디렉터리에 남기지 않는다."""
    scenes = draft(FIRST)
    del scenes["scenes"][1]["target_duration"]
    target, stub, _ = synthesizer()

    with pytest.raises(SchemaError):
        synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)

    assert stub.call_count == 0
    assert not (tmp_path / "audio").exists()


def test_no_narrated_scene_creates_no_audio_directory(tmp_path: Path) -> None:
    """세그먼트는 낭독 장면이 있을 때만 생기는 산출물이다 (PRD 6.2 표)."""
    target, stub, _ = synthesizer()

    result = synthesize_segments(draft(), run_dir=tmp_path, synthesizer=target)

    assert stub.call_count == 0
    assert not (tmp_path / "audio").exists()
    assert result == draft()


# --- 재실행 -----------------------------------------------------------------


def test_second_run_in_the_same_dir_synthesizes_nothing(tmp_path: Path) -> None:
    target, stub, _ = synthesizer()
    scenes = draft(FIRST, SECOND)

    first = synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)
    second = synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)

    assert stub.calls == [FIRST, SECOND]  # 두 번째 실행이 아무것도 더하지 않았다
    assert second == first


def test_reuse_measures_the_file_again(tmp_path: Path) -> None:
    """세그먼트는 사람이 개별 교체할 수 있다 (PRD 7.5.2). 디스크에 있는 것이 진실이다."""
    probe = StubProbe()
    target, stub, _ = synthesizer(probe=probe)
    scenes = draft(FIRST)
    synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)
    probe.duration_sec = 12.0  # 사람이 다른 오디오로 바꿔 놓은 상황

    result = synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)

    assert stub.call_count == 1
    assert result["scenes"][1]["audio_duration"] == 12.0


def test_changed_text_resynthesizes_only_that_scene(tmp_path: Path) -> None:
    target, stub, _ = synthesizer()
    synthesize_segments(draft(FIRST, SECOND), run_dir=tmp_path, synthesizer=target)

    edited = draft(FIRST, "고쳐 쓴 문장")
    synthesize_segments(edited, run_dir=tmp_path, synthesizer=target)

    assert stub.calls == [FIRST, SECOND, "고쳐 쓴 문장"]
    assert (tmp_path / segment_path(3)).read_bytes() == "stub-audio:고쳐 쓴 문장".encode()


def test_a_missing_segment_file_is_synthesized_again(tmp_path: Path) -> None:
    """기록이 남아 있어도 파일이 없으면 재사용할 것이 없다."""
    target, stub, _ = synthesizer()
    scenes = draft(FIRST)
    synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)
    (tmp_path / segment_path(1)).unlink()

    synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)

    assert stub.calls == [FIRST, FIRST]


def test_a_changed_voice_resynthesizes_everything(tmp_path: Path) -> None:
    """텍스트만 비교하면 이전 목소리의 오디오를 그대로 쓰게 된다."""
    scenes = draft(FIRST)
    first, _, _ = synthesizer(StubTTS(voice="ko-KR-SunHiNeural"))
    synthesize_segments(scenes, run_dir=tmp_path, synthesizer=first)

    changed = StubTTS(voice="ko-KR-InJoonNeural")
    second, _, _ = synthesizer(changed)
    synthesize_segments(scenes, run_dir=tmp_path, synthesizer=second)

    assert changed.calls == [FIRST]


def test_a_corrupt_record_falls_back_to_synthesis(tmp_path: Path) -> None:
    """기록 손상이 실행을 멈추지 않는다 — 안전한 선택은 다시 합성하는 것이다."""
    target, stub, _ = synthesizer()
    scenes = draft(FIRST)
    synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)
    (tmp_path / "audio" / MANIFEST_NAME).write_text("{깨진 JSON", encoding="utf-8")

    result = synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)

    assert stub.calls == [FIRST, FIRST]
    assert result["scenes"][1]["audio_duration"] == PROBED_SEC


def test_the_record_survives_a_failure_partway_through(tmp_path: Path) -> None:
    """중간에 실패해도 그때까지 만든 세그먼트는 다음 실행이 재사용한다."""
    stub = StubTTS()
    target, _, _ = synthesizer(stub)
    scenes = draft(FIRST, SECOND)

    def fail_on_second(text: str, destination: Path) -> None:
        stub.calls.append(text)
        if len(stub.calls) > 1:
            raise TTSError("엔드포인트가 응답하지 않는다", retryable=False)
        destination.write_bytes(b"stub-audio")
        return None

    stub.synthesize = fail_on_second  # type: ignore[method-assign]
    with pytest.raises(TTSError):
        synthesize_segments(scenes, run_dir=tmp_path, synthesizer=target)

    recovered = StubTTS()
    retry, _, _ = synthesizer(recovered)
    synthesize_segments(scenes, run_dir=tmp_path, synthesizer=retry)

    assert recovered.calls == [SECOND]  # 첫 세그먼트는 다시 합성하지 않았다


def test_the_record_names_the_file_and_the_text(tmp_path: Path) -> None:
    """사람이 열어 봤을 때 어느 문장이 어느 파일이 됐는지 보여야 한다."""
    target, _, _ = synthesizer()

    synthesize_segments(draft(FIRST), run_dir=tmp_path, synthesizer=target)

    record = json.loads((tmp_path / "audio" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert record["segments"] == [
        {"index": 1, "audio": segment_path(1), "text": FIRST}
    ]
    assert record["voice"] == "stub-voice"


def test_the_cache_serves_a_new_run_directory(tmp_path: Path) -> None:
    """run 디렉터리가 바뀌면 재사용할 파일이 없지만 캐시는 남는다 (PRD 7.5.2)."""
    cache = SpeechCache(directory=tmp_path / "cache")
    target, stub, _ = synthesizer(cache=cache)
    scenes = draft(FIRST)

    synthesize_segments(scenes, run_dir=tmp_path / "run-1", synthesizer=target)
    result = synthesize_segments(scenes, run_dir=tmp_path / "run-2", synthesizer=target)

    assert stub.calls == [FIRST]  # 합성 없이 캐시에서 복사됐다
    assert (tmp_path / "run-2" / segment_path(1)).is_file()
    assert result["scenes"][1]["audio_duration"] == PROBED_SEC
