"""run 디렉터리 생성 규칙 검증."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from shorts_maker import PACKAGE_LOGGER
from shorts_maker.run_context import (
    LOG_FILENAME,
    MAX_SAME_SECOND_RUNS,
    create_run_dir,
    run_logging,
    start_run,
)

FIXED_TIME = datetime(2026, 8, 6, 15, 30, 12)


def test_run_dir_uses_timestamp_name(tmp_path: Path) -> None:
    run_dir = create_run_dir(tmp_path, FIXED_TIME)

    assert run_dir == tmp_path / "run-20260806-153012"
    assert run_dir.is_dir()


def test_output_root_is_created_if_missing(tmp_path: Path) -> None:
    output_root = tmp_path / "nested" / "outputs"

    run_dir = create_run_dir(output_root, FIXED_TIME)

    assert run_dir.parent == output_root


def test_same_second_runs_get_distinct_dirs(tmp_path: Path) -> None:
    first = create_run_dir(tmp_path, FIXED_TIME)
    second = create_run_dir(tmp_path, FIXED_TIME)
    third = create_run_dir(tmp_path, FIXED_TIME)

    assert len({first, second, third}) == 3
    assert second.name == "run-20260806-153012-2"
    assert third.name == "run-20260806-153012-3"


def test_existing_run_contents_are_untouched(tmp_path: Path) -> None:
    first = create_run_dir(tmp_path, FIXED_TIME)
    artifact = first / "scenes.json"
    artifact.write_text('{"kept": true}', encoding="utf-8")

    create_run_dir(tmp_path, FIXED_TIME)

    assert artifact.read_text(encoding="utf-8") == '{"kept": true}'


def test_raises_when_same_second_limit_is_reached(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shorts_maker.run_context.MAX_SAME_SECOND_RUNS", 2)
    create_run_dir(tmp_path, FIXED_TIME)
    create_run_dir(tmp_path, FIXED_TIME)

    with pytest.raises(RuntimeError, match="run 디렉터리가"):
        create_run_dir(tmp_path, FIXED_TIME)


def test_start_run_points_log_into_run_dir(tmp_path: Path) -> None:
    context = start_run(tmp_path, FIXED_TIME)

    assert context.started_at == FIXED_TIME
    assert context.log_path == context.run_dir / LOG_FILENAME
    assert context.run_dir.is_dir()


def test_run_logging_removes_handlers_on_exit(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    # pytest의 로그 캡처 핸들러가 같은 로거에 붙어 있으므로 전체 개수로 판단할 수 없다.
    # run_logging이 추가한 핸들러만 차집합으로 추적한다.
    before = list(logging.getLogger(PACKAGE_LOGGER).handlers)

    with run_logging(log_path) as logger:
        logger.info("첫 번째 run")
        added = [handler for handler in logger.handlers if handler not in before]

    assert len(added) == 2, "콘솔 핸들러와 파일 핸들러가 붙어야 한다"
    assert [handler for handler in added if handler in logger.handlers] == []
    assert "첫 번째 run" in log_path.read_text(encoding="utf-8")


def test_second_run_log_does_not_leak_into_first(tmp_path: Path) -> None:
    first_log = tmp_path / "first.log"
    second_log = tmp_path / "second.log"

    with run_logging(first_log) as logger:
        logger.info("첫 번째 run")
    with run_logging(second_log) as logger:
        logger.info("두 번째 run")

    assert "두 번째 run" not in first_log.read_text(encoding="utf-8")


def test_same_second_limit_constant_is_positive() -> None:
    assert MAX_SAME_SECOND_RUNS > 0
