"""CLI 동작 검증 — 이슈 #5의 완료 조건, 그리고 #9·#12가 붙인 산출물 배선.

**모든 테스트가 `stub_llm`을 쓴다.** run이 콘텐츠 생성기를 부르므로, 픽스처가 없으면
테스트마다 실제 claude CLI가 돈다 (`conftest.py`).
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from conftest import StubLLM

from shorts_maker.llm import LLMError
from shorts_maker.main import (
    EXIT_CONFIG_ERROR,
    EXIT_FLAGGED,
    EXIT_RUNTIME_ERROR,
    main,
)
from shorts_maker.run_context import LOG_FILENAME
from shorts_maker.schemas import SCENES_SCHEMA, SchemaError, load_scenes
from shorts_maker.shorts_types import DEFAULT_TYPE, available_types, get_type

pytestmark = pytest.mark.usefixtures("stub_llm")


def run_dirs(output_root: Path) -> list[Path]:
    return sorted(p for p in output_root.iterdir() if p.is_dir())


def content_artifact(run_dir: Path) -> dict:
    """이번 run이 만든 타입 전용 콘텐츠 산출물. 파일명은 타입 선언에서 얻는다."""
    path = run_dir / get_type(DEFAULT_TYPE).content_artifact
    return json.loads(path.read_text(encoding="utf-8"))


def test_topic_run_creates_run_dir(tmp_path: Path) -> None:
    exit_code = main(["--topic", "세계 지리 상식", "--out", str(tmp_path)])

    assert exit_code == 0
    created = run_dirs(tmp_path)
    assert len(created) == 1
    assert created[0].name.startswith("run-")


def test_repeated_runs_do_not_overwrite(tmp_path: Path) -> None:
    main(["--topic", "첫 번째", "--out", str(tmp_path)])
    first = run_dirs(tmp_path)[0]
    artifact = first / "scenes.json"
    artifact.write_text('{"kept": true}', encoding="utf-8")

    main(["--topic", "두 번째", "--out", str(tmp_path)])

    assert len(run_dirs(tmp_path)) == 2
    assert artifact.read_text(encoding="utf-8") == '{"kept": true}'
    assert "첫 번째" in (first / LOG_FILENAME).read_text(encoding="utf-8")


def test_run_log_records_topic_and_type(tmp_path: Path) -> None:
    main(["--topic", "한국사 상식", "--type", "quiz", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert "한국사 상식" in log_text
    assert "quiz" in log_text


def test_run_log_always_keeps_debug_lines(tmp_path: Path) -> None:
    """run.log는 사후 검수 기록이므로 --verbose 여부와 무관하게 DEBUG까지 남는다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    assert "python" in (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")


def test_verbose_adds_debug_lines_to_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])
    quiet_console = capsys.readouterr().err

    main(["--topic", "주제", "--out", str(tmp_path), "--verbose"])
    verbose_console = capsys.readouterr().err

    assert "python" not in quiet_console
    assert "python" in verbose_console


def test_type_defaults_to_quiz(tmp_path: Path) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert f"타입 {DEFAULT_TYPE}" in log_text


def test_help_lists_arguments_and_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    for flag in ("--topic", "--type", "--out", "--config", "--fail-on-flagged", "--verbose"):
        assert flag in help_text
    assert f"default: {DEFAULT_TYPE}" in help_text
    assert "default: outputs" in help_text
    # required 인자에 무의미한 "(default: None)"이 붙지 않는다.
    assert "default: None" not in help_text


