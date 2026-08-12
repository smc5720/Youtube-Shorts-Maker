"""프로토타입 백엔드 — stdin/stdout JSON Lines로 말하는 Python 프로세스.

Electron main이 이 파일을 자식 프로세스로 띄우고, 렌더러(React)는 preload가 열어 둔
`window.api.call()`로만 부른다. **프로토콜은 한 줄 = JSON 하나**이고 종류는 셋이다.

    {"id": 3, "method": "...", "params": {...}}   요청 (main → 백엔드)
    {"id": 3, "result": ...}                       응답 (백엔드 → main)
    {"event": "progress", "job": 3, ...}           진행 보고 (백엔드 → main, 언제든)

**출하 코드를 다시 구현하지 않는다.** 장면·프로젝트는 `shorts_maker.schemas`로 읽고,
프리뷰와 최종 렌더는 `video_renderer.build_command`가 만든 같은 명령에서 갈라진다 —
프리뷰가 다른 코드로 그리면 앱이 보여준 화면과 최종 결과가 갈릴 수 있고, 그것이 이
스파이크가 확인하려는 지점이다 (PRD 7.9).

이 파일은 **검증용이며 그대로 제품 코드로 쓰지 않는다** (#25 범위). 특히 아래 두 곳이
제품에서는 `video_renderer` 안으로 들어가야 한다.

  - `preview_command()`의 명령 리스트 수술 — 렌더러가 프리뷰 진입점을 갖는 편이 맞다
  - `-progress pipe:1` 파싱 — 진행률 보고는 렌더러의 계약이지 앱의 계약이 아니다
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[4]
sys.path.insert(0, str(REPO_ROOT / "src"))

from shorts_maker import video_renderer  # noqa: E402
from shorts_maker.schemas import load_project, load_scenes  # noqa: E402

# **Windows에서 이 두 줄이 없으면 프로토콜이 깨진다.** 파이프로 연결된 stdio의 기본 인코딩이
# 콘솔 코드페이지(한국어 Windows는 cp949)라, 장면 텍스트에 한글이 있는 응답이 cp949로 나가고
# UTF-8로 읽는 Node 쪽에서 깨진다. 에러도 나지 않고 값만 망가지는 종류의 고장이다.
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
sys.stdin.reconfigure(encoding="utf-8")

WRITE_LOCK = threading.Lock()
JOBS: dict[int, subprocess.Popen] = {}
"""진행 중인 렌더. `cancel`이 여기서 프로세스를 찾아 죽인다."""


def send(payload: dict[str, Any]) -> None:
    """한 줄 = JSON 하나. 렌더 스레드와 메인 루프가 같이 쓰므로 잠근다."""
    with WRITE_LOCK:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


# --- 메서드 ------------------------------------------------------------------


def method_ping(params: dict) -> Any:
    return params


def method_open(params: dict) -> Any:
    """run 디렉터리를 열어 장면 목록을 만든다. 앱의 '프로젝트 열기'가 하는 일이다."""
    run_dir = Path(params["run_dir"])
    project = load_project(run_dir / "project.json")
    scenes = load_scenes(run_dir / str(project["scenes"]), finalized=True)
    timeline = video_renderer.align(scenes, fps=int(project["render"]["fps"]))

    listed = []
    for index, scene in enumerate(scenes["scenes"]):
        start, end = timeline.span(index)
        listed.append(
            {
                "index": index,
                "role": scene["role"],
                "text": scene.get("text", ""),
                "narrate": bool(scene.get("narrate")),
                "start": round(start, 3),
                "end": round(end, 3),
            }
        )
    return {
        "run_dir": str(run_dir),
        "type": project["type"],
        "background": project["background"],
        "render": project["render"],
        "total_sec": round(timeline.total_sec, 3),
        "total_frames": timeline.total_frames,
        "scenes": listed,
    }


def preview_command(run_dir: Path, at_sec: float, destination: Path) -> list[str]:
    """최종 렌더 명령에서 **프레임 하나짜리 PNG** 명령을 만든다.

    같은 `build_command` 결과에서 갈라지는 것이 요점이다 — 배경·오버레이·좌표가 최종과
    같은 필터 그래프를 지나므로 프리뷰가 최종과 다른 그림을 낼 수 없다.
    """
    project = load_project(run_dir / "project.json")
    scenes = load_scenes(run_dir / str(project["scenes"]), finalized=True)
    timeline = video_renderer.align(scenes, fps=int(project["render"]["fps"]))
    command = video_renderer.build_command(
        project,
        run_dir=run_dir,
        total_sec=timeline.total_sec,
        overlays=video_renderer.build_overlays(project, scenes, timeline=timeline),
        audio=video_renderer.build_audio(project, scenes, timeline=timeline),
    )

    # 여기부터가 스파이크 전용 수술이다. 제품에서는 렌더러가 프리뷰 진입점을 갖는다.
    #
    # **오디오 체인을 필터 그래프에서 함께 들어내야 한다.** `-map [audio]`만 빼면 `alimiter`의
    # 출력이 연결되지 않은 채 남아 ffmpeg가 그래프 바인딩에서 실패한다
    # ("Filter 'alimiter:default' has output 1 (audio) unconnected"). 그래프의 첫 조각이
    # 영상이고 나머지가 오디오라는 것은 `build_command`가 그렇게 잇기 때문이다.
    # 입력은 그대로 둔다 — 매핑되지 않은 입력은 ffmpeg가 조용히 무시한다.
    trimmed: list[str] = []
    skip = 0
    for index, token in enumerate(command):
        if skip:
            skip -= 1
            continue
        if token == "-filter_complex":
            trimmed += ["-filter_complex", command[index + 1].split(";")[0]]
            skip = 1
            continue
        if token == "-map" and command[index + 1] == "[audio]":
            skip = 1
            continue
        # 영상 인코더 설정도 뺀다. `-c:v libx264`를 남기면 확장자가 .png인 파일에 H.264
        # 비트스트림이 그대로 쓰인다 — ffmpeg는 경고 없이 성공한다.
        if token in ("-c:a", "-b:a", "-t", "-movflags", "-c:v", "-crf", "-pix_fmt", "-r"):
            skip = 1
            continue
        trimmed.append(token)
    output = trimmed.pop()  # final_short.mp4
    assert output.endswith(".mp4")
    return [*trimmed, "-ss", f"{at_sec:.3f}", "-frames:v", "1", "-update", "1", str(destination)]


def method_preview(params: dict) -> Any:
    """장면 하나의 대표 프레임을 PNG로 만들어 base64로 돌려준다."""
    run_dir = Path(params["run_dir"])
    at_sec = float(params["at_sec"])
    destination = run_dir / "preview.png"
    command = preview_command(run_dir, at_sec, destination)

    start = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    elapsed = time.perf_counter() - start
    if completed.returncode != 0:
        raise RuntimeError(f"프리뷰 실패: {completed.stderr.strip()[:400]}")
    return {
        "png_base64": base64.b64encode(destination.read_bytes()).decode("ascii"),
        "bytes": destination.stat().st_size,
        "elapsed_ms": round(elapsed * 1000, 1),
    }


def method_render(params: dict, job: int) -> Any:
    """최종 렌더. `-progress`를 읽어 진행률을 흘리고, 도중에 `cancel`을 받을 수 있다.

    **프리뷰와 같은 프로세스에서 돌지만 메인 루프를 막지 않는다** — 이 함수는 스레드에서
    돌고, 그동안에도 백엔드는 다른 요청을 받는다 (PRD 7.9의 프리뷰/최종 분리).
    """
    run_dir = Path(params["run_dir"])
    project = load_project(run_dir / "project.json")
    scenes = load_scenes(run_dir / str(project["scenes"]), finalized=True)
    timeline = video_renderer.align(scenes, fps=int(project["render"]["fps"]))
    command = video_renderer.build_command(
        project,
        run_dir=run_dir,
        total_sec=timeline.total_sec,
        overlays=video_renderer.build_overlays(project, scenes, timeline=timeline),
        audio=video_renderer.build_audio(project, scenes, timeline=timeline),
    )
    command = [*command[:1], "-progress", "pipe:1", "-nostats", *command[1:]]

    start = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,  # ffmpeg는 stdin을 조작 입력으로 읽는다. 백엔드의 stdin을
        # 물려주면 프로토콜 줄을 ffmpeg가 먹는다.
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    JOBS[job] = process
    total = timeline.total_frames
    assert process.stdout
    for line in process.stdout:
        key, _, value = line.strip().partition("=")
        if key == "frame":
            send(
                {
                    "event": "progress",
                    "job": job,
                    "frame": int(value),
                    "total": total,
                    "ratio": round(min(1.0, int(value) / total), 4),
                    "at_ms": round((time.perf_counter() - start) * 1000, 1),
                }
            )
    code = process.wait()
    JOBS.pop(job, None)
    elapsed = time.perf_counter() - start
    if code != 0:
        raise RuntimeError(f"렌더 실패 (종료 코드 {code})")
    output = run_dir / str(project["render"]["output"])
    return {
        "output": str(output),
        "bytes": output.stat().st_size,
        "frames": total,
        "elapsed_ms": round(elapsed * 1000, 1),
    }


def method_cancel(params: dict) -> Any:
    process = JOBS.get(int(params["job"]))
    if process is None:
        return {"cancelled": False}
    process.terminate()
    return {"cancelled": True}


METHODS = {
    "ping": method_ping,
    "open": method_open,
    "preview": method_preview,
    "cancel": method_cancel,
}
THREADED = {"render": method_render}
"""오래 걸리는 메서드. 스레드로 보내 메인 루프를 비워 둔다."""


def dispatch(request: dict) -> None:
    identifier = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    try:
        if method in THREADED:
            def run() -> None:
                try:
                    send({"id": identifier, "result": THREADED[method](params, identifier)})
                except Exception as error:  # noqa: BLE001 - 프로토타입은 무엇이든 보고한다
                    send({"id": identifier, "error": f"{type(error).__name__}: {error}"})

            threading.Thread(target=run, daemon=True).start()
            return
        if method not in METHODS:
            send({"id": identifier, "error": f"알 수 없는 method: {method!r}"})
            return
        send({"id": identifier, "result": METHODS[method](params)})
    except Exception as error:  # noqa: BLE001
        send({"id": identifier, "error": f"{type(error).__name__}: {error}"})


def main() -> None:
    send({"event": "ready", "pid": __import__("os").getpid()})
    for line in sys.stdin:  # stdin이 닫히면(=Electron이 죽으면) 여기서 끝난다
        line = line.strip()
        if line:
            dispatch(json.loads(line))


if __name__ == "__main__":
    main()
