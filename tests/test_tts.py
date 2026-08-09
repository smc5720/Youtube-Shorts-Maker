"""TTS provider 레이어 — 이슈 #14의 완료 조건에 대응한다.

**실제로 합성하지 않는다.** edge-tts는 네트워크 호출이고 비공식 엔드포인트라(스파이크 #2
5.1) 테스트가 그것에 매달리면 회귀가 아니라 엔드포인트 상태를 측정하게 된다. 대신 두
경계를 가짜로 바꿔 끼운다 — 합성 경계(`SpeechStream`)와 길이 측정 경계(`DurationProbe`).
`test_llm.py`가 `CommandRunner`를 바꾸는 것과 같은 방식이다.

`edge_tts` 모듈 자체도 설치되지 않은 환경에서 통과해야 한다. `boundary` 인자를 확인하는
테스트는 `sys.modules`에 가짜 모듈을 넣어 지연 import를 가로챈다.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from shorts_maker.config import SPEC, Config, defaults, load_config
from shorts_maker.main import EXIT_CONFIG_ERROR, main
from shorts_maker.run_context import LOG_FILENAME, run_logging
from shorts_maker.tts import (
    RetryingProvider,
    Speech,
    SpeechCache,
    SpeechSynthesizer,
    TTSError,
    UnknownTTSProviderError,
    WordTiming,
    available_providers,
    create_synthesizer,
    probe_duration,
    validate_tts_provider,
)
from shorts_maker.tts import registry, speech as speech_module
from shorts_maker.tts.edge_tts import (
    BOUNDARY,
    HNS_PER_SEC,
    PROVIDER_NAME,
    EdgeTTSProvider,
    stream_speech,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

TEXT = "세계에서 가장 긴 강은?"
PROBED_SEC = 7.5
"""가짜 측정값. provider가 아니라 측정 경계에서 나온 값임을 알아볼 수 있게 실측과 다른 값이다."""

TIMINGS = (WordTiming(text="강은", offset_sec=0.5, duration_sec=0.65),)


# --- 대역 -------------------------------------------------------------------


class StubTTS:
    """`TTSProvider` 대역. 응답을 순서대로 돌려주고 목록이 바닥나면 마지막을 반복한다."""

    name = "stub_tts"

    def __init__(self, *responses: Any, supports_word_timings: bool = True) -> None:
        self.responses = list(responses) or [TIMINGS]
        self.supports_word_timings = supports_word_timings
        self.voice = "stub-voice"
        self.calls: list[str] = []
        self.destinations: list[Path] = []
        self.options: Mapping[str, Any] = {}
        self.timeout_sec = 0

    def synthesize(self, text: str, destination: Path) -> tuple[WordTiming, ...] | None:
        index = min(len(self.calls), len(self.responses) - 1)
        self.calls.append(text)
        self.destinations.append(destination)

        response = self.responses[index]
        if isinstance(response, BaseException):
            raise response
        destination.write_bytes(b"stub-audio")
        return response

    def factory(
        self, *, voice: str, options: Mapping[str, Any], timeout_sec: int
    ) -> StubTTS:
        """`ProviderFactory` 자리에 끼워지는 생성자. 인스턴스를 공유해 호출을 한곳에 모은다."""
        self.voice = voice
        self.options = options
        self.timeout_sec = timeout_sec
        return self

    @property
    def call_count(self) -> int:
        return len(self.calls)


class StubProbe:
    """`DurationProbe` 대역. ffprobe 없이 돌기 위한 것이며 호출 횟수를 센다."""

    def __init__(self, duration_sec: float = PROBED_SEC) -> None:
        self.duration_sec = duration_sec
        self.paths: list[Path] = []

    def __call__(self, path: Path) -> float:
        self.paths.append(path)
        return self.duration_sec

    @property
    def call_count(self) -> int:
        return len(self.paths)


class FakeStream:
    """`SpeechStream` 대역. 넘겨받은 인자를 기록하고 미리 정한 청크를 돌려준다."""

    def __init__(self, *chunks: Any) -> None:
        self.chunks = list(chunks)
        self.calls: list[dict[str, Any]] = []
        self.error: BaseException | None = None

    def __call__(
        self, *, text: str, voice: str, timeout_sec: int
    ) -> Sequence[Mapping[str, Any]]:
        self.calls.append({"text": text, "voice": voice, "timeout_sec": timeout_sec})
        if self.error is not None:
            raise self.error
        return self.chunks


def audio_chunk(data: bytes = b"\x49\x44\x33") -> dict[str, Any]:
    return {"type": "audio", "data": data}


def boundary_chunk(text: str, offset_sec: float, duration_sec: float) -> dict[str, Any]:
    return {
        "type": BOUNDARY,
        "text": text,
        "offset": int(offset_sec * HNS_PER_SEC),
        "duration": int(duration_sec * HNS_PER_SEC),
    }


def synthesizer(
    provider: Any = None, **kwargs: Any
) -> tuple[SpeechSynthesizer, StubTTS, StubProbe]:
    stub = provider if provider is not None else StubTTS()
    probe = StubProbe()
    kwargs.setdefault("measure", probe)
    return SpeechSynthesizer(provider=stub, **kwargs), stub, probe


def config_with(**overrides: Any) -> Config:
    return load_config(overrides=overrides) if overrides else Config(data=defaults())


# --- 계약: 길이는 provider가 보고하지 않는다 --------------------------------


def test_duration_comes_from_the_probe_not_the_provider(tmp_path: Path) -> None:
    """계약에 길이 칸이 없다. 값이 들어오는 유일한 경로가 측정 경계다 (스파이크 5.3)."""
    target, stub, probe = synthesizer()
    destination = tmp_path / "audio" / "seg-003.mp3"

    result = target.synthesize(TEXT, destination)

    assert result.duration_sec == PROBED_SEC
    assert probe.paths == [destination]
    assert stub.calls == [TEXT]


def test_synthesize_writes_the_audio_to_the_requested_path(tmp_path: Path) -> None:
    """세그먼트 경로는 호출자(#15)가 정한다. 디렉터리가 없으면 만든다."""
    target, _, _ = synthesizer()
    destination = tmp_path / "audio" / "seg-003.mp3"

    result = target.synthesize(TEXT, destination)

    assert result.path == destination
    assert destination.read_bytes() == b"stub-audio"


def test_failed_measurement_stops_the_synthesis(tmp_path: Path) -> None:
    def broken(path: Path) -> float:
        raise TTSError("길이를 읽지 못했다", retryable=False)

    target, _, _ = synthesizer(measure=broken)

    with pytest.raises(TTSError, match="길이를 읽지 못했다"):
        target.synthesize(TEXT, tmp_path / "seg.mp3")


# --- 계약: 단어 타이밍은 선택이다 -------------------------------------------


def test_word_timings_are_none_when_the_provider_does_not_supply_them(
    tmp_path: Path,
) -> None:
    """필수로 요구하면 provider 교체가 막힌다 (스파이크 5.3)."""
    stub = StubTTS(None, supports_word_timings=False)
    target, _, _ = synthesizer(stub)

    result = target.synthesize(TEXT, tmp_path / "seg.mp3")

    assert result.word_timings is None
    assert result.duration_sec == PROBED_SEC  # 합성 자체는 끝까지 돈다


def test_word_timings_pass_through_when_supplied(tmp_path: Path) -> None:
    target, _, _ = synthesizer()

    assert target.synthesize(TEXT, tmp_path / "seg.mp3").word_timings == TIMINGS


# --- edge_tts adapter --------------------------------------------------------


def test_adapter_asks_for_word_boundary_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본값 `SentenceBoundary`면 이벤트가 문장 하나뿐이라 쓸모가 없다 (스파이크 4.7).

    설치되지 않은 `edge_tts`를 가짜 모듈로 대신해 지연 import를 가로챈다.
    """
    recorded: list[dict[str, Any]] = []

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, **kwargs: Any) -> None:
            recorded.append({"text": text, "voice": voice, **kwargs})

        async def stream(self) -> Any:
            yield audio_chunk()

    module = types.ModuleType("edge_tts")
    module.Communicate = FakeCommunicate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edge_tts", module)

    chunks = stream_speech(text=TEXT, voice="ko-KR-SunHiNeural", timeout_sec=5)

    assert recorded == [
        {"text": TEXT, "voice": "ko-KR-SunHiNeural", "boundary": BOUNDARY}
    ]
    assert list(chunks) == [audio_chunk()]


def test_socket_timeout_is_not_mistaken_for_our_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """소켓 타임아웃도 `TimeoutError`다. 그대로 두면 재시도하지 않는 실패로 둔갑한다."""

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, **kwargs: Any) -> None:
            pass

        async def stream(self) -> Any:
            raise TimeoutError("소켓이 응답하지 않는다")
            yield  # pragma: no cover — 위에서 끝난다

    module = types.ModuleType("edge_tts")
    module.Communicate = FakeCommunicate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edge_tts", module)

    with pytest.raises(ConnectionError):
        stream_speech(text=TEXT, voice="ko-KR-SunHiNeural", timeout_sec=60)


def test_adapter_writes_audio_and_converts_boundary_units(tmp_path: Path) -> None:
    """edge-tts는 100나노초 단위로 준다. 초로 바꿔 계약에 싣는다."""
    stream = FakeStream(
        audio_chunk(b"\x01\x02"),
        boundary_chunk("세계에서", 0.0, 0.5),
        audio_chunk(b"\x03"),
        boundary_chunk("강은", 1.15, 0.65),
    )
    target = EdgeTTSProvider(voice="ko-KR-SunHiNeural", stream=stream)
    destination = tmp_path / "seg.mp3"

    timings = target.synthesize(TEXT, destination)

    assert destination.read_bytes() == b"\x01\x02\x03"
    assert timings == (
        WordTiming(text="세계에서", offset_sec=0.0, duration_sec=0.5),
        WordTiming(text="강은", offset_sec=1.15, duration_sec=0.65),
    )


def test_adapter_passes_voice_and_timeout_to_the_boundary(tmp_path: Path) -> None:
    stream = FakeStream(audio_chunk())
    target = EdgeTTSProvider(voice="ko-KR-InJoonNeural", timeout_sec=42, stream=stream)

    target.synthesize(TEXT, tmp_path / "seg.mp3")

    assert stream.calls == [
        {"text": TEXT, "voice": "ko-KR-InJoonNeural", "timeout_sec": 42}
    ]


def test_adapter_reports_word_timing_support() -> None:
    assert EdgeTTSProvider(voice="ko-KR-SunHiNeural").supports_word_timings is True


@pytest.mark.parametrize(
    ("error", "expected", "retryable"),
    [
        (ImportError("No module named 'edge_tts'"), "pip install edge-tts", False),
        (TimeoutError(), "60초", False),
        (ConnectionResetError("연결이 끊겼다"), "ConnectionResetError", True),
    ],
)
def test_adapter_classifies_failures(
    tmp_path: Path, error: BaseException, expected: str, retryable: bool
) -> None:
    """모듈 부재·타임아웃은 같은 조건에서 같은 결과가 나오므로 재시도하지 않는다."""
    stream = FakeStream()
    stream.error = error
    target = EdgeTTSProvider(voice="ko-KR-SunHiNeural", timeout_sec=60, stream=stream)

    with pytest.raises(TTSError) as raised:
        target.synthesize(TEXT, tmp_path / "seg.mp3")

    assert expected in str(raised.value)
    assert raised.value.retryable is retryable


def test_adapter_does_not_leave_an_empty_file_when_no_audio_arrives(
    tmp_path: Path,
) -> None:
    """빈 파일을 남기면 ffprobe 실패(재시도 불가)로 둔갑해 원인이 가려진다."""
    target = EdgeTTSProvider(
        voice="ko-KR-SunHiNeural", stream=FakeStream(boundary_chunk("강은", 0.0, 0.5))
    )
    destination = tmp_path / "seg.mp3"

    with pytest.raises(TTSError) as raised:
        target.synthesize(TEXT, destination)

    assert raised.value.retryable is True
    assert not destination.exists()


# --- 재시도 -----------------------------------------------------------------


def test_retries_up_to_max_retries_and_succeeds(tmp_path: Path) -> None:
    stub = StubTTS(TTSError("일시 실패"), TIMINGS)
    target = RetryingProvider(inner=stub, max_retries=2)

    assert target.synthesize(TEXT, tmp_path / "seg.mp3") == TIMINGS
    assert stub.call_count == 2


def test_final_failure_names_the_sentence_attempts_and_cause(tmp_path: Path) -> None:
    stub = StubTTS(TTSError("엔드포인트가 응답하지 않는다"))
    target = RetryingProvider(inner=stub, max_retries=2)

    with pytest.raises(TTSError) as raised:
        target.synthesize(TEXT, tmp_path / "seg.mp3")

    message = str(raised.value)
    assert stub.call_count == 3  # 최초 1회 + 재시도 2회
    assert "3회" in message
    assert TEXT in message
    assert "엔드포인트가 응답하지 않는다" in message
    assert raised.value.retryable is False


def test_long_sentence_is_excerpted_in_the_failure_message(tmp_path: Path) -> None:
    long_text = "가" * 200
    target = RetryingProvider(inner=StubTTS(TTSError("실패")), max_retries=0)

    with pytest.raises(TTSError) as raised:
        target.synthesize(long_text, tmp_path / "seg.mp3")

    assert len(str(raised.value)) < len(long_text)


def test_unretryable_failure_is_not_repeated(tmp_path: Path) -> None:
    """타임아웃을 재시도하면 대기 시간만 배가된다."""
    stub = StubTTS(TTSError("타임아웃", retryable=False))
    target = RetryingProvider(inner=stub, max_retries=5)

    with pytest.raises(TTSError) as raised:
        target.synthesize(TEXT, tmp_path / "seg.mp3")

    assert stub.call_count == 1
    assert "1회" in str(raised.value)


def test_no_retries_when_max_retries_is_zero(tmp_path: Path) -> None:
    stub = StubTTS(TTSError("일시 실패"))

    with pytest.raises(TTSError):
        RetryingProvider(inner=stub, max_retries=0).synthesize(TEXT, tmp_path / "seg.mp3")

    assert stub.call_count == 1


def test_retrying_provider_proxies_the_provider_identity() -> None:
    """캐시 키가 provider 이름과 음성으로 만들어지므로 래퍼가 그것을 가려서는 안 된다."""
    target = RetryingProvider(inner=StubTTS(), max_retries=0)

    assert (target.name, target.voice, target.supports_word_timings) == (
        "stub_tts",
        "stub-voice",
        True,
    )


def test_run_log_records_each_synthesis_and_retry(tmp_path: Path) -> None:
    stub = StubTTS(TTSError("일시 실패"), TIMINGS)
    log_path = tmp_path / LOG_FILENAME

    with run_logging(log_path):
        RetryingProvider(inner=stub, max_retries=2).synthesize(TEXT, tmp_path / "seg.mp3")

    log_text = log_path.read_text(encoding="utf-8")
    assert "TTS 합성 실패 (1/3)" in log_text
    assert "voice=stub-voice" in log_text


# --- 캐시 -------------------------------------------------------------------


def cached_synthesizer(
    tmp_path: Path, *, provider: StubTTS | None = None
) -> tuple[SpeechSynthesizer, StubTTS, StubProbe]:
    cache = SpeechCache(directory=tmp_path / "cache")
    return synthesizer(provider, cache=cache)


def test_second_request_skips_synthesis_and_returns_the_same_duration(
    tmp_path: Path,
) -> None:
    target, stub, probe = cached_synthesizer(tmp_path)

    first = target.synthesize(TEXT, tmp_path / "run-1" / "seg.mp3")
    second = target.synthesize(TEXT, tmp_path / "run-2" / "seg.mp3")

    assert stub.call_count == 1
    assert probe.call_count == 1  # 사이드카에 길이가 있으니 다시 재지 않는다
    assert second.duration_sec == first.duration_sec
    assert second.word_timings == first.word_timings
    assert (tmp_path / "run-2" / "seg.mp3").read_bytes() == b"stub-audio"


def test_cache_is_outside_the_run_directory(tmp_path: Path) -> None:
    """run마다 새 디렉터리이므로 안에 두면 반복 실행 비용이 줄지 않는다 (PRD 13)."""
    cache_dir = tmp_path / "cache"
    target, _, _ = synthesizer(cache=SpeechCache(directory=cache_dir))
    run_dir = tmp_path / "outputs" / "run-1"

    target.synthesize(TEXT, run_dir / "audio" / "seg-003.mp3")

    assert cache_dir.exists()
    assert cache_dir not in run_dir.parents


def test_cache_key_separates_voices(tmp_path: Path) -> None:
    """voice만 바꾼 재실행이 이전 목소리의 오디오를 재사용하면 안 된다."""
    cache = SpeechCache(directory=tmp_path / "cache")

    assert cache.key(provider=PROVIDER_NAME, voice="ko-KR-SunHiNeural", text=TEXT) != cache.key(
        provider=PROVIDER_NAME, voice="ko-KR-InJoonNeural", text=TEXT
    )


def test_cache_key_separates_texts_and_providers(tmp_path: Path) -> None:
    cache = SpeechCache(directory=tmp_path / "cache")
    key = cache.key(provider=PROVIDER_NAME, voice="ko-KR-SunHiNeural", text=TEXT)

    assert key != cache.key(provider=PROVIDER_NAME, voice="ko-KR-SunHiNeural", text="다른 문장")
    assert key != cache.key(provider="azure", voice="ko-KR-SunHiNeural", text=TEXT)


def test_word_timings_survive_a_cache_round_trip(tmp_path: Path) -> None:
    target, _, _ = cached_synthesizer(tmp_path)

    target.synthesize(TEXT, tmp_path / "run-1" / "seg.mp3")
    second = target.synthesize(TEXT, tmp_path / "run-2" / "seg.mp3")

    assert second.word_timings == TIMINGS


def test_missing_word_timings_stay_missing_across_the_cache(tmp_path: Path) -> None:
    """`None`(주지 않는다)과 `()`(주는데 없다)를 캐시가 뭉개면 안 된다."""
    target, _, _ = cached_synthesizer(
        tmp_path, provider=StubTTS(None, supports_word_timings=False)
    )

    target.synthesize(TEXT, tmp_path / "run-1" / "seg.mp3")

    assert target.synthesize(TEXT, tmp_path / "run-2" / "seg.mp3").word_timings is None


def test_corrupt_sidecar_is_a_miss_not_a_failure(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    target, stub, _ = synthesizer(cache=SpeechCache(directory=cache_dir))
    target.synthesize(TEXT, tmp_path / "run-1" / "seg.mp3")
    for sidecar in cache_dir.glob("*.json"):
        sidecar.write_text("{깨진 JSON", encoding="utf-8")

    result = target.synthesize(TEXT, tmp_path / "run-2" / "seg.mp3")

    assert stub.call_count == 2
    assert result.duration_sec == PROBED_SEC


def test_cache_write_failure_does_not_lose_the_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """캐시는 속도를 위한 것이다. 못 썼다고 만들어진 오디오를 버리지 않는다."""
    target, _, _ = synthesizer(cache=SpeechCache(directory=tmp_path / "cache"))

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError("읽기 전용")

    monkeypatch.setattr(speech_module.shutil, "copyfile", refuse)
    destination = tmp_path / "run-1" / "seg.mp3"

    assert target.synthesize(TEXT, destination).duration_sec == PROBED_SEC
    assert destination.read_bytes() == b"stub-audio"


def test_disabled_cache_neither_reads_nor_writes(tmp_path: Path) -> None:
    target, stub, _ = synthesizer()  # cache=None

    target.synthesize(TEXT, tmp_path / "run-1" / "seg.mp3")
    target.synthesize(TEXT, tmp_path / "run-2" / "seg.mp3")

    assert stub.call_count == 2
    assert [path.name for path in tmp_path.iterdir()] == ["run-1", "run-2"]


# --- 길이 측정 (ffprobe) ----------------------------------------------------


class FakeFFprobe:
    def __init__(self, stdout: str = "5.11", stderr: str = "") -> None:
        self.result = subprocess.CompletedProcess([], 0, stdout, stderr)
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> Any:
        self.commands.append(command)
        return self.result


def test_probe_reads_the_container_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """디코딩 값과의 차이가 프레임 이하라 컨테이너 값을 쓴다 (스파이크 4.1)."""
    fake = FakeFFprobe("5.11\n")
    monkeypatch.setattr(speech_module.subprocess, "run", fake)

    assert probe_duration(tmp_path / "seg.mp3") == pytest.approx(5.11)
    assert "format=duration" in fake.commands[0]


def test_probe_without_ffprobe_names_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "not found")

    monkeypatch.setattr(speech_module.subprocess, "run", missing)

    with pytest.raises(TTSError) as raised:
        probe_duration(tmp_path / "seg.mp3")

    assert "ffprobe" in str(raised.value)
    assert raised.value.retryable is False


def test_probe_reports_unreadable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(speech_module.subprocess, "run", FakeFFprobe("", "moov atom not found"))

    with pytest.raises(TTSError) as raised:
        probe_duration(tmp_path / "seg.mp3")

    assert "moov atom not found" in str(raised.value)


# --- 설정 연동 --------------------------------------------------------------


def test_changing_the_provider_setting_swaps_the_implementation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """호출 코드는 그대로다 — 바뀌는 것은 `tts.provider` 값 하나다."""
    stub = StubTTS()
    monkeypatch.setitem(registry.BUILTIN_PROVIDERS, stub.name, stub.factory)
    monkeypatch.setattr(speech_module.subprocess, "run", FakeFFprobe("2.66"))
    config = config_with(**{"tts.provider": stub.name, "tts.cache_dir": None})

    result = create_synthesizer(config).synthesize(TEXT, tmp_path / "seg.mp3")

    assert isinstance(result, Speech)
    assert result.duration_sec == pytest.approx(2.66)
    assert stub.call_count == 1


def test_create_synthesizer_reads_voice_timeout_retries_and_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = StubTTS()
    monkeypatch.setitem(registry.BUILTIN_PROVIDERS, stub.name, stub.factory)
    config = config_with(
        **{
            "tts.provider": stub.name,
            "tts.voice": "ko-KR-InJoonNeural",
            "tts.timeout_sec": 30,
            "tts.max_retries": 4,
            "tts.cache_dir": "tmp/tts",
        }
    )

    target = create_synthesizer(config)

    assert isinstance(target.provider, RetryingProvider)
    assert target.provider.max_retries == 4
    assert target.provider.voice == "ko-KR-InJoonNeural"
    assert stub.timeout_sec == 30
    assert target.cache is not None
    assert target.cache.directory == Path("tmp/tts")


def test_null_cache_dir_turns_the_cache_off() -> None:
    assert create_synthesizer(config_with(**{"tts.cache_dir": None})).cache is None


def test_default_config_creates_the_edge_tts_provider() -> None:
    target = create_synthesizer(config_with())

    assert target.provider.name == PROVIDER_NAME
    assert target.provider.voice == "ko-KR-SunHiNeural"


def test_provider_without_a_config_section_gets_empty_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`edge_tts`는 여는 키가 없다. 빈 섹션을 미리 만들면 미지의 키 검출과 충돌한다."""
    stub = StubTTS()
    monkeypatch.setitem(registry.BUILTIN_PROVIDERS, stub.name, stub.factory)

    create_synthesizer(config_with(**{"tts.provider": stub.name}))

    assert stub.options == {}
    assert "providers" not in SPEC["tts"]


def test_default_config_passes_provider_validation() -> None:
    validate_tts_provider(config_with())


def test_unknown_provider_name_lists_the_registered_ones() -> None:
    config = config_with(**{"tts.provider": "azure_speech"})

    with pytest.raises(UnknownTTSProviderError) as raised:
        validate_tts_provider(config)

    message = str(raised.value)
    assert "tts.provider" in message
    for name in available_providers():
        assert name in message


def test_unknown_provider_exits_before_creating_the_run_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tts:\n  provider: azure_speech\n", encoding="utf-8")
    output_root = tmp_path / "out"

    exit_code = main(
        ["--topic", "주제", "--out", str(output_root), "--config", str(config_path)]
    )

    assert exit_code == EXIT_CONFIG_ERROR
    assert "azure_speech" in capsys.readouterr().err
    assert not output_root.exists()


def test_edge_tts_is_declared_as_a_dependency() -> None:
    """지연 import라 없어도 레지스트리는 돈다 — 선언이 빠진 것을 실행 전에 잡는다."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["dependencies"]

    assert any(item.startswith("edge-tts") for item in declared)
