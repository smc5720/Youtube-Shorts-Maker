"""앱의 편집을 반영해 장면·오디오·자막을 다시 만든다 (PRD 7.9·14.1, 이슈 #77).

편집 앱(#28, #79~#83)은 결과를 `project.json`과 콘텐츠 산출물에 남기고 **"낡았다"고 표시하는
데까지**만 간다. 그 표시를 지우는 실행이 여기이고, 없으면 사용자가 앱에서 고친 내용을 반영할
길이 CLI 전체 재실행뿐이다 — 그러면 LLM 생성부터 다시 돌아 **편집한 콘텐츠가 덮인다.**

- **경로가 하나다. 범위 선택이 없다.** "자막만 낡음"과 "음성까지 낡음"은 실행이 갈리는 것이
  아니라 **TTS 재합성이 일어나는지**에서만 갈리고, 그 판단은 이미 `audio/segments.json`이
  한다 (#15). 화면의 두 버튼이 이 함수 하나를 부른다 (D2 확정 스펙 7.3).
- **`review`의 목록은 입력이 아니다.** 무엇을 다시 만들지는 콘텐츠와 `segments.json`을
  비교하면 나오므로, 목록이 틀려 있어도(되돌린 편집이 `stale`에 남아 있어도) 결과가 같다.
  이 실행이 그 목록에 하는 일은 **비우는 것뿐이다.**
- **입력은 파일이다.** 프리뷰(#27)·렌더(#30)는 앱이 든 프로젝트를 받지만 이쪽은 받지 않는다 —
  그 둘은 결과가 화면 하나·파일 하나라 되돌릴 수 있고, 이쪽은 run 디렉터리의 산출물 집합을
  콘텐츠에 맞추는 실행이다. 저장하지 않은 콘텐츠로 돌리면 콘텐츠 파일과 `scenes.json`이
  **서로 다른 문구를 들게 되고 어느 쪽이 원본인지 알 수 없다.**
- **LLM을 부르지 않는다.** 콘텐츠 생성기도 메타데이터 생성기도 이 경로에 없다. 그래서 문제를
  고치면 `metadata.json`은 옛 제목·태그로 남고, 그것을 다시 만드는 것은 CLI 전체 실행이다.
- **설정은 run 디렉터리의 기록에서 온다** (#92). `load_config()`를 인자 없이 부르면 **cwd**의
  `config.yaml`을 찾는데 앱 백엔드에서 cwd를 정하는 것은 앱이다 — 생성 때와 다른 값으로 도는
  경로가 그것이다 (PRD 14.1).
- **이 모듈은 타입을 모른다.** 콘텐츠 파일명도 검증도 레지스트리에서 온다
  (`ShortsType.content_schema`, #28) — 여기 콘텐츠 파일명을 적으면
  `tests/test_type_boundary.py`가 잡는다.

## 사람이 얹은 편집을 어디에 얹는가

오디오·자막은 **`render.scene_overrides`를 얹은 장면으로 만든다.** 렌더가 그리는 타임라인이
그것이므로(`video_renderer.apply_scene_overrides`), 얹지 않고 만든 `voice.mp3`는 사람이 길이를
고친 순간부터 화면과 어긋난다 — `review.timeline_stale`이 뜻하는 상태가 바로 그것이고, 얹어야
지워진다. 문구(`text`)도 같이 얹는다: 번인은 이미 오버라이드를 쓰므로 얹지 않으면
`captions.srt`만 옛 문구로 남는다.

**확정 검증은 `scenes.json`에 쓰는 값에만 건다.** 낭독보다 짧은 `duration`은
`validate_scenes_final`이 거부하는 값이고(그래서 오버라이드가 `project.json`에 산다), 그래서
얹은 사본은 `timeline.place_narration`과 `captions.build_applied`로 간다.

## 무손상과 취소

확정 산출물 넷(`scenes.json`·`captions.srt`·`voice.mp3`·`project.json`)은 **전부 성공했을 때만
교체한다** — 임시 파일에 쓰고 마지막에 바꿔 끼운다 (`run_context.commit_staged`).

**세그먼트 mp3는 제자리에 쓴다.** 재사용 층(`audio/segments.json`)이 "그 경로의 파일이
진실"에 기대고 있어서다 (#15). 반쯤 갱신된 채로 멈춰도 다음 재생성이 이어서 하고, 이미 있는
`voice.mp3`는 세그먼트를 참조하지 않는 완성 파일이라 영향받지 않는다.

**취소는 단계 경계에서만 멈춘다.** 교체 전이므로 잘린 산출물이 남지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from . import PACKAGE_LOGGER, captions, narration, timeline, video_renderer
from .config import Config, load_run_config
from .run_context import (
    commit_staged,
    discard_staged,
    serialize_artifact,
    stage_text,
    staging_path,
)
from .schemas.project import PROJECT_SCHEMA, load_project, validate_project
from .schemas.scenes import SCENES_SCHEMA
from .shorts_types import ShortsType
from .timeline import VOICE_TRACK, Placement
from .tts import create_synthesizer
from .video_renderer import scene_key

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.regenerate")

STEPS = ("content", "scenes", "narration", "timeline", "captions", "commit")
"""진행 표시의 단계 (D2 확정 스펙 3.3의 표현과 같은 층).

