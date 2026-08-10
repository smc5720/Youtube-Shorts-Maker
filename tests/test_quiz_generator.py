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
from shorts_maker.schemas.quiz import VERIFY_STATUSES, content_json_schema
from shorts_maker.types.quiz.quiz_generator import (
    CAPPED_FIELDS,
    CATEGORY,
    LANGUAGE,
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    SYSTEM,
    check_config,
    generate,
    length_caps,
)

from conftest import StubLLM, from_schema

DEFAULT_VERIFY_CALLS = 3
"""기본 설정에서 검증이 쓰는 호출 수 — 재답변 `llm.verifier.runs`(2) + 모호성 프로브 1 (#10).

생성 호출 수를 세는 테스트가 검증 호출까지 함께 세게 된다. 이 값이 문제 수와 무관하다는
것이 아래 `test_call_count_does_not_scale_with_question_count`가 확인하는 성질이다.
"""

DEFAULT_CAPS = length_caps(Config(data=defaults()))
"""기본 설정의 글자 수 상한. 값을 여기 적지 않는 이유는 `config.SPEC`이 정하기 때문이다."""


def config_with(**overrides: Any) -> Config:
    return load_config(overrides=overrides) if overrides else Config(data=defaults())


def model_output(count: int = 4, **overrides: Any) -> dict[str, Any]:
    """모델이 낼 법한 응답. 생성기가 실제로 넘기는 스키마에서 만든다."""
    payload = from_schema(content_json_schema(question_count=count, caps=DEFAULT_CAPS))
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


def test_every_question_carries_a_verify_field(stub_llm: StubLLM) -> None:
    """검증(#10)은 `generate()` 안의 한 단계다 — 산출물에 `verify`가 이미 채워져 나온다.

    초안이 `verify` 없이 스키마를 통과하는 성질은 여전히 필요하고(검증 전 검증),
    `tests/test_schemas.py`가 그것을 고정한다.
    """
    content = generate(topic="주제", config=config_with())

    assert all(item["verify"]["status"] in VERIFY_STATUSES for item in content["questions"])


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


@pytest.mark.parametrize("runs", [0, -1])
def test_verifier_runs_below_one_is_a_config_error(runs: int) -> None:
    """재답변이 한 번도 없으면 검증 단계가 이름만 남는다 (#10).

    `llm` 아래 키이지만 하한을 아는 것은 사실 검증을 필수로 두는 퀴즈 타입이다.
    """
    with pytest.raises(ConfigError) as error:
        check_config(config_with(**{"llm.verifier.runs": runs}))

    assert "llm.verifier.runs" in str(error.value)


def test_config_check_reports_every_violation_at_once() -> None:
    """설정 오류는 모아서 던진다 — 하나 고치고 다시 돌려서 다음 오류를 보는 왕복을 줄인다."""
    with pytest.raises(ConfigError) as error:
        check_config(config_with(**{"quiz.question_count": 9, "llm.verifier.runs": 0}))

    assert len(error.value.messages) == 2


def test_out_of_range_count_stops_before_calling_the_model(stub_llm: StubLLM) -> None:
    """범위를 벗어난 값으로 모델을 부르면 그 비용이 그대로 버려진다."""
    with pytest.raises(ConfigError):
        generate(topic="주제", config=config_with(**{"quiz.question_count": 9}))

    assert stub_llm.call_count == 0


@pytest.mark.parametrize("count", [MIN_QUESTIONS, 4, MAX_QUESTIONS])
def test_call_count_does_not_scale_with_question_count(stub_llm: StubLLM, count: int) -> None:
    """CLI 기동 오버헤드가 호출당 약 6.5초다 (스파이크 3장). 문제별 호출은 이를 곱한다.

    생성 1회 + 검증 `DEFAULT_VERIFY_CALLS`회로 끝난다. 문제 수를 3에서 5로 올려도
    호출 수가 그대로여야 한다 — 생성기와 검증기가 둘 다 문제를 묶어 부르기 때문이다.
    """
    generate(topic="주제", config=config_with(**{"quiz.question_count": count}))

    assert stub_llm.call_count == 1 + DEFAULT_VERIFY_CALLS
    assert stub_llm.calls[0]["prompt"].startswith("주제:")  # 첫 호출이 생성이다


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
        question_count=4, caps=DEFAULT_CAPS
    )


