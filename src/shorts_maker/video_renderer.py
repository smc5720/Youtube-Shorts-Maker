"""확정 `scenes.json` + `project.json` → `final_short.mp4` (PRD 6.3·7.7, 이슈 #19).

MoviePy를 쓰지 않고 FFmpeg 명령을 직접 만든다 (PRD 14.1). **오버레이(#20~#23)가 여기에
얹히는 골격이다** — 이 모듈이 정하는 것은 캔버스 규격, 배경, 오디오 트랙, 그리고 장면
경계의 프레임 정렬이고, 그 위에 무엇을 그리는지는 뒤따르는 이슈가 정한다.

- **입력은 `project.json`이다.** 배경·오디오·출력 규격을 config에서 다시 읽지 않는다 —
  값을 정하는 경로가 둘이 되면 앱(#26)이 편집한 프로젝트와 CLI 렌더가 갈린다 (PRD 7.10).
  `scenes.json`을 함께 받는 이유는 프로젝트가 장면 배열을 복사하지 않기 때문이다 (7.4.1).
- **프레임 경계 정렬은 이 모듈이 소유한다** (`align`). `duration`은 밀리초 자리 실수이고
  30fps 한 프레임은 33.33ms라, 장면 시작 시각을 각자 누적하면 오버레이마다 다른 경계를
  갖게 된다 (`schemas/scenes.py`의 `DURATION_DIGITS` 주석).
- **오디오 스트림은 항상 정확히 하나다.** 낭독 장면이 없어도 무음 트랙을 넣고, 효과음이
  얹혀도(#23) 믹스 결과가 하나로 나온다 — 필터 체인은 `audio_mix`가 만들고 이 모듈은 명령에
  옮겨 담는다.
- **이 단계도 타입을 모른다.** `role`로 갈라지는 규칙은 오버레이 쪽에 생기고, 골격이 읽는
  것은 `duration`뿐이다 (PRD 7.4.1).
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, NamedTuple

from . import PACKAGE_LOGGER, audio_mix, overlay
from .assets import AssetError, background_presets
from .audio_mix import AudioChain, AudioMixError
from .overlay import OverlayError
from .run_context import commit_staged, staging_path
from .schemas.project import BACKGROUND_KINDS, DEFAULT_VOICE_VOLUME
from .schemas.scenes import validate_scenes_final

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.video_renderer")

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
FPS = 30
OUTPUT_NAME = "final_short.mp4"
"""영상 규격과 출력 파일명 (PRD 6.3, 6.2 표).

