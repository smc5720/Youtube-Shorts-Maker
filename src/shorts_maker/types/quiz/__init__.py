"""퀴즈 타입 선언 — 레지스트리(`shorts_types`)가 이 모듈의 `SHORTS_TYPE`을 읽는다.

장면 템플릿은 아직 스텁이다 (#12). 이 파일이 하는 일은 그 이슈가 채울 자리와 퀴즈의
산출물 조건, 설정 요구를 **한 곳에** 고정하는 것이다.
"""

from __future__ import annotations

from ...schemas.quiz import QUIZ_SCHEMA, TYPE
from ...shorts_types import ShortsType
from .quiz_generator import check_config, generate
from .quiz_review import review
from .scene_template import build

SHORTS_TYPE = ShortsType(
    name=TYPE,
    # 파일명은 스키마가 확정한다. 여기 문자열을 다시 적으면 계약이 두 곳에 생긴다.
    content_artifact=QUIZ_SCHEMA.name,
    generator=generate,
    scene_template=build,
    # 문제 수 허용 범위(3~5)는 퀴즈의 규칙이다. 파이프라인이 run 디렉터리를 만들기 전에
    # 부른다 — 설정 오류 하나로 빈 run 디렉터리가 남지 않는다.
    config_check=check_config,
    # 임계값 판정은 퀴즈의 규칙이다 (퀴즈 스펙 5장). 파이프라인은 결과 항목만 경고로
    # 옮기고, `confidence`가 무엇인지도 임계값을 어디서 읽는지도 모른다 (#11).
    content_review=review,
    # 낭독 대상이 `quiz.json`의 질문·정답 필드이므로 별도 대본이 없다 (PRD 6.2, 퀴즈 스펙 4장).
    produces_script=False,
    # 주제에서 바로 문제를 만든다. 요약할 원문이 없다.
    produces_summary=False,
)
