"""CLI 동작 검증 — 이슈 #5의 완료 조건, 그리고 #9·#12·#15·#16이 붙인 산출물 배선.

**모든 테스트가 `stub_llm`과 `stub_tts`를 쓴다.** run이 콘텐츠 생성기와 TTS provider를
부르므로, 픽스처가 없으면 테스트마다 실제 claude CLI와 edge-tts 엔드포인트가 돈다. `stub_tts`가
끌어오는 `stub_ffmpeg`가 길이 측정과 합성 트랙 생성까지 대신한다 (`conftest.py`).
"""

from __future__ import annotations

import io
import json
import re
import sys
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from conftest import (
    ARTICLE_HEADLINE,
    ARTICLE_URL,
    STUB_SEGMENT_SEC,
    StubFFmpeg,
    StubHTTP,
    StubLLM,
    StubTTS,
    article_page,
    needs_extractor,
)

from shorts_maker.captions import CAPTIONS_NAME, timecode
from shorts_maker.config import RUN_CONFIG_FILENAME, defaults, load_run_config
from shorts_maker.llm import LLMError
from shorts_maker.main import (
    EXIT_CONFIG_ERROR,
    EXIT_FLAGGED,
    EXIT_RUNTIME_ERROR,
    main,
)
from shorts_maker.run_context import LOG_FILENAME
from shorts_maker.schemas import (
    METADATA_SCHEMA,
    PROJECT_SCHEMA,
    SCENES_SCHEMA,
    SOURCE_SCHEMA,
    load_metadata,
    load_project,
    load_scenes,
    load_source,
)
from shorts_maker.shorts_types import DEFAULT_TYPE, available_types, get_type
from shorts_maker.tts import TTSError
from shorts_maker.video_renderer import OUTPUT_NAME, align

pytestmark = pytest.mark.usefixtures("stub_llm", "stub_tts")


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
    for flag in (
        "--topic",
        "--text-file",
        "--url",
        "--type",
        "--out",
        "--config",
        "--fail-on-flagged",
        "--verbose",
    ):
        assert flag in help_text
    assert f"default: {DEFAULT_TYPE}" in help_text
    assert "default: outputs" in help_text
    # required 인자에 무의미한 "(default: None)"이 붙지 않는다.
    assert "default: None" not in help_text


def test_missing_input_exits_with_code_two_naming_the_three_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """입력 세 갈래 중 정확히 하나가 필수다 (#94). 인자 오류는 argparse의 코드 2다."""
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2
    stderr = capsys.readouterr().err
    for flag in ("--topic", "--text-file", "--url"):
        assert flag in stderr


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


# --- run 설정 기록 (#92) -----------------------------------------------------


