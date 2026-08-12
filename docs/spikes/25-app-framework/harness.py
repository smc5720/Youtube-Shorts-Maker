"""이슈 #25 연결 방식 측정 — 로컬 HTTP / 자식 프로세스 stdio / 파일 교환을 같은 작업으로 잰다.

PRD 14.2의 "Python 백엔드와 앱 프론트엔드를 어떻게 연결할지"에 숫자를 붙이는 것이 목적이다.
세 방식 모두 `worker.py`의 같은 처리부를 부르므로 차이는 전송 계층에서만 나온다.

재는 것 —

  1. `cold_start` : 프로세스를 띄우고 첫 응답을 받기까지. 앱 시작 시 사용자가 기다리는 시간
  2. `rtt_small`  : 200B 왕복 200회. 장면 선택·필드 편집처럼 잦은 호출의 비용
  3. `rtt_large`  : 1MB 왕복 20회. `scenes.json`·프리뷰 이미지처럼 큰 payload의 비용
  4. `stream`     : 진행률 100프레임. 렌더 진행 보고가 얼마나 촘촘할 수 있는가
  5. `orphan`     : 부모를 강제 종료했을 때 백엔드가 살아남는가 (하트비트 파일로 판정)

**측정 경계는 Python ↔ Python이다.** Electron 렌더러 → main → 백엔드까지의 전체 경로는
`prototype/`이 재고, 이 하니스는 그 안쪽 전송 계층만 분리해서 잰다.

사용법:
    python harness.py [--out results.json] [--iterations 200]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE / "worker.py"

SMALL_PAYLOAD = {"scene": 3, "field": "text", "value": "대한민국의 수도는?" * 4}
LARGE_CHARS = 1_000_000
STREAM_FRAMES = 100
POLL_SEC = 0.02
"""파일 모드 클라이언트의 폴링 주기. `worker.POLL_SEC`와 같은 값이다."""


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def summarize(values: list[float]) -> dict:
    return {
        "n": len(values),
        "median_ms": round(statistics.median(values) * 1000, 3),
        "p95_ms": round(percentile(values, 0.95) * 1000, 3),
        "max_ms": round(max(values) * 1000, 3),
    }


# --- 전송별 클라이언트 --------------------------------------------------------


class StdioClient:
    """자식 프로세스 + JSON Lines. Electron main의 `child_process.spawn`과 같은 모양이다."""

    name = "stdio"

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(WORKER), "--mode", "stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def call(self, method: str, params: object) -> dict:
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps({"id": 1, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        return json.loads(self.process.stdout.readline())

    def stream(self, count: int) -> list[float]:
        assert self.process.stdin and self.process.stdout
        start = time.perf_counter()
        self.process.stdin.write(
            json.dumps({"id": 1, "method": "stream", "params": {"count": count}}) + "\n"
        )
        self.process.stdin.flush()
        marks = []
        while True:
            message = json.loads(self.process.stdout.readline())
            marks.append(time.perf_counter() - start)
            if "result" in message:
                return marks

    def close(self) -> None:
        try:
            self.call("shutdown", None)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self.process.wait(timeout=5)


class HttpClient:
    """127.0.0.1 로컬 HTTP + keep-alive. PRD 11장의 `src/api.py`가 상정한 모양이다."""

    name = "http"

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(WORKER), "--mode", "http"],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert self.process.stdout
        self.port = int(self.process.stdout.readline().strip())
        self.connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)

    def call(self, method: str, params: object) -> dict:
        body = json.dumps({"id": 1, "method": method, "params": params})
        self.connection.request(
            "POST", "/rpc", body=body.encode("utf-8"), headers={"Content-Type": "application/json"}
        )
        response = self.connection.getresponse()
        return json.loads(response.read())

    def stream(self, count: int) -> list[float]:
        start = time.perf_counter()
        body = json.dumps({"id": 1, "method": "stream", "params": {"count": count}})
        self.connection.request(
            "POST", "/rpc", body=body.encode("utf-8"), headers={"Content-Type": "application/json"}
        )
        response = self.connection.getresponse()
        marks = []
        while True:
            line = response.readline()
            if not line:
                return marks
            marks.append(time.perf_counter() - start)
            if "result" in json.loads(line):
                response.read()  # 남은 청크를 비워 연결을 재사용 가능하게 둔다
                return marks

    def close(self) -> None:
        self.connection.close()
        self.process.terminate()
        self.process.wait(timeout=5)


class FileClient:
    """디렉터리에 요청/응답 파일을 두고 양쪽이 폴링한다. 프로세스 간 계약이 파일뿐인 방식."""

    name = "file"

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.counter = 0
        self.retries = 0
        """응답 파일을 읽다가 실패해 다시 시도한 횟수. 파일 모드에만 있는 비용이라 센다 —
        Windows에서 rename 중인 파일을 열면 `PermissionError`가 난다."""
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(WORKER), "--mode", "file", "--dir", str(directory)],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert self.process.stdout
        self.process.stdout.readline()  # "ready"

    def _token(self) -> str:
        self.counter += 1
        return f"{self.counter:06d}"

    def call(self, method: str, params: object) -> dict:
        token = self._token()
        request = self.directory / f"req-{token}.json"
        temporary = request.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"id": token, "method": method, "params": params}), encoding="utf-8"
        )
        temporary.replace(request)
        response = self.directory / f"res-{token}.json"
        while True:
            if response.exists():
                try:
                    payload = json.loads(response.read_text(encoding="utf-8"))
                    response.unlink()
                    return payload
                except (PermissionError, json.JSONDecodeError, OSError):
                    # 쓰는 쪽의 rename과 겹쳤다. 이 재시도가 파일 모드의 실제 비용이다.
                    self.retries += 1
            time.sleep(POLL_SEC)

    def stream(self, count: int) -> list[float]:
        token = self._token()
        start = time.perf_counter()
        request = self.directory / f"req-{token}.json"
        temporary = request.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"id": token, "method": "stream", "params": {"count": count}}),
            encoding="utf-8",
        )
        temporary.replace(request)
        seen: set[str] = set()
        marks = []
        result = self.directory / f"res-{token}.json"
        while True:
            for path in sorted(self.directory.glob(f"prog-{token}-*.json")):
                if path.name not in seen:
                    seen.add(path.name)
                    marks.append(time.perf_counter() - start)
            if result.exists():
                marks.append(time.perf_counter() - start)
                return marks
            time.sleep(POLL_SEC)

    def close(self) -> None:
        try:
            self.call("shutdown", None)
        except OSError:
            pass
        self.process.wait(timeout=5)


# --- 측정 --------------------------------------------------------------------


def measure(client, iterations: int, large_iterations: int) -> dict:
    # ASCII로 채운다. 한글은 `json.dumps`의 기본값(`ensure_ascii=True`)에서 문자당 6바이트
    # 이스케이프로 늘어나 "1MB 왕복"이라는 이름과 실제 전송량이 갈린다.
    large = "x" * LARGE_CHARS

    small = []
    for _ in range(iterations):
        start = time.perf_counter()
        client.call("echo", SMALL_PAYLOAD)
        small.append(time.perf_counter() - start)

    big = []
    for _ in range(large_iterations):
        start = time.perf_counter()
        response = client.call("echo", large)
        big.append(time.perf_counter() - start)
        assert len(response["result"]) == LARGE_CHARS

    marks = client.stream(STREAM_FRAMES)
    gaps = [b - a for a, b in zip(marks, marks[1:])]

    presets = client.call("presets", None)["result"]

    return {
        "read_retries": getattr(client, "retries", 0),
        "rtt_small": summarize(small),
        "rtt_large": summarize(big),
        "stream": {
            "frames": len(marks),
            "total_ms": round(marks[-1] * 1000, 3),
            "median_gap_ms": round(statistics.median(gaps) * 1000, 3),
            "max_gap_ms": round(max(gaps) * 1000, 3),
        },
        "presets": presets,
    }


def cold_start(factory) -> tuple[object, float]:
    """프로세스 생성부터 첫 응답까지. 앱을 켰을 때 사용자가 기다리는 값이다."""
    start = time.perf_counter()
    client = factory()
    client.call("echo", {"ping": 1})
    return client, time.perf_counter() - start


def orphan_test(mode: str, workspace: Path) -> dict:
    """부모를 강제 종료했을 때 백엔드가 살아남는가.

    중간 부모(`--parent-sim`)가 worker를 띄우고, 하니스가 그 부모만 `taskkill /F`로 죽인다
    (`/T`를 붙이지 않으므로 OS가 자식을 함께 죽이지 않는다). worker는 살아 있는 동안
    하트비트 파일을 갱신하므로, 죽인 뒤에도 파일이 갱신되면 고아가 남은 것이다.
    """
    heartbeat = workspace / f"beat-{mode}.txt"
    pid_file = workspace / f"pid-{mode}.txt"
    heartbeat.unlink(missing_ok=True)
    pid_file.unlink(missing_ok=True)
    parent = subprocess.Popen(
        [
            sys.executable, "-u", str(Path(__file__).resolve()),
            "--parent-sim", mode, "--workspace", str(workspace), "--heartbeat", str(heartbeat),
        ],
    )
    deadline = time.time() + 15
    while not heartbeat.exists() and time.time() < deadline:
        time.sleep(0.1)
    alive_before = heartbeat.exists()

    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(parent.pid)], capture_output=True)
    else:  # pragma: no cover - 개발 환경은 Windows다
        parent.kill()
    parent.wait(timeout=10)

    time.sleep(1.5)
    stamp = heartbeat.read_text(encoding="utf-8") if heartbeat.exists() else ""
    time.sleep(1.5)
    later = heartbeat.read_text(encoding="utf-8") if heartbeat.exists() else ""
    survived = bool(stamp) and stamp != later

    if survived and pid_file.exists():  # 측정이 남긴 고아를 정리한다
        subprocess.run(
            ["taskkill", "/F", "/PID", pid_file.read_text(encoding="utf-8").strip()],
            capture_output=True,
        )
    return {
        "heartbeat_started": alive_before,
        "backend_survived_parent_kill": survived,
    }


def parent_sim(mode: str, workspace: Path, heartbeat: Path) -> None:
    """worker를 띄우고 가만히 있는 중간 부모. 하니스가 이 프로세스를 죽인다."""
    command = [sys.executable, "-u", str(WORKER), "--mode", mode, "--heartbeat", str(heartbeat)]
    if mode == "file":
        command += ["--dir", str(workspace / "exchange-orphan")]
    child = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
    )
    # 하니스가 이 부모를 죽인 뒤 남은 고아를 정리할 수 있도록 자식 pid를 남긴다.
    (workspace / f"pid-{mode}.txt").write_text(str(child.pid), encoding="utf-8")
    child.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "results.json")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--large-iterations", type=int, default=20)
    parser.add_argument("--workspace", type=Path, default=HERE / ".work")
    parser.add_argument("--parent-sim", choices=("stdio", "http", "file"))
    parser.add_argument("--heartbeat", type=Path)
    arguments = parser.parse_args()

    if arguments.parent_sim:
        parent_sim(arguments.parent_sim, arguments.workspace, arguments.heartbeat)
        return

    workspace = arguments.workspace
    workspace.mkdir(parents=True, exist_ok=True)

    factories = {
        "stdio": StdioClient,
        "http": HttpClient,
        "file": lambda: FileClient(workspace / "exchange"),
    }

    results = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu": platform.processor(),
        },
        "parameters": {
            "iterations": arguments.iterations,
            "large_iterations": arguments.large_iterations,
            "large_payload_chars": LARGE_CHARS,
            "stream_frames": STREAM_FRAMES,
            "file_poll_sec": POLL_SEC,
        },
        "transports": {},
    }

    for name, factory in factories.items():
        print(f"[{name}] 측정 중...", file=sys.stderr)
        client, started = cold_start(factory)
        try:
            measured = measure(client, arguments.iterations, arguments.large_iterations)
        finally:
            client.close()
        measured["cold_start_ms"] = round(started * 1000, 3)
        measured["orphan"] = orphan_test(name, workspace)
        results["transports"][name] = measured

    results["finished_at"] = datetime.now().isoformat(timespec="seconds")
    arguments.out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"기록: {arguments.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
