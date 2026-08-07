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


# --- 생성기가 LLM에 넘기는 부분 스키마 --------------------------------------

_MODEL_OMITS_ROOT = ("schema_version", "type", "category", "language")
"""모델에게 묻지 않는 최상위 필드 — 전부 코드가 정한다 (버전, 타입 이름, 고정 분류·언어)."""

_MODEL_OMITS_QUESTION = ("id", "countdown_sec", "verify")
"""모델에게 묻지 않는 문제 필드.

`id`는 난이도 정렬 뒤에 코드가 1부터 다시 매기고, `countdown_sec`은 config가 정하며,
`verify`는 `quiz_verifier`(#10)가 채운다. 물어봐야 버려지는 값에 토큰을 쓰지 않는다.
"""


def content_json_schema(
    *, question_count: int, answer_max_len: int, explanation_max_len: int
) -> dict[str, Any]:
    """`quiz_generator`가 `--json-schema`로 넘길 JSON Schema.

    **`QUIZ_SCHEMA`에서 파생한다.** 프롬프트 쪽에 필드 이름을 손으로 다시 적으면 계약이
    두 곳에 생기고, 한쪽만 고쳐졌을 때 모델이 낡은 모양을 만들어 낸다 (PRD 14.1).
    스파이크 2.2가 실측한 형태 — 3.1의 콘텐츠 구조에서 `verify`를 뺀 것 — 와 같다.

    길이·개수 상한은 스키마가 아니라 config가 정하므로 여기서 받아 얹는다. CLI가
    `--json-schema`를 강제하므로(스파이크 4.3) 상한 위반은 대개 모델 쪽에서 걸러진다.
    """
    schema = _ROOT.without(*_MODEL_OMITS_ROOT).to_json_schema()

    questions = schema["properties"]["questions"]
    questions["items"] = _QUESTION.without(*_MODEL_OMITS_QUESTION).to_json_schema()
    # 문제 수는 정확히 config 값이다. 모델이 더 내거나 덜 내면 카운트다운·장면 수가
    # 사용자가 지정한 값과 달라진다.
    questions["minItems"] = question_count
    questions["maxItems"] = question_count

    fields = questions["items"]["properties"]
    fields["answer"]["maxLength"] = answer_max_len
    fields["explanation"]["maxLength"] = explanation_max_len
    return schema


def validate_quiz(data: Any, *, source: Path | None = None) -> None:
    """`quiz.json` 내용을 검증한다. 위반이 있으면 `SchemaError`."""
    QUIZ_SCHEMA.validate(data, source=source)


def load_quiz(path: Path) -> dict[str, Any]:
    """`quiz.json`을 읽고 검증해서 돌려준다."""
    return QUIZ_SCHEMA.load(path)
