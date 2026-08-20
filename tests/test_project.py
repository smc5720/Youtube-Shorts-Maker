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

    assert content["background"] == {
        "kind": "preset",
        "value": "purple_gradient",
        # 모션은 배경과 한 섹션에 산다 (#34). 기본은 없음이고, 그 값이 여기 있어야 렌더에
        # 도달한다 — 렌더러는 config를 다시 열지 않는다.
        "motion": {"kind": "none", "strength": 0.08},
    }


def test_the_background_motion_rides_through_the_project(tmp_path: Path) -> None:
    """**config가 아니라 이 파일이 렌더러의 입력이다** (#34, PRD 7.10). 여기 옮겨 담지 않으면
    설정한 모션이 렌더에 도달하지 않는다."""
    content = project.build(
        FINAL_SCENES,
        config=config_of(
            tmp_path,
            **{"render.motion.kind": "pan_left", "render.motion.strength": 0.2},
        ),
        run_dir=tmp_path,
    )

    assert content["background"]["motion"] == {"kind": "pan_left", "strength": 0.2}
    validate_project(content)


def test_without_a_voice_track_the_audio_is_null(tmp_path: Path) -> None:
    """낭독 장면이 없으면 `voice.mp3`가 생성되지 않는다 (PRD 6.2 표)."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert content["audio"] == {
        "voice": None,
        # 배경음악도 기본은 없음이다 (PRD 8장) — 번들 음악이 없으므로 config 기본값이 `null`이고
        # 값 셋은 경로가 생길 때를 위해 항상 적힌다 (#35).
        "music": None,
        "music_volume": 0.30,
        "music_duck": 0.35,
        "music_duck_fade_sec": 0.25,
        "sfx_volume": 1.0,
        "voice_volume": 1.0,
    }


def test_the_track_gains_come_from_the_config(tmp_path: Path) -> None:
    """값이 config → `project.json` → 렌더러 한 방향으로 흐른다 (#23, #81, PRD 7.10).

    렌더러가 두 게인을 config에서 다시 읽으면 앱이 편집한 프로젝트와 CLI 렌더가 갈린다 —
    그 방향을 지키는지는 여기와 `test_audio_mix.py`가 함께 본다.
    """
    content = project.build(
        FINAL_SCENES,
        config=config_of(tmp_path, **{"audio.sfx_volume": 0.0, "audio.voice_volume": 0.4}),
        run_dir=tmp_path,
    )

    assert content["audio"]["sfx_volume"] == 0.0
    assert content["audio"]["voice_volume"] == 0.4


def test_the_music_settings_come_from_the_config(tmp_path: Path) -> None:
    """음악도 같은 한 방향을 지난다 (#35, PRD 7.10).

    **`project.build`가 옮겨 담지 않으면 렌더에 도달하지 않는다** — 렌더러는 config를 다시
    열지 않으므로, 경로만 config에 적고 여기를 지나지 않으면 음악이 조용히 빠진다.
    """
    content = project.build(
        FINAL_SCENES,
        config=config_of(
            tmp_path,
            **{
                "audio.music": "bgm/bed.mp3",
                "audio.music_volume": 0.5,
                "audio.music_duck": 0.2,
                "audio.music_duck_fade_sec": 0.4,
            },
        ),
        run_dir=tmp_path,
    )

    assert content["audio"]["music"] == "bgm/bed.mp3"
    assert content["audio"]["music_volume"] == 0.5
    assert content["audio"]["music_duck"] == 0.2
    assert content["audio"]["music_duck_fade_sec"] == 0.4
    validate_project(content)


def test_a_new_project_always_writes_the_voice_gain(tmp_path: Path) -> None:
    """**스키마에서는 선택이지만 여기서는 항상 쓴다** (#81). 선택인 것은 이 필드가 생기기 전에
    만들어진 run 디렉터리 때문이고, 새 프로젝트가 비워 두면 앱이 보여 줄 값이 파일마다 갈린다."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)

    assert "voice_volume" in content["audio"]


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
    있고(#20), 트랙별 볼륨도 같은 이유로 `audio`에 있다(#81). 여기 이름이 늘어나면 렌더러가
    읽지 않는 값이 하나 더 생겼다는 뜻이다 — 프리뷰 지문에서 빠지는 조건은 그것과 다르므로
    (`PREVIEW_BLIND_SECTIONS`는 "프레임에 닿지 않는 것"이다) 두 목록을 같이 움직이지 않는다.
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


# --- 자막 텍스트와 텍스트 오버레이 (#83) --------------------------------------


def overlay_of(**overrides: Any) -> dict[str, Any]:
    """확정 스키마를 만족하는 오버레이 하나 (D2 확정 스펙 7.2)."""
    return {
        "id": "o1",
        "text": "여기 주목",
        "pos": "bottom-center",
        "offset": {"x": 0, "y": 40},
        "color": "preset",
        "size": 40,
        "weight": 700,
        "timing": "scene",
    } | overrides


def test_a_scene_override_can_carry_a_caption_text(tmp_path: Path) -> None:
    """장면의 `text` 한 칸을 덮는다 — `scenes.json`은 건드리지 않는다 (확정 스펙 7.3)."""
    validate_project(with_overrides(tmp_path, {"role": "hook", "text": "고친 문구"}))


def test_an_empty_caption_text_is_rejected(tmp_path: Path) -> None:
    """빈 문구를 얹는 것은 뜻이 없다 — 앱은 그때 키를 지워 원래 문구로 돌아간다."""
    with pytest.raises(SchemaError):
        validate_project(with_overrides(tmp_path, {"role": "hook", "text": "  "}))


def test_a_scene_override_can_carry_overlays(tmp_path: Path) -> None:
    validate_project(
        with_overrides(tmp_path, {"role": "hook", "overlays": [overlay_of()]})
    )


def test_the_three_edits_share_one_override_item(tmp_path: Path) -> None:
    """길이·문구·오버레이가 같은 장면을 가리키므로 키가 세 벌 생기지 않는다 (PRD 14.1)."""
    validate_project(
        with_overrides(
            tmp_path,
            {
                "role": "hook",
                "duration": 2.0,
                "text": "고친 문구",
                "overlays": [overlay_of()],
            },
        )
    )


def test_an_overlay_weight_that_is_not_bundled_is_rejected(tmp_path: Path) -> None:
    """시안의 400·600은 번들에 없다 — 저장되면 `font_path()`가 렌더를 멈춘다 (7.1-2).

    **화면에서는 정상으로 보이고 렌더에서만 실패하므로** UI에서 빼는 것으로는 부족하다.
    """
    for weight in (400, 600):
        with pytest.raises(SchemaError) as failure:
            validate_project(
                with_overrides(
                    tmp_path, {"role": "hook", "overlays": [overlay_of(weight=weight)]}
                )
            )
        assert "500 | 700 | 800" in str(failure.value)


def test_the_bundled_weights_are_accepted(tmp_path: Path) -> None:
    for weight in (500, 700, 800):
        validate_project(
            with_overrides(
                tmp_path, {"role": "hook", "overlays": [overlay_of(weight=weight)]}
            )
        )


def test_an_overlay_takes_one_of_nine_positions(tmp_path: Path) -> None:
    for position in (
        "top-left",
        "mid-center",
        "bottom-right",
    ):
        validate_project(
            with_overrides(
                tmp_path, {"role": "hook", "overlays": [overlay_of(pos=position)]}
            )
        )
    with pytest.raises(SchemaError):
        validate_project(
            with_overrides(
                tmp_path, {"role": "hook", "overlays": [overlay_of(pos="center")]}
            )
        )


def test_an_overlay_color_is_a_name_not_a_value(tmp_path: Path) -> None:
    """자유 색이 없다 — 이름이 프리셋의 역할로 옮겨진다 (확정 스펙 7.2)."""
    with pytest.raises(SchemaError):
        validate_project(
            with_overrides(
                tmp_path, {"role": "hook", "overlays": [overlay_of(color="#FF0000")]}
            )
        )


def test_an_overlay_offset_can_be_negative(tmp_path: Path) -> None:
    """안전 영역 밖으로 미는 것은 사람의 선택이고, 넘침은 프리뷰에 그대로 보인다 (7.5)."""
    validate_project(
        with_overrides(
            tmp_path,
            {"role": "hook", "overlays": [overlay_of(offset={"x": -20, "y": -8})]},
        )
    )


def test_an_overlay_timing_is_either_scene_or_a_window(tmp_path: Path) -> None:
    validate_project(
        with_overrides(
            tmp_path,
            {
                "role": "hook",
                "overlays": [overlay_of(timing={"start": 0.5, "dur": 1.5})],
            },
        )
    )

    with pytest.raises(SchemaError) as failure:
        validate_project(
            with_overrides(
                tmp_path, {"role": "hook", "overlays": [overlay_of(timing="always")]}
            )
        )
    assert "scene" in str(failure.value)

    # 매핑이면 그 안의 위반을 그대로 말한다 — 후보를 골라 그쪽 오류만 낸다.
    with pytest.raises(SchemaError) as failure:
        validate_project(
            with_overrides(
                tmp_path,
                {"role": "hook", "overlays": [overlay_of(timing={"start": 0, "dur": 0})]},
            )
        )
    assert "dur" in str(failure.value)


def test_an_empty_overlay_list_is_rejected(tmp_path: Path) -> None:
    """마지막 항목을 지운 자리에 `[]`가 남으면 아무것도 하지 않는 오버라이드가 쌓인다."""
    with pytest.raises(SchemaError):
        validate_project(with_overrides(tmp_path, {"role": "hook", "overlays": []}))


def test_two_overlays_with_the_same_id_are_rejected(tmp_path: Path) -> None:
    """화면에서 한 카드를 고쳤을 때 다른 카드가 함께 바뀐다."""
    with pytest.raises(SchemaError) as failure:
        validate_project(
            with_overrides(
                tmp_path,
                {"role": "hook", "overlays": [overlay_of(), overlay_of(text="다른 문구")]},
            )
        )
    assert "id가 두 번" in str(failure.value)


def test_the_same_overlay_id_in_another_scene_is_fine(tmp_path: Path) -> None:
    """장면 안에서만 유일하면 된다 — 다른 장면의 목록과 섞이지 않는다."""
    validate_project(
        with_overrides(
            tmp_path,
            {"role": "hook", "overlays": [overlay_of()]},
            {"role": "cta", "overlays": [overlay_of()]},
        )
    )


def test_the_captions_stale_list_is_optional(tmp_path: Path) -> None:
    """자막만 낡은 항목 (#83). 이 필드가 생기기 전의 run 디렉터리가 열려야 한다."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)
    assert "captions_stale" not in content["review"]
    validate_project(content)

    content["review"]["captions_stale"] = [1, 2]
    validate_project(content)

    content["review"]["captions_stale"] = [1, 1]
    with pytest.raises(SchemaError) as failure:
        validate_project(content)
    assert "중복된 번호" in str(failure.value)


def test_the_two_stale_lists_can_overlap(tmp_path: Path) -> None:
    """겹치면 강한 쪽(음성까지)이 화면에 서고, 지우는 것은 재생성(#77)이다."""
    content = project.build(FINAL_SCENES, config=config_of(tmp_path), run_dir=tmp_path)
    content["review"]["stale"] = [1]
    content["review"]["captions_stale"] = [1]

    validate_project(content)


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
