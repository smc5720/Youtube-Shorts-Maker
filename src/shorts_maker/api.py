"""앱 백엔드 — stdin/stdout JSON Lines 디스패처 (PRD 11장·14.1, 이슈 #26).

**HTTP 서버가 아니다.** 앱(Electron main)이 이 모듈을 자식 프로세스로 띄우고 파이프로만
말한다 — 포트도, 로컬 인증도, 서버 수명 관리도 없다. 스파이크 #25가 세 전송(stdio / 로컬
HTTP / 파일 교환)을 재서 stdio를 골랐고, 결정적인 축은 지연이 아니라 **수명**이었다:
부모를 강제 종료했을 때 HTTP·파일 백엔드는 살아남았고 stdio 백엔드만 함께 죽었다
(스파이크 4.2). stdin이 닫히면 `serve()`의 for 루프가 끝난다.

프로토콜은 **한 줄 = JSON 하나**이고 종류는 셋이다.

    {"id": 3, "method": "open", "params": {...}}       요청  (앱 → 백엔드)
    {"id": 3, "result": {...}}                          응답  (백엔드 → 앱)
    {"id": 3, "error": {"code": "schema", ...}}         실패  (백엔드 → 앱)
    {"event": "ready", "pid": 1234, "protocol": 1}      알림  (백엔드 → 앱, 언제든)

- **오류는 문자열이 아니라 `code` + `message` + `details`다.** 앱이 "왜 안 되는지"를
  화면에 그릴 때 원인 종류에 따라 다른 것을 보여준다 — 스키마 위반은 필드별 목록을
  펼치고(D2 확정 스펙 4장의 `danger`), 파일 없음은 다른 디렉터리를 고르라고 한다.
  종류를 문자열 매칭으로 알아내게 하면 메시지를 다듬는 순간 앱이 깨진다.
- **UTF-8을 명시한다.** Windows에서 파이프 stdio의 기본 인코딩은 콘솔 코드페이지
  (한국어 Windows는 cp949)라, 한글이 든 응답이 cp949로 나가고 UTF-8로 읽는 Node 쪽에서
  **에러 없이 값만 깨진다** (스파이크 4.3). `serve()`가 첫 줄을 쓰기 전에 재설정한다.
- **이 모듈은 타입을 모른다.** `project.json`·`scenes.json`만 지나므로 퀴즈 스펙 1.1의
  경계가 앱 경로에서도 그대로다.

`open`/`save` 외의 메서드(프리뷰·렌더·진행률)는 #27·#30이 붙인다. 오래 걸리는 메서드가
생기면 그것만 스레드로 보내고 이 루프는 비워 둔다 — 렌더 중에도 다른 요청을 받는 구조가
프로토타입에서 확인됐다 (스파이크 7장).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, TextIO

from .run_context import serialize_artifact
from .schemas import SchemaError, load_project, validate_project
from .schemas.project import PROJECT_SCHEMA

PROTOCOL_VERSION = 1
"""프로토콜 형식 버전. `ready` 이벤트에 실어 보낸다.

동결 배포(PyInstaller onedir, 스파이크 5.1)에서는 앱과 백엔드가 각각 갱신될 수 있어
**서로 다른 세대가 만날 수 있다.** 그때 "왜 응답이 이상한가"를 앱이 첫 줄에서 알 수 있게
한다.
"""

REQUIRED_TOOLS = ("ffmpeg", "ffprobe")
"""PATH에 있어야 하는 외부 실행 파일.

