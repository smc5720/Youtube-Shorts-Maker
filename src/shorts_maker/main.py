"""CLI 진입점.

이번 단계에서 하는 일은 입력을 검증하고 run 디렉터리와 실행 로그를 만드는 것까지다.
생성 파이프라인은 아직 붙지 않았다.

- `--url` / `--text-file` 입력은 #31에서 추가한다. 그때 `--topic`은 배타 그룹의 한 갈래가 된다.
- `config.yaml` 로딩은 #6에서 붙는다.
- `--type`은 값을 검증해 기록만 하고, 타입 플러그인 디스패치는 #8에서 붙는다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .run_context import RunContext, run_logging, start_run
from .shorts_types import DEFAULT_TYPE, SUPPORTED_TYPES

DEFAULT_OUTPUT_ROOT = Path("outputs")

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
# 인자 오류는 argparse가 종료 코드 2로 처리한다.


def _force_utf8_console() -> None:
    """콘솔 출력을 UTF-8로 고정한다.

    Windows에서 출력을 파이프나 파일로 리다이렉트하면 stdout/stderr이 로케일 인코딩
    (여기서는 cp949)을 쓴다. 로그 메시지가 한국어이므로 인코딩할 수 없는 문자가 섞이면
    `UnicodeEncodeError`가 나거나 `\\u2014` 같은 이스케이프가 그대로 출력된다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # 리다이렉트된 스트림이 TextIOWrapper가 아닐 수 있다 (테스트 캡처 등)
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass  # 인코딩을 못 바꿔도 실행 자체를 막지는 않는다


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shorts-maker",
        description="주제를 입력받아 세로형 쇼츠 초안을 생성한다.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--topic",
        required=True,
        metavar="주제",
        # SUPPRESS를 쓰면 --help에 의미 없는 "(default: None)"이 붙지 않는다.
        # required=True이므로 파싱을 통과하면 항상 값이 있다.
        default=argparse.SUPPRESS,
        help="쇼츠로 만들 주제 한 줄",
    )
    parser.add_argument(
        "--type",
        dest="shorts_type",
        choices=SUPPORTED_TYPES,
        default=DEFAULT_TYPE,
        help="쇼츠 타입",
    )
    parser.add_argument(
        "--out",
        dest="output_root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        metavar="경로",
        help="run 디렉터리를 만들 상위 경로",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="디버그 로그까지 출력",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run(args: argparse.Namespace) -> RunContext:
    """run 디렉터리를 만들고 실행 정보를 로그에 남긴다."""
    context = start_run(args.output_root, datetime.now())

    with run_logging(context.log_path, verbose=args.verbose) as logger:
        logger.info("run 시작 %s", context.started_at.isoformat(timespec="seconds"))
        logger.info("run 디렉터리 %s", context.run_dir)
        logger.info("타입 %s", args.shorts_type)
        logger.info("주제 %s", args.topic)
        logger.debug("python %s", sys.version.split()[0])
        logger.debug("출력 상위 경로 %s", args.output_root.resolve())
        logger.info("생성 파이프라인은 아직 연결되지 않았다. 산출물 없이 종료한다")

    return context


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        run(args)
    except OSError as error:
        # 쓰기 권한이 없거나 경로가 파일인 경우. 스택트레이스 대신 원인을 남긴다.
        print(f"run 디렉터리를 만들 수 없다: {error}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
