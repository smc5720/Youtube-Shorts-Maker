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
- **이 모듈은 타입을 모른다.** 사람이 고치는 콘텐츠 산출물(`content` / `save_content`, #28)도
  지나지만 파일명과 검증은 **레지스트리에서 온다** — 여기에 `quiz.json`을 적거나 `load_quiz`를
  import하면 `tests/test_type_boundary.py`가 잡는다. 퀴즈 스펙 1.1의 경계가 앱 경로에서도
  그대로다.

- **오래 걸리는 메서드는 스레드로 나간다** (`BACKGROUND_METHODS`, #27). 프리뷰 한 번이 이
  머신에서 2~3초이고, 그동안 루프를 잡고 있으면 앱은 저장도 다른 장면 선택도 하지 못한다.
  응답은 `id`로 짝을 찾으므로 순서가 뒤바뀌어도 앱이 헷갈리지 않는다 (스파이크 7장).
- **최종 렌더는 응답 하나로 끝나지 않는다** (`render`, #30). 진행 상황이 요청과 짝이 없는
  알림으로 나가고, 그래서 메서드가 `notify`를 받는다 — 그 알림에도 요청 `id`를 실어 보내는
  것은 재시도한 뒤 이전 렌더의 늦은 알림이 화면을 거꾸로 돌리지 않게 하기 위함이다.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, TextIO

from . import overlay, video_renderer
from .assets import AssetError, background_presets, caption_styles
from .run_context import serialize_artifact
from .schemas import SchemaError, load_project, validate_project
from .schemas import project as project_schema
from .schemas.core import Schema
from .schemas.project import PREVIEW_BLIND_SECTIONS, PROJECT_SCHEMA
from .schemas.scenes import SCENES_SCHEMA, load_scenes
from .shorts_types import ShortsTypeError, get_type
from .video_renderer import RenderError

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


Notifier = Callable[..., None]
"""요청과 짝이 없는 알림을 보내는 함수 — `notify("render_progress", frame=1, ...)` (#30).

`serve`가 만들어 넘기고, 그 구현이 `event` 이름과 요청 `id`를 함께 실어 한 줄로 내보낸다.
알림을 쓰지 않는 메서드는 이 인자를 받지 않는다 (`_plain`).
"""

Handler = Callable[[dict[str, Any], Notifier], Any]


def _plain(handler: Callable[[dict[str, Any]], Any]) -> Handler:
    """알림을 쓰지 않는 메서드를 공통 서명에 맞춘다.

    **여덟 메서드에 쓰지 않는 인자를 달지 않기 위한 것이다.** 알림이 필요한 것은 지금 `render`
    하나뿐이고(#30), 그 하나 때문에 나머지가 `notify`를 받아 무시하면 "이 메서드도 알림을
    낼 수 있다"로 읽힌다.
    """

    def wrapped(params: dict[str, Any], notify: Notifier) -> Any:
        return handler(params)

    return wrapped


class ApiError(Exception):
    """앱에 그대로 전달되는 실패. `code`가 앱의 분기 기준이다."""

    def __init__(
        self,
        code: str,
        message: str,
        details: Iterable[str] = (),
        *,
        raw: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = list(details)
        self.raw = raw
        """기계가 낸 원문 (#30). 앱이 `mono`로 그리고 사용자가 그대로 복사한다.

        **`details`와 다르다.** 그쪽은 사람이 읽는 줄의 목록(위반한 필드, 다음에 할 일)이고
        이쪽은 ffmpeg stderr처럼 손대지 않아야 하는 덩어리다 (D2 확정 스펙 3.3).
        """

    def payload(self) -> dict[str, Any]:
        body = {"code": self.code, "message": self.message, "details": self.details}
        # **없으면 칸도 없다.** 빈 문자열을 실어 보내면 앱이 빈 `mono` 상자를 그린다.
        return body if not self.raw else {**body, "raw": self.raw}


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


def method_presets(params: dict[str, Any]) -> Any:
    """앱이 배경·자막 스타일로 **고를 수 있는 것들** (#38, 이슈 #79·#80).

    번들 프리셋 둘과 사용자 파일의 지원 형식이 함께 온다. 셋 다 "앱이 적어 두면 안 되는
    목록"이라는 같은 이유로 여기를 지난다 — 프리셋 이름은 `assets/`가, 형식 목록은 렌더러가
    소유한다 (PRD 14.1).

    **이름을 앱에 적지 않게 하는 것이 이 메서드의 존재 이유다.** 프리셋은 `assets/`가
    소유하고(D1 확정 스펙 6장) 앱이 `assets/`를 직접 읽을 수는 없다 — 동결 배포(PyInstaller
    onedir)에서 `assets/`는 백엔드 실행 파일 옆이라 앱에서 본 경로와 다르다 (스파이크 5.1).
    이름을 앱에도 적으면 프리셋을 하나 더할 때 고칠 곳이 둘이 되고, 그때 갈리는 것은 화면에
    보이는 목록과 렌더가 아는 목록이다.

    색까지 함께 준다. 앱이 스타일·배경 견본을 그리므로 색이 필요한데, 그것을 앱 CSS에 적으면
    같은 표가 두 번째로 생긴다 (`assets.py` 머리말).

    `caption_styles[].background`는 그 스타일의 **기본 짝**이고 조합을 막는 값이 아니다 —
    9조합 전부 사용 가능하다 (D1 확정 스펙 6.3). 앱이 스타일을 고를 때 배경을 함께 바꾸는
    근거가 이 필드다.

    Raises:
        ApiError: 프리셋 파일이 없거나 계약을 어겼을 때(`assets`). 동결 배포에서 `assets/`가
            실행 파일 옆에 없는 것이 실제 실패 경로다.
    """
    try:
        styles = caption_styles()
        backgrounds = background_presets()
    except AssetError as error:
        raise ApiError(
            "assets",
            f"번들 프리셋을 읽을 수 없다 — {error}",
            ["동결 배포에서는 assets/가 백엔드 실행 파일 옆에 있어야 한다"],
        ) from error

    return {
        # **파일 정의 순서 그대로다.** 앱이 다시 정렬하면 목록 순서가 두 곳에서 정해진다.
        "caption_styles": [
            {
                "name": style.name,
                "label": style.label,
                "background": style.background,
                "colors": dict(style.colors),
            }
            for style in styles.values()
        ],
        "backgrounds": [
            # `stops`가 1개면 단색, 2개면 위→아래 2스톱 그라디언트다 (확정 스펙 6.2).
            # 종류를 따로 싣지 않는 것은 스톱 수와 어긋날 수 있는 값을 만들지 않기 위함이다.
            {"name": preset.name, "label": preset.label, "stops": list(preset.stops)}
            for preset in backgrounds.values()
        ],
        # 배경으로 받는 사용자 파일 (#80). **확장자가 `kind`를 정하므로 둘을 함께 보낸다** —
        # 확장자 목록만 보내면 어느 것이 `image`이고 어느 것이 `video`인지를 앱이 다시
        # 정하게 되고, 그 판단이 두 곳에 생긴다 (PRD 14.1).
        "background_files": [
            {"extension": extension, "kind": kind}
            for extension, kind in video_renderer.BACKGROUND_FILE_KINDS.items()
        ],
        # 텍스트 오버레이가 고를 수 있는 것들 (#83). **배경 형식 목록과 같은 이유로 여기를
        # 지난다** — 소유자는 `assets/`가 아니라 스키마(`schemas/project.py`)와 렌더러지만,
        # 앱이 적어 두면 안 되는 목록이라는 점이 같다. 특히 웨이트는 앱이 시안대로 400·600을
        # 적으면 **화면은 정상이고 렌더에서만** `AssetError`로 멈춘다 (확정 스펙 7.1-2).
        "overlay": {
            "positions": list(project_schema.OVERLAY_POSITIONS),
            # 색 이름과 그것이 가리키는 프리셋 역할을 **한 표로 함께 보낸다** —
            # 확장자와 `kind`를 함께 보내는 것과 같은 판단이다. 앱은 이 역할로 위의
            # `caption_styles[].colors`에서 견본 색을 찾고, 값 자체는 들지 않는다.
            "colors": [
                {"name": name, "role": role}
                for name, role in overlay.OVERLAY_COLOR_ROLES.items()
            ],
            "sizes": list(project_schema.OVERLAY_SIZES),
            "weights": list(project_schema.OVERLAY_WEIGHTS),
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


def method_scenes(params: dict[str, Any]) -> Any:
    """확정 `scenes.json`을 읽어 그대로 넘긴다 (PRD 7.4.1, 이슈 #27).

    **`open`과 나눠 둔다.** 장면 목록을 읽지 못하는 것이 프로젝트를 열지 못할 이유는 아니다 —
    `project.json`의 값은 여전히 보이고 고칠 수 있어야 한다. 앱은 둘을 따로 부르고 실패도
    따로 그린다.

    확정 상태를 요구하는 이유는 이 목록이 곧 프리뷰와 렌더의 입력이기 때문이다. 길이가 없는
    초안을 목록에 그리면 총 길이도 장면 경계도 화면과 결과가 갈린다.
    """
    run_dir = _run_dir(params)
    path = run_dir / SCENES_SCHEMA.name
    if not path.is_file():
        raise ApiError(
            "not_found",
            f"이 디렉터리에는 {SCENES_SCHEMA.name}이 없다: {run_dir}",
            ["쇼츠를 생성한 run 디렉터리를 고른다 (기본 위치: outputs/run-*)"],
        )
    try:
        scenes = load_scenes(path, finalized=True)
    except SchemaError as error:
        raise ApiError("schema", f"{path.name}을 열 수 없다", error.messages) from error

    return {"scenes_path": str(path), "scenes": scenes}


def method_content(params: dict[str, Any]) -> Any:
    """타입 전용 콘텐츠 산출물을 읽어 그대로 넘긴다 (퀴즈는 `quiz.json`, 이슈 #28).

    **파일명도 검증도 레지스트리에서 온다.** 이 모듈은 `types/` 밖이라 타입 전용 스키마를
    import할 수도 파일명을 적을 수도 없다(`tests/test_type_boundary.py`). `params.type`은
    `project.json`이 들고 있던 값이고, 앱은 그것을 그대로 돌려보낸다.

    **`open`과 나눠 둔다.** 콘텐츠를 읽지 못하는 것이 프로젝트를 열지 못할 이유는 아니다 —
    장면 목록과 프리뷰는 `scenes.json`만 있으면 되고, 문제 편집만 닫히면 된다.
    """
    run_dir = _run_dir(params)
    schema = _content_schema(params)
    path = run_dir / schema.name
    if not path.is_file():
        raise ApiError(
            "not_found",
            f"이 디렉터리에는 {schema.name}이 없다: {run_dir}",
            ["쇼츠를 생성한 run 디렉터리를 고른다 (기본 위치: outputs/run-*)"],
        )
    try:
        content = schema.load(path)
    except SchemaError as error:
        raise ApiError("schema", f"{path.name}을 열 수 없다", error.messages) from error

    return {"content_path": str(path), "content": content}


def method_save_content(params: dict[str, Any]) -> Any:
    """사람이 고친 콘텐츠를 콘텐츠 산출물에 쓴다 (#28).

    `save`와 같은 규칙이다 — **검증이 쓰기보다 먼저이고**, 걸리면 원본은 손대지 않은 채로
    남는다. 이 파일은 사람이 검수하는 원본이라(퀴즈 스펙 3.1) 반쯤 쓰인 상태로 남는 것이
    편집 하나를 잃는 것보다 훨씬 나쁘다.

    **검증기가 채운 필드를 이 경로가 지우지 못하게 하는 장치는 없다.** 앱이 콘텐츠를 통째로
    받아 통째로 돌려주기 때문이고, 사람 확인 기록을 `project.json`으로 보낸 이유가 그것이다
    (D2 확정 스펙 1.4).
    """
    run_dir = _run_dir(params)
    schema = _content_schema(params)
    content = params.get("content")
    if not isinstance(content, dict):
        raise ApiError("bad_request", f"params.content는 {schema.name} 내용(매핑)이어야 한다")

    try:
        schema.validate(content)
    except SchemaError as error:
        raise ApiError(
            "schema",
            f"{schema.name}의 계약을 어겨 저장하지 않았다. 원본은 그대로다.",
            error.messages,
        ) from error

    path = run_dir / schema.name
    write_atomically(path, serialize_artifact(content))
    return {"content_path": str(path), "bytes": path.stat().st_size}


def _content_schema(params: dict[str, Any]) -> Schema:
    """`params.type`이 가리키는 타입의 콘텐츠 계약.

    등록되지 않은 타입은 `bad_request`다 — 백엔드가 고장 난 것이 아니라 앱이 이 백엔드가
    모르는 타입의 프로젝트를 열었다는 뜻이고(동결 배포에서 세대가 갈릴 수 있다), 앱은 그때
    편집 폼을 닫고 나머지 화면을 그대로 둔다.
    """
    name = params.get("type")
    if not isinstance(name, str) or not name.strip():
        raise ApiError("bad_request", "params.type에 쇼츠 타입 이름이 필요하다")
    try:
        return get_type(name).content_schema
    except ShortsTypeError as error:
        raise ApiError("bad_request", str(error)) from error


def method_preview(params: dict[str, Any]) -> Any:
    """장면 하나의 대표 프레임을 PNG로 돌려준다 (PRD 7.9, 이슈 #27).

    **최종 렌더 경로를 지나지 않는다.** 명령은 `video_renderer.build_preview_command`가 만들고
    거기에는 인코더도 오디오도 출력 mp4도 없다. 두 명령이 같은 그림을 내는 것은 배경·오버레이를
    `_video_stage` 하나에서 함께 받기 때문이다.

    **프로젝트를 앱에서 받는다.** 파일을 다시 읽으면 저장하지 않은 편집이 프리뷰에 보이지
    않고, 그러면 프리뷰가 편집 도구가 되지 못한다. 대신 저장과 같은 검증을 지난다 — 계약을
    어긴 값으로 그린 그림은 렌더 결과와 다르다.

    요청한 장면 하나만 만들지 않고 **전부 만들어 캐시에 넣는다.** 뒤쪽 장면 하나를 만드는
    비용이 전부를 만드는 비용과 거의 같기 때문이다 (`docs/spikes/27-preview-frames.md`).
    """
    run_dir = _run_dir(params)
    project = params.get("project")
    if not isinstance(project, dict):
        raise ApiError("bad_request", "params.project는 project.json 내용(매핑)이어야 한다")

    index = params.get("scene_index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ApiError("bad_request", "params.scene_index는 0 이상의 정수여야 한다")

    try:
        validate_project(project)
    except SchemaError as error:
        raise ApiError(
            "schema",
            f"{PROJECT_SCHEMA.name}의 계약을 어겨 프리뷰를 만들지 않았다.",
            error.messages,
        ) from error

    scenes = method_scenes({"run_dir": str(run_dir)})["scenes"]
    signature = _signature(run_dir, project, scenes)
    frames, elapsed_ms = _preview_cache(run_dir, project, scenes, signature)

    path = frames.get(index)
    if path is None:
        raise ApiError(
            "bad_request",
            f"장면 인덱스가 범위를 벗어났다: {index} (장면 {len(scenes['scenes'])}개)",
        )

    return {
        "scene_index": index,
        "signature": signature,
        "scene_count": len(frames),
        # **이 호출이 FFmpeg를 지났는가.** 앱이 대기를 어떻게 그릴지와는 무관하고, 실측
        # 지연을 화면에 그대로 띄우기 위한 값이다 (#27 완료 조건).
        "generated": elapsed_ms is not None,
        "elapsed_ms": elapsed_ms,
        # 바이너리는 base64를 지난다. 프리뷰 PNG는 40~60KB이고 1MB 왕복이 12ms이므로
        # 이 해상도에서는 여유가 있다 (스파이크 #25 5장).
        "png": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def method_render(params: dict[str, Any], notify: Notifier) -> Any:
    """최종 `final_short.mp4`를 만든다 (PRD 7.9, 이슈 #30).

    **프리뷰와 같은 입력 규칙이다** — 프로젝트를 앱에서 받고(파일을 다시 읽으면 화면에서 본
    것과 결과가 갈린다) 저장과 같은 검증을 지난다. 그래서 **저장하지 않은 편집도 렌더에
    반영되고**, 그 사실을 경고 목록으로 말하는 것은 앱 쪽이다 (D2 확정 스펙 4장의 `warn`).

    **`flagged` 게이트는 여기 없다.** 사람이 확인했는지는 `project.json`의 `review`가 들고
    있고 그 판단은 앱이 한다 — 백엔드가 막으면 CLI 렌더(경고 후 진행, 종료 코드 0)와 규칙이
    갈린다 (퀴즈 스펙 5.2).

    **동시에 둘을 돌리지 않는다.** 같은 `final_short.mp4`를 두 ffmpeg가 쓰면 결과가 어느
    쪽인지 알 수 없고, 앱의 버튼 잠금은 창이 여럿이거나 스모크가 직접 부를 때 성립하지 않는다.

    진행 알림은 ffmpeg가 내는 주기(기본 0.5초)를 그대로 따른다 — 따로 조절하지 않는 이유는
    **실측 28초 영상 하나가 다섯 줄**이고(이 머신에서 3.4초 렌더) 앱이 마지막 값만 그리기
    때문이다.

    Raises:
        ApiError: 이미 렌더가 돌고 있을 때(`busy`), 프로젝트가 계약을 어겼을 때(`schema`),
            장면 목록을 읽지 못할 때(`not_found`/`schema`), 렌더가 실패했을 때(`render`).
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
            f"{PROJECT_SCHEMA.name}의 계약을 어겨 렌더하지 않았다.",
            error.messages,
        ) from error

    if not _RENDER_LOCK.acquire(blocking=False):
        raise ApiError(
            "busy",
            "이미 렌더가 돌고 있다. 끝난 뒤에 다시 시작한다.",
            ["같은 출력 파일에 두 ffmpeg가 쓰면 결과가 어느 쪽인지 알 수 없다"],
        )
    try:
        scenes = method_scenes({"run_dir": str(run_dir)})["scenes"]
        started = time.perf_counter()

        def report(progress: video_renderer.RenderProgress) -> None:
            notify(
                "render_progress",
                frame=progress.frame,
                total_frames=progress.total_frames,
                scene_index=progress.scene_index,
                # **퍼센트와 남은 시간은 싣지 않는다.** 경과 시간과 프레임 수가 있으면 화면이
                # 계산할 수 있고, 그 표현은 시안이 정한다 (D2 확정 스펙 3.3).
                elapsed_ms=round((time.perf_counter() - started) * 1000),
            )

        try:
            output = video_renderer.render(
                project, scenes, run_dir=run_dir, on_progress=report
            )
        except RenderError as error:
            raise ApiError(
                "render",
                f"렌더에 실패했다 — {error}",
                [
                    "FFmpeg가 PATH에 있는지, 배경·폰트·오디오 경로가 맞는지 확인한다",
                    "같은 명령과 stderr가 run.log에 남는다",
                ],
                # ffmpeg stderr는 사람이 읽는 문구와 갈라져 온다 (`RenderError.raw`).
                raw=error.raw,
            ) from error

        return {
            "output_path": str(output),
            "bytes": output.stat().st_size,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    finally:
        _RENDER_LOCK.release()


METHODS: dict[str, Handler] = {
    "ping": _plain(method_ping),
    "env": _plain(method_env),
    "presets": _plain(method_presets),
    "open": _plain(method_open),
    "save": _plain(method_save),
    "scenes": _plain(method_scenes),
    "content": _plain(method_content),
    "save_content": _plain(method_save_content),
    "preview": _plain(method_preview),
    "render": method_render,
}

BACKGROUND_METHODS = frozenset({"preview", "render"})
"""디스패치 루프를 잡고 있으면 안 되는 메서드 (`serve`).

프리뷰 한 번이 2~3초이고 최종 렌더는 그보다 훨씬 길다. 그 사이 저장도 다른 요청도 받지
못하면 앱이 멈춘 것과 구분되지 않고, **렌더 중에도 다른 화면을 볼 수 있어야 한다**
(D2 확정 스펙 3.3). 여기 이름을 추가하는 조건은 "사람이 기다린다고 느낄 만큼 걸린다"이다.
"""

_RENDER_LOCK = threading.Lock()
"""렌더는 한 번에 하나다 (`method_render`). 프리뷰 락과 갈라 둔 이유는 둘이 서로를 막을 이유가
없기 때문이다 — 프리뷰는 캐시를 지키고 이쪽은 출력 파일을 지킨다."""


# --- 프리뷰 캐시 -----------------------------------------------------------------

_PREVIEW_LOCK = threading.Lock()
"""프리뷰는 스레드에서 돈다. 같은 서명에 두 요청이 겹치면 FFmpeg를 두 번 띄우게 된다."""

_PREVIEW: dict[str, Any] = {"root": None, "signature": None, "frames": {}}
"""**서명 하나 분량만 들고 있다.**

편집은 앞으로만 가므로 지난 서명의 프레임은 다시 요청되지 않는다. 전부 남기면 편집 한 번에
11장씩 쌓이고, 지우는 기준을 따로 정해야 한다.

run 디렉터리 **밖**이다 — TTS 캐시와 같은 이유가 아니라 반대 이유다. 이쪽은 파생물이고
수명이 앱 세션이라, run 디렉터리에 남기면 사용자가 만들지 않은 파일이 산출물 옆에 쌓인다.
"""


def _signature(run_dir: Path, project: dict[str, Any], scenes: dict[str, Any]) -> str:
    """프레임을 정하는 입력 전부의 지문.

    **프로젝트를 통째로 넣되 `PREVIEW_BLIND_SECTIONS`만 뺀다.** 프리뷰에 영향을 주는 필드를
    골라 적으면 렌더가 읽는 필드가 늘었을 때(#34의 모션) 화면이 옛 그림에 머문다 — 틀린 그림을
    캐시가 지켜 주는 쪽이 값을 하나 더 해싱하는 것보다 비싸다. 그래서 목록은 "프레임에 닿는
    것"이 아니라 **"프레임에 닿지 않는 것"** 쪽으로 뒀다. 빼는 근거는 스키마가 들고 있고
    (`schemas/project.py`), 새 섹션은 아무것도 하지 않아도 지문에 들어간다.

    빼지 않으면 확인 버튼 한 번(#28)이나 볼륨 슬라이더 한 칸(#81)이 프레임 11장을 다시 만들고
    (이 머신에서 2초대) 결과는 같은 그림이다.
    """
    payload = "\n".join(
        [
            str(run_dir),
            serialize_artifact(
                {k: v for k, v in project.items() if k not in PREVIEW_BLIND_SECTIONS}
            ),
            serialize_artifact(scenes),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _preview_cache(
    run_dir: Path, project: dict[str, Any], scenes: dict[str, Any], signature: str
) -> tuple[dict[int, Path], int | None]:
    """서명에 해당하는 프레임 전부. 없으면 만든다.

    Returns:
        (`{장면 인덱스: PNG 경로}`, 이번에 만드는 데 걸린 밀리초). 캐시에 있었으면 둘째가
        `None`이다.
    """
    with _PREVIEW_LOCK:
        if _PREVIEW["signature"] == signature:
            return dict(_PREVIEW["frames"]), None

        if _PREVIEW["root"] is None:
            _PREVIEW["root"] = Path(tempfile.mkdtemp(prefix="shorts-preview-"))
            # 정상 종료(stdin EOF)에서 치운다. 강제 종료로 남은 것은 OS의 임시 디렉터리
            # 정리에 맡긴다 — 그것까지 지키려고 수명 관리 코드를 두는 것은 과하다.
            atexit.register(shutil.rmtree, _PREVIEW["root"], ignore_errors=True)
        root = Path(_PREVIEW["root"])
        target = root / signature

        started = time.perf_counter()
        try:
            frames = video_renderer.preview(
                project, scenes, run_dir=run_dir, out_dir=target
            )
        except RenderError as error:
            shutil.rmtree(target, ignore_errors=True)
            raise ApiError(
                "render",
                f"프리뷰 프레임을 만들지 못했다 — {error}",
                ["FFmpeg가 PATH에 있는지, 배경·폰트 경로가 맞는지 확인한다"],
            ) from error
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        # 지난 서명은 여기서 버린다. 새 프레임이 자리에 앉은 뒤라 실패해도 잃는 것이 없다.
        for stale in root.iterdir():
            if stale != target:
                shutil.rmtree(stale, ignore_errors=True)

        _PREVIEW["signature"] = signature
        _PREVIEW["frames"] = frames
        return dict(frames), elapsed_ms


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


def handle(
    request: Any, *, emit: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """요청 하나를 처리해 응답 하나를 만든다.

    **예외를 밖으로 내지 않는다.** 백엔드가 요청 하나 때문에 죽으면 앱은 열려 있고 저장은
    불가능한 상태가 된다 — 사용자에게는 그것이 가장 나쁜 실패다. 알 수 없는 예외도
    `internal`로 실어 보내고 루프는 계속 돈다.

    Args:
        emit: 줄 하나를 내보내는 함수. 주면 진행 알림이 여기로 나가고(#30), 주지 않으면
            알림은 버려진다 — 파일·목록으로 돌리는 경로가 그쪽이다.
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

    def notify(event: str, **fields: Any) -> None:
        # **알림에도 요청 `id`를 싣는다.** 재시도한 뒤 이전 렌더의 늦은 알림이 오면 화면이
        # 거꾸로 갈 수 있고, 앱은 프리뷰에서 같은 함정을 티켓으로 막고 있다 (#30).
        if emit is not None:
            emit({"event": event, "id": identifier, **fields})

    try:
        return {"id": identifier, "result": handler(params, notify)}
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

    # 스레드에서도 이 함수로 쓴다. 줄이 섞이면 앱 쪽 파서가 한 줄 = JSON 하나를 잃는다.
    guard = threading.Lock()

    def emit(payload: dict[str, Any]) -> None:
        with guard:
            _emit(stdout, payload)

    def background(request: dict[str, Any]) -> None:
        # **응답을 이 루프가 내지 않는다.** 끝나는 순서가 요청 순서와 다를 수 있고, 그래도
        # 되는 이유는 앱이 `id`로 짝을 찾기 때문이다 (`electron/main.js`의 `pending`).
        # daemon인 것은 stdin이 닫혔을 때 프리뷰가 종료를 붙잡지 않기 위해서다.
        threading.Thread(
            target=lambda: emit(handle(request, emit=emit)), daemon=True
        ).start()

    # **렌더 중에 백엔드가 끝나면 ffmpeg가 남는다** (#30). 렌더 스레드는 daemon이라 stdin EOF로
    # 이 함수가 돌아올 때 그냥 사라지지만, 자식 ffmpeg는 계속 돌아 사용자가 앱을 닫은 뒤에
    # `final_short.mp4`를 완성한다. `atexit`인 것은 정상 종료(EOF)에서 확실히 지나는 자리이기
    # 때문이고, 강제 종료로 남은 것까지 지키지는 못한다 (프리뷰 임시 디렉터리와 같은 선이다).
    atexit.register(video_renderer.kill_active)

    emit({"event": "ready", "pid": os.getpid(), "protocol": PROTOCOL_VERSION})
    for response in respond(stdin, background=background, emit=emit):
        emit(response)


def respond(
    lines: Iterable[str],
    *,
    background: Callable[[dict[str, Any]], None] | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """요청 줄들 → 응답들. 파일이나 목록으로도 돌릴 수 있게 stdio와 분리한다.

    Args:
        background: 주면 `BACKGROUND_METHODS`의 요청을 그쪽에 넘기고 여기서는 아무것도
            내지 않는다 — 그 응답은 넘겨받은 쪽이 직접 쓴다. 주지 않으면 전부 순서대로
            처리한다(테스트와 파일 입력이 그 경로다).
        emit: 진행 알림이 나가는 자리 (#30). `background`와 갈라 둔 이유는 알림이 응답과
            달리 **요청 하나에 여러 줄**이고, 여기서 `yield`로는 표현되지 않기 때문이다.
    """
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            yield _failure(None, ApiError("bad_request", f"JSON 문법 오류 — {error}"))
            continue
        if (
            background is not None
            and isinstance(request, dict)
            and request.get("method") in BACKGROUND_METHODS
        ):
            background(request)
            continue
        yield handle(request, emit=emit)


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
