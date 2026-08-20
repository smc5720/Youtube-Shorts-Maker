"""렌더 엔진 골격 — 이슈 #19의 완료 조건.

**두 층으로 갈라져 있다.** 명령 생성은 FFmpeg 없이 돌고(`build_command`), 규격·길이·오디오는
진짜 FFmpeg로 렌더해 `ffprobe`로 확인한다. 후자는 FFmpeg가 없는 환경에서 건너뛴다 — 명령이
맞는지는 앞 층이 이미 지키므로 건너뛴 실행이 검사 부재가 되지 않는다.

렌더 테스트의 영상 길이는 1~2초다. 확인하는 것이 인코딩 규격과 프레임 경계라 길이를 늘려도
검증력이 늘지 않는다.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import overlay, timeline, video_renderer
from shorts_maker.audio_mix import AudioChain
from shorts_maker.schemas import SchemaError
from shorts_maker.video_renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    FPS,
    OUTPUT_NAME,
    RenderError,
    align,
    apply_scene_overrides,
    build_command,
    render,
)

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg가 없다 — 명령 생성 테스트만 돈다",
)


def scenes_with(*durations: float, narrated: tuple[int, ...] = ()) -> dict[str, Any]:
    """확정 검증을 통과하는 최소 장면 목록. 낭독 장면은 인덱스로 지정한다."""
    scenes: list[dict[str, Any]] = []
    for index, duration in enumerate(durations):
        scene: dict[str, Any] = {"role": "hook", "text": "문구", "duration": duration}
        if index in narrated:
            scene |= {
                "role": "question",
                "narrate": True,
                "target_duration": 3.0,
                "audio": f"audio/seg-{index:03d}.mp3",
                "audio_duration": min(duration, 0.2),
                "narration_offset": round(sum(durations[:index]) + 0.3, 3),
            }
        scenes.append(scene)
    return {"schema_version": 1, "type": "quiz", "scenes": scenes}


def project_with(**overrides: Any) -> dict[str, Any]:
    """`project.build`가 만드는 초기 상태와 같은 모양."""
    project = {
        "schema_version": 1,
        "type": "quiz",
        "language": "ko",
        "scenes": "scenes.json",
        "background": {"kind": "preset", "value": "deep_navy"},
        "audio": {"voice": None, "music": None, "sfx_volume": 1.0},
        "render": {
            "width": CANVAS_WIDTH,
            "height": CANVAS_HEIGHT,
            "fps": FPS,
            "output": OUTPUT_NAME,
            # 번인 오버레이가 읽는 값 (#20). `project.build`가 config에서 옮겨 담는다.
            "caption_style": "impact_yellow",
            "font_path": None,
            "cta_punch": "구독 · 좋아요",
            "cta_tail": "매일 새 상식 퀴즈",
            # 해설이 뜨는 시각 (#22). `timing.caption_onset_sec`에서 온다.
            "caption_onset_sec": 0.90,
        },
    }
    return project | overrides


def overriding(project: dict[str, Any], *overrides: dict[str, Any]) -> dict[str, Any]:
    """`render.scene_overrides`를 얹은 프로젝트. 원본은 그대로 둔다."""
    return project | {"render": project["render"] | {"scene_overrides": list(overrides)}}


# --- 사람이 얹은 장면 편집 (#82) ---------------------------------------------


def test_an_override_replaces_the_duration_of_the_matching_scene() -> None:
    scenes = scenes_with(2.5, 3.0)
    scenes["scenes"][1]["role"] = "cta"

    applied = apply_scene_overrides(
        overriding(project_with(), {"role": "cta", "duration": 5.0}), scenes
    )

    assert [scene["duration"] for scene in applied["scenes"]] == [2.5, 5.0]


def test_the_original_scene_list_is_not_touched() -> None:
    """`scenes.json`의 `duration`은 그대로다 — 사람이 얹은 값은 `project.json`에 산다."""
    scenes = scenes_with(2.5)

    apply_scene_overrides(
        overriding(project_with(), {"role": "hook", "duration": 9.0}), scenes
    )

    assert scenes["scenes"][0]["duration"] == 2.5


def test_without_overrides_the_same_object_comes_back() -> None:
    """얹을 것이 없으면 장면 배열을 두 벌 만들지 않는다."""
    scenes = scenes_with(2.5)

    assert apply_scene_overrides(project_with(), scenes) is scenes
    assert apply_scene_overrides(overriding(project_with()), scenes) is scenes


def test_an_override_finds_its_scene_by_question_id_not_by_index() -> None:
    """앞 문제를 지워 인덱스가 밀려도 조정한 길이가 같은 문제에 남는다 (#28의 함정)."""
    scenes = scenes_with(4.0, 4.0, narrated=(0, 1))
    scenes["scenes"][0]["question_id"] = 1
    scenes["scenes"][1]["question_id"] = 2
    project = overriding(
        project_with(), {"role": "question", "question_id": 2, "duration": 6.0}
    )

    applied = apply_scene_overrides(project, scenes)
    assert [scene["duration"] for scene in applied["scenes"]] == [4.0, 6.0]

    # 1번 문제를 지운 뒤에도 같은 장면이 6.0이다.
    del scenes["scenes"][0]
    applied = apply_scene_overrides(project, scenes)
    assert [scene["duration"] for scene in applied["scenes"]] == [6.0]


def test_an_override_with_no_scene_to_point_at_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """문제를 지우면 그 오버라이드가 가리킬 장면이 없다. 조용히 버리지 않는다 (#77이 정리한다)."""
    with caplog.at_level("WARNING"):
        applied = apply_scene_overrides(
            overriding(
                project_with(), {"role": "answer", "question_id": 9, "duration": 4.0}
            ),
            scenes_with(2.5),
        )

    assert [scene["duration"] for scene in applied["scenes"]] == [2.5]
    assert "question_id=9" in caplog.text


def test_an_override_shorter_than_the_narration_is_applied() -> None:
    """확정 검증은 이 값을 거부한다 — 그래서 `scenes.json`이 아니라 여기에 산다 (PRD 14.1).

    화면이 경고하고 값은 적용된다 (확정 스펙 4장의 `warn`).
    """
    scenes = scenes_with(4.0, narrated=(0,))
    scenes["scenes"][0]["question_id"] = 1
    scenes["scenes"][0]["audio_duration"] = 3.8

    applied = apply_scene_overrides(
        overriding(
            project_with(), {"role": "question", "question_id": 1, "duration": 1.0}
        ),
        scenes,
    )

    assert applied["scenes"][0]["duration"] == 1.0
    with pytest.raises(SchemaError):
        # 같은 값을 `scenes.json`에 쓰면 열 수 없는 run 디렉터리가 된다.
        video_renderer.validate_scenes_final(applied)


def test_the_timeline_follows_the_override() -> None:
    """프레임 정렬은 여전히 `align()` 하나가 소유한다 — 얹은 값이 그 입력이 된다 (PRD 7.7)."""
    scenes = scenes_with(2.5, 3.0)
    scenes["scenes"][1]["role"] = "cta"
    project = overriding(project_with(), {"role": "cta", "duration": 1.0})

    aligned = align(apply_scene_overrides(project, scenes), fps=30)

    assert aligned.frames == (75, 30)
    assert aligned.total_sec == pytest.approx(3.5)


# --- 자막 문구와 텍스트 오버레이 (#83) ---------------------------------------

OVERLAY: dict[str, Any] = {
    "id": "o1",
    "text": "여기 주목",
    "pos": "bottom-center",
    "offset": {"x": 0, "y": 40},
    "color": "preset",
    "size": 40,
    "weight": 700,
    "timing": "scene",
}


def test_an_override_replaces_the_text_of_the_matching_scene() -> None:
    """자막 문구 편집도 길이와 같은 자리를 지난다 (확정 스펙 7.1-3)."""
    scenes = scenes_with(2.5)

    applied = apply_scene_overrides(
        overriding(project_with(), {"role": "hook", "text": "고친 문구"}), scenes
    )

    assert applied["scenes"][0]["text"] == "고친 문구"
    assert scenes["scenes"][0]["text"] == "문구"


def test_an_override_carries_overlays_onto_the_scene() -> None:
    """`scenes.json`에 없는 키로 얹는다 — 그 스키마는 모르는 필드를 거부하므로 파일에서 올 수
    없고, 출처가 `render.scene_overrides` 하나로 남는다 (`overlay.SCENE_OVERLAYS`)."""
    scenes = scenes_with(2.5)

    applied = apply_scene_overrides(
        overriding(project_with(), {"role": "hook", "overlays": [OVERLAY]}), scenes
    )

    assert applied["scenes"][0][overlay.SCENE_OVERLAYS] == [OVERLAY]
    assert overlay.SCENE_OVERLAYS not in scenes["scenes"][0]


def test_the_three_edits_ride_one_override_item() -> None:
    """길이·문구·오버레이가 같은 장면을 가리키므로 항목 하나를 공유한다 (PRD 14.1)."""
    scenes = scenes_with(2.5)

    applied = apply_scene_overrides(
        overriding(
            project_with(),
            {"role": "hook", "duration": 1.5, "text": "고친 문구", "overlays": [OVERLAY]},
        ),
        scenes,
    )

    (scene,) = applied["scenes"]
    assert (scene["duration"], scene["text"]) == (1.5, "고친 문구")
    assert scene[overlay.SCENE_OVERLAYS] == [OVERLAY]


def test_an_override_leaves_the_fields_it_does_not_carry() -> None:
    """길이만 고친 장면의 문구가 사라지면 안 된다 — 없는 키는 손대지 않는다."""
    scenes = scenes_with(2.5)

    applied = apply_scene_overrides(
        overriding(project_with(), {"role": "hook", "duration": 1.5}), scenes
    )

    assert applied["scenes"][0]["text"] == "문구"
    assert overlay.SCENE_OVERLAYS not in applied["scenes"][0]


def test_the_overlays_reach_the_filter_list() -> None:
    """렌더와 프리뷰가 같은 함수를 지나므로 둘 다 그린다 (`build_overlays`)."""
    scenes = scenes_with(2.5)
    project = overriding(
        project_with(), {"role": "hook", "overlays": [OVERLAY]}
    )
    applied = apply_scene_overrides(project, scenes)

    filters = video_renderer.build_overlays(
        project, applied, timeline=align(applied)
    )

    assert any("여기 주목" in item for item in filters)


# --- 프레임 경계 정렬 --------------------------------------------------------


def test_scene_starts_come_from_the_frame_counts_not_the_durations() -> None:
    """`duration`을 그대로 누적하면 시작 시각이 프레임 사이에 떨어진다."""
    aligned = align(scenes_with(2.5, 3.237, 3.0), fps=30)

    assert aligned.frames == (75, 97, 90)
    # 두 번째 장면 뒤부터 누계가 갈린다. 2.5 + 3.237 = 5.737이지만 경계는 172/30이다.
    assert aligned.starts == (0.0, 2.5, pytest.approx(172 / 30))
    assert aligned.total_frames == 262
    assert aligned.total_sec == pytest.approx(262 / 30)


def test_every_scene_start_lands_on_a_frame_boundary() -> None:
    aligned = align(scenes_with(1.234, 2.567, 0.891, 3.456), fps=30)

    for start in (*aligned.starts, aligned.total_sec):
        assert (start * 30) == pytest.approx(round(start * 30))


def test_spans_are_contiguous() -> None:
    """장면 끝은 다음 장면 시작과 같다 — 오버레이 구간에 틈이 생기면 배경이 드러난다."""
    aligned = align(scenes_with(1.1, 2.2, 3.3), fps=30)

    spans = [aligned.span(index) for index in range(3)]
    assert [start for start, _ in spans[1:]] == [end for _, end in spans[:-1]]
    assert spans[-1][1] == pytest.approx(aligned.total_sec)


def test_a_scene_shorter_than_one_frame_is_kept_as_one_frame(
    caplog: pytest.LogCaptureFixture
) -> None:
    """0프레임이면 다음 장면과 시작 시각이 같아져 `between(t,a,b)`가 빈 구간이 된다."""
    aligned = align(scenes_with(1.0, 0.01), fps=30)

    assert aligned.frames == (30, 1)
    assert "한 프레임보다 짧다" in caplog.text


def test_align_rejects_a_draft_scene_list() -> None:
    draft = {"schema_version": 1, "type": "quiz", "scenes": [{"role": "hook"}]}

    with pytest.raises(RenderError, match="duration이 없다"):
        align(draft)


# --- 명령 생성 (FFmpeg 없이) -------------------------------------------------


def flags(command: list[str], name: str) -> list[str]:
    return [command[index + 1] for index, item in enumerate(command) if item == name]


def test_the_command_carries_the_spec_from_the_project(tmp_path: Path) -> None:
    """규격을 config에서 다시 읽지 않는다 — 앱이 편집한 값이 그대로 렌더된다 (PRD 7.10)."""
    project = project_with(
        render={"width": 720, "height": 1280, "fps": 24, "output": "custom.mp4"}
    )

    command = build_command(project, run_dir=tmp_path, total_sec=5.0)

    assert "720x1280" in " ".join(command)
    assert flags(command, "-r") == ["24"]
    assert command[-1] == str(tmp_path / "custom.mp4")


def test_the_command_encodes_h264_yuv420p_and_aac(tmp_path: Path) -> None:
    command = build_command(project_with(), run_dir=tmp_path, total_sec=5.0)

    assert flags(command, "-c:v") == ["libx264"]
    assert flags(command, "-pix_fmt") == ["yuv420p"]
    assert flags(command, "-c:a") == ["aac"]
    assert flags(command, "-t")[-1] == "5.000"


def test_a_solid_preset_becomes_a_color_source(tmp_path: Path) -> None:
    command = build_command(project_with(), run_dir=tmp_path, total_sec=5.0)

    assert "color=c=0x101A33:s=1080x1920:r=30" in command


def test_a_gradient_preset_ends_the_ramp_at_height_minus_one_and_stops_rotating(
    tmp_path: Path,
) -> None:
    """`y1`이 1920이면 필터가 회전 기본 동작으로 되돌아가고, `speed`가 기본값이면 그라디언트가
    시간에 따라 돈다 — 둘 다 경고 없이 그림만 달라진다 (D1 확정 스펙 6.2)."""
    project = project_with(background={"kind": "preset", "value": "purple_gradient"})

    command = build_command(project, run_dir=tmp_path, total_sec=5.0)

    source = next(item for item in command if item.startswith("gradients="))
    assert "y1=1919" in source
    assert "speed=0" in source
    assert "c0=0x1B0B2E" in source and "c1=0x4A1052" in source


def test_an_unknown_preset_name_lists_the_bundled_ones(tmp_path: Path) -> None:
    project = project_with(background={"kind": "preset", "value": "sunset"})

    with pytest.raises(RenderError, match="deep_navy"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


def test_a_color_background_takes_the_value_as_is(tmp_path: Path) -> None:
    project = project_with(background={"kind": "color", "value": "#123abc"})

    command = build_command(project, run_dir=tmp_path, total_sec=5.0)

    assert "color=c=0x123ABC:s=1080x1920:r=30" in command


def test_a_malformed_color_is_rejected(tmp_path: Path) -> None:
    project = project_with(background={"kind": "color", "value": "빨강"})

    with pytest.raises(RenderError, match="#RRGGBB"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


@pytest.mark.parametrize(("kind", "name"), [("image", "bg.png"), ("video", "bg.mp4")])
def test_a_file_background_fills_the_canvas_without_distortion(
    tmp_path: Path, kind: str, name: str
) -> None:
    """비율을 유지한 채 넘치는 쪽을 자른다 — 빈 영역이 남지 않는다."""
    source = tmp_path / name
    source.write_text("명령을 만들 때는 파일 내용을 보지 않는다", encoding="utf-8")
    project = project_with(background={"kind": kind, "value": source.name})

    command = build_command(project, run_dir=tmp_path, total_sec=5.0)

    chain = command[command.index("-filter_complex") + 1]
    assert "force_original_aspect_ratio=increase" in chain
    assert "crop=1080:1920" in chain
    assert str(source) in command


def test_a_background_file_outside_the_run_directory_is_taken_as_is(
    tmp_path: Path,
) -> None:
    """사용자 파일은 있는 자리를 가리킨다 — run 디렉터리로 복사하지 않는다 (#80, PRD 14.1)."""
    outside = tmp_path / "사진" / "배경.jpg"
    outside.parent.mkdir()
    outside.write_text("어디에 있든 절대 경로면 그대로 읽는다", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    project = project_with(background={"kind": "image", "value": str(outside)})

    command = build_command(project, run_dir=run_dir, total_sec=5.0)

    assert str(outside) in command


def test_a_missing_background_file_names_the_path(tmp_path: Path) -> None:
    project = project_with(background={"kind": "image", "value": "없는파일.png"})

    with pytest.raises(RenderError, match="없는파일.png"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


@pytest.mark.parametrize(
    ("name", "kind"),
    [("사진.png", "image"), ("PHOTO.JPG", "image"), ("clip.jpeg", "image"), ("clip.mp4", "video")],
)
def test_the_extension_decides_the_background_kind(name: str, kind: str) -> None:
    """대소문자를 가리지 않는다 — `.PNG`는 다른 형식이 아니다 (#80, PRD 14.1)."""
    assert video_renderer.background_kind(name) == kind


@pytest.mark.parametrize("name", ["clip.webm", "사진.gif", "확장자없음"])
def test_an_unsupported_background_file_says_what_is_accepted(name: str) -> None:
    """목록을 화면에 옮기는 것은 앱이지만(#80), 문구는 목록을 소유한 쪽이 만든다."""
    with pytest.raises(RenderError, match=r"\.png, \.jpg, \.jpeg, \.mp4"):
        video_renderer.background_kind(name)


def test_a_background_file_with_an_unsupported_extension_stops_before_ffmpeg(
    tmp_path: Path,
) -> None:
    """손으로 고친 `project.json`도 열린다 — 앱만 막으면 렌더 도중에 실패한다."""
    source = tmp_path / "bg.webm"
    source.write_text("내용은 읽지 않는다", encoding="utf-8")
    project = project_with(background={"kind": "video", "value": source.name})

    with pytest.raises(RenderError, match="받지 않는 형식"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


def test_a_background_file_must_match_the_declared_kind(tmp_path: Path) -> None:
    """`.png`에 `video`가 붙으면 `-stream_loop`가 정지 이미지에 걸려 결과가 조용히 달라진다."""
    source = tmp_path / "bg.png"
    source.write_text("내용은 읽지 않는다", encoding="utf-8")
    project = project_with(background={"kind": "video", "value": source.name})

    with pytest.raises(RenderError, match="image"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


def test_an_unknown_background_kind_lists_the_supported_ones(tmp_path: Path) -> None:
    project = project_with(background={"kind": "webcam", "value": "0"})

    with pytest.raises(RenderError, match="preset"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


# --- 배경 모션 (#34) ---------------------------------------------------------


def with_background(tmp_path: Path, kind: str, motion: Any = ..., **spec: Any) -> dict[str, Any]:
    """배경 하나를 갈아 끼운 프로젝트. 파일 배경이면 그 파일도 만든다.

    `motion`을 주지 않으면 `background`에 그 키가 아예 없다 — 이 필드가 생기기 전에 만들어진
    run 디렉터리의 모양이다.
    """
    value = {
        "image": "bg.png",
        "video": "bg.mp4",
        "preset": "deep_navy",
        "color": "#123abc",
    }[kind]
    if kind in ("image", "video"):
        (tmp_path / value).write_text("명령을 만들 때는 파일 내용을 보지 않는다", encoding="utf-8")
    background: dict[str, Any] = {"kind": kind, "value": value}
    if motion is not ...:
        background["motion"] = motion
    project = project_with(background=background)
    if spec:
        project["render"] = project["render"] | spec
    return project


def video_chain(command: list[str]) -> str:
    """`[0:v]`에 걸린 필터 체인. 오디오 쪽은 보지 않는다."""
    graph = command[command.index("-filter_complex") + 1]
    return next(step for step in graph.split(";") if step.startswith("[0:v]"))


@pytest.mark.parametrize("kind", ["image", "video"])
def test_motion_rides_on_the_background_chain_before_the_overlays(
    tmp_path: Path, kind: str
) -> None:
    """오버레이는 모션 **뒤**에 붙는다 — 그래서 자막이 배경과 함께 움직이지 않는다."""
    project = with_background(tmp_path, kind, {"kind": "zoom_in", "strength": 0.08})

    command = build_command(
        project, run_dir=tmp_path, total_sec=5.0, overlays=["drawtext=text='문구'"]
    )

    chain = video_chain(command)
    assert chain.index("crop=1080:1920") < chain.index("zoompan=") < chain.index("drawtext")


def test_motion_names_the_canvas_the_fps_and_the_duration(tmp_path: Path) -> None:
    """**세 기본값이 함정이다.** `s`가 없으면 배경이 1280x720으로 나와 오버레이 좌표가 전부
    어긋나고, `fps` 기본값은 25(우리는 30), `d` 기본값 90은 입력 프레임 하나를 90프레임으로
    늘려 `-loop 1` 이미지에서 3초 주기로 같은 줌이 반복된다.

    셋 다 `project.json`의 값을 따른다 — 규격을 앱이 바꾸면 모션도 따라와야 한다.
    """
    project = with_background(
        tmp_path, "image", {"kind": "zoom_in", "strength": 0.08},
        width=720, height=1280, fps=24,
    )

    chain = video_chain(build_command(project, run_dir=tmp_path, total_sec=5.0))

    assert "s=720x1280" in chain
    assert ":fps=24" in chain
    assert ":d=1:" in chain


def test_the_motion_progress_ends_on_the_last_frame(tmp_path: Path) -> None:
    """**`on`은 출력 프레임 번호이고 0부터 센다**(실측). 프레임 수로 나누면 마지막 프레임이
    목표 배율에 닿지 못하므로 프레임 수 - 1로 나눈다.

    쉼표는 필터 인자 구분자라 이스케이프한다 (`build_preview_command`의 `select`와 같다).
    """
    project = with_background(tmp_path, "image", {"kind": "zoom_in", "strength": 0.08})

    chain = video_chain(build_command(project, run_dir=tmp_path, total_sec=5.0))

    # 5.000초 × 30fps = 150프레임, 마지막 프레임 번호는 149다.
    assert r"min(on/149\,1)" in chain


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("zoom_in", r"z='1+0.5*min(on/149\,1)'"),
        ("zoom_out", r"z='1+0.5*(1-min(on/149\,1))'"),
        # 팬은 배율을 고정하고 확대로 생긴 여백 안에서 움직인다.
        ("pan_left", r"z='1.5':x='(iw-iw/zoom)*(1-min(on/149\,1))'"),
        ("pan_right", r"z='1.5':x='(iw-iw/zoom)*min(on/149\,1)'"),
        ("pan_up", r"y='(ih-ih/zoom)*(1-min(on/149\,1))'"),
        ("pan_down", r"y='(ih-ih/zoom)*min(on/149\,1)'"),
    ],
)
def test_each_motion_keeps_the_zoom_at_or_above_one(
    tmp_path: Path, kind: str, expected: str
) -> None:
    """**1 아래로 내려가면 크롭 창이 캔버스보다 커져 배경 경계가 프레임에 들어온다.**

    이름 하나가 방향까지 정하므로 표현식도 이름마다 하나다 (`_motion_expressions`).
    """
    project = with_background(tmp_path, "image", {"kind": kind, "strength": 0.5})

    assert expected in video_chain(build_command(project, run_dir=tmp_path, total_sec=5.0))


@pytest.mark.parametrize("kind", ["preset", "color"])
def test_a_preset_or_color_background_gets_no_motion_filter(
    tmp_path: Path, kind: str
) -> None:
    """**값을 무시하는 것이 아니라 결과가 같다.** zoom/pan은 공간 변화를 옮기는 것이라 단색에서
    프레임이 완전히 동일하고, 필터를 하나 더 지나며 렌더 시간만 늘 자리다.

    명령이 모션 필드가 없을 때와 **정확히 같다** — 기본 배경으로 도는 CLI 경로가 이쪽이다.
    """
    moving = with_background(tmp_path, kind, {"kind": "pan_right", "strength": 0.5})
    still = with_background(tmp_path, kind)

    assert build_command(moving, run_dir=tmp_path, total_sec=5.0) == build_command(
        still, run_dir=tmp_path, total_sec=5.0
    )


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize(
    "motion",
    [
        ...,
        None,
        {"kind": "none", "strength": 0.08},
        {"kind": "zoom_in", "strength": 0.0},
    ],
)
def test_motion_off_leaves_the_command_as_it_was(
    tmp_path: Path, kind: str, motion: Any
) -> None:
    """모션이 없는 네 모양 — 필드 없음(옛 run 디렉터리) · null · `none` · 강도 0.

    필터 단계가 하나도 붙지 않아야 한다. 빈 단계를 이어 붙이면 `,,`가 생겨 그래프가 깨진다.
    """
    project = with_background(tmp_path, kind, motion)

    chain = video_chain(build_command(project, run_dir=tmp_path, total_sec=5.0))

    assert "zoompan" not in chain
    assert ",," not in chain


def test_an_unknown_motion_kind_lists_the_supported_ones(tmp_path: Path) -> None:
    project = with_background(tmp_path, "image", {"kind": "ken_burns", "strength": 0.08})

    with pytest.raises(RenderError, match="zoom_in"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


@pytest.mark.parametrize("strength", [-0.1, 1.5, "0.08", True, None])
def test_a_motion_strength_outside_the_contract_stops_before_ffmpeg(
    tmp_path: Path, strength: Any
) -> None:
    """상한이 있는 것이 다른 게인 값과 갈리는 점이다 — `zoompan`은 배율을 10에서 조용히
    자르므로, 넘긴 값을 통과시키면 파일의 값과 그림이 갈린다."""
    project = with_background(tmp_path, "image", {"kind": "zoom_in", "strength": strength})

    with pytest.raises(RenderError, match="strength"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


def test_a_bad_motion_value_is_rejected_even_where_it_would_not_apply(
    tmp_path: Path,
) -> None:
    """**배경 종류가 판정을 미루지 않는다.** 프리셋 배경에서 통과시키면 그 값은 앱에서 배경을
    파일로 바꾸는 순간에야 터지고, 그때 원인은 배경 교체처럼 보인다."""
    project = with_background(tmp_path, "preset", {"kind": "ken_burns", "strength": 0.08})

    with pytest.raises(RenderError, match="ken_burns"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


def test_the_preview_command_carries_the_motion(tmp_path: Path) -> None:
    """프리뷰도 `_video_stage`를 지난다 (#27). 여기 없으면 정지 프레임이 렌더와 다른 그림이다."""
    project = with_background(tmp_path, "image", {"kind": "zoom_in", "strength": 0.08})

    command = video_renderer.build_preview_command(
        project, run_dir=tmp_path, total_sec=5.0, frames=[10], out_dir=tmp_path
    )

    assert "zoompan=" in video_chain(command)


def test_the_voice_track_is_the_audio_input_when_it_exists(tmp_path: Path) -> None:
    (tmp_path / "voice.mp3").write_bytes(b"audio")
    project = project_with(audio={"voice": "voice.mp3", "music": None, "sfx_volume": 1.0})

    command = build_command(project, run_dir=tmp_path, total_sec=5.0)

    assert str(tmp_path / "voice.mp3") in command
    assert "anullsrc" not in " ".join(command)


def test_without_a_voice_track_the_audio_input_is_silence(tmp_path: Path) -> None:
    """#23이 효과음을 얹을 때 오디오 스트림이 항상 하나 있다고 전제할 수 있어야 한다."""
    command = build_command(project_with(), run_dir=tmp_path, total_sec=5.0)

    assert any(item.startswith("anullsrc=") for item in command)
    assert flags(command, "-map") == ["[video]", "[audio]"]


def test_the_audio_is_padded_so_a_short_track_does_not_cut_the_video(
    tmp_path: Path,
) -> None:
    """합성 트랙은 프레임 정렬 길이보다 반 프레임쯤 짧을 수 있다."""
    (tmp_path / "voice.mp3").write_bytes(b"audio")
    project = project_with(audio={"voice": "voice.mp3", "music": None, "sfx_volume": 1.0})

    command = build_command(project, run_dir=tmp_path, total_sec=5.0)

    assert "apad" in command[command.index("-filter_complex") + 1]


def test_the_sfx_inputs_come_after_the_voice_input(tmp_path: Path) -> None:
    """효과음 입력 인덱스는 `audio_mix`가 매긴다 (#23). 순서가 어긋나면 낭독이 지연된다."""
    (tmp_path / "voice.mp3").write_bytes(b"audio")
    project = project_with(audio={"voice": "voice.mp3", "music": None, "sfx_volume": 1.0})
    chain = AudioChain(steps=("[1:a]anull[audio]",), inputs=("-i", "beep.mp3"))

    command = build_command(project, run_dir=tmp_path, total_sec=5.0, audio=chain)

    # 입력 0은 배경이므로 뒤 두 개를 본다 — 낭독이 1, 효과음이 2다.
    assert flags(command, "-i")[-2:] == [str(tmp_path / "voice.mp3"), "beep.mp3"]
    assert "[1:a]anull[audio]" in command[command.index("-filter_complex") + 1]


def test_overlays_ride_on_the_background_chain(tmp_path: Path) -> None:
    """오버레이(#20~#22)는 배경 체인 뒤에 이어 붙는다. 순서가 그리는 순서다."""
    command = build_command(
        project_with(),
        run_dir=tmp_path,
        total_sec=5.0,
        overlays=["drawtext=text='하나'", "drawtext=text='둘'"],
    )

    chain = command[command.index("-filter_complex") + 1]
    assert "[0:v]setsar=1,drawtext=text='하나',drawtext=text='둘'[video]" in chain


def test_without_overlays_only_the_background_is_drawn(tmp_path: Path) -> None:
    """#19까지의 상태다. 문구가 하나도 없는 장면 목록도 정상이다."""
    command = build_command(project_with(), run_dir=tmp_path, total_sec=5.0)

    assert "[0:v]setsar=1[video]" in command[command.index("-filter_complex") + 1]


def test_a_bad_overlay_setting_becomes_a_render_error(tmp_path: Path) -> None:
    """**인코딩을 시작하기 전에 멈춘다.** 부르는 쪽(main)이 잡는 예외가 하나로 유지되도록
    `OverlayError`를 `RenderError`로 옮긴다."""
    project = project_with()
    project["render"] = project["render"] | {"caption_style": "없는_스타일"}

    with pytest.raises(RenderError, match="impact_yellow"):
        video_renderer.build_overlays(
            project, scenes_with(1.0), timeline=align(scenes_with(1.0))
        )


def test_a_missing_overlay_field_names_the_key(tmp_path: Path) -> None:
    """앱이 만든 프로젝트나 사람이 편집한 파일이 스키마를 지나지 않고 직접 들어올 수 있다."""
    project = project_with()
    del project["render"]["cta_punch"]

    with pytest.raises(RenderError, match="render.cta_punch"):
        video_renderer.build_overlays(
            project, scenes_with(1.0), timeline=align(scenes_with(1.0))
        )


def test_a_broken_render_section_is_rejected(tmp_path: Path) -> None:
    project = project_with(
        render={"width": 1080, "height": 1920, "fps": 0, "output": OUTPUT_NAME}
    )

    with pytest.raises(RenderError, match="render.fps"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


def test_render_rejects_a_draft_scene_list(tmp_path: Path) -> None:
    """초안으로 렌더하면 영상 길이가 `voice.mp3`와 어긋난다."""
    draft = {"schema_version": 1, "type": "quiz", "scenes": [{"role": "hook"}]}

    with pytest.raises(SchemaError):
        render(project_with(), draft, run_dir=tmp_path)


class FakeProcess:
    """`subprocess.Popen`을 대신하는 최소 구현 (#30).

    **진짜 ffmpeg를 부르지 않는 실패 경로를 여기서 밟는다.** `render`가 이제 `Popen`을 직접
    쓰므로 `run`을 바꿔 끼우던 자리가 이것으로 바뀌었고, 확인하는 것은 그대로다 — 종료 코드,
    stderr, 상한을 넘긴 프로세스를 죽이는지.
    """

    def __init__(
        self, *, code: int = 0, progress: str = "", stderr: str = "", hang: bool = False
    ) -> None:
        self.stdout = io.StringIO(progress)
        self.returncode = code
        self._stderr = stderr
        self._hang = hang
        self.killed = False
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self._hang and not self.killed:
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)
        return self.returncode

    def poll(self) -> int | None:
        return None if (self._hang and not self.killed) else self.returncode

    def kill(self) -> None:
        self.killed = True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeProcess:
        def spawn(command: list[str], **kwargs: Any) -> FakeProcess:
            # stderr 파일에 쓰는 것은 진짜 ffmpeg의 몫이므로 여기서 대신 쓴다.
            kwargs["stderr"].write(self._stderr)
            self.spawn(command)
            return self

        monkeypatch.setattr(video_renderer.subprocess, "Popen", spawn)
        return self

    def spawn(self, command: list[str]) -> None:
        """목적지 파일을 만든다. **성공한 ffmpeg는 파일을 남긴다** (#36).

        `render`가 그 파일을 최종 이름으로 바꿔 끼우므로, 만들지 않는 대역은 "성공했는데
        결과물이 없다"는 실제로 있을 수 없는 상태를 흉내 내게 된다. 목적지는 명령의 마지막
        인자다 (`build_command`) — **최종 경로가 아니라 임시 경로다.**
        """
        if self.returncode == 0 and not self._hang:
            Path(command[-1]).write_bytes(b"fake-video")


def test_a_missing_ffmpeg_says_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError(2, "그런 파일이 없다")

    monkeypatch.setattr(video_renderer.subprocess, "Popen", missing)

    with pytest.raises(RenderError, match="ffmpeg를 찾을 수 없다"):
        render(project_with(), scenes_with(1.0), run_dir=tmp_path)


def test_a_failing_ffmpeg_carries_the_exit_code_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """명령 전문과 stderr가 run.log에 남아야 실패한 렌더를 손으로 재현할 수 있다.

    **stderr는 메시지가 아니라 `raw`로 온다** (#30). 앱의 실패 카드가 사람이 읽는 원인과
    원문을 다르게 그리므로 한 문자열에 섞지 않는다 (D2 확정 스펙 3.3).
    """
    FakeProcess(code=1, stderr="Invalid argument").install(monkeypatch)

    with caplog.at_level("DEBUG"):
        with pytest.raises(RenderError, match="종료 코드 1") as failure:
            render(project_with(), scenes_with(1.0), run_dir=tmp_path)

    assert failure.value.raw == "Invalid argument"
    assert OUTPUT_NAME in str(failure.value)
    assert "렌더 명령 ffmpeg" in caplog.text
    assert "Invalid argument" in caplog.text


def test_the_render_writes_to_a_staging_file_and_swaps_it_in(tmp_path: Path) -> None:
    """**제자리에 쓰지 않는다** (#36).

    명령이 받는 목적지는 임시 파일이고 확장자가 같아야 한다 — FFmpeg는 출력 형식을 확장자로
    정하므로 뒤에 붙이면 "Unable to find a suitable output format"으로 실패한다.
    """
    seen: list[list[str]] = []

    class Recorder(FakeProcess):
        def install_recording(self, monkeypatch: pytest.MonkeyPatch) -> None:
            def spawn(command: list[str], **kwargs: Any) -> FakeProcess:
                seen.append(command)
                self.spawn(command)
                return self

            monkeypatch.setattr(video_renderer.subprocess, "Popen", spawn)

    with pytest.MonkeyPatch.context() as patch:
        Recorder().install_recording(patch)
        output = render(project_with(), scenes_with(1.0), run_dir=tmp_path)

    destination = Path(seen[0][-1])
    assert destination != output and destination.suffix == ".mp4"
    assert destination.parent == tmp_path  # `os.replace`는 볼륨을 넘지 못한다
    # 바꿔 끼운 뒤에는 임시 파일이 남지 않는다.
    assert output.is_file() and list(tmp_path.glob("*.mp4")) == [output]


@pytest.mark.parametrize("failure", ["exit_code", "timeout"])
def test_a_failed_render_leaves_the_previous_output_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """#36 완료 조건 — 잘린 `final_short.mp4`가 남지 않고, 이전 성공본은 그대로다.

    **부분 산출물은 성공한 결과물과 구분되지 않는다.** 이어 돌리기가 "산출물이 있으니
    건너뛴다"로 판단하므로, 잘린 파일이 남으면 그 판단 자체가 틀린 답을 낸다.
    """
    previous = tmp_path / OUTPUT_NAME
    previous.write_bytes(b"previous-success")
    if failure == "timeout":
        FakeProcess(hang=True).install(monkeypatch)
    else:
        FakeProcess(code=1, stderr="Invalid argument").install(monkeypatch)

    with pytest.raises(RenderError):
        render(project_with(), scenes_with(1.0), run_dir=tmp_path)

    assert previous.read_bytes() == b"previous-success"
    assert list(tmp_path.glob("*.mp4")) == [previous]


def test_a_timeout_names_the_output_and_kills_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**상한을 넘긴 프로세스를 죽인다** — 남겨 두면 인코딩이 계속 돈다 (#30)."""
    process = FakeProcess(hang=True).install(monkeypatch)

    with pytest.raises(RenderError, match=OUTPUT_NAME):
        render(project_with(), scenes_with(1.0), run_dir=tmp_path)

    assert process.killed
    assert process.waits[0] == video_renderer.FFMPEG_TIMEOUT_SEC


def test_progress_reports_frames_and_the_scene_they_belong_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`-progress` 줄 → `RenderProgress` (#30).

    **퍼센트도 남은 시간도 없다.** 진행 프레임과 총 프레임, 그리고 그 프레임이 속한 장면이
    전부이고 나머지는 화면의 계산이다 (D2 확정 스펙 3.3).
    """
    # 1초 + 1초 = 30프레임씩 두 장면. ffmpeg는 묶음마다 `frame=`을 낸다.
    lines = "frame=1\nfps=0\nprogress=continue\nframe=31\nfps=30\nprogress=end\n"
    FakeProcess(progress=lines).install(monkeypatch)

    seen: list[video_renderer.RenderProgress] = []
    render(
        project_with(), scenes_with(1.0, 1.0), run_dir=tmp_path, on_progress=seen.append
    )

    assert [(item.frame, item.scene_index) for item in seen] == [(1, 0), (31, 1)]
    assert {item.total_frames for item in seen} == {60}


def test_a_running_render_is_registered_so_it_can_be_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**앱 백엔드가 끝날 때 자식 ffmpeg를 죽이려면 그 프로세스를 들고 있어야 한다** (#30).

    렌더 스레드는 daemon이라 그냥 사라지고, 남은 ffmpeg는 사용자가 앱을 닫은 뒤에
    `final_short.mp4`를 완성한다. 끝난 뒤에는 집합이 비어야 `kill_active()`의 개수가
    거짓말을 하지 않는다.
    """
    seen: list[int] = []
    FakeProcess(progress="frame=1\n").install(monkeypatch)

    render(
        project_with(),
        scenes_with(1.0),
        run_dir=tmp_path,
        on_progress=lambda _progress: seen.append(len(video_renderer._ACTIVE)),
    )

    assert seen == [1]
    assert len(video_renderer._ACTIVE) == 0


def test_kill_active_kills_what_is_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**임시 파일도 함께 치운다** (#36). 렌더 중에 앱을 닫는 것은 정상 사용이라, 치우지
    않으면 run 디렉터리에 임시 파일이 쌓인다 — 최종 파일은 성공했을 때만 놓이므로 잘린
    `final_short.mp4`가 생기지는 않는다.
    """
    running, finished = FakeProcess(hang=True), FakeProcess(code=0)
    staged = {
        running: tmp_path / "final_short.tmp-1.mp4",
        finished: tmp_path / "final_short.tmp-2.mp4",
    }
    for path in staged.values():
        path.write_bytes(b"partial-mp4")
    monkeypatch.setattr(video_renderer, "_ACTIVE", staged)

    assert video_renderer.kill_active() == 2
    assert running.killed
    # 이미 끝난 프로세스에는 `kill`을 보내지 않는다 — 그 pid가 재사용됐을 수 있다.
    assert not finished.killed
    assert not list(tmp_path.glob("*.mp4"))


def test_the_render_command_asks_ffmpeg_for_progress(tmp_path: Path) -> None:
    """훅이 없어도 붙는다 — CLI와 앱이 같은 명령으로 돈다 (#30)."""
    seen: list[list[str]] = []

    class Recorder(FakeProcess):
        def install_recording(self, monkeypatch: pytest.MonkeyPatch) -> None:
            def spawn(command: list[str], **kwargs: Any) -> FakeProcess:
                seen.append(command)
                self.spawn(command)
                return self

            monkeypatch.setattr(video_renderer.subprocess, "Popen", spawn)

    with pytest.MonkeyPatch.context() as patch:
        Recorder().install_recording(patch)
        render(project_with(), scenes_with(1.0), run_dir=tmp_path)

    assert seen[0][:3] == ["ffmpeg", "-progress", "pipe:1"]


# --- 실제 렌더 (FFmpeg 필요) -------------------------------------------------


def probe(path: Path, entries: str, *extra: str) -> Any:
    """`ffprobe` 한 번. JSON으로 받아 값을 그대로 돌려준다."""
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries,
         "-of", "json", *extra, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def duration_of(path: Path) -> float:
    return float(probe(path, "format=duration")["format"]["duration"])


def voice_track(run_dir: Path, *, offset_sec: float, total_sec: float) -> Path:
    """오프셋 하나에 짧은 톤을 놓은 합성 트랙. **진짜 `timeline`이 만든다** — 영상 안의
    낭독 시작 시각이 `narration_offset`과 맞는지 보려면 배치 경로가 실물이어야 한다."""
    segment = run_dir / "audio" / "seg-001.mp3"
    segment.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", "0.3", "-i",
         "sine=frequency=880:sample_rate=44100", str(segment)],
        check=True,
        capture_output=True,
    )
    track = run_dir / "voice.mp3"
    timeline.mix_voice_track(
        [timeline.Placement(audio=segment, offset_sec=offset_sec)], track, total_sec
    )
    return track


def first_frame(path: Path, width: int, height: int) -> bytes:
    """첫 프레임을 RGB 원본으로. 배경이 캔버스를 채웠는지 픽셀에서 본다."""
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
        check=True,
    )
    assert len(completed.stdout) == width * height * 3
    return completed.stdout


@needs_ffmpeg
def test_the_output_meets_the_format_spec(tmp_path: Path) -> None:
    """1080x1920 / 30fps / H.264(yuv420p) + AAC (PRD 6.3)."""
    scenes = scenes_with(1.0, 0.5)

    output = render(project_with(), scenes, run_dir=tmp_path)

    streams = probe(
        output, "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt"
    )
    video = next(s for s in streams["streams"] if s["codec_type"] == "video")
    audio = [s for s in streams["streams"] if s["codec_type"] == "audio"]

    assert (video["width"], video["height"]) == (CANVAS_WIDTH, CANVAS_HEIGHT)
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    assert video["r_frame_rate"] == "30/1"
    # 낭독 장면이 없는 입력에서도 정확히 하나다 (#23이 전제한다).
    assert len(audio) == 1
    assert audio[0]["codec_name"] == "aac"


@needs_ffmpeg
def test_the_output_length_matches_the_frame_aligned_total(tmp_path: Path) -> None:
    scenes = scenes_with(2.5, 1.237, 0.891)

    output = render(project_with(), scenes, run_dir=tmp_path)

    expected = align(scenes).total_sec
    assert duration_of(output) == pytest.approx(expected, abs=1 / FPS)


@needs_ffmpeg
def test_an_override_changes_the_rendered_length(tmp_path: Path) -> None:
    """사람이 얹은 길이가 최종 mp4에 도달한다 (#82의 완료 조건)."""
    scenes = scenes_with(2.5, 1.237)
    scenes["scenes"][1]["role"] = "cta"
    project = overriding(project_with(), {"role": "cta", "duration": 3.0})

    output = render(project, scenes, run_dir=tmp_path)

    expected = align(apply_scene_overrides(project, scenes)).total_sec
    assert expected == pytest.approx(5.5)
    assert duration_of(output) == pytest.approx(expected, abs=1 / FPS)


@needs_ffmpeg
def test_the_narration_starts_at_its_recorded_offset(tmp_path: Path) -> None:
    """자막(#17)과 오버레이(#20~)가 같은 오프셋을 쓰므로 영상 안에서도 맞아야 한다."""
    scenes = scenes_with(1.0, 1.5, 1.0, narrated=(1,))
    total = align(scenes).total_sec
    offset = scenes["scenes"][1]["narration_offset"]
    voice_track(tmp_path, offset_sec=offset, total_sec=total)
    project = project_with(audio={"voice": "voice.mp3", "music": None, "sfx_volume": 1.0})

    output = render(project, scenes, run_dir=tmp_path)

    detected = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(output), "-af",
         "silencedetect=noise=-40dB:duration=0.1", "-f", "null", "-"],
        capture_output=True,
        text=True,
    ).stderr
    onset = float(
        next(line for line in detected.splitlines() if "silence_end" in line)
        .split("silence_end:")[1]
        .split("|")[0]
    )
    assert onset == pytest.approx(offset, abs=1 / FPS)
    assert len(probe(output, "stream=codec_type")["streams"]) == 2


@needs_ffmpeg
def test_an_image_background_leaves_no_empty_area(tmp_path: Path) -> None:
    """가로로 긴 이미지를 세로 캔버스에 채운다. 비율을 무시하고 늘리면 가운데 띠 바깥이
    보이고, 비율을 유지한 채 맞추기만 하면 위아래에 검은 영역이 남는다."""
    background = tmp_path / "wide.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=1920x360",
         "-vf", "drawbox=x=816:y=0:w=288:h=360:color=green:t=fill",
         "-frames:v", "1", str(background)],
        check=True,
        capture_output=True,
    )
    project = project_with(background={"kind": "image", "value": background.name})

    output = render(project, scenes_with(0.5), run_dir=tmp_path)

    frame = first_frame(output, CANVAS_WIDTH, CANVAS_HEIGHT)
    corners = [
        (0, 0),
        (CANVAS_WIDTH - 1, 0),
        (0, CANVAS_HEIGHT - 1),
        (CANVAS_WIDTH - 1, CANVAS_HEIGHT - 1),
        (CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2),
    ]
    for x, y in corners:
        offset = (y * CANVAS_WIDTH + x) * 3
        red, green, blue = frame[offset : offset + 3]
        # `color=green`은 #008000이다. yuv420p 왕복 오차를 감안해 넉넉히 본다.
        assert green > 100 and red < 60 and blue < 60, f"({x},{y})가 배경 가운데가 아니다"


def frame_stats(command: list[str], key: str) -> list[float]:
    """`signalstats` 값을 프레임 순서대로. 필터가 `metadata=print`로 내보낸 줄을 읽는다."""
    reported = subprocess.run(
        [*command, "-f", "null", "-"], capture_output=True, text=True, check=True
    ).stderr
    values = [
        float(line.split("=")[1])
        for line in reported.splitlines()
        if f"signalstats.{key}" in line
    ]
    assert values, f"{key} 통계가 나오지 않았다"
    return values


def frame_gap(path: Path, first: int, second: int) -> float:
    """떨어진 두 프레임의 평균 휘도 차 — 0에 가까우면 같은 그림이다.

    **바이트 비교를 쓰지 않는다.** H.264는 손실 압축이라 같은 그림도 프레임 위치에 따라 몇
    단위씩 다르게 디코드된다 — 정지 배경에서도 바이트가 갈린다 (실측 차이 0.005).
    """
    return frame_stats(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf",
         rf"select=eq(n\,{first})+eq(n\,{second}),tblend=all_mode=difference,"
         "signalstats,metadata=print:key=lavfi.signalstats.YAVG"],
        "YAVG",
    )[-1]


def frame_mismatch(first: Path, second: Path, index: int) -> float:
    """두 영상의 같은 번호 프레임에서 가장 크게 어긋난 픽셀의 휘도 차.

    **평균이 아니라 최대다.** 그림 하나가 몇 픽셀 밀린 것은 넓이가 작아 평균에 묻히지만
    경계에서는 값이 크게 벌어진다 — 실측에서 오버레이를 24px 옮기면 216, 같은 자리면 13이었다.
    """
    return frame_stats(
        ["ffmpeg", "-hide_banner", "-i", str(first), "-i", str(second), "-filter_complex",
         rf"[0:v]select=eq(n\,{index})[a];[1:v]select=eq(n\,{index})[b];"
         "[a][b]blend=all_mode=difference,signalstats,"
         "metadata=print:key=lavfi.signalstats.YMAX"],
        "YMAX",
    )[-1]


def luma_floor(path: Path) -> float:
    """영상 **전 프레임**의 최소 휘도. 프레임 하나만 보면 경계가 드러나지 않는다 —
    `zoompan`이 캔버스를 못 채우는 순간은 모션 도중이다."""
    return min(
        frame_stats(
            ["ffmpeg", "-hide_banner", "-i", str(path), "-vf",
             "signalstats,metadata=print:key=lavfi.signalstats.YMIN"],
            "YMIN",
        )
    )


def render_background_only(
    project: dict[str, Any], *, run_dir: Path, total_sec: float
) -> Path:
    """오버레이 없이 배경 체인만 렌더한다.

    **번인 텍스트가 화면의 가장 어두운 픽셀이다** (검은 외곽선, D1 확정 스펙 2.1). 배경이
    캔버스를 채웠는지 휘도로 보려면 그 글자가 없어야 한다 — 여기서 확인하는 것은 배경 체인
    하나이고, 오버레이가 모션과 무관하다는 것은 아래 별 테스트가 지킨다.
    """
    output = run_dir / "background-only.mp4"
    command = build_command(
        project, run_dir=run_dir, total_sec=total_sec, destination=output
    )
    subprocess.run(command, check=True, capture_output=True)
    return output


def canvas_image(path: Path, *, color: str, size: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}",
         "-frames:v", "1", str(path)],
        check=True,
        capture_output=True,
    )


def moving(project: dict[str, Any], kind: str, strength: float) -> dict[str, Any]:
    return project | {"background": project["background"] | {
        "motion": {"kind": kind, "strength": strength}
    }}


@needs_ffmpeg
def test_motion_moves_the_pixels_and_no_motion_leaves_them_still(tmp_path: Path) -> None:
    """**서로 떨어진 두 프레임을 본다.** 이웃한 두 프레임은 같을 수 있다 — `zoompan`의 크롭
    창이 정수 픽셀이라 움직임의 실질 갱신률이 fps가 아니라 강도에서 나온다 (#34 실측).
    """
    background = tmp_path / "bg.png"
    # 세부가 있는 그림이어야 공간 변화가 픽셀에 남는다. 단색에서는 모션이 붙어도 결과가 같다.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920",
         "-frames:v", "1", str(background)],
        check=True,
        capture_output=True,
    )
    still = project_with(background={"kind": "image", "value": background.name})
    scenes = scenes_with(1.0)
    last = align(scenes).total_frames - 1

    moved = render(moving(still, "zoom_in", 0.5), scenes, run_dir=tmp_path)
    assert frame_gap(moved, 0, last) > 5

    # 모션이 꺼져 있으면 같은 그림이다. 이 확인이 없으면 위 차이가 인코딩 잡음일 수 있다.
    assert frame_gap(render(still, scenes, run_dir=tmp_path), 0, last) < 0.1


@needs_ffmpeg
@pytest.mark.parametrize("kind", ["zoom_in", "zoom_out", "pan_right", "pan_up"])
def test_a_moving_background_leaves_no_empty_area_and_keeps_the_spec(
    tmp_path: Path, kind: str
) -> None:
    """**최대 강도로 본다.** 배율이 1 아래로 내려가거나 팬이 여백을 넘으면 캔버스를 못 채운
    영역이 검게 남는다 — 캔버스보다 큰 그림을 채워 넣었으므로 어느 프레임에도 있을 수 없다.

    규격도 함께 본다. `zoompan`의 `s` 기본값(`hd720`)에 걸리면 여기서 드러난다.
    """
    background = tmp_path / "bg.png"
    canvas_image(background, color="green", size="1600x900")
    project = moving(
        project_with(background={"kind": "image", "value": background.name}), kind, 1.0
    )

    output = render_background_only(project, run_dir=tmp_path, total_sec=1.0)

    # `color=green`(#008000)의 휘도는 80 근처다. 검은 영역이 한 프레임에라도 있으면 내려간다.
    assert luma_floor(output) > 40
    video = next(
        stream
        for stream in probe(output, "stream=codec_type,width,height,r_frame_rate")["streams"]
        if stream["codec_type"] == "video"
    )
    assert (video["width"], video["height"]) == (CANVAS_WIDTH, CANVAS_HEIGHT)
    assert video["r_frame_rate"] == "30/1"


@needs_ffmpeg
def test_the_overlays_land_in_the_same_place_with_and_without_motion(
    tmp_path: Path,
) -> None:
    """모션은 배경 체인 안에 있고 오버레이는 그 뒤에 붙는다 — 그림이 갈릴 자리가 없다.

    **단색 배경으로 본다.** 모션이 옮기는 것은 배경의 공간 변화뿐이라 단색에서는 두 렌더의
    프레임이 같아야 하고, 어긋난다면 그 차이는 자막이 함께 움직였다는 뜻이다.
    """
    background = tmp_path / "bg.png"
    canvas_image(background, color="0x1B0B2E", size="1080x1920")
    still = project_with(background={"kind": "image", "value": background.name})
    scenes = scenes_with(1.0)
    middle = align(scenes).total_frames // 2

    with_motion = render(moving(still, "pan_right", 0.5), scenes, run_dir=tmp_path)
    # 같은 이름에 쓰지 않는다 — 뒤 렌더가 앞 결과를 덮으면 비교할 것이 없다.
    (kept := tmp_path / "moving.mp4").write_bytes(with_motion.read_bytes())
    without = render(still, scenes, run_dir=tmp_path)

    # 같은 자리면 13, 24px 밀리면 216이었다 (실측). 40은 그 사이다.
    assert frame_mismatch(kept, without, middle) < 40


@needs_ffmpeg
def test_rendering_twice_gives_the_same_spec_and_length(tmp_path: Path) -> None:
    """바이트 동일성은 요구하지 않는다 — 인코더가 결정적이지 않다."""
    scenes = scenes_with(1.0, 0.767)

    first = duration_of(render(project_with(), scenes, run_dir=tmp_path))
    second = duration_of(render(project_with(), scenes, run_dir=tmp_path))

    assert first == pytest.approx(second, abs=1e-6)


# --- 프리뷰 (#27) -----------------------------------------------------------


def preview_chain(command: list[str]) -> str:
    return command[command.index("-filter_complex") + 1]


def test_the_preview_command_carries_no_audio_at_all(tmp_path: Path) -> None:
    """**`-map [audio]`만 빼는 것으로는 안 된다** — `alimiter`의 출력이 어디에도 연결되지
    않아 그래프 바인딩이 실패한다 (스파이크 #25 6.1). 오디오는 처음부터 만들지 않는다."""
    command = video_renderer.build_preview_command(
        project_with(), run_dir=tmp_path, total_sec=5.0, frames=[10], out_dir=tmp_path
    )

    assert "[audio]" not in " ".join(command)
    assert "-c:a" not in command
    assert "anullsrc" not in " ".join(command)
    # 입력은 배경 하나뿐이다.
    assert command.count("-i") == 1


def test_the_preview_command_writes_png_not_h264(tmp_path: Path) -> None:
    """`-c:v libx264`가 남은 채 확장자만 `.png`로 주면 **경고 없이 H.264가 그 파일에
    쓰인다** (스파이크 #25 6.1). 인코더를 명시해 그 경로를 없앤다."""
    command = video_renderer.build_preview_command(
        project_with(), run_dir=tmp_path, total_sec=5.0, frames=[10], out_dir=tmp_path
    )

    assert "libx264" not in command
    assert command[command.index("-c:v") + 1] == "png"
    assert command[-1].endswith(".png")
    # 최종 산출물 이름이 이 명령에 없다 — 프리뷰가 렌더 결과를 덮어쓸 수 없다.
    assert OUTPUT_NAME not in " ".join(command)


def test_the_preview_shares_the_video_chain_with_the_final_render(tmp_path: Path) -> None:
    """**두 명령이 같은 그림을 내는 근거다.** 배경과 오버레이가 한 함수를 지나므로, 프리뷰만
    다르게 그리려면 그 함수를 고쳐야 하고 그러면 최종 렌더도 함께 바뀐다 (PRD 7.9)."""
    overlays = ["drawtext=text='하나'", "drawtext=text='둘'"]
    final = build_command(project_with(), run_dir=tmp_path, total_sec=5.0, overlays=overlays)
    preview = video_renderer.build_preview_command(
        project_with(), run_dir=tmp_path, total_sec=5.0, frames=[10], out_dir=tmp_path,
        overlays=overlays,
    )

    shared = "[0:v]setsar=1,drawtext=text='하나',drawtext=text='둘'"
    assert f"{shared}[video]" in preview_chain(final)
    assert preview_chain(preview).startswith(f"{shared},select=")


def test_the_preview_stops_after_the_last_requested_frame(tmp_path: Path) -> None:
    """비용을 정하는 인자다. 요청 수만큼만 쓰고 끝나므로 앞쪽 장면만 고르면 그만큼 싸다."""
    command = video_renderer.build_preview_command(
        project_with(), run_dir=tmp_path, total_sec=30.0, frames=[10, 200, 400],
        out_dir=tmp_path,
    )

    assert command[command.index("-frames:v") + 1] == "3"
    # `select`가 버린 자리를 복제해 채우면 상한에 먼저 닿아 뒤쪽 장면이 빈다.
    assert command[command.index("-fps_mode") + 1] == "passthrough"
    assert r"select='eq(n\,10)+eq(n\,200)+eq(n\,400)'" in preview_chain(command)


def test_representative_frames_sit_in_the_middle_of_each_scene() -> None:
    """경계 프레임은 어느 장면을 고른 것인지 화면에서 구분되지 않는다."""
    aligned = align(scenes_with(2.5, 3.0, 1.0), fps=30)

    assert aligned.frame_spans == ((0, 75), (75, 165), (165, 195))
    assert video_renderer.representative_frames(aligned) == (37, 120, 180)


def test_preview_rejects_a_scene_index_out_of_range(tmp_path: Path) -> None:
    with pytest.raises(RenderError, match="범위를 벗어났다"):
        video_renderer.preview(
            project_with(), scenes_with(1.0, 1.0), run_dir=tmp_path,
            out_dir=tmp_path / "frames", indices=[5],
        )


def test_preview_rejects_a_draft_scene_list(tmp_path: Path) -> None:
    """확정 상태만 받는다 — 길이가 없는 초안으로 그린 그림은 렌더 결과와 다르다."""
    draft = {"schema_version": 1, "type": "quiz",
             "scenes": [{"role": "hook", "text": "문구"}]}

    with pytest.raises(SchemaError):
        video_renderer.preview(
            project_with(), draft, run_dir=tmp_path, out_dir=tmp_path / "frames"
        )


@needs_ffmpeg
def test_preview_names_every_frame_by_its_scene_index(tmp_path: Path) -> None:
    """**순번이 아니라 장면 인덱스다.** 일부만 요청하면 둘이 어긋나므로 여기서 갈린다."""
    scenes = scenes_with(1.0, 1.0, 1.0, 1.0)

    frames = video_renderer.preview(
        project_with(), scenes, run_dir=tmp_path, out_dir=tmp_path / "frames",
        indices=[1, 3],
    )

    assert sorted(frames) == [1, 3]
    assert frames[1].name == "scene-001.png"
    assert frames[3].name == "scene-003.png"
    # 중간 이름이 남지 않는다.
    assert not list((tmp_path / "frames").glob("seq-*.png"))


@needs_ffmpeg
def test_preview_frames_are_png_at_the_canvas_spec(tmp_path: Path) -> None:
    frames = video_renderer.preview(
        project_with(), scenes_with(1.0, 1.0), run_dir=tmp_path,
        out_dir=tmp_path / "frames",
    )

    assert sorted(frames) == [0, 1]
    for path in frames.values():
        streams = probe(path, "stream=codec_name,width,height")["streams"]
        assert streams[0]["codec_name"] == "png"
        assert (streams[0]["width"], streams[0]["height"]) == (CANVAS_WIDTH, CANVAS_HEIGHT)


@needs_ffmpeg
def test_preview_does_not_produce_the_final_output(tmp_path: Path) -> None:
    """#27 완료 조건 — 프리뷰가 최종 렌더 경로를 실행하지 않는다."""
    video_renderer.preview(
        project_with(), scenes_with(1.0, 1.0), run_dir=tmp_path,
        out_dir=tmp_path / "frames",
    )

    assert not (tmp_path / OUTPUT_NAME).exists()
    assert not list(tmp_path.glob("*.mp4"))