def test_run_records_the_resolved_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run 디렉터리가 자기 실행 설정을 들고 있다 — 재생성(#77)의 전제다."""
    monkeypatch.chdir(tmp_path)  # 실행 디렉터리에 config.yaml이 없는 상태
    output_root = tmp_path / "out"

    main(["--topic", "주제", "--out", str(output_root)])

    run_dir = run_dirs(output_root)[0]
    assert (run_dir / RUN_CONFIG_FILENAME).is_file()
    assert load_run_config(run_dir).data == defaults()


def test_run_record_holds_the_applied_value_not_the_config_path(tmp_path: Path) -> None:
    """`--config` 경로가 아니라 **적용된 값**이 남는다. 그 파일은 나중에 바뀔 수 있다."""
    config_path = tmp_path / "my.yaml"
    config_path.write_text("tts:\n  voice: ko-KR-InJoonNeural\n", encoding="utf-8")
    output_root = tmp_path / "out"

    main(["--topic", "주제", "--out", str(output_root), "--config", str(config_path)])

    run_dir = run_dirs(output_root)[0]
    config_path.write_text("tts:\n  voice: 나중에바뀐값\n", encoding="utf-8")

    assert load_run_config(run_dir).get("tts.voice") == "ko-KR-InJoonNeural"


def test_failed_run_still_records_the_config(tmp_path: Path, stub_llm: StubLLM) -> None:
    """산출물 중 가장 먼저 쓴다 — 어느 단계에서 멈춰도 그 실행의 값이 남아야 한다 (#36)."""
    stub_llm.reply(*[LLMError("실패")] * 3)
    output_root = tmp_path / "out"

    exit_code = main(["--topic", "주제", "--out", str(output_root)])

    assert exit_code == EXIT_RUNTIME_ERROR
    run_dir = run_dirs(output_root)[0]
    assert not (run_dir / get_type(DEFAULT_TYPE).content_artifact).exists()
    assert load_run_config(run_dir).data == defaults()


# --- 원문 파일 입력과 입력 기록 (#94) ---------------------------------------

ARTICLE_TITLE = "폭염 속 전력 수요 사상 최고"
ARTICLE_BODY = "20일 전력거래소는 최대 전력 수요가 사상 최고치를 기록했다고 밝혔다."
ARTICLE = f"{ARTICLE_TITLE}\n\n{ARTICLE_BODY}\n"


def write_article(directory: Path, text: str = ARTICLE) -> Path:
    path = directory / "기사.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_text_file_run_writes_the_source_record(tmp_path: Path) -> None:
    """입력 종류·파일 경로·글자 수·제목·수집 시각이 남는다 (PRD 7.1)."""
    article = write_article(tmp_path)
    output_root = tmp_path / "out"

    exit_code = main(["--text-file", str(article), "--out", str(output_root)])

    assert exit_code == 0
    record = load_source(run_dirs(output_root)[0] / SOURCE_SCHEMA.name)
    assert record["kind"] == "text_file"
    assert record["path"] == article.as_posix()
    assert record["title"] == ARTICLE_TITLE
    assert record["char_count"] == len(ARTICLE)
    assert record["collected_at"]


def test_a_topic_run_writes_no_source_record(tmp_path: Path) -> None:
    """`--topic` 경로에는 출처가 없다. 없는 것이 실패가 아니다 (PRD 6.2 표)."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    assert not (run_dirs(tmp_path)[0] / SOURCE_SCHEMA.name).exists()


def test_the_source_record_comes_after_the_config_record(tmp_path: Path) -> None:
    """자리는 `config.used.yaml` 다음, 콘텐츠 생성보다 앞이다 (#94)."""
    output_root = tmp_path / "out"

    main(["--text-file", str(write_article(tmp_path)), "--out", str(output_root)])

    log_text = (run_dirs(output_root)[0] / LOG_FILENAME).read_text(encoding="utf-8")
    order = [
        log_text.index(f"{name} 생성 완료")
        for name in (
            RUN_CONFIG_FILENAME,
            SOURCE_SCHEMA.name,
            get_type(DEFAULT_TYPE).content_artifact,
        )
    ]
    assert order == sorted(order)


def test_a_failed_run_still_keeps_the_source_record(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """어느 단계에서 멈춰도 "무엇을 입력으로 받았는지"가 남아야 한다 (#92와 같은 이유)."""
    stub_llm.reply(*[LLMError("실패")] * 3)
    output_root = tmp_path / "out"

    exit_code = main(["--text-file", str(write_article(tmp_path)), "--out", str(output_root)])

    assert exit_code == EXIT_RUNTIME_ERROR
    run_dir = run_dirs(output_root)[0]
    assert not (run_dir / get_type(DEFAULT_TYPE).content_artifact).exists()
    assert load_source(run_dir / SOURCE_SCHEMA.name)["title"] == ARTICLE_TITLE


def test_the_generator_gets_the_title_and_not_the_body(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """`ContentGenerator` 계약은 그대로다 — `topic` 자리에 제목이 간다 (PRD 14.1).

    본문을 소비하는 것은 요약·대본(#32)이고 그 경로를 쓰는 타입이 아직 없다.
    """
    main(["--text-file", str(write_article(tmp_path)), "--out", str(tmp_path / "out")])

    prompt = stub_llm.calls[0]["prompt"]
    assert ARTICLE_TITLE in prompt
    assert ARTICLE_BODY not in prompt


def test_the_run_log_says_the_body_did_not_reach_the_generator(tmp_path: Path) -> None:
    """조용히 버리면 "기사를 줬는데 왜 이 문제가 나왔지"의 답이 없다 (#94)."""
    output_root = tmp_path / "out"

    main(["--text-file", str(write_article(tmp_path)), "--out", str(output_root)])

    log_text = (run_dirs(output_root)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert f"{SOURCE_SCHEMA.name} 생성 완료" in log_text
    assert "원문을 콘텐츠 생성에 쓰지 않는다" in log_text
    assert f"{len(ARTICLE)}자" in log_text


def test_two_inputs_together_exit_with_code_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """둘을 함께 주면 어느 것이 콘텐츠의 근거인지 말할 수 없다."""
    output_root = tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "--topic",
                "주제",
                "--text-file",
                str(write_article(tmp_path)),
                "--out",
                str(output_root),
            ]
        )

    assert exit_info.value.code == 2
    assert "--text-file" in capsys.readouterr().err
    assert not output_root.exists()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("", "비어 있다"),
        ("   \n\n", "비어 있다"),
        ("가" * 100, "source.max_chars"),
    ],
    ids=("empty", "whitespace", "over-limit"),
)
def test_a_rejected_text_file_stops_before_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], stub_llm: StubLLM,
    body: str, expected: str,
) -> None:
    """설정 오류와 같은 종료 코드다. 빈 run 디렉터리를 남기지 않는다."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("source:\n  max_chars: 20\n", encoding="utf-8")
    output_root = tmp_path / "out"

    exit_code = main(
        [
            "--text-file",
            str(write_article(tmp_path, body)),
            "--out",
            str(output_root),
            "--config",
            str(config_path),
        ]
    )

    assert exit_code == EXIT_CONFIG_ERROR
    assert expected in capsys.readouterr().err
    assert not output_root.exists()
    assert stub_llm.call_count == 0


def test_a_missing_text_file_stops_before_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_root = tmp_path / "out"

    exit_code = main(
        ["--text-file", str(tmp_path / "없다.txt"), "--out", str(output_root)]
    )

    assert exit_code == EXIT_CONFIG_ERROR
    assert "찾을 수 없다" in capsys.readouterr().err
    assert not output_root.exists()


@needs_extractor
def test_url_run_writes_the_source_record(tmp_path: Path, stub_http: StubHTTP) -> None:
    """제목·본문·URL·접근 시각이 남는다 (#95, PRD 7.1). 위치 필드는 `url` 쪽이다."""
    stub_http.body = article_page()
    output_root = tmp_path / "out"

    exit_code = main(["--url", ARTICLE_URL, "--out", str(output_root)])

    assert exit_code == 0
    record = load_source(run_dirs(output_root)[0] / SOURCE_SCHEMA.name)
    assert record["kind"] == "url"
    assert record["url"] == ARTICLE_URL
    assert record["path"] is None
    assert record["title"] == ARTICLE_HEADLINE
    assert record["collected_at"]


@needs_extractor
def test_the_url_run_puts_the_page_on_the_console(
    tmp_path: Path, stub_http: StubHTTP
) -> None:
    """추출 글자 수와 제목을 사람이 본다 — 통과한 페이지를 가르는 것은 사람이다 (#95)."""
    stub_http.body = article_page()
    output_root = tmp_path / "out"

    main(["--url", ARTICLE_URL, "--out", str(output_root)])

    log_text = (run_dirs(output_root)[0] / LOG_FILENAME).read_text(encoding="utf-8")
    record = load_source(run_dirs(output_root)[0] / SOURCE_SCHEMA.name)
    assert ARTICLE_URL in log_text
    assert ARTICLE_HEADLINE in log_text
    assert f"{record['char_count']}자" in log_text


def test_a_rejected_url_stops_before_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], stub_http: StubHTTP,
    stub_llm: StubLLM,
) -> None:
    """거부는 설정 오류와 같은 종료 코드다. 빈 run 디렉터리도 LLM 호출도 남기지 않는다."""
    stub_http.error = urllib.error.HTTPError(ARTICLE_URL, 403, "Forbidden", Message(), None)
    output_root = tmp_path / "out"

    exit_code = main(["--url", ARTICLE_URL, "--out", str(output_root)])

    assert exit_code == EXIT_CONFIG_ERROR
    stderr = capsys.readouterr().err
    assert "403" in stderr
    assert "--text-file" in stderr  # 대안을 안내한다
    assert not output_root.exists()
    assert stub_llm.call_count == 0


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
    assert not (run_dir / METADATA_SCHEMA.name).exists()


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