**동봉하지 않는다** (스파이크 5.2 — 전체 빌드 462MB에 빌드마다 다른 라이선스 조건).
CLI는 렌더 단계에서 없다는 사실을 말하며 멈추지만, 앱은 렌더에 도달하기 한참 전에
알려 줘야 한다. `env`가 그 자리다.
"""


class ApiError(Exception):
    """앱에 그대로 전달되는 실패. `code`가 앱의 분기 기준이다."""

    def __init__(self, code: str, message: str, details: Iterable[str] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = list(details)

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


# --- 저장 -----------------------------------------------------------------------


def write_atomically(path: Path, content: str) -> None:
    """같은 디렉터리의 임시 파일에 쓰고 교체한다.

    **이미 있는 파일을 고쳐 쓰는 유일한 경로다.** 앱이 저장하는 동안 디스크가 차거나
    프로세스가 죽으면, 열어 둔 파일을 그대로 쓰는 방식은 원본을 반쯤 쓰인 상태로 남긴다 —
    사용자가 편집한 프로젝트가 아니라 **아무도 열 수 없는 파일**이 된다.

    같은 디렉터리에 임시 파일을 두는 것은 `os.replace`가 볼륨을 넘지 못하기 때문이고
    (run 디렉터리가 다른 드라이브일 수 있다), 교체 전 `fsync`는 내용이 디스크에 닿기 전에
    디렉터리 엔트리만 바뀌는 것을 막는다.
    """
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ApiError("io", f"저장하지 못했다: {path} — {error}") from error


# --- 메서드 ---------------------------------------------------------------------


def method_ping(params: dict[str, Any]) -> Any:
    """받은 것을 그대로 돌려준다. 앱이 백엔드 왕복을 확인하는 데 쓴다."""
    return params


def method_env(params: dict[str, Any]) -> Any:
    """실행 환경 — 외부 도구가 PATH에 있는지 (스파이크 5.2).

    앱은 첫 화면에서 이것을 묻고, 없으면 프로젝트를 열기 전에 안내한다. **여는 것 자체는
    막지 않는다** — `project.json`을 읽고 고치는 데 FFmpeg가 필요 없고, 실제로 필요한
    시점(프리뷰 #27, 렌더 #30)에 다시 걸린다.
    """
    return {
        "protocol": PROTOCOL_VERSION,
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        "tools": {
            name: {"found": (found := shutil.which(name)) is not None, "path": found}
            for name in REQUIRED_TOOLS
        },
    }


def method_open(params: dict[str, Any]) -> Any:
    """run 디렉터리를 프로젝트로 연다 (PRD 7.10).

    **경로 해석을 백엔드가 한다.** 앱은 사용자가 고른 디렉터리를 그대로 넘기고,
    `project.json`이 어느 이름인지·어디 있는지는 스키마가 정한다. 이 규칙이 없으면
    `scenes.json`을 여는 #27이 같은 판단을 한 번 더 하게 된다.

    Raises:
        ApiError: 디렉터리나 `project.json`이 없을 때(`not_found`), 계약을 어겼을 때(`schema`).
    """
    run_dir = _run_dir(params)
    path = run_dir / PROJECT_SCHEMA.name
    if not path.is_file():
        raise ApiError(
            "not_found",
            f"이 디렉터리에는 {PROJECT_SCHEMA.name}이 없다: {run_dir}",
            ["쇼츠를 생성한 run 디렉터리를 고른다 (기본 위치: outputs/run-*)"],
        )

    return {
        "run_dir": str(run_dir),
        "project_path": str(path),
        "project": _load(path),
    }


def method_save(params: dict[str, Any]) -> Any:
    """편집한 프로젝트를 `project.json`에 쓴다.

    **검증이 쓰기보다 먼저다.** 계약을 어긴 상태를 파일에 남기면 그 run은 앱으로도 CLI로도
    열 수 없게 되고, 사용자가 잃는 것은 방금 한 편집이 아니라 프로젝트 전체다. 검증에
    걸리면 원본은 손대지 않은 채로 남는다.
    """
    run_dir = _run_dir(params)
    project = params.get("project")
    if not isinstance(project, dict):
        raise ApiError("bad_request", "params.project는 project.json 내용(매핑)이어야 한다")

    try:
        validate_project(project)
    except SchemaError as error:
        raise ApiError(
            "schema",
            f"{PROJECT_SCHEMA.name}의 계약을 어겨 저장하지 않았다. 원본은 그대로다.",
            error.messages,
        ) from error

    path = run_dir / PROJECT_SCHEMA.name
    write_atomically(path, serialize_artifact(project))
    return {"project_path": str(path), "bytes": path.stat().st_size}


METHODS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "ping": method_ping,
    "env": method_env,
    "open": method_open,
    "save": method_save,
}


def _run_dir(params: dict[str, Any]) -> Path:
    value = params.get("run_dir")
    if not isinstance(value, str) or not value.strip():
        raise ApiError("bad_request", "params.run_dir에 run 디렉터리 경로가 필요하다")
    path = Path(value).expanduser()
    # 앱이 화면에 띄우고 다시 넘기는 값이므로 절대 경로로 통일한다. 상대 경로를 그대로
    # 돌려주면 백엔드의 작업 디렉터리(앱이 정한다)에 따라 다른 곳을 가리킨다.
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ApiError("not_found", f"디렉터리가 없다: {resolved}")
    return resolved


def _load(path: Path) -> dict[str, Any]:
    """`project.json`을 읽고 검증한다. 실패는 앱이 그릴 수 있는 형태로 바꾼다."""
    try:
        return load_project(path)
    except SchemaError as error:
        # `messages`를 그대로 넘긴다 — 각 항목이 필드 경로로 시작하므로(`render.fps: ...`)
        # 앱이 목록으로 펼치면 사용자가 어느 줄을 고쳐야 하는지 바로 안다.
        raise ApiError("schema", f"{path.name}을 열 수 없다", error.messages) from error


# --- 디스패치 -------------------------------------------------------------------


def handle(request: Any) -> dict[str, Any]:
    """요청 하나를 처리해 응답 하나를 만든다.

    **예외를 밖으로 내지 않는다.** 백엔드가 요청 하나 때문에 죽으면 앱은 열려 있고 저장은
    불가능한 상태가 된다 — 사용자에게는 그것이 가장 나쁜 실패다. 알 수 없는 예외도
    `internal`로 실어 보내고 루프는 계속 돈다.
    """
    if not isinstance(request, dict):
        return _failure(None, ApiError("bad_request", "요청은 JSON 객체 한 줄이어야 한다"))

    identifier = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return _failure(identifier, ApiError("bad_request", "params는 매핑이어야 한다"))

    handler = METHODS.get(method) if isinstance(method, str) else None
    if handler is None:
        return _failure(
            identifier,
            ApiError(
                "unknown_method",
                f"알 수 없는 method다: {method!r}",
                [f"쓸 수 있는 method: {', '.join(sorted(METHODS))}"],
            ),
        )

    try:
        return {"id": identifier, "result": handler(params)}
    except ApiError as error:
        return _failure(identifier, error)
    except Exception as error:  # noqa: BLE001 - 무엇이든 앱에 전달하고 루프는 유지한다
        return _failure(identifier, ApiError("internal", f"{type(error).__name__}: {error}"))


def _failure(identifier: Any, error: ApiError) -> dict[str, Any]:
    return {"id": identifier, "error": error.payload()}


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    """stdin의 줄을 받아 stdout으로 답한다. stdin이 닫히면 끝난다.

    앱이 사라지면 파이프가 EOF가 되고 이 함수가 돌아온다 — **고아 프로세스를 막는 것이
    별도의 수명 관리 코드가 아니라 이 for 문이다** (스파이크 4.2).
    """
    if stdin is None or stdout is None:
        _use_utf8()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    _emit(stdout, {"event": "ready", "pid": os.getpid(), "protocol": PROTOCOL_VERSION})
    for response in respond(stdin):
        _emit(stdout, response)


def respond(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """요청 줄들 → 응답들. 파일이나 목록으로도 돌릴 수 있게 stdio와 분리한다."""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            yield _failure(None, ApiError("bad_request", f"JSON 문법 오류 — {error}"))
            continue
        yield handle(request)


def _use_utf8() -> None:
    """파이프 stdio를 UTF-8로 고정한다 (스파이크 4.3).

    **첫 줄을 쓰기 전에 불러야 한다.** 기본값은 콘솔 코드페이지라 한글이 든 응답이 조용히
    깨지고, 깨진 뒤에는 앱 쪽에서 원인이 드러나지 않는다.
    """
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdin.reconfigure(encoding="utf-8")


def _emit(stream: TextIO, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