def test_schema_asks_only_for_what_the_model_decides() -> None:
    schema = content_json_schema(question_count=4, caps=DEFAULT_CAPS)

    assert set(schema["properties"]) == {"hook", "cta", "questions"}
    assert set(schema["properties"]["questions"]["items"]["properties"]) == {
        "question",
        "answer",
        "explanation",
        "difficulty",
    }


def test_schema_pins_the_question_count() -> None:
    schema = content_json_schema(question_count=5, caps=DEFAULT_CAPS)

    questions = schema["properties"]["questions"]
    assert questions["minItems"] == 5
    assert questions["maxItems"] == 5


def test_schema_carries_the_configured_length_caps() -> None:
    """다섯 필드 전부 config 값이 `maxLength`로 파생돼 들어간다 (#56).

    프롬프트 쪽에 숫자를 손으로 적으면 계약이 두 곳에 생긴다 (PRD 14.1).
    """
    caps = {"hook": 30, "cta": 21, "question": 40, "answer": 15, "explanation": 45}

    schema = content_json_schema(question_count=4, caps=caps)

    root = schema["properties"]
    fields = root["questions"]["items"]["properties"]
    assert root["hook"]["maxLength"] == 30
    assert root["cta"]["maxLength"] == 21
    assert fields["question"]["maxLength"] == 40
    assert fields["answer"]["maxLength"] == 15
    assert fields["explanation"]["maxLength"] == 45


def test_every_capped_field_finds_a_place_in_the_schema() -> None:
    """`CAPPED_FIELDS`와 스키마가 갈리면 상한이 조용히 사라진다 — 그때 오류가 나야 한다.

    `id`는 `quiz.json`에는 있지만 모델에게 묻지 않는 필드다. 얹을 자리가 없는 이름의 예다.
    """
    content_json_schema(question_count=4, caps=DEFAULT_CAPS)  # 위반이면 ValueError

    with pytest.raises(ValueError, match="상한을 얹을 수 없다: id"):
        content_json_schema(question_count=4, caps={"id": 5})


def test_schema_keeps_the_difficulty_vocabulary_of_the_artifact_schema() -> None:
    schema = content_json_schema(question_count=4, caps=DEFAULT_CAPS)

    fields = schema["properties"]["questions"]["items"]["properties"]
    assert fields["difficulty"]["enum"] == list(DIFFICULTIES)


def test_schema_rejects_fields_the_model_invents() -> None:
    schema = content_json_schema(question_count=4, caps=DEFAULT_CAPS)

    assert schema["additionalProperties"] is False
    assert schema["properties"]["questions"]["items"]["additionalProperties"] is False


# --- 프롬프트 ---------------------------------------------------------------


def test_prompt_carries_the_topic_and_the_requested_count(stub_llm: StubLLM) -> None:
    generate(topic="한국사 상식", config=config_with(**{"quiz.question_count": 5}))

    prompt = stub_llm.calls[0]["prompt"]
    assert "한국사 상식" in prompt
    assert "5문제" in prompt


def test_prompt_carries_every_length_cap(stub_llm: StubLLM) -> None:
    """스키마의 `maxLength`만으로는 모델이 상한 직전에서 문장을 끊는다 (#56).

    숫자는 config에서 온다 — 프롬프트에 상수로 적혀 있으면 설정을 바꿔도 따라오지 않는다.
    """
    caps = {"hook": 30, "cta": 21, "question": 40, "answer": 15, "explanation": 45}
    config = config_with(**{f"quiz.{field}_max_len": limit for field, limit in caps.items()})

    generate(topic="주제", config=config)

    prompt = stub_llm.calls[0]["prompt"]
    assert all(f"{limit}자" in prompt for limit in caps.values())


def test_prompt_asks_for_ascending_difficulty_and_variety(stub_llm: StubLLM) -> None:
    """스파이크 4.1의 후속 과제 — 회차 간 반복이 프롬프트로 완화되는지 확인한다."""
    generate(topic="주제", config=config_with())

    prompt = stub_llm.calls[0]["prompt"]
    assert "오름차순" in prompt
    assert "다른 문제가 나와야" in prompt


def test_system_prompt_replaces_the_default_one(stub_llm: StubLLM) -> None:
    generate(topic="주제", config=config_with())

    assert stub_llm.calls[0]["system"] == SYSTEM


def test_each_role_uses_its_own_model(stub_llm: StubLLM) -> None:
    """생성과 검증이 같은 모델이면 프롬프트만 탈상관되고 지식은 공유된다 (스파이크 4.2)."""
    generate(
        topic="주제",
        config=config_with(**{"llm.generator.model": "haiku", "llm.verifier.model": "opus"}),
    )

    assert stub_llm.calls[0]["model"] == "haiku"
    assert {call["model"] for call in stub_llm.calls[1:]} == {"opus"}


