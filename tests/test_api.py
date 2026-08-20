"""앱 백엔드 — stdio JSON Lines 디스패처 (이슈 #26, PRD 14.1).

이 파일이 지키는 것은 **앱이 기대는 계약** 셋이다.

- 프로젝트를 열고 저장하는 왕복이 스키마를 지난다
- 실패가 앱이 그릴 수 있는 형태로 온다 — 그리고 백엔드가 죽지 않는다
- 파이프 위에서 한글이 깨지지 않고, 앱이 사라지면 백엔드도 사라진다

마지막 둘은 **실제 프로세스를 띄워서** 확인한다. 함수 호출로는 stdio 인코딩도 stdin EOF도
지나지 않아서, 스파이크 #25가 실제로 밟은 두 함정(4.2·4.3)이 회귀해도 통과한다.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import api, audio_mix, overlay, project, video_renderer
from shorts_maker.assets import (
    CAPTION_COLOR_ROLES,
    AssetError,
    background_presets,
    caption_styles,
)
from shorts_maker.config import load_config
from shorts_maker.run_context import serialize_artifact, write_artifact
from shorts_maker.schemas import project as project_schema
from shorts_maker.schemas.project import BACKGROUND_KINDS, PROJECT_SCHEMA
from shorts_maker.schemas.scenes import SCENES_SCHEMA
from shorts_maker.shorts_types import DEFAULT_TYPE, get_type

REPO_ROOT = Path(__file__).resolve().parents[1]

PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
"""PNG 시그니처. **`-c:v png`가 지켜지는지를 여기서 본다** — libx264가 남은 채 확장자만
`.png`이면 경고 없이 H.264가 그 파일에 쓰인다 (스파이크 #25 6.1)."""

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="FFmpeg가 없다 — 프리뷰 명령이 맞는지는 test_video_renderer.py가 지킨다",
)

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
    """`project.json`과 `scenes.json`이 있는 run 디렉터리. 파이프라인이 내는 초기 상태다.

    **장면 목록도 함께 쓴다.** `project.json`은 장면 배열을 복사하지 않고 파일 이름만 들고
    있으므로(PRD 7.4.1), 그 파일이 없는 run 디렉터리는 앱이 열 수는 있어도 그릴 것이 없다.
    """
    write_artifact(tmp_path, SCENES_SCHEMA.name, FINAL_SCENES)
    content = project.build(
        FINAL_SCENES, config=load_config(search_from=tmp_path), run_dir=tmp_path
    )
    write_artifact(tmp_path, PROJECT_SCHEMA.name, content)
    return tmp_path


QUIZ_CONTENT: dict[str, Any] = {
    "schema_version": 1,
    "type": DEFAULT_TYPE,
    "category": "general_knowledge",
    "language": "ko",
    "hook": "이 문제 맞힐 수 있나",
    "cta": "다음 문제도 풀어보자",
    "questions": [
        {
            "id": 1,
            "question": "세계에서 가장 긴 강은?",
            "answer": "나일강",
            "explanation": "약 6,650km로 아마존강보다 조금 길다.",
            "difficulty": "easy",
            "countdown_sec": 3,
            "verify": {"status": "flagged", "confidence": 0.62, "source": "재답변 불일치"},
        },
        {
            "id": 2,
            "question": "적도가 지나는 대륙은 몇 개인가?",
            "answer": "3개",
            "explanation": "남아메리카·아프리카·아시아 세 대륙을 지난다.",
            "difficulty": "medium",
            "countdown_sec": 3,
            # `verify`가 없는 초안 상태. 앱은 이것을 `unverified`로 읽는다 (퀴즈 스펙 5.2).
        },
    ],
}


@pytest.fixture
def content_run_dir(run_dir: Path) -> Path:
    """콘텐츠 산출물까지 있는 run 디렉터리 (#28).

    **파일명을 여기 적지 않는다.** 레지스트리가 확정하므로, 이름을 옮겨 적으면 타입이
    늘었을 때 이 테스트가 조용히 낡는다.
    """
    write_artifact(run_dir, get_type(DEFAULT_TYPE).content_schema.name, QUIZ_CONTENT)
    return run_dir


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
    # 반쯤 쓰인 임시 파일이 남지 않는다 — 남으면 다음 저장이 그것을 원본으로 착각할 수 있다.
    assert not [child for child in run_dir.iterdir() if ".tmp-" in child.name]


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

    def explode(params: dict[str, Any], notify: api.Notifier) -> Any:
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


