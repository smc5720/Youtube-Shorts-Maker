"""확정 `scenes.json` → `drawtext`·`drawbox` 필터 목록 (D1 확정 스펙 5장, 이슈 #20~#22).

렌더 골격(#19)이 배경·오디오·프레임 경계를 정하고, **이 모듈이 그 위에 무엇을 그리는지를
정한다.** 좌표·폰트 크기·요소별 외곽선은 여기 `_ELEMENTS` 표가 소유한다 — 프리셋(`assets/`)이
가진 것은 색과 그림자뿐이라, 프리셋을 갈아도 레이아웃이 회귀하지 않는다 (확정 스펙 6.1).

세 가지가 이 모듈의 형태를 정한다.

- **줄 하나가 `drawtext` 하나다.** `drawtext`는 여러 줄을 왼쪽 정렬로만 쌓으므로, 한 인스턴스에
  `\\n`을 넣으면 시안의 줄별 가운데 정렬이 나오지 않는다. 줄마다 인스턴스를 만들면 각 줄이
  `x=(500-text_w/2)`로 자기 폭 기준으로 가운데 서고, **줄 간격이 `line_spacing`(폰트 메트릭에
  더해지는 값)이 아니라 우리가 정한 `y`가 된다** — 확정 스펙 2.4의 역산 문제가 사라진다.
- **텍스트는 `expansion=none`으로 넣는다.** 기본 확장 모드에서는 `%`가 `%{...}` 문법의 도입
  문자라, 퀴즈 문장에 흔한 백분율(`약 71%`) 하나가 그 요소를 통째로 지운다. 일부가 잘리는
  것이 아니라 아무것도 그려지지 않고 경고만 나온다. `\\%`로 이스케이프해도 막히지 않는다 —
  실측은 `_escape` 주석에 있다. **카운트다운 숫자도 확장을 쓰지 않는다** — `%{eif}` 한
  인스턴스로 세는 대신 초마다 인스턴스를 나눴다 (`_Painter.draw_countdown`).
- **구간은 초가 아니라 프레임 번호로 준다.** `enable`에 `2.533`처럼 반올림한 초를 적으면 그
  값이 실제 프레임 시각보다 크게 반올림됐을 때 요소가 한 프레임 늦게 켜진다. `75/30`을 그대로
  적으면 필터의 `t`와 정확히 같은 값이 되어 경계가 어긋날 여지가 없다 (#19의 `align`).
- **정답 확대는 `fontsize`·`y` 표현식 하나로 끝난다** (#22). `text_w`·`text_h`가 매 프레임
  갱신되는 것이 실측됐으므로(확정 스펙 2.2) 커지는 동안에도 `x=(500-text_w/2)` /
  `y=(1112-text_h/2)`가 중심을 잡는다 — 티어별 좌표를 미리 계산해 고정할 필요가 없다.
  색 전환만 인스턴스 교체다 (`draw_answer`).
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PACKAGE_LOGGER
from .assets import AssetError, CaptionStyle, caption_styles, font_path
from .captions import wrap

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.overlay")

TEXT_COLUMN = 840
"""텍스트 컬럼 폭. 안전 영역 x 80~920이다 (확정 스펙 1장)."""

CENTER_X = 500
"""가로 정렬 중심. **캔버스 중앙 540이 아니다** — 우측 160px가 Shorts 사이드 버튼 영역이다."""

COLUMN_LEFT = CENTER_X - TEXT_COLUMN // 2
"""텍스트 컬럼의 왼쪽 끝 x80. 텍스트는 `text_w`로 중심을 잡지만 진행 바는 폭이 고정이라
왼쪽 끝을 직접 쓴다."""

BAR_Y = 1330
BAR_HEIGHT = 14
"""카운트다운 진행 바의 위치와 높이 (확정 스펙 5.3)."""

BAR_TRACK_COLOR = "white@0.16"
"""바의 빈 부분. **프리셋 색이 아니다** — 확정 스펙 5.3의 표에 있는 값이고 3종이 공통이라
좌표·크기와 같은 자리에 둔다. 채움색만 프리셋의 강조색을 쓴다 (6.1)."""

SHADOW_BASELINE = 0.5
"""아래 표의 `shadow_alpha`가 기준으로 삼는 불투명도 (P1 임팩트 옐로).

프리셋마다 그림자 불투명도가 다르므로(P3는 0.55) 요소별 값을 절대값으로 쓰면 프리셋 교체가
요소 사이의 관계를 깨뜨린다. 표의 값은 이 기준 대비 비율로 쓰이고, 프리셋이 `None`이면
(P2 네온 민트) 그림자를 아예 넣지 않는다 — 그쪽은 외곽선을 두껍게 해서 벌충한다 (6.1).
"""

ANSWER_ONSET_SEC = 0.15
ANSWER_ACCENT_SEC = 0.35
ANSWER_GROWN_SEC = 0.50
"""정답 강조 애니메이션의 세 시각 (장면 시작 기준, 확정 스펙 5.4의 타임라인).

