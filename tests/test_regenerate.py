"""앱의 편집을 반영한 재생성 (이슈 #77).

이 파일이 지키는 것은 넷이다.

- **다시 만드는 것이 콘텐츠에서 나온다** — 장면·세그먼트·타임라인·자막이 지금 콘텐츠와 맞고,
  텍스트가 그대로인 세그먼트는 다시 합성되지 않는다 (#15의 재사용 층)
- **사람이 얹은 편집이 오디오·자막에 반영된다** — `render.scene_overrides`를 얹은 타임라인이
  `voice.mp3`와 `captions.srt`의 기준이다 (PRD 14.1)
- **LLM이 이 경로에 없다** — 콘텐츠 생성기도 메타데이터 생성기도 부르지 않으므로 사람이 고친
  콘텐츠가 덮이지 않는다
- **실패·취소가 산출물을 손상시키지 않는다** — 임시 파일에 쓰고 마지막에 바꿔 끼운다

**픽스처를 손으로 쓰지 않는다.** run 디렉터리의 초기 상태는 파이프라인의 같은 함수들이
만든다 — 고정 사본을 두면 계약이 바뀌었을 때 이 파일만 조용히 통과한다.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import api, captions, narration, project, regenerate, timeline
from shorts_maker.config import (
    RUN_CONFIG_FILENAME,
    Config,
    ConfigError,
    load_config,
    serialize_config,
)
from shorts_maker.regenerate import RegenerateCancelled
from shorts_maker.run_context import (
    serialize_artifact,
    write_artifact,
    write_text_artifact,
)
from shorts_maker.schemas import SchemaError
from shorts_maker.schemas.project import PROJECT_SCHEMA, load_project
from shorts_maker.schemas.scenes import SCENES_SCHEMA, load_scenes, validate_scenes_final
from shorts_maker.shorts_types import DEFAULT_TYPE, ShortsType, get_type
from shorts_maker.timeline import VOICE_TRACK, TimelineError
from shorts_maker.tts import create_synthesizer

from conftest import StubFFmpeg, StubLLM, StubTTS

CONTENT: dict[str, Any] = {
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
            "verify": {"status": "verified", "confidence": 0.99},
        },
    ],
}


def shorts_type() -> ShortsType:
    return get_type(DEFAULT_TYPE)


def write_content(run_dir: Path, content: dict[str, Any]) -> None:
    """콘텐츠 산출물을 쓴다. **파일명은 레지스트리가 준다** (퀴즈 스펙 1.1)."""
    write_artifact(run_dir, shorts_type().content_artifact, content)


def read_content(run_dir: Path) -> dict[str, Any]:
    return json.loads(
        (run_dir / shorts_type().content_artifact).read_text(encoding="utf-8")
    )


@pytest.fixture
def run_dir(tmp_path: Path, stub_tts: StubTTS, stub_ffmpeg: StubFFmpeg) -> Path:
    """CLI가 낸 것과 같은 모양의 run 디렉터리.

    파이프라인의 같은 함수들로 만든다 — 설정 기록(#92)부터 `project.json`까지 전부 있고,
    세그먼트 파일과 `audio/segments.json`도 실제로 그 경로에 있다. 재생성이 재사용 판단을
    하려면 그 둘이 있어야 한다.
    """
    config = load_config(search_from=tmp_path)
    write_text_artifact(tmp_path, RUN_CONFIG_FILENAME, serialize_config(config))
    write_content(tmp_path, CONTENT)

    scenes = shorts_type().scene_template(CONTENT, config=config)
    scenes = narration.synthesize_segments(
        scenes, run_dir=tmp_path, synthesizer=create_synthesizer(config)
    )
    scenes = timeline.finalize(scenes, run_dir=tmp_path, config=config)
    write_artifact(tmp_path, SCENES_SCHEMA.name, scenes)
    write_text_artifact(
        tmp_path,
        captions.CAPTIONS_NAME,
        captions.render(captions.build(scenes, config=config)),
    )
    write_artifact(
        tmp_path,
        PROJECT_SCHEMA.name,
        project.build(scenes, config=config, run_dir=tmp_path),
    )
    # 픽스처를 만드느라 부른 합성은 여기까지다. 이 뒤의 호출은 전부 재생성의 것이다.
    stub_tts.calls.clear()
    stub_ffmpeg.mix_commands.clear()
    return tmp_path


def run(run_dir: Path, **kwargs: Any) -> regenerate.Report:
    return regenerate.regenerate(run_dir, shorts_type=shorts_type(), **kwargs)


def edit_content(run_dir: Path, change: Any) -> None:
    """콘텐츠를 고쳐 다시 쓴다. 앱이 저장한 뒤의 상태다."""
    content = read_content(run_dir)
    change(content)
    write_content(run_dir, content)


def patch_project(run_dir: Path, change: Any) -> dict[str, Any]:
    data = load_project(run_dir / PROJECT_SCHEMA.name)
    change(data)
    (run_dir / PROJECT_SCHEMA.name).write_text(
        serialize_artifact(data), encoding="utf-8"
    )
    return data


def scenes_of(run_dir: Path) -> dict[str, Any]:
    return load_scenes(run_dir / SCENES_SCHEMA.name, finalized=True)


def mtimes(run_dir: Path) -> dict[str, float]:
    return {
        path.name: path.stat().st_mtime_ns
        for path in sorted((run_dir / "audio").glob("seg-*.mp3"))
    }


def mix_command(stub_ffmpeg: StubFFmpeg) -> list[str]:
    """`voice.mp3`를 만든 마지막 명령. 오프셋과 총 길이가 여기 있다."""
    assert stub_ffmpeg.mix_commands, "합성 트랙을 만들지 않았다"
    return stub_ffmpeg.mix_commands[-1]


def delays(command: list[str]) -> list[int]:
    """명령의 `adelay` 값(밀리초). 세그먼트가 놓인 자리다."""
    graph = command[command.index("-filter_complex") + 1]
    return [
        int(part.split("adelay=")[1].split(":")[0])
        for part in graph.split(";")
        if "adelay=" in part
    ]


def total_of(command: list[str]) -> float:
    """마지막 `-t` 값. 합성 트랙의 총 길이다."""
    return float(command[len(command) - command[::-1].index("-t")])


# --- 무엇을 다시 만드는가 -----------------------------------------------------------


def test_unchanged_content_reuses_every_segment(
    run_dir: Path, stub_tts: StubTTS
) -> None:
    """고친 것이 없으면 합성이 한 번도 일어나지 않는다.

    재사용 판단은 `audio/segments.json`이 하고(#15), 재생성은 그 층 위에 있을 뿐이다.
    """
    before = mtimes(run_dir)

    report = run(run_dir)

    assert stub_tts.call_count == 0
    assert report.synthesized == 0
    assert report.segment_count == 4
    assert mtimes(run_dir) == before


def test_editing_one_answer_resynthesizes_only_that_segment(
    run_dir: Path, stub_tts: StubTTS
) -> None:
    """정답 문구를 고치면 **그 문제의 세그먼트만** 다시 쓰인다."""
    before = mtimes(run_dir)
    edit_content(run_dir, lambda data: data["questions"][1].update(answer="세 대륙"))

    report = run(run_dir)

    assert stub_tts.calls == ["세 대륙"]
    assert report.synthesized == 1
    after = mtimes(run_dir)
    changed = [name for name in before if after[name] != before[name]]
    # 두 번째 문제의 정답 장면 — hook(0) · q1(1,2,3) · q2(4,5,6) · cta(7)
    assert changed == ["seg-006.mp3"]


def test_editing_only_the_explanation_touches_no_segment(
    run_dir: Path, stub_tts: StubTTS
) -> None:
    """해설은 낭독되지 않는다 — 자막만 낡은 경우가 TTS 없이 끝난다 (D2 확정 스펙 7.3).

    **그래도 `voice.mp3`는 다시 만든다.** 해설이 길어지면 자막 읽기 하한이 그 장면의 길이를
    늘려 뒤 장면의 시작 시각이 전부 밀린다 — 실행 경로를 나누지 않는 이유가 이것이다.
    """
    edit_content(
        run_dir,
        lambda data: data["questions"][0].update(
            explanation="나일강은 약 6,650km이고 아마존강과의 길이 비교는 측정 방식에 따라 갈린다."
        ),
    )
    before = scenes_of(run_dir)["scenes"]

    report = run(run_dir)

    assert stub_tts.call_count == 0
    after = scenes_of(run_dir)["scenes"]
    assert after[3]["duration"] > before[3]["duration"]
    assert report.voice == VOICE_TRACK


def test_reordering_questions_moves_segments_to_the_new_indexes(
    run_dir: Path, stub_tts: StubTTS
) -> None:
    """순서를 바꾸면 장면 순서와 세그먼트 배치가 함께 따라간다.

    **재사용 키가 장면 인덱스라** 순서 변경은 텍스트가 같아도 다시 합성한다 (PRD 7.5.2).
    그 비용을 흡수하는 것은 run 밖 캐시(`.cache/tts`)다.
    """
    edit_content(run_dir, lambda data: data["questions"].reverse())

    report = run(run_dir)

    scenes = scenes_of(run_dir)["scenes"]
    assert [scene.get("question_id") for scene in scenes] == [
        None, 2, 2, 2, 1, 1, 1, None
    ]
    assert scenes[1]["text"] == "적도가 지나는 대륙은 몇 개인가?"
    assert scenes[1]["audio"] == "audio/seg-001.mp3"
    assert report.synthesized == 4


def test_adding_and_removing_questions_changes_the_scene_list(run_dir: Path) -> None:
    """문제를 더하고 지우면 장면 수와 총 길이가 따라가고, 옛 세그먼트 참조가 남지 않는다."""
    edit_content(
        run_dir,
        lambda data: data.__setitem__(
            "questions",
            [
                data["questions"][0],
                {
                    "id": 5,
                    "question": "가장 높은 산은?",
                    "answer": "에베레스트",
                    "explanation": "해발 8,848m다.",
                    "difficulty": "easy",
                    "countdown_sec": 3,
                },
            ],
        ),
    )

    report = run(run_dir)

    scenes = scenes_of(run_dir)["scenes"]
    assert report.scene_count == len(scenes) == 8
    assert [scene.get("question_id") for scene in scenes if "question_id" in scene] == [
        1, 1, 1, 5, 5, 5
    ]
    # 참조는 장면 인덱스로만 매겨진다 — 사라진 문제의 파일이 남아 있어도 목록에 없다.
    referenced = {scene["audio"] for scene in scenes if scene.get("narrate")}
    assert referenced == {f"audio/seg-{index:03d}.mp3" for index in (1, 3, 4, 6)}


def test_regenerated_scenes_pass_final_validation(run_dir: Path) -> None:
    """`scenes.json`은 확정 상태로 남는다 — 프리뷰·렌더가 그것을 요구한다."""
    run(run_dir)

    validate_scenes_final(scenes_of(run_dir))


# --- 사람이 얹은 편집 ---------------------------------------------------------------


def override(**fields: Any) -> dict[str, Any]:
    return fields


def test_voice_track_and_captions_use_the_overridden_timeline(
    run_dir: Path, stub_ffmpeg: StubFFmpeg
) -> None:
    """오버라이드를 얹은 타임라인이 `voice.mp3`와 자막의 기준이다 (PRD 14.1).

    **얹지 않으면 사람이 길이를 고친 순간부터 낭독이 화면과 어긋난다** — `timeline_stale`이
    뜻하는 상태가 그것이고, 얹어야 지워진다.
    """
    patch_project(
        run_dir,
        lambda data: data["render"].__setitem__(
            "scene_overrides",
            [override(role="hook", duration=6.0)],
        ),
    )

    report = run(run_dir)

    scenes = scenes_of(run_dir)["scenes"]
    # `scenes.json`은 얹기 전 값 그대로다 — 사람의 편집은 `project.json`에 산다.
    assert scenes[0]["duration"] == 2.5
    # 첫 장면이 3.5초 길어졌으므로 그 뒤 낭독이 전부 그만큼 밀린다.
    assert delays(mix_command(stub_ffmpeg))[0] == round((6.0 + 0.3) * 1000)
    assert abs(total_of(mix_command(stub_ffmpeg)) - report.total_sec) < 0.001
    assert report.total_sec == round(
        sum(scene["duration"] for scene in scenes) + 3.5, 3
    )

    srt = (run_dir / captions.CAPTIONS_NAME).read_text(encoding="utf-8")
    assert "00:00:06,300" in srt


def test_regeneration_accepts_a_duration_shorter_than_the_narration(
    run_dir: Path,
) -> None:
    """낭독보다 짧은 길이도 받는다 — 확정 검증이 거부하는 값이라 오버라이드에 산다.

    **검증을 자막·트랙 경로에 그대로 두면 사람이 줄인 길이에서 재생성이 실패한다.**
    """
    patch_project(
        run_dir,
        lambda data: data["render"].__setitem__(
            "scene_overrides",
            [override(role="question", question_id=1, duration=0.5)],
        ),
    )

    report = run(run_dir)

    assert report.cue_count > 0
    assert scenes_of(run_dir)["scenes"][1]["duration"] > 0.5


def test_edited_caption_text_reaches_the_srt(run_dir: Path) -> None:
    """사람이 고친 자막 문구가 `captions.srt`에 반영된다 (#83 → #77).

    번인은 이미 오버라이드를 쓰므로, 얹지 않으면 이 파일만 옛 문구로 남는다.
    """
    patch_project(
        run_dir,
        lambda data: data["render"].__setitem__(
            "scene_overrides",
            [override(role="answer", question_id=1, text="고친 자막")],
        ),
    )

    run(run_dir)

    srt = (run_dir / captions.CAPTIONS_NAME).read_text(encoding="utf-8")
    assert "고친 자막" in srt
    assert "나일강" not in srt.split("\n\n")[3]


def test_overrides_for_removed_questions_are_pruned_and_others_kept(
    run_dir: Path,
) -> None:
    """사라진 문제를 가리키던 항목만 정리한다. 남은 장면의 값은 사람이 넣은 것이다."""
    patch_project(
        run_dir,
        lambda data: data["render"].__setitem__(
            "scene_overrides",
            [
                override(role="answer", question_id=2, duration=4.0),
                override(role="hook", text="남는 문구"),
            ],
        ),
    )
    edit_content(run_dir, lambda data: data["questions"].pop(1))

    report = run(run_dir)

    kept = load_project(run_dir / PROJECT_SCHEMA.name)["render"]["scene_overrides"]
    assert report.dropped_overrides == 1
    assert kept == [override(role="hook", text="남는 문구")]


# --- 검수 상태 ---------------------------------------------------------------------


def test_regeneration_clears_stale_flags_but_keeps_acknowledged(run_dir: Path) -> None:
    """낡음 표시를 비우고 사람의 확인은 남긴다 (D2 확정 스펙 1.4)."""
    patch_project(
        run_dir,
        lambda data: data.__setitem__(
            "review",
            {
                "acknowledged": [1],
                "stale": [1, 2],
                "captions_stale": [2],
                "timeline_stale": True,
            },
        ),
    )

    run(run_dir)

    review = load_project(run_dir / PROJECT_SCHEMA.name)["review"]
    assert review == {
        "acknowledged": [1],
        "stale": [],
        "captions_stale": [],
        "timeline_stale": False,
    }


def test_regeneration_writes_review_into_a_project_that_had_none(
    run_dir: Path,
) -> None:
    """이 섹션이 생기기 전에 만들어진 run 디렉터리도 열리고, 비운 상태가 명시된다."""
    patch_project(run_dir, lambda data: data.pop("review"))

    run(run_dir)

    review = load_project(run_dir / PROJECT_SCHEMA.name)["review"]
    assert review["acknowledged"] == []
    assert review["timeline_stale"] is False


# --- 부르지 않는 것 -----------------------------------------------------------------


def test_regeneration_calls_no_model(run_dir: Path, stub_llm: StubLLM) -> None:
    """콘텐츠 생성기도 메타데이터 생성기도 이 경로에 없다.

    **그래서 `metadata.json`은 옛 제목·태그로 남는다** — 다시 만드는 것은 CLI 전체 실행이고
    (#37의 업로드 경로가 아직 없다), 사람이 고친 콘텐츠가 덮이지 않는 것이 그 대가다.
    """
    write_artifact(run_dir, "metadata.json", {"schema_version": 1, "title": "옛 제목"})
    before = read_content(run_dir)
    edit_content(run_dir, lambda data: data["questions"][0].update(answer="고친 정답"))

    run(run_dir)

    assert stub_llm.call_count == 0
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["title"] == "옛 제목"
    # 콘텐츠는 읽기만 한다 — 고친 정답이 그대로 있고 나머지도 손대지 않았다.
    assert read_content(run_dir) == {
        **before,
        "questions": [
            {**before["questions"][0], "answer": "고친 정답"},
            before["questions"][1],
        ],
    }


def test_regeneration_reads_the_recorded_config_not_the_working_directory(
    run_dir: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cwd에 다른 `config.yaml`이 있어도 그 값을 쓰지 않는다 (#92, PRD 14.1).

    **앱 백엔드에서 cwd를 정하는 것은 앱이다.** `load_config()`를 인자 없이 부르는 경로가
    하나라도 있으면 재생성이 생성 때와 다른 값으로 돈다.
    """
    elsewhere = tmp_path_factory.mktemp("cwd")
    (elsewhere / "config.yaml").write_text(
        "timing:\n  lead_in_sec: 9.0\n", encoding="utf-8"
    )
    monkeypatch.chdir(elsewhere)

    run(run_dir)

    assert scenes_of(run_dir)["scenes"][1]["narration_offset"] == 2.8


def test_missing_run_config_is_a_config_error(run_dir: Path) -> None:
    """설정 기록이 없는 run 디렉터리(#92 이전)는 원인을 말하며 멈춘다."""
    (run_dir / RUN_CONFIG_FILENAME).unlink()

    with pytest.raises(ConfigError):
        run(run_dir)


def test_broken_content_stops_before_touching_artifacts(run_dir: Path) -> None:
    """콘텐츠가 계약을 어기면 아무것도 바꾸지 않는다."""
    write_content(run_dir, {"schema_version": 1, "type": DEFAULT_TYPE})
    before = (run_dir / SCENES_SCHEMA.name).read_bytes()

    with pytest.raises(SchemaError):
        run(run_dir)

    assert (run_dir / SCENES_SCHEMA.name).read_bytes() == before


# --- 무손상과 취소 -----------------------------------------------------------------


def snapshot(run_dir: Path) -> dict[str, bytes]:
    return {
        name: (run_dir / name).read_bytes()
        for name in (
            SCENES_SCHEMA.name,
            PROJECT_SCHEMA.name,
            captions.CAPTIONS_NAME,
            VOICE_TRACK,
        )
    }


def leftovers(run_dir: Path) -> list[str]:
    return [path.name for path in run_dir.iterdir() if ".tmp-" in path.name]


def test_a_failed_mix_leaves_every_artifact_untouched(
    run_dir: Path, stub_ffmpeg: StubFFmpeg
) -> None:
    """합성 트랙이 실패하면 넷 다 이전 상태다 — 교체는 전부 만든 뒤 한 번이다."""
    edit_content(run_dir, lambda data: data["questions"][0].update(answer="고친 정답"))
    before = snapshot(run_dir)
    stub_ffmpeg.mix_returncode = 1

    with pytest.raises(TimelineError):
        run(run_dir)

    assert snapshot(run_dir) == before
    assert leftovers(run_dir) == []


def test_cancelling_stops_at_a_step_boundary(run_dir: Path) -> None:
    """취소하면 다음 단계 경계에서 멈추고 산출물은 그대로다."""
    edit_content(run_dir, lambda data: data["questions"][0].update(answer="고친 정답"))
    before = snapshot(run_dir)
    seen: list[str] = []

    def cancel() -> bool:
        return len(seen) >= 3

    def watch(progress: regenerate.Progress) -> None:
        seen.append(progress.step)

    with pytest.raises(RegenerateCancelled):
        run(run_dir, on_progress=watch, should_cancel=cancel)

    assert snapshot(run_dir) == before
    assert leftovers(run_dir) == []


def test_cancelling_keeps_the_segments_it_already_made(run_dir: Path) -> None:
    """세그먼트는 제자리에 쓰므로 남는다 — 다음 재생성이 이어서 한다 (#15의 재사용 층)."""
    edit_content(
        run_dir,
        lambda data: [
            question.update(answer=f"고친 정답 {question['id']}")
            for question in data["questions"]
        ],
    )
    steps: list[regenerate.Progress] = []

    with pytest.raises(RegenerateCancelled):
        run(
            run_dir,
            on_progress=steps.append,
            should_cancel=lambda: sum(
                1 for step in steps if step.step == "narration" and step.done
            )
            >= 1,
        )

    manifest = json.loads(
        (run_dir / "audio" / narration.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert any("고친 정답" in entry["text"] for entry in manifest["segments"])


def test_progress_reports_every_step_and_counts_segments(run_dir: Path) -> None:
    """진행은 단계 단위이고 세그먼트만 `n/m`을 낸다."""
    steps: list[regenerate.Progress] = []

    run(run_dir, on_progress=steps.append)

    assert [step.step for step in steps if step.total == 0] == list(regenerate.STEPS)
    assert [
        (step.done, step.total) for step in steps if step.step == "narration" and step.total
    ] == [(1, 4), (2, 4), (3, 4), (4, 4)]


# --- 앱 백엔드 경계 ------------------------------------------------------------------
#
# **`test_api.py`가 아니라 여기 있다.** 그쪽의 run 디렉터리 픽스처에는 설정 기록도 세그먼트도
# 없고(앱이 열고 저장하는 데 필요 없다), 재생성은 그 둘이 있어야 한 단계도 지나지 못한다.


def api_call(
    method: str, **params: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """앱이 보내는 것과 같은 모양의 요청 하나. 알림도 함께 받는다."""
    events: list[dict[str, Any]] = []
    response = api.handle(
        {"id": 7, "method": method, "params": params}, emit=events.append
    )
    return response, events


def test_the_app_method_regenerates_and_reports_progress(run_dir: Path) -> None:
    """앱은 `run_dir`과 `type`만 보낸다 — **프로젝트를 넘기지 않는다** (프리뷰·렌더와 갈린다)."""
    edit_content(run_dir, lambda data: data["questions"][0].update(answer="고친 정답"))

    response, events = api_call("regenerate", run_dir=str(run_dir), type=DEFAULT_TYPE)

    result = response["result"]
    assert result["cancelled"] is False
    assert (result["scene_count"], result["segment_count"], result["synthesized"]) == (8, 4, 1)
    assert [event["event"] for event in events] == ["regenerate_progress"] * len(events)
    assert [event["id"] for event in events] == [7] * len(events)
    assert {event["step"] for event in events} == set(regenerate.STEPS)


def test_an_unknown_type_is_a_bad_request(run_dir: Path) -> None:
    """등록되지 않은 타입은 백엔드 고장이 아니라 앱이 모르는 프로젝트를 열었다는 뜻이다."""
    response, _events = api_call("regenerate", run_dir=str(run_dir), type="없는타입")

    assert response["error"]["code"] == "bad_request"


def test_a_missing_run_config_comes_back_as_a_config_error(run_dir: Path) -> None:
    """#92 이전에 만들어진 run 디렉터리다. **앱이 그 사실을 그릴 수 있어야 한다.**"""
    (run_dir / RUN_CONFIG_FILENAME).unlink()

    response, _events = api_call("regenerate", run_dir=str(run_dir), type=DEFAULT_TYPE)

    assert response["error"]["code"] == "config"
    assert any(RUN_CONFIG_FILENAME in line for line in response["error"]["details"])


def test_a_failure_comes_back_with_the_previous_artifacts_intact(
    run_dir: Path, stub_ffmpeg: StubFFmpeg
) -> None:
    before = snapshot(run_dir)
    stub_ffmpeg.mix_returncode = 1

    response, _events = api_call("regenerate", run_dir=str(run_dir), type=DEFAULT_TYPE)

    assert response["error"]["code"] == "regenerate"
    assert snapshot(run_dir) == before


def test_a_second_regenerate_request_is_refused_while_one_runs(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**앱의 버튼 잠금에 맡기지 않는다** — 창이 여럿이거나 스모크가 직접 부를 수 있다 (#30과
    같은 자리)."""
    started = threading.Event()
    release = threading.Event()

    def slow(*args: Any, **kwargs: Any) -> regenerate.Report:
        started.set()
        release.wait(timeout=5)
        return regenerate.Report(
            scene_count=8,
            segment_count=4,
            synthesized=0,
            cue_count=6,
            total_sec=24.8,
            voice=VOICE_TRACK,
            dropped_overrides=0,
        )

    monkeypatch.setattr(regenerate, "regenerate", slow)
    first: list[dict[str, Any]] = []
    worker = threading.Thread(
        target=lambda: first.append(
            api_call("regenerate", run_dir=str(run_dir), type=DEFAULT_TYPE)[0]
        )
    )
    worker.start()
    assert started.wait(timeout=5)
    try:
        response, _events = api_call(
            "regenerate", run_dir=str(run_dir), type=DEFAULT_TYPE
        )
    finally:
        release.set()
        worker.join(timeout=5)

    assert response["error"]["code"] == "busy"
    assert first[0]["result"]["scene_count"] == 8


def test_cancelling_comes_back_as_a_result_not_a_failure(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**취소는 사용자가 누른 것이다.** 오류로 실어 보내면 앱이 실패 카드를 그린다."""
    started = threading.Event()

    def waiting(*args: Any, **kwargs: Any) -> regenerate.Report:
        started.set()
        for _attempt in range(500):
            if kwargs["should_cancel"]():
                raise RegenerateCancelled("취소")
            time.sleep(0.01)
        raise AssertionError("취소 요청이 오지 않았다")

    monkeypatch.setattr(regenerate, "regenerate", waiting)
    responses: list[dict[str, Any]] = []
    worker = threading.Thread(
        target=lambda: responses.append(
            api_call("regenerate", run_dir=str(run_dir), type=DEFAULT_TYPE)[0]
        )
    )
    worker.start()
    assert started.wait(timeout=5)
    cancelled, _events = api_call("cancel_regenerate")
    worker.join(timeout=5)

    assert cancelled["result"] == {"running": True}
    assert responses[0]["result"]["cancelled"] is True


def test_cancelling_when_nothing_runs_is_not_a_failure() -> None:
    """끝나는 순간에 누른 것과 구분할 방법이 없다 — 둘 다 아무 일도 일어나지 않는다."""
    response, _events = api_call("cancel_regenerate")

    assert response["result"] == {"running": False}


def test_report_says_what_was_made(run_dir: Path) -> None:
    edit_content(run_dir, lambda data: data["questions"][0].update(answer="고친 정답"))

    report = run(run_dir)

    assert report.scene_count == 8
    assert report.segment_count == 4
    assert report.synthesized == 1
    assert report.cue_count == 6
    assert report.voice == VOICE_TRACK
    assert report.total_sec > 0
