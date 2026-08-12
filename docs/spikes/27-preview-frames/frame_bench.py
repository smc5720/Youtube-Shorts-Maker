"""#27 프리뷰 프레임 생성 방식 실측 — 장면별 프로세스 vs 배치 한 프로세스.

**제품 코드를 그대로 부른다.** `video_renderer.preview`가 만드는 명령을 재지 않으면 여기서
나온 숫자가 앱이 실제로 내는 지연과 다른 것을 재게 된다. 벤치가 자기 명령을 만들면 그
명령만 빨라도 통과한다.

입력은 스파이크 #25가 남긴 **실제 파이프라인 산출물**이다 (11장면 / 27.9초 / 837프레임).
장면 수와 총 길이가 비용을 정하므로 손으로 만든 장면 목록으로는 의미 있는 값이 나오지 않는다.

사용법:
    .venv/Scripts/python.exe docs/spikes/27-preview-frames/frame_bench.py
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from shorts_maker import video_renderer  # noqa: E402

SAMPLE_ROOT = REPO_ROOT / "docs/spikes/25-app-framework/sample-run"
RUN = next(iter(sorted(SAMPLE_ROOT.glob("run-*"))), SAMPLE_ROOT / "run-없음")
RESULTS = HERE / "preview-results.json"
REPEATS = 3


def median_ms(samples: list[float]) -> float:
    return round(statistics.median(samples), 1)


def timed(call) -> tuple[float, object]:
    started = time.perf_counter()
    value = call()
    return (time.perf_counter() - started) * 1000, value


def main() -> None:
    project = json.loads((RUN / "project.json").read_text(encoding="utf-8"))
    scenes = json.loads((RUN / "scenes.json").read_text(encoding="utf-8"))
    count = len(scenes["scenes"])
    timeline = video_renderer.align(scenes, fps=project["render"]["fps"])
    numbers = video_renderer.representative_frames(timeline)

    work = Path(tempfile.mkdtemp(prefix="preview-bench-"))
    measurements: dict[str, list[float]] = {}
    digests: dict[str, dict[int, int]] = {}

    def record(name: str, elapsed: float) -> None:
        measurements.setdefault(name, []).append(elapsed)

    try:
        for attempt in range(REPEATS):
            # A. 장면마다 프로세스 하나 — 스파이크 #25가 기각한 구조의 실제 비용
            per_scene: list[float] = []
            sizes: dict[int, int] = {}
            for index in range(count):
                out = work / f"a{attempt}-{index}"
                elapsed, frames = timed(
                    lambda i=index, o=out: video_renderer.preview(
                        project, scenes, run_dir=RUN, out_dir=o, indices=[i]
                    )
                )
                per_scene.append(elapsed)
                sizes[index] = frames[index].stat().st_size
            record("장면별 프로세스 · 합계", sum(per_scene))
            record("장면별 프로세스 · 장면 하나", statistics.median(per_scene))
            digests["장면별"] = sizes

            # B. 한 프로세스가 전 장면
            out = work / f"b{attempt}"
            elapsed, frames = timed(
                lambda o=out: video_renderer.preview(project, scenes, run_dir=RUN, out_dir=o)
            )
            record("배치 한 프로세스 · 전 장면", elapsed)
            record("배치 한 프로세스 · 장면당", elapsed / count)
            digests["배치"] = {index: path.stat().st_size for index, path in frames.items()}

            # C. 부분 요청 — **비용은 마지막 요청 장면의 위치가 정한다**
            for label, index in (("첫 장면 하나", 0), ("마지막 장면 하나", count - 1)):
                out = work / f"c{attempt}-{index}"
                elapsed, _ = timed(
                    lambda i=index, o=out: video_renderer.preview(
                        project, scenes, run_dir=RUN, out_dir=o, indices=[i]
                    )
                )
                record(f"부분 요청 · {label}", elapsed)

            # D. 바닥 — 아무 일도 하지 않는 FFmpeg 기동
            elapsed, _ = timed(
                lambda: subprocess.run(["ffmpeg", "-version"], capture_output=True)
            )
            record("ffmpeg -version (일 없음)", elapsed)

        identical = digests["장면별"] == digests["배치"]
        payload = {
            "run": str(RUN),
            "scenes": count,
            "frames": timeline.total_frames,
            "total_sec": round(timeline.total_sec, 3),
            "representative_frames": list(numbers),
            "repeats": REPEATS,
            "median_ms": {name: median_ms(values) for name, values in measurements.items()},
            # 두 방식이 같은 그림을 내는가. 명령이 `_video_stage` 하나를 지나므로 같아야 하고,
            # 실제로 파일 크기까지 같았다.
            "frames_identical": identical,
            "frame_bytes": digests["배치"],
        }
        RESULTS.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(payload["median_ms"], ensure_ascii=False, indent=2))
        print(f"프레임 동일: {identical} → {RESULTS}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
