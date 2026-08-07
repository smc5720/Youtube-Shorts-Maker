"""블라인드 재답변 검증기 — 이슈 #10의 완료 조건에 대응한다.

**실제 LLM을 부르지 않는다.** `stub_llm` 픽스처가 provider 레지스트리를 가짜로 바꾸고,
검증 호출의 응답은 아래 `answers()` / `verdicts()`가 만든다. 큐는 FIFO이고 검증기는
재답변 `runs`회 → 모호성 프로브 1회 순으로 부르므로 그 순서대로 넣는다.

검출률·임계값 근거처럼 실제 모델이 있어야 하는 측정은 여기가 아니라
`docs/spikes/10-verifier-detection.md`에 있다.
"""

from __future__ import annotations

from typing import Any

import pytest

from shorts_maker.config import Config, defaults, load_config
from shorts_maker.llm import LLMError
from shorts_maker.schemas import validate_quiz
from shorts_maker.schemas.quiz import VERIFY_STATUSES
from shorts_maker.types.quiz.quiz_verifier import (
    FLAGGED,
    SOURCE_MAX_LEN,
    UNVERIFIED,
    VERIFIED,
    agrees,
    build_ambiguity_prompt,
    build_answer_prompt,
    normalize,
    verify,
)

from conftest import StubLLM

# --- 입력 만들기 -------------------------------------------------------------

QUESTIONS = [
    ("세계에서 가장 긴 강은?", "나일강"),
    ("대한민국의 수도는?", "서울"),
    ("물의 화학식은?", "H2O"),
]


def config_with(**overrides: Any) -> Config:
    return load_config(overrides=overrides) if overrides else Config(data=defaults())


def content(count: int = 3) -> dict[str, Any]:
    """`quiz_generator`가 만든 직후의 초안 — `verify`가 아직 없다."""
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
                "question": question,
                "answer": answer,
                "explanation": "해설입니다.",
                "difficulty": "easy",
                "countdown_sec": 4,
            }
            for index, (question, answer) in enumerate(QUESTIONS[:count], start=1)
        ],
    }


def answers(*entries: tuple[int, str, float]) -> dict[str, Any]:
    """재답변 호출 1회의 응답. `(문제 id, 답, 확신도)`."""
    return {
        "answers": [
            {"id": question_id, "answer": answer, "certainty": certainty, "basis": "근거 요약"}
            for question_id, answer, certainty in entries
        ]
    }


def verdicts(*entries: tuple[int, bool]) -> dict[str, Any]:
    """모호성 프로브 응답. `(문제 id, 답이 하나로 정해지는가)`."""
    return {
        "questions": [
            {"id": question_id, "single_answer": single, "reason": "판단 근거"}
            for question_id, single in entries
        ]
    }


def all_correct(certainty: float = 0.9, count: int = 3) -> dict[str, Any]:
    return answers(*[(index, QUESTIONS[index - 1][1], certainty) for index in range(1, count + 1)])


def all_unambiguous(count: int = 3) -> dict[str, Any]:
    return verdicts(*[(index, True) for index in range(1, count + 1)])


def verified_run(stub_llm: StubLLM, *, certainty: float = 0.9, count: int = 3) -> None:
    """모든 문제가 통과하는 응답 세트를 큐에 넣는다 (재답변 2회 + 프로브 1회)."""
    stub_llm.reply(
        all_correct(certainty, count), all_correct(certainty, count), all_unambiguous(count)
    )


def statuses(data: dict[str, Any]) -> list[str]:
    return [item["verify"]["status"] for item in data["questions"]]


# --- 블라인드성 --------------------------------------------------------------


def test_prompts_never_carry_the_generated_answer(stub_llm: StubLLM) -> None:
    """이 모듈의 유일한 안전장치다. 정답을 보여주면 모델이 자기 출력에 동조한다 (스파이크 2.3)."""
    data = content()
    verified_run(stub_llm)

    verify(data, config=config_with())

    for call in stub_llm.calls:
        for question in data["questions"]:
            assert question["answer"] not in call["prompt"]
            assert question["explanation"] not in call["prompt"]


def test_prompt_builders_carry_the_questions_but_not_the_answers() -> None:
    """프롬프트 조립 함수를 직접 본다 — 호출 경로를 거치지 않아도 성질이 유지돼야 한다."""
    questions = content()["questions"]

    for prompt in (build_answer_prompt(questions), build_ambiguity_prompt(questions)):
        assert all(question["question"] in prompt for question in questions)
        assert all(question["answer"] not in prompt for question in questions)


