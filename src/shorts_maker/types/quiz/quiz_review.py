"""임계값 판정과 검수 경고 — `verify.status`를 확정한다 (퀴즈 스펙 5장, 이슈 #11).

검증기(#10)는 결함을 **찾았는지**만 판정하고 `confidence`를 산출한다. 그 값을 정책과
대조해 "사람이 봐야 한다"로 내리는 것이 여기다. 네 가지가 이 모듈의 형태를 정한다.

- **임계값은 `llm.verifier.confidence_threshold` 하나다.** `quiz` 아래에 같은 뜻의 키를
  새로 만들지 않는다. 기본값 0.8은 #10의 함정 문제 세트 측정에서 나왔다 — 의미 있는
  범위가 `(0.5, 0.98)`이고 그 가운데다 (`docs/spikes/10-verifier-detection.md` 5장).
- **임계값과 정확히 같은 값은 통과다.** `<`로 자른다. 경계 방향을 코드가 아니라 테스트가
  고정한다 — 설정 파일에 0.8을 적은 사람이 0.8짜리 문제의 운명을 예측할 수 있어야 한다.
- **`status != "verified"`도 전부 `flagged`가 된다.** `unverified`(검증 호출 실패)는 성공이
  아니라 "판단 근거가 없다"이고, 근거 없이 통과시키면 검증 단계를 둔 뜻이 없다. 원래
  사유는 검증기가 적은 `source`에 그대로 남으므로 "검증 미완료"와 "재답변 불일치"는
  경고에서 계속 구분된다.
- **두 번 불러도 결과가 같다.** 판정 사유를 먼저 `source`에 쓰고 경고 문구를 거기서
  만든다. 앱(#30)이 같은 콘텐츠에 다시 부를 때 사유가 겹쳐 쌓이지 않는다.

경고를 출력하고 종료 코드를 정하는 것은 파이프라인이다. 이 모듈은 항목만 만든다 —
`ContentIssue`가 타입 어휘를 담지 않는 이유가 그것이다 (`shorts_types`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ... import PACKAGE_LOGGER
from ...shorts_types import ContentIssue
from .quiz_verifier import FLAGGED, SOURCE_MAX_LEN, VERIFIED

if TYPE_CHECKING:
    from ...config import Config

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.quiz")

THRESHOLD_KEY = "llm.verifier.confidence_threshold"
"""#6이 이미 연 키다. 이름을 여기 다시 적는 대신 상수 하나로 두고 경고 문구도 여기서 쓴다."""

THRESHOLD_PREFIX = "임계값 미달"
"""임계값으로 내린 문제의 사유 앞머리. 이 모듈이 `source`에 쓰고 경고에서 다시 읽는다."""

UNKNOWN_CAUSE = "검증 단계가 결함을 찾았다"
"""`source` 없이 `flagged`인 문제의 사유. 검증기는 항상 사유를 적지만, `quiz.json`은
사람이 손으로 고치는 파일이라 `source`가 지워진 채 들어올 수 있다."""

MISSING_VERIFY_SOURCE = "검증 미완료 — 검증 단계가 돌지 않았다"
"""`verify`가 아예 없는 문제. 초안 그대로이거나 사람이 지운 것이며, 어느 쪽이든
"검증되지 않았다"이므로 통과시키지 않는다."""


def review(content: dict[str, Any], *, config: Config) -> list[ContentIssue]:
    """임계값을 적용해 `verify.status`를 확정하고 검수가 필요한 문제를 돌려준다.

    `content`를 제자리에서 고친다. 파이프라인이 `quiz.json`을 쓰기 전에 부르므로 산출물에
    확정된 상태가 남고, 앱과 이후 단계가 같은 판정을 본다.
    """
    threshold = float(config.get(THRESHOLD_KEY))
    questions = content["questions"]

    issues = []
    for question in questions:
        entry = question.get("verify")
        if not isinstance(entry, dict):
            entry = {"status": FLAGGED, "confidence": 0.0, "source": MISSING_VERIFY_SOURCE}
            question["verify"] = entry
        else:
            _finalize(entry, threshold=threshold)

        if entry["status"] == FLAGGED:
            issues.append(_issue(question, threshold=threshold))

    LOGGER.info(
        "검수 판정 — 임계값 %s 적용, flagged %d/%d문제",
        _number(threshold),
        len(issues),
        len(questions),
    )
    return issues


def _finalize(entry: dict[str, Any], *, threshold: float) -> None:
    """`verify` 한 건의 상태를 확정한다. 이미 `flagged`면 사유를 덮어쓰지 않는다.

    **임계값 미달을 재답변 불일치·질문 모호보다 뒤에 둔다.** 실검출은 `status`에서 나오고
    (#10 측정 4장), 임계값은 남은 `verified`를 확신도 축에서 자르는 안전망이다. 이미
    실검출로 잡힌 문제에 "임계값 미달"을 덧쓰면 더 구체적인 사유를 잃는다.
    """
    if entry["status"] == FLAGGED:
        return
    if entry["status"] == VERIFIED and entry["confidence"] >= threshold:
        return

    if entry["status"] == VERIFIED:
        entry["source"] = _threshold_source(entry, threshold=threshold)
    # `unverified`의 사유("검증 미완료 — ...")는 검증기가 적은 그대로 둔다.
    entry["status"] = FLAGGED


def _threshold_source(entry: dict[str, Any], *, threshold: float) -> str:
    """임계값으로 내렸다는 사실과 그때의 값. 검증기가 남긴 근거는 뒤에 잇는다."""
    basis = str(entry.get("source", "")).strip()
    text = (
        f"{THRESHOLD_PREFIX} — confidence {_number(entry['confidence'])} "
        f"< {THRESHOLD_KEY} {_number(threshold)}"
    )
    if basis:
        text += f" / 근거: {basis}"
    return text[:SOURCE_MAX_LEN]


def _issue(question: dict[str, Any], *, threshold: float) -> ContentIssue:
    """경고 한 줄. `confidence`와 적용된 임계값이 사유와 함께 들어간다.

    **확정된 `verify`만 보고 만든다.** 판정을 다시 하지 않으므로 두 번 불러도 같은 문구가
    나오고, 판정과 표시가 각자 조건을 들어 어긋나는 일도 없다.
    """
    entry = question["verify"]
    cause = str(entry.get("source", "")).strip() or UNKNOWN_CAUSE
    # 임계값 사유는 위에서 이 모듈이 쓴 것이라 두 값을 이미 담고 있다. 다시 붙이면 같은
    # 숫자가 한 줄에 두 번 나온다. 검증기가 쓴 사유(재답변 불일치 등)에는 없으므로 붙인다.
    if not cause.startswith(THRESHOLD_PREFIX):
        cause += (
            f" / confidence {_number(entry['confidence'])} "
            f"(임계값 {_number(threshold)})"
        )
    return ContentIssue(
        subject=f"문제 {question['id']}",
        summary=str(question["question"]),
        reason=cause,
    )


def _number(value: float) -> str:
    """`0.80` 대신 `0.8`. 설정 파일에 적힌 표기와 경고에 찍히는 표기가 같아야 한다."""
    return f"{value:g}"