def test_the_scene_template_leaves_the_narrated_durations_to_the_measurement(
    tmp_path: Path,
) -> None:
    """장면 템플릿은 낭독 장면의 목표치만 넣는다 (#12). 확정값은 실측에서 온다.

    파일에 남는 것은 타임라인 확정을 지난 상태이므로, 초안 상태를 보려면 템플릿 출력을
    직접 봐야 한다 — 여기서는 목표치가 확정값과 별개로 남아 있는 것으로 확인한다.
    """
    main(["--topic", "주제", "--out", str(tmp_path)])

    for scene in scenes_artifact(run_dirs(tmp_path)[0])["scenes"]:
        if scene.get("narrate"):
            assert scene["target_duration"] == 3.0
            assert scene["duration"] != scene["target_duration"]


def test_run_log_records_the_scene_artifact(tmp_path: Path) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert f"{SCENES_SCHEMA.name} 생성 완료" in log_text
    assert "장면 14개" in log_text


# --- 메타데이터 산출물 (#13) -------------------------------------------------


def test_run_writes_the_metadata(tmp_path: Path) -> None:
    exit_code = main(["--topic", "세계 지리 상식", "--out", str(tmp_path)])

    assert exit_code == 0
    metadata = load_metadata(run_dirs(tmp_path)[0] / METADATA_SCHEMA.name)
    assert len(metadata["titles"]) == 3
    assert metadata["type"] == DEFAULT_TYPE
    # `--topic` 경로에는 출처가 없다. 없는 것이 실패가 아니다 (PRD 6.2 표).
    assert metadata["source"] is None


