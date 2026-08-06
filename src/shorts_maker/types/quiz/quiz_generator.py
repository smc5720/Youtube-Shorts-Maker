"""주제 → `quiz.json` 내용 (퀴즈 스펙 4장).

**아직 스텁이다.** 실제 생성은 #9, 정답 검증은 #10이 채운다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...config import Config


def generate(*, topic: str, config: Config) -> dict[str, Any]:
    """주제에서 퀴즈 문제 세트를 만든다.

    돌려주는 dict는 `validate_quiz`를 통과해야 한다. 문제 수와 난이도, LLM 설정은
    `config.quiz` / `config.llm`에서 읽는다.

    검증(#10)까지 마친 상태를 돌려준다 — 각 문제의 `verify`가 채워져 있다. 검증을
    별도의 플러그인 축으로 두지 않는 이유는 그것이 퀴즈 타입 내부 단계이기 때문이다
    (레지스트리가 아는 축은 생성기와 장면 템플릿 둘뿐이다).
    """
    raise NotImplementedError("퀴즈 콘텐츠 생성기는 #9에서 구현한다 (정답 검증은 #10)")
