"""쇼츠 타입 레지스트리 — 이슈 #8의 완료 조건에 대응한다.

산출물 조건은 PRD 6.2 표를 **문서에서 직접 읽어** 대조한다. 기대값을 여기 적어 두면
문서와 코드가 갈라졌을 때 둘 다 통과해 버린다 (`test_schemas.py`와 같은 이유).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import shorts_types
from shorts_maker.config import Config, ConfigError, defaults, load_config
from shorts_maker.main import main
from shorts_maker.run_context import LOG_FILENAME
from shorts_maker.schemas import SchemaError, validate_project, validate_scenes
from shorts_maker.shorts_types import (
    DEFAULT_TYPE,
    SCRIPT_ARTIFACT,
    SUMMARY_ARTIFACT,
    ShortsType,
    ShortsTypeError,
    UnknownShortsTypeError,
    available_types,
    get_type,
    register,
    unregister,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRD = REPO_ROOT / "docs" / "PRD.md"

DUMMY_TYPE = "ranking"
"""테스트 안에만 존재하는 두 번째 타입. 저장소에는 등록하지 않는다 (#8 out of scope)."""


# --- 헬퍼 ------------------------------------------------------------------


def artifact_condition(filename: str) -> str:
    """PRD 6.2 표에서 `filename` 행의 "생성 조건" 칸을 읽는다."""
    pattern = rf"^\|\s*`{re.escape(filename)}`\s*\|[^|]*\|([^|]*)\|"
    match = re.search(pattern, PRD.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None, f"PRD 6.2 표에 {filename} 행이 없다"
    return match.group(1).strip()


def dummy_generate(*, topic: str, config: Any) -> dict[str, Any]:
    return {"topic": topic}


def dummy_scene_template(content: Mapping[str, Any], *, config: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "type": DUMMY_TYPE,
        "scenes": [{"role": "hook", "text": content["topic"], "duration": 2.0}],
    }


@pytest.fixture
def dummy_type() -> Iterator[ShortsType]:
    """더미 타입을 등록한 상태. 파이프라인 코드는 건드리지 않는다."""
    declared = ShortsType(
        name=DUMMY_TYPE,
        content_artifact="ranking.json",
        generator=dummy_generate,
        scene_template=dummy_scene_template,
        produces_script=True,
        produces_summary=True,
    )
    register(declared)
    try:
        yield declared
    finally:
        unregister(DUMMY_TYPE)


def run_dirs(output_root: Path) -> list[Path]:
    return sorted(p for p in output_root.iterdir() if p.is_dir())


# --- 라우팅 ----------------------------------------------------------------


def test_quiz_routes_to_its_generator_and_scene_template() -> None:
    quiz = get_type("quiz")

    assert quiz.name == DEFAULT_TYPE
    assert quiz.generator.__module__ == "shorts_maker.types.quiz.quiz_generator"
    assert quiz.scene_template.__module__ == "shorts_maker.types.quiz.scene_template"


def test_quiz_scene_template_is_a_stub_pointing_at_its_issue() -> None:
    """실제 구현은 #12가 채운다. 스텁이 조용히 빈 값을 돌려주면 안 된다."""
    with pytest.raises(NotImplementedError, match="#12"):
        get_type("quiz").scene_template({}, config=None)


def test_lookup_is_cached_so_the_declaration_module_imports_once() -> None:
    assert get_type("quiz") is get_type("quiz")


# --- 설정 사전 점검 ---------------------------------------------------------


def test_type_without_a_config_check_accepts_any_config(dummy_type: ShortsType) -> None:
    """확인할 것이 없는 타입은 선언하지 않는다 — 빈 함수를 강요하지 않는다."""
    dummy_type.check_config(Config(data=defaults()))


def test_declared_config_check_runs(dummy_type: ShortsType) -> None:
    seen: list[Config] = []
    checked = replace(dummy_type, config_check=seen.append)
    config = Config(data=defaults())

    checked.check_config(config)

    assert seen == [config]


def test_quiz_declares_the_config_check_that_bounds_its_question_count() -> None:
    """범위를 아는 것은 타입이다. `config.py`는 타입별 허용 범위를 알 수 없다."""
    quiz = get_type("quiz")

    with pytest.raises(ConfigError, match="quiz.question_count"):
        quiz.check_config(load_config(overrides={"quiz.question_count": 9}))


def test_cli_type_choices_and_help_come_from_the_registry(
    dummy_type: ShortsType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])

    help_text = capsys.readouterr().out
    for name in available_types():
        assert name in help_text


def test_registered_type_runs_without_touching_pipeline_code(
    dummy_type: ShortsType, tmp_path: Path
) -> None:
    """더미 타입 등록만으로 CLI가 그 타입으로 돈다 — #8의 핵심 완료 조건."""
    exit_code = main(["--topic", "주제", "--type", DUMMY_TYPE, "--out", str(tmp_path), "-v"])

    assert exit_code == 0
    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")
    assert f"타입 {DUMMY_TYPE}" in log_text
    assert "dummy_generate" in log_text
    assert "dummy_scene_template" in log_text


# --- 미등록 타입 -----------------------------------------------------------


def test_unknown_type_lookup_lists_available_types() -> None:
    """CLI를 거치지 않는 호출 경로도 실행 초기에 막힌다."""
    with pytest.raises(UnknownShortsTypeError) as error_info:
        get_type("storytime")

    message = str(error_info.value)
    assert "storytime" in message
    for name in available_types():
        assert name in message


def test_broken_declaration_module_names_the_type_and_the_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(shorts_types._REGISTRY, "broken", "shorts_maker.types.없는모듈")

    with pytest.raises(ShortsTypeError) as error_info:
        get_type("broken")

    message = str(error_info.value)
    assert "broken" in message
    assert "shorts_maker.types.없는모듈" in message


def test_duplicate_registration_is_rejected(dummy_type: ShortsType) -> None:
    with pytest.raises(ShortsTypeError, match=DUMMY_TYPE):
        register(dummy_type)


def test_builtin_type_cannot_be_unregistered() -> None:
    with pytest.raises(ShortsTypeError, match="quiz"):
        unregister("quiz")

    assert "quiz" in available_types()


# --- 산출물 선언 (PRD 6.2 표) -----------------------------------------------


def test_quiz_does_not_produce_script_or_summary() -> None:
    quiz = get_type("quiz")

    assert quiz.produces_script is False
    assert quiz.produces_summary is False
    assert quiz.produces(SCRIPT_ARTIFACT) is False
    assert quiz.produces(SUMMARY_ARTIFACT) is False


def test_quiz_artifact_declaration_matches_prd_table() -> None:
    quiz = get_type("quiz")

    assert quiz.produces(quiz.content_artifact) is True
    assert quiz.artifacts() == (quiz.content_artifact,)
    # 표의 quiz.json 행은 `--type quiz`를, 나머지 둘은 퀴즈 제외를 조건으로 적는다.
    assert "quiz" in artifact_condition(quiz.content_artifact)
    for artifact in (SCRIPT_ARTIFACT, SUMMARY_ARTIFACT):
        condition = artifact_condition(artifact)
        assert "퀴즈" in condition and "생성하지 않는다" in condition


def test_type_declares_optional_artifacts_it_does_produce(dummy_type: ShortsType) -> None:
    assert dummy_type.produces(SCRIPT_ARTIFACT) is True
    assert dummy_type.artifacts() == ("ranking.json", SCRIPT_ARTIFACT, SUMMARY_ARTIFACT)


def test_produces_rejects_artifacts_the_type_does_not_decide() -> None:
    """공통 산출물과 입력 경로 산출물을 물으면 조용히 False가 아니라 오류다."""
    quiz = get_type("quiz")

    for artifact in ("scenes.json", "source.json", "final_short.mp4"):
        with pytest.raises(ValueError, match=artifact):
            quiz.produces(artifact)


# --- 스키마가 레지스트리를 따라온다 ------------------------------------------


def test_scenes_and_project_schemas_accept_a_newly_registered_type(
    dummy_type: ShortsType,
) -> None:
    """스키마의 `type` 후보가 import 시점에 굳어 있으면 여기서 걸린다."""
    scenes = dummy_scene_template({"topic": "주제"}, config=None)
    validate_scenes(scenes)

    validate_project(
        {
            "schema_version": 1,
            "type": DUMMY_TYPE,
            "language": "ko",
            "scenes": "scenes.json",
            "background": {"kind": "preset", "value": "dark-gradient"},
            "audio": {"voice": None, "music": None},
            "render": {
                "width": 1080,
                "height": 1920,
                "fps": 30,
                "output": "final_short.mp4",
            },
        }
    )


def test_scenes_schema_rejects_an_unregistered_type() -> None:
    scenes = dummy_scene_template({"topic": "주제"}, config=None)  # 등록 없이 만든다

    with pytest.raises(SchemaError) as error_info:
        validate_scenes(scenes)

    message = str(error_info.value)
    assert f"type: 허용되지 않는 값 {DUMMY_TYPE!r}" in message
    assert "quiz" in message
