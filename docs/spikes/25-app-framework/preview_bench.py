"""프리뷰 비용 측정 — PRD 7.9의 "빠른 프리뷰와 최종 렌더링을 분리한다"에 숫자를 붙인다.

앱이 장면을 고를 때마다 프레임 하나를 그린다면 그 비용이 편집 리듬을 정한다. 재는 것은
셋이고, 전부 `prototype/backend/server.py`의 `preview_command()`가 만든 **최종 렌더와 같은
필터 그래프**로 잰다 — 다른 명령으로 재면 프리뷰와 최종이 갈릴 수 있다는 전제가 무너진다.

  1. 시각에 따른 비용    : `-ss`를 출력 쪽에 두면 0초부터 필터를 지나므로 뒤 장면일수록 비싼가
  2. 해상도에 따른 비용  : 마지막에 `scale`을 붙여 절반 크기로 그리면 얼마나 싸지는가
  3. 최종 렌더 한 번     : 위 값들을 견줄 기준선
  4. 고정비             : `ffmpeg -version`과 배경만 1프레임. **이 둘이 프리뷰 비용의 바닥이다** —
                          필터를 아무리 줄여도 그 아래로 내려가지 않는다

사용법:
    .venv/Scripts/python.exe docs/spikes/25-app-framework/preview_bench.py [--repeats 3]
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE / "prototype" / "backend"))

import server  # noqa: E402 - 프로토타입 백엔드의 프리뷰 명령을 그대로 쓴다

from shorts_maker import video_renderer  # noqa: E402
from shorts_maker.schemas import load_project, load_scenes  # noqa: E402


def run(command: list[str]) -> float:
    start = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise SystemExit(f"ffmpeg 실패: {completed.stderr.strip()[:400]}")
    return time.perf_counter() - start


def scaled(command: list[str], width: int) -> list[str]:
    """필터 그래프 끝에 `scale`을 붙인다. 오버레이 좌표는 원본 캔버스 기준이므로 마지막이어야 한다."""
    output = list(command)
    index = output.index("-filter_complex")
    graph = output[index + 1]
    output[index + 1] = graph.replace("[video]", f",scale={width}:-2[video]", 1)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, default=HERE / "preview-results.json")
    arguments = parser.parse_args()

    run_dir = arguments.run_dir
    if run_dir is None:
        candidates = sorted((HERE / "sample-run").glob("run-*"))
        if not candidates:
            raise SystemExit("sample-run이 없다. sample_run.py를 먼저 돌린다")
        run_dir = candidates[-1]

    project = load_project(run_dir / "project.json")
    scenes = load_scenes(run_dir / str(project["scenes"]), finalized=True)
    timeline = video_renderer.align(scenes, fps=int(project["render"]["fps"]))
    destination = run_dir / "preview-bench.png"

    def median_ms(command: list[str]) -> float:
        samples = [run(command) for _ in range(arguments.repeats)]
        return round(statistics.median(samples) * 1000, 1)

    by_time = {}
    for at in (0.5, timeline.total_sec / 2, timeline.total_sec - 0.5):
        command = server.preview_command(run_dir, at, destination)
        by_time[f"{at:.2f}s"] = median_ms(command)

    middle = timeline.total_sec / 2
    base = server.preview_command(run_dir, middle, destination)
    by_scale = {
        "1080x1920": by_time[f"{middle:.2f}s"],
        "540x960": median_ms(scaled(base, 540)),
        "270x480": median_ms(scaled(base, 270)),
    }

    # 고정비 — 프로세스 기동과 배경 한 장. 오버레이가 없어도 지불하는 값이다.
    fixed = {
        "ffmpeg_version_ms": median_ms(["ffmpeg", "-version"]),
        "background_only_frame_ms": median_ms(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", "0.04",
             "-i", f"color=c=0x101A33:s={project['render']['width']}x{project['render']['height']}:r=30",
             "-frames:v", "1", "-update", "1", str(run_dir / "preview-bench-bg.png")]
        ),
    }
    (run_dir / "preview-bench-bg.png").unlink(missing_ok=True)

    # 입력측 seek — 같은 그림이 나오는지까지 확인한다. `-copyts`가 없으면 필터가 보는 `t`가
    # 0부터 다시 시작해 `enable` 구간이 통째로 어긋난다.
    late = timeline.total_sec - 0.5
    output_seek = server.preview_command(run_dir, late, destination)
    run(output_seek)  # 비교 대상이 될 그림을 다시 만든다 — 위 측정들이 같은 파일을 덮어썼다
    position = output_seek.index("-ss")
    without_seek = output_seek[:position] + output_seek[position + 2:]
    input_seek = [*without_seek[:4], "-ss", f"{late:.3f}", "-copyts", *without_seek[4:]]
    input_seek[-1] = str(run_dir / "preview-bench-input.png")
    seek = {
        "output_side_ms": by_time[f"{late:.2f}s"],
        "input_side_ms": median_ms(input_seek),
        "same_image": (
            (run_dir / "preview-bench-input.png").read_bytes() == destination.read_bytes()
        ),
    }
    (run_dir / "preview-bench-input.png").unlink(missing_ok=True)

    # 기준선 — 최종 렌더 1회. 프리뷰가 이것의 몇 분의 일인지가 결론을 정한다.
    final_command = video_renderer.build_command(
        project,
        run_dir=run_dir,
        total_sec=timeline.total_sec,
        overlays=video_renderer.build_overlays(project, scenes, timeline=timeline),
        audio=video_renderer.build_audio(project, scenes, timeline=timeline),
    )
    final_ms = round(run(final_command) * 1000, 1)

    results = {
        "measured_at": datetime.now().isoformat(timespec="seconds"),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "run_dir": str(run_dir),
        "video": {
            "total_sec": round(timeline.total_sec, 3),
            "total_frames": timeline.total_frames,
            "canvas": f"{project['render']['width']}x{project['render']['height']}",
            "scenes": len(scenes["scenes"]),
        },
        "repeats": arguments.repeats,
        "preview_ms_by_timestamp": by_time,
        "preview_ms_by_scale": by_scale,
        "fixed_cost": fixed,
        "seek_side": seek,
        "final_render_ms": final_ms,
    }
    arguments.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    destination.unlink(missing_ok=True)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
