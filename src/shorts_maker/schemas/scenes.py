"""`scenes.json` 스키마 — 콘텐츠와 공통 파이프라인 사이의 유일한 계약 (PRD 7.4.1).

**이 파일은 두 가지 상태로 존재한다.** 장면 템플릿(#12)이 만든 것은 초안이고,
`narrate: true` 장면의 `duration`·`audio`·`audio_duration`·`narration_offset`은 TTS
단계(#15, #16)가 채운다. 그래서 검증이 두 개다.

| 함수 | 대상 | 요구 |
| --- | --- | --- |
| `validate_scenes` | 초안과 확정 상태 모두 | 구조와 열거값, 낭독 장면의 목표치 |
| `validate_scenes_final` | TTS 이후 | 위 + 모든 장면의 `duration`, 낭독 장면의 오디오 필드 |

스키마 자체는 하나이고 **오디오 필드는 optional**이다. 초안용/확정용 스키마를 따로 두면
공통 필드가 두 곳에서 갈라진다. 갈라진 상태를 검증하는 대신, 확정 상태의 추가 요구를
`checks`로 얹는다.

**길이 규칙은 `narrate` 플래그로만 분기한다.** 고정 길이 장면 목록(`hook`·`cta`)을 여기
하드코딩하지 않는다 — 낭독을 추가하기로 결정하면 바꿀 것이 `narrate` 하나여야 한다
(PRD 7.5.1).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..shorts_types import available_types
from .core import Object, Schema, choices_from, flag, integer, items, number, text

SCHEMA_VERSION = 1
KNOWN_VERSIONS = (1,)

ROLES = ("hook", "question", "countdown", "answer", "cta")
"""렌더러가 분기하는 유일한 값. 이 장면이 어떤 콘텐츠에서 나왔는지는 담지 않는다."""

SEGMENT_DIR = "audio"
SEGMENT_PATTERN = r"audio/seg-\d{3,}\.mp3"

AUDIO_FIELDS = ("audio", "audio_duration", "narration_offset")
"""TTS 단계가 채우는 필드. 초안에는 없고, 확정 검증이 낭독 장면에만 요구한다."""

DURATION_DIGITS = 3
"""시간 필드를 기록하는 소수 자리 — 밀리초 자리다 (PRD 7.5.1).