def test_missing_topic_exits_nonzero_with_reason(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code != 0
    assert "--topic" in capsys.readouterr().err


def test_unsupported_type_exits_nonzero_and_lists_supported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--topic", "주제", "--type", "storytime", "--out", str(tmp_path)])

    assert exit_info.value.code != 0
    stderr = capsys.readouterr().err
    assert "storytime" in stderr
    for supported in available_types():
        assert supported in stderr
    assert not tmp_path.exists() or run_dirs(tmp_path) == []


def test_console_output_is_utf8_even_when_locale_is_cp949(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """리다이렉트된 스트림이 cp949여도 한국어 로그가 깨지지 않는다."""
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(raw, encoding="cp949"))

    exit_code = main(["--topic", "주제 — 대시 포함", "--out", str(tmp_path)])
    sys.stderr.flush()

    assert exit_code == 0
    assert "주제 — 대시 포함" in raw.getvalue().decode("utf-8")


def test_missing_config_file_exits_before_creating_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "없는파일.yaml"
    output_root = tmp_path / "out"

    exit_code = main(
        ["--topic", "주제", "--out", str(output_root), "--config", str(missing)]
    )

    assert exit_code == EXIT_CONFIG_ERROR
    assert str(missing) in capsys.readouterr().err
    assert not output_root.exists()


def test_invalid_config_exits_before_creating_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text('quiz:\n  question_count: "넷"\n', encoding="utf-8")
    output_root = tmp_path / "out"

    exit_code = main(
        ["--topic", "주제", "--out", str(output_root), "--config", str(config_path)]
    )

    assert exit_code == EXIT_CONFIG_ERROR
    assert "quiz.question_count" in capsys.readouterr().err
    assert not output_root.exists()


def test_run_log_records_resolved_config_and_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # 실행 디렉터리에 config.yaml이 없는 상태
    output_root = tmp_path / "out"

    main(["--topic", "주제", "--out", str(output_root)])

    log_text = (run_dirs(output_root)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert "설정 파일 없음" in log_text
    assert "설정 tts.voice = ko-KR-SunHiNeural" in log_text
    assert "설정 timing.lead_in_sec = 0.3" in log_text
    assert "설정 llm.verifier.confidence_threshold = 0.8" in log_text


def test_run_log_records_config_source_and_applied_value(tmp_path: Path) -> None:
    config_path = tmp_path / "my.yaml"
    config_path.write_text("tts:\n  voice: ko-KR-InJoonNeural\n", encoding="utf-8")
    output_root = tmp_path / "out"

    main(["--topic", "주제", "--out", str(output_root), "--config", str(config_path)])

    log_text = (run_dirs(output_root)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert str(config_path) in log_text
    assert "설정 tts.voice = ko-KR-InJoonNeural" in log_text


def test_unreadable_output_root_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "outputs"
    blocker.write_text("경로가 파일이다", encoding="utf-8")

    exit_code = main(["--topic", "주제", "--out", str(blocker)])

    assert exit_code == EXIT_RUNTIME_ERROR
    assert "run 디렉터리에 쓸 수 없다" in capsys.readouterr().err


# --- 콘텐츠 산출물 (#9) ------------------------------------------------------


def test_run_writes_the_type_content_artifact(tmp_path: Path) -> None:
    exit_code = main(["--topic", "세계 지리 상식", "--out", str(tmp_path)])

    assert exit_code == 0
    content = content_artifact(run_dirs(tmp_path)[0])
    assert content["type"] == DEFAULT_TYPE
    assert len(content["questions"]) == 4


def test_written_artifact_is_readable_korean_json(tmp_path: Path) -> None:
    """사람이 검수·수정하는 원본이다 (퀴즈 스펙 3.1). 이스케이프된 한 줄이면 못 고친다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    path = run_dirs(tmp_path)[0] / get_type(DEFAULT_TYPE).content_artifact
    raw = path.read_text(encoding="utf-8")

    assert "\\u" not in raw
    assert raw.count("\n") > 5


def test_topic_reaches_the_generator(tmp_path: Path, stub_llm: StubLLM) -> None:
    main(["--topic", "한국사 상식", "--out", str(tmp_path)])

    assert "한국사 상식" in stub_llm.calls[0]["prompt"]


def test_run_log_records_the_call_and_the_artifact(tmp_path: Path) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert "LLM 호출" in log_text
    assert f"{get_type(DEFAULT_TYPE).content_artifact} 생성 완료" in log_text


def test_question_count_outside_the_type_range_exits_before_creating_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], stub_llm: StubLLM
) -> None:
    """3~5는 퀴즈 타입의 규칙이므로 config.py가 아니라 타입 선언이 확인한다."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("quiz:\n  question_count: 9\n", encoding="utf-8")
    output_root = tmp_path / "out"

    exit_code = main(
        ["--topic", "주제", "--out", str(output_root), "--config", str(config_path)]
    )

    assert exit_code == EXIT_CONFIG_ERROR
    assert "quiz.question_count" in capsys.readouterr().err
    assert not output_root.exists()
    assert stub_llm.call_count == 0


def test_generation_failure_exits_nonzero_and_leaves_the_reason_in_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], stub_llm: StubLLM
) -> None:
    stub_llm.reply(*[LLMError("structured_output이 없다")] * 3)

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == EXIT_RUNTIME_ERROR
    # 콘솔은 단계를 특정하지 않는다. 어느 단계였는지는 run.log가 아래에서 답한다.
    assert "생성 실패" in capsys.readouterr().err
    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")
    assert "콘텐츠 생성 실패" in log_text
    assert "structured_output" in log_text


def test_failed_run_leaves_no_content_artifact(tmp_path: Path, stub_llm: StubLLM) -> None:
    """반쪽짜리 산출물을 남기지 않는다 — 검수 대상과 섞인다."""
    stub_llm.reply(*[LLMError("실패")] * 3)

    main(["--topic", "주제", "--out", str(tmp_path)])

    run_dir = run_dirs(tmp_path)[0]
    assert not (run_dir / get_type(DEFAULT_TYPE).content_artifact).exists()
    assert not (run_dir / SCENES_SCHEMA.name).exists()


# --- 장면 산출물 (#12) -------------------------------------------------------


def scenes_artifact(run_dir: Path) -> dict:
    """이번 run이 만든 `scenes.json`. 타입과 무관한 공통 산출물이다."""
    return json.loads((run_dir / SCENES_SCHEMA.name).read_text(encoding="utf-8"))


def test_run_writes_the_scene_draft(tmp_path: Path) -> None:
    exit_code = main(["--topic", "세계 지리 상식", "--out", str(tmp_path)])

    assert exit_code == 0
    scenes = scenes_artifact(run_dirs(tmp_path)[0])
    # 기본 4문제 → 후킹 1 + (질문·카운트다운·정답) × 4 + CTA 1.
    assert len(scenes["scenes"]) == 14
    assert scenes["type"] == DEFAULT_TYPE


def test_the_written_scene_draft_passes_validation(tmp_path: Path) -> None:
    """`scenes.json`을 읽는 이후 단계가 파일에서 실제로 받는 것을 검증한다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    load_scenes(run_dirs(tmp_path)[0] / SCENES_SCHEMA.name)


def test_the_scene_draft_is_not_finalized_yet(tmp_path: Path) -> None:
    """낭독 장면의 `duration`은 TTS(#16)가 채운다. 여기서 확정 상태가 되면 실측이 무의미해진다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    with pytest.raises(SchemaError):
        load_scenes(run_dirs(tmp_path)[0] / SCENES_SCHEMA.name, finalized=True)


def test_run_log_records_the_scene_artifact(tmp_path: Path) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert f"{SCENES_SCHEMA.name} 생성 완료" in log_text
    assert "장면 14개" in log_text


# --- 검수 게이트 (#11) -------------------------------------------------------

QUESTION = "세계에서 가장 긴 강은?"
ANSWER = "나일강"


def fixed_run(stub_llm: StubLLM, *, given: str = ANSWER, certainty: float = 1.0) -> None:
    """문제 하나짜리 고정 응답 세트 — 생성 1회 + 재답변 2회 + 모호성 프로브 1회.

    기본값은 전원 일치·확신도 1.0이라 `verified`로 통과한다. `given`을 바꾸면 재답변이
    갈려 검증기가 `flagged`를 만들고, `certainty`를 낮추면 임계값이 자른다 — **#11의
    두 경로를 같은 입력에서 갈라 볼 수 있다.**
    """
    reply = {
        "answers": [{"id": 1, "answer": given, "certainty": certainty, "basis": "근거"}]
    }
    stub_llm.reply(
        {
            "hook": "이 상식 다 맞히면 상위 1%",
            "cta": "몇 개 맞혔나요?",
            "questions": [
                {
                    "question": QUESTION,
                    "answer": ANSWER,
                    "explanation": "해설입니다.",
                    "difficulty": "easy",
                }
            ],
        },
        reply,
        reply,
        {"questions": [{"id": 1, "single_answer": True, "reason": "판단 근거"}]},
    )


def test_flagged_content_still_exits_zero_and_leaves_the_artifact(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """검수 주체는 사람이고 사람은 산출물이 있어야 검수한다 (PRD 2장).
    flagged 하나 때문에 산출물이 없으면 검수할 대상이 사라진다."""
    fixed_run(stub_llm, given="아마존강")

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == 0
    content = content_artifact(run_dirs(tmp_path)[0])
    assert content["questions"][0]["verify"]["status"] == "flagged"


def test_the_threshold_verdict_is_saved_in_the_artifact(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """재답변은 전부 일치했지만 확신도가 낮은 경우 — 임계값이 자르는 유일한 경로다."""
    fixed_run(stub_llm, certainty=0.5)

    main(["--topic", "주제", "--out", str(tmp_path)])

    verify = content_artifact(run_dirs(tmp_path)[0])["questions"][0]["verify"]
    assert verify["status"] == "flagged"
    assert "임계값 미달" in verify["source"]


def test_the_warning_carries_id_question_confidence_reason_and_threshold(
    tmp_path: Path, stub_llm: StubLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    fixed_run(stub_llm, certainty=0.5)

    main(["--topic", "주제", "--out", str(tmp_path)])

    console = capsys.readouterr().err
    for expected in ("문제 1", QUESTION, "confidence 0.5", "임계값 0.8", "임계값 미달"):
        assert expected in console


def test_the_warning_is_kept_in_the_run_log_without_verbose(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """run.log는 사후 검수 기록이므로 그때 --verbose를 붙였는지에 좌우되면 안 된다."""
    fixed_run(stub_llm, given="아마존강")

    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")
    assert "검수 필요" in log_text
    assert QUESTION in log_text
    assert "임계값 0.8" in log_text


def test_a_clean_run_says_nothing_about_review(
    tmp_path: Path, stub_llm: StubLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    fixed_run(stub_llm)

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == 0
    assert "검수 필요" not in capsys.readouterr().err
    assert content_artifact(run_dirs(tmp_path)[0])["questions"][0]["verify"]["status"] == (
        "verified"
    )


def test_fail_on_flagged_stops_with_a_code_of_its_own(
    tmp_path: Path, stub_llm: StubLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    """배치 실행이 "생성이 깨졌다"와 "검수가 필요하다"를 구분할 수 있어야 한다."""
    fixed_run(stub_llm, given="아마존강")

    exit_code = main(["--topic", "주제", "--out", str(tmp_path), "--fail-on-flagged"])

    assert exit_code == EXIT_FLAGGED
    assert exit_code not in (0, EXIT_RUNTIME_ERROR, EXIT_CONFIG_ERROR)
    assert "--fail-on-flagged" in capsys.readouterr().err
    # 멈추는 것은 이후 단계이지 이번 실행의 산출물이 아니다.
    assert content_artifact(run_dirs(tmp_path)[0])["questions"][0]["verify"]


def test_fail_on_flagged_passes_a_clean_run(tmp_path: Path, stub_llm: StubLLM) -> None:
    fixed_run(stub_llm)

    assert main(["--topic", "주제", "--out", str(tmp_path), "--fail-on-flagged"]) == 0


def test_flagged_content_still_produces_the_scene_draft(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """검수 경고는 진행을 멈추지 않는다 (#11). 장면 분할은 그 경고 **뒤에** 있다."""
    fixed_run(stub_llm, given="아마존강")

    assert main(["--topic", "주제", "--out", str(tmp_path)]) == 0
    # 문제 하나짜리 응답 세트 → 후킹 1 + 3 + CTA 1.
    assert len(scenes_artifact(run_dirs(tmp_path)[0])["scenes"]) == 5


def test_fail_on_flagged_still_leaves_this_runs_artifacts(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """`--fail-on-flagged`가 멈추는 것은 **이후 단계**이지 이번 실행의 산출물이 아니다.

    종료 코드는 run이 끝난 뒤에 정해지므로 장면 초안까지는 남는다. 검수 주체인 사람이
    `quiz.json`을 고친 뒤 무엇이 어떻게 배치됐는지 함께 보려면 이 파일이 있어야 한다.
    """
    fixed_run(stub_llm, given="아마존강")

    exit_code = main(["--topic", "주제", "--out", str(tmp_path), "--fail-on-flagged"])

    assert exit_code == EXIT_FLAGGED
    assert scenes_artifact(run_dirs(tmp_path)[0])["scenes"]
