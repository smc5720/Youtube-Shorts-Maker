"""실패한 run 디렉터리를 이어서 재실행 (이슈 #36).

이 파일이 지키는 것은 다섯이다.

- **비용을 다시 쓰지 않는다** — 렌더만 실패한 run을 이어 돌리면 LLM 호출도 TTS 합성도 0회다
- **설정이 그 run의 기록에서 온다** — cwd에 다른 `config.yaml`을 두어도 결과가 바뀌지 않는다
  (`config.used.yaml`, #92)
- **없는 것만 만든다** — 있는 산출물은 그대로 쓰고, 특히 `project.json`은 손대지 않는다
  (사람이 앱에서 얹은 값이 거기 산다)
- **만들 수 없는 것이 없으면 멈춘다** — 콘텐츠·메타데이터는 모델이 만드는 산출물이다
- **강제 재실행이 산출물이 있어도 다시 만든다** — 세그먼트 오디오와 렌더 둘

**run 디렉터리를 손으로 쓰지 않는다.** 초기 상태는 CLI가 만든다 — 실패한 run의 모양을 손으로
흉내 내면 무엇이 남고 무엇이 안 남는지를 이 파일이 정하게 되고, 그건 검증이 아니다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import StubFFmpeg, StubLLM, StubTTS

from shorts_maker.captions import CAPTIONS_NAME
from shorts_maker.config import load_run_config
from shorts_maker.main import EXIT_CONFIG_ERROR, EXIT_FLAGGED, EXIT_RUNTIME_ERROR, main
from shorts_maker.narration import manifest_path
from shorts_maker.resume import REQUIRED, ResumeError, resume
from shorts_maker.run_context import LOG_FILENAME
from shorts_maker.schemas import METADATA_SCHEMA, PROJECT_SCHEMA, SCENES_SCHEMA
from shorts_maker.timeline import VOICE_TRACK
from shorts_maker.video_renderer import OUTPUT_NAME

pytestmark = pytest.mark.usefixtures("stub_llm", "stub_tts")


def run_dirs(output_root: Path) -> list[Path]:
    return sorted(path for path in output_root.iterdir() if path.is_dir())


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def segments_of(run_dir: Path) -> list[Path]:
    """낭독 세그먼트 파일들. 경로는 `scenes.json`이 들고 있다."""
    scenes = read(run_dir / SCENES_SCHEMA.name)["scenes"]
    return [run_dir / scene["audio"] for scene in scenes if scene.get("audio")]


@pytest.fixture
def failed_render(tmp_path: Path, stub_ffmpeg: StubFFmpeg) -> Path:
    """렌더만 실패한 run 디렉터리. **CLI가 실제로 그 상태를 만든다.**

    이어 돌리기가 살리려는 실패가 정확히 이것이다 — 앞 단계 전부가 성공해 산출물로 남아
    있는데 마지막 인코딩만 깨졌다.
    """
    stub_ffmpeg.render_returncode = 1
    assert main(["--topic", "주제", "--out", str(tmp_path)]) == EXIT_RUNTIME_ERROR
    stub_ffmpeg.render_returncode = 0

    run_dir = run_dirs(tmp_path)[0]
    assert not (run_dir / OUTPUT_NAME).exists()
    return run_dir


@pytest.fixture
def finished(tmp_path: Path) -> Path:
    """성공해서 산출물이 다 있는 run 디렉터리."""
    assert main(["--topic", "주제", "--out", str(tmp_path)]) == 0
    return run_dirs(tmp_path)[0]


# --- 비용을 다시 쓰지 않는다 --------------------------------------------------


def test_resuming_a_failed_render_calls_no_model_and_synthesizes_nothing(
    failed_render: Path, stub_llm: StubLLM, stub_tts: StubTTS, stub_ffmpeg: StubFFmpeg
) -> None:
    """#36 완료 조건 — 이어 돌린 실행에서 LLM 0회, TTS 합성 0회다.

    합성 트랙도 다시 만들지 않는다 (`mix_count`). `scenes.json`이 확정 상태이고
    `voice.mp3`가 있으므로 세그먼트·타임라인 단계 자체를 건너뛴다.
    """
    calls = (stub_llm.call_count, stub_tts.call_count, stub_ffmpeg.mix_count)

    assert main(["--resume", str(failed_render)]) == 0

    assert (stub_llm.call_count, stub_tts.call_count, stub_ffmpeg.mix_count) == calls
    assert (failed_render / OUTPUT_NAME).is_file()


def test_resuming_reports_what_it_reused_and_what_it_remade(failed_render: Path) -> None:
    report = resume(failed_render)

    assert report.remade == (OUTPUT_NAME,)
    assert set(report.reused) == {
        SCENES_SCHEMA.name,
        VOICE_TRACK,
        CAPTIONS_NAME,
        PROJECT_SCHEMA.name,
    }
    assert report.synthesized == 0
    assert report.output == failed_render / OUTPUT_NAME


def test_a_finished_run_has_nothing_to_do(
    finished: Path, stub_ffmpeg: StubFFmpeg
) -> None:
    """**있는 것은 그대로 쓴다** — 이미 완성된 run을 이어 돌려도 다시 인코딩하지 않는다."""
    renders = stub_ffmpeg.render_count

    report = resume(finished)

    assert report.remade == ()
    assert stub_ffmpeg.render_count == renders


def test_a_run_stopped_by_the_review_gate_can_be_resumed(
    tmp_path: Path, stub_llm: StubLLM, stub_ffmpeg: StubFFmpeg
) -> None:
    """#36 완료 조건 — `--fail-on-flagged`로 멈춘 run도 이어 돌릴 수 있다.

    그 run은 **산출물이 다 있다** — 게이트는 파이프라인 뒤에 있고 멈추는 것은 이후 단계이지
    이번 실행의 결과물이 아니다 (#11). 그래서 이어 돌리기가 할 일은 없고, 검수 판정을 다시
    내리지도 않는다 (`--fail-on-flagged`는 `--resume`과 함께 쓸 수 없다).
    """
    flagged(stub_llm)
    exit_code = main(["--topic", "주제", "--out", str(tmp_path), "--fail-on-flagged"])
    assert exit_code == EXIT_FLAGGED
    run_dir = run_dirs(tmp_path)[0]
    renders = stub_ffmpeg.render_count

    assert main(["--resume", str(run_dir)]) == 0

    assert stub_ffmpeg.render_count == renders
    assert (run_dir / OUTPUT_NAME).is_file()


# --- 설정은 그 run의 기록에서 온다 (#92) --------------------------------------


def test_the_resumed_run_ignores_the_config_in_the_working_directory(
    failed_render: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#36 완료 조건 — cwd에 다른 `config.yaml`을 두어도 결과가 바뀌지 않는다.

    `load_config()`를 인자 없이 부르면 **cwd**의 `config.yaml`을 찾으므로(PRD 14.1), 그
    실수는 이어 돌린 실행이 생성 때와 다른 값으로 도는 것으로 드러난다. `project.json`을
    지워 그 값이 실제로 파일에 옮겨 담기는 경로를 지나게 한다.
    """
    recorded = str(load_run_config(failed_render).get("render.cta_punch"))
    elsewhere = tmp_path / "다른 곳"
    elsewhere.mkdir()
    (elsewhere / "config.yaml").write_text(
        "render:\n  cta_punch: 다른 설정의 값\n", encoding="utf-8"
    )
    monkeypatch.chdir(elsewhere)
    (failed_render / PROJECT_SCHEMA.name).unlink()

    assert main(["--resume", str(failed_render)]) == 0

    rebuilt = read(failed_render / PROJECT_SCHEMA.name)
    assert rebuilt["render"]["cta_punch"] == recorded != "다른 설정의 값"


# --- 없는 것만 만든다 ---------------------------------------------------------


def test_a_missing_voice_track_is_remade_from_the_existing_segments(
    failed_render: Path, stub_tts: StubTTS
) -> None:
    """`voice.mp3`를 만드는 자리가 `timeline.finalize` 안이라 트랙만 따로 만들 입구가 없다.

    그래서 확정 상태여도 다시 확정하지만, 세그먼트가 그대로면 **재합성은 일어나지 않는다**
    (#15의 재사용 층).
    """
    (failed_render / VOICE_TRACK).unlink()
    synthesized = stub_tts.call_count

    report = resume(failed_render)

    assert (failed_render / VOICE_TRACK).is_file()
    assert set(report.remade) >= {SCENES_SCHEMA.name, VOICE_TRACK, OUTPUT_NAME}
    assert report.synthesized == 0
    assert stub_tts.call_count == synthesized


def test_a_draft_scene_list_is_confirmed_and_carried_to_the_end(
    failed_render: Path,
) -> None:
    """TTS 단계에서 멈춘 run — `scenes.json`이 초안이고 오디오가 없다.

    이어 돌리기가 세그먼트부터 다시 하고, 그 실측 길이로 확정해 렌더까지 간다.
    """
    for path in segments_of(failed_render):
        path.unlink()
    manifest_path(failed_render).unlink()
    (failed_render / VOICE_TRACK).unlink()
    draft = read(failed_render / SCENES_SCHEMA.name)
    for scene in draft["scenes"]:
        # **낭독 장면만 비운다.** 낭독이 없는 장면(`countdown`·`hook`·`cta`)의 `duration`은
        # 장면 템플릿이 넣은 고정 길이라 초안에도 있다 (PRD 7.5.1).
        if not scene.get("narrate"):
            continue
        for field in ("audio", "audio_duration", "duration", "narration_offset"):
            scene.pop(field, None)
    (failed_render / SCENES_SCHEMA.name).write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )

    report = resume(failed_render)

    # **provider 호출 수가 아니라 "그대로 쓰지 않은 세그먼트 수"다.** 캐시가 run 디렉터리
    # 밖의 층이라(`.cache/tts`) 같은 문장은 합성 없이 복사된다 (PRD 7.5.2).
    assert report.synthesized == len(segments_of(failed_render)) > 0
    confirmed = read(failed_render / SCENES_SCHEMA.name)["scenes"]
    assert all(scene.get("duration") for scene in confirmed)
    assert (failed_render / OUTPUT_NAME).is_file()


