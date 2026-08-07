"""`quiz.json` → `scenes.json` 초안 (퀴즈 스펙 2장, 4장, 이슈 #12).

세 가지가 이 모듈의 형태를 정한다.

- **`duration`과 `target_duration`은 장면마다 다르게 채운다.** 낭독 장면(`question`,
  `answer`)은 목표치만 넣고 확정 길이를 비운다 — 실측 오디오 길이로 확정하는 것은 #16의
  몫이다 (PRD 7.5.1). 비낭독 장면(`hook`, `countdown`, `cta`)은 반대로 여기서 확정값까지
  넣는다. TTS는 `narrate: true` 장면만 건드리므로, 비워 두면 채워 줄 주체가 없어
  `validate_scenes_final`이 실패한다.
- **타입 전용 정보를 통과 필드로 옮겨 담는 자리가 여기다.** `countdown_sec` → `seconds`,
  `explanation` → `caption`, 문제 `id` → `question_id`. 렌더러가 `quiz.json`을 직접 읽지
  않게 하는 유일한 지점이며, 이 경계가 깨지면 두 번째 타입을 추가할 때 공통 파이프라인
  전체를 고쳐야 한다 (퀴즈 스펙 1.1).
- **장면 순서가 오디오 세그먼트 번호를 정한다.** 파일명이 장면 배열 위치로 매겨지므로
  (`audio/seg-003.mp3`, PRD 7.5.2) 순서를 바꾸면 세그먼트 번호가 전부 바뀐다. 이 모듈이
  그 순서를 확정하고, 앱에서 순서를 편집할 때의 재계산은 #29가 다룬다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

# `TYPE`은 `SHORTS_TYPE.name`과 같은 값이다. 선언(`__init__.py`)이 이 모듈을 import하므로
# 거기서 가져오면 순환 import가 되고, 문자열을 다시 적으면 계약이 두 곳에 생긴다.
from ...schemas.quiz import TYPE
from ...schemas.scenes import SCHEMA_VERSION, validate_scenes

if TYPE_CHECKING:
    from ...config import Config

HOOK_DURATION = 3.0
CTA_DURATION = 4.0
"""낭독이 없는 앞뒤 장면의 고정 길이 (퀴즈 스펙 2장 표).

현재 TTS 범위가 질문·정답뿐이라 이 둘에는 실측할 오디오가 없다. **config 키를 새로 열지
않는다** — 바꿔야 할 이유가 생기면 그때 연다. 낭독을 추가하기로 결정하면 두 장면은
`narrate: true`가 되고 길이 규칙은 `narrate` 하나로 갈린다 (PRD 7.5.1).
"""

NARRATION_TARGET = 3.0
"""낭독 장면의 목표 길이 (퀴즈 스펙 2장).

**글자 수 기반 추정식을 쓰지 않는다.** 목표치는 확정값이 크게 벌어졌는지 경고하는 기준일
뿐이고(#16), 추정식을 넣으면 근거 없는 상수가 하나 더 생긴다. 실제 길이는 합성된 오디오를
실측해 정한다.
"""

COUNTDOWN_SFX = "beep"
ANSWER_SFX = "correct"
"""효과음 이름 (퀴즈 스펙 2장). 라이선스가 명확한 에셋 파일 번들은 #18이 붙인다."""


def build(content: Mapping[str, Any], *, config: Config) -> dict[str, Any]:
    """퀴즈 콘텐츠를 후킹 → (질문·카운트다운·정답) × N → CTA 장면으로 편다.

    문제 하나가 서로 인접한 서브 장면 3개가 되고, 세 장면 모두에 그 문제의 `question_id`가
    붙는다. `hook`·`cta`는 어느 문제에서도 나오지 않았으므로 붙이지 않는다.

    **설정을 읽지 않는다.** 길이는 스펙이 정한 고정값이거나(`hook`·`cta`) 콘텐츠가 들고
    있는 값(`countdown_sec`)이고, 나머지는 실측이 정한다. 인자를 받는 것은 장면 템플릿
    축의 계약이기 때문이다 (`shorts_types.SceneTemplate`).

    Raises:
        SchemaError: 조립한 결과가 `scenes.json` 초안 스키마를 만족하지 않을 때.
    """
    scenes: list[dict[str, Any]] = [
        {"role": "hook", "text": content["hook"], "duration": HOOK_DURATION}
    ]
    for question in content["questions"]:
        scenes.extend(_question_block(question))
    scenes.append({"role": "cta", "text": content["cta"], "duration": CTA_DURATION})

    data = {"schema_version": SCHEMA_VERSION, "type": TYPE, "scenes": scenes}
    # 자기 산출물을 검증한다. 여기서 통과하지 못한 초안은 TTS·자막·렌더가 전부 잘못된
    # 계약 위에서 돌게 되고, 원인은 이 모듈에 있다 (`quiz_generator`가 하는 것과 같다).
    validate_scenes(data)
    return data


def _question_block(question: Mapping[str, Any]) -> list[dict[str, Any]]:
    """문제 하나 → 인접한 세 장면. 순서가 곧 화면 순서다 (퀴즈 스펙 2장).

    `countdown`의 `duration`은 `seconds`와 정확히 같다. 숫자 전환이 정수 초에 맞아야
    하므로 실측 보정 대상이 아니고, 확정 검증이 이 일치를 강제한다 (PRD 7.5.1).
    """
    question_id = question["id"]
    seconds = question["countdown_sec"]
    return [
        {
            "role": "question",
            "question_id": question_id,
            "text": question["question"],
            "narrate": True,
            "target_duration": NARRATION_TARGET,
        },
        {
            "role": "countdown",
            "question_id": question_id,
            "seconds": seconds,
            "duration": float(seconds),
            "sfx": COUNTDOWN_SFX,
        },
        {
            "role": "answer",
            "question_id": question_id,
            "text": question["answer"],
            # 해설은 낭독하지 않고 자막으로만 낸다 (퀴즈 스펙 0장의 TTS 범위).
            "caption": question["explanation"],
            "narrate": True,
            "target_duration": NARRATION_TARGET,
            "sfx": ANSWER_SFX,
        },
    ]