`audio_duration`을 재는 단계(#15)와 `duration`·`narration_offset`을 확정하는 단계(#16)가
같은 값으로 반올림해야 하므로 계약 쪽에 둔다. 단계마다 자기 상수를 들면 두 필드의 정밀도가
갈리고, 아래 허용 오차는 그 갈림을 덮을 만큼 크지 않다. 30fps 프레임 경계 정렬은 여기가
아니라 렌더러가 한다.
"""

# duration이 audio_duration보다 짧은지 비교할 때의 허용 오차. 두 값 모두 위 자리수로
# 반올림해 기록하므로 같은 값이 부동소수점 비교에서 어긋날 수 있다.
_TOLERANCE = 1e-6


def segment_path(scene_index: int) -> str:
    """장면 인덱스에 대응하는 세그먼트 오디오 경로 (PRD 7.5.2).

    파일명 규약의 단일 진실 공급원이다. TTS(#15)가 파일을 쓸 때도 확정 검증이 인덱스
    일치를 확인할 때도 이 함수를 쓴다. `q2_answer.mp3` 같은 타입 전용 어휘를 파일명에
    넣지 않는 이유는 퀴즈 스펙 1.1에 있다.
    """
    return f"{SEGMENT_DIR}/seg-{scene_index:03d}.mp3"


_SCENE = Object(
    {
        "role": text(choices=ROLES),
        # 낭독 장면에는 필수다 (아래 `_check_scene_roles`). 카운트다운처럼 화면에 문구가
        # 없는 장면도 있으므로 스키마 수준에서는 optional이다.
        "text": text(required=False),
        # 확정 길이. 낭독 장면은 TTS가 실측값으로 채우므로 초안에는 없을 수 있다.
        "duration": number(required=False, exclusive_minimum=0.0),
        # 없으면 낭독이 아니다. 있는 그대로가 길이 규칙의 분기 조건이다.
        "narrate": flag(required=False),
        # 템플릿이 넣은 목표치. 확정값이 크게 벌어졌는지 경고할 때만 쓴다 (#15, #16).
        "target_duration": number(required=False, exclusive_minimum=0.0),
        "audio": text(required=False, pattern=SEGMENT_PATTERN),
        "audio_duration": number(required=False, exclusive_minimum=0.0),
        # `voice.mp3` 안에서 이 세그먼트가 시작하는 시각. 자막 타임코드의 기준(#17).
        "narration_offset": number(required=False, minimum=0.0),
        # 타입 전용 정보를 옮겨 담는 통과 필드. 렌더러는 `role`과 함께 이 값만 본다.
        # 새 타입이 자기 통과 필드를 추가할 때는 여기 선언을 함께 추가한다.
        "question_id": integer(required=False, minimum=1),
        "caption": text(required=False),
        # 블록 내내 상단에 유지되는 문구. `text`와 별개다 — 정답 장면에서는 `text`가 정답,
        # `heading`이 질문이라 두 값이 갈린다. 렌더러 규칙은 "있으면 상단에 그린다" 하나다
        # (D1 확정 스펙 8장). 질문 장면에서는 `text`와 값이 같고, 그 중복이 규칙을 하나로
        # 유지하는 값이다.
        "heading": text(required=False),
        # 본문 위에 붙는 작은 라벨. 퀴즈에서는 카테고리 라벨이지만 이름은 타입 중립이다.
        "kicker": text(required=False),
        "seconds": integer(required=False, minimum=1),
        "sfx": text(required=False),
    }
)

_ROOT = Object(
    {
        "schema_version": integer(minimum=1),
        # 후보는 레지스트리가 정한다. 등록된 타입이 만든 파일을 스키마가 반려하면 안 된다.
        "type": choices_from(available_types, label="등록된 타입"),
        "scenes": items(_SCENE, min_items=1),
    }
)


def _check_scene_roles(data: Any, errors: list[str]) -> None:
    """초안 단계에서 장면 템플릿이 채웠어야 하는 것."""
    for index, scene in enumerate(data["scenes"]):
        path = f"scenes[{index}]"
        if scene.get("narrate"):
            # 낭독할 문장과 목표치는 템플릿이 넣는다. `text`가 없으면 TTS가 합성할
            # 대상이 없고, `target_duration`이 없으면 실측값이 튀었는지 판정할 기준이 없다.
            for key in ("text", "target_duration"):
                if key not in scene:
                    errors.append(f"{path}.{key}: 낭독 장면(narrate: true)에 필요하다")
        if scene["role"] == "countdown" and "seconds" not in scene:
            # 렌더러가 3-2-1 오버레이를 그릴 때 세는 초 수다 (#21).
            errors.append(f"{path}.seconds: countdown 장면에 필요하다")


def _check_finalized(data: Any, errors: list[str]) -> None:
    """TTS 단계까지 지난 상태인지 확인한다.

    낭독 장면에만 오디오 필드를 요구한다. `narrate`가 없거나 false인 장면(`hook`,
    `countdown`, `cta`)에는 요구하지 않는다 — 실측할 오디오가 없다.
    """
    for index, scene in enumerate(data["scenes"]):
        path = f"scenes[{index}]"
        duration = scene.get("duration")
        if duration is None:
            errors.append(
                f"{path}.duration: 확정 상태에는 필수다 "
                "(낭독 장면은 TTS가 실측 오디오 길이로 채운다)"
            )

        if scene.get("narrate"):
            missing = [key for key in AUDIO_FIELDS if key not in scene]
            if missing:
                errors.append(
                    f"{path}: 낭독 장면인데 TTS가 채우는 필드가 없다: {', '.join(missing)}"
                )
            else:
                expected = segment_path(index)
                if scene["audio"] != expected:
                    errors.append(
                        f"{path}.audio: 세그먼트 번호가 장면 인덱스와 다르다. "
                        f"{expected}이어야 한다. 받은 값: {scene['audio']!r}"
                    )
                if duration is not None and duration + _TOLERANCE < scene["audio_duration"]:
                    errors.append(
                        f"{path}.duration: 낭독 길이 {scene['audio_duration']}초보다 짧아 "
                        f"음성이 잘린다. 받은 값: {duration}"
                    )
        else:
            # 세그먼트 개수는 `narrate: true` 장면 수와 같다 (PRD 7.5.2). 낭독이 아닌
            # 장면에 오디오 필드가 있으면 둘 중 하나가 틀린 것이고, 십중팔구 플래그 누락이다.
            present = [key for key in AUDIO_FIELDS if key in scene]
            if present:
                errors.append(
                    f"{path}: 낭독 장면이 아닌데 오디오 필드가 있다: {', '.join(present)} "
                    "— narrate: true를 빠뜨렸는지 확인한다"
                )

        if scene["role"] == "countdown" and duration is not None and "seconds" in scene:
            # 숫자 전환이 정수 초에 맞아야 하므로 실측 보정 대상이 아니다 (PRD 7.5.1).
            if abs(duration - scene["seconds"]) > _TOLERANCE:
                errors.append(
                    f"{path}.duration: countdown은 seconds({scene['seconds']}초)와 "
                    f"같아야 한다. 받은 값: {duration}"
                )


SCENES_SCHEMA = Schema(
    name="scenes.json",
    versions=KNOWN_VERSIONS,
    root=_ROOT,
    checks=(_check_scene_roles,),
)

SCENES_FINAL_SCHEMA = replace(
    SCENES_SCHEMA, checks=(*SCENES_SCHEMA.checks, _check_finalized)
)
"""확정 상태용. 같은 root를 공유하므로 공통 필드가 갈라지지 않는다."""


def validate_scenes(data: Any, *, source: Path | None = None) -> None:
    """초안과 확정 상태 모두를 받는 기본 검증. 위반이 있으면 `SchemaError`."""
    SCENES_SCHEMA.validate(data, source=source)


def validate_scenes_final(data: Any, *, source: Path | None = None) -> None:
    """TTS 이후의 확정 상태를 요구하는 검증.

    자막·렌더러·메타데이터는 확정 상태만 입력으로 받으므로(퀴즈 스펙 4장) 이 함수를 쓴다.
    합성 트랙 안의 세그먼트 배치가 실제로 맞는지(오프셋과 장면 시작 시각의 관계)는 여기서
    보지 않는다 — config의 `lead_in`이 필요하고, 그 계산은 #16이 소유한다.
    """
    SCENES_FINAL_SCHEMA.validate(data, source=source)


def load_scenes(path: Path, *, finalized: bool = False) -> dict[str, Any]:
    """`scenes.json`을 읽고 검증해서 돌려준다.

    Args:
        finalized: TTS 이후 상태를 요구한다. 기본값은 초안도 통과시킨다.
    """
    schema = SCENES_FINAL_SCHEMA if finalized else SCENES_SCHEMA
    return schema.load(path)