# --- 번들 프리셋 (#79) ------------------------------------------------------------


def test_presets_carry_the_names_and_labels_from_the_bundled_assets() -> None:
    """**목록의 출처가 `assets/` 하나여야 한다** (#79).

    앱은 `assets/`를 직접 읽지 못한다 — 동결 배포에서 그 디렉터리는 백엔드 실행 파일 옆이라
    앱에서 본 경로와 다르다 (스파이크 5.1). 이름을 앱에도 적으면 화면에 보이는 목록과 렌더가
    아는 목록이 갈린다.
    """
    result = result_of(call("presets"))

    # **파일 정의 순서 그대로다.** 앱이 다시 정렬하면 순서가 두 곳에서 정해진다.
    assert [style["name"] for style in result["caption_styles"]] == list(caption_styles())
    assert [preset["name"] for preset in result["backgrounds"]] == list(background_presets())
    assert all(style["label"] for style in result["caption_styles"])
    assert all(preset["label"] for preset in result["backgrounds"])


def test_presets_carry_the_default_pairing_the_app_follows() -> None:
    """스타일을 고르면 배경이 함께 바뀌는 근거 (D2 확정 스펙 1.1).

    **조합을 막는 값이 아니다** — 9조합 전부 고를 수 있고(D1 확정 스펙 6.3) 앱은 이 값을
    기본 짝으로만 쓴다. 짝 자체가 확정값인지는 `test_visual_assets.py`가 지킨다.
    """
    result = result_of(call("presets"))

    pairs = {style["name"]: style["background"] for style in result["caption_styles"]}
    assert pairs == {name: style.background for name, style in caption_styles().items()}
    # 짝이 목록에 없는 이름이면 앱이 견본을 그릴 배경을 찾지 못한다.
    assert set(pairs.values()) <= {preset["name"] for preset in result["backgrounds"]}


def test_presets_carry_the_colors_the_app_draws_swatches_with() -> None:
    """색까지 함께 준다 — 앱 CSS에 적으면 같은 표가 두 번째로 생긴다 (`assets.py` 머리말)."""
    result = result_of(call("presets"))

    for style in result["caption_styles"]:
        assert set(style["colors"]) == set(CAPTION_COLOR_ROLES)
    for preset in result["backgrounds"]:
        # 1개면 단색, 2개면 위→아래 2스톱이다 (D1 확정 스펙 6.2).
        assert 1 <= len(preset["stops"]) <= 2


def test_presets_carry_the_background_file_formats_the_app_may_offer() -> None:
    """**형식 목록의 출처가 렌더러 하나여야 한다** (#80, PRD 14.1).

    앱이 자기 목록을 들면 앱이 받은 파일을 렌더가 거부할 수 있고, 그 어긋남은 파일을 고르는
    순간이 아니라 수십 초 걸리는 렌더 도중에 드러난다.
    """
    result = result_of(call("presets"))

    formats = {entry["extension"]: entry["kind"] for entry in result["background_files"]}
    assert formats == video_renderer.BACKGROUND_FILE_KINDS
    # `kind`는 확장자가 정한다 — 앱이 그 판단을 다시 하지 않도록 둘을 함께 보낸다.
    assert set(formats.values()) <= set(BACKGROUND_KINDS)
    # 순서도 그대로다. 앱이 다시 정렬하면 화면의 순서가 두 곳에서 정해진다.
    assert [entry["extension"] for entry in result["background_files"]] == list(
        video_renderer.BACKGROUND_FILE_KINDS
    )


def test_presets_carry_the_music_file_formats_the_app_may_offer() -> None:
    """**음악 형식 목록도 앱이 적어 두면 안 된다** (#35, 배경 파일과 같은 이유).

    `kind`가 없는 것이 배경 파일과 갈리는 지점이다 — 음악에는 확장자가 정하는 종류가 없다.
    번들 음악이 없으므로(PRD 8장) 프리셋 목록도 오지 않는다.
    """
    result = result_of(call("presets"))

    assert result["music_files"] == list(audio_mix.MUSIC_FILE_EXTENSIONS)
    assert all(entry.startswith(".") for entry in result["music_files"])