def test_the_ambiguity_probe_asks_about_the_question_not_the_answer(stub_llm: StubLLM) -> None:
    verified_run(stub_llm)

    verify(content(), config=config_with())

    probe = stub_llm.calls[-1]
    assert "답하지는 마라" in probe["prompt"]
    assert "single_answer" in str(probe["schema"])


# --- 정답 대조 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "given"),
    [
        ("나일강", "나일 강"),  # 띄어쓰기
        ("세종대왕(조선 제4대 왕)", "세종대왕"),  # 괄호 병기가 원본 쪽에
        ("세종대왕", "세종대왕(조선 제4대 왕 세종)"),  # 괄호 병기가 재답변 쪽에
        ("파리", "프랑스 파리"),  # 범위 차이
        ("H2O", "H₂O"),  # 첨자 (NFKC)
        ("에펠탑", "에펠 탑"),
        ("6,650km", "6650 km"),  # 자릿수 구분과 단위 사이 공백
        ("1592년", "1592"),  # 한쪽에만 단위가 붙은 수
        ("나일강.", "나일강"),  # 문장부호
        ("Seoul", "seoul"),  # 대소문자
    ],
)
def test_notation_differences_are_not_counted_as_mismatch(expected: str, given: str) -> None:
    """표기만 다른 같은 답을 불일치로 세면 검증이 사실 검사가 아니라 표기 검사가 된다."""
    assert agrees(expected, given)


@pytest.mark.parametrize(
    ("expected", "given"),
    [
        ("직지심체요절", "상정고금예문"),
        ("나일강", "아마존강"),
        ("서울", ""),  # 모른다고 답한 경우
        ("", "서울"),
        ("6", "16"),  # 짧은 답에 포함 규칙을 적용하면 안 된다
        ("1234", "234"),  # 부분 문자열이지만 다른 수
        ("1592", "1492"),
    ],
)
def test_different_answers_are_a_mismatch(expected: str, given: str) -> None:
    assert not agrees(expected, given)


def test_normalization_is_symmetric_for_the_fixed_corpus() -> None:
    assert normalize("세종대왕(조선 제4대 왕)") == normalize("세종대왕")
    assert normalize("나일 강") == normalize("나일강")


# --- 판정 -------------------------------------------------------------------


def test_agreeing_answers_pass_and_fill_every_field(stub_llm: StubLLM) -> None:
    data = content()
    verified_run(stub_llm, certainty=0.9)

    verify(data, config=config_with())

    assert statuses(data) == [VERIFIED] * 3
    for item in data["questions"]:
        assert 0.0 <= item["verify"]["confidence"] <= 1.0
        assert item["verify"]["source"].strip()


def test_a_wrong_answer_is_not_verified(stub_llm: StubLLM) -> None:
    """의도적으로 틀린 정답을 넣은 고정 입력 — 2번 문제만 재답변이 갈린다."""
    data = content()
    data["questions"][1]["answer"] = "부산"  # 재답변은 "서울"이라 답한다
    verified_run(stub_llm)

    verify(data, config=config_with())

    assert statuses(data) == [VERIFIED, FLAGGED, VERIFIED]
    assert "재답변 불일치" in data["questions"][1]["verify"]["source"]


def test_one_disagreeing_run_out_of_two_flags_the_question(stub_llm: StubLLM) -> None:
    data = content()
    stub_llm.reply(
        all_correct(),
        answers((1, "아마존강", 0.9), (2, "서울", 0.9), (3, "H2O", 0.9)),
        all_unambiguous(),
    )

    verify(data, config=config_with())

    assert statuses(data) == [FLAGGED, VERIFIED, VERIFIED]
    assert data["questions"][0]["verify"]["confidence"] <= 0.5


def test_a_missing_id_counts_as_a_mismatch(stub_llm: StubLLM) -> None:
    """모델이 한 문제를 빠뜨린 것을 "모르겠다"가 아니라 "확인되지 않음"으로 센다."""
    data = content()
    stub_llm.reply(all_correct(), answers((1, "나일강", 0.9), (2, "서울", 0.9)), all_unambiguous())

    verify(data, config=config_with())

    assert statuses(data) == [VERIFIED, VERIFIED, FLAGGED]
    assert "응답 없음" in data["questions"][2]["verify"]["source"]


# --- 모호성 프로브 -----------------------------------------------------------


