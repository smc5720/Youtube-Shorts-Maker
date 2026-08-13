"""렌더 엔진 골격 — 이슈 #19의 완료 조건.

**두 층으로 갈라져 있다.** 명령 생성은 FFmpeg 없이 돌고(`build_command`), 규격·길이·오디오는
진짜 FFmpeg로 렌더해 `ffprobe`로 확인한다. 후자는 FFmpeg가 없는 환경에서 건너뛴다 — 명령이
맞는지는 앞 층이 이미 지키므로 건너뛴 실행이 검사 부재가 되지 않는다.

렌더 테스트의 영상 길이는 1~2초다. 확인하는 것이 인코딩 규격과 프레임 경계라 길이를 늘려도
검증력이 늘지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import timeline, video_renderer
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


@pytest.mark.parametrize("kind", ["image", "video"])
def test_a_file_background_fills_the_canvas_without_distortion(
    tmp_path: Path, kind: str
) -> None:
    """비율을 유지한 채 넘치는 쪽을 자른다 — 빈 영역이 남지 않는다."""
    source = tmp_path / f"bg.{kind}"
    source.write_text("명령을 만들 때는 파일 내용을 보지 않는다", encoding="utf-8")
    project = project_with(background={"kind": kind, "value": source.name})

    command = build_command(project, run_dir=tmp_path, total_sec=5.0)

    chain = command[command.index("-filter_complex") + 1]
    assert "force_original_aspect_ratio=increase" in chain
    assert "crop=1080:1920" in chain
    assert str(source) in command


def test_a_missing_background_file_names_the_path(tmp_path: Path) -> None:
    project = project_with(background={"kind": "image", "value": "없는파일.png"})

    with pytest.raises(RenderError, match="없는파일.png"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


def test_an_unknown_background_kind_lists_the_supported_ones(tmp_path: Path) -> None:
    project = project_with(background={"kind": "webcam", "value": "0"})

    with pytest.raises(RenderError, match="preset"):
        build_command(project, run_dir=tmp_path, total_sec=5.0)


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


def test_a_missing_ffmpeg_says_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError(2, "그런 파일이 없다")

    monkeypatch.setattr(video_renderer.subprocess, "run", missing)

    with pytest.raises(RenderError, match="ffmpeg를 찾을 수 없다"):
        render(project_with(), scenes_with(1.0), run_dir=tmp_path)


def test_a_failing_ffmpeg_carries_the_exit_code_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """명령 전문과 stderr가 run.log에 남아야 실패한 렌더를 손으로 재현할 수 있다."""

    def failing(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "Invalid argument")

    monkeypatch.setattr(video_renderer.subprocess, "run", failing)

    with caplog.at_level("DEBUG"):
        with pytest.raises(RenderError, match="Invalid argument"):
            render(project_with(), scenes_with(1.0), run_dir=tmp_path)

    assert "렌더 명령 ffmpeg" in caplog.text
    assert "Invalid argument" in caplog.text


def test_a_timeout_names_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow(command: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(command, video_renderer.FFMPEG_TIMEOUT_SEC)

    monkeypatch.setattr(video_renderer.subprocess, "run", slow)

    with pytest.raises(RenderError, match=OUTPUT_NAME):
        render(project_with(), scenes_with(1.0), run_dir=tmp_path)


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