**`caption_onset_sec`와 달리 여기 있다.** 해설 등장 시각은 장면 길이 하한을 계산하는 #16이
같은 값을 읽어야 해서 config → `project.json`을 지나지만, 이 셋을 읽는 곳은 렌더러뿐이라
좌표·폰트 크기와 같은 자리에 둔다. **`ANSWER_ONSET_SEC`은 효과음 트리거 시각이기도 하다** —
#23이 이 값을 읽는다.
"""

CTA_PUNCH_MAX_LEN = 9
CTA_TAIL_MAX_LEN = 21
"""cta 고정 두 줄의 글자 수 상한 (확정 스펙 5.5). 설정값이라 초과는 사람의 오타이고, 렌더를
시작하기 전에 멈춘다 — 생성 텍스트의 상한(#56)과 달리 모델이 만든 값이 아니다."""

KOREAN_PROBE = ("한", "글")
"""사용자 지정 폰트가 한국어를 그릴 수 있는지 보는 두 글자.

**한 글자로는 판정할 수 없다.** 글리프가 없으면 FreeType이 `.notdef`를 그리는데 그것이 빈
사각형인 폰트가 많아서 "잉크가 있음"이 커버리지를 뜻하지 않는다. 서로 다른 두 글자가 같은
그림이면 둘 다 `.notdef`다.
"""


class OverlayError(Exception):
    """오버레이를 만들 수 없다. `video_renderer`가 `RenderError`로 옮긴다."""


@dataclass(frozen=True)
class Tier:
    """텍스트 길이에 따라 갈리는 크기 단계 (확정 스펙 5장).

    `max_chars`는 **원문 글자 수**이지 줄당 글자 수가 아니다. 줄당 글자 수는
    `floor(840 / size)`로 계산한다 (`chars_per_line`).
    """

    max_chars: int
    size: int
    y: int
    line_height: int
    max_lines: int
    borderw: int | None = None
    """요소 기본값을 덮는 외곽선 두께. **크기가 두 배 갈리는 티어에만 쓴다.**

    `borderw`는 픽셀 고정값이라 같은 값이 132px에서는 3.8%, 84px에서는 6.0%로 보인다.
    실측에서 정답 T1만 다른 요소들(5.3~8.3%)보다 눈에 띄게 얇았다 — 확정 스펙 2.1의
    "실제 렌더를 보고 맞춘다"가 가리킨 지점이 여기다.
    """
    grow_from: int | None = None
    """확대가 시작하는 크기. `size`가 도착점이다 (확정 스펙 5.4의 `79 → 132`).

    `None`이면 애니메이션이 없다 — 정답 말고는 전부 그렇다. **`borderw`는 함께 자라지
    않는다**: 픽셀 고정값이라 시작 크기에서 비율이 커 보이지만, 0.35초 동안만이고 두께를
    표현식으로 줄 수 없다 (`fontsize`의 `T` 플래그는 이 옵션에 없다).
    """

    @property
    def chars_per_line(self) -> int:
        """줄당 글자 수 상한 (확정 스펙 1장의 공식).

        **한글 1자 = 1.0em 가정이고 실제 자폭은 0.86em이다** — 그래서 상한을 꽉 채운 줄이
        컬럼의 86%만 쓴다. 넘침이 없는 쪽으로 보수적이고, 시안이 전제한 줄 수(hook 11자,
        질문 13/15자, 정답 10자, 해설 23자)를 그대로 재현하는 값이라 그대로 쓴다.
        실측값은 확정 스펙 7.3에 있다.
        """
        return TEXT_COLUMN // self.size


@dataclass(frozen=True)
class Element:
    """화면 요소 하나의 고정 수치 (확정 스펙 5장의 표).

    색은 **역할 이름**이고 값은 프리셋이 정한다. `center_y`가 있으면 블록을 그 y 중심에
    놓고, 없으면 티어의 `y`가 블록 상단이다.
    """

    weight: int
    color: str
    borderw: int
    shadow_offset: int
    shadow_alpha: float
    tiers: tuple[Tier, ...]
    border_color: str = "border"
    center_y: int | None = None

    def tier_for(self, text: str) -> Tier:
        """글자 수에 맞는 티어. 상한을 넘으면 가장 작은 티어로 그린다.

        상한 초과는 생성 단계가 막지만(#56) 사람이 고친 `scenes.json`은 그 경로를 지나지
        않는다. 그때 그리지 않는 것보다 넘치더라도 그리는 편이 낫다 — 어긋난 것이 화면에
        보여야 사람이 고친다.
        """
        for tier in self.tiers:
            if len(text) <= tier.max_chars:
                return tier
        return self.tiers[-1]


def _tier(
    max_chars: int,
    size: int,
    y: int,
    line_height: int,
    max_lines: int,
    borderw: int | None = None,
    grow_from: int | None = None,
) -> Tier:
    return Tier(max_chars, size, y, line_height, max_lines, borderw, grow_from)


_ELEMENTS: dict[str, Element] = {
    # --- hook (확정 스펙 5.1) ---
    "kicker": Element(
        weight=800, color="accent", borderw=3, shadow_offset=0, shadow_alpha=0.0,
        tiers=(_tier(max_chars=999, size=44, y=520, line_height=44, max_lines=1),),
    ),
    "hook": Element(
        weight=800, color="body", borderw=4, shadow_offset=8, shadow_alpha=0.5,
        tiers=(
            _tier(max_chars=22, size=76, y=760, line_height=104, max_lines=2),
            _tier(max_chars=36, size=64, y=760, line_height=88, max_lines=3),
        ),
    ),
    "meta": Element(
        weight=700, color="secondary", borderw=3, shadow_offset=0, shadow_alpha=0.0,
        tiers=(_tier(max_chars=999, size=40, y=1060, line_height=40, max_lines=1),),
    ),
    # --- question · countdown · answer 공통 상단 (5.2, 5.3, 5.4) ---
    "index": Element(
        weight=800, color="accent", borderw=3, shadow_offset=0, shadow_alpha=0.0,
        tiers=(_tier(max_chars=999, size=48, y=400, line_height=48, max_lines=1),),
    ),
    "heading": Element(
        weight=700, color="body", borderw=4, shadow_offset=6, shadow_alpha=0.45,
        tiers=(
            # **상단 정렬이다.** 줄 수가 변해도 첫 줄 위치가 안 움직인다 (5.2).
            _tier(max_chars=39, size=64, y=648, line_height=88, max_lines=3),
            _tier(max_chars=45, size=56, y=664, line_height=78, max_lines=3),
        ),
    ),
    # --- countdown (5.3) ---
    "digit": Element(
        weight=800, color="accent", borderw=7, shadow_offset=12, shadow_alpha=0.5,
        # 박스 y1060 h240에 240px 한 줄이라 잉크가 박스를 정확히 채운다 (2.4의 `y` 실측).
        tiers=(_tier(max_chars=999, size=240, y=1060, line_height=240, max_lines=1),),
    ),
    # --- answer (5.4) ---
    "answer": Element(
        weight=800, color="accent", borderw=5, shadow_offset=10, shadow_alpha=0.5,
        border_color="answer_border",
        # 박스 y1000 h224의 중심. 티어와 무관하게 박스가 같아서 해설 y1300이 안전하다.
        center_y=1112,
        tiers=(
            # T1만 7이다 — 132px에서 5는 다른 요소의 절반 비율이라 외곽선이 사라진다.
            _tier(max_chars=6, size=132, y=1112, line_height=132, max_lines=1,
                  borderw=7, grow_from=79),
            _tier(max_chars=20, size=84, y=1112, line_height=112, max_lines=2,
                  grow_from=50),
        ),
    ),
    "explanation": Element(
        weight=500, color="secondary", borderw=3, shadow_offset=0, shadow_alpha=0.0,
        tiers=(_tier(max_chars=999, size=36, y=1300, line_height=52, max_lines=3),),
    ),
    # --- cta (5.5) ---
    "cta": Element(
        weight=800, color="body", borderw=4, shadow_offset=8, shadow_alpha=0.5,
        tiers=(_tier(max_chars=999, size=68, y=700, line_height=96, max_lines=2),),
    ),
    "punch": Element(
        weight=800, color="accent", borderw=5, shadow_offset=10, shadow_alpha=0.5,
        tiers=(_tier(max_chars=999, size=88, y=980, line_height=88, max_lines=1),),
    ),
    "tail": Element(
        weight=700, color="secondary", borderw=3, shadow_offset=0, shadow_alpha=0.0,
        tiers=(_tier(max_chars=999, size=40, y=1180, line_height=40, max_lines=1),),
    ),
}
"""요소 이름 → 고정 수치. **이 표가 확정 스펙 5장의 구현본이다.**

`max_chars=999`는 티어가 하나뿐이라 길이로 갈리지 않는다는 뜻이다. 그 요소들도 줄 수 상한은
가지므로, 넘치면 `build`가 경고한다.
"""


@dataclass(frozen=True)
class Fonts:
    """웨이트 → 폰트 파일. 웨이트 셋을 한 번에 정하는 이유는 셋이 함께 움직여야 하기
    때문이다 — 사용자가 폰트를 지정하면 세 웨이트가 모두 그 파일이 된다."""

    paths: dict[int, Path]

    def path(self, weight: int) -> Path:
        return self.paths[weight]


def resolve_fonts(font_path_setting: str | None) -> Fonts:
    """`render.font_path`와 번들 폰트 사이의 선택 (확정 스펙 9장).

    지정된 파일 하나가 세 웨이트를 모두 맡는다. `drawtext`가 웨이트를 고를 수 없어 파일
    1개 = 웨이트 1개이므로(확정 스펙 9장), 사용자 폰트를 쓰면 굵기 대비가 사라진다 — 그것을
    감수하는 선택이고, 굵기를 유지하려면 번들을 쓴다.

    **사용자 폰트만 한국어 렌더를 실측한다.** 번들 폰트의 한글 커버리지는
    `tests/test_visual_assets.py`가 커밋된 파일에서 확인하므로, 실행마다 다시 재면 렌더
    한 번에 ffmpeg 호출 두 번이 늘 뿐이다.

    Raises:
        OverlayError: 지정한 파일이 없거나 한국어를 그리지 못할 때. 번들 폰트가 없을 때도
            같다 — 어느 쪽이든 렌더를 시작하기 전에 멈춘다.
    """
    if font_path_setting is None:
        try:
            return Fonts({weight: font_path(weight) for weight in (800, 700, 500)})
        except AssetError as error:
            raise OverlayError(f"번들 폰트를 찾을 수 없다 — {error}") from error

    path = Path(font_path_setting)
    if not path.is_file():
        raise OverlayError(
            f"render.font_path가 가리키는 폰트 파일이 없다: {path}. "
            "값을 비우면(null) 번들 Pretendard를 쓴다"
        )
    _require_korean(path)
    return Fonts(dict.fromkeys((800, 700, 500), path))


def _require_korean(path: Path) -> None:
    """폰트가 한글을 그리는지 실제 렌더로 본다 (`KOREAN_PROBE` 참고).

    Raises:
        OverlayError: 글리프가 비었거나 두 글자가 같은 그림일 때. FFmpeg가 없으면
            검사를 건너뛴다 — 없다는 사실은 렌더 단계가 곧 말한다.
    """
    inks = []
    for char in KOREAN_PROBE:
        command = [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=128x128",
            "-vf", f"drawtext=fontfile='{escape_path(path)}':text='{char}'"
                   ":fontsize=96:fontcolor=white:x=8:y=8:expansion=none",
            "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, timeout=60)
        except FileNotFoundError:
            return
        except subprocess.TimeoutExpired as error:
            raise OverlayError(f"폰트 확인이 끝나지 않았다: {path}") from error
        if completed.returncode != 0:
            raise OverlayError(
                f"폰트 파일을 열 수 없다: {path} — "
                f"ffmpeg {completed.stderr.decode('utf-8', 'replace').strip()!r}"
            )
        inks.append(bytes(byte > 128 for byte in completed.stdout))

    if not any(inks[0]):
        raise OverlayError(f"폰트가 한글을 그리지 못한다 (글리프가 비어 있다): {path}")
    if inks[0] == inks[1]:
        raise OverlayError(
            f"폰트에 한글 글리프가 없다 — {KOREAN_PROBE[0]!r}와 {KOREAN_PROBE[1]!r}가 "
            f"같은 모양으로 그려진다: {path}"
        )


def build(
    scenes: Mapping[str, Any],
    frame_spans: Sequence[tuple[int, int]],
    *,
    fps: int,
    style: CaptionStyle,
    fonts: Fonts,
    cta_punch: str,
    cta_tail: str,
    caption_onset: float,
) -> list[str]:
    """장면 목록을 `drawtext` 필터 문자열 목록으로 옮긴다.

    Args:
        scenes: **확정 상태** `scenes.json` 내용. 입구 검증은 부르는 쪽(`video_renderer`)이
            이미 했다.
        frame_spans: 장면별 (시작 프레임, 끝 프레임). 프레임 경계는 `video_renderer.align`이
            소유한다 — 여기서 `duration`을 다시 누적하지 않는다.
        fps: `enable` 식의 분모.
        style: 자막 스타일 프리셋. 색과 그림자만 여기서 나온다.
        fonts: 웨이트별 폰트 파일.
        cta_punch: cta의 강조 한 줄 (`render.cta_punch`).
        cta_tail: cta의 마지막 한 줄 (`render.cta_tail`).
        caption_onset: 해설이 뜨는 시각(장면 시작 기준 초, `render.caption_onset_sec`).
            **#16이 장면 길이 하한을 계산할 때 읽는 값과 같은 값이다** — 여기서 상수로
            박으면 화면에 뜨는 시각과 길이 계산이 갈려 해설을 다 읽기 전에 장면이 끝난다.

    Returns:
        `[0:v]` 뒤에 순서대로 이을 필터 문자열. 비어 있을 수 있다 — 문구가 하나도 없는 장면
        목록도 정상이다.

    Raises:
        OverlayError: cta 설정 문구가 상한을 넘을 때.
    """
    scene_list = list(scenes["scenes"])
    if len(frame_spans) != len(scene_list):
        raise OverlayError(
            f"장면 {len(scene_list)}개에 구간이 {len(frame_spans)}개다 — "
            "프레임 정렬과 장면 목록이 어긋났다"
        )

    _check_cta_setting("render.cta_punch", cta_punch, CTA_PUNCH_MAX_LEN)
    _check_cta_setting("render.cta_tail", cta_tail, CTA_TAIL_MAX_LEN)

    painter = _Painter(fps=fps, style=style, fonts=fonts, caption_onset=caption_onset)
    total_questions = len({
        scene["question_id"] for scene in scene_list if "question_id" in scene
    })

    for index, (scene, span) in enumerate(zip(scene_list, frame_spans, strict=True)):
        role = scene["role"]
        if role == "hook":
            painter.draw("kicker", scene.get("kicker"), span, where=f"scenes[{index}]")
            painter.draw("hook", scene.get("text"), span, where=f"scenes[{index}]")
            painter.draw(
                "meta", _meta(scene_list, total_questions), span,
                where=f"scenes[{index}]",
            )
        elif role == "countdown":
            painter.draw_countdown(
                scene.get("seconds"), span, where=f"scenes[{index}]"
            )
        elif role == "answer":
            painter.draw_answer(scene.get("text"), span, where=f"scenes[{index}]")
            painter.draw(
                "explanation",
                scene.get("caption"),
                painter.caption_span(span, where=f"scenes[{index}]"),
                where=f"scenes[{index}]",
            )
        elif role == "cta":
            painter.draw("cta", scene.get("text"), span, where=f"scenes[{index}]")
            painter.draw("punch", cta_punch, span, where="render.cta_punch")
            painter.draw("tail", cta_tail, span, where="render.cta_tail")

    # 상단 문구는 장면 하나가 아니라 블록을 덮는다. question → countdown이 한 인스턴스여야
    # 좌표·크기가 같다는 전제가 성립하고(5.3), answer는 감쇠색이라 갈린다 (5.4).
    for name, text, span, dimmed in _headers(scene_list, frame_spans, total_questions):
        painter.draw(name, text, span, where=name, color="dimmed" if dimmed else None)

    return painter.filters


def _check_cta_setting(key: str, value: str, limit: int) -> None:
    if len(value) > limit:
        raise OverlayError(
            f"{key}는 {limit}자 이하여야 한다. 받은 값: {value!r} ({len(value)}자)"
        )


def _meta(scene_list: Sequence[Mapping[str, Any]], count: int) -> str:
    """hook의 `문제 4개 · 48초` (확정 스펙 5.1).

    **`scenes.json`에서 계산한다.** 렌더러가 `quiz.json`을 열면 타입 경계가 깨지고(PRD 7.4.1),
    고정 문자열을 박으면 문제 수가 바뀌어도 화면이 안 따라온다.

    `question_id`를 가진 장면이 하나도 없으면 빈 문자열이다 — `문제 0개`는 아무것도 알려주지
    않는다. 통과 필드를 안 쓰는 타입이 추가되면 그 타입의 hook에는 meta가 안 뜬다.
    """
    if count == 0:
        return ""
    seconds = round(sum(float(scene["duration"]) for scene in scene_list))
    return f"문제 {count}개 · {seconds}초"


def _headers(
    scene_list: Sequence[Mapping[str, Any]],
    frame_spans: Sequence[tuple[int, int]],
    total_questions: int,
) -> list[tuple[str, str, tuple[int, int], bool]]:
    """`heading`·`index`를 연속 장면 묶음으로 편다.

    **묶는 기준은 (문구, 감쇠 여부)다.** 값이 같은 인접 장면은 인스턴스 하나로 덮으므로
    question → countdown 사이에 좌표가 흔들릴 여지가 없다. answer는 감쇠색이라 키가 달라져
    자연히 새 인스턴스가 된다.
    """
    runs: list[tuple[str, str, tuple[int, int], bool]] = []
    for scene, (start, end) in zip(scene_list, frame_spans, strict=True):
        dimmed = scene["role"] == "answer"
        pieces = [("heading", scene.get("heading"))]
        if "question_id" in scene:
            pieces.append(("index", f"Q{scene['question_id']} / {total_questions}"))

        for name, text in pieces:
            if not text:
                continue
            for position, (prev_name, prev_text, prev_span, prev_dim) in enumerate(runs):
                if (prev_name, prev_text, prev_dim) == (name, text, dimmed) and (
                    prev_span[1] == start
                ):
                    runs[position] = (name, text, (prev_span[0], end), dimmed)
                    break
            else:
                runs.append((name, text, (start, end), dimmed))
    return runs


class _Painter:
    """요소 하나를 줄 단위 `drawtext`로 편다. 필터 순서가 그리는 순서다."""

    def __init__(
        self, *, fps: int, style: CaptionStyle, fonts: Fonts, caption_onset: float
    ) -> None:
        self.fps = fps
        self.style = style
        self.fonts = fonts
        self.caption_onset = caption_onset
        self.filters: list[str] = []

    def draw(
        self,
        name: str,
        text: str | None,
        span: tuple[int, int],
        *,
        where: str,
        color: str | None = None,
    ) -> None:
        """요소 하나를 그린다. 문구가 없으면 아무것도 하지 않는다."""
        layout = self._layout(name, text, where=where)
        if layout is None:
            return
        element, tier, lines = layout

        top = tier.y
        if element.center_y is not None:
            # 블록 높이를 먼저 알아야 중심을 맞출 수 있다. 마지막 줄은 행간이 아니라 글자
            # 높이만 차지하므로 (n-1)×LH + size다.
            height = (len(lines) - 1) * tier.line_height + tier.size
            top = element.center_y - height // 2

        for number, line in enumerate(lines):
            self.filters.append(
                self._drawtext(
                    line,
                    element=element,
                    tier=tier,
                    y=str(top + number * tier.line_height),
                    span=span,
                    color=color or element.color,
                )
            )

    def _layout(
        self, name: str, text: str | None, *, where: str
    ) -> tuple[Element, Tier, list[str]] | None:
        """요소·티어·줄. 문구가 없으면 `None`이다."""
        if not text:
            return None

        element = _ELEMENTS[name]
        tier = element.tier_for(text)
        lines = wrap(text, tier.chars_per_line)
        if len(lines) > tier.max_lines:
            # 자르지 않는다 — 원문을 잃는 쪽이 더 나쁘다 (`captions._warn_about_overflow`와
            # 같은 판단이다). 생성 단계의 상한(#56)을 지난 문구는 여기 걸리지 않는다.
            LOGGER.warning(
                "%s의 %s가 %d줄이다 — %dpx 티어의 상한 %d줄을 넘어 안전 영역을 벗어난다: %s",
                where, name, len(lines), tier.size, tier.max_lines, text,
            )
        return element, tier, lines

    def draw_answer(
        self, text: str | None, span: tuple[int, int], *, where: str
    ) -> None:
        """정답을 확대하며 그린다 (확정 스펙 5.4의 타임라인).

        **크기는 표현식 하나, 색은 인스턴스 교체다.** `fontsize`는 시간 표현식을 받으므로
        (`T` 플래그) 확대가 필터 하나로 끝나지만, `fontcolor`에는 그런 것이 없고 스펙이
        요구하는 것도 1프레임 전환이라 `answer_onset` 구간과 `accent` 구간을 나눈다 —
        카운트다운 숫자를 초마다 나눈 것과 같은 방식이다. 두 인스턴스가 같은 크기 표현식을
        쓰므로 경계에서 크기는 이어진다.

        **좌표는 미리 계산하지 않는다.** `text_w`·`text_h`가 매 프레임 갱신되는 것을 #22에서
        실측했다 (확정 스펙 2.2) — 확대 내내 잉크 중심이 (500, 1112)에 머문다. 여러 줄이면
        줄 중심도 같은 비율로 벌어져야 블록 전체가 중심에서 자란다.
        """
        layout = self._layout("answer", text, where=where)
        if layout is None:
            return
        element, tier, lines = layout
        assert element.center_y is not None, "정답은 박스 중앙 정렬이다 (5.4)"

        start, end = span
        onset = start + self._frames(ANSWER_ONSET_SEC)
        accent = start + self._frames(ANSWER_ACCENT_SEC)
        grown = start + self._frames(ANSWER_GROWN_SEC)

        if tier.grow_from is None or grown >= end:
            # 장면이 확대를 다 담지 못한다. 확정 검증을 지난 장면은 최소 `min_duration_sec`
            # (1.2초)이라 여기 오지 않지만, 사람이 고친 `scenes.json`은 그 경로를 지나지
            # 않는다. 그때는 애니메이션을 버리고 정답을 목표 크기로 세운다 — 반쯤 커진
            # 상태로 장면이 끝나는 것보다 낫다.
            if tier.grow_from is not None:
                LOGGER.warning(
                    "%s: 장면이 %.2f초라 정답 확대(%.2f초)를 담지 못한다 — "
                    "목표 크기로 고정해 그린다",
                    where, (end - start) / self.fps, ANSWER_GROWN_SEC,
                )
            segments = [((start, end), "accent", str(tier.size))]
        else:
            size = self._grow(tier, onset, grown)
            segments = [
                ((onset, accent), "answer_onset", size),
                ((accent, end), "accent", size),
            ]

        for number, line in enumerate(lines):
            # 줄 중심의 목표 위치. 블록 중심에서 위아래로 LH씩 벌어진다 — `draw`의
            # `(n-1)×LH + size` 블록과 같은 배치이고, 여기서는 상단이 아니라 중심을 쓴다.
            offset = (number - (len(lines) - 1) / 2) * tier.line_height
            for window, color, size in segments:
                self.filters.append(
                    self._drawtext(
                        line,
                        element=element,
                        tier=tier,
                        y=self._grown_y(element.center_y, offset, size, tier.size),
                        span=window,
                        color=color,
                        size=size,
                    )
                )

    def _grow(self, tier: Tier, onset: int, grown: int) -> str:
        """`grow_from` → `size` 선형 확대 표현식. 도착 후에는 `min`이 목표 크기로 묶는다."""
        assert tier.grow_from is not None
        rate = (tier.size - tier.grow_from) * self.fps / (grown - onset)
        return f"min({tier.size},{tier.grow_from}+{rate:.4f}*(t-{onset}/{self.fps}))"

    def _grown_y(self, center: int, offset: float, size: str, target: int) -> str:
        """줄 하나의 `y`. 잉크 높이의 절반을 빼서 줄 중심을 목표 위치에 놓는다.

        `text_h`가 매 프레임 갱신되므로 커지는 동안에도 중심이 고정된다. 줄 중심의 간격도
        크기에 비례해야 블록이 한 덩어리로 자란다 — 간격을 목표값으로 고정하면 작을 때
        줄 사이가 벌어져 보인다.
        """
        if not offset:
            return f"({center}-text_h/2)"
        if size == str(target):
            return f"({center + round(offset)}-text_h/2)"
        return f"({center}{offset:+g}*({size})/{target}-text_h/2)"

    def caption_span(self, span: tuple[int, int], *, where: str) -> tuple[int, int]:
        """해설이 서 있는 구간. 장면 시작이 아니라 `caption_onset` 뒤에 뜬다 (확정 스펙 4장).

        정답이 목표 크기로 고정된(0.50초) 뒤라 확대가 끝난 화면에 해설이 얹힌다.
        """
        start, end = span
        onset = start + self._frames(self.caption_onset)
        if onset >= end:
            # #16이 이 값을 하한에 넣으므로 확정된 장면에서는 일어나지 않는다. 사람이 길이를
            # 줄인 `scenes.json`에서 해설을 통째로 잃는 것보다 일찍 띄우는 편이 낫다.
            LOGGER.warning(
                "%s: 장면이 %.2f초라 해설 등장 시각(%.2f초)이 장면을 넘는다 — "
                "장면 시작부터 그린다",
                where, (end - start) / self.fps, self.caption_onset,
            )
            return span
        return onset, end

    def _frames(self, seconds: float) -> int:
        """장면 시작 기준 초 → 프레임 수. `video_renderer.align`과 같은 반올림이다."""
        return max(0, round(seconds * self.fps))

    def draw_countdown(
        self, seconds: Any, span: tuple[int, int], *, where: str
    ) -> None:
        """카운트다운 숫자와 진행 바 (확정 스펙 5.3).

        **숫자는 초마다 인스턴스 하나다.** `%{eif}` 한 인스턴스로 남은 초를 계산하는 방법도
        되지만, 그러려면 그 요소만 확장 모드를 켜야 하고 구간이 표현식 안의 반올림한 초로
        들어간다. 인스턴스를 나누면 다른 요소와 같은 `n/fps` 경계를 쓴다 — 숫자가 바뀌는
        프레임이 장면 경계와 같은 규칙으로 정해진다.

        **바는 1초 단위로 끊긴다.** 확정 스펙 5.3이 요구한 `840*(1-t/T)` 연속 감소는
        `drawbox`로 만들 수 없다 — 이 필터의 `t`는 시간이 아니라 **박스 두께**이고
        (`ffmpeg -h filter=drawbox`), 표현식에 프레임·시간 변수가 아예 없어 `n`이나 `time`을
        적으면 "Undefined constant"로 필터 설정이 실패한다 (#21에서 실측, 확정 스펙 2.3).
        고정 폭 인스턴스를 숫자와 같은 박자로 갈아 끼워 남은 시간을 전달한다.

        Args:
            seconds: `scenes.json`의 `seconds`. 확정 검증이 요구하는 값이지만(스키마의
                `_check_scene_roles`) 앱이 만든 프로젝트가 그 경로를 지나지 않을 수 있어
                없으면 경고만 남기고 그리지 않는다.
        """
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
            LOGGER.warning(
                "%s의 seconds가 1 이상의 정수가 아니다 — 카운트다운을 그리지 않는다: %r",
                where, seconds,
            )
            return

        steps = self._countdown_steps(seconds, span, where=where)
        if not steps:
            return

        self.filters.append(self._drawbox(TEXT_COLUMN, BAR_TRACK_COLOR, span))
        fill = _color(self.style.color("accent"))
        for index, window in steps:
            # 남은 초에 비례한다. 첫 초가 가득 찬 840이고 마지막 초가 한 칸이다.
            width = round(TEXT_COLUMN * (seconds - index) / seconds)
            self.filters.append(self._drawbox(width, fill, window))
        for index, window in steps:
            self.draw("digit", str(seconds - index), window, where=where)

    def _countdown_steps(
        self, seconds: int, span: tuple[int, int], *, where: str
    ) -> list[tuple[int, tuple[int, int]]]:
        """숫자 하나가 서 있는 (순번, 프레임 구간). 순번 0이 가장 큰 숫자다.

        각 1.0초이고, **마지막 숫자만 장면 끝까지 늘어난다.** 확정 검증이 `duration`과
        `seconds`를 같게 묶지만(`schemas/scenes.py`) 프레임 반올림으로 한두 프레임이 남을 수
        있고, 그 프레임에 숫자가 비면 카운트다운이 끝나기 전에 화면이 빈다.
        """
        start, end = span
        steps: list[tuple[int, tuple[int, int]]] = []
        for index in range(seconds):
            first = start + index * self.fps
            if first >= end:
                # 장면이 seconds보다 짧다. 들어갈 수 있는 숫자까지만 그린다.
                LOGGER.warning(
                    "%s: 장면 길이가 seconds(%d초)보다 짧아 숫자 %d개만 그린다",
                    where, seconds, index,
                )
                break
            last = end if index == seconds - 1 else min(first + self.fps, end)
            steps.append((index, (first, last)))
        return steps

    def _drawbox(self, width: int, color: str, span: tuple[int, int]) -> str:
        """진행 바 한 칸. 폭 말고는 전부 고정이다 (확정 스펙 5.3)."""
        return "drawbox=" + ":".join([
            f"x={COLUMN_LEFT}",
            f"y={BAR_Y}",
            f"w={width}",
            f"h={BAR_HEIGHT}",
            f"color={color}",
            # 테두리만 그리는 기본값(두께 3)이 아니라 안을 채운다.
            "t=fill",
            f"enable='{self._enable(span)}'",
        ])

    def _drawtext(
        self,
        line: str,
        *,
        element: Element,
        tier: Tier,
        y: str,
        span: tuple[int, int],
        color: str,
        size: str | None = None,
    ) -> str:
        """줄 하나. `y`와 `size`는 표현식일 수 있다 (#22의 정답 확대)."""
        borderw = tier.borderw if tier.borderw is not None else element.borderw
        if element.border_color == "answer_border":
            # 그림자를 안 쓰는 프리셋이 정답만 두껍게 한다 (확정 스펙 6.1).
            borderw += self.style.answer_border_bonus

        options = [
            f"fontfile='{escape_path(self.fonts.path(element.weight))}'",
            f"text='{_escape(line)}'",
            # `%`가 요소를 통째로 지우는 것을 막는다 (모듈 주석).
            "expansion=none",
            f"fontsize={_value(size or str(tier.size))}",
            f"fontcolor={_color(self.style.color(color))}",
            f"bordercolor={_color(self.style.color(element.border_color))}",
            f"borderw={borderw}",
            f"x=({CENTER_X}-text_w/2)",
            f"y={_value(y)}",
            f"enable='{self._enable(span)}'",
        ]

        alpha = self.style.shadow_alpha
        if element.shadow_offset and alpha is not None:
            options[-1:-1] = [
                "shadowx=0",
                f"shadowy={element.shadow_offset}",
                f"shadowcolor=black@{alpha * element.shadow_alpha / SHADOW_BASELINE:.3f}",
            ]

        return "drawtext=" + ":".join(options)

    def _enable(self, span: tuple[int, int]) -> str:
        """구간을 프레임 번호 나눗셈으로 적는다 (모듈 주석의 셋째 항목).

        `lt`로 끝을 여는 이유는 `between(t,a,b)`가 양끝을 포함해서 장면 경계 프레임에 앞뒤
        요소가 겹치기 때문이다. 시작은 `gte`라 경계 프레임이 새 장면 것이 된다.
        """
        start, end = span
        return f"gte(t,{start}/{self.fps})*lt(t,{end}/{self.fps})"


def _escape(text: str) -> str:
    r"""`drawtext`의 `text=` 값으로 넣을 수 있게 만든다. 값은 작은따옴표로 감싼다.

    실측으로 정한 세 규칙이다 (`docs/design/d1-video-design-spec.md` 7.4).

    - `\` → `\\`
    - `:` → `\:` — **작은따옴표 안이어도 필터 옵션 구분자로 먼저 잘린다.**
    - `'` → `'\\\''` — 인용을 닫고 세 겹 이스케이프한 따옴표를 넣은 뒤 다시 연다.
      필터그래프와 옵션 파서가 각각 한 겹씩 벗기므로 두 겹으로는 따옴표가 사라진다.

    `%`·`,`·`;`·`[`·`]`·`=`는 인용 안에서 그대로 통과한다 (`%`는 `expansion=none` 전제).
    순서가 중요하다 — 역슬래시를 먼저 처리해야 뒤 규칙이 넣은 이스케이프가 두 번 처리되지
    않는다.
    """
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "'\\\\\\''")
    )