def test_an_ambiguous_question_is_flagged_even_when_every_answer_agrees(
    stub_llm: StubLLM,
) -> None:
    """금속활자본 유형 — 정답이 틀린 게 아니라 질문이 모호하다 (스파이크 4.2).

    블라인드 재답변으로는 원리상 잡히지 않는다. 재답변 모델도 같은 근거로 같은 답을 낸다.
    """
    data = content(count=1)
    data["questions"][0]["question"] = "한국 최초의 금속활자본은?"
    data["questions"][0]["answer"] = "직지심체요절"
    match = answers((1, "직지심체요절", 0.95))
    stub_llm.reply(match, match, verdicts((1, False)))

    verify(data, config=config_with())

    assert statuses(data) == [FLAGGED]
    assert "질문 모호" in data["questions"][0]["verify"]["source"]


def test_the_probe_is_the_only_path_that_flags_a_fully_agreeing_question(
    stub_llm: StubLLM,
) -> None:
    """같은 입력에서 프로브 판정만 뒤집으면 결과가 갈린다 — 두 축이 독립이라는 뜻이다."""
    data = content(count=1)
    match = answers((1, "나일강", 0.95))
    stub_llm.reply(match, match, verdicts((1, True)))
    verify(data, config=config_with())
    assert statuses(data) == [VERIFIED]

    again = content(count=1)
    stub_llm.reply(match, match, verdicts((1, False)))
    verify(again, config=config_with())
    assert statuses(again) == [FLAGGED]


# --- confidence --------------------------------------------------------------


def test_confidence_is_agreement_times_certainty(stub_llm: StubLLM) -> None:
    data = content(count=1)
    stub_llm.reply(
        answers((1, "나일강", 0.9)), answers((1, "나일강", 0.7)), verdicts((1, True))
    )

    verify(data, config=config_with())

    assert data["questions"][0]["verify"]["confidence"] == pytest.approx(0.8)


def test_confidence_keeps_resolution_at_the_default_runs(stub_llm: StubLLM) -> None:
    """`runs: 2`에서 일치율만 쓰면 값이 {0, 0.5, 1} 셋뿐이라 임계값 0.8이
    "2회 전부 일치"와 같은 뜻이 된다. 확신도를 곱해 그 사이를 채운다."""
    data = content(count=3)
    high, low = 0.95, 0.6
    stub_llm.reply(
        answers((1, "나일강", high), (2, "서울", low), (3, "H2O", 0.75)),
        answers((1, "나일강", high), (2, "서울", low), (3, "H2O", 0.75)),
        all_unambiguous(),
    )

    verify(data, config=config_with())

    scores = [item["verify"]["confidence"] for item in data["questions"]]
    assert statuses(data) == [VERIFIED] * 3  # 셋 다 전원 일치인데
    assert len(set(scores)) == 3  # confidence는 갈린다
    assert scores[0] >= 0.8 > scores[2] > scores[1]  # 임계값 0.8이 이 집합을 실제로 자른다


def test_a_split_vote_can_never_reach_the_default_threshold(stub_llm: StubLLM) -> None:
    """확신도가 1을 넘지 못하므로 일치율 0.5인 문제의 confidence 상한이 0.5다."""
    data = content(count=1)
    stub_llm.reply(answers((1, "나일강", 1.0)), answers((1, "아마존강", 1.0)), verdicts((1, True)))

    verify(data, config=config_with())

    assert data["questions"][0]["verify"]["confidence"] <= 0.5


@pytest.mark.parametrize("certainty", [-1.0, 2.0])
def test_certainty_outside_the_range_cannot_push_confidence_out_of_range(
    stub_llm: StubLLM, certainty: float
) -> None:
    data = content(count=1)
    match = answers((1, "나일강", certainty))
    stub_llm.reply(match, match, verdicts((1, True)))

    verify(data, config=config_with())

    assert 0.0 <= data["questions"][0]["verify"]["confidence"] <= 1.0
    validate_quiz(data)


# --- 호출 수 ----------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 3])
def test_calls_do_not_scale_with_question_count(stub_llm: StubLLM, count: int) -> None:
    """문제별 호출은 CLI 기동 오버헤드(호출당 약 6.5초)를 문제 수만큼 곱한다 (스파이크 3장)."""
    verify(content(count), config=config_with())

    assert stub_llm.call_count == 3  # 재답변 2회 + 프로브 1회


@pytest.mark.parametrize("runs", [1, 3])
def test_runs_config_decides_how_many_blind_answers(stub_llm: StubLLM, runs: int) -> None:
    verify(content(), config=config_with(**{"llm.verifier.runs": runs}))

    assert stub_llm.call_count == runs + 1


