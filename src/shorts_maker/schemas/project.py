"""`project.json` 스키마 — 편집 앱이 프로젝트를 여는 입구 (PRD 7.10).

**대부분은 생성 직후의 초기 상태다.** 렌더러가 읽는 값(배경·오디오·출력 규격·자막 스타일)이
여기를 지나므로, config를 렌더가 다시 열면 앱이 편집한 프로젝트와 CLI 렌더가 갈린다.

**예외가 `review` 하나다** — 앱이 소유하는 편집 상태이고 렌더러가 읽지 않는다(#28).
PRD 7.10이 "앱 프레임워크가 정해진 뒤 추가한다"고 미뤄 둔 자리이며, 프레임워크는 #25에서
정해졌다.

**`render.scene_overrides`는 앱이 쓰지만 렌더러가 읽는다** (#82, #83). 그래서 `review`와 달리
`PREVIEW_BLIND_SECTIONS`에 들어가지 않는다 — 사람이 얹은 길이·문구·오버레이가 프리뷰 프레임을
바꾸므로 지문에 들어가야 화면이 옛 그림에 머물지 않는다. 그 목록의 기준은 "앱이 쓰는가"도
"렌더러가 읽는가"도 아니고 **프리뷰 프레임에 닿는가**다 (#81).

경로 값은 모두 **run 디렉터리 기준 상대 경로**다. run 디렉터리를 옮기거나 이름을 바꿔도
프로젝트가 열려야 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..shorts_types import available_types
from .core import (
    Field,
    Object,
    Rule,
    Scalar,
    Schema,
    choices_from,
    describe,
    flag,
    integer,
    items,
    number,
    section,
    text,
)
from .scenes import ROLES

SCHEMA_VERSION = 1
KNOWN_VERSIONS = (1,)

BACKGROUND_KINDS = ("preset", "color", "image", "video")
"""`preset`은 번들 프리셋 이름, `color`는 색상 값, 나머지는 사용자 파일 경로다.

무료 이미지 API는 MVP 범위 밖이므로 원격 소스 종류를 두지 않는다 (PRD 14.1).
"""

MOTION_NONE = "none"
"""모션을 걸지 않는다 — **기본값이다.**

