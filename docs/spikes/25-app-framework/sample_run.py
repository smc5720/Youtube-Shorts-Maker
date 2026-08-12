"""프로토타입이 열 run 디렉터리를 하나 만든다. 네트워크로 나가지 않는다.

프로토타입이 확인하려는 것은 **앱이 실제 산출물을 다룰 수 있는가**이므로, 손으로 만든
가짜 `project.json`이 아니라 파이프라인이 낸 진짜 run 디렉터리가 필요하다. 대역은
전 구간 스모크(`tests/test_e2e_smoke.py`)와 같은 둘 — `StubLLM`과 `ToneTTS`뿐이고,
FFmpeg·스키마·타임라인·렌더는 전부 실제 코드가 지난다.

**대역을 여기서 새로 만들지 않는다.** 스모크가 쓰는 것을 그대로 import한다 — 하니스가
자기 대역을 들면 이 run이 파이프라인의 산출물이 아니라 하니스의 산출물이 된다.

사용법:
    .venv/Scripts/python.exe docs/spikes/25-app-framework/sample_run.py [--out DIR]
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from conftest import StubLLM  # noqa: E402

from shorts_maker.llm import registry as llm_registry  # noqa: E402
from shorts_maker.llm.claude_cli import PROVIDER_NAME as LLM_PROVIDER  # noqa: E402
from shorts_maker.main import main as run_pipeline  # noqa: E402
from shorts_maker.shorts_types import DEFAULT_TYPE  # noqa: E402
from shorts_maker.tts import registry as tts_registry  # noqa: E402
from shorts_maker.tts.edge_tts import PROVIDER_NAME as TTS_PROVIDER  # noqa: E402

TOPIC = "세계 지리 상식"
QUESTION_COUNT = 3


def build(output_root: Path) -> Path:
    tone = importlib.import_module("test_e2e_smoke").ToneTTS()
    llm_registry.BUILTIN_PROVIDERS[LLM_PROVIDER] = StubLLM().factory  # type: ignore[assignment]
    tts_registry.BUILTIN_PROVIDERS[TTS_PROVIDER] = tone.factory  # type: ignore[assignment]

    output_root.mkdir(parents=True, exist_ok=True)
    config = output_root / "config.yaml"
    config.write_text(
        f"quiz:\n  question_count: {QUESTION_COUNT}\n"
        f"tts:\n  cache_dir: {(output_root / 'tts-cache').as_posix()}\n",
        encoding="utf-8",
    )

    before = {path for path in output_root.iterdir() if path.is_dir()}
    exit_code = run_pipeline(
        ["--topic", TOPIC, "--type", DEFAULT_TYPE,
         "--out", str(output_root), "--config", str(config)]
    )
    created = {path for path in output_root.iterdir() if path.is_dir()} - before
    created = {path for path in created if path.name.startswith("run-")}
    if exit_code != 0 or len(created) != 1:
        raise SystemExit(f"파이프라인이 run 디렉터리를 하나 만들지 못했다 (종료 코드 {exit_code})")
    return created.pop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "sample-run")
    arguments = parser.parse_args()
    print(build(arguments.out))
