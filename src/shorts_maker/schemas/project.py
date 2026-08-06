"""`project.json` 스키마 — 편집 앱이 프로젝트를 여는 입구 (PRD 7.10).

**생성 직후의 초기 상태만 정의한다.** 앱이 쓰는 편집 상태(텍스트 오버레이 편집 이력, 자막
스타일 선택, 트랙별 볼륨 등)는 앱 프레임워크가 정해진 뒤 #26에서 붙인다 — 지금 정하면
#25 결과에 따라 다시 쓴다. 그래도 초기 상태가 지금 필요한 이유는 `project.json`이 항상
생성되는 산출물이기 때문이다 (PRD 6.2).

경로 값은 모두 **run 디렉터리 기준 상대 경로**다. run 디렉터리를 옮기거나 이름을 바꿔도
프로젝트가 열려야 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..shorts_types import SUPPORTED_TYPES
from .core import Object, Schema, integer, section, text

SCHEMA_VERSION = 1
KNOWN_VERSIONS = (1,)

BACKGROUND_KINDS = ("preset", "color", "image", "video")
"""`preset`은 번들 프리셋 이름, `color`는 색상 값, 나머지는 사용자 파일 경로다.

무료 이미지 API는 MVP 범위 밖이므로 원격 소스 종류를 두지 않는다 (PRD 14.1).
"""

_BACKGROUND_FIELDS = {
    "kind": text(choices=BACKGROUND_KINDS),
    "value": text(),
}

_AUDIO_FIELDS = {
    # 낭독 장면이 하나도 없으면 `voice.mp3`가 생성되지 않는다 (PRD 6.2). 그래서 null을
    # 받는다. 키 자체는 필수로 둬서 앱이 존재 여부를 확인하지 않고 읽을 수 있게 한다.
    "voice": text(nullable=True),
    # 라이선스를 확인한 파일만 사용자가 지정한다. 기본은 없음 (PRD 8장).
    "music": text(nullable=True),
}

_RENDER_FIELDS = {
    "width": integer(minimum=1),
    "height": integer(minimum=1),
    "fps": integer(minimum=1),
    "output": text(),
}

_ROOT = Object(
    {
        "schema_version": integer(minimum=1),
        "type": text(choices=SUPPORTED_TYPES),
        "language": text(),
        # `scenes.json` 참조. 장면 배열을 여기 복사하지 않는다 — 두 곳에 같은 장면이
        # 있으면 어느 쪽이 원본인지 모호해진다 (PRD 7.4.1).
        "scenes": text(),
        "background": section(_BACKGROUND_FIELDS),
        "audio": section(_AUDIO_FIELDS),
        "render": section(_RENDER_FIELDS),
    }
)

PROJECT_SCHEMA = Schema(name="project.json", versions=KNOWN_VERSIONS, root=_ROOT)


def validate_project(data: Any, *, source: Path | None = None) -> None:
    """`project.json` 초기 상태를 검증한다. 위반이 있으면 `SchemaError`."""
    PROJECT_SCHEMA.validate(data, source=source)


def load_project(path: Path) -> dict[str, Any]:
    """`project.json`을 읽고 검증해서 돌려준다."""
    return PROJECT_SCHEMA.load(path)