def test_the_captions_are_remade_when_the_timeline_was_confirmed_again(
    failed_render: Path,
) -> None:
    """타임코드가 낡았으므로 파일이 있어도 다시 만든다."""
    (failed_render / VOICE_TRACK).unlink()
    (failed_render / CAPTIONS_NAME).write_text("낡은 자막", encoding="utf-8")

    report = resume(failed_render)

    assert CAPTIONS_NAME in report.remade
    assert "낡은 자막" not in (failed_render / CAPTIONS_NAME).read_text(encoding="utf-8")


def test_an_existing_project_file_is_never_rebuilt(failed_render: Path) -> None:
    """**사람이 앱에서 얹은 값이 그 파일에 산다** (`render.scene_overrides`, `review`).

    `project.build`로 다시 만들면 조용히 사라진다 — 장면을 다시 확정했더라도 손대지 않는
    이유가 그것이고, 자막과 갈리는 지점이 여기다.
    """
    path = failed_render / PROJECT_SCHEMA.name
    edited = read(path)
    edited["render"]["scene_overrides"] = [{"role": "hook", "duration": 1.0}]
    edited["review"] = {"acknowledged": [1], "stale": [], "captions_stale": [],
                        "timeline_stale": False}
    path.write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")
    (failed_render / VOICE_TRACK).unlink()
    (failed_render / CAPTIONS_NAME).unlink()

    report = resume(failed_render)

    assert PROJECT_SCHEMA.name in report.reused
    assert read(path) == edited


