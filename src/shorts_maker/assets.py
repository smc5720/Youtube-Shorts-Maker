"""번들 에셋 조회 — 폰트 파일과 프리셋 정의값 (PRD 8·11장, 이슈 #38).

`assets/`에 커밋된 것을 이름으로 찾는 **단일 지점**이다. 렌더러(#19~#22)와 앱(#29)이
같은 이름으로 같은 값을 얻어야 하므로, 색과 파일명을 소비하는 쪽에 적지 않는다.

- **프리셋 이름은 여기가 확정한다.** `project.json`의 `background.value`(kind가 `preset`일 때)
  와 `render.background` / `render.caption_style` 설정이 그 이름을 쓴다. 배경은 장면별 값이
  아니라 프로젝트 단위 값이다 (PRD 14.1).
- **색은 `assets/*/presets.json`에 있고 이 모듈은 읽기만 한다.** 파이썬 리터럴로 옮겨 적으면
  앱이 같은 표를 두 번째로 갖게 된다 — 편집 앱은 파이썬을 통과하지 않는 경로(#25 미결)를
  가질 수 있다.
- **레이아웃 수치는 여기 없다.** D1 확정 스펙 5장의 y·폰트 크기·요소별 `borderw`는 렌더러
  (#20~#22)의 표다. 프리셋이 바꾸는 것은 색과 그림자뿐이고, 그래서 프리셋을 갈아도 레이아웃이
  회귀하지 않는다 (확정 스펙 6.1).

`assets/`는 wheel에 들어가지 않는다 (`pyproject.toml`의 packages.find는 `src`만 본다).
저장소에서 실행하는 CLI는 문제가 없고, 앱 동봉 시점의 경로 해석은 배포를 정하는 #25 이후의
일이다 — `assets/sfx`(#18)도 같은 상태다. 파일이 없으면 `AssetError`가 어느 경로를 찾았는지
말한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
"""저장소의 `assets/` 디렉터리. `src/shorts_maker/assets.py`에서 두 단계 위다."""

FONTS_DIR = ASSETS_DIR / "fonts"
BACKGROUNDS_FILE = ASSETS_DIR / "backgrounds" / "presets.json"
CAPTION_STYLES_FILE = ASSETS_DIR / "caption-styles" / "presets.json"

SCHEMA_VERSION = 1
"""프리셋 파일의 형식 버전. 소비하는 쪽이 늘어난 뒤 모양을 바꿀 때 쓴다."""

FONT_FILES: dict[int, str] = {
    800: "Pretendard-ExtraBold.otf",
    700: "Pretendard-Bold.otf",
    500: "Pretendard-Medium.otf",
}
"""웨이트 → 번들 파일명 (D1 확정 스펙 9장).

**파일 하나가 웨이트 하나다.** `drawtext`는 웨이트를 고를 수 없어서 가변 폰트를 쓸 수 없고,
그래서 스펙이 요구하는 세 웨이트를 각각 받는다. 800은 정답·카운트다운 숫자·hook·cta punch·
index, 700은 질문·kicker·meta·cta tail, 500은 해설 자막이다.
"""

HEX_COLOR = re.compile(r"^#[0-9A-F]{6}$")
"""`#RRGGBB` 대문자. `drawtext`/`drawbox`가 `0xRRGGBB`를 받으므로 형식이 하나여야 변환이
한 줄로 끝난다."""

CAPTION_COLOR_ROLES = (
    "body",
    "dimmed",
    "accent",
    "answer_onset",
    "secondary",
    "border",
    "answer_border",
)
"""자막 스타일이 정하는 색 역할 (D1 확정 스펙 6.1).

