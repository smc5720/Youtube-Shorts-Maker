"""산출물 스키마 검증 — 이슈 #7의 완료 조건에 대응한다.

문서의 예시 JSON은 **문서 파일에서 직접 읽어** 검증한다. 예시를 여기 복사해 두면 문서만
고쳐졌을 때 테스트가 통과해 버리고, 이 스키마가 계약인 근거가 사라진다.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from shorts_maker.schemas import (
    SchemaError,
    load_metadata,
    load_project,
    load_quiz,
    load_scenes,
    segment_path,
    validate_metadata,
    validate_project,
    validate_quiz,
    validate_scenes,
    validate_scenes_final,
)
from shorts_maker.schemas import metadata as metadata_schema
from shorts_maker.schemas.core import (
    Array,
    Choices,
    Object,
    Scalar,
    integer,
    section,
    text,
)
from shorts_maker.schemas.quiz import QUIZ_SCHEMA, content_json_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
QUIZ_SPEC = REPO_ROOT / "docs" / "types" / "quiz.md"
PRD = REPO_ROOT / "docs" / "PRD.md"

Validator = Callable[..., None]


# --- 헬퍼 ------------------------------------------------------------------


def doc_example(document: Path, heading: str) -> dict[str, Any]:
    """문서의 한 절에서 첫 JSON 코드블록을 읽는다. 없으면 테스트를 실패시킨다."""
    body = document.read_text(encoding="utf-8")
    start = body.find(heading)
    assert start != -1, f"{document.name}에 `{heading}` 절이 없다"

    rest = body[start + len(heading) :]
    next_heading = re.search(r"^#{1,4} ", rest, flags=re.MULTILINE)
    section = rest[: next_heading.start()] if next_heading else rest

    block = re.search(r"```json\n(.*?)```", section, flags=re.DOTALL)
    assert block is not None, f"{document.name}의 `{heading}` 절에 JSON 예시가 없다"
    return json.loads(block.group(1))


def failures(validate: Validator, data: Any) -> list[str]:
    """검증이 실패하는 것을 확인하고 위반 메시지 목록을 돌려준다."""
    with pytest.raises(SchemaError) as error_info:
        validate(data)
    return error_info.value.messages


def assert_reports(messages: list[str], path: str) -> str:
    """`path` 필드를 지적하는 메시지가 있는지 확인하고 그 메시지를 돌려준다."""
    for message in messages:
        if message.startswith(f"{path}:"):
            return message
    raise AssertionError(f"{path}를 지적하는 메시지가 없다. 받은 메시지: {messages}")


def quiz_question(**overrides: Any) -> dict[str, Any]:
    question = {
        "id": 1,
        "question": "세계에서 가장 긴 강은?",
        "answer": "나일강",
        "explanation": "약 6,650km로 세계에서 가장 긴 강입니다.",
        "difficulty": "easy",
        "countdown_sec": 4,
        "verify": {"status": "verified", "confidence": 0.95, "source": "근거 요약"},
    }
    question.update(overrides)
    return question


def quiz(**overrides: Any) -> dict[str, Any]:
    data = {
        "schema_version": 1,
        "type": "quiz",
        "category": "general_knowledge",
        "language": "ko",
        "hook": "이 상식 4개, 다 맞히면 상위 1%",
        "cta": "몇 개 맞혔나요?",
        "questions": [quiz_question()],
    }
    data.update(overrides)
    return data


def draft_scenes(**overrides: Any) -> dict[str, Any]:
    """장면 템플릿(#12)이 만드는 초안 — 오디오 필드와 낭독 장면 duration이 없다."""
    data = {
        "schema_version": 1,
        "type": "quiz",
        "scenes": [
            {"role": "hook", "text": "이 상식 4개, 다 맞히면 상위 1%", "duration": 3.0},
            {
                "role": "question",
                "question_id": 1,
                "text": "세계에서 가장 긴 강은?",
                "narrate": True,
                "target_duration": 3.0,
            },
            {
                "role": "countdown",
                "question_id": 1,
                "seconds": 4,
                "duration": 4.0,
                "sfx": "beep",
            },
            {
                "role": "answer",
                "question_id": 1,
                "text": "나일강",
                "caption": "약 6,650km로 세계 최장",
                "narrate": True,
                "target_duration": 3.0,
                "sfx": "correct",
            },
            {"role": "cta", "text": "몇 개 맞혔나요?", "duration": 4.0},
        ],
    }
    data.update(overrides)
    return data


def final_scenes(**overrides: Any) -> dict[str, Any]:
    """TTS(#15, #16)까지 지난 확정 상태."""
    data = draft_scenes()
    data["scenes"][1].update(
        duration=2.94, audio=segment_path(1), audio_duration=2.14, narration_offset=3.3
    )
    data["scenes"][3].update(
        duration=1.72, audio=segment_path(3), audio_duration=0.92, narration_offset=10.24
    )
    data.update(overrides)
    return data


def project(**overrides: Any) -> dict[str, Any]:
    data = {
        "schema_version": 1,
        "type": "quiz",
        "language": "ko",
        "scenes": "scenes.json",
        "background": {"kind": "preset", "value": "gradient_default"},
        "audio": {"voice": "voice.mp3", "music": None},
        "render": {"width": 1080, "height": 1920, "fps": 30, "output": "final_short.mp4"},
    }
    data.update(overrides)
    return data


def metadata(**overrides: Any) -> dict[str, Any]:
    """`--topic` 경로가 만드는 상태 — 출처가 없으므로 `source`는 `null`이다."""
    data = {
        "schema_version": 1,
        "type": "quiz",
        "language": "ko",
        "titles": [
            "이 상식 4개, 다 맞히면 상위 1%",
            "상식 퀴즈 4문제, 몇 개 맞히나요",
            "세계 지리 상식 테스트",
        ],
        "description": "세계 지리 상식 퀴즈 4문제입니다. 정답을 맞혀 보세요.",
        "tags": ["상식퀴즈", "지리"],
        "source": None,
    }
    data.update(overrides)
    return data


def write_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# --- 문서 예시 -------------------------------------------------------------


def test_quiz_spec_example_validates() -> None:
    validate_quiz(doc_example(QUIZ_SPEC, "### 3.1 `quiz.json`"))


def test_scenes_spec_example_is_a_finalized_state() -> None:
    example = doc_example(QUIZ_SPEC, "### 3.2 `scenes.json` (파생)")

    # 문서 예시는 TTS 단계까지 지난 확정 상태다. 두 검증을 모두 통과해야 한다.
    validate_scenes(example)
    validate_scenes_final(example)


def test_project_prd_example_validates() -> None:
    validate_project(doc_example(PRD, "### 7.10 프로젝트 파일"))


def test_metadata_prd_example_validates() -> None:
    validate_metadata(doc_example(PRD, "### 7.8 메타데이터 생성"))


# --- quiz.json -------------------------------------------------------------


def test_quiz_accepts_minimal_valid_data() -> None:
    validate_quiz(quiz())


def test_quiz_reports_missing_required_field() -> None:
    data = quiz()
    del data["questions"][0]["answer"]

    assert_reports(failures(validate_quiz, data), "questions[0].answer")


def test_quiz_reports_bad_difficulty_with_index_path() -> None:
    data = quiz(questions=[quiz_question(), quiz_question(id=2), quiz_question(id=3)])
    data["questions"][2]["difficulty"] = "very-hard"

    message = assert_reports(failures(validate_quiz, data), "questions[2].difficulty")
    assert "easy | medium | hard" in message


def test_quiz_reports_bad_verify_status() -> None:
    data = quiz()
    data["questions"][0]["verify"]["status"] = "ok"

    assert_reports(failures(validate_quiz, data), "questions[0].verify.status")


def test_quiz_accepts_missing_verify_because_the_verifier_fills_it() -> None:
    # `quiz_generator`(#9)의 산출물에는 verify가 없다. 초안이 자기 검증을 통과해야 한다.
    data = quiz()
    del data["questions"][0]["verify"]

    validate_quiz(data)


def test_quiz_requires_status_when_verify_is_present() -> None:
    data = quiz()
    data["questions"][0]["verify"] = {"confidence": 0.9}

    assert_reports(failures(validate_quiz, data), "questions[0].verify.status")


def test_quiz_accepts_verify_without_source() -> None:
    data = quiz()
    data["questions"][0]["verify"] = {"status": "flagged", "confidence": 0.4}

    validate_quiz(data)


def test_quiz_rejects_confidence_out_of_range() -> None:
    data = quiz()
    data["questions"][0]["verify"]["confidence"] = 1.5

    assert_reports(failures(validate_quiz, data), "questions[0].verify.confidence")


def test_quiz_rejects_unknown_field() -> None:
    data = quiz()
    data["questions"][0]["hint"] = "힌트"

    message = assert_reports(failures(validate_quiz, data), "questions[0].hint")
    assert "알 수 없는 필드" in message


def test_quiz_rejects_bool_where_integer_is_expected() -> None:
    data = quiz()
    data["questions"][0]["countdown_sec"] = True

    assert_reports(failures(validate_quiz, data), "questions[0].countdown_sec")


def test_quiz_rejects_empty_text() -> None:
    data = quiz(hook="   ")

    assert_reports(failures(validate_quiz, data), "hook")


def test_quiz_rejects_empty_question_list() -> None:
    assert_reports(failures(validate_quiz, quiz(questions=[])), "questions")


def test_quiz_rejects_duplicate_question_ids() -> None:
    data = quiz(questions=[quiz_question(id=2), quiz_question(id=2)])

    message = assert_reports(failures(validate_quiz, data), "questions[1].id")
    assert "중복" in message


def test_quiz_collects_every_violation_at_once() -> None:
    data = quiz(hook="")
    data["questions"][0]["difficulty"] = "trivial"
    del data["questions"][0]["answer"]

    messages = failures(validate_quiz, data)

    assert len(messages) == 3
    for path in ("hook", "questions[0].answer", "questions[0].difficulty"):
        assert_reports(messages, path)


# --- schema_version --------------------------------------------------------


@pytest.mark.parametrize(
    ("validate", "data"),
    [
        (validate_quiz, quiz()),
        (validate_scenes, draft_scenes()),
        (validate_project, project()),
        (validate_metadata, metadata()),
    ],
)
def test_schema_version_is_required(validate: Validator, data: dict[str, Any]) -> None:
    del data["schema_version"]

    assert_reports(failures(validate, data), "schema_version")


@pytest.mark.parametrize(
    ("validate", "data"),
    [
        (validate_quiz, quiz(schema_version=99)),
        (validate_scenes, draft_scenes(schema_version=99)),
        (validate_scenes_final, final_scenes(schema_version=99)),
        (validate_project, project(schema_version=99)),
        (validate_metadata, metadata(schema_version=99)),
    ],
)
def test_unknown_schema_version_is_an_error(
    validate: Validator, data: dict[str, Any]
) -> None:
    message = assert_reports(failures(validate, data), "schema_version")

    assert "모르는 버전 99" in message


def test_unknown_version_is_reported_alone() -> None:
    # 모르는 버전에서는 필드 구성이 다를 수 있다. "알 수 없는 필드"를 쏟아내면 진짜 원인이 묻힌다.
    data = quiz(schema_version=99)
    data["questions"][0]["mystery"] = "?"

    assert failures(validate_quiz, data) == [
        "schema_version: 모르는 버전 99. 이 코드가 아는 버전: 1"
    ]


# --- scenes.json: 초안과 확정 상태 -----------------------------------------


def test_draft_passes_base_validation_but_fails_finalized() -> None:
    draft = draft_scenes()

    validate_scenes(draft)

    messages = failures(validate_scenes_final, draft)
    assert_reports(messages, "scenes[1].duration")
    for path in ("scenes[1]", "scenes[3]"):
        message = assert_reports(messages, path)
        assert "audio, audio_duration, narration_offset" in message


def test_finalized_scenes_pass_both_validations() -> None:
    data = final_scenes()

    validate_scenes(data)
    validate_scenes_final(data)


def test_finalized_validation_ignores_audio_for_non_narrated_scenes() -> None:
    # hook / countdown / cta에는 낭독이 없다. 확정 검증도 오디오 필드를 요구하지 않는다.
    data = final_scenes()
    data["scenes"][0]["narrate"] = False  # 명시적 false도 같게 취급한다

    validate_scenes_final(data)

    assert "audio" not in data["scenes"][0]
    assert "narrate" not in data["scenes"][2]


def test_finalized_validation_requires_duration_for_every_scene() -> None:
    data = final_scenes()
    del data["scenes"][2]["duration"]

    assert_reports(failures(validate_scenes_final, data), "scenes[2].duration")


def test_segment_index_must_match_scene_position() -> None:
    data = final_scenes()
    data["scenes"][3]["audio"] = segment_path(2)

    message = assert_reports(failures(validate_scenes_final, data), "scenes[3].audio")
    assert "audio/seg-003.mp3" in message


def test_segment_path_uses_three_digit_scene_index() -> None:
    assert segment_path(0) == "audio/seg-000.mp3"
    assert segment_path(3) == "audio/seg-003.mp3"
    assert segment_path(42) == "audio/seg-042.mp3"


def test_audio_path_outside_the_naming_convention_is_rejected() -> None:
    data = final_scenes()
    data["scenes"][1]["audio"] = "audio/q1_question.mp3"

    assert_reports(failures(validate_scenes, data), "scenes[1].audio")


def test_audio_field_on_a_non_narrated_scene_is_rejected() -> None:
    data = final_scenes()
    data["scenes"][0]["audio"] = segment_path(0)

    message = assert_reports(failures(validate_scenes_final, data), "scenes[0]")
    assert "narrate" in message


def test_duration_shorter_than_the_narration_is_rejected() -> None:
    data = final_scenes()
    data["scenes"][1]["duration"] = 1.0  # audio_duration 2.14보다 짧다

    assert_reports(failures(validate_scenes_final, data), "scenes[1].duration")


def test_narrated_scene_needs_text_and_target_duration() -> None:
    data = draft_scenes()
    del data["scenes"][1]["text"]
    del data["scenes"][1]["target_duration"]

    messages = failures(validate_scenes, data)
    assert_reports(messages, "scenes[1].text")
    assert_reports(messages, "scenes[1].target_duration")


def test_countdown_scene_needs_seconds() -> None:
    data = draft_scenes()
    del data["scenes"][2]["seconds"]

    assert_reports(failures(validate_scenes, data), "scenes[2].seconds")


def test_countdown_duration_must_equal_seconds() -> None:
    data = final_scenes()
    data["scenes"][2]["duration"] = 3.5

    message = assert_reports(failures(validate_scenes_final, data), "scenes[2].duration")
    assert "seconds" in message


def test_scenes_rejects_unknown_role() -> None:
    data = draft_scenes()
    data["scenes"][0]["role"] = "intro"

    message = assert_reports(failures(validate_scenes, data), "scenes[0].role")
    assert "hook | question | countdown | answer | cta" in message


def test_scenes_rejects_non_positive_duration() -> None:
    data = draft_scenes()
    data["scenes"][0]["duration"] = 0

    assert_reports(failures(validate_scenes, data), "scenes[0].duration")


def test_scenes_rejects_misspelled_narrate_flag() -> None:
    # 조용히 무시하면 낭독 장면이 음성 없이 렌더된다.
    data = draft_scenes()
    scene = data["scenes"][1]
    scene["narate"] = scene.pop("narrate")

    assert_reports(failures(validate_scenes, data), "scenes[1].narate")


def test_scenes_rejects_empty_scene_list() -> None:
    assert_reports(failures(validate_scenes, draft_scenes(scenes=[])), "scenes")


# --- project.json ----------------------------------------------------------


def test_project_accepts_initial_state() -> None:
    validate_project(project())


def test_project_accepts_null_voice_when_nothing_is_narrated() -> None:
    data = project()
    data["audio"]["voice"] = None

    validate_project(data)


def test_project_rejects_unknown_background_kind() -> None:
    data = project()
    data["background"]["kind"] = "pexels"

    message = assert_reports(failures(validate_project, data), "background.kind")
    assert "preset | color | image | video" in message


def test_project_reports_missing_nested_field() -> None:
    data = project()
    del data["render"]["fps"]

    assert_reports(failures(validate_project, data), "render.fps")


def test_project_rejects_scene_array_in_place_of_reference() -> None:
    # 장면 배열은 scenes.json에만 있다. 여기 복사되면 원본이 모호해진다.
    data = project(scenes=[{"role": "hook"}])

    assert_reports(failures(validate_project, data), "scenes")


def test_project_rejects_editing_state_fields_for_now() -> None:
    # 편집 상태는 #26에서 스키마와 함께 추가한다. 미리 흘러들면 앱과 엔진의 가정이 갈린다.
    data = project(caption_style={"font": "malgun"})

    assert_reports(failures(validate_project, data), "caption_style")


def test_project_rejects_non_mapping_root() -> None:
    message = assert_reports(failures(validate_project, []), "최상위")

    assert "매핑" in message


# --- metadata.json ---------------------------------------------------------


def test_metadata_accepts_the_topic_path_state() -> None:
    validate_metadata(metadata())


def test_metadata_requires_exactly_three_titles() -> None:
    """사람이 셋 중 하나를 골라 업로드 폼에 붙여넣는다 (PRD 7.8). 개수가 계약이다."""
    two = metadata()["titles"][:2]

    message = assert_reports(failures(validate_metadata, metadata(titles=two)), "titles")

    assert f"정확히 {metadata_schema.TITLE_COUNT}개" in message


def test_metadata_rejects_more_than_three_titles() -> None:
    four = [*metadata()["titles"], "덤으로 하나 더"]

    assert_reports(failures(validate_metadata, metadata(titles=four)), "titles")


def test_metadata_keeps_source_as_a_nullable_required_field() -> None:
    """필드를 빼면 "출처 없음"과 "생성기가 빠뜨림"이 구분되지 않는다 (#13, #31)."""
    validate_metadata(metadata(source=None))
    validate_metadata(metadata(source="https://example.com/article"))

    data = metadata()
    del data["source"]
    assert_reports(failures(validate_metadata, data), "source")


def test_metadata_rejects_an_empty_tag_list() -> None:
    assert_reports(failures(validate_metadata, metadata(tags=[])), "tags")


def test_metadata_rejects_an_empty_description() -> None:
    assert_reports(failures(validate_metadata, metadata(description="  ")), "description")


def test_metadata_rejects_an_unregistered_type() -> None:
    """타입 후보는 레지스트리가 정한다. 스키마에 이름을 적지 않는다 (#8)."""
    message = assert_reports(failures(validate_metadata, metadata(type="ranking")), "type")

    assert "등록된 타입" in message


def test_metadata_rejects_unknown_field() -> None:
    assert_reports(failures(validate_metadata, metadata(category="general")), "category")


# --- 파일 읽기 -------------------------------------------------------------


def test_loaders_read_and_validate_files(tmp_path: Path) -> None:
    loaded_quiz = load_quiz(write_json(tmp_path / "quiz.json", quiz()))
    loaded_scenes = load_scenes(write_json(tmp_path / "scenes.json", draft_scenes()))
    loaded_project = load_project(write_json(tmp_path / "project.json", project()))
    loaded_metadata = load_metadata(write_json(tmp_path / "metadata.json", metadata()))

    assert loaded_quiz["type"] == "quiz"
    assert len(loaded_scenes["scenes"]) == 5
    assert loaded_project["scenes"] == "scenes.json"
    assert loaded_metadata["source"] is None


def test_load_scenes_can_demand_a_finalized_file(tmp_path: Path) -> None:
    path = write_json(tmp_path / "scenes.json", draft_scenes())

    load_scenes(path)  # 초안은 통과

    with pytest.raises(SchemaError) as error_info:
        load_scenes(path, finalized=True)

    # 어느 파일이 문제인지 메시지에 남아야 한다.
    assert str(path) in str(error_info.value)
    assert error_info.value.source == path


def test_load_reports_json_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / "quiz.json"
    path.write_text('{"type": "quiz",}', encoding="utf-8")

    with pytest.raises(SchemaError) as error_info:
        load_quiz(path)

    assert "JSON 문법 오류" in str(error_info.value)


def test_load_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SchemaError) as error_info:
        load_quiz(tmp_path / "없는파일.json")

    assert "읽을 수 없다" in str(error_info.value)


