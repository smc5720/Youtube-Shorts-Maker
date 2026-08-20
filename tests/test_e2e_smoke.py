"""`--topic` 하나로 `final_short.mp4`까지 — 이슈 #24의 완료 조건.

**단위 테스트가 구간별로 지키는 것을 여기서는 한 번에 지난다.** 다른 테스트들은 경계마다
대역을 끼워 그 구간만 보지만, 이 파일이 확인하는 것은 구간들이 실제로 이어지는가다 —
PRD 13장 성공 기준의 첫 항목("하나의 주제 입력으로 `final_short.mp4`가 생성된다")과
"같은 입력으로 실패 없이 반복 실행할 수 있다"가 그것이다.

대역을 두는 경계는 **네트워크로 나가는 둘뿐이다.**

- **LLM은 `conftest.StubLLM`으로 충분하다.** 넘긴 JSON Schema에서 값을 만들므로 생성기와
  검증기가 실제로 쓰는 계약을 그대로 지난다.
- **TTS는 `conftest.StubTTS`를 쓸 수 없다.** 그것이 쓰는 `b"stub-audio"`는 오디오가 아니라
  진짜 `ffprobe`가 길이를 재지 못하고 진짜 `ffmpeg`가 디코드하지 못한다. 아래 `ToneTTS`가
  문장 길이에 비례한 **실제 오디오**를 낸다.
- **`conftest.stub_ffmpeg`를 쓰지 않는다.** 그 대역은 `ffprobe`까지 가짜 길이로 답하므로
  duration 확정이 실측 경로를 지나지 않는다. 여기서 재는 값은 전부 진짜여야 한다.
  그래서 FFmpeg가 없는 환경에서는 실패가 아니라 skip이다.

**실행은 세 번뿐이고 확인은 그 결과에 대해서 한다.** 27초 남짓한 1080x1920 인코딩이 실행당
붙으므로 테스트마다 파이프라인을 다시 돌리면 이 파일 하나가 분 단위로 늘어난다. 모듈 스코프
픽스처가 (1) 같은 입력 2회 연속 + (2) `--fail-on-flagged` 1회를 돌리고, 각 테스트는 그
산출물을 읽는다.

`quiz.question_count`를 타입 하한인 3으로 내리는 것도 같은 이유다 — 검사 대상은 영상 길이가
아니라 경로다. `tts.cache_dir`도 함께 옮긴다. 기본값 `.cache/tts`는 실행 디렉터리 기준이라
그대로 두면 테스트가 저장소에 오디오를 쌓는다.

실제 provider로 도는 **로컬 실전 모드**는 README "설치와 실행"에 절차가 있다. 이 파일이
지나지 않는 것 — 실제 모델·음성의 품질과 영상의 시각적 내용 — 을 사람이 그때 본다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from conftest import StubLLM

from shorts_maker import narration
from shorts_maker.captions import CAPTIONS_NAME
from shorts_maker.llm import registry as llm_registry
from shorts_maker.llm.claude_cli import PROVIDER_NAME as LLM_PROVIDER_NAME
from shorts_maker.main import EXIT_FLAGGED, main
from shorts_maker.run_context import LOG_FILENAME
from shorts_maker.schemas import (
    METADATA_SCHEMA,
    PROJECT_SCHEMA,
    SCENES_SCHEMA,
    load_metadata,
    load_project,
    load_scenes,
)
from shorts_maker.schemas.scenes import SEGMENT_DIR, segment_path
from shorts_maker.shorts_types import DEFAULT_TYPE, get_type
from shorts_maker.timeline import VOICE_TRACK
from shorts_maker.tts import registry as tts_registry
from shorts_maker.tts.edge_tts import PROVIDER_NAME as TTS_PROVIDER_NAME
from shorts_maker.tts.provider import TTSError
from shorts_maker.video_renderer import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    FPS,
    OUTPUT_NAME,
    align,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg가 없다 — 전 구간 스모크는 실측이 진짜여야 한다",
)

TOPIC = "세계 지리 상식"
QUESTION_COUNT = 3
"""퀴즈 타입의 문제 수 하한 (`quiz_generator.MIN_QUESTIONS`). 여기서 값을 낮추는 이유는
인코딩 시간이고, 확인 대상은 길이가 아니라 경로다."""

FOREIGN_ARTIFACTS = ("script.txt", "summary.json", "source.json")
"""`--topic` + 퀴즈 경로에서 **생성되지 않아야** 하는 파일 (PRD 6.2 표).

