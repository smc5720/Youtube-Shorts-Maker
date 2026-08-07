"""run 출력 디렉터리 생성과 실행 로그 설정.

한 번의 실행은 `outputs/run-{timestamp}/` 하나를 소유한다. **기존 run을 덮어쓰지 않는 것이
불변식이다** — 같은 초에 두 번 실행되면 `-2`, `-3` 접미사를 붙인다. 산출물 검수와 재실행
비교(PRD 2장, 13장)가 이전 run이 남아 있는 것에 의존하기 때문이다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import PACKAGE_LOGGER

RUN_DIR_PREFIX = "run-"
TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
LOG_FILENAME = "run.log"

# 같은 초에 이만큼 실행되는 것은 정상 사용이 아니다. 무한 루프를 막는 상한.
MAX_SAME_SECOND_RUNS = 1000


@dataclass(frozen=True)
class RunContext:
    """이번 실행이 산출물을 쓰는 위치."""

    run_dir: Path
    log_path: Path
    started_at: datetime


def create_run_dir(output_root: Path, started_at: datetime) -> Path:
    """`output_root` 아래에 이번 run 전용 디렉터리를 새로 만든다.

    이미 있는 디렉터리를 반환하지 않는다. `mkdir(exist_ok=False)`로 만들기 때문에
    두 프로세스가 같은 초에 시작해도 서로의 run을 침범하지 않는다.
    """
    output_root.mkdir(parents=True, exist_ok=True)
    base = f"{RUN_DIR_PREFIX}{started_at.strftime(TIMESTAMP_FORMAT)}"

    for attempt in range(1, MAX_SAME_SECOND_RUNS + 1):
        name = base if attempt == 1 else f"{base}-{attempt}"
        candidate = output_root / name
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate

    raise RuntimeError(
        f"{output_root}에 {base}로 시작하는 run 디렉터리가 "
        f"{MAX_SAME_SECOND_RUNS}개 있다. 오래된 run을 정리한 뒤 다시 실행한다."
    )


def start_run(output_root: Path, started_at: datetime | None = None) -> RunContext:
    """run 디렉터리를 만들고 컨텍스트를 돌려준다."""
    started_at = started_at or datetime.now()
    run_dir = create_run_dir(output_root, started_at)
    return RunContext(
        run_dir=run_dir,
        log_path=run_dir / LOG_FILENAME,
        started_at=started_at,
    )


def write_artifact(run_dir: Path, name: str, data: Any) -> Path:
    """산출물 JSON을 run 디렉터리에 쓴다.

    **사람이 읽고 고치는 파일이다** (퀴즈 스펙 3.1: `quiz.json`이 검수 원본). 그래서
    들여쓰기를 넣고 한글을 이스케이프하지 않는다. 파일명은 호출자가 준다 — 타입 전용
    산출물의 이름을 이 모듈이 알면 공통 파이프라인이 타입을 알게 된다 (퀴즈 스펙 1.1).
    """
    path = run_dir / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


@contextmanager
def run_logging(log_path: Path, *, verbose: bool = False) -> Iterator[logging.Logger]:
    """실행 로그를 콘솔과 `run.log`에 동시에 남긴다.

    **`run.log`는 항상 DEBUG까지 남기고, `verbose`는 콘솔에만 영향을 준다.** run.log는
    사후 검수·재현용 기록이므로(PRD 2장, 13장) 사용자가 그때 `--verbose`를 붙였는지에
    따라 내용이 달라지면 안 된다. 해석된 설정 전체처럼 양이 많아 콘솔에는 부담스럽지만
    기록으로는 반드시 필요한 항목이 여기 해당한다.

    핸들러를 종료 시 반드시 떼어낸다. 한 프로세스에서 여러 run을 돌리면(테스트, 이후의
    앱 백엔드) 핸들러가 누적되어 로그가 이전 run의 파일로도 새어 나간다.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    handlers: list[logging.Handler] = [file_handler, console_handler]
    for handler in handlers:
        logger.addHandler(handler)
    try:
        yield logger
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