def test_validation_does_not_mutate_the_input() -> None:
    data = final_scenes()
    before = copy.deepcopy(data)

    validate_scenes_final(data)

    assert data == before


# --- JSON Schema 파생 -------------------------------------------------------
#
# 산출물 스키마를 LLM에 넘길 형태로 옮기는 변환 (#9). 필드 이름과 열거값을 프롬프트 쪽에
# 다시 적지 않기 위한 것이므로, 여기서 검증하는 것은 "같은 규칙이 그대로 옮겨졌는가"다.


def test_scalar_kinds_map_to_json_types() -> None:
    assert Scalar("str").to_json_schema()["type"] == "string"
    assert Scalar("int").to_json_schema()["type"] == "integer"
    assert Scalar("float").to_json_schema()["type"] == "number"
    assert Scalar("bool").to_json_schema()["type"] == "boolean"


def test_non_empty_text_becomes_min_length() -> None:
    """`check`가 빈 문자열을 반려하는 것과 같다 — 자막에 빈 값이 렌더될 수 없다."""
    assert Scalar("str").to_json_schema()["minLength"] == 1
    assert "minLength" not in Scalar("str", allow_empty=True).to_json_schema()


def test_choices_become_an_enum() -> None:
    node = Scalar("str", choices=("easy", "hard")).to_json_schema()

    assert node["enum"] == ["easy", "hard"]