def test_presets_carry_the_overlay_contract_the_app_offers() -> None:
    """**오버레이 후보 목록도 앱이 적어 두면 안 된다** (#83).

    소유자는 `assets/`가 아니라 스키마와 렌더러지만 위험이 같다 — 특히 웨이트는 시안이 적은
    400·600을 앱이 옮기면 **화면은 정상이고 렌더에서만** `AssetError`로 멈춘다 (확정 스펙 7.1-2).
    """
    result = result_of(call("presets"))["overlay"]

    assert result["positions"] == list(project_schema.OVERLAY_POSITIONS)
    assert result["sizes"] == list(project_schema.OVERLAY_SIZES)
    assert result["weights"] == list(project_schema.OVERLAY_WEIGHTS)
    assert 400 not in result["weights"] and 600 not in result["weights"]
    # 이름과 프리셋 역할을 함께 보낸다 — 앱이 그 역할로 `caption_styles[].colors`에서
    # 견본 색을 찾고 값 자체는 들지 않는다 (확장자와 `kind`를 함께 보내는 것과 같다).
    colors = {entry["name"]: entry["role"] for entry in result["colors"]}
    assert colors == overlay.OVERLAY_COLOR_ROLES
    assert set(colors) == set(project_schema.OVERLAY_COLORS)
    assert set(colors.values()) <= set(CAPTION_COLOR_ROLES)


def test_presets_does_not_need_a_run_directory() -> None:
    """`assets/`의 내용이라 run 디렉터리마다 다르지 않다. 앱이 프로젝트를 열기 전에 묻는다."""
    assert "error" not in call("presets")


