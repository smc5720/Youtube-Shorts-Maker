"""실패한 run 디렉터리를 이어서 재실행한다 (PRD 2장·13장, 이슈 #36).

긴 파이프라인에서 **마지막 렌더만 실패했는데 전체를 다시 실행하면** LLM·TTS 비용을 다시
쓴다. 이 모듈은 이미 있는 run 디렉터리를 열어 **없는 산출물만 만들고, 있는 것은 그대로
쓴다.**

- **모델을 부르지 않는다.** 콘텐츠 생성기도 메타데이터 생성기도 이 경로에 없다
  (`regenerate`와 같은 이유다) — 앱에서 고친 콘텐츠를 덮게 되고, 그것을 다시 만드는 것은
  이어 돌리기가 아니라 **새 run**이다. 그래서 콘텐츠 산출물과 `metadata.json`은 **입력**이고,
  없으면 만들지 않고 멈춘다.
- **설정은 run 디렉터리의 기록에서 온다** (`config.used.yaml`, #92). `load_config()`를 인자
  없이 부르면 **cwd**의 `config.yaml`을 찾는데, 그러면 이어 돌린 실행이 생성 때와 다른 값으로
  돈다 (PRD 14.1).
- **`project.json`이 있으면 손대지 않는다.** 사람이 앱에서 얹은 값(`render.scene_overrides`,
  `review`)이 그 파일에 사는데, `project.build`로 다시 만들면 조용히 사라진다. 다시 만드는
  것은 **없을 때뿐**이다.
- **타입을 모른다.** 콘텐츠에서 장면을 다시 뽑지 않으므로(`scenes.json`이 입력이다) 타입
  선언도 콘텐츠 파일명도 필요하지 않다 — 재생성(#77)과 갈리는 지점이 여기다.

## 어디까지 이어 돌릴 수 있는가

파이프라인의 순서는 `콘텐츠 → scenes.json(초안) → metadata.json → 세그먼트 →
타임라인 확정(+ voice.mp3) → captions.srt → project.json → final_short.mp4`다. 앞의 셋은
모델이 만들므로, **이어 돌리기가 실제로 살리는 실패 지점은 세그먼트 합성부터 뒤**다. 그래서
`scenes.json`과 `metadata.json`이 요구 산출물이고, 그보다 앞에서 멈춘 run은 이어 돌릴 것이
아니라 다시 만들 것이다 — 무엇이 없어서 멈추는지 말해 준다 (`ResumeError`).

## 무손상

**최종 mp4는 제자리에 쓰지 않는다** — 임시 파일에 렌더하고 성공했을 때만 바꿔 끼운다
(`video_renderer.render`). 잘린 mp4는 성공한 결과물과 구분되지 않으므로, 이것이 없으면
"산출물이 있으니 건너뛴다"는 이 모듈의 판단 자체가 틀린 답을 낸다.

`voice.mp3`와 확정 `scenes.json`은 제자리에 쓴다. 재생성(#77)이 그 넷을 바꿔 끼우는 이유는
**정상 산출물을 가진 run 디렉터리를 고치기** 때문이고, 이쪽은 반대로 그 산출물이 없거나
낡은 상태에서 시작한다 — 실패하면 다음 이어 돌리기가 같은 자리에서 다시 한다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import PACKAGE_LOGGER, captions, narration, project, timeline, video_renderer
from .config import RUN_CONFIG_FILENAME, Config, load_run_config
from .run_context import write_artifact, write_text_artifact
from .schemas import SchemaError
from .schemas.metadata import METADATA_SCHEMA
from .schemas.project import PROJECT_SCHEMA, load_project
from .schemas.scenes import SCENES_SCHEMA, load_scenes, validate_scenes_final
from .timeline import VOICE_TRACK
from .tts import create_synthesizer

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.resume")

REQUIRED = (RUN_CONFIG_FILENAME, SCENES_SCHEMA.name, METADATA_SCHEMA.name)
"""이어 돌리기가 만들 수 없는 산출물. 하나라도 없으면 시작하지 않는다.

