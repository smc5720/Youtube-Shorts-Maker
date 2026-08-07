"""퀴즈 콘텐츠 생성기 — 이슈 #9의 완료 조건에 대응한다.

**실제 LLM을 부르지 않는다.** `stub_llm` 픽스처(`conftest.py`)가 provider 레지스트리를
가짜로 바꾼다. 기본 응답은 생성기가 넘긴 JSON Schema에서 만들어지므로, 스키마가 실제로
쓸 수 있는 모양인지도 함께 검증된다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from shorts_maker.config import Config, ConfigError, defaults, load_config
from shorts_maker.llm import LLMError
from shorts_maker.schemas import DIFFICULTIES, SchemaError, validate_quiz
from shorts_maker.schemas.quiz import content_json_schema
from shorts_maker.types.quiz.quiz_generator import (
    CATEGORY,
    LANGUAGE,
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    SYSTEM,
    check_config,
    generate,
)

from conftest import StubLLM, from_schema


def config_with(**overrides: Any) -> Config:
    return load_config(overrides=overrides) if overrides else Config(data=defaults())


def model_output(count: int = 4, **overrides: Any) -> dict[str, Any]:
    """모델이 낼 법한 응답. 생성기가 실제로 넘기는 스키마에서 만든다."""
    payload = from_schema(
        content_json_schema(question_count=count, answer_max_len=20, explanation_max_len=60)
    )
    payload.update(overrides)
    return payload


def question(**overrides: Any) -> dict[str, Any]:
    base = {
        "question": "세계에서 가장 긴 강은?",
        "answer": "나일강",
        "explanation": "약 6,650km로 세계에서 가장 깁니다.",
        "difficulty": "easy",
    }
    base.update(overrides)
    return base


# --- 산출물 ----------------------------------------------------------------


def test_generated_content_passes_the_quiz_schema(stub_llm: StubLLM) -> None:
    content = generate(topic="세계 지리 상식", config=config_with())

    validate_quiz(content)  # 위반이 있으면 SchemaError로 실패한다


def test_draft_has_no_verify_field_because_the_verifier_fills_it(stub_llm: StubLLM) -> None:
    """#10이 채우기 전의 초안은 `verify` 없이 검증을 통과한다 (퀴즈 스펙 3.1)."""
    content = generate(topic="주제", config=config_with())

    assert all("verify" not in item for item in content["questions"])


def test_category_and_language_are_fixed_by_code(stub_llm: StubLLM) -> None:
    """서브 장르와 언어는 결정된 값이다 (퀴즈 스펙 0장, PRD 14.1) — 모델에게 묻지 않는다."""
    content = generate(topic="주제", config=config_with())

    assert content["category"] == CATEGORY
    assert content["language"] == LANGUAGE
    assert content["type"] == "quiz"


def test_hook_and_cta_are_not_empty(stub_llm: StubLLM) -> None:
    content = generate(topic="주제", config=config_with())

    assert content["hook"].strip()
    assert content["cta"].strip()


# --- 문제 수 ---------------------------------------------------------------


def test_default_run_produces_four_questions(stub_llm: StubLLM) -> None:
    content = generate(topic="주제", config=config_with())

    assert len(content["questions"]) == 4


@pytest.mark.parametrize("count", [MIN_QUESTIONS, 4, MAX_QUESTIONS])
def test_question_count_config_decides_how_many(stub_llm: StubLLM, count: int) -> None:
    content = generate(topic="주제", config=config_with(**{"quiz.question_count": count}))

    assert len(content["questions"]) == count


@pytest.mark.parametrize("count", [0, 1, MIN_QUESTIONS - 1, MAX_QUESTIONS + 1, 99])
def test_question_count_outside_the_allowed_range_is_a_config_error(count: int) -> None:
    with pytest.raises(ConfigError) as error:
        check_config(config_with(**{"quiz.question_count": count}))

    assert "quiz.question_count" in str(error.value)
    assert f"{MIN_QUESTIONS}~{MAX_QUESTIONS}" in str(error.value)


def test_out_of_range_count_stops_before_calling_the_model(stub_llm: StubLLM) -> None:
    """범위를 벗어난 값으로 모델을 부르면 그 비용이 그대로 버려진다."""
    with pytest.raises(ConfigError):
        generate(topic="주제", config=config_with(**{"quiz.question_count": 9}))

    assert stub_llm.call_count == 0


