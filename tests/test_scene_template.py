"""퀴즈 장면 템플릿 — 이슈 #12의 완료 조건에 대응한다.

**LLM을 부르지 않는다.** 입력은 손으로 만든 `quiz.json` 내용이고, 템플릿은 파일이 아니라
dict를 받는다. 콘텐츠가 어떻게 만들어지는지는 `test_quiz_generator.py`가 본다.

여기서 고정하는 것은 두 가지다 — **어느 장면에 `duration`이 있고 어디가 비어 있는가**
(비낭독은 여기서 확정, 낭독은 #16이 실측으로 채운다), 그리고 **타입 전용 정보가 어느 통과
필드로 옮겨 갔는가**(퀴즈 스펙 1.1). 둘 다 틀려도 초안 검증은 통과할 수 있는 지점이다.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from shorts_maker.config import Config, defaults
from shorts_maker.schemas import SchemaError, validate_scenes
from shorts_maker.schemas.scenes import SCHEMA_VERSION
from shorts_maker.shorts_types import DEFAULT_TYPE, get_type
from shorts_maker.types.quiz.scene_template import (
    ANSWER_SFX,
    CATEGORY_LABELS,
    COUNTDOWN_SFX,
    CTA_DURATION,
    HOOK_DURATION,
    NARRATION_TARGET,
    build,
)

HOOK = "이 상식 4개, 다 맞히면 상위 1%"
CTA = "몇 개 맞혔나요? 댓글로 알려주세요!"
CATEGORY = "general_knowledge"

NARRATED_ROLES = ("question", "answer")
FIXED_ROLES = ("hook", "countdown", "cta")
HEADING_ROLES = ("question", "countdown", "answer")

# 퀴즈 스펙 2장의 길이 가이드. 확정 합계가 아니라 예산을 대조하는 기준이다.
LENGTH_GUIDE = {3: 36.0, 4: 47.0, 5: 57.0}
GUIDE_TOLERANCE = 0.10

NARRATION_MEDIAN = 4.3
"""질문 낭독의 실측 중앙값 (스파이크 #2 4.3).

**위 가이드는 이 값에서 나왔고 `NARRATION_TARGET`(3.0)에서 나오지 않았다.** 목표치는 확정값이
튀었는지 보는 기준일 뿐이라 예산보다 짧다. 정답 낭독은 예산도 3.0을 쓴다 (D1 확정 스펙 3장).
"""


def question(index: int, *, countdown_sec: int = 3) -> dict[str, Any]:
    return {
        "id": index,
        "question": f"{index}번 질문은?",
        "answer": f"{index}번 정답",
        "explanation": f"{index}번 해설입니다.",
        "difficulty": "easy",
        "countdown_sec": countdown_sec,
    }


def content(*questions: dict[str, Any]) -> dict[str, Any]:
    """검증까지 끝난 `quiz.json` 내용. `verify`는 템플릿이 보지 않으므로 넣지 않는다."""
    return {
        "schema_version": 1,
        "type": "quiz",
        "category": CATEGORY,
        "language": "ko",
        "hook": HOOK,
        "cta": CTA,
        "questions": list(questions),
    }


def quiz(count: int = 4, *, countdown_sec: int = 3) -> dict[str, Any]:
    return content(
        *(question(index, countdown_sec=countdown_sec) for index in range(1, count + 1))
    )


@pytest.fixture
def config() -> Config:
    return Config(data=defaults())


def scenes_of(data: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [scene for scene in data["scenes"] if scene["role"] == role]


def target_length(scene: dict[str, Any]) -> float:
    """이 장면이 목표하는 길이. 낭독 장면은 목표치가, 나머지는 확정값이 그것이다."""
    return scene["target_duration"] if scene.get("narrate") else scene["duration"]


def budget_length(scene: dict[str, Any]) -> float:
    """스펙 예산이 이 장면에 배정한 길이 (D1 확정 스펙 3장).

    질문 장면만 목표치가 아니라 실측 중앙값을 쓴다. 실제 길이는 합성된 오디오가 정하므로
    (#16) 목표치 합계를 가이드와 대조하면 늘 짧게 나온다 — 대조하려는 것은 템플릿이 정한
    **고정 길이**가 그 예산 안에 있는가다.
    """
    return NARRATION_MEDIAN if scene["role"] == "question" else target_length(scene)


# --- 장면 구성 ---------------------------------------------------------------


def test_four_questions_become_hook_plus_three_each_plus_cta(config: Config) -> None:
    data = build(quiz(4), config=config)

    assert len(data["scenes"]) == 1 + 3 * 4 + 1
    assert data["scenes"][0]["role"] == "hook"
    assert data["scenes"][-1]["role"] == "cta"


def test_the_draft_passes_scene_validation(config: Config) -> None:
    """`build`가 스스로 검증하지만, 통과 여부를 여기서도 고정한다."""
    validate_scenes(build(quiz(4), config=config))


def test_the_three_scenes_of_a_question_are_adjacent_and_ordered(config: Config) -> None:
    data = build(quiz(3), config=config)

    blocks = [scene["role"] for scene in data["scenes"][1:-1]]

    assert blocks == ["question", "countdown", "answer"] * 3


def test_header_names_the_type_and_schema_version(config: Config) -> None:
    """`type`은 타입 선언의 이름과 같아야 한다 — 렌더가 이 값으로 타입을 되찾는다."""
    data = build(quiz(), config=config)

    assert data["type"] == get_type(DEFAULT_TYPE).name
    assert data["schema_version"] == SCHEMA_VERSION


def test_scene_order_follows_question_order(config: Config) -> None:
    """세그먼트 파일명이 장면 배열 위치로 매겨지므로 순서가 계약이다 (PRD 7.5.2)."""
    data = build(quiz(4), config=config)

    ids = [scene["question_id"] for scene in data["scenes"][1:-1]]

    assert ids == [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4]


# --- duration과 target_duration ---------------------------------------------


def test_narrated_scenes_leave_duration_to_the_measured_audio(config: Config) -> None:
    """확정은 #16의 몫이다 (PRD 7.5.1). 여기서 채우면 실측값이 덮이는 대신 굳어 버린다."""
    data = build(quiz(4), config=config)

    narrated = [scene for role in NARRATED_ROLES for scene in scenes_of(data, role)]

    assert len(narrated) == 8
    for scene in narrated:
        assert "duration" not in scene
        assert scene["narrate"] is True
        assert scene["text"]
        assert scene["target_duration"] == NARRATION_TARGET


def test_scenes_without_narration_get_their_final_duration_here(config: Config) -> None:
    """TTS는 `narrate: true` 장면만 건드린다. 비워 두면 채울 주체가 없다."""
    data = build(quiz(4), config=config)

    fixed = [scene for role in FIXED_ROLES for scene in scenes_of(data, role)]

    assert len(fixed) == 6
    for scene in fixed:
        assert scene["duration"] > 0
        assert "narrate" not in scene
        assert "target_duration" not in scene


def test_hook_and_cta_carry_the_quiz_text_and_the_fixed_lengths(config: Config) -> None:
    data = build(quiz(), config=config)

    hook, cta = data["scenes"][0], data["scenes"][-1]

    assert (hook["text"], hook["duration"]) == (HOOK, HOOK_DURATION)
    assert (cta["text"], cta["duration"]) == (CTA, CTA_DURATION)


def test_countdown_duration_matches_its_own_seconds(config: Config) -> None:
    """숫자 전환이 정수 초에 맞아야 하므로 실측 보정 대상이 아니다. 문제마다 값이 다르다."""
    source = content(question(1, countdown_sec=3), question(2, countdown_sec=5))

    data = build(source, config=config)

    countdowns = scenes_of(data, "countdown")

    assert [scene["seconds"] for scene in countdowns] == [3, 5]
    for scene in countdowns:
        assert scene["duration"] == scene["seconds"]


def test_the_fixed_lengths_are_the_ones_the_design_verified(config: Config) -> None:
    """숫자를 상수로 되짚지 않고 직접 적는다 (D1 확정 스펙 3장).

    시안이 이 길이로 프레임 레이아웃을 검증했으므로, 상수가 조용히 바뀌면 검증이 무효가 된다.
    """
    data = build(quiz(1), config=config)

    assert data["scenes"][0]["duration"] == 2.5
    assert data["scenes"][-1]["duration"] == 3.0
    assert scenes_of(data, "countdown")[0]["duration"] == 3.0


def test_a_longer_countdown_setting_still_lengthens_the_scene(config: Config) -> None:
    """`countdown_sec`는 설정값 그대로다 — 3이 기본값이지만 상한이 아니다 (D1 확정 스펙 3장)."""
    countdown = scenes_of(build(quiz(1, countdown_sec=4), config=config), "countdown")[0]

    assert (countdown["seconds"], countdown["duration"]) == (4, 4.0)


@pytest.mark.parametrize("count", sorted(LENGTH_GUIDE))
def test_budget_total_stays_within_the_length_guide(config: Config, count: int) -> None:
    """3문제 ≈ 36초 / 4문제 ≈ 47초 / 5문제 ≈ 57초 (퀴즈 스펙 2장, D1 확정 스펙 3장).

    확정 합계가 아니라 예산이다. 실측 확정 뒤에 PRD 6.3의 45~60초를 벗어나는지 보는 것은
    #16이고, 여기서 보는 것은 템플릿의 고정 길이가 그 예산을 겨냥하고 있는가다 — 고정 길이
    세 개가 짧아진 이유가 이 예산에 여유를 만드는 것이었다.
    """
    data = build(quiz(count), config=config)

    total = sum(budget_length(scene) for scene in data["scenes"])
    guide = LENGTH_GUIDE[count]

    assert abs(total - guide) <= guide * GUIDE_TOLERANCE


# --- 타입 전용 정보의 이전 (퀴즈 스펙 1.1) -----------------------------------


def test_the_explanation_moves_into_the_answer_caption(config: Config) -> None:
    """렌더러는 `quiz.json`을 열 수 없다. 해설이 여기 실리지 않으면 화면에서 사라진다."""
    data = build(quiz(3), config=config)

    captions = [scene["caption"] for scene in scenes_of(data, "answer")]

    assert captions == ["1번 해설입니다.", "2번 해설입니다.", "3번 해설입니다."]


def test_answer_scenes_speak_the_answer_not_the_explanation(config: Config) -> None:
    """낭독 범위는 질문과 정답까지다 (퀴즈 스펙 0장). 해설은 자막으로만 나간다."""
    answer = scenes_of(build(quiz(1), config=config), "answer")[0]

    assert answer["text"] == "1번 정답"
    assert answer["caption"] != answer["text"]


def test_question_scenes_speak_the_question(config: Config) -> None:
    assert scenes_of(build(quiz(1), config=config), "question")[0]["text"] == "1번 질문은?"


def test_question_id_marks_only_the_scenes_that_came_from_a_question(config: Config) -> None:
    data = build(quiz(2), config=config)

    for scene in data["scenes"]:
        if scene["role"] in ("hook", "cta"):
            assert "question_id" not in scene
        else:
            assert scene["question_id"] in (1, 2)


def test_the_question_becomes_the_heading_of_all_three_scenes(config: Config) -> None:
    """시안은 카운트다운·정답 장면에도 질문을 띄운다 (D1 확정 스펙 5.2~5.4).

    세 장면의 값이 같아야 `drawtext` 인스턴스 하나가 두 장면을 덮을 수 있다 (5.3).
    """
    data = build(quiz(2), config=config)

    for role in HEADING_ROLES:
        assert [scene["heading"] for scene in scenes_of(data, role)] == [
            "1번 질문은?",
            "2번 질문은?",
        ]


def test_the_answer_scene_splits_heading_and_text(config: Config) -> None:
    """`heading`을 신설한 이유가 이 장면이다 — `text`는 정답이라 질문을 담을 수 없다."""
    answer = scenes_of(build(quiz(1), config=config), "answer")[0]

    assert answer["heading"] == "1번 질문은?"
    assert answer["text"] == "1번 정답"


def test_hook_and_cta_carry_no_heading(config: Config) -> None:
    """렌더러 규칙은 "`heading`이 있으면 상단에 그린다" 하나다 (D1 확정 스펙 8장)."""
    data = build(quiz(2), config=config)

    assert "heading" not in data["scenes"][0]
    assert "heading" not in data["scenes"][-1]


def test_the_category_becomes_the_hook_kicker(config: Config) -> None:
    """렌더러는 `quiz.json`을 열 수 없으므로 라벨이 여기 실려야 화면에 나온다."""
    hook = build(quiz(1), config=config)["scenes"][0]

    assert hook["kicker"] == CATEGORY_LABELS[CATEGORY] == "상식 퀴즈"


def test_only_the_hook_carries_a_kicker(config: Config) -> None:
    for scene in build(quiz(2), config=config)["scenes"][1:]:
        assert "kicker" not in scene


def test_an_unmapped_category_stops_before_the_label_reaches_the_screen(
    config: Config,
) -> None:
    """영어 카테고리 이름을 화면에 내거나 라벨 자리를 비우는 대신 멈춘다."""
    source = quiz(1)
    source["category"] = "world_history"

    with pytest.raises(ValueError) as error:
        build(source, config=config)

    assert "world_history" in str(error.value)
    assert CATEGORY in str(error.value)  # 고칠 자리를 알려 준다


def test_every_category_the_generator_produces_has_a_label() -> None:
    """생성기가 고정한 카테고리에 라벨이 없으면 모든 실행이 hook에서 멈춘다."""
    from shorts_maker.types.quiz.quiz_generator import CATEGORY as GENERATED

    assert GENERATED in CATEGORY_LABELS


def test_the_category_label_map_lives_only_inside_the_type_package() -> None:
    """한국어 라벨이 공통 파이프라인에 새면 두 번째 타입이 자기 라벨을 넣을 자리가 없다.

    `tests/test_type_boundary.py`는 import와 산출물 파일명을 막지만 라벨 문자열은 모른다 —
    라벨 목록에서 유도해야 새 카테고리가 추가돼도 낡지 않으므로 검사를 여기 둔다 (퀴즈 스펙 1.1).

    **문자열 상수가 라벨과 같은지만 본다.** 부분 문자열로 보면 `render.cta_tail` 기본값
    (`매일 새 상식 퀴즈`)이 걸린다 — 그 값은 채널 브랜딩이고 카테고리 매핑이 아니다.
    """
    import shorts_maker

    package_root = Path(shorts_maker.__file__).resolve().parent
    types_package = package_root / "types"
    labels = set(CATEGORY_LABELS.values())

    leaked = [
        f"{path.relative_to(package_root)}:{node.lineno} {node.value!r}"
        for path in sorted(package_root.rglob("*.py"))
        if types_package not in path.parents
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and node.value in labels
    ]

    assert leaked == [], "\n".join(leaked)


def test_countdown_and_answer_name_their_sound_effects(config: Config) -> None:
    """에셋 파일은 #18이 붙인다. 여기서 정하는 것은 이름뿐이다."""
    data = build(quiz(2), config=config)

    assert [scene["sfx"] for scene in scenes_of(data, "countdown")] == [COUNTDOWN_SFX] * 2
    assert [scene["sfx"] for scene in scenes_of(data, "answer")] == [ANSWER_SFX] * 2
    assert "sfx" not in data["scenes"][0]


def test_the_verify_verdict_does_not_move_into_scenes(config: Config) -> None:
    """경고는 #11이 `quiz.json`을 보고 낸다. 장면에 실으면 판정이 두 곳에 생긴다."""
    source = quiz(1)
    source["questions"][0]["verify"] = {"status": "flagged", "confidence": 0.1}

    data = build(source, config=config)

    assert "verify" not in str(data)


# --- 계약 ---------------------------------------------------------------------


def test_the_template_takes_a_mapping_not_a_file(config: Config) -> None:
    """파일을 직접 열면 앱(#28)이 편집 중인 상태로는 미리보기를 만들 수 없다."""
    from shorts_maker.types.quiz import scene_template

    source = inspect.getsource(scene_template)

    assert "open" not in source
    assert "load_quiz" not in source
    # 인자로 받은 dict만으로 동작한다 — 아래 호출에 파일 경로가 등장하지 않는다.
    assert build(quiz(), config=config)["scenes"]


def test_a_broken_draft_is_rejected_before_it_leaves_the_template(config: Config) -> None:
    """스스로 검증하지 않으면 잘못된 계약이 TTS·자막·렌더까지 그대로 흘러간다."""
    broken = quiz(1)
    broken["questions"][0]["countdown_sec"] = 0  # `seconds`는 1 이상이어야 한다

    with pytest.raises(SchemaError) as error:
        build(broken, config=config)

    assert "seconds" in str(error.value)
