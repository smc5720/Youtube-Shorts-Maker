"""스모크가 열 run 디렉터리를 하나 만든다 (이슈 #26).

**손으로 쓴 `project.json`을 두지 않는다.** 앱이 여는 것은 파이프라인이 낸 파일이고,
고정된 사본을 두면 스키마가 바뀌었을 때 스모크만 조용히 통과한다. 그래서 초기 상태는
`project.build`가 만든다 — 계약을 아는 코드가 하나뿐이어야 한다는 것과 같은 이유다.

렌더 산출물은 만들지 않는다. #26의 앱은 `project.json`만 읽고, FFmpeg를 쓰는 경로(프리뷰
#27·렌더 #30)는 그 이슈들이 자기 대역을 가져온다.

사용법:
    python app/smoke/make_run.py --out DIR   → 만든 run 디렉터리 경로를 한 줄로 출력한다
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from shorts_maker import project  # noqa: E402
from shorts_maker.config import load_config  # noqa: E402
from shorts_maker.run_context import write_artifact  # noqa: E402
from shorts_maker.schemas.project import PROJECT_SCHEMA  # noqa: E402
from shorts_maker.schemas.scenes import SCENES_SCHEMA  # noqa: E402

SCENES: dict[str, Any] = {
    "schema_version": 1,
    "type": "quiz",
    # 고정 길이 장면만 쓴다 — 낭독이 있는 장면은 실측 오디오를 요구하고(PRD 7.5.1), 이
    # 스모크가 확인하려는 것은 파일 왕복이지 타임라인이 아니다.
    "scenes": [
        {"role": "hook", "text": "이 문제 맞힐 수 있나", "duration": 2.5},
        {"role": "countdown", "duration": 3.0, "seconds": 3},
    ],
}


def build(out: Path) -> Path:
    run_dir = out / "run-smoke"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_artifact(run_dir, SCENES_SCHEMA.name, SCENES)
    # `search_from`을 주지 않으면 저장소의 config.yaml을 읽어 실행 위치에 좌우된다.
    content = project.build(SCENES, config=load_config(search_from=run_dir), run_dir=run_dir)
    write_artifact(run_dir, PROJECT_SCHEMA.name, content)
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="run 디렉터리를 만들 위치")
    print(build(parser.parse_args().out))


if __name__ == "__main__":
    main()
