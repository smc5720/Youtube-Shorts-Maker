"""스파이크 #25 — 세 가지 연결 방식의 Python 쪽 구현. 하니스가 이 파일을 자식으로 띄운다.

세 모드가 **같은 작업**을 한다. 요청 JSON을 받아 `echo` / `stream` / `presets`를 처리하고
응답 JSON을 돌려준다. 전송 계층만 다르므로 하니스가 재는 차이는 전송 비용이다.

- `stdio` : stdin으로 한 줄 = 요청 하나(JSON Lines), stdout으로 한 줄 = 응답 하나
- `http`  : 127.0.0.1의 임의 포트에 POST /rpc. 시작하자마자 포트를 stdout 첫 줄에 알린다
- `file`  : 요청 디렉터리를 폴링해 `req-*.json`을 읽고 `res-*.json`을 쓴다

`presets`는 **실제 출하 코드**(`shorts_maker.assets`)를 부른다. 전송 계층이 파이썬 쪽
파이프라인 코드를 그대로 부를 수 있는지를 세 모드에서 같은 방식으로 확인하기 위함이다.

살아 있는 동안 `--heartbeat` 파일에 계속 시각을 쓴다. 하니스의 고아 프로세스 시험이
부모를 죽인 뒤 이 파일이 계속 갱신되는지로 생존을 판정한다.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from shorts_maker import assets  # noqa: E402

POLL_SEC = 0.02
"""파일 모드의 폴링 주기. 20ms는 실무에서 쓸 만한 하한이다 — 더 줄이면 CPU를 태운다."""

HEARTBEAT_SEC = 0.2


def handle(request: dict) -> dict:
    """전송 계층과 무관한 처리부. 세 모드가 이 함수 하나를 공유한다."""
    method = request.get("method")
    if method == "echo":
        return {"id": request.get("id"), "result": request.get("params")}
    if method == "presets":
        return {
            "id": request.get("id"),
            "result": {
                "caption_styles": sorted(assets.caption_styles()),
                "backgrounds": sorted(assets.background_presets()),
            },
        }
    return {"id": request.get("id"), "error": f"알 수 없는 method: {method!r}"}


def stream_frames(count: int) -> list[dict]:
    """진행률 스트리밍용 프레임. 렌더 진행 보고가 이 모양이다."""
    return [{"progress": i / count, "stage": "render"} for i in range(1, count + 1)]


def beat(path: Path, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SEC):
        path.write_text(f"{time.time():.6f}", encoding="utf-8")


# --- stdio -------------------------------------------------------------------


def serve_stdio() -> None:
    """stdin이 닫히면(=부모가 죽으면) 루프가 끝난다. 고아가 남지 않는 이유가 이것이다."""
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        if request.get("method") == "stream":
            for frame in stream_frames(int(request["params"]["count"])):
                out.write(json.dumps({"id": request.get("id"), "progress": frame}) + "\n")
                out.flush()
            out.write(json.dumps({"id": request.get("id"), "result": "done"}) + "\n")
        elif request.get("method") == "shutdown":
            out.write(json.dumps({"id": request.get("id"), "result": "bye"}) + "\n")
            out.flush()
            return
        else:
            out.write(json.dumps(handle(request)) + "\n")
        out.flush()


# --- http --------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive. 요청마다 TCP를 새로 열면 전송 비용이 아니라
    # 연결 비용을 재게 된다.

    def log_message(self, *args: object) -> None:  # 벤치 중 stderr 소음을 없앤다
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length))
        if request.get("method") == "stream":
            # 청크 전송으로 진행률을 흘린다 (SSE와 같은 성질).
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for frame in stream_frames(int(request["params"]["count"])):
                self._chunk(json.dumps({"id": request.get("id"), "progress": frame}) + "\n")
            self._chunk(json.dumps({"id": request.get("id"), "result": "done"}) + "\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        body = json.dumps(handle(request)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chunk(self, text: str) -> None:
        data = text.encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
        self.wfile.flush()


def serve_http() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    print(server.server_address[1], flush=True)  # 포트를 부모에게 알린다
    server.serve_forever()


# --- file --------------------------------------------------------------------


def serve_file(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    print("ready", flush=True)
    while True:
        for request_path in sorted(directory.glob("req-*.json")):
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue  # 반쯤 쓰인 파일. 다음 주기에 다시 본다 — 이것이 파일 모드의 세금이다
            request_path.unlink()
            token = request_path.name[len("req-") : -len(".json")]
            if request.get("method") == "stream":
                for index, frame in enumerate(stream_frames(int(request["params"]["count"]))):
                    (directory / f"prog-{token}-{index:04d}.json").write_text(
                        json.dumps({"progress": frame}), encoding="utf-8"
                    )
                _write_atomic(directory / f"res-{token}.json", {"result": "done"})
            elif request.get("method") == "shutdown":
                _write_atomic(directory / f"res-{token}.json", {"result": "bye"})
                return
            else:
                _write_atomic(directory / f"res-{token}.json", handle(request))
        time.sleep(POLL_SEC)


def _write_atomic(path: Path, payload: dict) -> None:
    """부분 쓰기를 읽히지 않게 임시 파일에 쓰고 rename한다. 파일 모드가 지불하는 비용."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("stdio", "http", "file"), required=True)
    parser.add_argument("--dir", type=Path, help="파일 모드의 교환 디렉터리")
    parser.add_argument("--heartbeat", type=Path, help="생존 표시 파일")
    arguments = parser.parse_args()

    stop = threading.Event()
    if arguments.heartbeat:
        threading.Thread(target=beat, args=(arguments.heartbeat, stop), daemon=True).start()

    try:
        if arguments.mode == "stdio":
            serve_stdio()
        elif arguments.mode == "http":
            serve_http()
        else:
            serve_file(arguments.dir)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