def _value(expression: str) -> str:
    """옵션 값 하나. `,`가 있으면 인용한다.

    필터그래프 파서는 인용 밖의 `,`에서 **필터를 끊으므로**, `min(132,79+...)` 같은 표현식을
    그대로 넣으면 나머지가 다음 필터로 읽힌다. `enable`이 늘 인용돼 있는 것도 같은 이유다
    (`gte(t,4/30)`). 인용이 필요 없는 값까지 감싸지 않는 것은 #20이 만든 필터 문자열을
    그대로 두기 위함이다.
    """
    return f"'{expression}'" if "," in expression else expression


def escape_path(path: Path) -> str:
    r"""폰트 경로를 필터 문자열에 넣는다. Windows의 `D:/...` 콜론을 `D\:/...`로 바꾼다."""
    return str(path).replace("\\", "/").replace(":", "\\:")


def _color(value: str) -> str:
    """`#RRGGBB` → `0xRRGGBB`. `video_renderer._color`와 같은 변환이지만 입력이 다르다 —
    이쪽은 프리셋이 이미 형식을 보증한 값이라 검사하지 않는다 (`assets.HEX_COLOR`)."""
    return f"0x{value.lstrip('#').upper()}"


def style_for(name: str) -> CaptionStyle:
    """이름으로 자막 스타일 프리셋을 찾는다.

    Raises:
        OverlayError: 모르는 이름이거나 프리셋 파일을 읽을 수 없을 때.
    """
    try:
        return caption_styles()[name]
    except AssetError as error:
        raise OverlayError(f"자막 스타일을 읽을 수 없다 — {error}") from error
    except KeyError:
        known = ", ".join(caption_styles())
        raise OverlayError(
            f"모르는 자막 스타일이다: {name!r}. 쓸 수 있는 이름: {known}"
        ) from None
