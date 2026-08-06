"""이슈 #1 스파이크 하니스 — LLM provider 후보 비교.

로컬 `claude` CLI를 헤드리스(`-p`)로 호출해 후보 모델별로 다음을 측정한다.

  1. 상식 퀴즈 4문제 생성 (동일 프롬프트, N회 반복)
  2. 구조화 출력(JSON) 성공/실패
  3. 블라인드 재답변 검증 — 정답을 감추고 질문만 다시 물어 독립 답변 2회
  4. 비용(total_cost_usd)과 소요 시간(API/wall)

프로젝트 컨텍스트(CLAUDE.md·스킬·툴·MCP)를 전부 벗겨낸 상태로 호출한다.
그렇게 하지 않으면 사소한 질문 하나에도 캐시 생성/읽기 토큰이 2만 개 넘게 실려
후보 간 비용 비교가 무의미해진다.

사용법:
    python harness.py [--out results.json] [--gen-runs 3]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --- 후보 -------------------------------------------------------------------

CANDIDATES = ["opus", "sonnet", "haiku"]

# --- 프롬프트 ---------------------------------------------------------------

GEN_SYSTEM = (
    "너는 한국어 상식 퀴즈 출제기다. "
    "쇼츠 영상용으로 짧고 명확한 주관식 문제를 만든다. "
    "정답은 논란의 여지가 없는 단일 값이어야 한다. "
    "JSON 외에는 아무것도 출력하지 않는다."
)

GEN_PROMPT = (
    "한국인 일반 시청자 대상 상식 퀴즈 4문제를 만들어라.\n"
    "- 주제는 서로 겹치지 않게 하라.\n"
    "- 난이도는 easy → medium → hard 순으로 배치하라 (easy 2, medium 1, hard 1).\n"
    "- 질문은 40자 이내, 정답은 20자 이내로 하라.\n"
    "- 정답이 여러 개로 갈릴 수 있는 문제는 내지 마라.\n"
)

# quiz.json 콘텐츠 구조 (docs/types/quiz.md 3.1). verify 필드는 quiz_verifier가 채우므로 제외.
GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "cta": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                    "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
                },
                "required": ["id", "question", "answer", "explanation", "difficulty"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["hook", "cta", "questions"],
    "additionalProperties": False,
}

# 블라인드 검증: 생성된 정답을 보여주지 않는다. 질문만 주고 독립 답변을 받는다.
# "이 정답이 맞습니까?"라고 물으면 같은 모델이 자기 출력에 동조해 검증 효과가 없다 (CLAUDE.md).
VERIFY_SYSTEM = (
    "너는 한국어 상식 문제에 답하는 응답기다. "
    "질문에 대한 정답만 간결하게 답한다. 모르면 answer를 빈 문자열로 둔다. "
    "JSON 외에는 아무것도 출력하지 않는다."
)

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["answer", "confidence"],
    "additionalProperties": False,
}

VERIFY_RUNS_PER_QUESTION = 2  # 이슈 #1: 블라인드 검증 2회 독립 호출

# --- CLI 호출 ---------------------------------------------------------------

CLAUDE = shutil.which("claude")


@dataclass
class Call:
    """claude CLI 호출 1회의 결과."""

    model: str
    kind: str  # "generate" | "verify"
    ok: bool
    schema_ok: bool  # structured_output이 스키마대로 나왔는가
    cost_usd: float
    api_ms: int  # CLI가 보고한 API 소요 시간
    wall_ms: int  # 프로세스 시작~종료 (CLI 기동 오버헤드 포함)
    output: dict | None = None
    error: str = ""


def call_claude(model: str, kind: str, system: str, prompt: str, schema: dict) -> Call:
    """프로젝트 컨텍스트를 배제한 상태로 claude CLI를 1회 호출한다."""
    cmd = [
        CLAUDE, "-p", prompt,
        "--model", model,
        # 아래 6개가 컨텍스트 격리의 핵심이다. 하나라도 빠지면 CLAUDE.md·스킬 목록·
        # 툴 정의가 프롬프트에 실려 비용이 한 자릿수 배로 뛴다.
        "--safe-mode",
        "--tools", "",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--no-session-persistence",
        "--system-prompt", system,
        "--json-schema", json.dumps(schema, ensure_ascii=False),
        "--output-format", "json",
    ]

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=600
        )
    except subprocess.TimeoutExpired:
        return Call(model, kind, False, False, 0.0, 0, int((time.monotonic() - t0) * 1000),
                    error="timeout")
    wall_ms = int((time.monotonic() - t0) * 1000)

    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return Call(model, kind, False, False, 0.0, 0, wall_ms,
                    error=f"CLI 응답이 JSON이 아님: {proc.stdout[:200]}")

    ok = not env.get("is_error", True) and env.get("subtype") == "success"
    cost = float(env.get("total_cost_usd") or 0.0)
    api_ms = int(env.get("duration_ms") or 0)

    # --json-schema를 쓰면 CLI가 검증까지 마친 값을 structured_output에 넣어준다.
    # 이 필드가 비어 있으면 모델이 스키마를 못 맞춘 것이다.
    out = env.get("structured_output")
    schema_ok = isinstance(out, dict)

    return Call(model, kind, ok, schema_ok, cost, api_ms, wall_ms,
                output=out if schema_ok else None,
                error="" if ok else str(env.get("api_error_status") or env.get("result", ""))[:200])


# --- 정답 대조 ---------------------------------------------------------------

_PUNCT = re.compile(r"[\s(){}\[\]<>·,.:;!?'\"`~\-—/\\]+")


def normalize(s: str) -> str:
    """대조용 정규화. 괄호 병기·공백·문장부호 차이를 흡수한다."""
    return _PUNCT.sub("", (s or "").strip().lower())


def agrees(generated: str, verified: str) -> bool:
    """블라인드 답변이 생성된 정답과 일치하는가.

    한쪽이 다른 쪽을 포함하면 일치로 본다. "세종대왕(세종)" vs "세종"처럼
    표기 범위만 다른 경우를 오답으로 세지 않기 위함이다.
    최종 판정은 사람이 raw 문자열을 보고 확정한다.
    """
    a, b = normalize(generated), normalize(verified)
    if not a or not b:
        return False
    return a in b or b in a


# --- 실행 -------------------------------------------------------------------

@dataclass
class CandidateResult:
    model: str
    gen_calls: list[Call] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    verify: list[dict] = field(default_factory=list)


def run_candidate(model: str, gen_runs: int) -> CandidateResult:
    res = CandidateResult(model=model)

    print(f"\n=== {model} ===", flush=True)

    # 1. 생성 — 동일 프롬프트로 gen_runs회. 구조화 출력 실패율 표본.
    for i in range(gen_runs):
        print(f"  [생성 {i + 1}/{gen_runs}] ...", end=" ", flush=True)
        c = call_claude(model, "generate", GEN_SYSTEM, GEN_PROMPT, GEN_SCHEMA)
        res.gen_calls.append(c)
        print(f"ok={c.ok} schema={c.schema_ok} ${c.cost_usd:.4f} {c.api_ms}ms", flush=True)

    # 검증에는 첫 번째 성공한 생성 결과를 쓴다.
    first_ok = next((c for c in res.gen_calls if c.schema_ok and c.output), None)
    if not first_ok:
        print("  생성이 전부 실패 — 검증 생략", flush=True)
        return res
    res.questions = first_ok.output.get("questions", [])

    # 2. 블라인드 검증 — 정답을 감추고 질문만 재질의, 문제당 2회 독립 호출.
    for q in res.questions:
        record = {"id": q.get("id"), "question": q.get("question"),
                  "generated_answer": q.get("answer"), "runs": []}
        for r in range(VERIFY_RUNS_PER_QUESTION):
            c = call_claude(model, "verify", VERIFY_SYSTEM, q.get("question", ""), VERIFY_SCHEMA)
            ans = (c.output or {}).get("answer", "")
            conf = (c.output or {}).get("confidence", 0.0)
            record["runs"].append({
                "answer": ans, "confidence": conf,
                "agrees": agrees(q.get("answer", ""), ans),
                "cost_usd": c.cost_usd, "api_ms": c.api_ms, "wall_ms": c.wall_ms,
                "ok": c.ok, "schema_ok": c.schema_ok, "error": c.error,
            })
        hits = sum(1 for r in record["runs"] if r["agrees"])
        print(f"  [검증] Q{record['id']} 일치 {hits}/{VERIFY_RUNS_PER_QUESTION} "
              f"| 생성정답={record['generated_answer']!r} "
              f"| 재답변={[r['answer'] for r in record['runs']]!r}", flush=True)
        record["agree_count"] = hits
        res.verify.append(record)

    return res


def main() -> int:
    # Windows 기본 콘솔은 cp949라 모델이 뱉은 em dash 하나에도 print가 죽는다.
    # 측정과 무관한 로깅 계층 때문에 실행 전체를 잃지 않도록 강제로 UTF-8을 쓴다.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    if not CLAUDE:
        print("claude CLI를 찾을 수 없다.", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "results.json"))
    ap.add_argument("--gen-runs", type=int, default=3)
    ap.add_argument("--models", nargs="*", default=CANDIDATES)
    args = ap.parse_args()

    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    results = [asdict(run_candidate(m, args.gen_runs)) for m in args.models]

    payload = {
        "started_at": started,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "harness": "claude CLI headless (-p), 프로젝트 컨텍스트 격리",
        "verify_runs_per_question": VERIFY_RUNS_PER_QUESTION,
        "gen_runs": args.gen_runs,
        "candidates": results,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