**프레임 단위가 아니다** (#30의 렌더와 갈린다). 합성 트랙 하나는 초 단위로 끝나고, 사람이
기다리는 시간의 대부분은 세그먼트 합성이라 그 단계만 `n/m`을 함께 낸다.
"""


class RegenerateCancelled(Exception):
    """사람이 취소했다. **실패가 아니다** — 산출물은 이전 상태 그대로다."""


class Progress(NamedTuple):
    """재생성이 어디까지 왔는가.

    **퍼센트가 없다.** 단계마다 걸리는 시간이 크게 다르므로(합성이 대부분이다) 단계 수로
    나눈 비율은 진행을 말해 주지 않는다 — 어느 단계인지와 그 안의 `n/m`이 전부다.
    """

    step: str
    """`STEPS`의 한 이름."""

    done: int
    """그 단계 안에서 끝난 개수. 세그먼트가 아닌 단계는 0이다."""

    total: int
    """그 단계 안에서 할 전체 개수. 세그먼트가 아닌 단계는 0이다."""


ProgressHook = Callable[[Progress], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class Report:
    """무엇이 다시 만들어졌는가. 앱과 테스트가 읽는다."""

    scene_count: int
    segment_count: int
    synthesized: int
    """실제로 합성한 세그먼트 수. 나머지는 파일을 다시 재서 그대로 썼다 (#15)."""

    cue_count: int
    total_sec: float
    """오버라이드를 얹은 타임라인의 총 길이. `voice.mp3`의 길이이기도 하다."""

    voice: str | None
    """`project.json`의 `audio.voice`가 된 값. 낭독 장면이 없으면 `None`이다."""

    dropped_overrides: int
    """가리킬 장면이 없어져 정리한 `render.scene_overrides` 항목 수."""


def regenerate(
    run_dir: Path,
    *,
    shorts_type: ShortsType,
    on_progress: ProgressHook | None = None,
    should_cancel: CancelCheck | None = None,
) -> Report:
    """콘텐츠에 맞춰 `scenes.json`·세그먼트·`voice.mp3`·`captions.srt`·`project.json`을 다시 만든다.

    Args:
        run_dir: 다시 만들 run 디렉터리. 읽는 것도 쓰는 것도 여기뿐이다.
        shorts_type: `project.json`의 `type`이 가리키는 타입 선언. 콘텐츠 파일명·검증과
            장면 템플릿이 여기서 온다.
        on_progress: 단계가 바뀔 때 부른다.
        should_cancel: 참을 돌려주면 **다음 단계 경계에서** 멈춘다.

    Raises:
        RegenerateCancelled: 취소됐을 때. 산출물은 이전 상태 그대로다.
        ConfigError: 설정 기록이 없거나(이 파일이 생기기 전에 만들어진 run 디렉터리다)
            계약을 어겼을 때.
        SchemaError: 콘텐츠·프로젝트가 계약을 어겼거나 다시 만든 장면 목록이 확정 검증을
            통과하지 못할 때.
        TTSError: 세그먼트 합성이 실패했을 때.
        TimelineError: 합성 트랙을 만들지 못했을 때.
        CaptionError: 설정값이 자막을 만들 수 없는 값일 때.
        OSError: 산출물을 쓰지 못했을 때.
    """
    report = _Run(
        run_dir=run_dir,
        shorts_type=shorts_type,
        on_progress=on_progress or (lambda _progress: None),
        should_cancel=should_cancel or (lambda: False),
    ).execute()
    LOGGER.info(
        "재생성 완료 — 장면 %d개, 세그먼트 %d개(합성 %d), 자막 %d큐, 총 %.3f초",
        report.scene_count,
        report.segment_count,
        report.synthesized,
        report.cue_count,
        report.total_sec,
    )
    return report


@dataclass
class _Run:
    """한 번의 재생성. **상태를 들고 있어야 하는 것은 임시 파일 목록 하나다.**

    실패하거나 취소되면 그 목록을 치우고 원본은 손대지 않는다 — 함수 하나로 쓰면 그 정리가
    단계마다 반복되거나 빠진다.
    """

    run_dir: Path
    shorts_type: ShortsType
    on_progress: ProgressHook
    should_cancel: CancelCheck

    def execute(self) -> Report:
        staged: list[tuple[Path, Path]] = []
        try:
            return self._build(staged)
        except BaseException:
            # 만들다 만 임시 파일을 치운다. **원본은 손대지 않았다** — 교체는 마지막
            # 한 번뿐이고 거기까지 갔으면 실패할 것이 남아 있지 않다.
            discard_staged(staged)
            raise

    def _build(self, staged: list[tuple[Path, Path]]) -> Report:
        config = load_run_config(self.run_dir)

        self._step("content")
        content = self.shorts_type.content_schema.load(
            self.run_dir / self.shorts_type.content_artifact
        )
        project = load_project(self.run_dir / PROJECT_SCHEMA.name)

        self._step("scenes")
        draft = self.shorts_type.scene_template(content, config=config)

        with_audio, synthesized = self._synthesize(draft, config=config)

        self._step("timeline")
        # **트랙을 여기서 만들지 않는다.** 이 타임라인은 사람이 얹은 편집을 반영하기 전이라
        # 렌더가 그리는 것과 다르다 — 만들면 곧바로 버리는 ffmpeg 호출이 하나 는다.
        confirmed = timeline.finalize(
            with_audio, run_dir=self.run_dir, config=config, mix=_skip_mix
        )

        project, dropped = _prune_overrides(project, confirmed)
        applied = video_renderer.apply_scene_overrides(project, confirmed)
        voice = staging_path(self.run_dir / VOICE_TRACK)
        placed = timeline.place_narration(
            applied, run_dir=self.run_dir, config=config, destination=voice
        )
        if voice.is_file():
            staged.append((voice, self.run_dir / VOICE_TRACK))

        self._step("captions")
        cues = captions.build_applied(placed, config=config)

        self._step("commit")
        project = _cleared(project, voice=VOICE_TRACK if voice.is_file() else None)
        # 프로젝트도 검증한 뒤에 쓴다 (`api.method_save`와 같은 규칙이다). 계약을 어긴 상태를
        # 파일에 남기면 그 run은 앱으로도 CLI로도 열 수 없게 된다.
        validate_project(project)
        staged += [
            _stage(self.run_dir / SCENES_SCHEMA.name, serialize_artifact(confirmed)),
            _stage(self.run_dir / captions.CAPTIONS_NAME, captions.render(cues)),
            _stage(self.run_dir / PROJECT_SCHEMA.name, serialize_artifact(project)),
        ]
        commit_staged(staged)
        staged.clear()

        return Report(
            scene_count=len(confirmed["scenes"]),
            segment_count=sum(
                1 for scene in confirmed["scenes"] if scene.get("narrate")
            ),
            synthesized=synthesized,
            cue_count=len(cues),
            total_sec=round(
                sum(float(scene["duration"]) for scene in placed["scenes"]), 3
            ),
            voice=project["audio"]["voice"],
            dropped_overrides=dropped,
        )

    def _synthesize(
        self, draft: Mapping[str, Any], *, config: Config
    ) -> tuple[dict[str, Any], int]:
        """낭독 세그먼트. **재합성 여부는 `segments.json`이 정한다** (#15).

        진행이 `n/m`으로 나가는 유일한 단계다 — 사람이 기다리는 시간의 대부분이 여기다.
        """
        self._step("narration")
        synthesized = 0

        def report(done: int, total: int, reused: bool) -> None:
            nonlocal synthesized
            if not reused:
                synthesized += 1
            self._step("narration", done=done, total=total)

        return (
            narration.synthesize_segments(
                draft,
                run_dir=self.run_dir,
                synthesizer=create_synthesizer(config),
                on_segment=report,
            ),
            synthesized,
        )

    def _step(self, name: str, *, done: int = 0, total: int = 0) -> None:
        """단계 경계. **취소를 여기서만 본다** — 교체 전이라 잘린 산출물이 남지 않는다."""
        if self.should_cancel():
            raise RegenerateCancelled(f"{name} 단계 경계에서 취소했다")
        self.on_progress(Progress(step=name, done=done, total=total))


def _skip_mix(
    placements: Sequence[Placement], destination: Path, total_sec: float
) -> None:
    """`timeline.finalize`가 합성 트랙을 만들지 않게 한다.

    확정 타임라인은 **사람이 얹은 길이를 반영하기 전**이라 그것으로 만든 트랙은 렌더가
    그리는 시각과 어긋난다. 트랙은 오버라이드를 얹은 뒤 `place_narration`이 한 번 만든다.
    """


def _stage(path: Path, content: str) -> tuple[Path, Path]:
    return (stage_text(path, content), path)


def _prune_overrides(
    project: Mapping[str, Any], scenes: Mapping[str, Any]
) -> tuple[dict[str, Any], int]:
    """가리킬 장면이 없어진 오버라이드 항목을 정리한다.

    **남은 장면의 값은 손대지 않는다** — 길이도 문구도 오버레이도 사람이 넣은 값이고
    재생성이 지울 근거가 없다. 지우는 것은 문제가 콘텐츠에서 사라져 **가리킬 대상이 없어진**
    항목뿐이고, 그대로 두면 렌더가 매번 "가리키는 장면이 없다"고 경고한다
    (`video_renderer.apply_scene_overrides`).
    """
    overrides = project.get("render", {}).get("scene_overrides")
    if not overrides:
        return dict(project), 0

    live = {scene_key(scene) for scene in scenes["scenes"]}
    kept = [item for item in overrides if scene_key(item) in live]
    dropped = len(overrides) - len(kept)
    if dropped:
        LOGGER.info("가리킬 장면이 없어진 scene_overrides %d개를 정리했다", dropped)
    return (
        {**project, "render": {**project["render"], "scene_overrides": kept}},
        dropped,
    )


def _cleared(project: Mapping[str, Any], *, voice: str | None) -> dict[str, Any]:
    """낡음 표시를 비운 프로젝트.

    **`acknowledged`는 지우지 않는다.** 사람이 `flagged`를 보고 넘어가기로 한 기록이고,
    재생성은 그 판단을 무르지 않는다 (D2 확정 스펙 1.4).

    **세 칸을 모두 쓴다.** 선택 필드라 없는 채로 열린 프로젝트가 있는데(이 필드들이 생기기
    전에 만들어진 run 디렉터리다), 비운 상태를 명시해 두지 않으면 "비었다"와 "이 세대가
    모르는 값이다"가 파일에서 갈리지 않는다.

    `audio.voice`는 **파일이 있는지로 정한다** — `project.build`와 같은 규칙이고, 조건을 여기
    다시 적으면 트랙을 만드는 쪽과 두 곳에서 갈린다.
    """
    review = dict(project.get("review") or {})
    return {
        **project,
        "audio": {**project["audio"], "voice": voice},
        "review": {
            "acknowledged": list(review.get("acknowledged") or []),
            "stale": [],
            "captions_stale": [],
            # **장면 편집이 산출물에 반영됐다** — 길이도 문구도 방금 만든 트랙·자막에 얹혔다.
            "timeline_stale": False,
        },
    }