def test_nullable_scalar_allows_null() -> None:
    node = Scalar("str", nullable=True).to_json_schema()

    assert node["type"] == ["string", "null"]


def test_numeric_bounds_are_carried_over() -> None:
    node = Scalar("float", minimum=0.0, maximum=1.0).to_json_schema()

    assert node["minimum"] == 0.0
    assert node["maximum"] == 1.0


def test_pattern_is_anchored_because_check_uses_fullmatch() -> None:
    """JSON Schema의 `pattern`은 부분 일치다. 앵커가 빠지면 접두·접미가 붙은 값도 통과한다."""
    node = Scalar("str", pattern=r"audio/seg-\d{3}\.mp3").to_json_schema()

    assert re.search(node["pattern"], "audio/seg-003.mp3") is not None
    assert re.search(node["pattern"], "x/audio/seg-003.mp3.bak") is None


def test_runtime_choices_are_resolved_when_derived() -> None:
    """`Choices`는 검증 시점에 후보를 조회한다. 파생된 스키마는 그 순간의 목록으로 굳는다."""
    node = Choices(lambda: ("quiz", "ranking"), "등록된 타입").to_json_schema()

    assert node["enum"] == ["quiz", "ranking"]


def test_object_lists_required_fields_and_rejects_unknown_ones() -> None:
    node = Object(
        {"question": text(), "verify": section({"status": text()}, required=False)}
    ).to_json_schema()

    assert node["required"] == ["question"]
    assert node["additionalProperties"] is False
    assert set(node["properties"]) == {"question", "verify"}