def test_the_missing_project_file_is_rebuilt(failed_render: Path) -> None:
    (failed_render / PROJECT_SCHEMA.name).unlink()

    report = resume(failed_render)

    assert PROJECT_SCHEMA.name in report.remade
    assert (failed_render / PROJECT_SCHEMA.name).is_file()


# --- 만들 수 없는 것이 없으면 멈춘다 ------------------------------------------


@pytest.mark.parametrize("name", REQUIRED)
def test_a_missing_required_artifact_says_which_one(
    failed_render: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """#36 완료 조건 — 무엇이 없는지 말하며 멈춘다.

    셋 다 이어 돌리기가 만들 수 없다. 콘텐츠·메타데이터는 모델이 만드는 산출물이므로
    다시 만드는 것은 새 run이고, 설정 기록이 없으면 어떤 값으로 돌았는지 알 수 없다.
    """
    (failed_render / name).unlink()
    log_before = (failed_render / LOG_FILENAME).read_text(encoding="utf-8")

    exit_code = main(["--resume", str(failed_render)])

    assert exit_code == EXIT_CONFIG_ERROR
    stderr = capsys.readouterr().err
    assert name in stderr and "새로" in stderr
    assert not (failed_render / OUTPUT_NAME).exists()
    # **거부는 아무것도 쓰지 않는다** — 로그도 남기지 않는다 (`resume.check`).
    assert (failed_render / LOG_FILENAME).read_text(encoding="utf-8") == log_before


def test_all_the_missing_artifacts_come_in_one_message(failed_render: Path) -> None:
    """하나씩 말하면 고치고 다시 돌려서 다음 것을 보는 왕복이 생긴다."""
    (failed_render / SCENES_SCHEMA.name).unlink()
    (failed_render / METADATA_SCHEMA.name).unlink()

    with pytest.raises(ResumeError) as failure:
        resume(failed_render)

    assert SCENES_SCHEMA.name in str(failure.value)
    assert METADATA_SCHEMA.name in str(failure.value)


def test_a_path_that_is_not_a_run_directory_stops_before_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """**거부할 때 그 자리에 아무것도 쓰지 않는다.** 엉뚱한 경로를 가리킨 사람에게 남는 것이
    로그 파일 하나여서는 안 된다 (`resume.check`)."""
    missing = tmp_path / "없는 디렉터리"
    plain = tmp_path / "빈 디렉터리"
    plain.mkdir()

    assert main(["--resume", str(missing)]) == EXIT_CONFIG_ERROR
    assert main(["--resume", str(plain)]) == EXIT_CONFIG_ERROR

    assert "디렉터리가 아니다" in capsys.readouterr().err
    assert not missing.exists()
    assert list(plain.iterdir()) == []


def test_an_unknown_force_target_is_named(failed_render: Path) -> None:
    """CLI는 argparse가 막지만 이 함수는 앱·테스트도 부른다."""
    with pytest.raises(ResumeError, match="quiz") as failure:
        resume(failed_render, force=["quiz"])

    assert "segments, render" in str(failure.value)


# --- 강제 재실행 --------------------------------------------------------------


def test_forcing_the_render_encodes_again_over_an_existing_output(
    finished: Path, stub_ffmpeg: StubFFmpeg
) -> None:
    """#36 완료 조건 — 지정한 단계가 산출물이 있어도 다시 실행된다."""
    renders = stub_ffmpeg.render_count

    report = resume(finished, force=["render"])

    assert report.remade == (OUTPUT_NAME,)
    assert stub_ffmpeg.render_count == renders + 1
    assert (finished / OUTPUT_NAME).is_file()


def test_forcing_the_segments_rewrites_the_audio_files(finished: Path) -> None:
    """세그먼트 오디오가 있어도 다시 만든다.

    **provider 호출까지 늘지는 않는다.** TTS 캐시는 run 디렉터리 밖의 층이고(`.cache/tts`,
    PRD 7.5.2) 이 옵션의 대상은 run 디렉터리의 파일이다 — 같은 문장이면 캐시에서 복사된다.
    """
    segment = segments_of(finished)[0]
    segment.write_bytes(b"broken")

    # 강제하지 않으면 세그먼트 단계 자체를 건너뛰므로 깨진 파일이 그대로 남는다.
    resume(finished)
    assert segment.read_bytes() == b"broken"

    report = resume(finished, force=["segments"])

    assert segment.read_bytes() != b"broken"
    assert set(report.remade) >= {SCENES_SCHEMA.name, VOICE_TRACK, OUTPUT_NAME}


# --- 렌더 무손상 --------------------------------------------------------------


def test_a_failed_resume_leaves_no_truncated_output(
    finished: Path, stub_ffmpeg: StubFFmpeg
) -> None:
    """#36 완료 조건 — 이전 성공본이 그대로 남는다.

    잘린 mp4가 남으면 다음 이어 돌리기가 "산출물이 있으니 건너뛴다"로 판단해 **그 파일을
    최종 결과로 넘긴다.**
    """
    kept = (finished / OUTPUT_NAME).read_bytes()
    stub_ffmpeg.render_returncode = 1

    assert main(["--resume", str(finished), "--force", "render"]) == EXIT_RUNTIME_ERROR

    assert (finished / OUTPUT_NAME).read_bytes() == kept
    assert list(finished.glob("*.mp4")) == [finished / OUTPUT_NAME]


# --- 기록 --------------------------------------------------------------------


def test_the_resume_appends_to_the_same_run_log(failed_render: Path) -> None:
    """이어 돌리기는 같은 run의 계속이다 — 무엇을 건너뛰었는지가 실패 기록 아래에 있어야 한다."""
    before = (failed_render / LOG_FILENAME).read_text(encoding="utf-8")

    assert main(["--resume", str(failed_render)]) == 0

    log_text = (failed_render / LOG_FILENAME).read_text(encoding="utf-8")
    assert log_text.startswith(before)
    assert "이어 돌리기 시작" in log_text
    assert "이어 돌리기 완료" in log_text


# --- CLI 경계 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        ["--config", "config.yaml"],
        ["--type", "quiz"],
        ["--out", "outputs"],
        ["--fail-on-flagged"],
    ],
)
def test_generation_only_arguments_are_rejected_with_resume(
    failed_render: Path, extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """조용히 무시하면 일어나지 않은 일을 일어났다고 믿는다.

    특히 `--config`가 그렇다 — 이어 돌리기는 설정을 `config.used.yaml`에서 읽으므로 다른
    설정 파일을 준 사람은 그 값으로 돌았다고 생각하게 된다.
    """
    exit_code = main(["--resume", str(failed_render), *extra])

    assert exit_code == EXIT_CONFIG_ERROR
    assert extra[0] in capsys.readouterr().err
    assert not (failed_render / OUTPUT_NAME).exists()


def test_force_without_resume_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--topic", "주제", "--force", "render"]) == EXIT_CONFIG_ERROR
    assert "--resume" in capsys.readouterr().err


def test_resume_is_exclusive_with_the_three_generation_inputs(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """함께 주면 새로 만들 것인지 이어 돌릴 것인지 말할 수 없다. argparse의 코드 2다."""
    with pytest.raises(SystemExit) as exit_info:
        main(["--topic", "주제", "--resume", str(tmp_path)])

    assert exit_info.value.code == 2
    assert "--resume" in capsys.readouterr().err


QUESTION = "세계에서 가장 긴 강은?"
ANSWER = "나일강"


def flagged(stub_llm: StubLLM) -> None:
    """재답변이 갈려 `flagged`가 나오는 고정 응답 세트 — 생성 1회 + 재답변 2회 + 프로브 1회.

    `tests/test_main.py`의 `fixed_run`과 같은 모양이다. 여기 다시 두는 이유는 이 파일이
    확인하는 것이 **게이트로 멈춘 run을 이어 돌릴 수 있는가**여서, 그 상태를 만드는 응답이
    이 파일 안에 있어야 왜 `flagged`인지가 읽히기 때문이다.
    """
    reply = {
        "answers": [
            {"id": 1, "answer": "아마존강", "certainty": 1.0, "basis": "근거"}
        ]
    }
    stub_llm.reply(
        {
            "hook": "이 상식 다 맞히면 상위 1%",
            "cta": "몇 개 맞혔나요?",
            "questions": [
                {
                    "question": QUESTION,
                    "answer": ANSWER,
                    "explanation": "약 6,650km로 아마존강보다 조금 길다.",
                    "difficulty": "easy",
                }
            ],
        },
        reply,
        reply,
        {"questions": [{"id": 1, "single_answer": True, "reason": "판단 근거"}]},
    )
