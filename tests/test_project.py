"""`project.json` 초기 상태 — 이슈 #19의 완료 조건 중 렌더러 입력 계약 쪽 (PRD 7.10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shorts_maker import project
from shorts_maker.config import Config, load_config
from shorts_maker.schemas import SchemaError, validate_project
from shorts_maker.timeline import VOICE_TRACK
from shorts_maker.video_renderer import CANVAS_HEIGHT, CANVAS_WIDTH, FPS, OUTPUT_NAME

FINAL_SCENES: dict[str, Any] = {
    "schema_version": 1,
    "type": "quiz",
    "scenes": [
        {"role": "hook", "text": "훅", "duration": 2.5},
        {"role": "countdown", "duration": 3.0, "seconds": 3},
    ],
}


def config_of(tmp_path: Path, **overrides: Any) -> Config:
    """설정 파일이 없는 상태의 기본 설정. `search_from`을 주지 않으면 저장소의 config.yaml을
    읽어 테스트가 실행 위치에 좌우된다."""
    return load_config(overrides=overrides, search_from=tmp_path)


def test_the_initial_state_passes_its_own_schema(tmp_path: Path) -> None:
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    validate_project(content)


def test_the_render_section_is_the_format_spec(tmp_path: Path) -> None:
    """PRD 6.3의 규격이 그대로 들어간다. 렌더러가 읽는 값이다."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["render"] == {
        "width": CANVAS_WIDTH,
        "height": CANVAS_HEIGHT,
        "fps": FPS,
        "output": OUTPUT_NAME,
        "caption_style": "impact_yellow",
        "font_path": None,
        "cta_punch": "구독 · 좋아요",
        "cta_tail": "매일 새 상식 퀴즈",
        "caption_onset_sec": 0.90,
    }
    assert (content["render"]["width"], content["render"]["height"]) == (1080, 1920)
    assert content["render"]["fps"] == 30


def test_the_overlay_settings_ride_through_the_project(tmp_path: Path) -> None:
    """**렌더러가 config를 다시 열지 않는다** (#20). 자막 스타일·폰트·cta 문구가 여기 없으면
    앱(#29)이 편집한 프로젝트와 CLI 렌더가 갈린다 (PRD 7.10)."""
    content = project.build(
        FINAL_SCENES,
        config=config_of(
            tmp_path,
            **{
                "render.caption_style": "neon_mint",
                "render.font_path": "C:/fonts/mine.otf",
                "render.cta_punch": "구독하기",
                "render.cta_tail": "내일도 한 문제",
            },
        ),
        run_dir=tmp_path,
    )

    assert content["render"]["caption_style"] == "neon_mint"
    assert content["render"]["font_path"] == "C:/fonts/mine.otf"
    assert content["render"]["cta_punch"] == "구독하기"
    assert content["render"]["cta_tail"] == "내일도 한 문제"


def test_an_unset_font_path_stays_null(tmp_path: Path) -> None:
    """`str(None)`이 `"None"`이 되면 렌더러가 그 이름의 파일을 찾다가 죽는다."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["render"]["font_path"] is None


def test_the_background_comes_from_the_config(tmp_path: Path) -> None:
    """이름은 #38이 정한다. 여기서 이름을 새로 짓지 않는다."""
    content = project.build(
        FINAL_SCENES,
        config=config_of(tmp_path, **{"render.background": "purple_gradient"}),
        run_dir=tmp_path,
    )

    assert content["background"] == {"kind": "preset", "value": "purple_gradient"}


def test_without_a_voice_track_the_audio_is_null(tmp_path: Path) -> None:
    """낭독 장면이 없으면 `voice.mp3`가 생성되지 않는다 (PRD 6.2 표)."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["audio"] == {"voice": None, "music": None}


def test_an_existing_voice_track_is_referenced(tmp_path: Path) -> None:
    (tmp_path / VOICE_TRACK).write_bytes(b"audio")

    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["audio"]["voice"] == VOICE_TRACK


def test_the_type_comes_from_the_scenes(tmp_path: Path) -> None:
    """이 모듈은 타입 이름을 모른다 — 장면이 들고 있는 값을 옮길 뿐이다."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["type"] == FINAL_SCENES["type"]


def test_the_scene_array_is_referenced_not_copied(tmp_path: Path) -> None:
    """같은 장면이 두 곳에 있으면 어느 쪽이 원본인지 모호해진다 (PRD 7.4.1)."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["scenes"] == "scenes.json"


def test_no_edit_state_fields_yet(tmp_path: Path) -> None:
    """텍스트 오버레이 편집 이력과 트랙별 볼륨은 #26이 붙인다 (PRD 7.10). 자막 스타일·폰트·
    cta 문구는 편집 상태가 아니라 렌더러가 읽는 초기 상태라 `render` 안에 있다 (#20)."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert set(content) == {
        "schema_version",
        "type",
        "language",
        "scenes",
        "background",
        "audio",
        "render",
    }


def test_a_draft_scene_list_is_rejected(tmp_path: Path) -> None:
    """초안을 가리키는 `project.json`은 열 수 없는 프로젝트다."""
    draft = {"schema_version": 1, "type": "quiz", "scenes": [{"role": "hook"}]}

    with pytest.raises(SchemaError):
        project.build(draft, config=config_of(tmp_path), run_dir=tmp_path)
