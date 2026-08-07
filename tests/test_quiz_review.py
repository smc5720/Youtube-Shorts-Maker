"""임계값 판정과 검수 항목 — 이슈 #11의 완료 조건에 대응한다.

**LLM을 부르지 않는다.** 검증기가 이미 채운 `verify`에서 시작하므로 입력은 손으로 만든
고정값이다. 검증 호출 자체의 동작은 `test_quiz_verifier.py`가 본다.

임계값 기본값 0.8의 근거는 코드나 여기가 아니라 `docs/spikes/10-verifier-detection.md`
5장의 측정에 있다. 여기서 고정하는 것은 **경계 방향**과 **사유가 어디서 오는가**다.
"""

from __future__ import annotations

from typing import Any

import pytest

from shorts_maker.config import Config, defaults, load_config
from shorts_maker.schemas import validate_quiz
from shorts_maker.shorts_types import ContentIssue, get_type
from shorts_maker.types.quiz.quiz_review import THRESHOLD_KEY, review
from shorts_maker.types.quiz.quiz_verifier import (
    FLAGGED,
    SOURCE_MAX_LEN,
    UNVERIFIED,
    VERIFIED,
)

DEFAULT_THRESHOLD = 0.8
"""#10 측정이 권고하고 #11이 확정한 기본값. `SPEC`과 어긋나면 아래 첫 테스트가 잡는다."""

QUESTIONS = [
    ("세계에서 가장 긴 강은?", "나일강"),
    ("대한민국의 수도는?", "서울"),
    ("물의 화학식은?", "H2O"),
]


# --- 입력 만들기 -------------------------------------------------------------


def config_with(threshold: float | None = None) -> Config:
    if threshold is None:
        return Config(data=defaults())
    return load_config(overrides={THRESHOLD_KEY: threshold})


def verified(confidence: float, source: str | None = "근거 요약") -> dict[str, Any]:
    entry: dict[str, Any] = {"status": VERIFIED, "confidence": confidence}
    if source is not None:
        entry["source"] = source
    return entry


def content(*entries: dict[str, Any] | None) -> dict[str, Any]:
    """검증기가 `verify`를 채운 직후의 콘텐츠. `None`은 `verify`가 없는 문제다."""
    return {
        "schema_version": 1,
        "type": "quiz",
        "category": "general_knowledge",
        "language": "ko",
        "hook": "이 상식 다 맞히면 상위 1%",
        "cta": "몇 개 맞혔나요?",
        "questions": [
            {
                "id": index,
                "question": QUESTIONS[index - 1][0],
                "answer": QUESTIONS[index - 1][1],
                "explanation": "해설입니다.",
                "difficulty": "easy",
                "countdown_sec": 4,
                **({"verify": entry} if entry is not None else {}),
            }
            for index, entry in enumerate(entries, start=1)
        ],
    }


def statuses(data: dict[str, Any]) -> list[str]:
    return [item["verify"]["status"] for item in data["questions"]]


def sources(data: dict[str, Any]) -> list[str]:
    return [item["verify"].get("source", "") for item in data["questions"]]


# --- 임계값 ------------------------------------------------------------------


def test_the_threshold_default_is_the_measured_one() -> None:
    """0.8은 잠정값이 아니라 #10 측정에서 확정된 값이다 (측정 보고서 5장)."""
    assert Config(data=defaults()).get(THRESHOLD_KEY) == DEFAULT_THRESHOLD
    assert THRESHOLD_KEY == "llm.verifier.confidence_threshold"


def test_confidence_below_the_threshold_is_flagged() -> None:
    data = content(verified(0.79))

    issues = review(data, config=config_with())

    assert statuses(data) == [FLAGGED]
    assert "임계값 미달" in sources(data)[0]
    assert len(issues) == 1


def test_confidence_exactly_at_the_threshold_passes() -> None:
    """경계 방향을 코드가 아니라 여기서 고정한다. 설정에 0.8을 적은 사람이 0.8짜리
    문제의 운명을 예측할 수 있어야 한다."""
    data = content(verified(DEFAULT_THRESHOLD))

    assert review(data, config=config_with()) == []
    assert statuses(data) == [VERIFIED]


def test_confidence_above_the_threshold_passes() -> None:
    data = content(verified(0.95))

    assert review(data, config=config_with()) == []
    assert statuses(data) == [VERIFIED]


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [(0.5, [VERIFIED]), (0.9, [FLAGGED])],
)
def test_the_shared_config_key_moves_the_cut(threshold: float, expected: list[str]) -> None:
    """`quiz` 아래 새 키를 만들지 않는다 — 판정은 `llm.verifier.confidence_threshold`가 정한다."""
    data = content(verified(0.7))

    review(data, config=config_with(threshold))

    assert statuses(data) == expected


def test_the_applied_threshold_is_the_one_in_the_message() -> None:
    data = content(verified(0.7))

    issues = review(data, config=config_with(0.9))

    assert "0.9" in issues[0].reason
    assert "0.9" in sources(data)[0]


# --- 임계값과 무관한 실검출 ---------------------------------------------------