def test_an_unreadable_preset_file_says_where_assets_must_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**동결 배포에서 `assets/`가 실행 파일 옆에 없는 것이 실제 실패 경로다** (스파이크 5.1).

    `internal`로 흘리면 앱이 "백엔드가 고장 났다"와 구분하지 못한다.
    """

    def explode() -> dict[str, Any]:
        raise AssetError("프리셋 파일이 없다: /없는/경로/presets.json")

    monkeypatch.setattr(api, "caption_styles", explode)
    error = error_of(call("presets"))

    assert error["code"] == "assets"
    assert "presets.json" in error["message"]
    assert "assets/" in error["details"][0]


# --- 장면 목록과 프리뷰 (#27) ------------------------------------------------------


def test_scenes_comes_back_separately_from_the_project(run_dir: Path) -> None:
    """**`open`과 나눠 둔다** — 장면 목록을 읽지 못하는 것이 프로젝트를 열지 못할 이유는
    아니다. 앱은 둘을 따로 부르고 실패도 따로 그린다."""
    result = result_of(call("scenes", run_dir=str(run_dir)))

    assert result["scenes"] == FINAL_SCENES
    assert result["scenes_path"].endswith("scenes.json")


def test_scenes_rejects_a_draft_scene_list(run_dir: Path) -> None:
    """확정 상태만 화면에 온다. 길이가 없는 초안을 목록에 그리면 총 길이도 장면 경계도
    화면과 결과가 갈린다."""
    draft = dict(FINAL_SCENES, scenes=[{"role": "hook", "text": "문구"}])
    write_artifact(run_dir, SCENES_SCHEMA.name, draft)

    error = error_of(call("scenes", run_dir=str(run_dir)))

    assert error["code"] == "schema"
    assert any("duration" in message for message in error["details"])


def test_scenes_in_a_directory_without_one_says_which_directory(tmp_path: Path) -> None:
    error = error_of(call("scenes", run_dir=str(tmp_path)))

    assert error["code"] == "not_found"
    assert "scenes.json" in error["message"]


# --- 콘텐츠 산출물 (#28) ----------------------------------------------------------


def test_content_comes_from_the_registry_not_a_filename_in_this_module(
    content_run_dir: Path,
) -> None:
    """**`api.py`는 `quiz.json`을 적을 수 없다** (`tests/test_type_boundary.py`).

    앱이 넘기는 것은 `project.json`이 들고 있던 타입 이름뿐이고, 파일명과 검증은 레지스트리가
    준다. 그래서 이 테스트도 이름을 적지 않고 같은 곳에서 가져온다.
    """
    schema = get_type(DEFAULT_TYPE).content_schema

    result = result_of(call("content", run_dir=str(content_run_dir), type=DEFAULT_TYPE))

    assert result["content"] == QUIZ_CONTENT
    assert result["content_path"].endswith(schema.name)


def test_content_needs_a_registered_type(content_run_dir: Path) -> None:
    """모르는 타입은 `bad_request`다 — 백엔드가 고장 난 것이 아니라 앱이 이 백엔드가 모르는
    타입의 프로젝트를 열었다는 뜻이고(동결 배포에서 세대가 갈린다), 앱은 편집 폼만 닫는다."""
    error = error_of(call("content", run_dir=str(content_run_dir), type="ranking"))

    assert error["code"] == "bad_request"
    assert DEFAULT_TYPE in error["message"]


def test_content_without_a_type_says_so(content_run_dir: Path) -> None:
    error = error_of(call("content", run_dir=str(content_run_dir)))

    assert error["code"] == "bad_request"
    assert "type" in error["message"]


def test_content_in_a_directory_without_one_says_which_directory(run_dir: Path) -> None:
    """`open`과 나눠 둔다 — 콘텐츠가 없어도 장면 목록과 프리뷰는 그대로 돈다."""
    error = error_of(call("content", run_dir=str(run_dir), type=DEFAULT_TYPE))

    assert error["code"] == "not_found"
    assert get_type(DEFAULT_TYPE).content_schema.name in error["message"]


def test_saving_content_and_reading_it_back_keeps_the_edit(content_run_dir: Path) -> None:
    edited = json.loads(json.dumps(QUIZ_CONTENT))
    edited["questions"][0]["answer"] = "고친 정답"

    result_of(call("save_content", run_dir=str(content_run_dir), type=DEFAULT_TYPE, content=edited))

    reopened = result_of(call("content", run_dir=str(content_run_dir), type=DEFAULT_TYPE))
    assert reopened["content"]["questions"][0]["answer"] == "고친 정답"


def test_saving_content_writes_the_same_shape_the_pipeline_writes(
    content_run_dir: Path,
) -> None:
    """**CLI가 쓴 파일을 열어 저장만 하면 diff가 생기지 않는다.** 직렬화 경로가 하나여야
    사람이 검수하는 원본에 앱이 흔적을 남기지 않는다 (`serialize_artifact`)."""
    path = content_run_dir / get_type(DEFAULT_TYPE).content_schema.name
    before = path.read_text(encoding="utf-8")

    result_of(
        call("save_content", run_dir=str(content_run_dir), type=DEFAULT_TYPE, content=QUIZ_CONTENT)
    )

    assert path.read_text(encoding="utf-8") == before


def test_saving_content_that_breaks_the_contract_leaves_the_original(
    content_run_dir: Path,
) -> None:
    """검증이 쓰기보다 먼저다. 사람이 검수하는 원본이 반쯤 쓰인 상태로 남는 것이 편집 하나를
    잃는 것보다 훨씬 나쁘다."""
    path = content_run_dir / get_type(DEFAULT_TYPE).content_schema.name
    before = path.read_text(encoding="utf-8")
    broken = json.loads(json.dumps(QUIZ_CONTENT))
    broken["questions"][0]["countdown_sec"] = "셋"

    error = error_of(
        call("save_content", run_dir=str(content_run_dir), type=DEFAULT_TYPE, content=broken)
    )

    assert error["code"] == "schema"
    assert any("countdown_sec" in message for message in error["details"])
    assert path.read_text(encoding="utf-8") == before


def test_saving_content_rejects_duplicate_item_ids(content_run_dir: Path) -> None:
    """`id`는 문제를 가리키는 유일한 손잡이다 — `scenes.json`의 `question_id`와
    `project.json`의 `review`가 이 값을 참조한다."""
    duplicated = json.loads(json.dumps(QUIZ_CONTENT))
    duplicated["questions"][1]["id"] = duplicated["questions"][0]["id"]

    error = error_of(
        call("save_content", run_dir=str(content_run_dir), type=DEFAULT_TYPE, content=duplicated)
    )

    assert error["code"] == "schema"
    assert any("중복" in message for message in error["details"])


def test_saving_content_needs_the_content_body(content_run_dir: Path) -> None:
    error = error_of(call("save_content", run_dir=str(content_run_dir), type=DEFAULT_TYPE))

    assert error["code"] == "bad_request"
    assert "content" in error["message"]


def test_the_review_section_is_optional_so_older_runs_still_open(run_dir: Path) -> None:
    """이 필드가 생기기 전에 만들어진 run 디렉터리가 있다 — 사람이 검수하려고 남겨 둔
    산출물이 그것이다."""
    body = json.loads((run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    del body["review"]
    write_artifact(run_dir, PROJECT_SCHEMA.name, body)

    result = result_of(call("open", run_dir=str(run_dir)))

    assert "review" not in result["project"]


def test_a_human_acknowledgement_saves_without_touching_the_content(
    content_run_dir: Path,
) -> None:
    """**이 이슈의 계약이다** (D2 확정 스펙 1.4). 확인 기록은 `project.json`으로 가고
    `verify.status`·`confidence`는 검증기(#10)와 검수 게이트(#11)가 소유한 채로 남는다."""
    content_path = content_run_dir / get_type(DEFAULT_TYPE).content_schema.name
    before = content_path.read_text(encoding="utf-8")
    body = json.loads((content_run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    body["review"] = {"acknowledged": [1], "stale": []}

    result_of(call("save", run_dir=str(content_run_dir), project=body))

    reopened = result_of(call("open", run_dir=str(content_run_dir)))
    assert reopened["project"]["review"]["acknowledged"] == [1]
    assert content_path.read_text(encoding="utf-8") == before


def test_a_duplicated_acknowledgement_is_rejected(content_run_dir: Path) -> None:
    """두 목록 모두 집합의 뜻이라 중복은 값을 바꾸지 않는다. 조용히 통과시키면 확인 버튼을
    누를 때마다 목록을 늘리는 버그가 드러나지 않는다."""
    body = json.loads((content_run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    body["review"] = {"acknowledged": [1, 1], "stale": []}

    error = error_of(call("save", run_dir=str(content_run_dir), project=body))

    assert error["code"] == "schema"
    assert any("review.acknowledged" in message for message in error["details"])


def test_the_review_section_does_not_reach_the_preview_signature(run_dir: Path) -> None:
    """**렌더러가 읽지 않는 값이다** (#28). 확인 버튼 한 번이 프레임 11장을 다시 만들면
    2초가 붙는데 결과는 같은 그림이다. 반대로 렌더가 읽는 값은 지문에 남아야 한다."""
    body = json.loads((run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    signature = api._signature(run_dir, body, FINAL_SCENES)

    acknowledged = json.loads(json.dumps(body))
    acknowledged["review"] = {"acknowledged": [1, 2], "stale": [3]}
    restyled = json.loads(json.dumps(body))
    restyled["render"]["caption_style"] = "neon_mint"

    assert api._signature(run_dir, acknowledged, FINAL_SCENES) == signature
    assert api._signature(run_dir, restyled, FINAL_SCENES) != signature


def test_the_audio_section_does_not_reach_the_preview_signature(run_dir: Path) -> None:
    """**렌더러는 읽지만 프리뷰 명령에는 오디오 체인이 아예 없다** (#81, #27).

    볼륨은 슬라이더라 값이 연속으로 바뀐다 — 지문에 넣으면 드래그 한 번마다 프레임 11장이
    2초대에 다시 만들어지고 결과는 같은 그림이다. 그래서 목록의 기준이 "렌더가 읽지 않는 것"이
    아니라 "프레임에 닿지 않는 것"이다.
    """
    body = json.loads((run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    signature = api._signature(run_dir, body, FINAL_SCENES)

    louder = json.loads(json.dumps(body))
    louder["audio"] = {**louder["audio"], "voice_volume": 0.3, "sfx_volume": 2.0}

    assert api._signature(run_dir, louder, FINAL_SCENES) == signature


def test_a_scene_override_reaches_the_preview_signature(run_dir: Path) -> None:
    """**앱이 쓰지만 렌더러가 읽는다** (#82). `review`와 달리 프레임을 바꾸므로 지문에 있어야
    하고, 없으면 사람이 길이를 고쳐도 화면이 옛 그림에 머문다."""
    body = json.loads((run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    signature = api._signature(run_dir, body, FINAL_SCENES)

    shortened = json.loads(json.dumps(body))
    shortened["render"]["scene_overrides"] = [{"role": "hook", "duration": 1.0}]

    assert api._signature(run_dir, shortened, FINAL_SCENES) != signature


def test_the_background_motion_reaches_the_preview_signature(run_dir: Path) -> None:
    """**목록이 "닿지 않는 것" 쪽인 덕에 아무것도 하지 않아도 들어온다** (#34, #81).

    빠지면 모션을 바꿔도 화면이 옛 그림에 머문다 — 프리뷰 정지 프레임은 모션 도중의 한 장이라
    값이 바뀌면 그림도 바뀐다.
    """
    body = json.loads((run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    signature = api._signature(run_dir, body, FINAL_SCENES)

    for motion in ({"kind": "zoom_in", "strength": 0.08}, {"kind": "none", "strength": 0.2}):
        changed = json.loads(json.dumps(body))
        changed["background"]["motion"] = motion
        assert api._signature(run_dir, changed, FINAL_SCENES) != signature


def test_preview_validates_the_project_it_is_handed(run_dir: Path) -> None:
    """**앱이 들고 있는 값으로 그린다** — 저장하지 않은 편집이 프리뷰에 보이지 않으면
    프리뷰가 편집 도구가 되지 못한다. 대신 저장과 같은 검증을 지난다."""
    broken = json.loads((run_dir / "project.json").read_text(encoding="utf-8"))
    broken["render"]["fps"] = "서른"

    error = error_of(call("preview", run_dir=str(run_dir), project=broken, scene_index=0))

    assert error["code"] == "schema"
    assert any("render.fps" in message for message in error["details"])


def test_preview_rejects_a_scene_index_out_of_range(run_dir: Path) -> None:
    project_body = json.loads((run_dir / "project.json").read_text(encoding="utf-8"))

    error = error_of(
        call("preview", run_dir=str(run_dir), project=project_body, scene_index=9)
    )

    assert error["code"] == "bad_request"
    assert "9" in error["message"]


def test_preview_needs_a_scene_index(run_dir: Path) -> None:
    project_body = json.loads((run_dir / "project.json").read_text(encoding="utf-8"))

    error = error_of(call("preview", run_dir=str(run_dir), project=project_body))

    assert error["code"] == "bad_request"
    assert "scene_index" in error["message"]


@needs_ffmpeg
def test_preview_returns_a_png_and_serves_the_next_scene_from_cache(run_dir: Path) -> None:
    """**요청한 장면 하나만 만들지 않는다.** 뒤쪽 장면 하나를 만드는 비용이 전부를 만드는
    비용과 거의 같아서, 두 번째 요청이 FFmpeg를 다시 지날 이유가 없다."""
    project_body = json.loads((run_dir / "project.json").read_text(encoding="utf-8"))

    first = result_of(call("preview", run_dir=str(run_dir), project=project_body, scene_index=0))
    second = result_of(call("preview", run_dir=str(run_dir), project=project_body, scene_index=1))

    assert base64.b64decode(first["png"]).startswith(PNG_MAGIC)
    assert first["generated"] and first["elapsed_ms"] > 0
    assert first["scene_count"] == len(FINAL_SCENES["scenes"])
    assert not second["generated"] and second["elapsed_ms"] is None
    assert second["signature"] == first["signature"]
    assert second["png"] != first["png"]


@needs_ffmpeg
def test_editing_the_project_invalidates_the_cached_frames(run_dir: Path) -> None:
    """**프로젝트를 통째로 해싱한다.** 프리뷰에 영향을 주는 필드를 골라 적으면 렌더가 읽는
    필드가 늘었을 때 화면이 옛 그림에 머문다."""
    project_body = json.loads((run_dir / "project.json").read_text(encoding="utf-8"))
    before = result_of(call("preview", run_dir=str(run_dir), project=project_body, scene_index=0))

    edited = json.loads(json.dumps(project_body))
    edited["render"]["caption_style"] = "neon_mint"
    after = result_of(call("preview", run_dir=str(run_dir), project=edited, scene_index=0))

    assert after["signature"] != before["signature"]
    assert after["generated"]


@needs_ffmpeg
def test_preview_leaves_nothing_in_the_run_directory(run_dir: Path) -> None:
    """프레임은 파생물이고 수명이 앱 세션이다. run 디렉터리에 남기면 사용자가 만들지 않은
    파일이 산출물 옆에 쌓인다 — 최종 렌더 산출물도 물론 생기지 않는다 (#27 완료 조건)."""
    project_body = json.loads((run_dir / "project.json").read_text(encoding="utf-8"))
    before = sorted(path.name for path in run_dir.iterdir())

    result_of(call("preview", run_dir=str(run_dir), project=project_body, scene_index=0))

    assert sorted(path.name for path in run_dir.iterdir()) == before


# --- 최종 렌더 (#30) -------------------------------------------------------------


def render_call(
    run_dir: Path, project_body: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """렌더 요청 하나와 그 사이에 나간 알림들.

    **알림을 응답과 함께 본다.** 진행 상황은 요청 하나에 여러 줄이라 `handle`의 반환값에는
    없고, 앱은 그 줄들을 `backend-event`로 받는다 (#30).
    """
    body = project_body or json.loads(
        (run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8")
    )
    events: list[dict[str, Any]] = []
    response = api.handle(
        {"id": 7, "method": "render", "params": {"run_dir": str(run_dir), "project": body}},
        emit=events.append,
    )
    return response, events


def test_render_validates_the_project_it_is_handed(run_dir: Path) -> None:
    """프리뷰와 같은 규칙이다 — 앱이 들고 있는 값으로 렌더하고 저장과 같은 검증을 지난다."""
    broken = json.loads((run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    broken["render"]["fps"] = "서른"

    error = error_of(render_call(run_dir, broken)[0])

    assert error["code"] == "schema"
    assert any("render.fps" in message for message in error["details"])


def test_render_reports_the_cause_when_ffmpeg_fails(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패 카드가 그리는 것 — 사람이 읽는 문구와 원문 오류다 (D2 확정 스펙 3.3)."""

    def failing(*args: Any, **kwargs: Any) -> Path:
        raise video_renderer.RenderError(
            "final_short.mp4을 만들지 못했다 — ffmpeg 종료 코드 1",
            raw="Error opening output file: Invalid argument",
        )

    monkeypatch.setattr(video_renderer, "render", failing)

    error = error_of(render_call(run_dir)[0])

    assert error["code"] == "render"
    assert "종료 코드 1" in error["message"]
    # **원문은 갈라져 온다** — 앱이 사람이 읽는 원인과 다르게 그린다 (확정 스펙 3.3).
    assert error["raw"] == "Error opening output file: Invalid argument"
    assert "Invalid argument" not in error["message"]


def test_a_failure_without_raw_output_has_no_raw_field(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """빈 원문을 실어 보내면 앱이 빈 `mono` 상자를 그린다 — 없으면 칸도 없다."""

    def failing(*args: Any, **kwargs: Any) -> Path:
        raise video_renderer.RenderError("ffmpeg를 찾을 수 없다. FFmpeg를 설치하고 PATH에 넣는다")

    monkeypatch.setattr(video_renderer, "render", failing)

    error = error_of(render_call(run_dir)[0])

    assert "raw" not in error
    assert "ffmpeg를 찾을 수 없다" in error["message"]


def test_a_second_render_is_refused_while_one_runs(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**같은 출력 파일에 두 ffmpeg가 쓰면 결과가 어느 쪽인지 알 수 없다.**

    앱의 버튼 잠금에 맡기지 않는 이유는 스모크나 두 번째 창이 직접 부를 수 있기 때문이다.
    """
    started = threading.Event()
    release = threading.Event()

    def slow(*args: Any, **kwargs: Any) -> Path:
        started.set()
        release.wait(timeout=5)
        return run_dir / "final_short.mp4"

    monkeypatch.setattr(video_renderer, "render", slow)
    (run_dir / "final_short.mp4").write_bytes(b"stub")

    first: list[dict[str, Any]] = []
    worker = threading.Thread(target=lambda: first.append(render_call(run_dir)[0]))
    worker.start()
    assert started.wait(timeout=5)
    try:
        error = error_of(render_call(run_dir)[0])
    finally:
        release.set()
        worker.join(timeout=5)

    assert error["code"] == "busy"
    assert "id" not in error
    # 첫 렌더는 그대로 끝난다 — 거절된 것은 두 번째다.
    assert result_of(first[0])["output_path"].endswith("final_short.mp4")

    # 락이 풀렸으므로 다음 요청은 다시 받는다.
    assert result_of(render_call(run_dir)[0])["bytes"] == len(b"stub")


def test_render_progress_goes_out_as_notifications_with_the_request_id(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**응답 하나로 끝나지 않는 유일한 메서드다** (#30).

    `id`를 싣는 것은 재시도한 뒤 이전 렌더의 늦은 알림이 화면을 거꾸로 돌리지 않게 하기
    위함이고, 퍼센트와 남은 시간이 없는 것은 그 계산이 화면의 것이기 때문이다.
    """

    def reporting(*args: Any, **kwargs: Any) -> Path:
        report = kwargs["on_progress"]
        report(video_renderer.RenderProgress(frame=1, total_frames=165, scene_index=0))
        report(video_renderer.RenderProgress(frame=90, total_frames=165, scene_index=1))
        return run_dir / "final_short.mp4"

    monkeypatch.setattr(video_renderer, "render", reporting)
    (run_dir / "final_short.mp4").write_bytes(b"stub")

    response, events = render_call(run_dir)

    assert result_of(response)["elapsed_ms"] >= 0
    assert [event["event"] for event in events] == ["render_progress"] * 2
    assert [event["id"] for event in events] == [7, 7]
    assert [(event["frame"], event["scene_index"]) for event in events] == [(1, 0), (90, 1)]
    assert all(event["total_frames"] == 165 for event in events)
    assert all(event["elapsed_ms"] >= 0 for event in events)
    assert all("percent" not in event for event in events)


def test_progress_is_dropped_when_nobody_is_listening(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """파일·목록으로 돌리는 경로는 알림을 받을 자리가 없다. 그래도 렌더는 끝난다."""

    def reporting(*args: Any, **kwargs: Any) -> Path:
        kwargs["on_progress"](
            video_renderer.RenderProgress(frame=1, total_frames=2, scene_index=0)
        )
        return run_dir / "final_short.mp4"

    monkeypatch.setattr(video_renderer, "render", reporting)
    (run_dir / "final_short.mp4").write_bytes(b"stub")

    assert result_of(call("render", run_dir=str(run_dir), project=json.loads(
        (run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8")
    )))["output_path"].endswith("final_short.mp4")


@needs_ffmpeg
def test_render_makes_a_playable_file_with_the_edited_duration(run_dir: Path) -> None:
    """**앱 경로가 CLI와 같은 규격을 낸다** — 같은 `video_renderer.render`를 지나기 때문이다.

    사람이 얹은 길이(#82)가 결과에 반영되는지도 함께 본다. 오버라이드는 확정 검증 **뒤에**
    얹히므로(`apply_scene_overrides`) 낭독보다 짧은 값도 렌더까지 간다.
    """
    body = json.loads((run_dir / PROJECT_SCHEMA.name).read_text(encoding="utf-8"))
    body["render"]["scene_overrides"] = [{"role": "hook", "duration": 1.0}]

    result = result_of(render_call(run_dir, body)[0])

    output = Path(result["output_path"])
    assert output.name == "final_short.mp4" and output.parent == run_dir.resolve()
    assert result["bytes"] > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=width,height,codec_name", "-of", "json", str(output)],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(probe.stdout)
    # 2.5초 hook을 1.0초로 줄였으므로 3.0초 countdown과 합쳐 4.0초다.
    assert float(streams["format"]["duration"]) == pytest.approx(4.0, abs=0.05)
    assert {stream["codec_name"] for stream in streams["streams"]} == {"h264", "aac"}
    assert [stream for stream in streams["streams"] if stream.get("width")][0]["width"] == 1080


def test_serving_arranges_to_kill_ffmpeg_when_the_backend_goes_away(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """**렌더 중에 앱이 닫히면 자식 ffmpeg가 남는다** (#30).

    렌더는 daemon 스레드에서 돌아 stdin EOF로 `serve`가 돌아올 때 그 스레드는 그냥 사라지지만,
    ffmpeg는 계속 돌아 사용자가 앱을 닫은 뒤에 `final_short.mp4`를 완성한다.
    """
    registered: list[Any] = []
    monkeypatch.setattr(api.atexit, "register", lambda hook: registered.append(hook))

    api.serve(stdin=io.StringIO(""), stdout=io.StringIO())

    assert video_renderer.kill_active in registered


def test_a_slow_method_does_not_hold_the_dispatch_loop(run_dir: Path) -> None:
    """**프리뷰 한 번이 2~3초다.** 그 사이 저장도 다른 요청도 받지 못하면 앱이 멈춘 것과
    구분되지 않는다. 응답은 `id`로 짝을 찾으므로 순서가 뒤바뀌어도 앱이 헷갈리지 않는다.

    **최종 렌더도 같은 자리를 지난다** (#30) — 그쪽이 훨씬 길고, 렌더 중에도 다른 화면을 볼
    수 있어야 한다 (D2 확정 스펙 3.3).
    """
    assert {"preview", "render"} <= api.BACKGROUND_METHODS
    lines = [
        json.dumps({"id": 1, "method": "preview", "params": {}}),
        json.dumps({"id": 2, "method": "ping", "params": {"값": 1}}),
    ]
    sent: list[dict[str, Any]] = []

    responses = list(api.respond(lines, background=sent.append))

    # 프리뷰는 루프가 답하지 않는다 — 넘겨받은 쪽이 자기 스레드에서 쓴다.
    assert [response["id"] for response in responses] == [2]
    assert [request["id"] for request in sent] == [1]


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
