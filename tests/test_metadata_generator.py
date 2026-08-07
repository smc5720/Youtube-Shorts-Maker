"""메타데이터 생성기 — 이슈 #13의 완료 조건에 대응한다.

**실제 LLM을 부르지 않는다.** `stub_llm` 픽스처(`conftest.py`)가 provider 레지스트리를
가짜로 바꾼다. 기본 응답은 생성기가 넘긴 JSON Schema에서 만들어지므로, 스키마가 실제로
쓸 수 있는 모양인지도 함께 검증된다.

경계는 `tests/test_type_boundary.py`가 강제한다 — 여기서는 그 경계를 지킨 재료
(`scenes.json`의 `role`·`text`·`caption`)만으로 산출물이 나오는지를 본다.
"""

from __future__ import annotations

from typing import Any

import pytest

from shorts_maker.config import Config, defaults, load_config
from shorts_maker.llm import LLMError
from shorts_maker.metadata_generator import LANGUAGE, SYSTEM, generate
from shorts_maker.schemas import SchemaError, validate_metadata
from shorts_maker.schemas.metadata import TITLE_COUNT
from shorts_maker.schemas.scenes import segment_path

from conftest import StubLLM

HOOK = "이 상식 4개, 다 맞히면 상위 1%"
QUESTION = "세계에서 가장 긴 강은?"
ANSWER = "나일강"
EXPLANATION = "약 6,650km로 세계 최장"
CTA = "몇 개 맞혔나요?"


def config_with(**overrides: Any) -> Config:
    return load_config(overrides=overrides) if overrides else Config(data=defaults())


def draft_scenes(**overrides: Any) -> dict[str, Any]:
    """장면 템플릿(#12)이 만드는 초안 — 낭독 장면의 `duration`과 오디오 필드가 없다."""
    data = {
        "schema_version": 1,
        "type": "quiz",
        "scenes": [
            {"role": "hook", "text": HOOK, "duration": 3.0},
            {
                "role": "question",
                "question_id": 1,
                "text": QUESTION,
                "narrate": True,
                "target_duration": 3.0,
            },
            {"role": "countdown", "question_id": 1, "seconds": 4, "duration": 4.0},
            {
                "role": "answer",
                "question_id": 1,
                "text": ANSWER,
                "caption": EXPLANATION,
                "narrate": True,
                "target_duration": 3.0,
            },
            {"role": "cta", "text": CTA, "duration": 4.0},
        ],
    }
    data.update(overrides)
    return data


def model_output(**overrides: Any) -> dict[str, Any]:
    payload = {
        "titles": ["제목 후보 하나", "제목 후보 둘", "제목 후보 셋"],
        "description": "세계 지리 상식 퀴즈입니다. 정답을 맞혀 보세요.",
        "tags": ["상식퀴즈", "지리"],
    }
    payload.update(overrides)
    return payload


# --- 산출물 ----------------------------------------------------------------


def test_generated_metadata_passes_the_schema(stub_llm: StubLLM) -> None:
    content = generate(draft_scenes(), config=config_with())

    validate_metadata(content)  # 위반이 있으면 SchemaError로 실패한다
    assert len(content["titles"]) == TITLE_COUNT


def test_source_is_null_not_missing_on_the_topic_path(stub_llm: StubLLM) -> None:
    """`--topic`에는 출처가 없다. 필드를 빼면 "생성기가 빠뜨림"과 구분되지 않는다 (#31)."""
    content = generate(draft_scenes(), config=config_with())

    assert "source" in content
    assert content["source"] is None


def test_type_comes_from_the_scenes_and_language_is_fixed_by_code(
    stub_llm: StubLLM,
) -> None:
    """타입 이름은 장면이 들고 있는 값을 옮긴 것이다 — 이 모듈이 아는 이름이 아니다."""
    content = generate(draft_scenes(), config=config_with())

    assert content["type"] == "quiz"
    assert content["language"] == LANGUAGE


def test_a_draft_is_enough(stub_llm: StubLLM) -> None:
    """메타데이터는 오디오 길이와 무관하고, 렌더가 실패해도 남아야 한다 (PRD 6.2 표)."""
    draft = draft_scenes()
    assert "duration" not in draft["scenes"][1]  # TTS(#16)가 채우기 전 상태

    validate_metadata(generate(draft, config=config_with()))