def test_an_already_flagged_question_keeps_the_verifier_reason() -> None:
    """실검출은 `status`에서 나온다 (#10 측정 4장). "임계값 미달"로 덧쓰면 사유를 잃는다."""
    detected = {
        "status": FLAGGED,
        "confidence": 0.0,
        "source": "재답변 불일치 — 2회 중 0회 일치. 재답변: '아마존강', '나일강'",
    }
    data = content(detected)

    issues = review(data, config=config_with())

    assert sources(data) == [detected["source"]]
    assert "재답변 불일치" in issues[0].reason
    assert "임계값 미달" not in issues[0].reason


def test_unverified_is_flagged_but_keeps_saying_why() -> None:
    """`unverified`는 성공이 아니라 "판단 근거가 없다"이므로 통과시키지 않는다.
    사유는 검증기가 적은 그대로 남아 "검증 미완료"와 "재답변 불일치"가 계속 구분된다."""
    data = content(
        {
            "status": UNVERIFIED,
            "confidence": 0.0,
            "source": "검증 미완료 — 블라인드 재답변 호출이 실패했다",
        }
    )

    issues = review(data, config=config_with())

    assert statuses(data) == [FLAGGED]
    assert "검증 미완료" in issues[0].reason
    assert "임계값 미달" not in issues[0].reason


def test_a_question_without_verify_is_not_treated_as_passing() -> None:
    """검증 단계를 건너뛴 콘텐츠(초안, 사람이 지운 파일)도 검수 대상이다."""
    data = content(None)

    issues = review(data, config=config_with())

    assert statuses(data) == [FLAGGED]
    assert "검증 미완료" in issues[0].reason


def test_a_flagged_question_without_a_source_still_says_something() -> None:
    """`quiz.json`은 사람이 손으로 고치는 파일이라 근거 칸이 지워진 채 들어올 수 있다."""
    data = content({"status": FLAGGED, "confidence": 0.4})

    issues = review(data, config=config_with())

    assert issues[0].reason.strip()
    assert "confidence 0.4" in issues[0].reason


# --- 검수 항목 ---------------------------------------------------------------


def test_the_issue_carries_id_question_confidence_and_threshold() -> None:
    """완료 조건이 요구하는 다섯 가지가 한 항목 안에 있어야 한다."""
    data = content(verified(0.72))

    issue = review(data, config=config_with())[0]

    assert isinstance(issue, ContentIssue)
    assert "1" in issue.subject
    assert issue.summary == QUESTIONS[0][0]
    assert "0.72" in issue.reason  # confidence
    assert "0.8" in issue.reason  # 적용된 임계값
    assert "임계값 미달" in issue.reason  # 사유


def test_each_number_appears_once_in_the_reason() -> None:
    """임계값 사유는 `source`에 이미 두 값을 담고 있다. 경고가 다시 붙이면 같은 숫자가
    한 줄에 두 번 나오고, 사람이 두 값을 다른 값으로 읽는다."""
    reason = review(content(verified(0.6)), config=config_with())[0].reason

    assert reason.count("0.6") == 1
    assert reason.count("0.8") == 1


def test_only_the_flagged_questions_become_issues() -> None:
    data = content(verified(0.99), verified(0.5), {"status": FLAGGED, "confidence": 0.0})

    issues = review(data, config=config_with())

    assert statuses(data) == [VERIFIED, FLAGGED, FLAGGED]
    assert [issue.subject for issue in issues] == ["문제 2", "문제 3"]


def test_nothing_to_review_returns_an_empty_list() -> None:
    assert review(content(verified(0.9), verified(0.85)), config=config_with()) == []


def test_the_basis_survives_the_downgrade() -> None:
    """임계값으로 내린 문제의 근거를 지우지 않는다 — 사람이 검수할 때 읽는 값이다."""
    data = content(verified(0.6, source="나일강이 최장이라는 통설"))

    review(data, config=config_with())

    assert "나일강이 최장이라는 통설" in sources(data)[0]


# --- 산출물 계약 -------------------------------------------------------------


def test_review_is_idempotent() -> None:
    """앱(#30)이 같은 콘텐츠에 다시 부른다. 사유가 겹쳐 쌓이면 안 된다."""
    data = content(verified(0.6), {"status": UNVERIFIED, "confidence": 0.0}, verified(0.99))

    first = review(data, config=config_with())
    snapshot = [dict(item["verify"]) for item in data["questions"]]
    second = review(data, config=config_with())

    assert first == second
    assert [dict(item["verify"]) for item in data["questions"]] == snapshot


def test_reviewed_content_passes_the_quiz_schema() -> None:
    data = content(verified(0.6), None, {"status": UNVERIFIED, "confidence": 0.0})

    review(data, config=config_with())

    validate_quiz(data)  # 위반이 있으면 SchemaError로 실패한다


def test_the_source_stays_within_the_cap() -> None:
    """`quiz.json`은 사람이 읽는 원본이다. 사유를 이어 붙이다 문제 배열이 길어지면 안 된다."""
    data = content(verified(0.1, source="가" * 500))

    review(data, config=config_with())

    assert len(sources(data)[0]) == SOURCE_MAX_LEN


# --- 타입 선언 ---------------------------------------------------------------


def test_quiz_declares_this_module_as_its_content_review() -> None:
    quiz = get_type("quiz")

    assert quiz.content_review is not None
    assert quiz.content_review.__module__ == "shorts_maker.types.quiz.quiz_review"


def test_the_registry_hook_routes_to_the_same_judgement() -> None:
    data = content(verified(0.6))

    issues = get_type("quiz").review(data, config=config_with())

    assert statuses(data) == [FLAGGED]
    assert len(issues) == 1
