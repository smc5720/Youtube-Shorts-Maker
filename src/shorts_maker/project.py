"""확정 `scenes.json` → `project.json` 초기 상태 (PRD 7.10, 이슈 #19).

**렌더러의 입력 계약이다.** 배경·오디오·출력 규격을 렌더러가 config에서 따로 읽으면 같은
값을 정하는 경로가 둘이 되고, 앱(#26)이 `project.json`을 편집해도 CLI 렌더가 그 편집을
무시한다. 그래서 config → `project.json` → 렌더러 한 방향만 둔다.

- **렌더보다 먼저 쓴다.** `project.json`은 항상 생성되고 `final_short.mp4`는 렌더 성공 시에만
  생성된다 (PRD 6.2 표). 렌더가 실패한 run에도 이 파일이 남아야 사람이 값을 고쳐 다시 돌릴
  수 있다.
- **편집 상태로 여는 것은 `review` 하나다** (#28) — 사람의 검수 기록이라 렌더러가 읽지 않고
  값도 비어 있는 채로 시작한다. **자막 스타일·폰트·cta 문구는 편집 상태가 아니라 초기
  상태다** — 렌더러가 실제로 읽는 값이므로 배경과 같은 자리를 지나야 한다. 여기 없으면 렌더가
  config를 직접 열게 되고, 그 순간 앱이 편집한 프로젝트와 CLI 렌더가 갈린다 (#20).
- **이 단계도 타입을 모른다.** `type`은 장면 목록이 들고 있는 값을 옮길 뿐이다.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .schemas.project import SCHEMA_VERSION, validate_project
from .schemas.scenes import SCENES_SCHEMA, validate_scenes_final
from .timeline import VOICE_TRACK
from .video_renderer import CANVAS_HEIGHT, CANVAS_WIDTH, FPS, OUTPUT_NAME

if TYPE_CHECKING:
    from .config import Config

LANGUAGE = "ko"
"""한국어 1차 타깃 (PRD 14.1). 확장 여지는 `project.json`의 `language` 필드가 담는다.

`metadata_generator.LANGUAGE`와 같은 값을 따로 두는 이유는 두 산출물이 서로를 읽지 않기
때문이다. 언어가 실제로 갈리는 시점에 장면 스키마에 필드를 열거나 config 키를 여는 쪽이
맞고, 그때 두 곳이 함께 그 값을 읽는다.
"""

BACKGROUND_PRESET = "preset"
"""`render.background` 설정이 만드는 `background.kind`.

설정은 번들 프리셋 이름만 받으므로(`config.SPEC`의 `choices`) CLI 경로가 만드는 종류는 이
하나다. `color`·`image`·`video`는 사람이나 앱(#29)이 `project.json`을 편집해 넣는 값이고,
렌더러는 네 종류를 모두 그린다 — 생성 경로가 좁은 것과 소비 경로가 좁은 것은 다른 문제다.
"""


def build(scenes: Mapping[str, Any], *, config: Config, run_dir: Path) -> dict[str, Any]:
    """`project.json`의 초기 상태를 만든다 (PRD 7.10).

    Args:
        scenes: `scenes.json` 내용. **확정 상태여야 한다** — 이 파일이 가리키는 장면 목록이
            렌더 입력이므로, 초안을 가리키는 `project.json`은 열 수 없는 프로젝트다.
        config: `render` 섹션과 `timing.caption_onset_sec`를 읽는다.
        run_dir: 이번 run의 출력 디렉터리. 합성 트랙의 존재 여부를 여기서 본다.

    Raises:
        SchemaError: 입력이 확정 상태가 아니거나 결과가 스키마를 만족하지 않을 때.
    """
    validate_scenes_final(scenes)

    project = {
        "schema_version": SCHEMA_VERSION,
        # 장면이 들고 있는 값을 옮긴다. 이 모듈이 타입 이름을 아는 것이 아니다.
        "type": scenes["type"],
        "language": LANGUAGE,
        # 장면 배열을 복사하지 않는다 (PRD 7.4.1). 파일명은 스키마가 확정한다.
        "scenes": SCENES_SCHEMA.name,
        "background": {
            "kind": BACKGROUND_PRESET,
            "value": str(config.get("render.background")),
        },
        "audio": {
            # **파일이 있는지로 정한다.** "낭독 장면이 없으면 null"이라는 조건을 여기 다시
            # 적으면 트랙을 만드는 쪽(`timeline.finalize`)과 두 곳에서 갈린다.
            "voice": VOICE_TRACK if (run_dir / VOICE_TRACK).is_file() else None,
            # 배경음악은 사용자가 라이선스를 확인한 파일만 넣는다 (PRD 8장). #35까지 없음이다.
            "music": None,
            # 효과음 게인 (#23). 렌더러가 읽는 값이므로 배경·자막 스타일과 같은 자리를
            # 지난다 — config를 렌더가 다시 열면 앱이 편집한 값이 무시된다 (PRD 7.10).
            "sfx_volume": float(config.get("audio.sfx_volume")),
        },
        # 6.3의 영상 규격. 값의 단일 진실 공급원은 렌더러이고 여기는 옮겨 담는다.
        "render": {
            "width": CANVAS_WIDTH,
            "height": CANVAS_HEIGHT,
            "fps": FPS,
            "output": OUTPUT_NAME,
            # 번인 오버레이가 읽는 값 (#20). 배경과 같은 이유로 여기를 지난다 — 렌더러가
            # config를 다시 열면 앱이 편집한 프로젝트와 CLI 렌더가 갈린다.
            "caption_style": str(config.get("render.caption_style")),
            "font_path": _optional(config.get("render.font_path")),
            "cta_punch": str(config.get("render.cta_punch")),
            "cta_tail": str(config.get("render.cta_tail")),
            # **`timing` 섹션에서 온다** (#22). 장면 길이 하한을 계산한 #16이 읽은 값과
            # 같아야 해설을 다 읽기 전에 장면이 끝나지 않는다 — 그래서 렌더러가 자기
            # 상수를 갖지 않고 이 자리를 지난다 (확정 스펙 4장).
            "caption_onset_sec": float(config.get("timing.caption_onset_sec")),
        },
        # **여기만 렌더러가 읽지 않는다** (#28). 사람이 `flagged`를 확인한 기록과 낭독 문구가
        # 바뀐 항목을 앱이 여기 남긴다 — 검증기가 소유하는 `verify`에 쓸 수 없기 때문이다
        # (D2 확정 스펙 1.4, PRD 14.2). 생성 직후에는 확인할 것도 낡은 것도 없다.
        "review": {"acknowledged": [], "stale": []},
    }

    # 쓰기 전에 검증한다. 앱(#26)이 여는 파일이고, 계약을 어긴 상태로 남으면 그쪽에서
    # 원인이 드러나지 않는다.
    validate_project(project)
    return project


def _optional(value: Any) -> str | None:
    """nullable 설정값을 그대로 옮긴다. `str(None)`이 `"None"`이 되는 것을 막는다."""
    return None if value is None else str(value)