@pytest.mark.parametrize("count", [MIN_QUESTIONS, 4, MAX_QUESTIONS])
def test_one_llm_call_regardless_of_question_count(stub_llm: StubLLM, count: int) -> None:
    """CLI 기동 오버헤드가 호출당 약 6.5초다 (스파이크 3장). 문제별 호출은 이를 곱한다."""
    generate(topic="주제", config=config_with(**{"quiz.question_count": count}))

    assert stub_llm.call_count == 1


# --- 배치와 식별자 ---------------------------------------------------------


def test_questions_are_sorted_easy_to_hard_even_if_the_model_is_not(
    stub_llm: StubLLM,
) -> None:
    """스키마가 순서를 강제하지 않으므로 받은 뒤 다시 정렬한다 (퀴즈 스펙 2장)."""
    stub_llm.reply(
        model_output(
            questions=[
                question(difficulty="hard"),
                question(difficulty="easy"),
                question(difficulty="hard"),
                question(difficulty="medium"),
            ]
        )
    )

    content = generate(topic="주제", config=config_with())

    assert [item["difficulty"] for item in content["questions"]] == [
        "easy",
        "medium",
        "hard",
        "hard",
    ]


def test_ids_are_numbered_from_one_after_sorting(stub_llm: StubLLM) -> None:
    stub_llm.reply(
        model_output(
            questions=[
                question(difficulty="hard", answer="마지막"),
                question(difficulty="easy", answer="처음"),
                question(difficulty="medium", answer="가운데"),
            ]
        )
    )

    content = generate(topic="주제", config=config_with(**{"quiz.question_count": 3}))

    assert [item["id"] for item in content["questions"]] == [1, 2, 3]
    assert [item["answer"] for item in content["questions"]] == ["처음", "가운데", "마지막"]


def test_same_difficulty_keeps_the_order_the_model_chose(stub_llm: StubLLM) -> None:
    """안정 정렬이다 — 난이도가 같은 문제의 순서를 뒤집을 근거가 없다."""
    stub_llm.reply(
        model_output(
            questions=[
                question(difficulty="easy", answer="첫째"),
                question(difficulty="easy", answer="둘째"),
                question(difficulty="hard", answer="셋째"),
            ]
        )
    )

    content = generate(topic="주제", config=config_with(**{"quiz.question_count": 3}))

    assert [item["answer"] for item in content["questions"]] == ["첫째", "둘째", "셋째"]


def test_countdown_sec_comes_from_config_for_every_question(stub_llm: StubLLM) -> None:
    content = generate(topic="주제", config=config_with(**{"quiz.countdown_sec": 6}))

    assert {item["countdown_sec"] for item in content["questions"]} == {6}


# --- LLM에 넘기는 스키마 ----------------------------------------------------


def sent_schema(stub_llm: StubLLM) -> dict[str, Any]:
    return stub_llm.calls[0]["schema"]


def test_schema_is_derived_from_the_artifact_schema(stub_llm: StubLLM) -> None:
    """생성기가 넘긴 스키마가 `schemas/quiz.py`의 파생 결과와 같다.

    프롬프트 쪽에 필드 이름을 손으로 다시 적으면 계약이 두 곳에 생긴다 (PRD 14.1).
    """
    generate(topic="주제", config=config_with())

    assert sent_schema(stub_llm) == content_json_schema(
        question_count=4, answer_max_len=20, explanation_max_len=60
    )


def test_schema_asks_only_for_what_the_model_decides() -> None:
    schema = content_json_schema(question_count=4, answer_max_len=20, explanation_max_len=60)

    assert set(schema["properties"]) == {"hook", "cta", "questions"}
    assert set(schema["properties"]["questions"]["items"]["properties"]) == {
        "question",
        "answer",
        "explanation",
        "difficulty",
    }


def test_schema_pins_the_question_count() -> None:
    schema = content_json_schema(question_count=5, answer_max_len=20, explanation_max_len=60)

    questions = schema["properties"]["questions"]
    assert questions["minItems"] == 5
    assert questions["maxItems"] == 5


def test_schema_carries_the_configured_length_caps() -> None:
    schema = content_json_schema(question_count=4, answer_max_len=15, explanation_max_len=45)

    fields = schema["properties"]["questions"]["items"]["properties"]
    assert fields["answer"]["maxLength"] == 15
    assert fields["explanation"]["maxLength"] == 45


def test_schema_keeps_the_difficulty_vocabulary_of_the_artifact_schema() -> None:
    schema = content_json_schema(question_count=4, answer_max_len=20, explanation_max_len=60)

    fields = schema["properties"]["questions"]["items"]["properties"]
    assert fields["difficulty"]["enum"] == list(DIFFICULTIES)


