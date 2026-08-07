"""블라인드 재답변 검증 — `quiz.json`의 `verify`를 채운다 (퀴즈 스펙 5장, 이슈 #10).

다섯 가지 결정이 이 모듈의 형태를 정한다.

- **정답을 감춘다.** 생성된 정답을 보여주고 "맞습니까?"라고 물으면 같은 모델이 자기 출력에
  동조해 검증 효과가 없다. 질문만 다시 물어 독립 답변을 받고 코드가 대조한다. 프롬프트에
  원래 정답이 들어가지 않는다는 것이 이 모듈의 유일한 안전장치이므로 테스트가 프롬프트
  문자열을 직접 검사한다.
- **호출은 문제 수와 무관하게 `runs + 1`회다.** 문제별로 부르면 CLI 기동 오버헤드(호출당
  약 6.5초, 스파이크 3장)가 문제 수만큼 곱해진다. 한 호출에 문제를 묶으면 문제끼리
  컨텍스트가 새지만, **새는 것은 질문뿐이고 정답이 아니다** — 블라인드성은 "원래 정답이
  프롬프트에 없다"로 정의되며 묶어도 그대로다. 게다가 한 영상의 문제들은 애초에 같은
  주제로 한 번에 생성된 것이라 주제 인접성은 이미 공유돼 있다 (`quiz_generator`, #9).
- **모호성 프로브를 따로 둔다.** *정답이 틀린 게 아니라 질문 표현이 모호한* 유형
  ("한국 최초의 금속활자본은?" — `현존하는`이 빠져 답이 갈린다)은 블라인드 재답변으로는
  원리상 잡히지 않는다. 재답변 모델도 같은 근거로 같은 답을 내기 때문이다 (스파이크 4.2).
  정답을 감춘 채 "답이 하나로 정해지는가"만 묻는 호출을 1회 더 쓴다. 스파이크에서 검출률이
  0/24였던 단계에 실검출 경로를 만드는 것이 이 호출의 값이다.
- **`confidence = 일치율 × 자기 확신도`다.** 일치율만 쓰면 `runs: 2`에서 값이 `{0, 0.5, 1}`
  세 개뿐이라 임계값 0.8이 "2회 전부 일치"와 같은 뜻이 되고, 임계값을 조정할 수 있다는
  전제가 허구가 된다. `runs`를 올려 해상도를 얻는 길은 호출이 선형으로 늘어나 버렸다 —
  검증은 이미 문제당 비용의 59%다 (스파이크 4.4). 대신 재답변 모델이 자기 답에 대해 보고한
  확신도를 곱해 연속값을 만든다. 자세한 성질은 `_confidence`에 적었다.
- **임계값은 여기서 적용하지 않는다.** `llm.verifier.confidence_threshold`를 읽는 것은
  #11이다. 이 모듈은 "검증 단계가 결함을 찾았는가"만 판정한다 — 재답변 불일치와 질문
  모호는 임계값과 무관한 실검출이므로 바로 `flagged`가 되고, 결함을 못 찾은 문제는
  `verified`로 두되 `confidence`에 확신도를 남겨 #11이 그 축에서 자른다.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from statistics import fmean
from typing import TYPE_CHECKING, Any

from ... import PACKAGE_LOGGER
from ...llm import LLMError, provider_for_role
from ...schemas.quiz import VERIFY_STATUSES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ...config import Config
    from ...llm import LLMProvider

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.quiz")
"""`shorts_maker` 로거의 자식이라 `run_logging`이 붙인 핸들러로 그대로 흘러간다."""

VERIFIED = "verified"
UNVERIFIED = "unverified"
FLAGGED = "flagged"
"""`verify.status` 값. 스키마의 `VERIFY_STATUSES`와 같은 집합이며 테스트가 그것을 고정한다.

- `verified`: 검증 단계가 결함을 찾지 못했다. **"정답이 맞다"가 아니다.**
- `flagged`: 실검출 — 재답변이 갈렸거나 질문이 모호하다.
- `unverified`: 검증이 완료되지 않았다. 판단 근거가 없다는 뜻이며 성공이 아니다.
"""

MIN_RUNS = 1
"""`llm.verifier.runs`의 하한. 0이면 재답변이 한 번도 없어 검증 단계가 이름만 남는다."""

SOURCE_MAX_LEN = 200
"""`verify.source`에 남길 길이 상한.