퀴즈는 대본도 요약도 만들지 않고(`SHORTS_TYPE.produces_script`/`produces_summary`),
`source.json`은 **입력 경로가 결정한다** — `--text-file`·`--url` 실행의 산출물이므로 주제 한
줄로 돌린 이 경로에는 없다 (#94). 없는 것이 실패가
아니라 **있는 것이 실패다** — 여기 파일이 생기면 어느 단계가 자기 경로를 벗어난 것이다.
"""


# --- 실제 오디오를 내는 TTS 픽스처 -------------------------------------------


class ToneTTS:
    """문장 길이에 비례한 실제 오디오를 내는 픽스처 provider. 네트워크로 나가지 않는다.

    **`conftest.StubTTS`와 갈라지는 지점이 이 파일의 전제다.** 그쪽은 오디오가 아닌 바이트를
    쓰므로 길이 측정까지 함께 가짜여야 하지만, 여기서는 `ffprobe`가 이 파일을 실제로 재고
    그 값이 `duration`·`narration_offset`·자막 타임코드·오버레이 구간으로 흘러가야 한다.

    길이를 글자 수에 비례시키는 이유는 **문장마다 다른 값이 나와야 하기 때문이다.** 전부
    같은 길이면 세그먼트가 뒤바뀌어도 타임라인이 그대로여서 배선 오류가 드러나지 않는다.
    """

    name = TTS_PROVIDER_NAME
    supports_word_timings = False

    SEC_PER_CHAR = 0.12
    MIN_SEC = 0.6
    MAX_SEC = 6.0
    """실제 낭독의 대략적인 속도대. 정확할 필요는 없고 문장마다 갈리기만 하면 된다."""

    FREQUENCY = 440
    SAMPLE_RATE = 48000

    def __init__(self) -> None:
        self.calls: list[str] = []
        """합성한 문장. 캐시 적중은 여기 남지 않는다 — 그것이 캐시가 하는 일이다."""

        self.voice = "fixture-voice"

    def duration_for(self, text: str) -> float:
        return round(min(max(len(text) * self.SEC_PER_CHAR, self.MIN_SEC), self.MAX_SEC), 3)

    def synthesize(self, text: str, destination: Path) -> None:
        self.calls.append(text)
        completed = subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-t", f"{self.duration_for(text):.3f}",
             "-i", f"sine=frequency={self.FREQUENCY}:sample_rate={self.SAMPLE_RATE}",
             "-ac", "1", str(destination)],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise TTSError(
                f"픽스처 합성이 실패했다: {completed.stderr.strip()!r}", retryable=False
            )
        return None

    def factory(
        self, *, voice: str, options: Mapping[str, Any], timeout_sec: int
    ) -> ToneTTS:
        self.voice = voice
        return self


# --- 파이프라인 실행 ----------------------------------------------------------


@dataclass(frozen=True)
class Smoke:
    """세 번의 실행 결과. 각 테스트는 여기서 읽기만 한다."""

    runs: tuple[Path, ...]
    """같은 입력으로 연속 2회 실행한 run 디렉터리 (오래된 순)."""

    exit_codes: tuple[int, ...]
    """위 2회의 종료 코드."""

    gated: Path
    """`--fail-on-flagged`를 붙인 3회차의 run 디렉터리."""

    gated_exit: int
    snapshot: dict[str, str]
    """1회차 산출물의 (경로 → 내용 해시). **1회차 직후에 뜬 값이다** — 2·3회차가 끝난 뒤에
    뜨면 무엇과 비교해도 같아서 훼손을 잡지 못한다."""

    tts_calls: tuple[str, ...]


def run_dirs(output_root: Path) -> list[Path]:
    return sorted(path for path in output_root.iterdir() if path.is_dir())


def fingerprint(root: Path) -> dict[str, str]:
    """디렉터리 안 모든 파일의 내용 해시. 반복 실행이 앞선 run을 훼손했는지 이 값이 답한다."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def smoke(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Smoke]:
    """`--topic` 하나로 파이프라인을 세 번 돌린다. 대역은 LLM과 TTS 둘뿐이다.

    **`monkeypatch` 픽스처를 쓰지 않는다** — 그것은 함수 스코프라 모듈 스코프에서 부를 수
    없다. 하는 일은 같고, `undo()`가 이 모듈 밖으로 대역이 새지 않게 한다.
    """
    base = tmp_path_factory.mktemp("e2e")
    output_root = base / "outputs"
    config_path = base / "config.yaml"
    config_path.write_text(
        f"quiz:\n"
        f"  question_count: {QUESTION_COUNT}\n"
        f"tts:\n"
        f"  cache_dir: {(base / 'tts-cache').as_posix()}\n",
        encoding="utf-8",
    )
    command = ["--topic", TOPIC, "--type", DEFAULT_TYPE,
               "--out", str(output_root), "--config", str(config_path)]

    llm, tts = StubLLM(), ToneTTS()
    patch = pytest.MonkeyPatch()
    patch.setitem(llm_registry.BUILTIN_PROVIDERS, LLM_PROVIDER_NAME, llm.factory)  # type: ignore[arg-type]
    patch.setitem(tts_registry.BUILTIN_PROVIDERS, TTS_PROVIDER_NAME, tts.factory)  # type: ignore[arg-type]
    try:
        first_exit, first = _run(command, output_root)
        # **여기서 뜬다.** 2·3회차가 끝난 뒤에 뜨면 무엇과 비교해도 같다.
        snapshot = fingerprint(first)
        second_exit, second = _run(command, output_root)
        # 같은 입력을 검수 게이트만 켜서 한 번 더. 갈리는 것이 종료 코드뿐인지 보려면
        # 앞 두 실행과 같은 입력이어야 한다.
        gated_exit, gated = _run([*command, "--fail-on-flagged"], output_root)
    finally:
        patch.undo()

    yield Smoke(
        runs=(first, second),
        exit_codes=(first_exit, second_exit),
        gated=gated,
        gated_exit=gated_exit,
        snapshot=snapshot,
        tts_calls=tuple(tts.calls),
    )


def _run(command: list[str], output_root: Path) -> tuple[int, Path]:
    """파이프라인 1회. 이번 실행이 만든 run 디렉터리를 함께 돌려준다.

    이름으로 고르지 않는다 — 같은 초에 두 번 시작하면 `-2` 접미사가 붙고(`run_context`),
    정렬 규칙을 여기 다시 적으면 그 규칙이 두 곳에 생긴다. 늘어난 하나가 이번 것이다.
    """
    before = set(run_dirs(output_root)) if output_root.is_dir() else set()
    exit_code = main(command)
    created = set(run_dirs(output_root)) - before

    assert len(created) == 1, f"이번 실행이 만든 run 디렉터리가 하나여야 한다: {created}"
    return exit_code, created.pop()


@pytest.fixture(scope="module")
def run_dir(smoke: Smoke) -> Path:
    """1회차 run 디렉터리. 산출물 검사는 전부 여기를 본다."""
    return smoke.runs[0]


def probe(path: Path, entries: str) -> Any:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def narrated_indexes(scenes: Mapping[str, Any]) -> list[int]:
    return [
        index for index, scene in enumerate(scenes["scenes"]) if scene.get("narrate")
    ]


# --- 산출물 (PRD 6.2 표) ------------------------------------------------------


def test_one_topic_produces_the_final_video(smoke: Smoke, run_dir: Path) -> None:
    """PRD 13장 성공 기준의 첫 항목. 이 한 줄이 수직 슬라이스의 정의다."""
    assert smoke.exit_codes == (0, 0)
    assert (run_dir / OUTPUT_NAME).is_file()


def test_every_artifact_of_this_path_is_present(run_dir: Path) -> None:
    """`--topic` + `--type quiz` 경로가 남기는 파일 전부 (PRD 6.2 표).

    파일명을 문자열로 적지 않고 소유한 모듈에서 가져온다 — 이름을 바꾸는 이슈가 여기를
    함께 고치지 않으면 이 테스트가 통과해 버린다.
    """
    expected = [
        get_type(DEFAULT_TYPE).content_artifact,
        SCENES_SCHEMA.name,
        METADATA_SCHEMA.name,
        PROJECT_SCHEMA.name,
        CAPTIONS_NAME,
        VOICE_TRACK,
        OUTPUT_NAME,
        LOG_FILENAME,
        f"{SEGMENT_DIR}/{narration.MANIFEST_NAME}",
    ]

    missing = [name for name in expected if not (run_dir / name).is_file()]
    assert not missing, f"산출물이 없다: {missing}"


def test_there_is_one_segment_per_narrated_scene(run_dir: Path) -> None:
    """개수와 번호가 곧 계약이다 (PRD 7.5.2). 번호는 장면 배열 인덱스다."""
    scenes = load_scenes(run_dir / SCENES_SCHEMA.name, finalized=True)
    indexes = narrated_indexes(scenes)

    # 문제마다 question·answer 둘이 낭독된다 (`scene_template`). hook·countdown·cta는 아니다.
    assert len(indexes) == QUESTION_COUNT * 2
    assert sorted(path.name for path in (run_dir / SEGMENT_DIR).glob("seg-*.mp3")) == [
        Path(segment_path(index)).name for index in indexes
    ]


def test_no_artifact_of_another_input_path_is_created(run_dir: Path) -> None:
    """산출물은 타입·입력 경로에 따라 다르다 (PRD 6.2 표)."""
    present = [name for name in FOREIGN_ARTIFACTS if (run_dir / name).exists()]

    assert not present, f"이 경로에서 나오지 않아야 하는 파일이다: {present}"


# --- 계약 (스키마) -----------------------------------------------------------


def test_the_scenes_are_finalized_and_the_project_opens_them(run_dir: Path) -> None:
    """`scenes.json`이 확정 상태이고 `project.json`이 그것을 가리킨다 (퀴즈 스펙 4장).

    자막과 렌더러가 확정 상태만 입력으로 받으므로, 여기서 확정 검증이 통과하지 않으면
    영상이 나왔더라도 그 길이가 `voice.mp3`와 맞는다는 보장이 없다.
    """
    project = load_project(run_dir / PROJECT_SCHEMA.name)
    load_metadata(run_dir / METADATA_SCHEMA.name)

    scenes = load_scenes(run_dir / project["scenes"], finalized=True)
    assert scenes["type"] == project["type"] == DEFAULT_TYPE
    assert project["audio"]["voice"] == VOICE_TRACK


# --- MP4 규격 (PRD 6.3) -------------------------------------------------------


def test_the_output_meets_the_format_spec(run_dir: Path) -> None:
    """1080x1920 / 30fps / H.264(yuv420p) + AAC 하나.

    **오디오 스트림 수를 여기서도 본다.** 효과음이 얹힌 뒤(#23)에도 하나로 나와야 하고,
    `test_video_renderer.py`가 보는 것은 효과음이 없는 최소 입력이다.
    """
    streams = probe(
        run_dir / OUTPUT_NAME,
        "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt",
    )["streams"]
    video = [stream for stream in streams if stream["codec_type"] == "video"]
    audio = [stream for stream in streams if stream["codec_type"] == "audio"]

    assert len(video) == 1 and len(audio) == 1
    assert (video[0]["width"], video[0]["height"]) == (CANVAS_WIDTH, CANVAS_HEIGHT)
    assert video[0]["codec_name"] == "h264"
    assert video[0]["pix_fmt"] == "yuv420p"
    assert video[0]["r_frame_rate"] == f"{FPS}/1"
    assert audio[0]["codec_name"] == "aac"


def test_the_container_length_matches_the_frame_aligned_total(run_dir: Path) -> None:
    """길이는 장면 템플릿의 목표치가 아니라 실측에서 확정된 값에서 나온다 (PRD 7.5.1).

    `align()`이 그 확정값을 프레임 경계에 맞춘 결과가 컨테이너 길이여야 한다 — 한 프레임
    안에서 맞으면 낭독·자막·오버레이가 같은 시간축 위에 있다는 뜻이다 (PRD 7.7).
    """
    scenes = load_scenes(run_dir / SCENES_SCHEMA.name, finalized=True)
    expected = align(scenes).total_sec

    duration = float(
        probe(run_dir / OUTPUT_NAME, "format=duration")["format"]["duration"]
    )
    assert duration == pytest.approx(expected, abs=1 / FPS)


# --- 반복 실행 (PRD 13장) -----------------------------------------------------


def test_running_twice_succeeds_and_keeps_the_earlier_run(smoke: Smoke) -> None:
    """"같은 입력으로 실패 없이 반복 실행할 수 있다" — PRD 13장 성공 기준.

    **두 번째 실행이 첫 번째를 훼손하지 않는다.** run 디렉터리가 갈리는 것만으로는
    부족하다 — 상대 경로로 쓰는 단계가 하나라도 실행 디렉터리를 기준으로 삼으면 앞선
    run의 파일을 덮어쓴다.
    """
    first, second = smoke.runs

    assert smoke.exit_codes == (0, 0)
    assert first != second
    assert fingerprint(first) == smoke.snapshot
    assert (second / OUTPUT_NAME).is_file()


def test_the_second_run_stands_on_its_own(smoke: Smoke) -> None:
    """두 run이 서로를 참조하지 않는다 — 앞선 run을 지워도 뒤의 것이 열려야 한다."""
    first, second = smoke.runs

    assert set(fingerprint(second)) == set(smoke.snapshot)
    assert load_scenes(second / SCENES_SCHEMA.name, finalized=True)["scenes"]


def test_the_cache_keeps_the_second_run_from_synthesizing_again(smoke: Smoke) -> None:
    """캐시는 run 디렉터리 **밖**에 있다 (PRD 7.5.2). run마다 새 디렉터리이므로 안에 두면
    반복 실행이 매번 전부 재합성한다 — 그때도 결과는 같으므로 위 테스트로는 드러나지 않는다.

    같은 문장이 여러 장면에 나오면 합성은 한 번이므로 호출 수를 세그먼트 수와 비교하지
    않는다. 보는 것은 "2·3회차가 합성을 하나도 더하지 않았다"이다.
    """
    unique = set(smoke.tts_calls)

    assert len(smoke.tts_calls) == len(unique), f"같은 문장을 다시 합성했다: {smoke.tts_calls}"


# --- 검수 게이트 (#11) --------------------------------------------------------


def flagged_questions(run_dir: Path) -> list[dict[str, Any]]:
    content = json.loads(
        (run_dir / get_type(DEFAULT_TYPE).content_artifact).read_text(encoding="utf-8")
    )
    return [
        question
        for question in content["questions"]
        if question["verify"]["status"] == "flagged"
    ]


def test_the_smoke_input_really_is_flagged(run_dir: Path) -> None:
    """아래 두 테스트의 전제. 스키마에서 만든 응답은 확신도가 없어 임계값에 걸린다 —
    전제가 무너지면 "flagged인데도 렌더됐다"가 아니라 "flagged가 아니었다"가 된다."""
    assert flagged_questions(run_dir)


def test_flagged_content_still_renders_and_exits_zero(
    smoke: Smoke, run_dir: Path
) -> None:
    """검수 주체는 사람이고 사람은 산출물이 있어야 검수한다 (PRD 2장, #11).

    `main.py`의 게이트 주석("경고가 렌더를 막지 않는다 — #24가 확인한다")이 가리키는
    자리이며, 그 게이트와 렌더 사이에는 이제 파이프라인 전 구간이 있다.
    """
    assert smoke.exit_codes[0] == 0
    assert (run_dir / OUTPUT_NAME).is_file()


def test_fail_on_flagged_changes_the_exit_code_and_nothing_else(smoke: Smoke) -> None:
    """멈추는 것은 **이후 단계**이지 이번 실행의 산출물이 아니다 (#11).

    종료 코드도 실패와 갈린다 — 배치 스크립트가 "생성이 깨졌다"와 "검수가 필요하다"에
    같은 대응을 하면 안 된다.
    """
    assert smoke.gated_exit == EXIT_FLAGGED
    assert smoke.gated_exit != 0
    assert flagged_questions(smoke.gated)
    # 산출물은 게이트 없이 돈 run과 같은 집합이다 — 영상까지 포함해서다.
    assert set(fingerprint(smoke.gated)) == set(smoke.snapshot)
    assert (smoke.gated / OUTPUT_NAME).is_file()


# --- 이어 돌리기 (#36) --------------------------------------------------------


def test_a_run_missing_only_its_video_is_resumed(smoke: Smoke) -> None:
    """#36 완료 조건 — 렌더 단계만 실패한 run을 이어 돌리면 성공하고 모델을 부르지 않는다.

    **모듈 스코프 파이프라인 실행 수를 늘리지 않는다.** 새로 돌리는 것이 아니라 이미 있는
    run 디렉터리의 mp4 하나를 다시 만들고, 세 번째 run을 쓰므로 앞의 둘을 건드리지 않는다.

    **대역은 이미 걷혔다** (`smoke` 픽스처의 `patch.undo()`). 그래서 "LLM·TTS를 부르지
    않는다"가 여기서는 계약이 아니라 실측이다 — 불렀다면 네트워크로 나간다. `--fail-on-flagged`
    로 멈춘 run이라는 것도 함께 확인된다: 검수 판정이 이어 돌리기를 막지 않는다.
    """
    run_dir = smoke.gated
    before = fingerprint(run_dir)
    (run_dir / OUTPUT_NAME).unlink()

    assert main(["--resume", str(run_dir)]) == 0

    after = fingerprint(run_dir)
    # 다시 만든 것이 mp4 하나다. `run.log`는 이어 돌리기 기록이 붙어 달라진다.
    assert set(after) == set(before)
    assert {name for name in before if before[name] != after[name]} <= {
        LOG_FILENAME,
        OUTPUT_NAME,
    }
    # 이어 돌린 렌더도 확정된 타임라인으로 돈다 — 프레임 정렬 총 길이와 맞아야 한다.
    scenes = load_scenes(run_dir / SCENES_SCHEMA.name, finalized=True)
    duration = float(
        probe(run_dir / OUTPUT_NAME, "format=duration")["format"]["duration"]
    )
    assert duration == pytest.approx(align(scenes).total_sec, abs=1 / FPS)