def test_run_log_records_the_metadata_artifact(tmp_path: Path) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert f"{METADATA_SCHEMA.name} 생성 완료" in log_text


def test_metadata_generation_gets_the_scene_material(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """마지막 호출이 메타데이터다. 재료는 장면 문구뿐이고 `quiz.json`을 열지 않는다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    scenes = scenes_artifact(run_dirs(tmp_path)[0])
    assert scenes["scenes"][0]["text"] in stub_llm.calls[-1]["prompt"]


def test_metadata_failure_leaves_the_reason_in_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], stub_llm: StubLLM
) -> None:
    """콘텐츠와 장면은 이미 남았다. 어느 단계에서 멈췄는지는 run.log가 답한다."""
    fixed_run(stub_llm)
    stub_llm.reply(*[LLMError("메타데이터 응답이 스키마를 벗어났다")] * 3)

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == EXIT_RUNTIME_ERROR
    assert "생성 실패" in capsys.readouterr().err
    run_dir = run_dirs(tmp_path)[0]
    assert "메타데이터 생성 실패" in (run_dir / LOG_FILENAME).read_text(encoding="utf-8")
    assert not (run_dir / METADATA_SCHEMA.name).exists()
    assert (run_dir / SCENES_SCHEMA.name).exists()


# --- 검수 게이트 (#11) -------------------------------------------------------

QUESTION = "세계에서 가장 긴 강은?"
ANSWER = "나일강"


def fixed_run(
    stub_llm: StubLLM,
    *,
    given: str = ANSWER,
    certainty: float = 1.0,
    explanation: str = "해설입니다.",
) -> None:
    """문제 하나짜리 고정 응답 세트 — 생성 1회 + 재답변 2회 + 모호성 프로브 1회.

    기본값은 전원 일치·확신도 1.0이라 `verified`로 통과한다. `given`을 바꾸면 재답변이
    갈려 검증기가 `flagged`를 만들고, `certainty`를 낮추면 임계값이 자른다 — **#11의
    두 경로를 같은 입력에서 갈라 볼 수 있다.** `explanation`은 정답 장면의 자막이 되므로
    길이가 읽기 하한을 통해 확정 길이로 이어진다 (#16).
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
                    "explanation": explanation,
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


def test_flagged_content_still_produces_the_segments(
    tmp_path: Path, stub_llm: StubLLM, stub_tts: StubTTS
) -> None:
    """검수 경고는 진행을 멈추지 않는다 (#11). 합성은 그 경고 **뒤에** 있다."""
    fixed_run(stub_llm, given="아마존강")

    assert main(["--topic", "주제", "--out", str(tmp_path)]) == 0
    assert stub_tts.call_count == 2  # 낭독 장면 2개(문제 하나짜리 응답 세트)


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


# --- 낭독 세그먼트 (#15) -----------------------------------------------------


def segment_files(run_dir: Path) -> list[str]:
    return sorted(path.name for path in (run_dir / "audio").glob("seg-*.mp3"))


def test_run_writes_one_segment_per_narrated_scene(tmp_path: Path) -> None:
    """개수와 번호가 곧 계약이다 (PRD 7.5.2). 번호는 장면 배열 인덱스다.

    **파일 수를 provider 호출 수로 세지 않는다.** 같은 문장이 두 번 나오면 두 번째는
    캐시에서 복사되므로 호출은 한 번이고 파일은 둘이다.
    """
    exit_code = main(["--topic", "세계 지리 상식", "--out", str(tmp_path)])

    assert exit_code == 0
    run_dir = run_dirs(tmp_path)[0]
    scenes = scenes_artifact(run_dir)["scenes"]
    narrated = [index for index, scene in enumerate(scenes) if scene.get("narrate")]

    assert len(narrated) == 8  # 기본 4문제 → 질문·정답 각 4개
    assert segment_files(run_dir) == [f"seg-{index:03d}.mp3" for index in narrated]
    for index in narrated:
        assert scenes[index]["audio"] == f"audio/seg-{index:03d}.mp3"
        assert scenes[index]["audio_duration"] == STUB_SEGMENT_SEC


def test_scenes_without_narration_get_no_audio_fields(tmp_path: Path) -> None:
    """카운트다운 구간에 낭독 오디오가 붙으면 확정 검증이 반려한다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    for scene in scenes_artifact(run_dirs(tmp_path)[0])["scenes"]:
        if not scene.get("narrate"):
            assert "audio" not in scene and "audio_duration" not in scene


def test_run_log_records_the_segment_count_and_total_narration(tmp_path: Path) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert "세그먼트 8개 생성 완료" in log_text  # 기본 4문제 → 질문·정답 각 4개
    assert f"총 낭독 {8 * STUB_SEGMENT_SEC:.2f}초" in log_text


def test_synthesis_failure_exits_nonzero_and_names_the_stage_in_the_log(
    tmp_path: Path, stub_tts: StubTTS, capsys: pytest.CaptureFixture[str]
) -> None:
    """메타데이터는 이미 남았다 — 합성 실패가 그 산출물을 지우지 않는다 (PRD 6.2 표)."""
    stub_tts.error = TTSError("엔드포인트가 응답하지 않는다")

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == EXIT_RUNTIME_ERROR
    assert "생성 실패" in capsys.readouterr().err
    run_dir = run_dirs(tmp_path)[0]
    assert "세그먼트 합성 실패" in (run_dir / LOG_FILENAME).read_text(encoding="utf-8")
    assert (run_dir / METADATA_SCHEMA.name).exists()


# --- 확정 타임라인과 합성 트랙 (#16) ----------------------------------------


def test_the_written_scenes_are_finalized(tmp_path: Path) -> None:
    """자막·렌더는 확정 상태만 입력으로 받는다 (퀴즈 스펙 4장). 파일에서 받는 것을 본다."""
    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == 0
    load_scenes(run_dirs(tmp_path)[0] / SCENES_SCHEMA.name, finalized=True)


def test_narrated_scenes_get_a_measured_duration_and_an_offset(tmp_path: Path) -> None:
    """확정값이 앞선 장면 duration 누계 + lead_in과 맞아야 한다 (PRD 7.5.1)."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    scenes = scenes_artifact(run_dirs(tmp_path)[0])["scenes"]
    running = 0.0
    for scene in scenes:
        if scene.get("narrate"):
            assert scene["duration"] == pytest.approx(0.3 + STUB_SEGMENT_SEC + 0.5)
            assert scene["narration_offset"] == pytest.approx(running + 0.3)
        running += scene["duration"]


def test_fixed_length_scenes_keep_the_template_values(tmp_path: Path) -> None:
    """실측할 오디오가 없는 장면은 보정 대상이 아니다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    for scene in scenes_artifact(run_dirs(tmp_path)[0])["scenes"]:
        if scene["role"] == "countdown":
            assert scene["duration"] == scene["seconds"]
        elif not scene.get("narrate"):
            assert "narration_offset" not in scene


def test_run_writes_the_voice_track(tmp_path: Path, stub_ffmpeg: StubFFmpeg) -> None:
    """세그먼트가 1개 이상일 때의 산출물이다 (PRD 6.2 표)."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    assert (run_dirs(tmp_path)[0] / "voice.mp3").is_file()
    assert stub_ffmpeg.mix_count == 1


def test_a_scene_far_over_its_target_warns_and_still_exits_zero(
    tmp_path: Path, stub_llm: StubLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    """60자 해설이 읽기 하한에 걸려 목표 3.0초의 2배를 넘는 경우 (PRD 7.5.1)."""
    fixed_run(stub_llm, explanation="가" * 60)

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == 0
    console = capsys.readouterr().err
    assert "목표 3.00초" in console
    scenes = scenes_artifact(run_dirs(tmp_path)[0])["scenes"]
    answers = [scene for scene in scenes if scene["role"] == "answer"]
    assert answers[0]["duration"] == pytest.approx(0.9 + 60 / 12.0 + 0.5)


def test_run_log_records_the_finalized_total(tmp_path: Path) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])

    scenes = scenes_artifact(run_dirs(tmp_path)[0])["scenes"]
    total = sum(scene["duration"] for scene in scenes)
    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert f"{SCENES_SCHEMA.name} 확정 완료 — 총 {total:.2f}초" in log_text


def test_a_total_outside_the_range_warns_and_still_finishes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """3문제면 총합이 45초 미만이다. 경고는 나오고 종료 코드는 0이다 (PRD 6.3)."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("quiz:\n  question_count: 3\n", encoding="utf-8")
    output_root = tmp_path / "out"

    exit_code = main(
        ["--topic", "주제", "--out", str(output_root), "--config", str(config_path)]
    )

    assert exit_code == 0
    assert "목표 범위 45~60초" in capsys.readouterr().err
    assert (run_dirs(output_root)[0] / "voice.mp3").is_file()


def test_a_voice_track_failure_names_the_stage_in_the_log(
    tmp_path: Path, stub_ffmpeg: StubFFmpeg, capsys: pytest.CaptureFixture[str]
) -> None:
    """세그먼트와 메타데이터는 이미 남았다 — 합성 트랙 실패가 그것을 지우지 않는다."""
    stub_ffmpeg.mix_returncode = 1

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == EXIT_RUNTIME_ERROR
    assert "생성 실패" in capsys.readouterr().err
    run_dir = run_dirs(tmp_path)[0]
    assert "타임라인 확정 실패" in (run_dir / LOG_FILENAME).read_text(encoding="utf-8")
    assert (run_dir / METADATA_SCHEMA.name).exists()
    assert segment_files(run_dir)


def test_unknown_tts_provider_stops_before_spending_llm_calls(
    tmp_path: Path, stub_llm: StubLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    """이름 검증이 run 디렉터리보다 앞에 있다. 합성은 LLM 호출을 다 쓴 뒤에 시작한다."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tts:\n  provider: azure_speech\n", encoding="utf-8")
    output_root = tmp_path / "out"

    exit_code = main(
        ["--topic", "주제", "--out", str(output_root), "--config", str(config_path)]
    )

    assert exit_code == EXIT_CONFIG_ERROR
    assert stub_llm.call_count == 0
    assert not output_root.exists()


