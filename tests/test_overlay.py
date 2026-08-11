"""자막·텍스트 오버레이 번인 — 이슈 #20의 완료 조건.

**두 층으로 갈라져 있다** (`test_video_renderer.py`와 같은 이유). 필터 문자열을 만드는 층은
FFmpeg 없이 돌고, 좌표·이스케이프·프레임 경계가 실제로 그렇게 나오는지는 진짜 렌더의 픽셀에서
확인한다. 후자는 FFmpeg가 없는 환경에서 건너뛴다.

수치의 출처는 전부 `docs/design/d1-video-design-spec.md` 5장이다. 이 파일은 그 표를 다시 적지
않고, 표가 요구하는 **성질**(안전 영역 안, 중심 x=500, 프리셋 교체가 레이아웃을 안 바꿈)을
확인한다 — 숫자를 두 곳에 적으면 스펙을 고칠 때 테스트가 함께 틀린다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import overlay, video_renderer
from shorts_maker.assets import caption_styles
from shorts_maker.overlay import CENTER_X, OverlayError, build, resolve_fonts
from shorts_maker.video_renderer import CANVAS_HEIGHT, CANVAS_WIDTH, align

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg가 없다 — 필터 생성 테스트만 돈다"
)

SAFE_TOP, SAFE_BOTTOM = 260, 1500
SAFE_LEFT, SAFE_RIGHT = 80, 920
"""확정 스펙 1장의 안전 영역. 상 260 / 하 420(=1920-1500) / 좌 80 / 우 160(=1080-920)."""

PUNCH = "구독 · 좋아요"
TAIL = "매일 새 상식 퀴즈"


def scene(role: str, duration: float, **fields: Any) -> dict[str, Any]:
    return {"role": role, "duration": duration, **fields}


def quiz_scenes(
    question: str = "지구 표면의 약 71%를 덮고 있는 것은?",
    answer: str = "바다",
    hook: str = "이 4문제 다 맞히면 상식 천재",
    explanation: str = "태평양만으로도 지구 바다 면적의 약 46%를 차지한다",
) -> dict[str, Any]:
    """hook · (question · countdown · answer) · cta 한 벌. 장면 템플릿(#12)이 만드는 모양."""
    return {
        "schema_version": 1,
        "type": "quiz",
        "scenes": [
            scene("hook", 2.5, kicker="상식 퀴즈", text=hook),
            scene("question", 4.3, question_id=1, heading=question, text=question),
            scene("countdown", 3.0, question_id=1, heading=question, seconds=3),
            scene("answer", 3.4, question_id=1, heading=question, text=answer,
                  caption=explanation),
            scene("cta", 3.0, text="다음 문제도 풀어보세요"),
        ],
    }


def filters(scenes: dict[str, Any], *, style: str = "impact_yellow", **kwargs: Any) -> list[str]:
    timeline = align(scenes)
    return build(
        scenes,
        timeline.frame_spans,
        fps=timeline.fps,
        style=caption_styles()[style],
        fonts=resolve_fonts(None),
        cta_punch=kwargs.pop("cta_punch", PUNCH),
        cta_tail=kwargs.pop("cta_tail", TAIL),
        **kwargs,
    )


def option(filter_string: str, name: str) -> str:
    """필터 문자열에서 옵션 하나를 꺼낸다. 값이 인용돼 있으면 인용을 벗긴다."""
    # 첫 옵션은 `drawtext=` 뒤에 붙으므로 구분자가 `:`가 아니라 `=`다.
    match = re.search(rf"(?:^|[:=]){name}=('[^']*'|[^:]*)", filter_string)
    assert match is not None, f"{name}이 없다: {filter_string}"
    return match.group(1).strip("'")


def drawtexts(filter_list: list[str]) -> list[str]:
    """`drawtext`만. 카운트다운 바는 `drawbox`라 `text`·`fontsize`가 없다 (#21)."""
    return [item for item in filter_list if item.startswith("drawtext=")]


def drawboxes(filter_list: list[str]) -> list[str]:
    return [item for item in filter_list if item.startswith("drawbox=")]


def texts(filter_list: list[str]) -> list[str]:
    return [option(item, "text") for item in drawtexts(filter_list)]


# --- 문구의 출처 (완료 조건: 렌더러에 고정 문자열이 없다) ---------------------


def test_the_kicker_and_heading_come_from_the_scene_fields() -> None:
    """**렌더러가 `상식 퀴즈`를 알지 못한다.** 타입이 통과 필드에 넣은 값을 그대로 그린다
    (#55, 확정 스펙 8장). 여기 고정 문자열이 생기면 두 번째 타입이 화면을 못 바꾼다."""
    scenes = quiz_scenes()
    scenes["scenes"][0]["kicker"] = "과학 퀴즈"
    scenes["scenes"][1]["heading"] = "달의 반지름은?"
    scenes["scenes"][2]["heading"] = "달의 반지름은?"
    scenes["scenes"][3]["heading"] = "달의 반지름은?"

    drawn = texts(filters(scenes))

    assert "과학 퀴즈" in drawn
    assert "달의 반지름은?" in drawn
    assert "상식 퀴즈" not in drawn


def test_the_hook_meta_is_computed_from_the_scene_list() -> None:
    """`문제 N개 · M초` (확정 스펙 5.1). 문제 수는 고유 `question_id` 수, 초는 duration 합이다
    — `quiz.json`을 열지 않고도 나온다 (PRD 7.4.1)."""
    scenes = quiz_scenes()
    # 4.3 + 3.0 + 3.4 = 10.7초짜리 문제 블록을 하나 더 붙인다.
    scenes["scenes"][4:4] = [
        scene("question", 4.3, question_id=2, heading="두 번째?", text="두 번째?"),
        scene("countdown", 3.0, question_id=2, heading="두 번째?", seconds=3),
        scene("answer", 3.4, question_id=2, heading="두 번째?", text="답"),
    ]

    drawn = texts(filters(scenes))

    assert "문제 2개 · 27초" in drawn


def test_the_question_index_counts_the_distinct_questions() -> None:
    """`Q2 / 4`. 총 문제 수도 `scenes.json`에서 나온다 (확정 스펙 5.2)."""
    scenes = quiz_scenes()
    scenes["scenes"][1]["question_id"] = 3
    scenes["scenes"][2]["question_id"] = 3
    scenes["scenes"][3]["question_id"] = 3

    assert "Q3 / 1" in texts(filters(scenes))


def test_the_cta_punch_and_tail_come_from_the_settings() -> None:
    """채널 브랜딩이라 콘텐츠가 아니다 (확정 스펙 5.5)."""
    drawn = texts(filters(quiz_scenes(), cta_punch="구독하기", cta_tail="내일 또 만나요"))

    assert "구독하기" in drawn
    assert "내일 또 만나요" in drawn


@pytest.mark.parametrize(
    ("key", "value"),
    [("cta_punch", "열 글자가 넘는 문구다"), ("cta_tail", "스물한 글자를 넘기는 아주 긴 꼬리 문구입니다")],
)
def test_an_overlong_cta_setting_stops_before_rendering(key: str, value: str) -> None:
    """설정값이라 초과는 사람의 오타다 — 인코딩을 시작하기 전에 멈춘다 (확정 스펙 5.5)."""
    with pytest.raises(OverlayError, match=f"render.{key}"):
        filters(quiz_scenes(), **{key: value})


def test_a_scene_without_text_draws_only_its_own_elements() -> None:
    """countdown에는 `text`가 없다 — 화면에 나오는 것은 숫자와 바뿐이다 (#21)."""
    scenes = {
        "schema_version": 1,
        "type": "quiz",
        "scenes": [scene("countdown", 3.0, seconds=3)],
    }

    drawn = filters(scenes)

    assert texts(drawn) == ["3", "2", "1"]
    # 트랙 1 + 채움 3.
    assert len(drawboxes(drawn)) == 4


# --- 카운트다운 (완료 조건: #21) ---------------------------------------------


def countdown_scenes(seconds: int) -> dict[str, Any]:
    """카운트다운 앞뒤로 장면이 있는 목록. 구간을 벗어나는지 보려면 앞뒤가 필요하다."""
    scenes = quiz_scenes()
    scenes["scenes"][2].update(seconds=seconds, duration=float(seconds))
    return scenes


def digit_windows(filter_list: list[str]) -> dict[str, str]:
    """숫자 → `enable` 식. 240px 요소만 카운트다운 숫자다 (확정 스펙 5.3)."""
    return {
        option(item, "text"): option(item, "enable")
        for item in drawtexts(filter_list)
        if option(item, "fontsize") == "240"
    }


def test_the_digits_count_down_one_second_each() -> None:
    """`countdown_sec` 기본값 3에서 3 → 2 → 1이 각 1초씩 (확정 스펙 5.3)."""
    scenes = countdown_scenes(3)
    start, end = align(scenes).frame_spans[2]

    windows = digit_windows(filters(scenes))

    assert list(windows) == ["3", "2", "1"]
    assert windows["3"] == f"gte(t,{start}/30)*lt(t,{start + 30}/30)"
    assert windows["2"] == f"gte(t,{start + 30}/30)*lt(t,{start + 60}/30)"
    # 마지막 숫자만 장면 끝까지 간다 — 프레임 반올림으로 남는 프레임에 화면이 비지 않는다.
    assert windows["1"] == f"gte(t,{start + 60}/30)*lt(t,{end}/30)"


def test_the_digit_count_follows_the_seconds_field() -> None:
    """`countdown_sec`를 4로 올리면 4부터 센다 — 렌더러에 3이 박혀 있지 않다."""
    assert list(digit_windows(filters(countdown_scenes(4)))) == ["4", "3", "2", "1"]


def test_the_countdown_never_draws_outside_its_scene() -> None:
    """숫자와 바 모두 countdown 장면 구간 안에서만 켜진다."""
    scenes = countdown_scenes(3)
    start, end = align(scenes).frame_spans[2]
    drawn = filters(scenes)

    countdown = drawboxes(drawn) + [
        item for item in drawtexts(drawn) if option(item, "fontsize") == "240"
    ]

    assert countdown
    for item in countdown:
        first, last = re.findall(r"t,(\d+)/30", option(item, "enable"))
        assert start <= int(first) < int(last) <= end


def test_the_progress_bar_empties_one_step_per_second() -> None:
    """연속 감소는 `drawbox`로 만들 수 없다 (확정 스펙 2.3의 실측). 숫자와 같은 박자로
    끊어 남은 시간을 전달한다 — 첫 초가 가득 차고 마지막 초가 1/N이다."""
    drawn = filters(countdown_scenes(3))

    widths = [int(option(item, "w")) for item in drawboxes(drawn)]

    # 트랙(가득 참) + 채움 3칸.
    assert widths == [840, 840, 560, 280]


def test_the_progress_bar_track_is_preset_independent() -> None:
    """트랙은 확정 스펙 5.3의 값이라 3종이 공통이고, 채움만 프리셋 강조색을 따른다."""
    for name, style in caption_styles().items():
        boxes = drawboxes(filters(countdown_scenes(3), style=name))
        accent = style.color("accent").replace("#", "0x")

        assert option(boxes[0], "color") == overlay.BAR_TRACK_COLOR
        assert {option(item, "color") for item in boxes[1:]} == {accent}


def test_the_bar_sits_in_the_text_column() -> None:
    """x80 w840 — 텍스트 컬럼과 같은 폭이다 (확정 스펙 1장, 5.3)."""
    boxes = drawboxes(filters(countdown_scenes(3)))

    assert option(boxes[0], "x") == str(SAFE_LEFT)
    assert int(option(boxes[0], "w")) == SAFE_RIGHT - SAFE_LEFT
    assert int(option(boxes[0], "y")) + int(option(boxes[0], "h")) <= SAFE_BOTTOM


def test_a_countdown_without_seconds_warns_instead_of_crashing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """확정 검증이 요구하는 필드지만(`schemas/scenes.py`) 앱이 만든 프로젝트는 그 경로를
    지나지 않을 수 있다. 화면 하나가 비는 것이 렌더 전체를 잃는 것보다 낫다."""
    scenes = countdown_scenes(3)
    del scenes["scenes"][2]["seconds"]

    drawn = filters(scenes)

    assert not drawboxes(drawn)
    assert "seconds" in caplog.text


# --- 길이 티어 (완료 조건: 22자에서 76→64px, 39자에서 64→56px) ----------------


def size_of(filter_list: list[str], text_start: str) -> int:
    found = next(
        item for item in drawtexts(filter_list)
        if option(item, "text").startswith(text_start)
    )
    return int(option(found, "fontsize"))


def lines_of(filter_list: list[str], size: int, *, weight: str = "") -> list[str]:
    """그 크기로 그려진 줄들. 질문(700)과 hook(800)이 64px을 공유하므로 웨이트로 가른다."""
    return [
        option(item, "text")
        for item in drawtexts(filter_list)
        if option(item, "fontsize") == str(size) and weight in option(item, "fontfile")
    ]


def test_a_short_hook_uses_the_large_tier() -> None:
    """≤22자 → 76px 2줄 (확정 스펙 5.1)."""
    drawn = filters(quiz_scenes(hook="스물두 글자까지는 큰 티어를 그대로 쓴다"))

    assert size_of(drawn, "스물두") == 76
    assert len(lines_of(drawn, 76, weight="ExtraBold")) == 2


def test_a_long_hook_drops_to_the_small_tier() -> None:
    """23~36자 → 64px. 경계 한 글자로 갈린다 (위 테스트의 22자와 짝이다)."""
    drawn = filters(quiz_scenes(hook="스물세 글자부터는 작은 티어로 내려서 쓴다"))

    assert size_of(drawn, "스물세") == 64


def test_the_longest_hook_still_fits_three_lines() -> None:
    """상한 36자가 13자/줄에서 3줄 안에 들어간다 — `quiz.hook_max_len`이 나온 계산이다."""
    drawn = filters(quiz_scenes(hook="서른여섯 글자를 꽉 채운 후킹 문구를 여기에 적어서 세 줄로 쓴다"))

    assert len(lines_of(drawn, 64, weight="ExtraBold")) == 3


def test_a_long_question_drops_a_tier_and_moves_down() -> None:
    """40~45자 → 56px, y664 (확정 스펙 5.2). 위치까지 함께 바뀐다."""
    short = "서른아홉 글자까지는 큰 티어로 그리고 줄 수는 세 줄을 넘지 않게 쓴다"
    long = "마흔 글자가 되면 작은 티어로 내려가고 시작 위치도 아래로 조금씩 옮긴다"
    assert len(short) == 39 and len(long) == 40

    small = filters(quiz_scenes(question=short))
    smaller = filters(quiz_scenes(question=long))

    assert size_of(small, "서른아홉") == 64
    assert size_of(smaller, "마흔") == 56
    first = next(
        item for item in drawtexts(smaller) if option(item, "text").startswith("마흔")
    )
    assert option(first, "y") == "664"


def test_a_one_word_answer_uses_the_biggest_tier() -> None:
    """1~6자 → 132px 1줄 (확정 스펙 5.4)."""
    drawn = filters(quiz_scenes(answer="바다"))

    assert size_of(drawn, "바다") == 132


def test_a_long_answer_drops_to_two_lines() -> None:
    """7~20자 → 84px 최대 2줄 (10자/줄)."""
    drawn = filters(quiz_scenes(answer="아주 긴 정답 문구가 들어온 경우"))

    assert len(lines_of(drawn, 84)) == 2


def test_an_overlong_text_still_draws_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """생성 단계가 막는 상한이지만(#56) 사람이 고친 `scenes.json`은 그 경로를 안 지난다.
    그리지 않는 것보다 넘치더라도 그리는 편이 낫다 — 어긋난 것이 보여야 고친다."""
    drawn = filters(quiz_scenes(explanation="해" * 120))

    assert any(option(item, "fontsize") == "36" for item in drawtexts(drawn))
    assert "상한" in caplog.text


# --- 배치 (완료 조건: 중심 x=500, 상단 고정, 한 인스턴스가 두 장면을 덮는다) ---


def test_every_line_is_centred_on_500_not_540() -> None:
    """우측 160px가 Shorts 사이드 버튼 영역이다 (확정 스펙 1장)."""
    for item in drawtexts(filters(quiz_scenes())):
        assert option(item, "x") == f"({CENTER_X}-text_w/2)"


def test_the_question_keeps_its_first_line_fixed_across_line_counts() -> None:
    """상단 정렬이다 — 줄 수가 변해도 첫 줄이 안 움직인다 (확정 스펙 5.2)."""
    two = filters(quiz_scenes(question="두 줄짜리 질문은 여기까지만 쓴다"))
    three = filters(quiz_scenes(question="세 줄이 되는 질문은 이만큼 더 길게 이어서 쓴다"))

    assert option(two[0 + _index_of(two, "두 줄짜리")], "y") == "648"
    assert option(three[_index_of(three, "세 줄이")], "y") == "648"


def _index_of(filter_list: list[str], start: str) -> int:
    return next(
        index for index, item in enumerate(filter_list)
        if item.startswith("drawtext=") and option(item, "text").startswith(start)
    )


def test_the_answer_block_stays_centred_on_its_box() -> None:
    """정답 박스는 y1000 h224 고정, 중심 (500, 1112) (확정 스펙 5.4). 티어가 갈려도 중심이
    같아야 해설 y1300이 어떤 경우에도 안전하다."""
    one = filters(quiz_scenes(answer="바다"))
    two = filters(quiz_scenes(answer="아주 긴 정답 문구가 들어온 경우"))

    single = int(option(one[_index_of(one, "바다")], "y"))
    assert single + 132 // 2 == 1112

    tops = sorted(
        int(option(item, "y")) for item in drawtexts(two) if option(item, "fontsize") == "84"
    )
    assert (tops[0] + (tops[-1] + 84)) // 2 == 1112


def test_one_instance_covers_the_question_and_countdown() -> None:
    """좌표·크기·색이 같아야 성립하는 전제라 인스턴스를 나누지 않는다 (확정 스펙 5.3).
    나누면 두 장면 사이에서 질문이 한 프레임 깜빡인다."""
    scenes = quiz_scenes()
    drawn = filters(scenes)
    timeline = align(scenes)
    question_start = timeline.frame_spans[1][0]
    countdown_end = timeline.frame_spans[2][1]

    headings = [
        item for item in drawtexts(drawn) if option(item, "text").startswith("지구 표면")
    ]
    body = [item for item in headings if "0xFFFFFF" in item]

    assert body, "본문색 질문이 없다"
    for item in body:
        assert option(item, "enable") == (
            f"gte(t,{question_start}/30)*lt(t,{countdown_end}/30)"
        )


def test_the_answer_scene_replaces_the_question_with_the_dimmed_colour() -> None:
    """정답 장면에서는 질문·index가 감쇠색이다 (확정 스펙 5.4). 색이 갈리므로 인스턴스도
    갈린다 — 같은 좌표에 두 인스턴스가 구간을 나눠 갖는다."""
    scenes = quiz_scenes()
    drawn = filters(scenes)
    dimmed = caption_styles()["impact_yellow"].color("dimmed").replace("#", "0x")
    answer_span = align(scenes).frame_spans[3]

    faded = [item for item in drawn if dimmed in item]

    assert {option(item, "text") for item in faded} >= {"Q1 / 1"}
    for item in faded:
        assert option(item, "enable") == (
            f"gte(t,{answer_span[0]}/30)*lt(t,{answer_span[1]}/30)"
        )


def test_scene_spans_never_overlap() -> None:
    """`between(t,a,b)`는 양끝을 포함해서 경계 프레임에 앞뒤 요소가 겹친다. `gte`/`lt`로
    열어야 경계 프레임이 새 장면 것이 된다."""
    for item in filters(quiz_scenes()):
        assert re.fullmatch(r"gte\(t,\d+/30\)\*lt\(t,\d+/30\)", option(item, "enable"))


# --- 프리셋 (완료 조건: 3종이 색만 바꾼다) -----------------------------------


def layout_only(filter_string: str) -> str:
    """색·외곽선·그림자를 지운 필터. 남는 것이 레이아웃이다."""
    return re.sub(
        r"(?:^|:)(fontcolor|bordercolor|borderw|shadowx|shadowy|shadowcolor|color)=[^:]*",
        "",
        filter_string,
    )


def test_swapping_the_style_changes_colours_only() -> None:
    """좌표·크기·줄 수는 3종 공통이다 (확정 스펙 6.1). 프리셋 교체로 레이아웃이 회귀하면
    #38이 프리셋을 뺀 이유가 사라진다."""
    scenes = quiz_scenes()

    layouts = {
        name: [layout_only(item) for item in filters(scenes, style=name)]
        for name in caption_styles()
    }

    reference = layouts["impact_yellow"]
    for name, drawn in layouts.items():
        assert drawn == reference, f"{name}이 레이아웃을 바꾼다"
    # 색은 실제로 갈린다 — 위 비교가 전부를 지워서 통과한 것이 아님을 보인다.
    assert filters(scenes, style="neon_mint") != filters(scenes, style="impact_yellow")


def test_the_shadowless_preset_draws_no_shadow_and_thickens_the_answer() -> None:
    """P2는 그림자 대신 정답 외곽선을 두껍게 한다 (확정 스펙 6.1)."""
    mint = filters(quiz_scenes(), style="neon_mint")
    yellow = filters(quiz_scenes(), style="impact_yellow")

    assert not any("shadowcolor" in item for item in mint)
    assert any("shadowcolor" in item for item in yellow)
    bonus = caption_styles()["neon_mint"].answer_border_bonus
    assert int(option(mint[_index_of(mint, "바다")], "borderw")) == (
        int(option(yellow[_index_of(yellow, "바다")], "borderw")) + bonus
    )


def test_the_shadow_opacity_follows_the_preset() -> None:
    """요소별 값은 P1 기준이고 프리셋이 그 기준을 옮긴다 (`SHADOW_BASELINE`). 절대값으로
    적으면 P3에서 요소 사이의 관계가 깨진다."""
    yellow = filters(quiz_scenes())
    orange = filters(quiz_scenes(), style="orange_card")

    def alpha(filter_list: list[str], start: str) -> float:
        item = filter_list[_index_of(filter_list, start)]
        return float(option(item, "shadowcolor").split("@")[1])

    assert alpha(yellow, "이 4문제") == pytest.approx(0.5)
    assert alpha(orange, "이 4문제") == pytest.approx(0.55)
    # 질문은 기준의 0.9배(0.45/0.5)라 프리셋을 따라 함께 움직인다.
    assert alpha(orange, "지구 표면") == pytest.approx(0.55 * 0.9, abs=0.001)


def test_an_unknown_style_lists_the_bundled_ones() -> None:
    with pytest.raises(OverlayError, match="impact_yellow"):
        overlay.style_for("없는_스타일")


# --- 이스케이프 (실측: docs/design/d1-video-design-spec.md 7.4) ---------------


def test_percent_signs_survive_because_expansion_is_off() -> None:
    """`%`는 `%{...}` 확장의 도입 문자라 기본 모드에서는 그 요소가 통째로 빈다. 백분율은
    퀴즈 문장에 흔하다."""
    drawn = filters(quiz_scenes(question="지구 표면의 약 71%를 덮는 것은?"))

    percent = [item for item in drawtexts(drawn) if "71%" in option(item, "text")]
    assert percent
    for item in percent:
        assert option(item, "expansion") == "none"


@pytest.mark.parametrize(
    "text",
    ["비율은 3:1이다", "오늘's 퀴즈", "역슬래시\\하나", "하나, 둘, 셋", "[정답] 태평양", "a=b;c"],
)
def test_special_characters_are_escaped_not_dropped(text: str) -> None:
    """필터 문법을 깨지 않으면서 문자가 남아야 한다. 픽셀 대조는 아래 렌더 테스트가 한다."""
    escaped = overlay._escape(text)

    assert ":" not in escaped.replace("\\:", "")
    assert escaped.count("'") % 2 == 0 or "\\'" in escaped


# --- 폰트 (완료 조건: 잘못된 폰트를 렌더 시작 전에 잡는다) --------------------


def test_the_bundled_weights_are_used_when_no_font_is_set() -> None:
    """확정 스펙 9장의 세 웨이트. 정답·hook은 800, 질문은 700, 해설은 500이다."""
    fonts = resolve_fonts(None)

    assert {800, 700, 500} == set(fonts.paths)
    assert len({str(path) for path in fonts.paths.values()}) == 3


def test_a_missing_font_file_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(OverlayError, match="번들 Pretendard"):
        resolve_fonts(str(tmp_path / "없는폰트.otf"))


@needs_ffmpeg
def test_a_font_without_korean_is_rejected_before_rendering(tmp_path: Path) -> None:
    """한글 글리프가 없는 폰트는 `.notdef`(빈 사각형)를 그리므로 잉크 유무로는 못 잡는다 —
    서로 다른 두 글자가 같은 그림인지를 본다 (`KOREAN_PROBE`)."""
    latin = _latin_only_font(tmp_path)
    if latin is None:
        pytest.skip("한글 없는 폰트를 만들 수 없다 (fontTools 없음)")

    with pytest.raises(OverlayError, match="한글"):
        resolve_fonts(str(latin))


def _latin_only_font(tmp_path: Path) -> Path | None:
    """번들 폰트에서 한글을 들어낸 사본. 라틴 전용 폰트를 저장소에 커밋하지 않기 위함이다."""
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError:
        return None

    font = TTFont(overlay.font_path(700))
    subset.Subsetter(options=subset.Options(notdef_outline=True)).subset(font)
    target = tmp_path / "latin-only.otf"
    font.save(target)
    return target


@needs_ffmpeg
def test_a_file_that_is_not_a_font_is_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "fake.otf"
    fake.write_bytes("이것은 폰트가 아니다".encode())

    with pytest.raises(OverlayError, match="폰트 파일을 열 수 없다"):
        resolve_fonts(str(fake))


# --- 실제 렌더 (FFmpeg 필요) -------------------------------------------------


def project_with(**render: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": "quiz",
        "language": "ko",
        "scenes": "scenes.json",
        "background": {"kind": "color", "value": "#101A33"},
        "audio": {"voice": None, "music": None},
        "render": {
            "width": CANVAS_WIDTH, "height": CANVAS_HEIGHT, "fps": 30,
            "output": "final_short.mp4", "caption_style": "impact_yellow",
            "font_path": None, "cta_punch": PUNCH, "cta_tail": TAIL, **render,
        },
    }


def ink_box(frame: bytes) -> tuple[int, int, int, int]:
    """밝은 픽셀의 (x0, y0, x1, y1). 배경(#101A33)은 gray로 약 26이라 넉넉히 가른다."""
    x0, y0, x1, y1 = CANVAS_WIDTH, CANVAS_HEIGHT, -1, -1
    for y in range(CANVAS_HEIGHT):
        row = frame[y * CANVAS_WIDTH : (y + 1) * CANVAS_WIDTH]
        for x, value in enumerate(row):
            if value > 90:
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
    return x0, y0, x1, y1


def gray_frame(video: Path, index: int) -> bytes:
    """프레임 번호로 한 장. `select`가 디코딩 순서와 무관하게 그 프레임을 고른다."""
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"select=eq(n\\,{index})", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    )
    assert len(completed.stdout) == CANVAS_WIDTH * CANVAS_HEIGHT
    return completed.stdout