`answer_onset`은 정답이 등장하는 순간의 색이고 0.35초 뒤 `accent`로 바뀐다 (5.4). `dimmed`는
정답 장면에서 질문·index를 죽이는 색이다. `answer_border`는 정답만 외곽선색이 다른 P3 때문에
있고, 나머지 프리셋에서는 `border`와 같은 값을 명시한다 — 폴백 규칙을 두면 렌더러가 "다르면
쓰고 없으면 border"를 알아야 한다.
"""


class AssetError(Exception):
    """번들 에셋을 찾거나 읽을 수 없다."""


@dataclass(frozen=True)
class BackgroundPreset:
    """배경 프리셋 하나 (D1 확정 스펙 6.2).

    `stops`가 1개면 단색, 2개면 **수직 2스톱 그라디언트**이고 첫 값이 y=0(위), 둘째가
    y=1920(아래)이다. 종류를 따로 적지 않는 이유는 스톱 수와 어긋날 수 있는 값을 만들지
    않기 위함이다 — 확정 스펙이 수직 2스톱만 허용하므로 스톱 수가 종류를 결정한다.
    """

    name: str
    label: str
    stops: tuple[str, ...]

    @property
    def is_gradient(self) -> bool:
        return len(self.stops) == 2

    @property
    def top(self) -> str:
        return self.stops[0]

    @property
    def bottom(self) -> str:
        return self.stops[-1]


@dataclass(frozen=True)
class CaptionStyle:
    """자막 스타일 프리셋 하나 (D1 확정 스펙 6.1).

    `background`는 확정 스펙 6.3 조합 매트릭스에서 ◎ 기본으로 짝지어진 배경 프리셋 이름이다.
    **조합을 막는 값이 아니다** — 9조합 전부 사용 가능하고, 앱(#29)이 스타일을 고를 때 기본
    배경을 함께 제안하는 데 쓴다.
    """

    name: str
    label: str
    background: str
    colors: dict[str, str]
    shadow_alpha: float | None
    """`None`이면 그림자 없음 (P2 네온 민트). 요소별 그림자 오프셋과 P1 기준 불투명도는
    확정 스펙 5장의 표이고 렌더러(#20)가 소유한다 — 여기 있는 것은 프리셋의 기준값이다."""
    answer_border_bonus: int
    """정답 `borderw`에 더하는 값. 그림자를 안 쓰는 P2가 정답만 5→6으로 두껍게 한다 (6.1)."""

    def color(self, role: str) -> str:
        try:
            return self.colors[role]
        except KeyError:
            raise AssetError(
                f"자막 스타일 {self.name}에 없는 색 역할이다: {role}. "
                f"쓸 수 있는 역할: {', '.join(CAPTION_COLOR_ROLES)}"
            ) from None


def font_path(weight: int) -> Path:
    """번들 폰트 파일 경로. 웨이트는 `FONT_FILES`의 키다.

    **`render.font_path` 설정을 보지 않는다.** 사용자 지정 폰트와 번들 폰트 사이의 선택은
    렌더러(#20)가 하고, 여기는 번들에 무엇이 있는지만 안다.

    Raises:
        AssetError: 모르는 웨이트이거나 파일이 없을 때.
    """
    try:
        filename = FONT_FILES[weight]
    except KeyError:
        known = ", ".join(str(value) for value in sorted(FONT_FILES))
        raise AssetError(f"번들에 없는 폰트 웨이트다: {weight}. 있는 웨이트: {known}") from None

    path = FONTS_DIR / filename
    if not path.is_file():
        raise AssetError(f"번들 폰트가 없다: {path}")
    return path


@lru_cache(maxsize=1)
def background_presets() -> dict[str, BackgroundPreset]:
    """배경 프리셋 전체를 파일 정의 순서로 돌려준다."""
    presets: dict[str, BackgroundPreset] = {}
    for name, entry in _read_presets(BACKGROUNDS_FILE).items():
        where = f"{BACKGROUNDS_FILE.name}의 {name}"
        stops = _require(entry, "stops", list, where)
        if not 1 <= len(stops) <= 2:
            raise AssetError(f"{where}: stops는 1개(단색) 또는 2개(수직 그라디언트)다")
        presets[name] = BackgroundPreset(
            name=name,
            label=_require(entry, "label", str, where),
            stops=tuple(_color(stop, f"{where}의 stops") for stop in stops),
        )
    return presets


@lru_cache(maxsize=1)
def caption_styles() -> dict[str, CaptionStyle]:
    """자막 스타일 프리셋 전체를 파일 정의 순서로 돌려준다."""
    backgrounds = background_presets()
    styles: dict[str, CaptionStyle] = {}
    for name, entry in _read_presets(CAPTION_STYLES_FILE).items():
        where = f"{CAPTION_STYLES_FILE.name}의 {name}"
        background = _require(entry, "background", str, where)
        if background not in backgrounds:
            raise AssetError(
                f"{where}: 기본 배경 {background!r}이 배경 프리셋에 없다. "
                f"있는 이름: {', '.join(backgrounds)}"
            )

        raw_colors = _require(entry, "colors", dict, where)
        missing = [role for role in CAPTION_COLOR_ROLES if role not in raw_colors]
        if missing:
            raise AssetError(f"{where}: 빠진 색 역할 — {', '.join(missing)}")
        unknown = [role for role in raw_colors if role not in CAPTION_COLOR_ROLES]
        if unknown:
            raise AssetError(f"{where}: 모르는 색 역할 — {', '.join(unknown)}")

        alpha = entry.get("shadow_alpha")
        if alpha is not None and not (isinstance(alpha, (int, float)) and 0.0 < alpha <= 1.0):
            raise AssetError(f"{where}: shadow_alpha는 0 초과 1 이하의 수 또는 null이다")

        styles[name] = CaptionStyle(
            name=name,
            label=_require(entry, "label", str, where),
            background=background,
            colors={
                role: _color(raw_colors[role], f"{where}의 {role}")
                for role in CAPTION_COLOR_ROLES
            },
            shadow_alpha=None if alpha is None else float(alpha),
            answer_border_bonus=_require(entry, "answer_border_bonus", int, where),
        )
    return styles


def background_preset_names() -> tuple[str, ...]:
    """`render.background` 설정이 허용하는 이름 (config가 호출한다)."""
    return tuple(background_presets())


def caption_style_names() -> tuple[str, ...]:
    """`render.caption_style` 설정이 허용하는 이름 (config가 호출한다)."""
    return tuple(caption_styles())


def _read_presets(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AssetError(f"프리셋 파일이 없다: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise AssetError(f"프리셋 파일을 읽을 수 없다: {path} — {error}") from error
    except json.JSONDecodeError as error:
        raise AssetError(f"{path.name}: JSON 문법 오류 — {error}") from error

    version = data.get("schema_version") if isinstance(data, dict) else None
    if version != SCHEMA_VERSION:
        raise AssetError(f"{path.name}: schema_version이 {SCHEMA_VERSION}이어야 한다 (받은 값: {version!r})")

    presets = _require(data, "presets", dict, path.name)
    if not presets:
        raise AssetError(f"{path.name}: 프리셋이 하나도 없다")
    for name, entry in presets.items():
        if not isinstance(entry, dict):
            raise AssetError(f"{path.name}의 {name}: 키-값 매핑이어야 한다")
    return presets


def _require(node: dict[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in node:
        raise AssetError(f"{where}: {key} 항목이 없다")
    value = node[key]
    # bool은 int의 하위 타입이라 `answer_border_bonus: true`가 통과하면 안 된다.
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise AssetError(f"{where}: {key}의 형식이 맞지 않는다 (받은 값: {value!r})")
    return value


def _color(value: Any, where: str) -> str:
    if not isinstance(value, str) or not HEX_COLOR.match(value):
        raise AssetError(f"{where}: 색은 #RRGGBB 대문자 표기다 (받은 값: {value!r})")
    return value
