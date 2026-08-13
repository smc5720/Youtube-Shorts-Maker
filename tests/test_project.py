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
        # 사람이 얹은 장면 편집 (#82). 생성 직후에는 비어 있고, 렌더러가 읽는 값이라
        # `review`와 달리 이 섹션에 있다.
        "scene_overrides": [],
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

    assert content["audio"] == {"voice": None, "music": None, "sfx_volume": 1.0}


def test_the_sfx_gain_comes_from_the_config(tmp_path: Path) -> None:
    """값이 config → `project.json` → 렌더러 한 방향으로 흐른다 (#23, PRD 7.10).

    렌더러가 `audio.sfx_volume`을 config에서 다시 읽으면 앱이 편집한 프로젝트와 CLI 렌더가
    갈린다 — 그 방향을 지키는지는 여기와 `test_audio_mix.py`가 함께 본다.
    """
    content = project.build(
        FINAL_SCENES,
        config=config_of(tmp_path, **{"audio.sfx_volume": 0.0}),
        run_dir=tmp_path,
    )

    assert content["audio"]["sfx_volume"] == 0.0


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


def test_the_only_edit_state_section_is_review(tmp_path: Path) -> None:
    """앱이 소유하는 섹션은 `review` 하나다 (#28).

    자막 스타일·폰트·cta 문구는 편집 상태가 아니라 렌더러가 읽는 초기 상태라 `render` 안에
    있고(#20), 트랙별 볼륨도 같은 이유로 `audio`로 간다(#29). 여기 이름이 늘어나면 렌더러가
    읽지 않는 값이 하나 더 생겼다는 뜻이므로 `APP_STATE_SECTIONS`도 함께 움직여야 한다.
    """
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert set(content) == {
        "schema_version",
        "type",
        "language",
        "scenes",
        "background",
        "audio",
        "render",
        "review",
    }


def test_a_new_project_has_nothing_acknowledged_or_stale(tmp_path: Path) -> None:
    """생성 직후에는 사람이 확인한 것도 낡은 것도 없다."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["review"] == {"acknowledged": [], "stale": []}


def test_a_new_project_has_no_scene_overrides(tmp_path: Path) -> None:
    """생성 직후에는 사람이 얹은 편집이 없다 (#82)."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["render"]["scene_overrides"] == []


# --- 사람이 얹은 장면 편집 (#82) ---------------------------------------------


def with_overrides(tmp_path: Path, *overrides: dict[str, Any]) -> dict[str, Any]:
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)
    content["render"]["scene_overrides"] = list(overrides)
    return content


def test_a_scene_override_is_keyed_by_role_and_question_id(tmp_path: Path) -> None:
    """장면 인덱스로 잡으면 문제를 추가·삭제할 때 밀린다 (PRD 14.1)."""
    validate_project(with_overrides(tmp_path, {"role": "hook", "duration": 2.0}))
    validate_project(
        with_overrides(tmp_path, {"role": "answer", "question_id": 2, "duration": 4.0})
    )


def test_a_grouped_role_without_a_question_id_is_rejected(tmp_path: Path) -> None:
    """`question`·`countdown`·`answer`는 번호 없이 특정되지 않는다."""
    with pytest.raises(SchemaError) as failure:
        validate_project(with_overrides(tmp_path, {"role": "answer", "duration": 4.0}))

    assert "문제 번호" in str(failure.value)


def test_a_lone_role_with_a_question_id_is_rejected(tmp_path: Path) -> None:
    """`hook`·`cta`는 영상에 하나뿐이라 번호를 받지 않는다."""
    with pytest.raises(SchemaError):
        validate_project(
            with_overrides(tmp_path, {"role": "hook", "question_id": 1, "duration": 2.0})
        )


def test_a_countdown_duration_override_is_rejected(tmp_path: Path) -> None:
    """`duration`이 `seconds`와 같아야 하고 그 값은 콘텐츠가 소유한다 (확정 스펙 7.1).

    **UI에서 빼는 것만으로는 부족하다** — 손으로 고친 `project.json`도 열린다.
    """
    with pytest.raises(SchemaError) as failure:
        validate_project(
            with_overrides(
                tmp_path, {"role": "countdown", "question_id": 1, "duration": 2.0}
            )
        )

    assert "고칠 수 없다" in str(failure.value)


def test_an_override_that_lays_nothing_on_is_rejected(tmp_path: Path) -> None:
    """얹는 값이 없는 항목은 뜻이 없다. 조용히 통과시키면 앱의 버그가 파일에 남는다."""
    with pytest.raises(SchemaError):
        validate_project(with_overrides(tmp_path, {"role": "hook"}))


def test_two_overrides_for_the_same_scene_are_rejected(tmp_path: Path) -> None:
    """어느 값이 이기는지 모호해진다."""
    with pytest.raises(SchemaError) as failure:
        validate_project(
            with_overrides(
                tmp_path,
                {"role": "hook", "duration": 2.0},
                {"role": "hook", "duration": 3.0},
            )
        )

    assert "두 번" in str(failure.value)


def test_a_zero_duration_override_is_rejected(tmp_path: Path) -> None:
    """0프레임 장면은 다음 장면과 시작 시각이 같아져 오버레이 구간이 빈다."""
    with pytest.raises(SchemaError):
        validate_project(with_overrides(tmp_path, {"role": "hook", "duration": 0.0}))


def test_a_project_without_the_overrides_field_still_opens(tmp_path: Path) -> None:
    """이 필드가 생기기 전에 만들어진 run 디렉터리가 열려야 한다."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)
    del content["render"]["scene_overrides"]

    validate_project(content)


def test_the_timeline_stale_flag_is_optional(tmp_path: Path) -> None:
    """길이를 고친 적 없는 프로젝트에는 이 값이 없다 (#82)."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)
    validate_project(content)

    content["review"]["timeline_stale"] = True
    validate_project(content)


def test_a_draft_scene_list_is_rejected(tmp_path: Path) -> None:
    """초안을 가리키는 `project.json`은 열 수 없는 프로젝트다."""
    draft = {"schema_version": 1, "type": "quiz", "scenes": [{"role": "hook"}]}

    with pytest.raises(SchemaError):
        project.build(draft, config=config_of(tmp_path), run_dir=tmp_path)
