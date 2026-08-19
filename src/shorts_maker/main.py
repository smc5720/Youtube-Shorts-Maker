"""CLI 진입점.

이번 단계에서 하는 일은 입력을 검증하고, run 디렉터리를 만들고, **타입의 콘텐츠 생성기를
불러 그 산출물을 쓰고, 검수가 필요한 항목을 경고한 뒤, 타입의 장면 템플릿으로 `scenes.json`
초안을 쓰고, 그 초안에서 `metadata.json`을 만들고, 낭독 장면의 세그먼트 오디오를 합성한 뒤
그 실측 길이로 타임라인을 확정해 `voice.mp3`와 `captions.srt`를 만들고, `project.json`을
남긴 다음 `final_short.mp4`까지 렌더하는 것**이다. 화면에 그리는 오버레이는 #20~#23이
붙인다.

- 검수 경고는 "콘텐츠 검증이 끝나고 파이프라인이 계속 진행하는 지점"에 있다. 렌더가 뒤에
  붙어도 그 앞자리에 그대로 남는다. **기본 동작은 경고 후 진행이다** — MVP의 검수 주체는
  사람이고 사람은 산출물이 있어야 검수한다 (PRD 2장). 멈추는 것은 `--fail-on-flagged`를
  지정한 쪽(배치 실행, 이후의 앱 자동화)의 선택이다.
- 무엇이 검수 대상인지는 타입이 정한다. 파이프라인은 `ContentIssue` 세 칸을 읽어 옮길 뿐
  `confidence`도 임계값도 모른다 (퀴즈 스펙 1.1).

- `--url` / `--text-file` 입력은 #31에서 추가한다. 그때 `--topic`은 배타 그룹의 한 갈래가 된다.
- 산출물 파일명은 타입 선언(`content_artifact`)에서 나온다. 여기 `quiz.json`을 적으면
  공통 파이프라인이 타입을 알게 되고 `tests/test_type_boundary.py`가 깨진다.
- 산출물 검증은 생성기가 한다. 파이프라인은 타입 전용 스키마를 열 수 없다 (퀴즈 스펙 1.1).
- 설정 키별 CLI 플래그(`--voice` 등)는 그 값을 실제로 쓰는 이슈가 추가한다. 여기서는
  `--config`와 우선순위 규칙만 제공한다.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import (
    __version__,
    captions,
    metadata_generator,
    narration,
    project,
    timeline,
    video_renderer,
)
from .captions import CaptionError
from .config import DEFAULT_CONFIG_FILENAME, Config, ConfigError, load_config
from .llm import LLMError, validate_providers
from .run_context import (
    RunContext,
    run_logging,
    start_run,
    write_artifact,
    write_text_artifact,
)
from .schemas import METADATA_SCHEMA, PROJECT_SCHEMA, SCENES_SCHEMA, SchemaError
from .shorts_types import (
    DEFAULT_TYPE,
    ContentIssue,
    ShortsType,
    ShortsTypeError,
    available_types,
    get_type,
)
from .timeline import TimelineError
from .tts import TTSError, create_synthesizer, validate_tts_provider
from .video_renderer import RenderError

DEFAULT_OUTPUT_ROOT = Path("outputs")

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
# 인자 오류는 argparse가 종료 코드 2로 처리한다.
EXIT_CONFIG_ERROR = 3
EXIT_FLAGGED = 4
"""`--fail-on-flagged`로 멈췄다. **실패와 구분되는 코드다** — 산출물은 정상적으로 남아
있고 사람의 검수를 기다리는 상태다. 배치 스크립트가 이 둘을 같은 코드로 받으면 "생성이
깨졌다"와 "검수가 필요하다"에 같은 대응을 하게 된다."""


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
        "--fail-on-flagged",
        dest="fail_on_flagged",
        action="store_true",
        help="검수 필요 항목이 하나라도 있으면 0이 아닌 종료 코드로 멈춘다 (기본은 경고 후 진행)",
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