def test_a_finalized_scene_file_is_also_accepted(stub_llm: StubLLM) -> None:
    finalized = draft_scenes()
    for index in (1, 3):
        finalized["scenes"][index].update(
            duration=2.94,
            audio=segment_path(index),
            audio_duration=2.14,
            narration_offset=3.3,
        )

    validate_metadata(generate(finalized, config=config_with()))


# --- 호출 ------------------------------------------------------------------


def test_one_llm_call_covers_titles_description_and_tags(stub_llm: StubLLM) -> None:
    """셋은 같은 재료에서 나오고 서로 톤이 맞아야 한다. 나눠 부르면 기동 비용만 늘어난다."""
    generate(draft_scenes(), config=config_with())

    assert stub_llm.call_count == 1
    assert stub_llm.calls[0]["system"] == SYSTEM


def test_the_prompt_carries_the_scene_text_and_captions(stub_llm: StubLLM) -> None:
    """제목의 재료는 `role`·`text`·`caption`뿐이다 (퀴즈 스펙 1.1)."""
    generate(draft_scenes(), config=config_with())

    prompt = stub_llm.calls[0]["prompt"]
    for material in (HOOK, QUESTION, ANSWER, EXPLANATION, CTA):
        assert material in prompt


def test_scenes_without_text_are_left_out_of_the_prompt(stub_llm: StubLLM) -> None:
    """`countdown`에는 문구가 없다. 빈 줄을 보내도 모델이 쓸 재료가 늘지 않는다."""
    generate(draft_scenes(), config=config_with())

    assert "[countdown]" not in stub_llm.calls[0]["prompt"]


def test_the_config_limits_reach_both_the_prompt_and_the_schema(
    stub_llm: StubLLM,
) -> None:
    generate(
        draft_scenes(),
        config=config_with(**{"metadata.title_max_len": 25, "metadata.tag_max_count": 6}),
    )

    call = stub_llm.calls[0]
    assert "25자" in call["prompt"]
    assert "6개" in call["prompt"]
    assert call["schema"]["properties"]["titles"]["items"]["maxLength"] == 25
    assert call["schema"]["properties"]["tags"]["maxItems"] == 6


def test_broken_input_stops_before_the_call(stub_llm: StubLLM) -> None:
    """깨진 장면 목록으로 LLM을 부르면 그 비용이 버려진다."""
    broken = draft_scenes()
    broken["scenes"][0]["role"] = "outro"

    with pytest.raises(SchemaError):
        generate(broken, config=config_with())

    assert stub_llm.call_count == 0


# --- 상한 ------------------------------------------------------------------


def test_a_title_over_the_limit_fails_with_the_offending_value(
    stub_llm: StubLLM,
) -> None:
    long_title = "가" * 41
    stub_llm.reply(model_output(titles=["짧은 제목", long_title, "또 다른 제목"]))

    with pytest.raises(LLMError) as error_info:
        generate(draft_scenes(), config=config_with())

    message = str(error_info.value)
    assert "metadata.title_max_len" in message
    assert "titles[1]" in message
    assert "41자" in message


def test_too_many_tags_fail_with_the_count(stub_llm: StubLLM) -> None:
    stub_llm.reply(model_output(tags=[f"태그{index}" for index in range(11)]))

    with pytest.raises(LLMError) as error_info:
        generate(draft_scenes(), config=config_with())

    message = str(error_info.value)
    assert "metadata.tag_max_count" in message
    assert "11개" in message


def test_a_raised_limit_accepts_the_same_output(stub_llm: StubLLM) -> None:
    """상한은 config가 정한다 — 넘겼다는 것이 곧 결과가 나쁘다는 뜻은 아니다."""
    stub_llm.reply(model_output(titles=["가" * 41, "제목 둘", "제목 셋"]))

    content = generate(
        draft_scenes(), config=config_with(**{"metadata.title_max_len": 50})
    )

    assert len(content["titles"][0]) == 41


def test_the_wrong_number_of_titles_is_rejected_by_the_schema(stub_llm: StubLLM) -> None:
    """모델이 스키마를 어겨도 산출물 검증이 잡는다 — 파일로 나가지 않는다."""
    stub_llm.reply(model_output(titles=["하나뿐인 제목"]))

    with pytest.raises(SchemaError) as error_info:
        generate(draft_scenes(), config=config_with())

    assert any("titles" in message for message in error_info.value.messages)