`audio.music`이 기본 `null`인 것과 같은 자리다 (#35). 앱에 이 값을 바꾸는 컨트롤이 없으므로
(D2 확정 스펙에 모션 칸이 없다) 켜는 경로는 config 하나이고, 기본으로 켜 두면 앱에서 배경을
파일로 바꾼 사람이 화면에서 끌 수 없는 움직임을 얻는다.
"""

MOTION_KINDS = (
    MOTION_NONE,
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
)
"""배경에 걸 수 있는 움직임 (PRD 7.7의 "약한 zoom/pan", 이슈 #34).

**이름 하나가 방향까지 정한다.** `kind` + `direction` 두 칸으로 나누면 `zoom`에 `left`가,
`pan`에 `in`이 붙는 조합이 계약을 통과하고 그 뜻은 렌더러가 정해야 한다.

`preset`·`color` 배경에서는 어느 값을 줘도 결과가 같다 — 공간 변화를 옮기는 것이므로 그림이
균일한 소스에서는 옮길 것이 없다. 그래서 렌더러가 그 두 종류에는 필터를 붙이지 않는다
(`video_renderer._motion`).
"""

MOTION_STRENGTH_MAX = 1.0
"""`strength`의 상한 — 확대 배율 2배.

**`zoompan`이 배율을 10에서 조용히 자르기 때문에 계약이 먼저 거부한다.** 상한이 없으면
`strength: 20`이 검증을 지나 배율 10으로 렌더되고, 값과 그림이 갈린 이유가 어디에도 남지
않는다. 2배로 잡은 것은 캔버스 크기(1080x1920)로 이미 맞춰진 배경을 그 이상 확대하면 화면의
디테일이 업스케일로 뭉개지기 때문이다.
"""

_MOTION_FIELDS = {
    "kind": text(choices=MOTION_KINDS),
    # 확대 배율의 **증분**이다 — 0.08은 8% 더 확대한다는 뜻이고 배율 자체는 1 아래로 내려가지
    # 않는다 (내려가면 캔버스보다 작아져 배경 경계가 프레임에 노출된다).
    # 0을 받는 이유는 `kind`를 그대로 두고 움직임만 끄는 상태가 표현돼야 하기 때문이다.
    "strength": number(minimum=0.0, maximum=MOTION_STRENGTH_MAX),
}

_BACKGROUND_FIELDS = {
    "kind": text(choices=BACKGROUND_KINDS),
    "value": text(),
    # 배경 모션 (#34). **`render`가 아니라 여기 있다** — 적용 여부가 위 `kind`에 달려 있어서,
    # 떨어뜨려 놓으면 "모션을 켰는데 아무 일도 없다"의 이유가 파일에서 보이지 않는다.
    #
    # **선택이다.** 이 필드가 생기기 전에 만들어진 run 디렉터리의 `project.json`이 열려야
    # 하고, 없는 것은 `MOTION_NONE`과 같은 뜻이다 (`voice_volume`과 같은 이유, #81).
    "motion": section(_MOTION_FIELDS, required=False),
}

DEFAULT_VOICE_VOLUME = 1.0
"""`voice_volume`이 없는 프로젝트가 뜻하는 값 (#81).

**`config.SPEC`의 기본값과 같은 수지만 같은 질문의 답이 아니다** — 그쪽은 "새 프로젝트가 어떤
값으로 시작하는가"이고 이쪽은 "이 필드가 생기기 전에 만들어진 프로젝트가 무엇을 뜻하는가"다.
둘이 같은 수인 것은 게인 1.0이 원본 레벨이라는 사실(`audio_mix.UNITY_GAIN`) 하나에서 온다.
"""

_AUDIO_FIELDS = {
    # 낭독 장면이 하나도 없으면 `voice.mp3`가 생성되지 않는다 (PRD 6.2). 그래서 null을
    # 받는다. 키 자체는 필수로 둬서 앱이 존재 여부를 확인하지 않고 읽을 수 있게 한다.
    "voice": text(nullable=True),
    # 라이선스를 확인한 파일만 사용자가 지정한다. 기본은 없음 (PRD 8장).
    "music": text(nullable=True),
    # 효과음의 선형 게인 (#23). **편집 상태가 아니라 초기 상태다** — 렌더러가 실제로 읽는
    # 값이므로 `caption_style`과 같은 이유로 여기를 지나야 하고, config를 렌더가 다시 열면
    # 앱이 편집한 프로젝트와 CLI 렌더가 갈린다 (PRD 7.10). 앱(#81)의 볼륨 컨트롤이 이 필드를
    # 그대로 쓴다. 0이면 효과음이 없는 결과가 나온다.
    "sfx_volume": number(minimum=0),
    # 낭독의 선형 게인 (#81). **상한이 없다** — 1을 넘겨도 최종 오디오는 `alimiter`가
    # -1 dBFS에서 잡으므로(`audio_mix.LIMITER_CEILING`) 계약이 막을 것은 음수뿐이다.
    #
    # **선택이다.** 이 필드가 생기기 전에 만들어진 run 디렉터리의 `project.json`이 열려야
    # 하고, 그때의 뜻은 `DEFAULT_VOICE_VOLUME`이다. `sfx_volume`이 필수인 것과 갈리는 이유는
    # 계약의 성격이 아니라 도입 시점뿐이다.
    "voice_volume": number(minimum=0, required=False),
}

_ITEM_ID = Scalar("int", minimum=1)
"""검수 단위 하나를 가리키는 번호.

`scenes.json`의 `question_id`와 **같은 값**이고, 그것이 이미 공통 스키마의 어휘라
(`schemas/scenes.py`) 타입 경계를 넘지 않는다. 이 모듈은 그 번호가 무엇의 번호인지 모른다.
"""

OVERRIDE_EDITS = ("duration", "text", "overlays")
"""오버라이드 항목이 실제로 얹는 값 (#82의 `duration`, #83의 `text`·`overlays`).

빈 항목을 거부하는 근거이므로 편집 필드를 추가할 때 이 목록도 함께 늘린다 — 빠뜨리면
아무것도 얹지 않는 항목이 조용히 저장된다.
"""

OVERLAY_POSITIONS = (
    "top-left",
    "top-center",
    "top-right",
    "mid-left",
    "mid-center",
    "mid-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)
"""텍스트 오버레이가 붙는 9칸 (D2 확정 스펙 7.2).

세로-가로 순서이고 좌표는 이 스키마가 아니라 **렌더러가 정한다**
(`overlay.OVERLAY_ANCHORS` — 안전 영역은 D1 확정 스펙 1장의 표다).
"""

OVERLAY_COLORS = ("preset", "white", "muted")
"""오버레이 색 후보. **자유 색이 없다** (확정 스펙 7.2).

셋 다 자막 스타일 프리셋의 **역할 이름으로 옮겨진다**(`overlay.OVERLAY_COLOR_ROLES`) —
값을 복사하면 스타일을 바꿨을 때 D1 확정 스펙 6.3의 조합 판정이 △로 내려가도 드러나지
않는다. 렌더러에 색값을 적지 않는 것도 같은 규칙이다 (D1 확정 스펙 6장).
"""

OVERLAY_SIZES = (28, 40, 56)
"""오버레이 폰트 크기(px, 1080x1920 기준). 확정 스펙 7.2의 세 값이다."""

OVERLAY_WEIGHTS = (500, 700, 800)
"""오버레이 폰트 웨이트. **시안의 `400 | 600 | 800`이 아니다** (확정 스펙 7.1-2).

번들 Pretendard는 Medium·Bold·ExtraBold이고(`assets.FONT_FILES`) 렌더러는
`assets.font_path(weight)`로 파일을 직접 고른다 — 400·600을 저장하면 `AssetError`로 렌더가
멈춘다. **화면에서는 정상으로 보이고 렌더에서만 실패하는 종류의 오류라** UI에서 빼는 것만으로는
부족하고 계약이 거부해야 한다. 앱 CSS의 `400 500` / `600 700` 범위 매핑은 UI 텍스트용 편의이고
렌더러 계약이 아니다.
"""

TIMING_SCENE = "scene"
"""`timing`의 기본값 — 장면 전체를 덮는다 (확정 스펙 7.2)."""

_TIMING_WINDOW = Object(
    {
        # 장면 시작 기준 초. 끝 시각은 `video_renderer.align()`이 주는 장면 끝으로 clamp되고
        # 넘겨도 경고가 없다 — 장면 길이를 줄이면 함께 잘리는 것이 확정 동작이다 (7.2).
        "start": number(minimum=0.0),
        "dur": number(exclusive_minimum=0.0),
    }
)


class _Timing(Rule):
    """`"scene"` 또는 `{start, dur}` (확정 스펙 7.2).

    **모양이 둘로 갈리는 유일한 필드라 전용 규칙이다.** core에 일반 union primitive를 두면
    "어느 후보의 오류를 보여줄지"를 정해야 하고 그 답이 필드마다 다르지만, 여기서는 값이
    문자열인지 매핑인지가 곧 사용자의 의도라 분기가 명확하다 — 그래서 후보를 골라 그쪽 오류만
    낸다.
    """

    def check(self, value: Any, path: str, errors: list[str]) -> None:
        if isinstance(value, str):
            Scalar("str", choices=(TIMING_SCENE,)).check(value, path, errors)
        elif isinstance(value, dict):
            _TIMING_WINDOW.check(value, path, errors)
        else:
            errors.append(
                f'{path}: "{TIMING_SCENE}" 또는 {{start, dur}} 매핑이어야 한다. '
                f"받은 값: {describe(value)}"
            )

    def to_json_schema(self) -> dict[str, Any]:
        return {
            "oneOf": [
                {"type": "string", "enum": [TIMING_SCENE]},
                _TIMING_WINDOW.to_json_schema(),
            ]
        }


_OVERLAY = Object(
    {
        # 목록 안에서만 유일하면 된다 (아래 검증). 화면이 카드를 짚는 손잡이이고 렌더러는
        # 순서만 본다 — 겹칠 때 나중 항목이 위다.
        "id": text(),
        # **줄바꿈을 허용한다.** 자동 줄바꿈이 없고 줄 하나가 `drawtext` 하나다
        # (D1 확정 스펙 7.3, `overlay` 머리말). 폭·줄바꿈 속성이 없는 이유가 그것이다.
        "text": text(),
        "pos": text(choices=OVERLAY_POSITIONS),
        # 고른 모서리에서의 거리(px). **음수를 받는다** — 안전 영역 밖으로 밀어내는 것이
        # 사람의 선택일 수 있고, 넘치는 것은 프리뷰 정지 프레임에 그대로 보인다 (7.5).
        "offset": section({"x": integer(), "y": integer()}),
        "color": text(choices=OVERLAY_COLORS),
        "size": Field(Scalar("int", choices=OVERLAY_SIZES)),
        "weight": Field(Scalar("int", choices=OVERLAY_WEIGHTS)),
        "timing": Field(_Timing()),
    }
)
"""사람이 장면에 얹은 텍스트 하나 (확정 스펙 7.2).

**`scenes.json`에 두지 않는다.** 거기 두면 재생성(#77)이 장면을 다시 만들 때 사람이 넣은
오버레이가 사라진다 — 장면 길이(#82)와 같은 문제이고 그래서 같은 자리·같은 키를 쓴다
(PRD 14.1).
"""

_SCENE_OVERRIDE = Object(
    {
        # **키가 장면 인덱스가 아니다.** 인덱스는 문제를 추가·삭제하면 밀리고, 그러면 사람이
        # 조정한 값이 다른 장면에 붙는다 (#28이 새 문제 번호에서 같은 함정을 밟았다).
        # `role`과 `question_id`는 이미 `scenes.json`의 공통 어휘다 (PRD 7.4.1).
        "role": text(choices=ROLES),
        # 문제에 속한 장면(`question`·`countdown`·`answer`)에만 있다. 아래 검증이 강제한다.
        "question_id": Field(_ITEM_ID, required=False),
        # 사람이 조정한 장면 길이 (#82). **`scenes.json`의 `duration`을 덮지 않는다** —
        # `validate_scenes_final`이 낭독보다 짧은 값을 거부하므로 그쪽에 쓰면 앱의 저장이
        # 실패하고 그 run 디렉터리가 다시 열리지 않는다 (PRD 14.1). 낭독보다 짧아도 받는
        # 것은 화면이 경고하고 사람이 결정하기 때문이다.
        "duration": number(exclusive_minimum=0.0, required=False),
        # 사람이 고친 자막 문구 (#83). 장면의 `text` 한 칸을 덮는다 — 상단 문구(`heading`)와
        # 해설(`caption`)은 들어오지 않는다. `heading`은 한 문제의 세 장면이 공유하는 값이라
        # 한 장면에서 고치면 나머지가 갈리고, 해설은 콘텐츠 필드라 문제 편집(#28)이 소유한다
        # (확정 스펙 7.3).
        "text": text(required=False),
        # 사람이 얹은 텍스트 오버레이 (#83). **빈 목록은 얹는 값이 없다** — 마지막 항목을
        # 지운 자리에 `[]`가 남으면 아무것도 하지 않는 오버라이드가 파일에 쌓인다. 앱이 그때
        # 키를 지운다.
        "overlays": items(_OVERLAY, min_items=1, required=False),
    }
)
"""장면 하나에 사람이 얹은 편집 (PRD 14.1).

**세 편집이 항목 하나를 공유한다** — 길이(#82)와 문구·오버레이(#83)가 같은 장면을 가리키므로,
저장 위치를 나누면 같은 장면을 가리키는 키가 세 벌 생긴다.
"""

_RENDER_FIELDS = {
    "width": integer(minimum=1),
    "height": integer(minimum=1),
    "fps": integer(minimum=1),
    "output": text(),
    # 번인 오버레이가 읽는 값 (#20). **config가 아니라 여기서 읽는다** — 렌더가 설정
    # 파일을 다시 열면 앱(#29)이 편집한 프로젝트와 CLI 렌더가 갈린다 (PRD 7.10).
    # 이름 후보는 스키마가 아니라 `assets/`가 정하므로 여기서 열거하지 않는다. 없는
    # 이름은 렌더 시작 전에 `overlay.style_for`가 쓸 수 있는 이름을 나열하며 멈춘다.
    "caption_style": text(),
    # null이면 번들 Pretendard 세 웨이트를 쓴다 (확정 스펙 9장).
    "font_path": text(nullable=True),
    # 채널 브랜딩이라 콘텐츠가 아니라 프로젝트가 들고 있다 (확정 스펙 5.5).
    "cta_punch": text(),
    "cta_tail": text(),
    # 해설이 뜨는 시각 (장면 시작 기준 초, #22). **`timing.caption_onset_sec`의 사본이
    # 아니라 렌더러가 읽는 유일한 자리다** — 장면 길이 하한(#16)은 config에서 같은 값을
    # 읽고 이미 확정된 `duration`에 반영했다. 앱(#29)이 이 값을 키우면 렌더는 따라오지만
    # 장면 길이는 그대로이므로, 늘릴 수 있는 폭은 그 장면의 여유만큼이다.
    "caption_onset_sec": number(minimum=0),
    # 사람이 얹은 장면 편집 (#82, #83). **선택이다** — 이 필드가 생기기 전에 만들어진 run
    # 디렉터리의 `project.json`이 열려야 한다.
    "scene_overrides": items(_SCENE_OVERRIDE, required=False),
}

_REVIEW_FIELDS = {
    # 사람이 `flagged`/`unverified`를 보고 넘어가기로 한 항목 (#28).
    #
    # **`quiz.json`의 `verify`에 쓰지 않는 이유가 여기 있다.** 그 두 값은 검증기(#10)와
    # 검수 게이트(#11)가 소유하고 임계값 판정의 입력이다. 사람 판단이 같은 칸을 덮으면
    # 다음 실행의 판정이 조용히 통과한다 (D2 확정 스펙 1.4). 확인은 편집 상태이므로
    # 앱이 소유하는 이 파일이 맞는 자리이고, `--fail-on-flagged` 자동화 경로는 콘텐츠
    # 산출물만 보므로 사람 확인에 영향받지 않는다 (PRD 14.2).
    "acknowledged": items(_ITEM_ID),
    # 낭독 문구가 바뀌어 오디오·자막이 낡은 항목 (#28). 이 표시를 지우는 것은 재생성(#77)이다.
    #
    # 장면 **순서·개수**가 낡았는지는 여기 두지 않는다 — `scenes.json`의 `question_id`
    # 나열과 비교하면 나오는 값이라, 적어 두면 두 곳이 다른 말을 할 수 있다.
    "stale": items(_ITEM_ID),
    # **자막만 낡은 항목** (#83). 위 `stale`이 "음성까지 낡음"이고 이쪽은 "자막만 낡음"이다 —
    # 낭독으로 가지 않는 문구(퀴즈의 해설)만 바뀌면 `voice.mp3`는 그대로 쓸 수 있고
    # `captions.srt`만 다시 만들면 된다. 시안이 낡음을 두 상태로 갈랐고(파랑 사각 `↻` /
    # 파랑 원형 `♪`) 목록 하나로는 그 구분이 표현되지 않는다 (확정 스펙 7.3).
    #
    # **`stale`과 겹칠 수 있다.** 겹치면 강한 쪽(음성까지)이 화면에 서고, 재생성(#77)이 지우는
    # 것은 두 목록 모두다.
    #
    # **`project.build`가 만들지 않는다** — `timeline_stale`과 같은 자리다. 이 파일이 만들어진
    # 뒤에 앱이 쓰는 값이고, 필수로 만들면 이 필드가 생기기 전의 run 디렉터리가 열리지 않는다.
    "captions_stale": items(_ITEM_ID, required=False),
    # 사람이 `render.scene_overrides`에 얹은 편집이 `captions.srt`·`voice.mp3`에 아직 반영되지
    # 않은 상태 (#82의 장면 길이, #83의 자막 문구).
    #
    # **목록이 아니라 참·거짓이다.** 길이를 하나 고치면 그 뒤 장면의 시작 시각이 전부 밀리므로
    # 낡는 대상이 특정 항목이 아니라 타임라인 전체이고, 문구는 문제에 속하지 않는 `hook`·`cta`
    # 에서도 고칠 수 있어 항목 번호로는 애초에 표현되지 않는다 (PRD 14.1).
    #
    # **#77에서 문구 편집이 이 칸으로 들어왔다.** 그 전까지 "자막이 낡았다"의 근거는
    # `scene_overrides[].text`와 `scenes.json`의 비교였다 — 자막이 항상 `scenes.json`에서만
    # 만들어졌기 때문에 성립한 계산이다. 재생성이 **얹은 문구로** `captions.srt`를 만들면서
    # 그 비교는 "낡았는가"가 아니라 "고쳤는가"가 됐고, 비교 기준이 파일에서 사라졌다.
    # 얹은 편집 둘이 산출물에 닿는 시점이 같으므로 칸도 하나다.
    #
    # 지우는 것은 `stale`과 같이 재생성(#77)이다.
    "timeline_stale": flag(required=False),
}

PREVIEW_BLIND_SECTIONS = ("review", "audio")
"""**프리뷰 프레임에 닿지 않는** 섹션. 프리뷰 캐시가 이 목록을 쓴다(`api._signature`).

여기 이름이 없으면 값 하나를 고칠 때마다 프레임 11장이 2초대에 다시 만들어지고, 그 값이
그림을 정하지 않으면 결과는 같은 그림이다 (#28에서 `review`가, #81에서 `audio`가 밟았다).

**#81에서 기준이 넓어졌다.** 처음에는 "렌더러가 읽지 않는 섹션"이었고 `review` 하나였는데
(#28), 그 기준으로는 `audio`가 들어올 수 없다 — 렌더러는 오디오를 읽는다. 그런데 **프리뷰
명령에는 오디오 체인이 아예 없다** (`video_renderer.build_preview_command` — 필터 그래프에서
함께 들어내지 않으면 `alimiter` 출력이 연결되지 않아 실패한다, #27). 볼륨 슬라이더는 값이
연속으로 바뀌는 컨트롤이라 그 왕복이 편집 한 번이 아니라 드래그 한 번마다 붙는다.

그래서 조건은 **"이 섹션이 프리뷰 명령에 도달하는가"**이고, 렌더 전체가 아니라 그림이
기준이다. 새 섹션은 아무것도 하지 않아도 지문에 들어가고(목록이 "닿는 것"이 아니라 "닿지 않는
것" 쪽이다), 여기 이름을 더하려면 프리뷰 명령을 만드는 경로가 그 섹션을 열지 않아야 한다.
"""


def _check_review_ids_are_unique(data: Any, errors: list[str]) -> None:
    """같은 번호가 두 번 들어가지 않는다.

    두 목록 모두 집합의 뜻이라 중복은 값을 바꾸지 않는다. 그래서 조용히 통과시키면 앱이
    확인 버튼을 누를 때마다 목록을 늘리는 버그가 드러나지 않는다.
    """
    review = data.get("review")
    if not isinstance(review, dict):
        return
    for key in ("acknowledged", "stale", "captions_stale"):
        values = review.get(key)
        if not isinstance(values, list):
            continue
        seen: set[Any] = set()
        for index, value in enumerate(values):
            if value in seen:
                errors.append(f"review.{key}[{index}]: 중복된 번호 {value}")
            seen.add(value)


GROUPED_ROLES = ("question", "countdown", "answer")
"""문제 하나에 속한 장면. `question_id` 없이는 어느 장면인지 특정되지 않는다.

목록이 `scenes.json`의 `question_id` 유무와 같아야 하므로 장면 템플릿이 바뀌면 함께 본다 —
공통 계층이 아는 것은 "번호가 있는 역할"까지고 그 번호가 무엇인지는 모른다.
"""

FIXED_DURATION_ROLES = ("countdown",)
"""길이를 사람이 고칠 수 없는 역할.

`countdown`의 `duration`은 `seconds`와 정확히 같아야 하고(`schemas/scenes.py`) 그 값은 콘텐츠
필드다 — 문제 편집(#28)이 소유한다. **UI에서 빼는 것만으로는 부족하다** — 손으로 고친
`project.json`도 열리므로 계약이 거부해야 한다 (D2 확정 스펙 7.1).
"""


def _check_scene_overrides(data: Any, errors: list[str]) -> None:
    """오버라이드가 장면 하나를 가리키고, 뜻이 있고, 겹치지 않는지 (#82).

    구조 검증 다음이므로 타입은 이미 맞다. 여기서 보는 것은 **키의 뜻**이다.
    """
    render = data.get("render")
    if not isinstance(render, dict):
        return
    overrides = render.get("scene_overrides")
    if not isinstance(overrides, list):
        return

    seen: set[tuple[Any, Any]] = set()
    for index, override in enumerate(overrides):
        if not isinstance(override, dict):
            continue
        path = f"render.scene_overrides[{index}]"
        role = override.get("role")
        question_id = override.get("question_id")

        if role in GROUPED_ROLES and question_id is None:
            errors.append(
                f"{path}.question_id: {role} 장면은 문제 번호로 특정된다 — 번호가 없으면 "
                f"어느 장면인지 정해지지 않는다"
            )
        if role not in GROUPED_ROLES and question_id is not None:
            errors.append(
                f"{path}.question_id: {role} 장면은 영상에 하나뿐이라 번호를 받지 않는다. "
                f"받은 값: {question_id}"
            )
        if role in FIXED_DURATION_ROLES and "duration" in override:
            errors.append(
                f"{path}.duration: {role}의 길이는 사람이 고칠 수 없다 — seconds와 같아야 "
                f"하고 그 값은 콘텐츠가 소유한다"
            )
        if not any(key in override for key in OVERRIDE_EDITS):
            errors.append(
                f"{path}: 얹는 값이 없다. 쓸 수 있는 값: {', '.join(OVERRIDE_EDITS)}"
            )
        _check_overlay_ids(override.get("overlays"), path, errors)

        key = (role, question_id)
        if key in seen:
            errors.append(f"{path}: 같은 장면에 오버라이드가 두 번 있다 — {role} {question_id}")
        seen.add(key)


def _check_overlay_ids(overlays: Any, path: str, errors: list[str]) -> None:
    """한 장면의 오버레이 `id`가 서로 다른지 (#83).

    **장면 안에서만 유일하면 된다.** 앱이 카드를 짚는 손잡이이고 다른 장면의 목록과 섞이지
    않는다. 같은 번호가 두 번 있으면 화면에서 한 카드를 고쳤을 때 다른 카드가 함께 바뀌므로,
    조용히 통과시키면 그 버그가 파일에 쌓인다.
    """
    if not isinstance(overlays, list):
        return
    seen: set[Any] = set()
    for index, overlay in enumerate(overlays):
        if not isinstance(overlay, dict):
            continue
        identifier = overlay.get("id")
        if identifier in seen:
            errors.append(
                f"{path}.overlays[{index}].id: 같은 장면에 같은 id가 두 번 있다 — {identifier!r}"
            )
        seen.add(identifier)


_ROOT = Object(
    {
        "schema_version": integer(minimum=1),
        "type": choices_from(available_types, label="등록된 타입"),
        "language": text(),
        # `scenes.json` 참조. 장면 배열을 여기 복사하지 않는다 — 두 곳에 같은 장면이
        # 있으면 어느 쪽이 원본인지 모호해진다 (PRD 7.4.1).
        "scenes": text(),
        "background": section(_BACKGROUND_FIELDS),
        "audio": section(_AUDIO_FIELDS),
        "render": section(_RENDER_FIELDS),
        # **선택이다.** 필수로 만들면 이 필드가 생기기 전에 만들어진 run 디렉터리의
        # `project.json`이 열리지 않는다 — 사람이 검수하려고 남겨 둔 산출물이 그것이다.
        "review": section(_REVIEW_FIELDS, required=False),
    }
)

PROJECT_SCHEMA = Schema(
    name="project.json",
    versions=KNOWN_VERSIONS,
    root=_ROOT,
    checks=(_check_review_ids_are_unique, _check_scene_overrides),
)


def validate_project(data: Any, *, source: Path | None = None) -> None:
    """`project.json` 초기 상태를 검증한다. 위반이 있으면 `SchemaError`."""
    PROJECT_SCHEMA.validate(data, source=source)


def load_project(path: Path) -> dict[str, Any]:
    """`project.json`을 읽고 검증해서 돌려준다."""
    return PROJECT_SCHEMA.load(path)
