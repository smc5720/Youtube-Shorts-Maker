"""확정 `scenes.json` → `captions.srt` — 이슈 #17의 완료 조건.

**두 층에서 확인한다.** 대부분의 테스트는 `build()`가 만든 큐 목록을 직접 본다 — 큐 구간과
줄바꿈이 이 이슈의 내용이고, 텍스트로 옮긴 뒤에는 정규식으로 되짚어야 한다. 그 큐가 실제
SRT 파일로 옮겨져 외부 파서가 여는지는 아래 `--- 표준 형식`과 `--- 실제 FFmpeg` 절이 본다.

여기서 만드는 장면 목록은 **타입 어휘를 담지 않는다.** 이 모듈이 보는 것은 `text`·`caption`
필드와 `narrate` 플래그뿐이다 (PRD 7.4.1).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from shorts_maker.captions import (
    CAPTIONS_NAME,
    CaptionError,
    Cue,
    build,
    render,
    timecode,
    wrap,
)
from shorts_maker.config import Config, defaults, load_config
from shorts_maker.schemas import SchemaError
from shorts_maker.schemas.scenes import SCHEMA_VERSION, segment_path
from shorts_maker.shorts_types import DEFAULT_TYPE

LEAD_IN = 0.3
"""`config.example.yaml`의 `timing.lead_in_sec`. 낭독 장면의 큐 시작이 여기서 나온다."""

MAX_CHARS = 18
MAX_LINES = 4
"""`captions.*` 기본값. 아래 기대값은 이 값에서 손으로 계산한 것이다."""


def config(**overrides: Any) -> Config:
    return load_config(overrides=overrides, search_from=Path("없는-디렉터리"))


def scene(
    role: str,
    duration: float,
    *,
    text: str | None = None,
    caption: str | None = None,
    narration_offset: float | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """확정 상태의 장면 하나. `narration_offset`을 주면 낭독 장면이 된다."""
    built: dict[str, Any] = {"role": role, "duration": duration}
    if text is not None:
        built["text"] = text
    if caption is not None:
        built["caption"] = caption
    if role == "countdown":
        built["seconds"] = int(duration)
    if narration_offset is not None:
        built.update(
            narrate=True,
            target_duration=duration,
            audio=segment_path(index if index is not None else 0),
            # 큐 끝이 장면 끝이므로 낭독이 장면 안에 들어가기만 하면 된다.
            audio_duration=round(duration - LEAD_IN - 0.5, 3),
            narration_offset=narration_offset,
        )
    return built


def finalized(*scenes: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "type": DEFAULT_TYPE, "scenes": list(scenes)}


def sample() -> dict[str, Any]:
    """hook → 질문 → 카운트다운 → 정답(해설 포함) → cta.

    낭독 장면 사이에 낭독 없는 장면이 끼어 큐 번호와 장면 인덱스가 어긋난다.
    """
    return finalized(
        scene("hook", 2.5, text="상식 퀴즈"),
        scene("question", 3.3, text="질문", narration_offset=2.5 + LEAD_IN, index=1),
        scene("countdown", 3.0),
        scene(
            "answer",
            4.0,
            text="정답",
            caption="해설",
            narration_offset=8.8 + LEAD_IN,
            index=3,
        ),
        scene("cta", 3.0, text="구독"),
    )


def spans(cues: list[Cue]) -> list[tuple[float, float]]:
    return [(cue.start_sec, cue.end_sec) for cue in cues]


# --- 큐 구간 -----------------------------------------------------------------


def test_a_narrated_cue_opens_at_the_narration_offset() -> None:
    """장면 시작이 아니다 — `lead_in`은 말이 시작되기 전의 여백이다 (PRD 7.5.1)."""
    cues = build(sample(), config=config())

    assert cues[1].start_sec == pytest.approx(2.5 + LEAD_IN)


def test_a_scene_without_narration_is_covered_end_to_end() -> None:
    """PRD 7.6 — 낭독이 없는 장면의 자막은 구간 전체를 덮는다."""
    cues = build(sample(), config=config())

    assert spans(cues)[0] == pytest.approx((0.0, 2.5))  # hook
    assert spans(cues)[-1] == pytest.approx((12.8, 15.8))  # cta


def test_every_cue_ends_at_its_scene_boundary() -> None:
    cues = build(sample(), config=config())

    assert [end for _, end in spans(cues)] == pytest.approx([2.5, 5.8, 12.8, 15.8])


def test_cues_do_not_overlap_and_start_in_order() -> None:
    cues = build(sample(), config=config())

    starts = [cue.start_sec for cue in cues]
    assert starts == sorted(starts)
    for earlier, later in zip(cues, cues[1:]):
        assert earlier.end_sec <= later.start_sec


def test_every_cue_stays_inside_its_own_scene() -> None:
    """장면 경계를 넘으면 화면에 없는 문구가 자막에만 남는다."""
    scenes = sample()
    cues = build(scenes, config=config())

    bounds = []
    start = 0.0
    for item in scenes["scenes"]:
        bounds.append((start, start + item["duration"]))
        start += item["duration"]
    # 문구가 없는 countdown은 큐를 만들지 않으므로 인덱스가 어긋난다. 각 큐가 **어떤**
    # 장면 안에 들어가는지로 확인한다.
    for cue in cues:
        assert any(
            low <= cue.start_sec and cue.end_sec <= high for low, high in bounds
        ), cue


def test_the_last_cue_does_not_run_past_the_finalized_total() -> None:
    scenes = sample()

    cues = build(scenes, config=config())

    total = sum(item["duration"] for item in scenes["scenes"])
    assert cues[-1].end_sec == pytest.approx(total)


def test_a_scene_without_any_text_gets_no_cue() -> None:
    """카운트다운에는 문구가 없다. 빈 큐를 만들면 화면에 빈 자막이 뜬다."""
    cues = build(sample(), config=config())

    assert len(cues) == 4  # 장면 5개 중 countdown 하나가 빠진다
    assert all(cue.lines for cue in cues)


def test_the_cue_start_branches_on_the_flag_not_the_role() -> None:
    """`role`로 분기하면 같은 필드를 쓰는 다른 타입이 같은 보장을 못 받는다 (PRD 7.4.1)."""
    scenes = finalized(
        scene("hook", 3.0, text="후킹", narration_offset=LEAD_IN, index=0)
    )

    cues = build(scenes, config=config())

    assert cues[0].start_sec == pytest.approx(LEAD_IN)


# --- 큐 내용 -----------------------------------------------------------------


def test_a_caption_scene_carries_both_the_text_and_the_caption() -> None:
    """SRT는 단일 트랙이라 두 큐를 겹칠 수 없다 — 한 큐에 정답 줄과 해설 줄을 담는다."""
    cues = build(sample(), config=config())

    answer = cues[2]
    assert answer.lines == ("정답", "해설")


def test_the_text_and_the_caption_are_wrapped_separately() -> None:
    """이어 붙이면 정답 끝과 해설 앞이 한 줄에 섞인다 — 화면에서는 따로 뜬다."""
    scenes = finalized(
        scene(
            "answer",
            8.0,
            text="짧은정답",
            caption="해설 문장이 길어서 두 줄로 접힌다",
            narration_offset=LEAD_IN,
            index=0,
        )
    )

    cues = build(scenes, config=config())

    assert cues[0].lines[0] == "짧은정답"
    assert len(cues[0].lines) == 3


def test_a_scene_with_only_a_caption_still_gets_a_cue() -> None:
    """해설은 낭독이 없어 자막이 유일한 전달 경로다 (D1 발주서 1장)."""
    scenes = finalized(scene("answer", 3.0, caption="해설만 있다"))

    cues = build(scenes, config=config())

    assert cues[0].lines == ("해설만 있다",)


# --- 줄바꿈 -------------------------------------------------------------------


def test_no_line_exceeds_the_configured_width() -> None:
    scenes = finalized(
        scene("hook", 6.0, text="지구 표면에서 바다가 차지하는 비율은 얼마나 될까")
    )

    cues = build(scenes, config=config())

    assert cues[0].lines
    assert all(len(line) <= MAX_CHARS for line in cues[0].lines)


def test_wrapping_breaks_between_words() -> None:
    assert wrap("가나다 라마바 사아자 차카타", 7) == ["가나다 라마바", "사아자 차카타"]


def test_a_word_longer_than_the_limit_is_split_inside_itself() -> None:
    """넘치게 두면 재생기가 제멋대로 자른다. 자를 곳이 없는 경우만 어절 안에서 자른다."""
    assert wrap("가" * 25, 10) == ["가" * 10, "가" * 10, "가" * 5]


def test_a_long_word_flushes_the_line_it_could_not_join() -> None:
    lines = wrap("짧다 " + "가" * 12, 8)

    assert lines == ["짧다", "가" * 8, "가" * 4]


def test_wrapping_reads_the_configured_width() -> None:
    """기본값에 의존하지 않는다 — config로 바꾼 값이 실제로 줄바꿈에 들어간다."""
    scenes = finalized(scene("hook", 6.0, text="가나다 라마바 사아자"))

    cues = build(scenes, config=config(**{"captions.max_chars_per_line": 3}))

    assert cues[0].lines == ("가나다", "라마바", "사아자")


def test_a_width_below_one_is_rejected_with_the_key_name() -> None:
    """1 미만이면 어절을 자를 조각이 없어 줄바꿈이 끝나지 않는다."""
    with pytest.raises(CaptionError, match="captions.max_chars_per_line"):
        build(sample(), config=config(**{"captions.max_chars_per_line": 0}))


def test_no_content_is_lost_to_wrapping() -> None:
    text = "지구 표면에서 바다가 차지하는 비율은 약 71퍼센트다"
    scenes = finalized(scene("hook", 6.0, text=text))

    cues = build(scenes, config=config())

    assert "".join(cues[0].lines).replace(" ", "") == text.replace(" ", "")


# --- 줄 수 상한 --------------------------------------------------------------


def test_too_many_lines_warn_without_truncating(caplog: pytest.LogCaptureFixture) -> None:
    """원문을 잃는 쪽이 더 나쁘다 (#17). 경고만 하고 내용은 그대로 둔다."""
    caplog.set_level("WARNING")
    text = " ".join(["가나다라마바사아자차카타파"] * 6)  # 13자 어절 6개 → 6줄
    scenes = finalized(scene("hook", 6.0, text=text))

    cues = build(scenes, config=config())

    assert len(cues[0].lines) == 6
    assert "".join(cues[0].lines) == text.replace(" ", "")
    warning = "\n".join(record.getMessage() for record in caplog.records)
    assert "captions.max_lines" in warning
    assert "자막 1번" in warning


def test_a_cue_within_the_line_limit_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")

    build(sample(), config=config())

    assert caplog.records == []


def test_the_line_limit_is_read_from_the_config(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("WARNING")

    build(sample(), config=config(**{"captions.max_lines": 1}))

    assert "상한 1줄" in "\n".join(record.getMessage() for record in caplog.records)


def test_a_line_limit_below_one_is_rejected_with_the_key_name() -> None:
    with pytest.raises(CaptionError, match="captions.max_lines"):
        build(sample(), config=config(**{"captions.max_lines": 0}))


# --- 확정 상태 요구 ----------------------------------------------------------


def test_a_draft_is_rejected_so_no_path_computes_from_targets() -> None:
    """초안의 목표치로 타임코드를 계산하는 경로가 있으면 안 된다 (퀴즈 스펙 4장)."""
    scenes = sample()
    del scenes["scenes"][1]["duration"]

    with pytest.raises(SchemaError, match="duration"):
        build(scenes, config=config())


def test_a_narrated_scene_without_an_offset_is_rejected() -> None:
    scenes = sample()
    del scenes["scenes"][1]["narration_offset"]

    with pytest.raises(SchemaError):
        build(scenes, config=config())


# --- 표준 형식 ---------------------------------------------------------------

BLOCK = re.compile(
    r"^(\d+)\n"
    r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n"
    r"(.+)$",
    re.DOTALL,
)


def blocks(srt: str) -> list[re.Match[str]]:
    parsed = [BLOCK.match(chunk) for chunk in srt.split("\n\n") if chunk]
    assert all(parsed), srt
    return [match for match in parsed if match]


def test_cue_numbers_start_at_one_and_run_without_gaps() -> None:
    srt = render(build(sample(), config=config()))

    assert [match.group(1) for match in blocks(srt)] == ["1", "2", "3", "4"]


def test_timecodes_use_the_srt_comma_format() -> None:
    assert timecode(0.0) == "00:00:00,000"
    assert timecode(2.8) == "00:00:02,800"
    assert timecode(61.5) == "00:01:01,500"
    assert timecode(3723.456) == "01:02:03,456"


def test_timecodes_round_to_milliseconds() -> None:
    """`duration`도 밀리초 자리로 기록된다 (`schemas.scenes.DURATION_DIGITS`)."""
    assert timecode(1.23456) == "00:00:01,235"


def test_every_block_carries_its_lines() -> None:
    srt = render(build(sample(), config=config()))

    assert [match.group(4) for match in blocks(srt)] == [
        "상식 퀴즈",
        "질문",
        "정답\n해설",
        "구독",
    ]


def test_the_file_ends_with_a_blank_line_after_the_last_cue() -> None:
    """블록 구분자가 빈 줄이므로 파서가 파일 끝을 특수하게 다루지 않아도 된다."""
    srt = render(build(sample(), config=config()))

    assert srt.endswith("구독\n\n")


def test_an_empty_cue_list_renders_an_empty_file() -> None:
    """`captions.srt`는 항상 생성된다 (PRD 6.2 표) — 문구가 없는 것과 파일이 없는 것은 다르다."""
    scenes = finalized(scene("countdown", 3.0))

    assert render(build(scenes, config=config())) == ""


# --- 기본값 ------------------------------------------------------------------


def test_the_caption_keys_have_the_documented_defaults() -> None:
    assert defaults()["captions"] == {
        "max_chars_per_line": MAX_CHARS,
        "max_lines": MAX_LINES,
    }


# --- 실제 FFmpeg ------------------------------------------------------------

ffprobe_required = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="FFmpeg가 PATH에 없다"
)


@ffprobe_required
def test_an_external_parser_reads_every_cue(tmp_path: Path) -> None:
    """외부 재생기/파서에서 열려야 한다 (#17 완료 조건). ffprobe가 자막 디먹서다."""
    path = tmp_path / CAPTIONS_NAME
    path.write_text(render(build(sample(), config=config())), encoding="utf-8")

    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "packet=pts_time", "-of", "csv", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )

    times = [
        float(line.split(",")[1])
        for line in completed.stdout.splitlines()
        if line.startswith("packet,")
    ]
    assert times == pytest.approx([0.0, 2.8, 9.1, 12.8])


def test_scene_boundaries_do_not_drift_over_many_scenes() -> None:
    """#16이 `narration_offset`을 반올림 누계로 계산한다 — 여기서 잔여가 쌓이면 어긋난다."""
    scenes = finalized(
        *[scene("hook", 0.1 * step, text=f"{step}번") for step in range(1, 40)]
    )

    cues = build(scenes, config=config())

    running = 0.0
    for step, cue in enumerate(cues, start=1):
        running = round(running + 0.1 * step, 3)
        assert cue.end_sec == running
