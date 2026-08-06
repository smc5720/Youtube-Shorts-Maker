"""`quiz.json` 스키마 — 퀴즈 타입의 콘텐츠 산출물 (퀴즈 스펙 3.1).

사람이 검수·수정하는 원본이다. `scenes.json`은 여기서 파생되며, 이 파일을 읽는 코드는
`quiz_generator` / `quiz_verifier` / 장면 템플릿뿐이다 — 공통 파이프라인은 열지 않는다
(PRD 7.4.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import Object, Schema, integer, items, number, section, text

SCHEMA_VERSION = 1
KNOWN_VERSIONS = (1,)

TYPE = "quiz"
DIFFICULTIES = ("easy", "medium", "hard")
VERIFY_STATUSES = ("verified", "unverified", "flagged")

_VERIFY_FIELDS = {
    "status": text(choices=VERIFY_STATUSES),
    "confidence": number(minimum=0.0, maximum=1.0),
    # 근거를 대지 못하는 검증도 있다. 필수로 만들면 모델이 출처를 지어내는 쪽으로
    # 압력을 받는다 — 사실 검증(퀴즈 스펙 5장)의 목적과 반대다.
    "source": text(required=False),
}

_QUESTION = Object(
    {
        "id": integer(minimum=1),
        "question": text(),
        "answer": text(),
        "explanation": text(),
        "difficulty": text(choices=DIFFICULTIES),
        # 정수 초만 받는다. 카운트다운 숫자 전환이 정수 초에 맞아야 한다 (PRD 7.5.1).
        "countdown_sec": integer(minimum=1),
        # **초안에는 없다.** `quiz_generator`(#9)가 만든 직후에는 비어 있고
        # `quiz_verifier`(#10)가 채운다. 필수로 두면 #9가 자기 산출물을 검증할 수 없다.
        "verify": section(_VERIFY_FIELDS, required=False),
    }
)

_ROOT = Object(
    {
        "schema_version": integer(minimum=1),
        "type": text(choices=(TYPE,)),
        "category": text(),
        "language": text(),
        "hook": text(),
        "cta": text(),
        "questions": items(_QUESTION, min_items=1),
    }
)


def _check_unique_ids(data: Any, errors: list[str]) -> None:
    """`id`는 문제를 가리키는 유일한 손잡이다.

    `scenes.json`의 `question_id`와 편집 앱의 문제 선택이 이 값을 참조한다. 중복되면
    "3번 문제 정답 수정"이 어느 장면에 반영될지 알 수 없다.
    """
    seen: dict[int, int] = {}
    for index, question in enumerate(data["questions"]):
        question_id = question["id"]
        if question_id in seen:
            errors.append(
                f"questions[{index}].id: 중복된 id {question_id} "
                f"(questions[{seen[question_id]}]와 같다)"
            )
        else:
            seen[question_id] = index


QUIZ_SCHEMA = Schema(
    name="quiz.json",
    versions=KNOWN_VERSIONS,
    root=_ROOT,
    checks=(_check_unique_ids,),
)


def validate_quiz(data: Any, *, source: Path | None = None) -> None:
    """`quiz.json` 내용을 검증한다. 위반이 있으면 `SchemaError`."""
    QUIZ_SCHEMA.validate(data, source=source)


def load_quiz(path: Path) -> dict[str, Any]:
    """`quiz.json`을 읽고 검증해서 돌려준다."""
    return QUIZ_SCHEMA.load(path)