def test_schema_rejects_fields_the_model_invents() -> None:
    schema = content_json_schema(question_count=4, answer_max_len=20, explanation_max_len=60)

    assert schema["additionalProperties"] is False
    assert schema["properties"]["questions"]["items"]["additionalProperties"] is False


# --- 프롬프트 ---------------------------------------------------------------


def test_prompt_carries_the_topic_and_the_requested_count(stub_llm: StubLLM) -> None:
    generate(topic="한국사 상식", config=config_with(**{"quiz.question_count": 5}))

    prompt = stub_llm.calls[0]["prompt"]
    assert "한국사 상식" in prompt
    assert "5문제" in prompt


def test_prompt_asks_for_ascending_difficulty_and_variety(stub_llm: StubLLM) -> None:
    """스파이크 4.1의 후속 과제 — 회차 간 반복이 프롬프트로 완화되는지 확인한다."""
    generate(topic="주제", config=config_with())

    prompt = stub_llm.calls[0]["prompt"]
    assert "오름차순" in prompt
    assert "다른 문제가 나와야" in prompt


def test_system_prompt_replaces_the_default_one(stub_llm: StubLLM) -> None:
    generate(topic="주제", config=config_with())

    assert stub_llm.calls[0]["system"] == SYSTEM


def test_generator_role_decides_the_model(stub_llm: StubLLM) -> None:
    """검증(#10)과 다른 모델을 쓸 수 있어야 한다 (스파이크 4.2)."""
    generate(
        topic="주제",
        config=config_with(**{"llm.generator.model": "haiku", "llm.verifier.model": "opus"}),
    )

    assert stub_llm.calls[0]["model"] == "haiku"


# --- 실패 경로 --------------------------------------------------------------


def test_schema_failure_is_retried_up_to_max_retries(stub_llm: StubLLM) -> None:
    stub_llm.reply(LLMError("structured_output이 없다"), model_output())

    content = generate(topic="주제", config=config_with(**{"llm.max_retries": 2}))

    assert stub_llm.call_count == 2
    assert len(content["questions"]) == 4


def test_final_failure_reports_the_attempts_and_the_cause(stub_llm: StubLLM) -> None:
    stub_llm.reply(*[LLMError("structured_output이 없다")] * 3)

    with pytest.raises(LLMError) as error:
        generate(topic="주제", config=config_with(**{"llm.max_retries": 2}))

    assert stub_llm.call_count == 3
    assert "3회" in str(error.value)
    assert "structured_output" in str(error.value)


@pytest.mark.parametrize(
    ("field", "setting", "value"),
    [
        ("answer", "quiz.answer_max_len", "가" * 21),
        ("explanation", "quiz.explanation_max_len", "가" * 61),
    ],
)
def test_length_overrun_stops_instead_of_regenerating(
    stub_llm: StubLLM, field: str, setting: str, value: str
) -> None:
    """상한은 스키마로도 넘어간다. 그래도 넘긴 값은 주제·상한이 안 맞는다는 신호에 가깝고,
    다시 불러도 같은 이유로 같은 결과가 나온다."""
    payload = model_output(count=3)
    payload["questions"] = [question(**{field: value}) for _ in range(3)]
    stub_llm.reply(payload)

    with pytest.raises(LLMError) as error:
        generate(topic="주제", config=config_with(**{"quiz.question_count": 3}))

    assert stub_llm.call_count == 1  # 재생성하지 않는다
    assert setting in str(error.value)


def test_length_caps_are_read_from_config(stub_llm: StubLLM) -> None:
    payload = model_output(count=3)
    payload["questions"] = [question(answer="가" * 12) for _ in range(3)]
    stub_llm.reply(copy.deepcopy(payload), copy.deepcopy(payload))

    generate(
        topic="주제",
        config=config_with(**{"quiz.question_count": 3, "quiz.answer_max_len": 12}),
    )

    with pytest.raises(LLMError, match="quiz.answer_max_len"):
        generate(
            topic="주제",
            config=config_with(**{"quiz.question_count": 3, "quiz.answer_max_len": 11}),
        )


def test_output_that_breaks_the_artifact_schema_is_reported_with_the_field_path(
    stub_llm: StubLLM,
) -> None:
    """스키마 강제를 우회한 응답도 산출물 검증에서 멈춘다 — 반쪽짜리 dict를 쓰지 않는다."""
    payload = model_output(count=3)
    payload["questions"] = [question(), question(), question(explanation="   ")]
    stub_llm.reply(payload)

    with pytest.raises(SchemaError) as error:
        generate(topic="주제", config=config_with(**{"quiz.question_count": 3}))

    assert "questions[2].explanation" in str(error.value)