def ink_count(frame: bytes) -> int:
    return sum(1 for value in frame if value > 90)


@needs_ffmpeg
@pytest.mark.parametrize(
    "index", [0, 1, 2, 3, 4],
    ids=["hook", "question", "countdown", "answer", "cta"],
)
def test_every_scene_keeps_its_text_inside_the_safe_area(
    tmp_path: Path, index: int
) -> None:
    """상 260 / 하 420 / 우 160 / 좌 80 (확정 스펙 1장). 넘치면 Shorts UI에 가린다."""
    scenes = quiz_scenes(
        # 각 요소의 상한을 채운 문구. 티어가 가장 큰 블록을 만드는 경우다.
        hook="서른여섯 글자를 꽉 채운 후킹 문구를 여기에 적어서 세 줄로 쓴다",
        question="마흔다섯 글자를 꽉 채운 질문을 여기에 적으면 가장 작은 티어에서 세 줄로 나뉜다",
        answer="스무 글자를 가득 채워서 쓴 정답이다",
        explanation="예순 글자를 꽉 채운 해설을 적으면 서른여섯 픽셀 티어에서 세 줄이 되고 그 아래는 비어 있는 상태가 된다",
    )
    video = video_renderer.render(project_with(), scenes, run_dir=tmp_path)
    span = align(scenes).frame_spans[index]

    x0, y0, x1, y1 = ink_box(gray_frame(video, (span[0] + span[1]) // 2))

    assert SAFE_LEFT <= x0 and x1 <= SAFE_RIGHT, f"가로 {x0}~{x1}이 컬럼을 벗어난다"
    assert SAFE_TOP <= y0 and y1 <= SAFE_BOTTOM, f"세로 {y0}~{y1}이 안전 영역을 벗어난다"


@needs_ffmpeg
def test_the_drawn_text_is_centred_on_500(tmp_path: Path) -> None:
    """캔버스 중앙 540이면 오른쪽 버튼 영역으로 밀린다. 40px 차이가 실제로 나야 한다."""
    scenes = quiz_scenes(answer="바다")
    video = video_renderer.render(project_with(), scenes, run_dir=tmp_path)
    span = align(scenes).frame_spans[3]

    x0, _, x1, _ = ink_box(gray_frame(video, (span[0] + span[1]) // 2))

    assert (x0 + x1) / 2 == pytest.approx(CENTER_X, abs=4)


@needs_ffmpeg
def test_korean_renders_with_no_missing_glyphs(tmp_path: Path) -> None:
    """번들 폰트로 한국어가 깨짐 없이 나온다 — 빈 사각형이면 잉크 양이 크게 달라진다."""
    scenes = quiz_scenes()
    video = video_renderer.render(project_with(), scenes, run_dir=tmp_path)
    span = align(scenes).frame_spans[0]

    assert ink_count(gray_frame(video, (span[0] + span[1]) // 2)) > 5000


@needs_ffmpeg
def test_a_percent_sign_does_not_erase_the_line(tmp_path: Path) -> None:
    """**일부가 잘리는 것이 아니라 그 요소 전체가 빈다.** `약 71%를`이 통째로 사라지면
    질문 한 줄이 통째로 없어진다 — 잉크 양으로 비교한다."""
    with_percent = quiz_scenes(question="지구 표면의 약 71%를 덮는 것은?")
    without = quiz_scenes(question="지구 표면의 약 71을 덮는 것은?")

    inks = []
    for scenes, name in ((with_percent, "a"), (without, "b")):
        run_dir = tmp_path / name
        run_dir.mkdir()
        video = video_renderer.render(project_with(), scenes, run_dir=run_dir)
        span = align(scenes).frame_spans[1]
        inks.append(ink_count(gray_frame(video, (span[0] + span[1]) // 2)))

    # `%` 하나가 늘어난 만큼만 달라야 한다. 요소가 통째로 빠지면 절반 아래로 떨어진다.
    assert inks[0] == pytest.approx(inks[1], rel=0.15)


@needs_ffmpeg
def test_an_element_turns_on_at_exactly_its_first_frame(tmp_path: Path) -> None:
    """완료 조건의 1프레임 오차다. 경계 시각을 초로 반올림해 적으면 반올림 방향에 따라
    한 프레임 늦게 켜진다 — 그래서 `enable`에 `n/fps`를 그대로 적는다."""
    scenes = {
        "schema_version": 1,
        "type": "quiz",
        "scenes": [
            # 프레임 경계가 정수 초에 떨어지지 않는 길이를 고른다 (1.233초 = 37프레임).
            # 문구가 없는 장면이라 앞쪽 프레임에는 잉크가 하나도 없다.
            scene("hook", 1.233),
            scene("cta", 1.0, text="여기부터 보인다"),
        ],
    }
    video = video_renderer.render(project_with(), scenes, run_dir=tmp_path)
    first_cta_frame = align(scenes).frame_spans[1][0]

    assert ink_count(gray_frame(video, first_cta_frame - 1)) == 0
    assert ink_count(gray_frame(video, first_cta_frame)) > 0


DIGIT_BAND = (1050, 1310)
"""숫자 박스 y1060 h240에 여유를 준 띠. 질문(~y912)도 바(y1330)도 들어오지 않아서, 이 띠의
잉크는 카운트다운 숫자뿐이다."""


def band_mask(frame: bytes, top: int, bottom: int) -> list[bool]:
    """띠 안에서 잉크가 있는 픽셀. 두 프레임을 겹쳐 보면 숫자가 바뀌었는지가 나온다."""
    return [value > 90 for value in frame[top * CANVAS_WIDTH : bottom * CANVAS_WIDTH]]


def band_width(frame: bytes, top: int, bottom: int) -> int:
    """띠 안 잉크의 가로 폭. 글리프를 읽지 않고도 `1`과 `3`을 가른다."""
    columns = [
        index % CANVAS_WIDTH
        for index, inked in enumerate(band_mask(frame, top, bottom)) if inked
    ]
    return max(columns) - min(columns) + 1 if columns else 0


@needs_ffmpeg
def test_each_countdown_digit_holds_for_exactly_one_second(tmp_path: Path) -> None:
    """완료 조건의 프레임 검사다. 숫자 띠의 잉크 패턴이 **1초 동안 그대로**이고 초 경계에서
    바뀌어야 한다 — 개수만 세면 `3`과 `2`처럼 잉크량이 비슷한 글자를 못 가른다."""
    scenes = countdown_scenes(3)
    video = video_renderer.render(project_with(), scenes, run_dir=tmp_path)
    start, end = align(scenes).frame_spans[2]

    def band(index: int) -> list[bool]:
        return band_mask(gray_frame(video, index), *DIGIT_BAND)

    def differing(left: list[bool], right: list[bool]) -> int:
        return sum(1 for a, b in zip(left, right, strict=True) if a != b)

    # 질문 장면 마지막 프레임에는 숫자가 없다.
    assert not any(band(start - 1))
    seconds = [(start + offset * 30, start + offset * 30 + 29) for offset in range(3)]
    for first, last in seconds:
        assert last < end
        # 전환 애니메이션도 펄스도 없다 (확정 스펙 5.3) — 1초 내내 같은 그림이다.
        assert differing(band(first), band(last)) < 200
    for (_, last), (first, _) in zip(seconds, seconds[1:], strict=False):
        assert differing(band(last), band(first)) > 1000, "초 경계에서 숫자가 안 바뀐다"

    # 순서가 3 → 2 → 1이다. `1`은 다른 두 글자보다 확연히 좁다.
    widths = [band_width(gray_frame(video, first + 15), *DIGIT_BAND) for first, _ in seconds]
    assert widths[2] < widths[0] and widths[2] < widths[1]


@needs_ffmpeg
def test_the_question_does_not_move_between_question_and_countdown(
    tmp_path: Path,
) -> None:
    """완료 조건의 프레임 비교다. 인스턴스 하나가 두 장면을 덮으므로(확정 스펙 5.3) 질문과
    index가 **같은 픽셀에** 있어야 한다. 인스턴스를 나누면 여기서 어긋난다."""
    scenes = countdown_scenes(3)
    video = video_renderer.render(project_with(), scenes, run_dir=tmp_path)
    question_span, countdown_span = align(scenes).frame_spans[1:3]

    # 숫자 박스 위쪽 — index(y400)와 질문(y648~)만 있는 영역이다.
    heading = (300, 1000)
    before = gray_frame(video, (question_span[0] + question_span[1]) // 2)
    during = gray_frame(video, (countdown_span[0] + countdown_span[1]) // 2)

    left, right = band_mask(before, *heading), band_mask(during, *heading)

    assert sum(left) > 3000, "질문이 그려지지 않았다"
    # 인코딩이 손실이라 글리프 경계 몇 픽셀은 흔들린다. 한 글자만 밀려도 수천이 어긋난다.
    assert sum(1 for a, b in zip(left, right, strict=True) if a != b) < 200


BAR_ROW = 1337
"""바(y1330 h14)의 가운데 행. 이 행에는 바 말고 아무것도 없다."""


def bar_widths(frame: bytes) -> tuple[int, int]:
    """(채움 폭, 트랙 폭). 채움은 강조색이라 밝고, 트랙은 흰색@0.16이라 배경보다 조금 밝다."""
    row = frame[BAR_ROW * CANVAS_WIDTH : (BAR_ROW + 1) * CANVAS_WIDTH]
    return (
        sum(1 for value in row if value > 150),
        sum(1 for value in row if 45 < value <= 150),
    )


@needs_ffmpeg
def test_the_progress_bar_empties_a_step_at_a_time(tmp_path: Path) -> None:
    """확정 스펙 5.3이 요구한 `840*(1-t/T)` 연속 감소는 `drawbox`로 만들 수 없다 (2.3의
    실측). 남은 초에 비례한 폭이 실제 픽셀에 나오는지 본다 — 트랙은 내내 840이다."""
    scenes = countdown_scenes(3)
    video = video_renderer.render(project_with(), scenes, run_dir=tmp_path)
    start, _ = align(scenes).frame_spans[2]

    measured = [bar_widths(gray_frame(video, start + offset * 30 + 15)) for offset in range(3)]

    for fill, expected in zip(measured, (840, 560, 280), strict=True):
        assert fill[0] == pytest.approx(expected, abs=2)
        assert fill[0] + fill[1] == pytest.approx(840, abs=2)


@needs_ffmpeg
def test_the_background_presets_render_under_the_text(tmp_path: Path) -> None:
    """단색 1 + 수직 2스톱 그라디언트 2 (확정 스펙 6.2). 오버레이가 붙어도 배경이 남는다."""
    scenes = quiz_scenes()
    for name in ("deep_navy", "purple_gradient", "deep_teal_gradient"):
        run_dir = tmp_path / name
        run_dir.mkdir()
        project = project_with()
        project["background"] = {"kind": "preset", "value": name}

        video = video_renderer.render(project, scenes, run_dir=run_dir)

        frame = gray_frame(video, 10)
        # 텍스트가 없는 아래쪽 띠에서 배경이 보인다.
        assert any(value > 0 for value in frame[1700 * CANVAS_WIDTH :])
        assert ink_count(frame) > 0