`project.json`의 `render` 섹션에 그대로 들어가고(`project.build`), 렌더는 그 파일에서 읽은
값을 쓴다. 여기 상수는 초기 상태의 값이지 렌더가 강제하는 값이 아니다 — 앱이 해상도를 바꾸면
바뀐 값으로 렌더된다.
"""

FFMPEG_TIMEOUT_SEC = 900
"""60초 영상 하나를 인코딩하는 상한. 넘겼다면 기다릴 것이 아니라 환경 문제다
(`timeline.FFMPEG_TIMEOUT_SEC`와 같은 성격이고, 인코딩이 있어 값이 크다)."""

PREVIEW_TIMEOUT_SEC = 180
"""대표 프레임 전부를 뽑는 상한 (#27). 인코딩이 없어 최종 렌더보다 훨씬 작다 — 11장면 실측이
2.2초였으므로 이 값에 닿았다면 기다릴 것이 아니라 환경 문제다."""

PREVIEW_NAME = "scene-{index:03d}.png"
"""프리뷰 프레임 파일명. **장면 인덱스로 매긴다** — 세그먼트 오디오(`segment_path`)와 같은
규칙이라 어느 장면의 그림인지가 파일명에서 드러난다."""

_SEQUENCE_PATTERN = "seq-%03d.png"
"""image2 muxer가 쓰는 중간 이름. **순번이지 장면 인덱스가 아니다** — 장면 일부만 요청하면
둘이 어긋나므로 `preview`가 곧바로 바꿔 놓는다."""

CRF = 20
"""libx264 품질. **config 키가 아니다** — 45~60초 세로 영상이라 파일 크기보다 텍스트 경계가
중요해서 기본값 23보다 한 단계 올려 잡았다. 실측으로 고른 값은 아니므로, 번인이 들어간 뒤
(#20) 외곽선 주변이 뭉개지면 이 값부터 본다. 사람이 조정할 값이 되면 그때 config를 연다."""

_AUDIO_BITRATE = "192k"
"""출력 오디오 비트레이트. 샘플레이트·레이아웃은 `audio_mix`가 소유한다 — 효과음을 섞는 쪽이
입력 규격을 맞추므로(#23) 같은 값이 두 모듈에 있으면 한쪽만 바뀔 수 있다."""

_HEX_COLOR = re.compile(r"^#?([0-9A-Fa-f]{6})$")
"""`background.kind`가 `color`일 때의 값. 번들 프리셋(`assets.HEX_COLOR`)보다 느슨하다 —
사람이 `project.json`에 직접 적는 값이라 대소문자와 `#` 유무를 가리지 않는다."""

BACKGROUND_FILE_KINDS: dict[str, str] = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".mp4": "video",
}
"""배경으로 받는 사용자 파일의 확장자 → `background.kind` (PRD 14.1, 이슈 #80).

**이 표가 목록의 유일한 소유자다.** 앱은 백엔드를 지나 조회하고(`api.method_presets`) 자기
목록을 들지 않는다 — 두 벌이 되면 앱이 받은 파일을 렌더가 거부할 수 있고, 그 어긋남은 파일을
고르는 순간이 아니라 렌더 도중에 드러난다.

**좁게 시작한 이유는 실패 시점이다.** FFmpeg는 동봉하지 않고 PATH에서 찾으므로 사용자 빌드가
무엇을 디코드하는지 알 수 없다. 넓게 열면 수십 초 걸리는 렌더 중간에 실패한다 (PRD 14.1).

순서가 화면의 순서다 — 앱이 다시 정렬하면 목록 순서가 두 곳에서 정해진다.
"""


class RenderError(Exception):
    """최종 영상을 만들 수 없다.

    **사람이 읽는 문구와 기계가 낸 원문을 갈라 둔다** (#30). 앱의 실패 카드가 둘을 다르게
    그리므로(원인은 본문, 원문은 `mono`) 한 문자열에 섞으면 화면에서 다시 갈라야 하고,
    그 분리는 문구를 다듬는 순간 깨진다 (D2 확정 스펙 3.3).
    """

    def __init__(self, message: str, *, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw
        """ffmpeg stderr처럼 그대로 복사해야 쓸모가 있는 값. 없으면 빈 문자열이다."""


def background_kind(value: str) -> str:
    """배경 파일 경로 → `background.kind`. **확장자가 종류를 정한다** (PRD 14.1).

    파일이 있는지는 보지 않는다 — 이름만으로 답할 수 있어야 앱이 고르는 순간에 거부할 수
    있고, 없는 파일은 렌더·프리뷰가 `_source_file`에서 경로와 함께 말한다.

    Raises:
        RenderError: 목록에 없는 확장자일 때. 받는 형식을 함께 말한다.
    """
    kind = BACKGROUND_FILE_KINDS.get(Path(value).suffix.lower())
    if kind is None:
        raise RenderError(
            f"배경으로 받지 않는 형식이다: {value}. "
            f"받는 형식: {', '.join(BACKGROUND_FILE_KINDS)}"
        )
    return kind


@dataclass(frozen=True)
class Timeline:
    """프레임 경계에 맞춘 장면 배치.

    장면 하나의 길이를 프레임 수로 바꾸고, 시작 시각을 그 누계에서 낸다. `duration`을 직접
    더하지 않는 이유는 밀리초 자리 실수의 누계가 프레임 경계와 어긋나기 때문이다.
    """

    fps: int
    frames: tuple[int, ...]
    """장면별 프레임 수. 순서는 `scenes.json`의 장면 배열과 같다."""

    @property
    def total_frames(self) -> int:
        return sum(self.frames)

    @property
    def total_sec(self) -> float:
        return self.total_frames / self.fps

    @property
    def starts(self) -> tuple[float, ...]:
        """장면별 시작 시각(초). 영상 시작 기준이고 프레임 경계 위에 있다.

        **오버레이(#20~#22)가 `enable='between(t,a,b)'`에 쓰는 값이다.** 각자 `duration`을
        다시 누적하면 같은 장면의 경계가 요소마다 갈린다.
        """
        starts: list[float] = []
        elapsed = 0
        for count in self.frames:
            starts.append(elapsed / self.fps)
            elapsed += count
        return tuple(starts)

    def span(self, index: int) -> tuple[float, float]:
        """장면 하나의 (시작, 끝) 시각.

        끝은 **다음 장면의 시작과 정확히 같은 값이다** — `start + frames/fps`로 따로 계산하면
        부동소수점 표현이 갈려 `between(t,a,b)` 구간 사이에 한 프레임짜리 틈이 생길 수 있다.
        """
        starts = self.starts
        end = starts[index + 1] if index + 1 < len(starts) else self.total_sec
        return starts[index], end

    @property
    def frame_spans(self) -> tuple[tuple[int, int], ...]:
        """장면별 (시작 프레임, 끝 프레임). 끝은 다음 장면의 시작과 같다.

        **오버레이(#20~#22)가 `enable` 식에 쓰는 값이다.** 초로 반올림한 값을 적으면 그
        반올림이 실제 프레임 시각보다 커졌을 때 요소가 한 프레임 늦게 켜진다. 프레임 번호를
        `n/fps` 나눗셈으로 그대로 넘기면 필터의 `t`와 같은 값이 나온다 (`overlay._enable`).
        """
        spans: list[tuple[int, int]] = []
        elapsed = 0
        for count in self.frames:
            spans.append((elapsed, elapsed + count))
            elapsed += count
        return tuple(spans)


def scene_key(value: Mapping[str, Any]) -> tuple[str, Any]:
    """오버라이드 항목과 장면을 짝짓는 키 — `(role, question_id)` (PRD 14.1).

    **장면 인덱스가 아니다.** 인덱스는 문제를 추가·삭제하면 밀리고, 그러면 사람이 조정한
    값이 다른 장면에 붙는다 (#28이 새 문제 번호에서 같은 함정을 밟았다).

    장면과 오버라이드 양쪽에 쓴다 — 두 어휘가 `scenes.json`의 공통 필드라 같은 함수로
    읽힌다. 재생성(#77)이 가리킬 장면이 없어진 항목을 정리할 때도 이 키를 쓴다.
    """
    return (str(value["role"]), value.get("question_id"))


def apply_scene_overrides(
    project: Mapping[str, Any], scenes: Mapping[str, Any]
) -> Mapping[str, Any]:
    """사람이 얹은 장면 편집을 반영한 장면 목록 사본 (#82, #83, PRD 14.1).

    **`align()`보다 먼저, 확정 검증보다 나중에 부른다.** 낭독보다 짧은 길이는
    `validate_scenes_final`이 거부하는 값이므로(그래서 `scenes.json`에 쓸 수 없다) 검증을
    지난 뒤에 얹어야 하고, 프레임 정렬은 여전히 `align()` 하나가 소유해야 하므로 그 앞이다.

    얹는 것은 셋이다 — 길이(`duration`, #82), 자막 문구(`text`, #83), 텍스트
    오버레이(`overlays` → `SCENE_OVERLAYS`, #83). **세 값이 오버라이드 항목 하나에 함께 있다**
    (PRD 14.1).

    얹을 것이 없으면 **받은 객체를 그대로 돌려준다** — 사본을 만들면 오버라이드가 없는
    경로에서도 장면 배열이 두 벌 생긴다.

    Args:
        project: `project.json` 내용. `render.scene_overrides`를 읽는다.
        scenes: 확정 `scenes.json` 내용.

    Returns:
        얹은 값이 갈린 장면 목록. 원본은 바뀌지 않는다.
    """
    overrides = project.get("render", {}).get("scene_overrides") or []
    if not overrides:
        return scenes

    pending = {scene_key(item): item for item in overrides}
    applied: list[dict[str, Any]] = []
    for scene in scenes["scenes"]:
        override = pending.pop(scene_key(scene), None)
        edits = _scene_edits(override)
        applied.append(dict(scene) if not edits else {**scene, **edits})

    # **남은 오버라이드를 조용히 버리지 않는다.** 문제를 지우면 그 문제의 오버라이드가 가리킬
    # 장면이 없어지고, 그 상태로 렌더가 도는 것은 정상이지만(#77이 정리한다) 왜 값이 반영되지
    # 않았는지는 로그에 남아야 한다.
    for role, question_id in pending:
        LOGGER.warning(
            "가리키는 장면이 없는 scene_overrides — role=%s question_id=%s", role, question_id
        )
    return {**scenes, "scenes": applied}


def _scene_edits(override: Mapping[str, Any] | None) -> dict[str, Any]:
    """오버라이드 항목 하나가 장면에 갈아 끼우는 값 (`schemas/project.OVERRIDE_EDITS`).

    **없는 키는 손대지 않는다.** 항목 하나에 셋이 함께 있을 수 있고 그중 일부만 얹힌 상태가
    정상이다 — 길이만 고친 장면에 빈 문구가 들어가면 그 장면의 문구가 사라진다.
    """
    if not override:
        return {}
    edits: dict[str, Any] = {}
    if "duration" in override:
        edits["duration"] = float(override["duration"])
    if "text" in override:
        edits["text"] = str(override["text"])
    if "overlays" in override:
        edits[overlay.SCENE_OVERLAYS] = [dict(item) for item in override["overlays"]]
    return edits


def align(scenes: Mapping[str, Any], *, fps: int = FPS) -> Timeline:
    """확정 장면 목록을 프레임 경계에 맞춘다.

    `frames_i = round(duration_i * fps)` / `start_i = sum(frames_0..i-1) / fps`.

    Raises:
        RenderError: `fps`가 1 미만이거나 확정되지 않은 장면 목록일 때.
    """
    if fps < 1:
        raise RenderError(f"fps는 1 이상이어야 한다. 받은 값: {fps}")

    frames: list[int] = []
    for index, scene in enumerate(scenes["scenes"]):
        duration = scene.get("duration")
        if duration is None:
            raise RenderError(
                f"scenes[{index}]: duration이 없다 — 타임라인 확정을 거치지 않은 장면 목록이다"
            )
        count = round(float(duration) * fps)
        if count < 1:
            # 반 프레임보다 짧은 장면. 0프레임이면 다음 장면과 시작 시각이 같아져 오버레이의
            # `between(t,a,b)`가 빈 구간이 된다.
            LOGGER.warning(
                "scenes[%d](%s): %.3f초는 %dfps에서 한 프레임보다 짧다 — 한 프레임으로 늘린다",
                index,
                scene["role"],
                float(duration),
                fps,
            )
            count = 1
        frames.append(count)

    return Timeline(fps=fps, frames=tuple(frames))


class RenderProgress(NamedTuple):
    """렌더가 어디까지 왔는가 (#30).

    **퍼센트도 남은 시간도 여기 없다.** 그 둘은 화면의 표현이고(D2 확정 스펙 3.3), 이 모듈이
    아는 것은 프레임 수와 그것이 어느 장면인지다 — 총 프레임은 `align()`의 지식이다.
    """

    frame: int
    """인코딩된 프레임 수. FFmpeg의 `-progress` 출력에서 온다."""

    total_frames: int
    """프레임 정렬 총 프레임 수 (`Timeline.total_frames`)."""

    scene_index: int
    """`frame`이 속한 장면. 마지막 프레임을 넘어선 보고는 마지막 장면으로 잡는다."""


ProgressHook = Callable[[RenderProgress], None]

_ACTIVE: dict[subprocess.Popen[str], Path] = {}
_ACTIVE_LOCK = threading.Lock()
"""돌고 있는 렌더 프로세스 → 그것이 쓰고 있는 임시 파일. `kill_active()`가 이 표를 본다.

**값이 최종 경로가 아니라 임시 경로인 이유는 죽인 뒤에 치울 것이 그것이기 때문이다** (#36).
최종 파일은 렌더가 성공했을 때만 그 자리에 놓이므로, 죽은 렌더가 남기는 것은 항상 임시
파일 하나다.
"""

_READER_JOIN_SEC = 2.0
"""ffmpeg가 끝난 뒤 마지막 진행 묶음을 기다리는 시간. 파이프에 남은 몇 줄이므로 짧다."""

_KILL_WAIT_SEC = 5.0
"""죽인 ffmpeg가 실제로 끝나기를 기다리는 시간. **Windows는 열려 있는 파일을 지우지 못하므로**
이것을 기다리지 않으면 임시 파일이 run 디렉터리에 남는다 (#36)."""


def kill_active() -> int:
    """돌고 있는 ffmpeg를 죽이고 그 개수를 돌려준다 (#30).

    **앱 백엔드가 종료될 때 필요하다.** 렌더는 daemon 스레드에서 돌아 stdin EOF로 백엔드가
    끝날 때 그 스레드는 그냥 사라지는데, **자식 ffmpeg는 남아 `final_short.mp4`를 계속 쓴다** —
    사용자가 앱을 닫은 뒤에 완성되는 파일이고, 그것을 앱이 만들었다고 말할 방법도 없다.
    호출부는 `api.serve`의 `atexit`이다.

    CLI에는 필요 없다 — 그쪽은 `render()`를 부른 스레드가 곧 프로세스이고, 죽으면 파이프가
    닫혀 ffmpeg도 끝난다.

    **죽인 렌더의 임시 파일도 치운다** (#36). 최종 파일은 성공했을 때만 놓이므로 여기서
    잘린 `final_short.mp4`가 생기지는 않지만, 앱을 렌더 중에 닫는 것은 정상 사용이라
    치우지 않으면 run 디렉터리에 임시 파일이 쌓인다.
    """
    with _ACTIVE_LOCK:
        running = list(_ACTIVE.items())
    for process, staged in running:
        if process.poll() is None:
            process.kill()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=_KILL_WAIT_SEC)
        _discard(staged)
    return len(running)


def _discard(staged: Path) -> None:
    """임시 파일을 치운다. **지우지 못해도 렌더 결과를 바꾸지 않는다.**

    지우지 못하는 경우는 하나다 — 죽인 ffmpeg가 아직 파일을 열고 있는 Windows. 그때 남는
    것은 최종 산출물과 이름이 다른 임시 파일이라 성공한 결과물로 오인되지 않는다 (#36).
    """
    with suppress(OSError):
        staged.unlink(missing_ok=True)


def render(
    project: Mapping[str, Any],
    scenes: Mapping[str, Any],
    *,
    run_dir: Path,
    on_progress: ProgressHook | None = None,
) -> Path:
    """`final_short.mp4`를 만들고 그 경로를 돌려준다.

    **제자리에 쓰지 않는다** (#36). 임시 파일에 인코딩하고 성공했을 때만 바꿔 끼우므로,
    실패하거나 도중에 죽어도 잘린 `final_short.mp4`가 남지 않는다 — 이전 성공본이 있으면
    그것이 그대로 남는다. 잘린 파일은 성공한 결과물과 구분되지 않아서 이 규칙이 필요하다.

    Args:
        project: `project.json` 내용. 배경·오디오·출력 규격을 여기서 읽는다.
        scenes: `scenes.json` 내용. **확정 상태여야 한다** — 초안의 목표치로 렌더하면
            영상 길이가 `voice.mp3`와 어긋난다.
        run_dir: 이번 run의 출력 디렉터리. 상대 경로의 기준이다.
        on_progress: 진행 상황을 받는 훅 (#30). 주지 않으면 진행 줄을 읽어 버린다 —
            **명령은 어느 쪽이든 같다.** 앱과 CLI가 다른 명령으로 돌면 인코딩 설정이 갈릴 수
            있고, 갈려도 되는 것은 "누가 보고 있는가"뿐이다.

    Raises:
        RenderError: 배경을 해석할 수 없거나 FFmpeg가 없거나 실패했을 때.
        SchemaError: 장면 목록이 확정 상태가 아닐 때.
    """
    # 입구에서 확정 검증을 한다. 이 함수는 CLI 말고도 앱 백엔드와 테스트가 직접 부른다.
    validate_scenes_final(scenes)
    # **검증 다음이다** (#82). 사람이 얹은 길이는 낭독보다 짧을 수 있고 그것은 확정 검증이
    # 거부하는 값이다 — 그래서 `scenes.json`이 아니라 `project.json`에 산다 (PRD 14.1).
    scenes = apply_scene_overrides(project, scenes)

    fps = _int_field(project, "fps")
    timeline = align(scenes, fps=fps)
    # **오버레이를 명령보다 먼저 만든다.** 폰트 경로 오타나 한글을 못 그리는 폰트는 여기서
    # 걸리고, 그때 인코딩은 아직 시작되지 않았다 (#20의 완료 조건).
    overlays = build_overlays(project, scenes, timeline=timeline)
    # 효과음도 인코딩 전에 만든다 (#23). 번들에 없는 `sfx` 이름은 여기서 걸리고, 그때 인코딩은
    # 아직 시작되지 않았다 — 오버레이의 폰트 검증과 같은 자리다.
    audio = build_audio(project, scenes, timeline=timeline)
    output = run_dir / str(project["render"]["output"])
    # 임시 파일에 인코딩한다 (#36). `staging_path`가 확장자를 유지하므로 FFmpeg가 출력 형식을
    # 그대로 정한다 — 뒤에 붙이면 "Unable to find a suitable output format"으로 실패한다.
    staged = staging_path(output)
    command = build_command(
        project,
        run_dir=run_dir,
        total_sec=timeline.total_sec,
        overlays=overlays,
        audio=audio,
        destination=staged,
    )
    # **`-progress`는 `build_command`가 붙이지 않는다** (#30). 그 함수가 소유하는 것은 출력
    # 규격이고 프리뷰 명령과 갈리는 지점도 거기 하나여야 하는데, 이 옵션이 바꾸는 것은
    # 결과물이 아니라 진행 상황을 어디로 흘리는지다. 훅이 없어도 붙이는 이유는 CLI와 앱이
    # 같은 명령으로 돌아야 하기 때문이다 — 갈려도 되는 것은 누가 읽는가뿐이다.
    command = [command[0], "-progress", "pipe:1", *command[1:]]

    # 명령 전문을 남긴다. run.log는 --verbose와 무관하게 DEBUG까지 남으므로(run_context)
    # 실패한 렌더를 손으로 재현할 수 있다.
    LOGGER.debug("렌더 명령 %s", subprocess.list2cmdline(command))
    LOGGER.debug(
        "장면 %d개 / %d프레임 / %.3f초",
        len(timeline.frames),
        timeline.total_frames,
        timeline.total_sec,
    )

    try:
        code, stderr = _run_with_progress(
            command,
            timeline=timeline,
            on_progress=on_progress,
            output=output,
            staged=staged,
        )
    except BaseException:
        # 타임아웃과 Ctrl-C도 여기를 지난다. **`BaseException`인 이유가 그것이다** — 사람이
        # 끊은 렌더가 임시 파일을 남기면 다음 실행이 그것을 지우지 않는다.
        _discard(staged)
        raise
    if code != 0:
        # stderr도 run.log에 남긴다 — 위 명령 전문과 짝이어야 원인이 드러난다.
        LOGGER.error("ffmpeg stderr %s", stderr)
        _discard(staged)
        raise RenderError(
            f"{output.name}을 만들지 못했다 — ffmpeg 종료 코드 {code}", raw=stderr
        )

    # 여기까지 왔으면 실패할 것이 남아 있지 않다 (`run_context.commit_staged`와 같은 규칙).
    commit_staged([(staged, output)])
    return output


def _run_with_progress(
    command: Sequence[str],
    *,
    timeline: Timeline,
    on_progress: ProgressHook | None,
    output: Path,
    staged: Path,
) -> tuple[int, str]:
    """렌더 명령을 돌리며 진행 상황을 읽는다. `(종료 코드, stderr)`를 돌려준다.

    **`subprocess.run` 대신 `Popen`인 이유는 진행률뿐이다** (#30). `-progress pipe:1`이 몇 줄
    단위로 상태를 stdout에 흘리므로 프로세스가 끝나기 전에 읽어야 하고, `run`은 끝난 뒤에
    돌아온다. 그 대가로 `run`이 해 주던 넷을 손으로 지킨다.

    - **`stdin=DEVNULL`.** ffmpeg는 stdin을 조작 입력으로 읽으므로, 앱이 띄운 백엔드에서
      그대로 두면 프로토콜 줄을 가져가고 이 호출이 끝나지 않는다 — 실패도 로그도 없다
      (스파이크 #25 7장, #27에서 실제로 밟았다).
    - **stderr는 파이프가 아니라 임시 파일이다.** stdout만 읽는 동안 stderr 파이프가 차면
      ffmpeg가 그 쓰기에서 막혀 진행 줄도 멈춘다 — 실패한 렌더는 stderr를 길게 낸다.
    - **타임아웃은 진행 줄이 아니라 `wait`가 잰다.** 진행 줄을 읽는 루프에서 재면 아무것도
      내보내지 않고 멈춘 ffmpeg에는 상한이 없다 — 그래서 읽기는 스레드로 내보내고 이쪽은
      `wait(timeout=…)`에 선다.
    - **끝나면 표에서 뺀다.** 남겨 두면 종료 뒤의 `kill_active()`가 이미 끝난 프로세스를
      본다 (죽이지는 않지만 "몇 개를 죽였다"가 거짓이 된다).

    `staged`를 표에 함께 싣는다 (#36). 죽인 렌더의 임시 파일을 치우는 것은 `kill_active`이고,
    그쪽은 프로세스만 들고 있어서는 무엇을 지워야 할지 알 수 없다 — `output`은 아직 놓이지
    않은 최종 경로라 지울 대상이 아니다.
    """
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=(errors := tempfile.TemporaryFile("w+", encoding="utf-8")),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as error:
        errors.close()
        raise RenderError(
            "ffmpeg를 찾을 수 없다. FFmpeg를 설치하고 PATH에 넣는다"
        ) from error

    with _ACTIVE_LOCK:
        _ACTIVE[process] = staged
    reader = threading.Thread(
        target=_watch,
        args=(process,),
        kwargs={"timeline": timeline, "on_progress": on_progress},
        daemon=True,
    )
    reader.start()
    try:
        try:
            process.wait(timeout=FFMPEG_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RenderError(_timeout_message(output)) from None
        # 마지막 묶음(`progress=end`)이 아직 읽히는 중일 수 있다. 끝을 기다리는 이유는
        # 100%가 화면에 서야 완료 카드로 넘어가는 것이 갑작스럽지 않기 때문이다.
        reader.join(timeout=_READER_JOIN_SEC)
        return process.returncode, _read_all(errors)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(process, None)
        if process.stdout is not None:
            process.stdout.close()
        errors.close()


def _watch(
    process: subprocess.Popen[str],
    *,
    timeline: Timeline,
    on_progress: ProgressHook | None,
) -> None:
    """`-progress` 줄을 끝까지 읽으며 훅을 부른다. **스레드에서 돈다.**

    출력은 `key=value` 줄의 묶음이고 묶음마다 `progress=continue|end`로 끝난다. **`frame`
    하나만 읽는다** — `out_time_us`도 오지만 두 값을 함께 쓰면 어느 쪽이 진행의 기준인지가
    둘로 갈리고, 프레임 정렬을 소유한 이 모듈의 단위는 프레임이다 (#19).

    **예외를 밖으로 내지 않는다.** 이 스레드가 죽어서 잃는 것은 진행 표시뿐이고, 렌더 자체는
    `wait`에 서 있는 쪽이 끝까지 본다 — 읽다가 깨졌다고 렌더를 실패로 만들 이유가 없다.
    """
    stream = process.stdout
    if stream is None:  # pragma: no cover - Popen(stdout=PIPE)이므로 항상 있다
        return

    try:
        for line in stream:
            key, _, value = line.strip().partition("=")
            if key != "frame" or on_progress is None:
                continue
            try:
                frame = int(value)
            except ValueError:  # pragma: no cover - ffmpeg가 수를 준다
                continue
            on_progress(
                RenderProgress(
                    frame=frame,
                    total_frames=timeline.total_frames,
                    scene_index=_scene_at(frame, timeline),
                )
            )
    except Exception:  # noqa: BLE001 - 진행 표시가 렌더를 실패시키지 않는다
        LOGGER.debug("진행 줄을 더 읽지 못했다", exc_info=True)


def _timeout_message(output: Path) -> str:
    return f"ffmpeg가 {FFMPEG_TIMEOUT_SEC}초 안에 끝나지 않았다: {output}"


def _scene_at(frame: int, timeline: Timeline) -> int:
    """프레임 번호 → 장면 인덱스. 마지막을 넘어선 값은 마지막 장면이다.

    **`frame`은 "지금까지 인코딩한 개수"라 1부터 센다.** 그대로 구간에 대면 장면 경계에서
    다음 장면이 한 프레임 일찍 보고되므로 1을 뺀다.
    """
    position = max(0, frame - 1)
    for index, (start, end) in enumerate(timeline.frame_spans):
        if start <= position < end:
            return index
    return max(0, len(timeline.frames) - 1)


def _read_all(stream: IO[str]) -> str:
    """임시 파일에 쌓인 stderr 전문. 읽기 전에 처음으로 되감는다."""
    stream.seek(0)
    return stream.read().strip()


def build_overlays(
    project: Mapping[str, Any], scenes: Mapping[str, Any], *, timeline: Timeline
) -> list[str]:
    """장면 위에 그릴 `drawtext` 필터 목록 (#20~#22). 수치는 `overlay`가 소유한다.

    **재료를 `project.json`에서 읽는다.** 자막 스타일·폰트·cta 문구를 config에서 다시 읽으면
    값을 정하는 경로가 둘이 되고, 앱(#29)이 프로젝트를 편집해도 CLI 렌더가 그것을 무시한다
    (PRD 7.10, `project.py`).

    Raises:
        RenderError: 프리셋 이름·폰트 경로·cta 문구가 화면으로 옮겨지지 않을 때.
    """
    render_section = project.get("render", {})
    try:
        return overlay.build(
            scenes,
            timeline.frame_spans,
            fps=timeline.fps,
            style=overlay.style_for(_text_field(project, "caption_style")),
            fonts=overlay.resolve_fonts(render_section.get("font_path")),
            cta_punch=_text_field(project, "cta_punch"),
            cta_tail=_text_field(project, "cta_tail"),
            caption_onset=_number_field(project, "caption_onset_sec"),
        )
    except OverlayError as error:
        # 오버레이 실패도 렌더 실패다. 부르는 쪽(main)이 잡는 예외를 하나로 유지한다.
        raise RenderError(str(error)) from error


def build_audio(
    project: Mapping[str, Any], scenes: Mapping[str, Any], *, timeline: Timeline
) -> AudioChain:
    """낭독 위에 효과음을 얹는 오디오 체인 (#23). 트리거 시각은 `audio_mix`가 소유한다.

    **게인 둘을 `project.json`에서 읽는다.** 배경·자막 스타일과 같은 이유로 config를 다시 열지
    않는다 — 앱(#81)이 트랙 볼륨을 편집하면 CLI 렌더가 그 값으로 돌아야 한다 (PRD 7.10).

    Raises:
        RenderError: `sfx` 이름이 번들에 없거나 게인 값이 음수일 때.
    """
    try:
        return audio_mix.build(
            scenes,
            timeline.frame_spans,
            fps=timeline.fps,
            sfx_volume=_audio_number(project, "sfx_volume"),
            # **없으면 기본값이다** (#81). 이 필드가 생기기 전에 만들어진 run 디렉터리가
            # 렌더까지 가야 하므로, 없는 것은 계약 위반이 아니라 "원본 레벨"이다.
            voice_volume=_audio_number(
                project, "voice_volume", default=DEFAULT_VOICE_VOLUME
            ),
        )
    except AudioMixError as error:
        # 오디오 체인 실패도 렌더 실패다. 부르는 쪽(main)이 잡는 예외를 하나로 유지한다.
        raise RenderError(str(error)) from error


def build_command(
    project: Mapping[str, Any],
    *,
    run_dir: Path,
    total_sec: float,
    overlays: Sequence[str] = (),
    audio: AudioChain | None = None,
    destination: Path | None = None,
) -> list[str]:
    """렌더 명령을 만든다. **FFmpeg를 부르지 않으므로 어디서나 검증할 수 있다.**

    Args:
        overlays: 배경 위에 순서대로 이을 필터 문자열 (`build_overlays`). 비어 있으면
            배경만 그린다 — #19까지의 상태다.
        audio: 오디오 필터 체인 (`build_audio`). `None`이면 낭독만 담는다 — 효과음이 없는
            장면 목록과 같은 결과이고, #22까지의 명령과 같은 모양이다.
        destination: 쓸 자리. `None`이면 `project.json`의 `render.output`이다. `render`가
            임시 파일을 주는 자리이고(#36), **그 파일도 확장자가 같아야 한다** — FFmpeg는
            출력 형식을 확장자로 정한다.

    Raises:
        RenderError: `project.json`의 배경·규격 값을 명령으로 옮길 수 없을 때.
    """
    chain = audio if audio is not None else audio_mix.voice_only()
    fps = _int_field(project, "fps")
    output = destination or run_dir / str(project["render"]["output"])
    length = f"{total_sec:.3f}"

    video_input, video_chain = _video_stage(
        project, run_dir=run_dir, length=length, overlays=overlays
    )
    audio_input = _audio_input(project.get("audio", {}), run_dir=run_dir, length=length)

    # 효과음 입력은 낭독 뒤에 붙는다. `audio_mix`가 그 순서로 인덱스를 매긴다.
    command = ["ffmpeg", "-y", "-v", "error", *video_input, *audio_input, *chain.inputs]
    command += [
        "-filter_complex",
        ";".join(
            [
                f"[0:v]{video_chain}[video]",
                # 오디오는 낭독 하나이거나 효과음이 얹힌 믹스다 (#23). 어느 쪽이든 마지막
                # 단계가 `[audio]`를 내고, 스트림 수는 정확히 하나로 유지된다 (PRD 7.7).
                *chain.steps,
            ]
        ),
        "-map",
        "[video]",
        "-map",
        "[audio]",
        "-c:v",
        "libx264",
        "-crf",
        str(CRF),
        # 일반 재생기와 YouTube가 요구하는 크로마 서브샘플링 (PRD 6.3).
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        _AUDIO_BITRATE,
        # 총 길이를 여기서 끊는다. 프레임 정렬 값이므로 마지막 프레임이 잘리지 않는다.
        "-t",
        length,
        "-movflags",
        "+faststart",
        str(output),
    ]
    return command


# --- 프리뷰 (#27) ---------------------------------------------------------------


def representative_frames(timeline: Timeline) -> tuple[int, ...]:
    """장면별 대표 프레임 번호 — **장면 한가운데다.**

    경계를 피하는 것이 목적이다. 시작 프레임은 카운트다운의 첫 숫자나 정답 확대의 첫 크기처럼
    "아직 시작하지 않은" 그림을 잡고, 끝 프레임은 다음 장면과 한 프레임 차이라 어느 장면을
    고른 것인지 화면에서 구분되지 않는다.
    """
    return tuple((start + end) // 2 for start, end in timeline.frame_spans)


def build_preview_command(
    project: Mapping[str, Any],
    *,
    run_dir: Path,
    total_sec: float,
    frames: Sequence[int],
    out_dir: Path,
    overlays: Sequence[str] = (),
) -> list[str]:
    """대표 프레임들을 PNG로 뽑는 명령. **FFmpeg를 부르지 않는다** (`build_command`와 같다).

    최종 렌더 명령과 배경·오버레이를 `_video_stage` 하나에서 함께 받고, 여기서 갈라지는 것은
    오디오가 없다는 것과 인코더가 PNG라는 것 둘뿐이다. 프로토타입에서 밟은 함정 둘이 그
    갈림에 있다 (스파이크 #25 6.1).

    - **오디오는 체인째 들어낸다.** `-map [audio]`만 빼면 `alimiter`의 출력이 어디에도
      연결되지 않아 그래프 바인딩이 실패한다. 그래서 이 명령은 오디오 입력도 필터도 만들지
      않는다 — 지우는 것이 아니라 처음부터 없다
    - **`-c:v png`를 명시한다.** `libx264`가 남은 채 확장자만 `.png`로 주면 경고 없이 H.264
      비트스트림이 그 파일에 쓰이고, 눈으로 열어 보기 전까지 성공처럼 보인다

    파일명은 **요청 순서의 순번이지 장면 인덱스가 아니다** — image2 muxer가 1부터 세며 쓰고,
    장면 인덱스로 바꿔 놓는 것은 `preview`다.
    """
    if not frames:
        raise RenderError("프리뷰할 프레임이 없다")

    video_input, video_chain = _video_stage(
        project, run_dir=run_dir, length=f"{total_sec:.3f}", overlays=overlays
    )

    # `n`은 필터 입력의 프레임 번호이고 `Timeline.frame_spans`가 세는 것과 같은 값이다.
    # 쉼표는 필터 인자 구분자라 이스케이프한다.
    select = "+".join(rf"eq(n\,{number})" for number in frames)
    return [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        *video_input,
        "-filter_complex",
        f"[0:v]{video_chain},select='{select}'[video]",
        "-map",
        "[video]",
        # **여기가 비용을 정한다.** 마지막 요청 프레임까지만 필터를 돌고 끝난다 — 프레임마다
        # 프로세스를 띄우는 것과 다른 점이 이것이다 (스파이크 #25 6장의 기각 사유).
        "-frames:v",
        str(len(frames)),
        # `select`가 버린 프레임 자리를 복제해 채우지 않게 한다. 기본 동작이면 요청한 수보다
        # 먼저 상한에 닿아 뒤쪽 장면이 빈다.
        "-fps_mode",
        "passthrough",
        "-c:v",
        "png",
        "-f",
        "image2",
        str(out_dir / _SEQUENCE_PATTERN),
    ]


def preview(
    project: Mapping[str, Any],
    scenes: Mapping[str, Any],
    *,
    run_dir: Path,
    out_dir: Path,
    indices: Sequence[int] | None = None,
) -> dict[int, Path]:
    """장면 대표 프레임을 만들고 `{장면 인덱스: PNG 경로}`를 돌려준다 (PRD 7.9).

    **요청한 장면 전부를 프로세스 하나가 만든다.** 프리뷰 비용의 바닥은 필터가 아니라 FFmpeg
    기동(이 머신에서 1.1~1.3초)이라, 장면마다 띄우면 그 바닥을 장면 수만큼 낸다. 11장면
    실측으로 장면별 19.1초 / 배치 2.2초였고 나온 PNG는 바이트까지 같았다
    (`docs/spikes/27-preview-frames.md`).

    Args:
        project: `project.json` 내용. **앱이 편집 중인 값을 그대로 받는다** — 저장하지 않은
            편집이 프리뷰에 보이지 않으면 프리뷰가 편집 도구가 되지 못한다.
        scenes: 확정 `scenes.json` 내용.
        out_dir: PNG를 쓸 디렉터리. 이 함수는 만들기만 하고 지우지 않는다 — 언제 버릴지는
            캐시를 소유하는 쪽(`api`)이 안다.
        indices: 만들 장면. `None`이면 전부다. **마지막 요청 장면까지 필터가 도는 구조라
            뒤쪽 장면 하나를 요청하는 비용이 전부를 요청하는 비용과 거의 같다** — 그래서
            부르는 쪽의 기본은 전부다.

    Raises:
        RenderError: 장면 인덱스가 범위를 벗어났거나 FFmpeg가 없거나 실패했을 때.
        SchemaError: 장면 목록이 확정 상태가 아닐 때.
    """
    validate_scenes_final(scenes)
    # 최종 렌더와 같은 자리다 (#82). 여기서 얹지 않으면 사람이 길이를 고쳐도 프레임의 장면
    # 경계가 옛 값에 머문다 — 편집이 프리뷰에 반영되지 않는다.
    scenes = apply_scene_overrides(project, scenes)

    timeline = align(scenes, fps=_int_field(project, "fps"))
    numbers = representative_frames(timeline)
    wanted = tuple(range(len(numbers))) if indices is None else tuple(sorted(set(indices)))
    for index in wanted:
        if not 0 <= index < len(numbers):
            raise RenderError(
                f"장면 인덱스가 범위를 벗어났다: {index} (장면 {len(numbers)}개)"
            )

    overlays = build_overlays(project, scenes, timeline=timeline)
    out_dir.mkdir(parents=True, exist_ok=True)
    command = build_preview_command(
        project,
        run_dir=run_dir,
        total_sec=timeline.total_sec,
        frames=[numbers[index] for index in wanted],
        out_dir=out_dir,
        overlays=overlays,
    )

    LOGGER.debug("프리뷰 명령 %s", subprocess.list2cmdline(command))
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PREVIEW_TIMEOUT_SEC,
            # **stdin을 막는다.** ffmpeg는 stdin을 조작 입력으로 읽으므로, 앱이 띄운
            # 백엔드에서 그대로 두면 프로토콜 줄을 가져가고 이 호출이 끝나지 않는다
            # (스파이크 #25 7장, #27에서 실제로 밟았다).
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as error:
        raise RenderError("ffmpeg를 찾을 수 없다. FFmpeg를 설치하고 PATH에 넣는다") from error
    except subprocess.TimeoutExpired as error:
        raise RenderError(
            f"ffmpeg가 {PREVIEW_TIMEOUT_SEC}초 안에 프리뷰 프레임을 내지 않았다"
        ) from error

    if completed.returncode != 0:
        LOGGER.error("ffmpeg stderr %s", completed.stderr.strip())
        raise RenderError(
            f"프리뷰 프레임을 만들지 못했다 — ffmpeg 종료 코드 {completed.returncode}, "
            f"stderr {completed.stderr.strip()!r}"
        )

    # 순번 파일을 장면 인덱스로 바꾼다. 여기까지가 이 모듈의 계약이고, 부르는 쪽은 순번을
    # 다시 계산하지 않는다 — 일부만 요청했을 때 순번과 인덱스가 어긋나는 자리다.
    produced: dict[int, Path] = {}
    for position, index in enumerate(wanted, start=1):
        source = out_dir / (_SEQUENCE_PATTERN % position)
        if not source.is_file():
            raise RenderError(
                f"ffmpeg가 장면 {index}의 프레임을 내지 않았다: {source.name}"
            )
        target = out_dir / PREVIEW_NAME.format(index=index)
        source.replace(target)
        produced[index] = target
    return produced


def _video_stage(
    project: Mapping[str, Any],
    *,
    run_dir: Path,
    length: str,
    overlays: Sequence[str],
) -> tuple[list[str], str]:
    """배경 입력과 `[0:v]`에 걸 필터 체인 — **최종 렌더와 프리뷰가 갈라지는 지점이다.**

    두 명령이 같은 그림을 내는 이유가 이 함수 하나를 지나기 때문이다 (PRD 7.9). 프로토타입은
    완성된 명령 리스트에서 오디오 인자를 골라내는 방식이었고, 그 구조는 렌더러가 바뀌는 순간
    조용히 깨진다 (스파이크 #25 6.1). 갈라지는 것은 이 뒤 — 인코더와 오디오 — 뿐이다.

    Returns:
        (배경 입력 인자, `[0:v]` 뒤에 붙일 필터 체인). 체인에는 출력 레이블이 없다.
    """
    video_input, background_chain = _background(
        project.get("background", {}),
        run_dir=run_dir,
        width=_int_field(project, "width"),
        height=_int_field(project, "height"),
        fps=_int_field(project, "fps"),
        length=length,
    )
    # 오버레이는 배경 체인 뒤에 이어 붙는다. 목록 순서가 그리는 순서다.
    return video_input, ",".join([background_chain, *overlays])


def _background(
    background: Mapping[str, Any],
    *,
    run_dir: Path,
    width: int,
    height: int,
    fps: int,
    length: str,
) -> tuple[list[str], str]:
    """배경 하나를 (입력 인자, `[0:v]`에 걸 필터 체인)으로 옮긴다 (PRD 14.1의 배경 소스).

    **CLI 경로가 실제로 지나는 것은 `preset`뿐이다** — 파이프라인이 만드는 초기 상태가 그것이고,
    `image`·`video`로 바꾸는 주체는 앱이다 (#80). `color`는 아직 사람이 `project.json`을 손으로
    고치는 경로뿐이라 단위 테스트가 지킨다.
    """
    kind = background.get("kind")
    value = str(background.get("value", ""))
    size = f"{width}x{height}"

    if kind == "preset":
        try:
            preset = background_presets()[value]
        except AssetError as error:
            raise RenderError(f"배경 프리셋을 읽을 수 없다 — {error}") from error
        except KeyError:
            known = ", ".join(background_presets())
            raise RenderError(
                f"모르는 배경 프리셋이다: {value!r}. 쓸 수 있는 이름: {known}"
            ) from None

        if preset.is_gradient:
            # **`y1`은 높이-1이다.** 높이를 그대로 주면 범위를 벗어난 값이라 필터가 회전
            # 그라디언트 기본 동작으로 조용히 되돌아간다 (D1 확정 스펙 6.2).
            # **`speed=0`도 필수다.** 기본값 0.01이면 그라디언트가 시간에 따라 회전해
            # 50초 지점에서 위아래가 뒤집힌다 — 경고 없이 그림만 달라진다 (#19에서 실측).
            source = (
                f"gradients=s={size}:r={fps}:"
                f"c0={_color(preset.top)}:c1={_color(preset.bottom)}:"
                f"x0=0:y0=0:x1=0:y1={height - 1}:speed=0"
            )
        else:
            source = f"color=c={_color(preset.top)}:s={size}:r={fps}"
        return ["-f", "lavfi", "-t", length, "-i", source], "setsar=1"

    if kind == "color":
        source = f"color=c={_color(value)}:s={size}:r={fps}"
        return ["-f", "lavfi", "-t", length, "-i", source], "setsar=1"

    if kind == "image":
        # 정지 이미지를 영상 길이만큼 반복한다.
        path = str(_background_file(value, kind, run_dir))
        return (
            ["-loop", "1", "-framerate", str(fps), "-t", length, "-i", path],
            _fill(width, height),
        )

    if kind == "video":
        # 배경 영상이 짧으면 되감아 채운다. 길면 `-t`가 끊는다.
        path = str(_background_file(value, kind, run_dir))
        return (
            ["-stream_loop", "-1", "-t", length, "-i", path],
            f"{_fill(width, height)},fps={fps}",
        )

    raise RenderError(
        f"모르는 배경 종류다: {kind!r}. 쓸 수 있는 값: {', '.join(BACKGROUND_KINDS)}"
    )


def _fill(width: int, height: int) -> str:
    """세로 캔버스를 비율 왜곡 없이 채운다. 넘치는 쪽을 잘라 빈 영역을 남기지 않는다."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def _audio_input(
    audio: Mapping[str, Any], *, run_dir: Path, length: str
) -> list[str]:
    """오디오 입력 하나. 낭독이 없으면 무음 트랙이다 — 스트림 수가 입력에 따라 달라지면
    효과음을 얹는 #23이 인덱스를 조건부로 계산해야 한다."""
    voice = audio.get("voice")
    if voice is None:
        return [
            "-f",
            "lavfi",
            "-t",
            length,
            "-i",
            f"anullsrc=r={audio_mix.SAMPLE_RATE}:cl={audio_mix.CHANNEL_LAYOUT}",
        ]

    path = _source_file(str(voice), run_dir)
    return ["-i", str(path)]


def _background_file(value: str, kind: str, run_dir: Path) -> Path:
    """배경 파일 경로를 확인하고 푼다 (#80).

    **선언한 `kind`와 확장자가 같은 것을 가리켜야 한다.** 앱은 확장자에서 `kind`를 정하지만
    (`background_kind`) 사람이 손으로 고친 `project.json`도 열리므로, `.png`에 `video`가 붙은
    상태를 그대로 두면 `-stream_loop`가 정지 이미지에 걸려 결과가 조용히 달라진다.
    """
    declared = background_kind(value)
    if declared != kind:
        raise RenderError(
            f"배경 파일의 확장자가 {declared}인데 kind는 {kind!r}이다: {value}"
        )
    return _source_file(value, run_dir)


def _source_file(value: str, run_dir: Path) -> Path:
    """`project.json`의 파일 경로를 푼다.

    경로 값은 run 디렉터리 기준 상대 경로다 (PRD 7.10). 사용자가 자기 배경 파일을 절대
    경로로 적는 것도 받는다 — 그 파일은 run 디렉터리 밖에 있다.
    """
    if not value:
        raise RenderError("파일 경로가 비어 있다")

    path = Path(value)
    resolved = path if path.is_absolute() else run_dir / path
    if not resolved.is_file():
        raise RenderError(f"파일을 찾을 수 없다: {resolved}")
    return resolved


def _color(value: str) -> str:
    """`#RRGGBB` → `0xRRGGBB`. FFmpeg의 색 문법이다."""
    match = _HEX_COLOR.match(value.strip())
    if match is None:
        raise RenderError(f"색은 #RRGGBB 표기여야 한다. 받은 값: {value!r}")
    return f"0x{match.group(1).upper()}"


def _text_field(project: Mapping[str, Any], key: str) -> str:
    """`render` 섹션의 문자열 값. 없으면 어느 키인지 말한다 — 앱이 만든 프로젝트나 사람이
    편집한 파일이 스키마를 지나지 않고 직접 들어올 수 있다."""
    try:
        value = project["render"][key]
    except (KeyError, TypeError):
        raise RenderError(f"project.json의 render.{key}가 없다") from None
    if not isinstance(value, str):
        raise RenderError(f"render.{key}는 문자열이어야 한다. 받은 값: {value!r}")
    return value


def _number_field(project: Mapping[str, Any], key: str) -> float:
    """`render` 섹션의 실수 값. 정수도 받는다 — `caption_onset_sec: 1`은 1.0이다."""
    try:
        value = project["render"][key]
    except (KeyError, TypeError):
        raise RenderError(f"project.json의 render.{key}가 없다") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RenderError(f"render.{key}는 0 이상의 수여야 한다. 받은 값: {value!r}")
    return float(value)


def _audio_number(
    project: Mapping[str, Any], key: str, *, default: float | None = None
) -> float:
    """`audio` 섹션의 실수 값 (#23). 정수도 받는다 — `sfx_volume: 1`은 1.0이다.

    `render` 섹션이 아닌 이유는 이것이 트랙의 성격을 정하는 값이기 때문이다. 트랙별 볼륨이
    같은 자리에 있다 (PRD 7.10, #81).

    Args:
        default: 키가 없을 때 쓸 값. **스키마에서 선택인 필드에만 준다** (#81의
            `voice_volume`) — 필수 필드에 기본값을 주면 앱이 만든 잘못된 프로젝트가 조용히
            다른 레벨로 렌더된다.
    """
    try:
        value = project["audio"][key]
    except (KeyError, TypeError):
        if default is not None:
            return default
        raise RenderError(f"project.json의 audio.{key}가 없다") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RenderError(f"audio.{key}는 0 이상의 수여야 한다. 받은 값: {value!r}")
    return float(value)


def _int_field(project: Mapping[str, Any], key: str) -> int:
    """`render` 섹션의 정수 값. 스키마가 이미 확인하지만, 앱이 만든 프로젝트도 직접 들어온다."""
    try:
        value = project["render"][key]
    except (KeyError, TypeError):
        raise RenderError(f"project.json의 render.{key}가 없다") from None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RenderError(f"render.{key}는 1 이상의 정수여야 한다. 받은 값: {value!r}")
    return value