def run(
    args: argparse.Namespace, config: Config, shorts_type: ShortsType
) -> tuple[RunContext, list[ContentIssue]]:
    """run 디렉터리를 만들고 실행 정보를 로그에 남긴다. 검수 필요 항목을 함께 돌려준다."""
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

        logger.info("콘텐츠 생성 중 — 모델 호출은 수십 초가 걸린다")
        try:
            content = shorts_type.generator(topic=args.topic, config=config)
        except (LLMError, SchemaError) as error:
            # run.log에 원인을 남기고 넘긴다. 콘솔 메시지는 main이 낸다.
            logger.error("콘텐츠 생성 실패 — %s", error)
            raise

        # 판정을 산출물보다 먼저 확정한다. 경고만 하고 파일에는 남기지 않으면 콘솔을
        # 놓친 사람과 앱(#30)이 다른 상태를 보게 된다.
        issues = shorts_type.review(content, config=config)

        path = write_artifact(context.run_dir, shorts_type.content_artifact, content)
        logger.info("%s 생성 완료", path.name)

        # 게이트 자리. 렌더는 이 뒤에 있고, 경고가 그것을 막지 않는다 (#24가 확인한다).
        _warn_about(logger, issues, artifact=path.name)

        # 장면 분할. 여기부터 뒤는 타입을 모른다 — 공통 파이프라인이 읽는 것은
        # `scenes.json` 하나다 (퀴즈 스펙 1.1, PRD 7.4.1).
        try:
            scenes = shorts_type.scene_template(content, config=config)
        except SchemaError as error:
            # 초안이 계약을 어겼다는 뜻이므로 원인은 타입의 장면 템플릿에 있다.
            logger.error("장면 구성 실패 — %s", error)
            raise
        # 파일명은 스키마가 확정한다. `scenes.json`은 타입과 무관한 공통 산출물이다.
        scenes_path = write_artifact(context.run_dir, SCENES_SCHEMA.name, scenes)
        logger.info("%s 생성 완료 — 장면 %d개", scenes_path.name, len(scenes["scenes"]))

        # 퀴즈 스펙 4장의 파이프라인 그림은 메타데이터를 렌더 뒤에 두지만, 입력은 장면
        # 초안이고 오디오 길이·렌더 결과와 무관하다 (PRD 7.8). 렌더가 붙어도 여기서
        # 옮길 이유가 없다 — 렌더가 실패해도 남아야 하는 산출물이다 (PRD 6.2 표).
        logger.info("메타데이터 생성 중")
        try:
            metadata = metadata_generator.generate(scenes, config=config)
        except (LLMError, SchemaError) as error:
            logger.error("메타데이터 생성 실패 — %s", error)
            raise
        metadata_path = write_artifact(context.run_dir, METADATA_SCHEMA.name, metadata)
        logger.info("%s 생성 완료", metadata_path.name)

        # 낭독 세그먼트. 여기서 채우는 것은 `audio`·`audio_duration`뿐이고, 그 실측값으로
        # `duration`과 `narration_offset`을 확정하는 것은 바로 아래 타임라인 단계다.
        # 메타데이터보다 뒤에 두는 이유는 위와 같다 — 합성이 실패해도 남아야 하는 산출물이
        # 앞에 있어야 한다.
        logger.info("낭독 세그먼트 합성 중 — 낭독 장면마다 오디오 파일 하나를 만든다")
        try:
            scenes = narration.synthesize_segments(
                scenes, run_dir=context.run_dir, synthesizer=create_synthesizer(config)
            )
        except (TTSError, SchemaError) as error:
            logger.error("세그먼트 합성 실패 — %s", error)
            raise

        durations = [
            scene["audio_duration"] for scene in scenes["scenes"] if scene.get("narrate")
        ]
        if durations:
            write_artifact(context.run_dir, SCENES_SCHEMA.name, scenes)
            logger.info(
                "세그먼트 %d개 생성 완료 — 총 낭독 %.2f초, %s 갱신",
                len(durations),
                sum(durations),
                scenes_path.name,
            )
        else:
            # 낭독 장면이 없으면 세그먼트도 없다 (PRD 6.2 표). 길이 확정은 그래도 해야
            # 하므로 아래 단계를 건너뛰지 않는다.
            logger.info("낭독 장면이 없어 세그먼트를 만들지 않았다")

        # 타임라인 확정. 여기서 `scenes.json`이 확정 상태가 되고, 이후 단계(자막·렌더)는
        # 확정 상태만 입력으로 받는다 (퀴즈 스펙 4장).
        logger.info("타임라인 확정 중 — 실측 길이로 duration과 낭독 오프셋을 정한다")
        try:
            scenes = timeline.finalize(scenes, run_dir=context.run_dir, config=config)
        except (TimelineError, SchemaError) as error:
            logger.error("타임라인 확정 실패 — %s", error)
            raise
        write_artifact(context.run_dir, SCENES_SCHEMA.name, scenes)
        logger.info(
            "%s 확정 완료 — 총 %.2f초",
            scenes_path.name,
            sum(scene["duration"] for scene in scenes["scenes"]),
        )

        # 자막. 여기부터는 확정 상태만 읽는다 — `captions.build`가 입구에서 확정 검증을
        # 다시 하므로, 확정을 거치지 않은 장면 목록이 타임코드로 새어 들어갈 길이 없다.
        try:
            cues = captions.build(scenes, config=config)
        except (CaptionError, SchemaError) as error:
            logger.error("자막 생성 실패 — %s", error)
            raise
        captions_path = write_text_artifact(
            context.run_dir, captions.CAPTIONS_NAME, captions.render(cues)
        )
        logger.info("%s 생성 완료 — 큐 %d개", captions_path.name, len(cues))

        # 렌더러의 입력 계약이다 (PRD 7.10). **렌더보다 먼저 쓴다** — `project.json`은 항상
        # 생성되고 `final_short.mp4`는 렌더 성공 시에만 생성되므로(PRD 6.2 표), 렌더가
        # 실패한 run에도 값을 고쳐 다시 돌릴 파일이 남아야 한다.
        try:
            project_data = project.build(
                scenes, config=config, run_dir=context.run_dir
            )
        except SchemaError as error:
            logger.error("프로젝트 파일 생성 실패 — %s", error)
            raise
        project_path = write_artifact(context.run_dir, PROJECT_SCHEMA.name, project_data)
        logger.info(
            "%s 생성 완료 — 배경 %s",
            project_path.name,
            project_data["background"]["value"],
        )

        logger.info("렌더 중 — 1080x1920 30fps 인코딩은 수십 초가 걸린다")
        try:
            output = video_renderer.render(
                project_data, scenes, run_dir=context.run_dir
            )
        except (RenderError, SchemaError) as error:
            logger.error("렌더 실패 — %s", error)
            raise
        logger.info("%s 생성 완료", output.name)

    return context, issues