# --- 실패 경로 --------------------------------------------------------------


def test_schema_failure_is_retried_up_to_max_retries(stub_llm: StubLLM) -> None:
    stub_llm.reply(LLMError("structured_output이 없다"), model_output())

    content = generate(topic="주제", config=config_with(**{"llm.max_retries": 2}))

    assert stub_llm.call_count == 2 + DEFAULT_VERIFY_CALLS
    assert len(content["questions"]) == 4


def test_final_failure_reports_the_attempts_and_the_cause(stub_llm: StubLLM) -> None:
    stub_llm.reply(*[LLMError("structured_output이 없다")] * 3)

    with pytest.raises(LLMError) as error:
        generate(topic="주제", config=config_with(**{"llm.max_retries": 2}))

    assert stub_llm.call_count == 3
    assert "3회" in str(error.value)
    assert "structured_output" in str(error.value)


def overrun_payload(field: str) -> dict[str, Any]:
    """`field`만 기본 상한을 1자 넘긴 응답. 넘기는 자리는 필드가 사는 층을 따른다."""
    payload = model_output(count=3)
    payload["questions"] = [question() for _ in range(3)]
    value = "가" * (DEFAULT_CAPS[field] + 1)

    if field in payload:
        payload[field] = value
    else:
        payload["questions"][0][field] = value
    return payload


@pytest.mark.parametrize("field", CAPPED_FIELDS)
def test_length_overrun_stops_instead_of_regenerating(stub_llm: StubLLM, field: str) -> None:
    """상한은 스키마로도 넘어간다. 그래도 넘긴 값은 주제·상한이 안 맞는다는 신호에 가깝고,
    다시 불러도 같은 이유로 같은 결과가 나온다 (#9, #56)."""
    stub_llm.reply(overrun_payload(field))

    with pytest.raises(LLMError) as error:
        generate(topic="주제", config=config_with(**{"quiz.question_count": 3}))

    assert stub_llm.call_count == 1  # 재생성하지 않는다
    assert f"quiz.{field}_max_len" in str(error.value)


def test_length_overrun_names_the_field_and_the_actual_count(stub_llm: StubLLM) -> None:
    """어느 필드가 몇 자인지 밝힌다 — 상한을 올릴지 주제를 좁힐지는 사람이 정한다 (#56)."""
    stub_llm.reply(overrun_payload("question"))

    with pytest.raises(LLMError) as error:
        generate(topic="주제", config=config_with(**{"quiz.question_count": 3}))

    message = str(error.value)
    assert "questions[0].question" in message
    assert f"{DEFAULT_CAPS['question'] + 1}자" in message


def test_every_overrun_is_reported_at_once(stub_llm: StubLLM) -> None:
    """하나 고치고 다시 돌려서 다음 위반을 보는 왕복을 줄인다 — 설정 오류와 같은 판단이다."""
    payload = overrun_payload("hook")
    payload["questions"][1]["answer"] = "가" * (DEFAULT_CAPS["answer"] + 1)
    stub_llm.reply(payload)

    with pytest.raises(LLMError) as error:
        generate(topic="주제", config=config_with(**{"quiz.question_count": 3}))

    message = str(error.value)
    assert "hook:" in message
    assert "questions[1].answer" in message


@pytest.mark.parametrize(("cap", "accepted"), [(12, True), (11, False)])
def test_length_caps_are_read_from_config(stub_llm: StubLLM, cap: int, accepted: bool) -> None:
    """상한은 코드에 박힌 값이 아니라 config에서 온다 — 같은 응답의 판정이 상한으로 갈린다.

    두 경우를 한 테스트에서 연달아 부르지 않는다. `stub_llm.reply()`가 넣은 응답은 생성과
    검증이 함께 소비하는 하나의 큐라, 생성 2회분을 미리 넣으면 두 번째 응답을 검증기가
    먼저 가져간다.
    """
    payload = model_output(count=3)
    payload["questions"] = [question(answer="가" * 12) for _ in range(3)]
    stub_llm.reply(copy.deepcopy(payload))
    config = config_with(**{"quiz.question_count": 3, "quiz.answer_max_len": cap})

    if accepted:
        generate(topic="주제", config=config)
        return

    with pytest.raises(LLMError, match="quiz.answer_max_len"):
        generate(topic="주제", config=config)


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
