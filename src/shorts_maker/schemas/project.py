"""`project.json` 스키마 — 편집 앱이 프로젝트를 여는 입구 (PRD 7.10).

**대부분은 생성 직후의 초기 상태다.** 렌더러가 읽는 값(배경·오디오·출력 규격·자막 스타일)이
여기를 지나므로, config를 렌더가 다시 열면 앱이 편집한 프로젝트와 CLI 렌더가 갈린다.

**예외가 `review` 하나다** — 앱이 소유하는 편집 상태이고 렌더러가 읽지 않는다(#28).
PRD 7.10이 "앱 프레임워크가 정해진 뒤 추가한다"고 미뤄 둔 자리이며, 프레임워크는 #25에서
정해졌다.

**`render.scene_overrides`는 앱이 쓰지만 렌더러가 읽는다** (#82, #83). 그래서 `review`와 달리
`APP_STATE_SECTIONS`에 들어가지 않는다 — 사람이 얹은 길이·문구·오버레이가 프리뷰 프레임을
바꾸므로 지문에 들어가야 화면이 옛 그림에 머물지 않는다.

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
    Scalar,
    Schema,
    choices_from,
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
    # 효과음의 선형 게인 (#23). **편집 상태가 아니라 초기 상태다** — 렌더러가 실제로 읽는
    # 값이므로 `caption_style`과 같은 이유로 여기를 지나야 하고, config를 렌더가 다시 열면
    # 앱이 편집한 프로젝트와 CLI 렌더가 갈린다 (PRD 7.10). 앱(#26)이 트랙별 볼륨 편집을
    # 붙일 때 이 필드를 그대로 쓴다. 0이면 효과음이 없는 결과가 나온다.
    "sfx_volume": number(minimum=0),
}

_ITEM_ID = Scalar("int", minimum=1)
"""검수 단위 하나를 가리키는 번호.

`scenes.json`의 `question_id`와 **같은 값**이고, 그것이 이미 공통 스키마의 어휘라
(`schemas/scenes.py`) 타입 경계를 넘지 않는다. 이 모듈은 그 번호가 무엇의 번호인지 모른다.
"""

OVERRIDE_EDITS = ("duration",)
"""오버라이드 항목이 실제로 얹는 값. **#83이 `text`·`overlays`를 여기 더한다.**

빈 항목을 거부하는 근거이므로 편집 필드를 추가할 때 이 목록도 함께 늘린다 — 빠뜨리면
아무것도 얹지 않는 항목이 조용히 저장된다.
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
    # 사람이 장면 길이를 고쳐 `captions.srt`·`voice.mp3`가 어긋난 상태 (#82).
    #
    # **목록이 아니라 참·거짓이다.** 길이를 하나 고치면 그 뒤 장면의 시작 시각이 전부 밀리므로
    # 낡는 대상이 특정 항목이 아니라 타임라인 전체다. 항목 번호로 적을 수 있는 값이 아니다
    # (PRD 14.1). 지우는 것은 `stale`과 같이 재생성(#77)이다.
    "timeline_stale": flag(required=False),
}

APP_STATE_SECTIONS = ("review",)
"""**렌더러가 읽지 않는** 섹션. 앱이 소유하는 편집 상태다 (#28).

프리뷰 캐시가 이 목록을 쓴다(`api._signature`). 확인 버튼 한 번이 프레임 11장을 다시 만들면
2초가 붙는데, 그 값은 필터 그래프 어디에도 닿지 않아 결과가 같은 그림이다. **여기 이름을
추가하는 조건은 "렌더러가 이 섹션을 열지 않는다"이고**, 그것이 사실이 아니면 화면이 옛 그림에
머문다.
"""


def _check_review_ids_are_unique(data: Any, errors: list[str]) -> None:
    """같은 번호가 두 번 들어가지 않는다.

    두 목록 모두 집합의 뜻이라 중복은 값을 바꾸지 않는다. 그래서 조용히 통과시키면 앱이
    확인 버튼을 누를 때마다 목록을 늘리는 버그가 드러나지 않는다.
    """
    review = data.get("review")
    if not isinstance(review, dict):
        return
    for key in ("acknowledged", "stale"):
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

        key = (role, question_id)
        if key in seen:
            errors.append(f"{path}: 같은 장면에 오버라이드가 두 번 있다 — {role} {question_id}")
        seen.add(key)


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