def test_array_carries_its_minimum_length() -> None:
    node = Array(Scalar("str"), min_items=2).to_json_schema()

    assert node["type"] == "array"
    assert node["items"]["type"] == "string"
    assert node["minItems"] == 2
    assert "maxItems" not in node


def test_array_carries_its_maximum_length() -> None:
    node = Array(Scalar("str"), min_items=3, max_items=3).to_json_schema()

    assert node["minItems"] == 3
    assert node["maxItems"] == 3


def test_array_reports_a_count_it_cannot_accept() -> None:
    errors: list[str] = []
    Array(Scalar("str"), min_items=3, max_items=3).check(["하나"], "titles", errors)

    assert errors == ["titles: 항목이 정확히 3개여야 한다. 받은 값: 1개"]


def test_without_drops_fields_and_leaves_the_original_alone() -> None:
    original = Object({"id": integer(), "question": text()})

    trimmed = original.without("id")

    assert set(trimmed.fields) == {"question"}
    assert set(original.fields) == {"id", "question"}


def test_without_rejects_a_name_that_is_not_in_the_schema() -> None:
    """오타를 조용히 무시하면 코드가 채울 필드가 LLM에 넘기는 스키마에 남는다."""
    with pytest.raises(ValueError, match="countdown_secc"):
        Object({"countdown_sec": integer()}).without("countdown_secc")


