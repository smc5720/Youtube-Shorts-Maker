"""이슈 #10 검증 실효성 측정 — 함정 문제 세트로 `quiz_verifier`의 검출률을 잰다.

스파이크 #1 6장의 후속 과제다. 4.2에서 블라인드 재답변이 24/24 전부 일치해 검출률이
입증도 반증도 되지 않았고, 그럼에도 검증이 문제당 비용의 59%를 차지했다 (4.4).

**하니스가 검증 로직을 다시 구현하지 않는다.** `shorts_maker.types.quiz.quiz_verifier.verify`
를 그대로 부른다. 하니스가 자기 판정 코드를 들면 측정 대상과 출하 코드가 갈라져, 여기서
나온 임계값이 실제 파이프라인에서 다른 뜻을 갖게 된다.

문제 세트는 **정답 라벨이 아니라 기대 판정 라벨**을 들고 있다.

  - `wrong`     : 정답이 틀리게 심어진 문제. 재답변이 갈려야 한다 → `verified`가 아니어야 성공
  - `ambiguous` : 정답은 방어 가능하지만 질문이 모호한 문제. 모호성 프로브가 잡아야 한다
  - `control`   : 정상 문제. `verified`가 나와야 하고, 아니면 거짓 양성이다

사용법:
    python harness.py [--out results.json] [--models opus sonnet haiku] [--repeats 2]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from shorts_maker.config import load_config  # noqa: E402
from shorts_maker.types.quiz.quiz_verifier import VERIFIED, verify  # noqa: E402

# --- 함정 문제 세트 ----------------------------------------------------------

# (라벨, 질문, quiz.json에 실릴 정답, 메모)
TRAP_SET: list[tuple[str, str, str, str]] = [
    # 정답이 틀리게 심어진 문제 — 쉬운 것부터 함정성이 있는 것까지 섞는다.
    ("wrong", "태양계에서 가장 큰 행성은?", "토성", "정답은 목성"),
    ("wrong", "대한민국 제헌 헌법이 공포된 해는?", "1945년", "정답은 1948년"),
    ("wrong", "1기압에서 물의 끓는점은 섭씨 몇 도인가?", "90도", "정답은 100도"),
    ("wrong", "훈민정음을 반포한 조선의 왕은?", "정조", "정답은 세종"),
    ("wrong", "원소기호 Fe가 가리키는 원소는?", "납", "정답은 철"),
    ("wrong", "제1차 세계대전이 끝난 해는?", "1914년", "1914는 개전 연도"),
    ("wrong", "노벨상 6개 부문 중 가장 나중에 신설된 것은?", "평화상", "정답은 경제학상(1968)"),
    ("wrong", "에베레스트를 최초로 등정한 사람은?", "조지 맬러리", "정답은 힐러리·텐징(1953)"),
    # 정답은 방어 가능하지만 질문이 모호한 문제 — 재답변으로는 원리상 안 잡힌다.
    ("ambiguous", "한국 최초의 금속활자본은?", "직지심체요절", "현존 최초 / 기록상 상정고금예문"),
    ("ambiguous", "세계에서 가장 긴 강은?", "나일강", "측정 기준에 따라 아마존강"),
    ("ambiguous", "대한민국에서 가장 높은 산은?", "한라산", "한반도 기준이면 백두산"),
    ("ambiguous", "세계 최초의 컴퓨터는?", "에니악", "ABC·콜로서스와 정의가 갈린다"),
    # 정상 문제 — 거짓 양성 측정용.
    ("control", "대한민국의 수도는?", "서울", ""),
    ("control", "물의 화학식은?", "H2O", ""),
    ("control", "훈민정음을 창제한 조선의 왕은?", "세종대왕", ""),
    ("control", "지구에서 가장 큰 대양은?", "태평양", ""),
]

BATCH_SIZE = 4
"""한 호출에 묶는 문제 수.