`config.used.yaml`은 그 run이 어떤 설정으로 돌았는지이고(#92), 나머지 둘은 **모델이 만드는
산출물**이다 — 다시 만드는 것은 새 run이다. 콘텐츠 산출물(`quiz.json` 등)이 이 목록에 없는
이유는 이 경로가 그것을 읽지 않기 때문이다: 장면은 `scenes.json`에서 온다.
"""

FORCE_TARGETS = ("segments", "render")
"""`--force`가 받는 이름 — **이 경로가 실제로 다시 만들 수 있는 것 둘이다.**

`segments`는 세그먼트 오디오를, `render`는 최종 mp4를 산출물이 있어도 다시 만든다. 콘텐츠·
메타데이터가 여기 없는 이유는 위와 같다.

**`segments`가 TTS 캐시까지 비우지는 않는다.** 캐시는 run 디렉터리 밖의 층이고(`.cache/tts`,
PRD 7.5.2) 이 옵션이 대상으로 하는 것은 run 디렉터리의 파일이다 — 같은 문장이면 캐시에서
복사되므로 provider 호출이 0회일 수 있다.
"""


class ResumeError(Exception):
    """이어 돌릴 수 없다. run 디렉터리가 아니거나, 만들 수 없는 산출물이 없을 때."""


@dataclass(frozen=True)
class Report:
    """무엇을 다시 만들고 무엇을 그대로 썼는가. CLI와 테스트가 읽는다."""

    remade: tuple[str, ...]
    """이번에 만든 산출물 이름. 비어 있으면 할 일이 없었다는 뜻이다."""

    reused: tuple[str, ...]
    """이미 있어서 그대로 쓴 산출물 이름."""

    synthesized: int
    """실제로 합성한 세그먼트 수. 나머지는 파일을 다시 재서 그대로 썼다 (#15)."""

    output: Path
    """`final_short.mp4`의 경로. 이번에 만들었든 그대로 썼든 이 자리에 있다."""


def resume(run_dir: Path, *, force: Sequence[str] = ()) -> Report:
    """`run_dir`의 없는 산출물만 만들어 `final_short.mp4`까지 간다.

    Args:
        run_dir: 이어 돌릴 run 디렉터리. 읽는 것도 쓰는 것도 여기뿐이다.
        force: `FORCE_TARGETS`의 이름들. 산출물이 있어도 그 단계를 다시 실행한다.

    Raises:
        ResumeError: run 디렉터리가 아니거나 요구 산출물이 없을 때.
        ConfigError: 설정 기록이 계약을 어겼을 때.
        SchemaError: `scenes.json`이 계약을 어겼거나 확정 결과가 확정 검증을 통과하지 못할 때.
        TTSError: 세그먼트 합성이 실패했을 때.
        TimelineError: 합성 트랙을 만들지 못했을 때.
        CaptionError: 설정값이 자막을 만들 수 없는 값일 때.
        RenderError: 렌더가 실패했을 때.
        OSError: 산출물을 쓰지 못했을 때.
    """
    unknown = sorted(set(force) - set(FORCE_TARGETS))
    if unknown:
        # CLI는 argparse가 막지만 이 함수는 앱·테스트도 부른다. 조용히 무시하면 강제
        # 재실행이 일어나지 않은 것을 실행한 사람이 알 수 없다.
        raise ResumeError(
            f"강제 재실행 대상을 모른다: {', '.join(unknown)} — "
            f"쓸 수 있는 것은 {', '.join(FORCE_TARGETS)}이다"
        )

    check(run_dir)
    return _Run(run_dir=run_dir, force=frozenset(force)).execute()


def check(run_dir: Path) -> None:
    """이어 돌릴 수 있는 디렉터리인지 확인한다. **없는 것을 전부 모아서 말한다.**

    하나씩 말하면 사람이 고치고 다시 돌려서 다음 것을 보는 왕복이 생긴다 (`config.load_config`
    와 같은 규칙이다).

    `resume()`이 먼저 부르지만 CLI가 한 번 더 부른다 — **거부할 때 그 디렉터리에 아무것도
    쓰지 않아야** 하고, CLI는 실행 기록을 `run_dir/run.log`에 남기기 때문이다. run 디렉터리가
    아닌 곳을 가리킨 사람에게 남는 것이 로그 파일 하나여서는 안 된다.

    Raises:
        ResumeError: 디렉터리가 아니거나, 이어 돌리기가 만들 수 없는 산출물이 없을 때.
    """
    if not run_dir.is_dir():
        raise ResumeError(f"{run_dir}는 디렉터리가 아니다 — run 디렉터리를 지정한다")

    missing = [name for name in REQUIRED if not (run_dir / name).is_file()]
    if missing:
        raise ResumeError(
            f"{run_dir}에 {', '.join(missing)}이 없다 — 콘텐츠·메타데이터는 모델이 만드는 "
            "산출물이라 이어 돌리기가 만들지 않는다. 새로 실행한다"
        )


@dataclass
class _Run:
    """한 번의 이어 돌리기. 무엇을 만들고 무엇을 건너뛰었는지를 모으며 진행한다."""

    run_dir: Path
    force: frozenset[str]
    remade: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    synthesized: int = 0

    def execute(self) -> Report:
        config = load_run_config(self.run_dir)
        LOGGER.info("%s의 값으로 이어 돌린다", RUN_CONFIG_FILENAME)

        scenes = load_scenes(self.run_dir / SCENES_SCHEMA.name)
        scenes = self._audio(scenes, config=config)
        self._captions(scenes, config=config)
        # 순서가 `main.run`과 같다 — 자막, 프로젝트, 렌더. 렌더가 "앞에서 무엇을 다시
        # 만들었는가"를 보므로 이 셋의 순서가 곧 그 판단의 입력이다.
        data = self._project(scenes, config=config)
        output = self._render(scenes, data)

        return Report(
            remade=tuple(self.remade),
            reused=tuple(self.reused),
            synthesized=self.synthesized,
            output=output,
        )

    # --- 단계 -------------------------------------------------------------

    def _audio(self, scenes: dict[str, Any], *, config: Config) -> dict[str, Any]:
        """세그먼트와 타임라인 확정. 돌려주는 것은 확정 상태의 장면 목록이다.

        **`voice.mp3`가 없으면 확정 상태여도 다시 한다.** 그 파일을 만드는 자리가
        `timeline.finalize` 안이라(`_mix_or_skip`) 트랙만 따로 만들 입구가 없고, 세그먼트가
        그대로면 재합성도 일어나지 않는다 (#15).
        """
        voice = self.run_dir / VOICE_TRACK
        narrated = any(scene.get("narrate") for scene in scenes["scenes"])
        forced = "segments" in self.force

        if not forced and _confirmed(scenes) and (voice.is_file() or not narrated):
            self.reused += [SCENES_SCHEMA.name, *([VOICE_TRACK] if narrated else [])]
            LOGGER.info("%s이 확정 상태다 — 세그먼트와 타임라인을 건너뛴다", SCENES_SCHEMA.name)
            return scenes

        if forced:
            # 기록을 지우면 다음 합성이 전부 다시 만든다 (`narration.manifest_path`).
            narration.manifest_path(self.run_dir).unlink(missing_ok=True)
            LOGGER.info("세그먼트를 강제로 다시 합성한다")

        LOGGER.info("낭독 세그먼트 합성 중 — 이미 있는 것은 다시 재서 그대로 쓴다")
        scenes = narration.synthesize_segments(
            scenes,
            run_dir=self.run_dir,
            synthesizer=create_synthesizer(config),
            on_segment=self._count,
        )
        if narrated:
            # 확정 전 상태도 남긴다 (`main.run`과 같은 자리). 아래에서 실패하면 다음 이어
            # 돌리기가 실측을 다시 하지 않고 이 값을 읽는다.
            write_artifact(self.run_dir, SCENES_SCHEMA.name, scenes)

        LOGGER.info("타임라인 확정 중 — 실측 길이로 duration과 낭독 오프셋을 정한다")
        scenes = timeline.finalize(scenes, run_dir=self.run_dir, config=config)
        write_artifact(self.run_dir, SCENES_SCHEMA.name, scenes)
        self.remade += [SCENES_SCHEMA.name, *([VOICE_TRACK] if narrated else [])]
        LOGGER.info(
            "%s 확정 완료 — 총 %.2f초, 합성 %d개",
            SCENES_SCHEMA.name,
            sum(scene["duration"] for scene in scenes["scenes"]),
            self.synthesized,
        )
        return scenes

    def _captions(self, scenes: dict[str, Any], *, config: Config) -> None:
        """자막. **장면을 다시 확정했으면 파일이 있어도 다시 만든다** — 타임코드가 낡았다."""
        path = self.run_dir / captions.CAPTIONS_NAME
        if path.is_file() and not self._touched_scenes:
            self.reused.append(captions.CAPTIONS_NAME)
            return

        cues = captions.build(scenes, config=config)
        write_text_artifact(self.run_dir, captions.CAPTIONS_NAME, captions.render(cues))
        self.remade.append(captions.CAPTIONS_NAME)
        LOGGER.info("%s 생성 완료 — 큐 %d개", captions.CAPTIONS_NAME, len(cues))

    def _project(self, scenes: dict[str, Any], *, config: Config) -> dict[str, Any]:
        """렌더러의 입력 계약. **있으면 손대지 않는다.**

        사람이 앱에서 얹은 값이 이 파일에 산다 (`render.scene_overrides`, `review`). 장면을
        다시 확정했더라도 다시 만들지 않는 이유가 그것이다 — 자막과 갈리는 지점이 여기다.
        오버라이드가 가리킬 장면이 없어졌다면 렌더가 경고하고 넘어가며, 그 정리는 재생성의
        몫이다 (`regenerate._prune_overrides`).
        """
        path = self.run_dir / PROJECT_SCHEMA.name
        if path.is_file():
            self.reused.append(PROJECT_SCHEMA.name)
            # 검증하며 읽는다 — 사람이 고친 파일일 수 있고, 계약을 어긴 값으로 렌더를
            # 시작하면 원인이 인코딩 실패로 둔갑한다.
            return load_project(path)

        data = project.build(scenes, config=config, run_dir=self.run_dir)
        write_artifact(self.run_dir, PROJECT_SCHEMA.name, data)
        self.remade.append(PROJECT_SCHEMA.name)
        LOGGER.info("%s 생성 완료 — 배경 %s", PROJECT_SCHEMA.name, data["background"]["value"])
        return data

    def _render(self, scenes: dict[str, Any], data: dict[str, Any]) -> Path:
        """최종 렌더. 앞 단계에서 하나라도 다시 만들었으면 이미 있는 mp4는 낡았다."""
        output = self.run_dir / str(data["render"]["output"])

        if output.is_file() and not self.remade and "render" not in self.force:
            self.reused.append(output.name)
            LOGGER.info("%s이 이미 있고 낡지 않았다 — 렌더를 건너뛴다", output.name)
            return output

        LOGGER.info("렌더 중 — 1080x1920 30fps 인코딩은 수십 초가 걸린다")
        output = video_renderer.render(data, scenes, run_dir=self.run_dir)
        self.remade.append(output.name)
        LOGGER.info("%s 생성 완료", output.name)
        return output

    # --- 판단 -------------------------------------------------------------

    @property
    def _touched_scenes(self) -> bool:
        return SCENES_SCHEMA.name in self.remade

    def _count(self, done: int, total: int, reused: bool) -> None:
        if not reused:
            self.synthesized += 1
        LOGGER.debug("세그먼트 %d/%d (%s)", done, total, "재사용" if reused else "합성")


def _confirmed(scenes: dict[str, Any]) -> bool:
    """확정 상태인가 — `duration`과 낭독 오프셋이 실측으로 정해졌는가.

    **예외로 판단한다.** 확정 검증의 규칙은 `schemas.scenes`가 소유하므로(낭독 장면의
    오디오 필드, `duration >= audio_duration`, 세그먼트 인덱스 일치) 여기서 조건을 다시
    적으면 같은 판단이 두 곳에 생기고, 규칙이 늘어날 때 이쪽만 낡는다.
    """
    try:
        validate_scenes_final(scenes)
    except SchemaError:
        return False
    return True