`quiz.json`은 사람이 검수하는 원본이다. 모델이 근거를 문단으로 내면 문제 배열이 읽을 수
없게 길어지므로 자른다. 잘린 원문이 필요하면 run.log에 호출 기록이 있다.
"""

ANSWER_SYSTEM = (
    "너는 한국어 상식 문제에 답하는 응답기다. "
    "각 질문에 대한 정답만 간결하게 답한다. "
    "모르면 answer를 빈 문자열로 두고 certainty를 0으로 둔다. "
    "JSON 외에는 아무것도 출력하지 않는다."
)
"""재답변 시스템 프롬프트. **문제를 검토하는 역할이 아니라 푸는 역할을 준다.**

"검증하라"고 말하는 순간 모델은 눈앞의 문장을 정답 후보로 취급하기 시작한다. 스파이크
2.3이 실측한 형태를 유지한다.
"""

AMBIGUITY_SYSTEM = (
    "너는 한국어 퀴즈 문제의 표현을 검토하는 심사자다. "
    "각 질문이 답을 하나로 확정하는지만 판단한다. 질문에 답하지는 않는다. "
    "JSON 외에는 아무것도 출력하지 않는다."
)


def build_answer_prompt(questions: Sequence[dict[str, Any]]) -> str:
    """블라인드 재답변 프롬프트.

    **`question["answer"]`를 읽지 않는다.** 이 함수가 문제 dict 전체를 받으면서도 질문
    문자열만 꺼내 쓰는 것이 블라인드성의 실체다.
    """
    return (
        "아래 질문에 각각 답하라.\n"
        "- 질문 앞에 붙은 id를 그대로 달아 답한다.\n"
        "- answer에는 정답 하나만 담는다. 문장으로 풀어 쓰지 않는다.\n"
        "- certainty는 그 답이 맞다고 보는 정도다 (0~1). 확실하지 않으면 낮게 준다.\n"
        "- basis에는 그렇게 판단한 근거를 한 줄로 적는다. "
        "출처가 기억나지 않으면 지어내지 말고 아는 근거만 적는다.\n"
        "\n" + _numbered(questions)
    )


def build_ambiguity_prompt(questions: Sequence[dict[str, Any]]) -> str:
    """모호성 프로브 프롬프트. 답을 묻지 않고 질문의 확정성만 묻는다."""
    return (
        "아래 질문들이 답을 하나로 확정하는지 판단하라. 질문에 답하지는 마라.\n"
        "- 기준·범위·시점이 빠져 답이 갈릴 수 있으면 single_answer를 false로 둔다.\n"
        "  예: '한국 최초의 금속활자본은?' — '현존하는'이 빠져 직지심체요절과 "
        "상정고금예문으로 갈린다.\n"
        "- '최초', '최대', '가장 ~한'처럼 기준에 따라 답이 달라지는 표현을 특히 본다.\n"
        "- 답이 하나로 정해지면 single_answer를 true로 둔다.\n"
        "- reason에 그렇게 판단한 근거를 한 줄로 적는다.\n"
        "\n" + _numbered(questions)
    )


def answer_json_schema(*, question_count: int) -> dict[str, Any]:
    """재답변 호출에 넘길 JSON Schema.

    **산출물 스키마에서 파생하지 않는다.** `quiz.json`의 `verify`가 담는
    `status`·`confidence`는 코드가 계산하는 값이라 모델에게 묻지 않고, 모델이 내는
    `answer`·`certainty`·`basis`는 산출물에 그대로 실리는 필드가 아니다. 파생할 대응
    관계 자체가 없다 — `content_json_schema()`(#9)와 다른 점이다.
    """
    return _keyed_by_id(
        "answers",
        {
            "answer": {"type": "string"},
            "certainty": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "basis": {"type": "string"},
        },
        question_count=question_count,
    )


def ambiguity_json_schema(*, question_count: int) -> dict[str, Any]:
    """모호성 프로브에 넘길 JSON Schema."""
    return _keyed_by_id(
        "questions",
        {"single_answer": {"type": "boolean"}, "reason": {"type": "string"}},
        question_count=question_count,
    )


def verify(content: dict[str, Any], *, config: Config) -> None:
    """각 문제에 `verify`를 채운다. `content`를 제자리에서 고친다.

    **예외를 던지지 않는다.** 검증 호출이 전부 실패해도 문제는 `unverified`로 남고 생성된
    콘텐츠는 살아 있다. 여기서 멈추면 이미 지불한 생성 비용이 버려지고, 사람이 검수할
    대상 자체가 사라진다 (#11이 "경고 후 진행"을 기본으로 두는 것과 같은 판단이다).
    """
    questions = content["questions"]
    runs = int(config.get("llm.verifier.runs"))
    provider = provider_for_role("verifier", config=config)

    replies = _blind_answers(provider, questions, runs=runs)
    ambiguity = _ambiguity_probe(provider, questions)

    for question in questions:
        question["verify"] = _judge(
            question, replies=replies, verdict=ambiguity.get(question["id"])
        )

    tally = _tally(questions)
    LOGGER.info(
        "검증 완료 — %s (재답변 %d/%d회 성공, 모호성 프로브 %s)",
        ", ".join(f"{status} {count}" for status, count in tally.items()),
        len(replies),
        runs,
        "성공" if ambiguity else "실패",
    )


# --- 호출 -------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """재답변 1건."""

    answer: str
    certainty: float
    basis: str


@dataclass(frozen=True)
class Verdict:
    """모호성 프로브 판정 1건."""

    single_answer: bool
    reason: str


def _blind_answers(
    provider: LLMProvider, questions: Sequence[dict[str, Any]], *, runs: int
) -> list[dict[int, Answer]]:
    """정답을 감춘 채 재답변을 `runs`회 받는다. 성공한 호출의 결과만 돌려준다.

    **실패한 호출을 재시도 횟수에 얹지 않는다.** `RetryingProvider`가 이미 `llm.max_retries`
    만큼 다시 불렀으므로, 여기서 또 부르면 같은 실패에 대해 대기 시간이 곱절이 된다.
    남은 회차는 그대로 진행하고 분모(`len(replies)`)가 줄어든다.
    """
    completed: list[dict[int, Answer]] = []
    for attempt in range(1, runs + 1):
        try:
            result = provider.complete_json(
                system=ANSWER_SYSTEM,
                prompt=build_answer_prompt(questions),
                schema=answer_json_schema(question_count=len(questions)),
            )
        except LLMError as error:
            LOGGER.warning("블라인드 재답변 %d/%d 실패 — %s", attempt, runs, error)
            continue

        completed.append(
            {
                entry["id"]: Answer(
                    answer=str(entry.get("answer", "")),
                    certainty=_clamp(entry.get("certainty")),
                    basis=str(entry.get("basis", "")),
                )
                for entry in _entries(result.data, "answers")
            }
        )
    return completed


def _ambiguity_probe(
    provider: LLMProvider, questions: Sequence[dict[str, Any]]
) -> dict[int, Verdict]:
    """질문이 답을 하나로 확정하는지 묻는다. 실패하면 빈 dict — 판단 근거가 없다는 뜻이다."""
    try:
        result = provider.complete_json(
            system=AMBIGUITY_SYSTEM,
            prompt=build_ambiguity_prompt(questions),
            schema=ambiguity_json_schema(question_count=len(questions)),
        )
    except LLMError as error:
        LOGGER.warning("모호성 프로브 실패 — %s", error)
        return {}

    return {
        entry["id"]: Verdict(
            single_answer=bool(entry.get("single_answer", True)),
            reason=str(entry.get("reason", "")),
        )
        for entry in _entries(result.data, "questions")
    }


# --- 판정 -------------------------------------------------------------------


def _judge(
    question: dict[str, Any],
    *,
    replies: Sequence[dict[int, Answer]],
    verdict: Verdict | None,
) -> dict[str, Any]:
    """문제 하나의 `verify` 값을 만든다.

    판정 순서가 곧 우선순위다. 모호성은 정답 일치와 **독립적으로** `flagged`를 만든다 —
    모호한 질문은 재답변이 전부 일치해도 모호하다 (스파이크 4.2).
    """
    given = [run.get(question["id"]) for run in replies]
    answered = [reply for reply in given if reply is not None]
    matched = [reply for reply in answered if agrees(question["answer"], reply.answer)]
    confidence = _confidence(matched=len(matched), replies=len(replies), answered=answered)

    mismatch = len(matched) < len(replies)
    if verdict is not None and not verdict.single_answer:
        return _entry(FLAGGED, confidence, _ambiguous_source(verdict, given, mismatch=mismatch))
    if not replies or verdict is None:
        return _entry(UNVERIFIED, confidence, _incomplete_source(replies, verdict))
    if mismatch:
        return _entry(FLAGGED, confidence, _mismatch_source(given, matched=len(matched)))
    return _entry(VERIFIED, confidence, _best_basis(matched))


def _confidence(*, matched: int, replies: int, answered: Sequence[Answer]) -> float:
    """`일치율 × 평균 자기 확신도`.

    성질 세 가지가 이 곱을 고른 이유다.

    - **`runs: 2`에서도 연속값이다.** 일치율은 `{0, 0.5, 1}`뿐이지만 확신도가 연속이므로
      곱도 연속이다. `llm.verifier.confidence_threshold`(#11)를 0.8에서 0.7로 내리는 것이
      실제로 다른 문제 집합을 통과시킨다.
    - **0.5를 넘으려면 전원 일치가 필요하다.** 확신도는 1을 넘지 못하므로 일치율 0.5인
      문제의 상한이 0.5다. 임계값을 0.5 위에 두는 한 "불일치가 통과한다"는 일이 없다.
    - **분모는 성공한 호출 수다.** 답을 받지 못한 문제(모델이 그 id를 빠뜨렸다)는 확신도
      평균에서 빠지지만 일치율의 분모에는 남아 불일치로 센다. 빠뜨린 답을 "모르겠다"가
      아니라 "확인되지 않음"으로 세는 쪽이 안전하다.
    """
    if not replies:
        return 0.0
    agreement = matched / replies
    certainty = fmean(reply.certainty for reply in answered) if answered else 0.0
    # 소수점 셋째 자리에서 끊는다. 산출물은 사람이 읽는 파일이고, 0.7000000000000001이
    # 임계값 비교에서 의미를 갖는 정밀도가 아니다.
    return round(agreement * certainty, 3)


def _entry(status: str, confidence: float, source: str | None) -> dict[str, Any]:
    """`verify` 한 건. `source`는 내용이 있을 때만 넣는다.

    스키마의 `source`는 선택이지만 **빈 문자열은 허용하지 않는다**. 근거를 대지 못한 검증에
    `""`를 남기면 "근거가 없음"과 "근거 칸이 있음"이 구분되지 않는다.
    """
    entry: dict[str, Any] = {"status": status, "confidence": confidence}
    if source:
        entry["source"] = source[:SOURCE_MAX_LEN]
    return entry


def _best_basis(matched: Sequence[Answer]) -> str | None:
    """일치한 재답변 중 확신도가 가장 높은 것의 근거."""
    with_basis = [reply for reply in matched if reply.basis.strip()]
    if not with_basis:
        return None
    return max(with_basis, key=lambda reply: reply.certainty).basis.strip()


def _mismatch_source(given: Sequence[Answer | None], *, matched: int) -> str:
    return (
        f"재답변 불일치 — {len(given)}회 중 {matched}회 일치. "
        f"재답변: {_quoted(given)}"
    )


def _ambiguous_source(
    verdict: Verdict, given: Sequence[Answer | None], *, mismatch: bool
) -> str:
    reason = verdict.reason.strip() or "질문이 답을 하나로 확정하지 못한다"
    tail = f" / 재답변: {_quoted(given)}" if mismatch else ""
    return f"질문 모호 — {reason}{tail}"


def _incomplete_source(
    replies: Sequence[dict[int, Answer]], verdict: Verdict | None
) -> str:
    missing = [
        label
        for label, absent in (("블라인드 재답변", not replies), ("모호성 프로브", verdict is None))
        if absent
    ]
    return f"검증 미완료 — {' · '.join(missing)} 호출이 실패했다"


def _tally(questions: Sequence[dict[str, Any]]) -> dict[str, int]:
    """상태별 문제 수. 선언 순서를 따라 0인 항목도 남긴다 — run.log에서 자리가 고정된다."""
    return {
        status: sum(1 for item in questions if item["verify"]["status"] == status)
        for status in VERIFY_STATUSES
    }


# --- 정답 대조 ---------------------------------------------------------------

_PARENTHESIZED = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|【[^【】]*】")
"""괄호 병기. `"세종대왕(조선 제4대 왕)"`과 `"세종대왕"`은 같은 답이다."""

_NOISE = re.compile(r"[\s(){}\[\]<>·,.:;!?'\"`~\-—–/\\]+")

_DIGITS = re.compile(r"\d+")

MIN_CONTAINMENT_LEN = 2
"""포함 관계로 판정할 최소 길이.

한 글자짜리 답에 포함 규칙을 적용하면 `"6"`이 `"16"`에 포함돼 일치가 된다. 짧은 답은
정확히 같을 때만 일치로 본다.
"""


def normalize(text: str) -> str:
    """대조용 정규화. 표기 차이(공백·괄호 병기·문장부호·전각)를 흡수한다.

    NFKC를 먼저 돌린다 — 전각 괄호와 첨자(`H₂O`)가 ASCII로 접혀야 뒤의 규칙이 걸린다.
    """
    folded = unicodedata.normalize("NFKC", text or "").lower()
    return _NOISE.sub("", _PARENTHESIZED.sub("", folded))


def agrees(expected: str, given: str) -> bool:
    """재답변이 원래 정답과 같은 답인가.

    **한쪽이 다른 쪽을 포함하면 일치로 본다.** `"세종대왕(조선 제4대 왕)"`과 `"세종대왕"`,
    `"파리"`와 `"프랑스 파리"`처럼 범위만 다른 표기를 오답으로 세면 검증이 표기 검사가
    된다 (스파이크 2.3). 대신 `MIN_CONTAINMENT_LEN` 미만은 정확 일치만 받는다.
    """
    left, right = normalize(expected), normalize(given)
    if not left or not right:
        return False
    if min(len(left), len(right)) < MIN_CONTAINMENT_LEN:
        return left == right
    if _DIGITS.fullmatch(left) and _DIGITS.fullmatch(right):
        # 수에는 범위 병기가 없다. 짧은 쪽은 줄여 쓴 표기가 아니라 다른 수다 —
        # `"234"`가 `"1234"`에 포함된다고 같은 답으로 세면 연도·개수 문제가 무력해진다.
        # 한쪽에만 단위나 조사가 붙은 경우(`"1592년"` / `"1592"`)는 아래 포함 규칙이 받는다.
        return left == right
    return left in right or right in left


# --- 헬퍼 -------------------------------------------------------------------


def _numbered(questions: Sequence[dict[str, Any]]) -> str:
    """`id. 질문` 목록. **`answer`·`explanation`을 꺼내지 않는다.**"""
    return "\n".join(f"{question['id']}. {question['question']}" for question in questions)


def _keyed_by_id(
    key: str, fields: dict[str, dict[str, Any]], *, question_count: int
) -> dict[str, Any]:
    """`{key: [{id, ...fields}]}` 모양의 JSON Schema.

    `id`를 받는 이유는 응답을 문제에 다시 붙이기 위해서다. 배열 순서로 맞추면 모델이 한
    항목을 빠뜨렸을 때 그 뒤가 전부 다른 문제의 답으로 붙는다.
    """
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, **fields},
                    "required": ["id", *fields],
                    "additionalProperties": False,
                },
                "minItems": question_count,
                "maxItems": question_count,
            }
        },
        "required": [key],
        "additionalProperties": False,
    }


def _entries(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """응답에서 항목 목록을 꺼낸다. `id`가 정수가 아닌 항목은 버린다.

    스키마 강제는 provider의 책임이지만(`LLMProvider.complete_json`), 여기서 형태를 믿고
    바로 인덱싱하면 계약을 어기는 adapter 하나가 검증 단계 전체를 예외로 무너뜨린다.
    검증은 실패해도 콘텐츠를 살려야 하는 단계다.
    """
    entries = data.get(key)
    if not isinstance(entries, list):
        LOGGER.warning("검증 응답에 %s 목록이 없다: %r", key, data)
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), int)
    ]


def _clamp(value: Any) -> float:
    """확신도를 0~1로 자른다. 스키마 밖의 값이 `confidence`를 범위 밖으로 밀지 않는다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _quoted(given: Sequence[Answer | None]) -> str:
    return ", ".join(
        repr(reply.answer) if reply is not None else "(응답 없음)" for reply in given
    )