`quiz.question_count`의 허용 범위가 3~5이므로 실제 운영과 같은 크기로 부른다. 16문제를
한 번에 넣으면 배치 크기가 검출률에 미치는 영향이 측정에 섞인다.
"""

LABELS = ("wrong", "ambiguous", "control")

# --- 호출 기록 ---------------------------------------------------------------

_CALL_LOG = re.compile(r"^LLM 호출")


class CallRecorder(logging.Handler):
    """`RetryingProvider`가 남기는 호출 1회 로그에서 비용·지연을 줍는다.

    provider 계층을 건드리지 않고 비용을 얻는 길이다. `verify()`는 `LLMResult`를 밖으로
    돌려주지 않으므로(검증은 판정만 돌려준다) 하니스가 결과를 뜯어볼 자리가 없다.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.calls: list[dict[str, object]] = []
        self.failures: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            self.failures.append(record.getMessage())
        if not _CALL_LOG.match(str(record.msg)) or not record.args:
            return
        provider, model, cost, latency_ms, *_ = record.args
        self.calls.append(
            {
                "provider": provider,
                "model": model,
                "cost_usd": _usd(str(cost)),
                "latency_ms": latency_ms,
            }
        )


def _usd(reported: str) -> float | None:
    """`"$0.0068"` → `0.0068`. `"미보고"`는 `None`이다."""
    return float(reported[1:]) if reported.startswith("$") else None


# --- 실행 -------------------------------------------------------------------


def batches() -> list[list[dict[str, object]]]:
    """문제 세트를 운영과 같은 크기로 나눈다. 라벨이 배치마다 섞이도록 순서를 돌려 담는다.

    라벨별로 배치를 만들면 "이 배치는 전부 오답"이라는 신호가 컨텍스트에 생겨 검출률이
    부풀려진다.
    """
    ordered = sorted(range(len(TRAP_SET)), key=lambda index: (index % BATCH_SIZE, index))
    return [
        [_question(position, index) for position, index in enumerate(ordered[start:start + BATCH_SIZE], start=1)]
        for start in range(0, len(ordered), BATCH_SIZE)
    ]


def _question(position: int, index: int) -> dict[str, object]:
    label, question, answer, note = TRAP_SET[index]
    return {
        "id": position,
        "question": question,
        "answer": answer,
        "explanation": "측정용 고정 입력",
        "difficulty": "medium",
        "countdown_sec": 4,
        # 아래 두 값은 `quiz.json` 스키마에 없다. `verify()`는 문제 dict의 다른 키를 읽지
        # 않으므로 측정에 영향이 없고, 결과를 라벨과 다시 붙이는 데 쓴다.
        "_label": label,
        "_note": note,
    }


def run_config(model: str, runs: int, repeat: int) -> dict[str, object]:
    """검증 모델 하나로 전체 세트를 1회 돌린다."""
    config = load_config(overrides={"llm.verifier.model": model, "llm.verifier.runs": runs})
    recorder = CallRecorder()
    logging.getLogger("shorts_maker").addHandler(recorder)

    outcomes: list[dict[str, object]] = []
    started = time.monotonic()
    planned = batches()
    try:
        for number, batch in enumerate(planned, start=1):
            print(f"  [{model} #{repeat}] 배치 {number}/{len(planned)} ...", end=" ", flush=True)
            content = {"questions": [dict(question) for question in batch]}
            verify(content, config=config)
            for question in content["questions"]:
                outcomes.append(
                    {
                        "label": question["_label"],
                        "question": question["question"],
                        "planted_answer": question["answer"],
                        "note": question["_note"],
                        "verify": question["verify"],
                    }
                )
            print(
                " ".join(item["verify"]["status"][:4] for item in outcomes[-len(batch):]),
                flush=True,
            )
    finally:
        logging.getLogger("shorts_maker").removeHandler(recorder)

    return {
        "verifier_model": model,
        "runs": runs,
        "repeat": repeat,
        "wall_sec": round(time.monotonic() - started, 1),
        "calls": recorder.calls,
        "failures": recorder.failures,
        "cost_usd": round(sum(call["cost_usd"] or 0.0 for call in recorder.calls), 4),
        "outcomes": outcomes,
    }


