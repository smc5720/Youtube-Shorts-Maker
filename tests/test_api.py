"""앱 백엔드 — stdio JSON Lines 디스패처 (이슈 #26, PRD 14.1).

이 파일이 지키는 것은 **앱이 기대는 계약** 셋이다.

- 프로젝트를 열고 저장하는 왕복이 스키마를 지난다
- 실패가 앱이 그릴 수 있는 형태로 온다 — 그리고 백엔드가 죽지 않는다
- 파이프 위에서 한글이 깨지지 않고, 앱이 사라지면 백엔드도 사라진다

마지막 둘은 **실제 프로세스를 띄워서** 확인한다. 함수 호출로는 stdio 인코딩도 stdin EOF도
지나지 않아서, 스파이크 #25가 실제로 밟은 두 함정(4.2·4.3)이 회귀해도 통과한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import api, project
from shorts_maker.config import load_config
from shorts_maker.run_context import serialize_artifact, write_artifact
from shorts_maker.schemas.project import PROJECT_SCHEMA

REPO_ROOT = Path(__file__).resolve().parents[1]

FINAL_SCENES: dict[str, Any] = {
    "schema_version": 1,
    "type": "quiz",
    "scenes": [
        {"role": "hook", "text": "이 문제 맞힐 수 있나", "duration": 2.5},
        {"role": "countdown", "duration": 3.0, "seconds": 3},
    ],
}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """`project.json`이 있는 run 디렉터리. 파이프라인이 만드는 것과 같은 초기 상태다."""
    content = project.build(
        FINAL_SCENES, config=load_config(search_from=tmp_path), run_dir=tmp_path
    )
    write_artifact(tmp_path, PROJECT_SCHEMA.name, content)
    return tmp_path


def call(method: str, **params: Any) -> dict[str, Any]:
    """요청 하나를 처리한 응답. 앱이 보내는 것과 같은 모양으로 넣는다."""
    return api.handle({"id": 1, "method": method, "params": params})


def result_of(response: dict[str, Any]) -> Any:
    assert "error" not in response, response.get("error")
    return response["result"]


def error_of(response: dict[str, Any]) -> dict[str, Any]:
    assert "error" in response, response
    return response["error"]


# --- 열기 -----------------------------------------------------------------------


def test_opening_a_run_directory_returns_the_validated_project(run_dir: Path) -> None:
    result = result_of(call("open", run_dir=str(run_dir)))

    assert result["run_dir"] == str(run_dir.resolve())
    assert result["project_path"] == str(run_dir / PROJECT_SCHEMA.name)
    assert result["project"]["type"] == "quiz"
    assert result["project"]["render"]["output"] == "final_short.mp4"


def test_opening_resolves_the_path_the_app_hands_back(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """앱은 받은 `run_dir`을 그대로 다시 넘긴다. 상대 경로가 섞이면 백엔드의 작업
    디렉터리(앱이 정한다)에 따라 다른 곳을 가리킨다."""
    monkeypatch.chdir(run_dir.parent)

    result = result_of(call("open", run_dir=run_dir.name))

    assert result["run_dir"] == str(run_dir.resolve())


def test_opening_a_directory_without_a_project_says_which_directory(tmp_path: Path) -> None:
    error = error_of(call("open", run_dir=str(tmp_path)))

    assert error["code"] == "not_found"
    assert str(tmp_path.resolve()) in error["message"]
    assert PROJECT_SCHEMA.name in error["message"]


def test_opening_a_broken_project_reports_the_field_and_keeps_running(run_dir: Path) -> None:
    """**앱이 죽지 않는다** — 위반한 필드를 목록으로 받아 그린다 (D2 확정 스펙 4장)."""
    path = run_dir / PROJECT_SCHEMA.name
    content = json.loads(path.read_text(encoding="utf-8"))
    content["render"]["fps"] = "서른"
    path.write_text(serialize_artifact(content), encoding="utf-8")

    error = error_of(call("open", run_dir=str(run_dir)))

    assert error["code"] == "schema"
    assert any(message.startswith("render.fps") for message in error["details"])
    # 같은 백엔드가 다음 요청을 그대로 받는다.
    assert result_of(call("ping", ok=True)) == {"ok": True}


def test_opening_a_file_that_is_not_json_reports_the_syntax_error(run_dir: Path) -> None:
    (run_dir / PROJECT_SCHEMA.name).write_text("{ 이건 JSON이 아니다", encoding="utf-8")

    error = error_of(call("open", run_dir=str(run_dir)))

    assert error["code"] == "schema"
    assert any("JSON 문법 오류" in message for message in error["details"])


# --- 저장 -----------------------------------------------------------------------


def test_saving_and_reopening_keeps_the_edit(run_dir: Path) -> None:
    """PRD 13장의 성공 기준 — "편집 상태가 저장되고 다시 열 수 있다"."""
    opened = result_of(call("open", run_dir=str(run_dir)))
    edited = {**opened["project"], "render": {**opened["project"]["render"], "cta_tail": "새 문구"}}

    result_of(call("save", run_dir=str(run_dir), project=edited))
    reopened = result_of(call("open", run_dir=str(run_dir)))

    assert reopened["project"]["render"]["cta_tail"] == "새 문구"


def test_saving_writes_the_same_shape_the_pipeline_writes(run_dir: Path) -> None:
    """CLI가 쓴 파일을 앱이 열어 저장만 해도 diff가 생기면 안 된다."""
    before = (run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8")
    opened = result_of(call("open", run_dir=str(run_dir)))

    result_of(call("save", run_dir=str(run_dir), project=opened["project"]))

    assert (run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8") == before


def test_saving_a_project_that_breaks_the_contract_leaves_the_original(run_dir: Path) -> None:
    """**검증이 쓰기보다 먼저다.** 계약을 어긴 상태가 파일에 남으면 그 run은 앱으로도
    CLI로도 열 수 없다."""
    path = run_dir / PROJECT_SCHEMA.name
    before = path.read_text(encoding="utf-8")
    opened = result_of(call("open", run_dir=str(run_dir)))
    broken = {**opened["project"], "background": {"kind": "지원하지 않는 종류", "value": "x"}}

    error = error_of(call("save", run_dir=str(run_dir), project=broken))

    assert error["code"] == "schema"
    assert any(message.startswith("background.kind") for message in error["details"])
    assert path.read_text(encoding="utf-8") == before


def test_a_failed_write_leaves_the_original_and_no_leftovers(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """쓰기가 도중에 실패해도 원본이 온전하다 — 임시 파일에 쓰고 교체하기 때문이다."""
    path = run_dir / PROJECT_SCHEMA.name
    before = path.read_text(encoding="utf-8")
    opened = result_of(call("open", run_dir=str(run_dir)))

    def fail(source: Any, destination: Any) -> None:
        raise OSError(28, "디스크가 가득 찼다")

    monkeypatch.setattr(api.os, "replace", fail)
    error = error_of(call("save", run_dir=str(run_dir), project=opened["project"]))

    assert error["code"] == "io"
    assert path.read_text(encoding="utf-8") == before
    assert [child.name for child in run_dir.iterdir()] == [PROJECT_SCHEMA.name]


def test_saving_needs_the_project_body(run_dir: Path) -> None:
    error = error_of(call("save", run_dir=str(run_dir), project="문자열"))

    assert error["code"] == "bad_request"


# --- 환경과 프로토콜 --------------------------------------------------------------


def test_env_reports_the_external_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """FFmpeg는 동봉하지 않는다 (스파이크 5.2). 앱은 렌더에 도달하기 전에 안내한다."""
    monkeypatch.setattr(api.shutil, "which", lambda name: None if name == "ffmpeg" else "/x/ffprobe")

    result = result_of(call("env"))

    assert result["protocol"] == api.PROTOCOL_VERSION
    assert result["tools"]["ffmpeg"] == {"found": False, "path": None}
    assert result["tools"]["ffprobe"]["found"] is True


def test_an_unknown_method_lists_the_ones_that_exist() -> None:
    error = error_of(call("없는메서드"))

    assert error["code"] == "unknown_method"
    assert "open" in error["details"][0]


def test_an_unexpected_failure_comes_back_as_a_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """**요청 하나 때문에 백엔드가 죽으면** 앱은 열려 있고 저장은 불가능한 상태가 된다."""

    def explode(params: dict[str, Any]) -> Any:
        raise ZeroDivisionError("예상 못 한 실패")

    monkeypatch.setitem(api.METHODS, "ping", explode)
    error = error_of(call("ping"))

    assert error["code"] == "internal"
    assert "ZeroDivisionError" in error["message"]


def test_a_malformed_line_does_not_stop_the_stream() -> None:
    lines = ["{망가진 줄", "", json.dumps({"id": 7, "method": "ping", "params": {"값": 1}})]

    responses = list(api.respond(lines))

    assert responses[0]["error"]["code"] == "bad_request"
    assert responses[1] == {"id": 7, "result": {"값": 1}}


# --- 프로세스 경계 ----------------------------------------------------------------


def backend(**kwargs: Any) -> subprocess.Popen[bytes]:
    """앱이 띄우는 것과 같은 방식으로 백엔드 프로세스를 연다.

    **바이트로 읽는다.** 텍스트 모드로 열면 이 테스트 프로세스의 기본 인코딩이 개입해
    확인하려는 것(파이프에 실제로 실린 바이트)이 가려진다.
    """
    environment = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    return subprocess.Popen(
        [sys.executable, "-u", "-m", "shorts_maker.api"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=environment,
        cwd=REPO_ROOT,
        **kwargs,
    )


def test_the_pipe_carries_korean_as_utf8(run_dir: Path) -> None:
    """Windows에서 파이프 stdio의 기본 인코딩은 cp949다 (스파이크 4.3). 재설정을 빼면
    한글이 **에러 없이 값만** 깨져서, 문자열 비교가 아니라 바이트를 봐야 잡힌다."""
    process = backend()
    assert process.stdin and process.stdout
    try:
        ready = json.loads(process.stdout.readline().decode("utf-8"))
        assert ready["event"] == "ready"
        assert ready["protocol"] == api.PROTOCOL_VERSION

        request = {"id": 1, "method": "open", "params": {"run_dir": str(run_dir)}}
        process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        process.stdin.flush()
        raw = process.stdout.readline()
    finally:
        process.stdin.close()
        process.wait(timeout=10)

    assert "구독 · 좋아요".encode("utf-8") in raw
    payload = json.loads(raw.decode("utf-8"))
    assert payload["result"]["project"]["render"]["cta_punch"] == "구독 · 좋아요"


def test_closing_stdin_ends_the_backend() -> None:
    """**고아 프로세스를 막는 것이 수명 관리 코드가 아니라 stdin EOF다** (스파이크 4.2).
    앱이 크래시하면 파이프가 닫히고 백엔드가 스스로 끝난다."""
    process = backend()
    assert process.stdin and process.stdout
    process.stdout.readline()  # ready

    process.stdin.close()

    assert process.wait(timeout=10) == 0
