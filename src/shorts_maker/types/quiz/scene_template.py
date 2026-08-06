"""`quiz.json` → `scenes.json` 초안 (퀴즈 스펙 2장, 4장).

**아직 스텁이다.** 실제 장면 구성은 #12가 채운다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...config import Config


def build(content: Mapping[str, Any], *, config: Config) -> dict[str, Any]:
    """퀴즈 콘텐츠를 후킹 → (질문·카운트다운·정답) × N → CTA 장면으로 편다.

    돌려주는 dict는 `validate_scenes`(초안 검증)를 통과해야 한다. 낭독 장면의 `duration`은
    채우지 않는다 — 실측 오디오 길이로 확정하는 것은 #16의 몫이다 (PRD 7.5.1).

    타입 전용 정보를 여기서 `scenes.json` 필드로 옮겨 담는다. `countdown_sec`은 카운트다운
    장면의 `seconds`로, `explanation`은 정답 장면의 `caption`으로 간다 — 렌더러가
    `quiz.json`을 직접 읽지 않게 하는 유일한 지점이다 (퀴즈 스펙 1.1).
    """
    raise NotImplementedError("퀴즈 장면 템플릿은 #12에서 구현한다")