# --- 자막 (#17) --------------------------------------------------------------


def captions_artifact(run_dir: Path) -> str:
    """이번 run이 만든 `captions.srt`. 항상 생성되는 공통 산출물이다 (PRD 6.2 표)."""
    return (run_dir / CAPTIONS_NAME).read_text(encoding="utf-8")


def test_run_writes_the_captions(tmp_path: Path) -> None:
    exit_code = main(["--topic", "세계 지리 상식", "--out", str(tmp_path)])

    assert exit_code == 0
    srt = captions_artifact(run_dirs(tmp_path)[0])
    # 기본 4문제 → 후킹 1 + (질문·정답) × 4 + CTA 1. 카운트다운에는 문구가 없다.
    assert srt.count(" --> ") == 10


def test_the_captions_match_the_finalized_timeline(tmp_path: Path) -> None:
    """장면 템플릿의 목표치가 아니라 확정값에서 나와야 한다 (PRD 7.6)."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    run_dir = run_dirs(tmp_path)[0]
    scenes = scenes_artifact(run_dir)["scenes"]
    total = sum(scene["duration"] for scene in scenes)
    starts = [scene["narration_offset"] for scene in scenes if scene.get("narrate")]

    spans = re.findall(r"(\S+) --> (\S+)", captions_artifact(run_dir))
    # 낭독 장면의 큐는 오프셋에서 열고, 마지막 큐는 확정 총 길이에서 닫힌다 (cta가 마지막
    # 장면이고 문구가 있다).
    assert [start for start, _ in spans[1:]][: len(starts)] == [
        timecode(offset) for offset in starts
    ]
    assert spans[-1][1] == timecode(total)


def test_the_captions_carry_the_answer_and_its_explanation(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """해설은 낭독이 없어 자막이 유일한 전달 경로다 (D1 발주서 1장)."""
    fixed_run(stub_llm, explanation="세계에서 가장 긴 강이다.")

    main(["--topic", "주제", "--out", str(tmp_path)])

    srt = captions_artifact(run_dirs(tmp_path)[0])
    assert "나일강\n세계에서 가장 긴 강이다." in srt


def test_run_log_records_the_caption_artifact(tmp_path: Path) -> None:
    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert f"{CAPTIONS_NAME} 생성 완료 — 큐 10개" in log_text


def test_an_over_long_caption_line_warns_and_still_exits_zero(
    tmp_path: Path, stub_llm: StubLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    """자르면 원문을 잃는다. 경고만 하고 산출물은 그대로 남긴다 (#17)."""
    fixed_run(stub_llm, explanation="가" * 60)

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == 0
    assert "captions.max_lines" in capsys.readouterr().err
    assert "가" * 60 in captions_artifact(run_dirs(tmp_path)[0]).replace("\n", "")


def test_a_caption_failure_names_the_stage_in_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """앞 단계 산출물은 이미 남았다 — 자막 실패가 그것을 지우지 않는다."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("captions:\n  max_chars_per_line: 0\n", encoding="utf-8")
    output_root = tmp_path / "out"

    exit_code = main(
        ["--topic", "주제", "--out", str(output_root), "--config", str(config_path)]
    )

    assert exit_code == EXIT_RUNTIME_ERROR
    assert "생성 실패" in capsys.readouterr().err
    run_dir = run_dirs(output_root)[0]
    assert "자막 생성 실패" in (run_dir / LOG_FILENAME).read_text(encoding="utf-8")
    assert (run_dir / "voice.mp3").is_file()
    assert not (run_dir / CAPTIONS_NAME).exists()


# --- 프로젝트 파일과 렌더 (#19) ----------------------------------------------


def project_artifact(run_dir: Path) -> dict:
    """이번 run이 만든 `project.json`. 항상 생성되는 공통 산출물이다 (PRD 6.2 표)."""
    return json.loads((run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))


def test_run_writes_the_project_file(tmp_path: Path) -> None:
    exit_code = main(["--topic", "세계 지리 상식", "--out", str(tmp_path)])

    assert exit_code == 0
    run_dir = run_dirs(tmp_path)[0]
    project = load_project(run_dir / PROJECT_SCHEMA.name)
    assert project["render"] == {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "output": OUTPUT_NAME,
        "caption_style": "impact_yellow",
        "font_path": None,
        "cta_punch": "구독 · 좋아요",
        "cta_tail": "매일 새 상식 퀴즈",
        "caption_onset_sec": 0.90,
        # 생성 직후에는 사람이 얹은 편집이 없다 (#82).
        "scene_overrides": [],
    }
    # 기본 4문제 → 낭독 장면이 있으므로 합성 트랙을 가리킨다.
    assert project["audio"]["voice"] == "voice.mp3"
    assert project["background"] == {"kind": "preset", "value": "deep_navy"}


def test_the_project_file_points_at_the_finalized_scenes(tmp_path: Path) -> None:
    """장면 배열을 복사하지 않는다 (PRD 7.4.1). 가리키는 파일은 확정 상태여야 한다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    run_dir = run_dirs(tmp_path)[0]
    load_scenes(run_dir / project_artifact(run_dir)["scenes"], finalized=True)


def test_run_writes_the_final_video(tmp_path: Path, stub_ffmpeg: StubFFmpeg) -> None:
    """렌더 성공 시의 산출물이다 (PRD 6.2 표). 인코딩 자체는 test_video_renderer.py가 본다."""
    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == 0
    assert (run_dirs(tmp_path)[0] / OUTPUT_NAME).is_file()
    assert stub_ffmpeg.render_count == 1


def test_the_render_length_comes_from_the_finalized_durations(
    tmp_path: Path, stub_ffmpeg: StubFFmpeg
) -> None:
    """장면 템플릿의 목표치가 아니라 확정값에서 나와야 한다 (PRD 7.5.1)."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    scenes = scenes_artifact(run_dirs(tmp_path)[0])
    expected = align(scenes).total_sec
    command = stub_ffmpeg.render_commands[0]
    lengths = [command[index + 1] for index, item in enumerate(command) if item == "-t"]
    assert lengths[-1] == f"{expected:.3f}"


def test_run_log_records_the_project_artifact_and_the_render_command(
    tmp_path: Path,
) -> None:
    """명령 전문이 run.log에 있어야 실패한 렌더를 손으로 재현할 수 있다."""
    main(["--topic", "주제", "--out", str(tmp_path)])

    log_text = (run_dirs(tmp_path)[0] / LOG_FILENAME).read_text(encoding="utf-8")

    assert f"{PROJECT_SCHEMA.name} 생성 완료" in log_text
    assert f"{OUTPUT_NAME} 생성 완료" in log_text
    assert "렌더 명령 ffmpeg" in log_text


def test_a_render_failure_keeps_the_project_file_and_names_the_stage(
    tmp_path: Path, stub_ffmpeg: StubFFmpeg, capsys: pytest.CaptureFixture[str]
) -> None:
    """`project.json`은 항상, `final_short.mp4`는 렌더 성공 시다 (PRD 6.2 표).
    렌더가 실패한 run에도 값을 고쳐 다시 돌릴 파일이 남아야 한다."""
    stub_ffmpeg.render_returncode = 1

    exit_code = main(["--topic", "주제", "--out", str(tmp_path)])

    assert exit_code == EXIT_RUNTIME_ERROR
    assert "생성 실패" in capsys.readouterr().err
    run_dir = run_dirs(tmp_path)[0]
    log_text = (run_dir / LOG_FILENAME).read_text(encoding="utf-8")
    assert "렌더 실패" in log_text
    # 명령 전문과 stderr가 짝으로 남아야 손으로 재현할 수 있다.
    assert "렌더 명령 ffmpeg" in log_text
    assert "가짜 FFmpeg 실패" in log_text
    assert (run_dir / PROJECT_SCHEMA.name).is_file()
    assert (run_dir / CAPTIONS_NAME).is_file()


def test_flagged_content_still_gets_rendered(
    tmp_path: Path, stub_llm: StubLLM
) -> None:
    """검수 경고는 진행을 멈추지 않는다 (#11). 렌더는 그 경고 **뒤에** 있다."""
    fixed_run(stub_llm, given="아마존강")

    assert main(["--topic", "주제", "--out", str(tmp_path)]) == 0
    assert (run_dirs(tmp_path)[0] / OUTPUT_NAME).is_file()