# --- 집계 -------------------------------------------------------------------


def summarize(runs: list[dict[str, object]]) -> list[dict[str, object]]:
    """모델별 검출률·거짓 양성률과 라벨별 confidence 분포."""
    by_model: dict[str, list[dict[str, object]]] = defaultdict(list)
    for run in runs:
        by_model[str(run["verifier_model"])].extend(run["outcomes"])

    summary = []
    for model, outcomes in by_model.items():
        buckets = {label: [o for o in outcomes if o["label"] == label] for label in LABELS}
        summary.append(
            {
                "verifier_model": model,
                "cost_usd": round(
                    sum(float(run["cost_usd"]) for run in runs if run["verifier_model"] == model), 4
                ),
                # 검출 = `verified`가 아니다. 오답과 모호는 통과시키지 않는 것이 목적이다.
                "detection": {
                    label: _rate(bucket, lambda o: o["verify"]["status"] != VERIFIED)
                    for label, bucket in buckets.items()
                    if label != "control"
                },
                # 거짓 양성 = 정상 문제를 통과시키지 못했다.
                "false_positive": _rate(
                    buckets["control"], lambda o: o["verify"]["status"] != VERIFIED
                ),
                "confidence": {
                    label: _spread([o["verify"]["confidence"] for o in bucket])
                    for label, bucket in buckets.items()
                },
                "status_counts": _counts(outcomes),
            }
        )
    return summary


def _rate(outcomes: list[dict[str, object]], predicate) -> dict[str, object]:
    hits = sum(1 for outcome in outcomes if predicate(outcome))
    total = len(outcomes)
    return {"hits": hits, "total": total, "rate": round(hits / total, 3) if total else None}


def _spread(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "mean": None, "max": None}
    return {
        "min": min(values),
        "mean": round(sum(values) / len(values), 3),
        "max": max(values),
    }


def _counts(outcomes: list[dict[str, object]]) -> dict[str, int]:
    tally: dict[str, int] = defaultdict(int)
    for outcome in outcomes:
        tally[str(outcome["verify"]["status"])] += 1
    return dict(tally)


def main() -> int:
    # Windows 기본 콘솔은 cp949라 모델이 뱉은 문자 하나에 print가 죽는다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).parent / "results.json"))
    parser.add_argument("--models", nargs="*", default=["opus", "sonnet", "haiku"])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--runs", type=int, default=2, help="llm.verifier.runs")
    args = parser.parse_args()

    # 호출 1회 로그(비용·지연)는 INFO다. 로거를 INFO까지 열어야 `CallRecorder`가 그 기록을
    # 받고, 콘솔 핸들러만 WARNING으로 올려 진행 출력이 호출 로그에 묻히지 않게 한다.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.WARNING)

    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    results = [
        run_config(model, args.runs, repeat)
        for model in args.models
        for repeat in range(1, args.repeats + 1)
    ]

    payload = {
        "started_at": started,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "harness": "shorts_maker.types.quiz.quiz_verifier.verify (출하 코드 직접 호출)",
        "batch_size": BATCH_SIZE,
        "verifier_runs": args.runs,
        "repeats": args.repeats,
        "trap_set": [
            {"label": label, "question": question, "planted_answer": answer, "note": note}
            for label, question, answer, note in TRAP_SET
        ],
        "summary": summarize(results),
        "runs": results,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 요약 ===")
    for entry in payload["summary"]:
        print(
            f"{entry['verifier_model']:>7} | "
            f"오답 검출 {entry['detection']['wrong']['rate']} "
            f"| 모호 검출 {entry['detection']['ambiguous']['rate']} "
            f"| 거짓 양성 {entry['false_positive']['rate']} "
            f"| control confidence {entry['confidence']['control']} "
            f"| ${entry['cost_usd']}"
        )
    print(f"\n결과 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