def test_the_verifier_role_decides_the_model(stub_llm: StubLLM) -> None:
    verify(content(), config=config_with(**{"llm.verifier.model": "haiku"}))

    assert {call["model"] for call in stub_llm.calls} == {"haiku"}


# --- 실패 경로 --------------------------------------------------------------


def test_failed_answer_calls_leave_the_questions_unverified(stub_llm: StubLLM) -> None:
    """검증이 실패해도 예외를 던지지 않는다 — 생성 비용을 버리지 않고 사람이 검수한다."""
    data = content()
    # 재답변 2회가 각각 `llm.max_retries`(2)까지 쓰고 실패한다. 프로브는 성공한다.
    stub_llm.reply(*[LLMError("structured_output이 없다")] * 6, all_unambiguous())

    verify(data, config=config_with())

    assert statuses(data) == [UNVERIFIED] * 3
    assert all(item["verify"]["confidence"] == 0.0 for item in data["questions"])
    assert "블라인드 재답변" in data["questions"][0]["verify"]["source"]
    validate_quiz(data)


def test_a_failed_probe_leaves_the_questions_unverified(stub_llm: StubLLM) -> None:
    """프로브가 실검출의 유일한 경로다. 돌지 않았으면 "결함 없음"이라고 말할 수 없다."""
    data = content()
    stub_llm.reply(all_correct(), all_correct(), *[LLMError("호출 실패")] * 3)

    verify(data, config=config_with())

    assert statuses(data) == [UNVERIFIED] * 3
    assert "모호성 프로브" in data["questions"][0]["verify"]["source"]


def test_one_failed_run_reduces_the_denominator_instead_of_stopping(stub_llm: StubLLM) -> None:
    """실패한 회차를 다시 부르지 않는다 — `RetryingProvider`가 이미 재시도했다."""
    data = content(count=1)
    stub_llm.reply(
        *[LLMError("실패")] * 3, answers((1, "나일강", 0.8)), verdicts((1, True))
    )

    verify(data, config=config_with())

    assert statuses(data) == [VERIFIED]
    assert data["questions"][0]["verify"]["confidence"] == pytest.approx(0.8)


def test_a_response_without_the_expected_list_does_not_crash(stub_llm: StubLLM) -> None:
    """provider가 계약을 어겨도 검증 단계가 콘텐츠를 무너뜨리지 않는다."""
    data = content(count=1)
    stub_llm.reply({}, {}, {})

    verify(data, config=config_with())

    assert statuses(data) == [UNVERIFIED]
    validate_quiz(data)


# --- 산출물 계약 -------------------------------------------------------------


def test_verified_content_passes_the_quiz_schema(stub_llm: StubLLM) -> None:
    data = content()
    verified_run(stub_llm)

    verify(data, config=config_with())

    validate_quiz(data)  # 위반이 있으면 SchemaError로 실패한다


def test_every_status_is_one_the_schema_knows() -> None:
    assert {VERIFIED, UNVERIFIED, FLAGGED} == set(VERIFY_STATUSES)


def test_source_is_omitted_rather_than_left_empty(stub_llm: StubLLM) -> None:
    """스키마의 `source`는 선택이지만 빈 문자열은 허용하지 않는다.

    근거를 대지 못한 검증에 `""`를 남기면 "근거가 없음"과 "근거 칸이 있음"이 구분되지 않고,
    필수로 만들면 모델이 출처를 지어내는 쪽으로 압력을 받는다 (퀴즈 스펙 3.1).
    """
    data = content(count=1)
    blank = {"answers": [{"id": 1, "answer": "나일강", "certainty": 0.9, "basis": "   "}]}
    stub_llm.reply(blank, blank, verdicts((1, True)))

    verify(data, config=config_with())

    assert data["questions"][0]["verify"]["status"] == VERIFIED
    assert "source" not in data["questions"][0]["verify"]
    validate_quiz(data)


def test_source_is_capped_so_the_artifact_stays_readable(stub_llm: StubLLM) -> None:
    data = content(count=1)
    long = {"answers": [{"id": 1, "answer": "나일강", "certainty": 0.9, "basis": "가" * 500}]}
    stub_llm.reply(long, long, verdicts((1, True)))

    verify(data, config=config_with())

    assert len(data["questions"][0]["verify"]["source"]) == SOURCE_MAX_LEN
