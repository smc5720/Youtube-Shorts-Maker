"""CLI 동작 검증 — 이슈 #5의 완료 조건에 대응한다."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from shorts_maker.main import EXIT_CONFIG_ERROR, main
from shorts_maker.run_context import LOG_FILENAME
from shorts_maker.shorts_types import DEFAULT_TYPE, SUPPORTED_TYPES


def run_dirs(output_root: Path) -> list[Path]:
    return sorted(p for p in output_root.iterdir() if p.is_dir())


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
    for flag in ("--topic", "--type", "--out", "--config", "--verbose"):
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
    for supported in SUPPORTED_TYPES:
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

    assert exit_code == 1
    assert "run 디렉터리를 만들 수 없다" in capsys.readouterr().err