def test_quiz_content_schema_matches_the_artifact_schema_field_by_field() -> None:
    """생성기가 넘기는 스키마는 `quiz.json` 스키마에서 파생된 것이어야 한다 (#9)."""
    derived = content_json_schema(question_count=4, caps={"answer": 20, "explanation": 60})
    full = QUIZ_SCHEMA.root.to_json_schema()

    question = derived["properties"]["questions"]["items"]["properties"]
    full_question = full["properties"]["questions"]["items"]["properties"]

    assert set(derived["properties"]) < set(full["properties"])
    assert set(question) < set(full_question)
    assert question["difficulty"]["enum"] == full_question["difficulty"]["enum"]


def test_metadata_content_schema_asks_only_for_what_the_model_decides() -> None:
    """버전·타입·언어·출처는 코드가 채운다. 물어봐야 버려지는 값에 토큰을 쓰지 않는다 (#13)."""
    derived = metadata_schema.content_json_schema(title_max_len=40, tag_max_count=10)

    assert set(derived["properties"]) == {"titles", "description", "tags"}
    assert set(derived["properties"]) < set(
        metadata_schema.METADATA_SCHEMA.root.to_json_schema()["properties"]
    )


def test_metadata_content_schema_carries_the_config_limits() -> None:
    """상한은 스키마가 아니라 config가 정한다. 파생 시점에 얹힌다."""
    derived = metadata_schema.content_json_schema(title_max_len=25, tag_max_count=6)

    titles = derived["properties"]["titles"]
    assert titles["minItems"] == titles["maxItems"] == metadata_schema.TITLE_COUNT
    assert titles["items"]["maxLength"] == 25
    assert derived["properties"]["tags"]["maxItems"] == 6
