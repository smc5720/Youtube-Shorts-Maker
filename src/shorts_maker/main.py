"""CLI 진입점.

이번 단계에서 하는 일은 입력을 검증하고 run 디렉터리와 실행 로그를 만드는 것까지다.
생성 파이프라인은 아직 붙지 않았다.

- `--url` / `--text-file` 입력은 #31에서 추가한다. 그때 `--topic`은 배타 그룹의 한 갈래가 된다.
- `--type`은 레지스트리에서 타입 선언을 찾는 데까지만 쓴다. 생성기·장면 템플릿을 실제로
  호출하는 것은 #9·#12가 스텁을 채운 뒤다.
- 설정 키별 CLI 플래그(`--voice` 등)는 그 값을 실제로 쓰는 이슈가 추가한다. 여기서는
  `--config`와 우선순위 규칙만 제공한다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import DEFAULT_CONFIG_FILENAME, Config, ConfigError, load_config
from .run_context import RunContext, run_logging, start_run
from .shorts_types import (
    DEFAULT_TYPE,
    ShortsType,
    ShortsTypeError,
    available_types,
    get_type,
)

DEFAULT_OUTPUT_ROOT = Path("outputs")

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
# 인자 오류는 argparse가 종료 코드 2로 처리한다.
EXIT_CONFIG_ERROR = 3


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
        # 선택지도 --help 출력도 레지스트리에서 나온다. 타입을 등록하면 따라온다.
        choices=available_types(),
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
        "--config",
        dest="config_path",
        type=Path,
        # --topic과 같은 이유로 SUPPRESS를 쓴다. 기본 동작은 아래 help 문구가 설명하므로
        # "(default: None)"이 붙으면 노이즈다.
        default=argparse.SUPPRESS,
        metavar="경로",
        help=f"설정 파일 경로 (미지정 시 실행 디렉터리의 {DEFAULT_CONFIG_FILENAME}, 없으면 기본값)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="디버그 로그를 콘솔에도 출력 (run.log에는 항상 남는다)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _callable_name(target: object) -> str:
    """로그에 남길 이름. 어느 구현이 물렸는지가 run.log만 보고 드러나야 한다."""
    qualname = getattr(target, "__qualname__", None) or type(target).__qualname__
    module = getattr(target, "__module__", None)
    return f"{module}.{qualname}" if module else qualname


def run(args: argparse.Namespace, config: Config, shorts_type: ShortsType) -> RunContext:
    """run 디렉터리를 만들고 실행 정보를 로그에 남긴다."""
    context = start_run(args.output_root, datetime.now())

    with run_logging(context.log_path, verbose=args.verbose) as logger:
        logger.info("run 시작 %s", context.started_at.isoformat(timespec="seconds"))
        logger.info("run 디렉터리 %s", context.run_dir)
        logger.info("타입 %s", shorts_type.name)
        logger.info("주제 %s", args.topic)
        logger.debug("콘텐츠 생성기 %s", _callable_name(shorts_type.generator))
        logger.debug("장면 템플릿 %s", _callable_name(shorts_type.scene_template))
        # 타입 전용 산출물 목록을 남긴다. 나중에 "왜 script.txt가 없지"를 run.log로 답한다.
        logger.debug("타입 전용 산출물 %s", ", ".join(shorts_type.artifacts()))
        if config.source is None:
            logger.info("설정 파일 없음 — 기본값 사용")
        else:
            logger.info("설정 파일 %s", config.source)
        # 해석된 설정 전체를 남긴다. 나중에 "왜 저 목소리로 나왔지"를 run.log만 보고
        # 답할 수 있어야 한다. 콘솔에는 --verbose일 때만 나온다.
        for key, value in config.flatten():
            logger.debug("설정 %s = %s", key, value)
        logger.debug("python %s", sys.version.split()[0])
        logger.debug("출력 상위 경로 %s", args.output_root.resolve())
        logger.info("생성 파이프라인은 아직 연결되지 않았다. 산출물 없이 종료한다")

    return context


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    # 설정 검증을 run 디렉터리 생성보다 먼저 한다. 오타 하나 때문에 빈 run 디렉터리가
    # 쌓이면 검수할 산출물과 구분되지 않는다.
    try:
        config = load_config(getattr(args, "config_path", None))
    except ConfigError as error:
        print(f"설정 오류:\n{error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # 설정과 같은 이유로 run 디렉터리보다 먼저 검증한다. 등록되지 않은 타입이나 깨진
    # 타입 선언은 산출물을 하나도 만들 수 없으므로 빈 run 디렉터리를 남기지 않는다.
    try:
        shorts_type = get_type(args.shorts_type)
    except ShortsTypeError as error:
        print(f"쇼츠 타입 오류:\n{error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        run(args, config, shorts_type)
    except OSError as error:
        # 쓰기 권한이 없거나 경로가 파일인 경우. 스택트레이스 대신 원인을 남긴다.
        print(f"run 디렉터리를 만들 수 없다: {error}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
