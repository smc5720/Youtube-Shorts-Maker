"""번들된 폰트와 프리셋이 규격을 지키는지 — 이슈 #38의 완료 조건.

**바이너리와 데이터가 산출물인 이슈라서 테스트가 스펙의 유일한 상주 검사다** (#18의
`test_sfx_assets.py`와 같은 이유). 폰트는 사람이 교체할 수 있고 색값은 손으로 고칠 수
있는데, 어긋나면 렌더 결과나 라이선스 위반으로만 드러난다. 커밋된 파일을 여기서 직접
재면 교체 시점에 걸린다.

명도비 하한은 확정 스펙 6.3에서 온다. **그 절의 "9조합 전부 7:1 이상"은 사실이 아니었고**
이 이슈에서 실측으로 고쳤다 — 이 테스트가 그 측정의 상주 판본이다.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from shorts_maker.assets import (
    CAPTION_COLOR_ROLES,
    FONT_FILES,
    FONTS_DIR,
    AssetError,
    background_presets,
    caption_styles,
    font_path,
)
from shorts_maker.config import ConfigError, defaults, load_config

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg가 PATH에 없다"
)

HANGUL_SYLLABLES = range(0xAC00, 0xD7A4)
"""완성형 한글 음절 11,172자. 자막이 어떤 단어를 담을지 미리 알 수 없으므로 전 범위를 본다."""

DESIGN_GLYPHS = "0123456789Q?·%"
"""확정 스펙이 한글 외에 요구하는 문자 — index `Q2 / 4`, 카운트다운 숫자, meta의 가운뎃점."""

TEXT_ROLES = ("body", "dimmed", "accent", "answer_onset", "secondary")
"""배경 위에 직접 놓이는 색. `border`·`answer_border`는 글자와 배경 **사이**를 메우는
외곽선이라 배경 대비를 따지는 대상이 아니다."""

LARGE_TEXT_FLOOR = 3.0
"""WCAG 2.1 AA 대형 텍스트 하한. 번인 텍스트 중 가장 작은 것이 36px 해설(확정 스펙 5.4)이라
모든 요소가 "대형 텍스트"(18pt≈24px 이상) 범위 안이다."""

RECOMMENDED_FLOOR = 4.5
"""프리셋이 자기 기본 배경(확정 스펙 6.3의 ◎)과 짝일 때 강조색·본문색이 넘어야 하는 값.
대형 텍스트 AAA(4.5:1)이고, 일반 텍스트 AA와 같은 숫자다."""


# --- 폰트 ------------------------------------------------------------------


def font_files() -> list[tuple[int, Path]]:
    return [(weight, FONTS_DIR / name) for weight, name in sorted(FONT_FILES.items())]


def test_the_three_weights_the_spec_asks_for_are_bundled() -> None:
    """확정 스펙 9장이 지정한 웨이트다. `drawtext`가 웨이트를 고를 수 없어 파일 1개 = 웨이트
    1개이므로, 하나만 빠져도 그 웨이트를 쓰는 요소가 다른 굵기로 렌더된다."""
    assert set(FONT_FILES) == {800, 700, 500}

    for weight, path in font_files():
        assert path.is_file(), f"웨이트 {weight} 폰트가 없다: {path}"


@pytest.mark.parametrize(("weight", "path"), font_files(), ids=lambda value: str(value))
def test_each_file_really_is_the_weight_it_is_filed_under(weight: int, path: Path) -> None:
    """파일명이 아니라 폰트 안의 `usWeightClass`를 본다 — 이름만 바꿔 넣은 교체를 잡는다."""
    font = TTFont(path, lazy=True)

    assert font["OS/2"].usWeightClass == weight
    # 타이포그래피 패밀리(16)를 먼저 본다. 이름 16이 없는 웨이트에서만 이름 1이 패밀리이고,
    # Medium·ExtraBold는 이름 1이 "Pretendard Medium"처럼 스타일까지 담는다.
    names = {record.nameID: record.toUnicode() for record in font["name"].names}
    assert names.get(16, names[1]) == "Pretendard"


@pytest.mark.parametrize(("weight", "path"), font_files(), ids=lambda value: str(value))
def test_each_font_permits_embedding(weight: int, path: Path) -> None:
    """`fsType` 0 = Installable Embedding. 영상에 글리프를 심는 것을 폰트가 막지 않는다는
    뜻이고, OFL 허용과 별개로 파일 자체가 담고 있는 신호다 (`assets/fonts/CREDITS.md`)."""
    assert TTFont(path, lazy=True)["OS/2"].fsType == 0


@pytest.mark.parametrize(("weight", "path"), font_files(), ids=lambda value: str(value))
def test_each_font_covers_korean(weight: int, path: Path) -> None:
    """한글 음절 전체 + 확정 스펙이 쓰는 숫자·기호. 라틴 전용 폰트로 교체되면 여기서 걸린다."""
    cmap = TTFont(path, lazy=True).getBestCmap()

    missing_hangul = [code for code in HANGUL_SYLLABLES if code not in cmap]
    assert not missing_hangul, f"{path.name}: 한글 음절 {len(missing_hangul)}자가 없다"

    missing_glyphs = [char for char in DESIGN_GLYPHS if ord(char) not in cmap]
    assert not missing_glyphs, f"{path.name}: {''.join(missing_glyphs)}가 없다"


def test_the_license_original_sits_next_to_the_fonts() -> None:
    """OFL 조항 2가 사본마다 저작권 표시와 라이선스를 함께 담을 것을 요구한다. 이 파일이
    빠진 채 폰트만 재배포되면 라이선스 위반이다."""
    ofl = (FONTS_DIR / "OFL.txt").read_text(encoding="utf-8")

    assert "SIL OPEN FONT LICENSE Version 1.1" in ofl
    assert "Reserved Font Name Pretendard" in ofl


def test_every_bundled_file_is_credited() -> None:
    """CREDITS.md에 없는 파일이 섞여 들어오면 출처 없는 바이너리가 배포된다 (PRD 8장)."""
    credits = (FONTS_DIR / "CREDITS.md").read_text(encoding="utf-8")

    for path in sorted(FONTS_DIR.iterdir()):
        if path.name == "CREDITS.md":
            continue
        assert path.name in credits, f"{path.name}이 CREDITS.md에 없다"


def test_an_unknown_weight_says_what_is_bundled() -> None:
    with pytest.raises(AssetError) as raised:
        font_path(400)

    assert "500, 700, 800" in str(raised.value)


# --- 실제 FFmpeg 렌더 -------------------------------------------------------


def ink_pixels(path: Path, text: str) -> int:
    """검은 배경에 흰 글자를 그리고 밝은 픽셀 수를 센다.

    글리프가 실제로 그려졌는지 확인하는 가장 짧은 방법이다. `fontfile`의 Windows 경로는
    콜론을 이스케이프해야 필터 문법이 깨지지 않는다 (#17에서 확인).
    """
    escaped = str(path).replace("\\", "/").replace(":", r"\:")
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error",
         "-f", "lavfi", "-i", "color=c=black:s=640x200:d=1",
         "-vf", f"drawtext=fontfile='{escaped}':text='{text}':fontsize=64:fontcolor=white:x=20:y=60",
         "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True,
        check=True,
    )
    return sum(1 for byte in completed.stdout if byte > 128)


@ffmpeg_required
@pytest.mark.parametrize(("weight", "path"), font_files(), ids=lambda value: str(value))
def test_korean_renders_with_the_bundled_font(weight: int, path: Path) -> None:
    """**Windows 동봉 폰트에 의존하지 않는다**는 완료 조건의 실측이다 (#38).

    `malgun.ttf`는 재배포가 금지되므로 그 경로로 렌더를 맞춰 두면 배포 시점에 폰트를 갈아야
    하고, 그때 #20~#22가 맞춘 레이아웃 수치가 흔들린다.
    """
    assert ink_pixels(path, "한글 렌더 확인") > 0
    # 같은 명령에서 텍스트만 비우면 0이어야 한다 — 배경을 세고 있는 것이 아님을 보인다.
    assert ink_pixels(path, "") == 0


# --- 프리셋 ----------------------------------------------------------------


def test_the_background_presets_are_the_three_from_the_spec() -> None:
    """확정 스펙 6.2의 B1·B2·B3. 스톱이 1개면 단색, 2개면 수직 그라디언트다."""
    presets = background_presets()

    assert list(presets) == ["deep_navy", "purple_gradient", "deep_teal_gradient"]
    assert not presets["deep_navy"].is_gradient
    assert presets["deep_navy"].stops == ("#101A33",)
    assert presets["purple_gradient"].is_gradient
    assert presets["purple_gradient"].top == "#1B0B2E"
    assert presets["purple_gradient"].bottom == "#4A1052"


def test_the_caption_styles_are_the_three_from_the_spec() -> None:
    """확정 스펙 6.1의 P1·P2·P3. 색 역할이 하나라도 빠지면 로더가 먼저 막는다."""
    styles = caption_styles()

    assert list(styles) == ["impact_yellow", "neon_mint", "orange_card"]
    for style in styles.values():
        assert set(style.colors) == set(CAPTION_COLOR_ROLES)
        assert style.label


def test_the_shadowless_preset_thickens_the_answer_outline_instead() -> None:
    """P2가 그림자 대신 정답 외곽선을 5→6으로 올린다 (6.1). 두 값이 함께 움직이지 않으면
    정답 글자가 배경에 묻힌다."""
    styles = caption_styles()

    assert styles["neon_mint"].shadow_alpha is None
    assert styles["neon_mint"].answer_border_bonus == 1
    for name in ("impact_yellow", "orange_card"):
        assert styles[name].shadow_alpha is not None
        assert styles[name].answer_border_bonus == 0


def test_only_the_orange_card_gives_the_answer_its_own_outline_color() -> None:
    """`answer_border`가 `border`와 갈리는 프리셋은 P3뿐이다 (6.1). 나머지는 같은 값을
    명시하므로, 폴백 규칙 없이 언제나 `answer_border`를 읽으면 된다."""
    styles = caption_styles()

    assert styles["orange_card"].color("answer_border") != styles["orange_card"].color("border")
    for name in ("impact_yellow", "neon_mint"):
        assert styles[name].color("answer_border") == styles[name].color("border")


def test_every_style_pairs_with_a_bundled_background() -> None:
    """6.3의 ◎ 기본 짝. 없는 이름이면 앱(#29)이 스타일을 고를 때 기본 배경을 못 찾는다."""
    names = set(background_presets())

    assert {style.background for style in caption_styles().values()} <= names


def test_the_recommended_pairs_are_the_ones_the_spec_settled() -> None:
    """**D2 시안이 민트와 오렌지의 짝을 서로 바꿔 적었다** (D2 확정 스펙 1.1).

    이름이 맞는지(위 테스트)와 **어느 배경인지**는 다른 확인이다. 시안이 적은 두 조합은
    6.3에서 △(권장하지 않음)이고, 앱이 스타일을 고르면 배경이 이 값으로 함께 바뀌므로(#79)
    바뀐 짝이 커밋되면 사용자가 스타일 선택만으로 권장하지 않는 조합에 들어간다.
    """
    assert {name: style.background for name, style in caption_styles().items()} == {
        "impact_yellow": "deep_navy",
        "neon_mint": "purple_gradient",
        "orange_card": "deep_teal_gradient",
    }


# --- 색 대비 (확정 스펙 6.3) ------------------------------------------------


def relative_luminance(color: str) -> float:
    """WCAG 2.1 상대 휘도. `#RRGGBB`를 받는다."""
    channels = (int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(one: str, other: str) -> float:
    luminances = sorted((relative_luminance(one), relative_luminance(other)))
    return (luminances[1] + 0.05) / (luminances[0] + 0.05)


def test_the_ratio_formula_matches_the_known_extreme() -> None:
    """검은 배경 대 흰 글자가 21:1이다 — 위 두 함수의 자기 검사다."""
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)


def test_every_color_clears_the_large_text_floor_on_every_background() -> None:
    """9조합 × 5색 전부. **확정 스펙 6.3이 처음 주장한 7:1은 성립하지 않는다** — 오렌지 카드의
    강조색이 4.90~6.67:1이다. 실측을 스펙에 옮겨 적었고 여기서는 성립하는 하한을 지킨다.

    그라디언트는 밝은 쪽 스톱이 최악이므로 스톱마다 따로 본다.
    """
    for style in caption_styles().values():
        for background in background_presets().values():
            for role in TEXT_ROLES:
                for stop in background.stops:
                    ratio = contrast_ratio(style.color(role), stop)
                    assert ratio >= LARGE_TEXT_FLOOR, (
                        f"{style.name}의 {role} 대 {background.name}({stop}) = {ratio:.2f}:1"
                    )


def test_the_recommended_pairing_clears_the_stronger_floor() -> None:
    """프리셋과 그 기본 배경(◎)의 조합에서는 강조색·본문색이 4.5:1을 넘는다. ○·△ 조합까지
    이 값을 요구하면 스펙이 사용 가능하다고 판정한 조합이 실패한다."""
    backgrounds = background_presets()

    for style in caption_styles().values():
        background = backgrounds[style.background]
        for role in ("accent", "body"):
            for stop in background.stops:
                ratio = contrast_ratio(style.color(role), stop)
                assert ratio >= RECOMMENDED_FLOOR, (
                    f"{style.name}의 {role} 대 기본 배경 {background.name}({stop}) = {ratio:.2f}:1"
                )


# --- 설정과의 연결 ----------------------------------------------------------


def test_the_default_render_settings_name_bundled_presets() -> None:
    """기본값이 번들에 없는 이름이면 아무 설정도 건드리지 않은 실행이 렌더에서 죽는다."""
    data = defaults()

    assert data["render"]["background"] in background_presets()
    assert data["render"]["caption_style"] in caption_styles()


def test_the_defaults_are_the_recommended_pair() -> None:
    """기본 배경은 기본 자막 스타일이 6.3에서 ◎로 짝지어진 배경이다."""
    data = defaults()

    assert caption_styles()[data["render"]["caption_style"]].background == data["render"]["background"]


@pytest.mark.parametrize("key", ["render.background", "render.caption_style"])
def test_an_unknown_preset_name_is_a_config_error(key: str) -> None:
    """오타를 실행 시작 시점에 잡는다. 렌더까지 가서 죽으면 LLM·TTS 호출 비용을 다 쓴 뒤다."""
    with pytest.raises(ConfigError) as raised:
        load_config(overrides={key: "없는_프리셋"})

    message = str(raised.value)
    assert key in message
    assert "쓸 수 있는 이름" in message