def _warn_about(
    logger: logging.Logger, issues: list[ContentIssue], *, artifact: str
) -> None:
    """검수 필요 항목을 경고로 남긴다.

    **`WARNING`이라 `--verbose` 여부와 무관하게 콘솔과 `run.log` 양쪽에 남는다.**
    run.log는 사후 검수 기록이므로(`run_context.py`) 그때 `--verbose`를 붙였는지에 따라
    경고가 사라지면 안 된다.
    """
    if not issues:
        return

    logger.warning("검수 필요 %d건 — %s의 verify를 확인한 뒤 사용한다", len(issues), artifact)
    for issue in issues:
        logger.warning("  %s: %s — %s", issue.subject, issue.summary, issue.reason)


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

    # 타입만 아는 설정 조건(퀴즈의 문제 수 3~5 등)도 여기서 본다. 값 하나가 범위를
    # 벗어난 것 때문에 빈 run 디렉터리가 쌓이면 검수할 산출물과 구분되지 않는다.
    try:
        shorts_type.check_config(config)
    except ConfigError as error:
        print(f"설정 오류:\n{error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # 같은 이유로 여기서 본다. 등록되지 않은 provider 이름은 설정 오타이고, 첫 LLM 호출까지
    # 가서야 드러나면 그 전에 만든 run 디렉터리가 남는다.
    try:
        validate_providers(config)
    except LLMError as error:
        print(f"LLM provider 오류:\n{error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    # 같은 이유로 이름 검증이 여기 있다. 합성은 콘텐츠 생성·메타데이터가 끝난 뒤에야
    # 시작하므로, provider 오타를 그때 발견하면 LLM 호출 비용을 다 쓰고 버리게 된다.
    try:
        validate_tts_provider(config)
    except TTSError as error:
        print(f"TTS provider 오류:\n{error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        context, issues = run(args, config, shorts_type)
    except OSError as error:
        # 쓰기 권한이 없거나 경로가 파일인 경우. 스택트레이스 대신 원인을 남긴다.
        print(f"run 디렉터리에 쓸 수 없다: {error}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    except (
        LLMError,
        SchemaError,
        TTSError,
        TimelineError,
        CaptionError,
        RenderError,
    ) as error:
        # run 디렉터리는 남긴다 — run.log에 어느 단계에서 무슨 이유로 멈췄는지와 그때의
        # 설정이 들어 있다. 콘솔 문구가 단계를 특정하지 않는 이유가 그것이다 — 콘텐츠
        # 생성과 장면 구성이 같은 예외를 던지고, 구분은 run.log에 이미 있다.
        print(f"생성 실패:\n{error}", file=sys.stderr)
        # **원문을 함께 낸다** (#30). `RenderError`가 ffmpeg stderr를 사람이 읽는 문구와
        # 갈라 들고 있으므로(앱의 실패 카드가 둘을 다르게 그린다), 여기서 붙이지 않으면
        # 콘솔만 보는 CLI 사용자가 원인을 잃는다 — run.log에는 여전히 남는다.
        if raw := getattr(error, "raw", ""):
            print(raw, file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    if issues and args.fail_on_flagged:
        # 산출물은 이미 다 썼다. 멈추는 것은 이후 단계이지 이번 실행의 결과물이 아니다.
        print(
            f"검수 필요 항목이 {len(issues)}건이다 (--fail-on-flagged). "
            f"산출물은 {context.run_dir}에 남아 있다.",
            file=sys.stderr,
        )
        return EXIT_FLAGGED

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
