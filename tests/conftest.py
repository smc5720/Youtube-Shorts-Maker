"""테스트 공용 픽스처.

**어떤 테스트도 실제 LLM이나 TTS를 부르지 않는다.** LLM은 호출 1회에 수 초와 수 센트가
들고, TTS는 비공식 엔드포인트로 나간다 (스파이크 #2 5.1). 결과가 네트워크·인증 상태에
따라 흔들리면 회귀가 아니라 엔드포인트 상태를 측정하게 된다.

`test_llm.py`·`test_tts.py`는 subprocess와 스트림 경계를 가짜로 바꾸지만, 여기서는 그보다
한 단계 위인 **provider 레지스트리**를 바꾼다. 응답 봉투를 흉내 낼 필요 없이 파이프라인이
실제로 쓰는 값만 만들면 되기 때문이다. 재시도·캐시·로그는 `RetryingProvider`와
`SpeechSynthesizer`가 그대로 씌우므로 그 경로도 함께 돈다.
"""

from __future__ import annotations

import importlib.util
import io
import subprocess
from collections.abc import Iterator, Mapping
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from shorts_maker import source as source_module
from shorts_maker.config import SPEC, Setting
from shorts_maker.llm import registry
from shorts_maker.llm.claude_cli import PROVIDER_NAME
from shorts_maker.llm.provider import LLMResult
from shorts_maker.tts import registry as tts_registry
from shorts_maker.tts import speech as speech_module
from shorts_maker.tts.edge_tts import PROVIDER_NAME as TTS_PROVIDER_NAME

STUB_MODEL = "stub-model-1"
"""응답이 보고하는 실제 모델 ID. 요청한 별칭(`opus`)과 달라야 로그 검증이 의미가 있다."""


class StubLLM:
    """등록된 provider를 대신하는 가짜.

    기본 동작은 **요청받은 JSON Schema를 만족하는 값을 만드는 것**이다 — claude CLI가
    `--json-schema`로 하는 일과 같다 (스파이크 2.2). 덕분에 호출부 테스트가 문제 수 같은
    값을 매번 손으로 맞추지 않아도 되고, 넘긴 스키마가 실제로 쓸 수 있는 모양인지도
    함께 검증된다.

    특정 응답이 필요하면 `reply()`로 미리 넣는다. 넣은 것은 순서대로 소비되고, 바닥나면
    다시 스키마에서 만든 값을 돌려준다.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        """호출 1회당 `{"system", "prompt", "schema", "model", "role"}`."""

        self._replies: list[Any] = []

    def reply(self, *payloads: Any) -> None:
        """다음 호출들이 돌려줄 값. `LLMError`를 넣으면 그 호출이 실패한다."""
        self._replies.extend(payloads)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def factory(self, *, model: str, options: Mapping[str, Any], timeout_sec: int) -> StubLLM:
        """`ProviderFactory` 자리에 끼워지는 생성자. 인스턴스를 공유해 호출을 한곳에 모은다."""
        self.model = model
        self.options = options
        self.timeout_sec = timeout_sec
        return self

    # --- LLMProvider ------------------------------------------------------

    name = PROVIDER_NAME
    model = "stub"

    def complete_json(
        self, *, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> LLMResult:
        self.calls.append(
            {"system": system, "prompt": prompt, "schema": schema, "model": self.model}
        )

        payload = self._replies.pop(0) if self._replies else from_schema(schema)
        if isinstance(payload, BaseException):
            raise payload

        return LLMResult(
            data=payload,
            raw="stub",
            model=STUB_MODEL,
            cost_usd=0.0,
            latency_ms=1,
        )


def from_schema(schema: Mapping[str, Any], name: str = "") -> Any:
    """JSON Schema를 만족하는 값 하나를 만든다. 필수 필드만 채운다."""
    kinds = schema.get("type")
    kind = kinds[0] if isinstance(kinds, list) else kinds

    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object":
        properties = schema.get("properties", {})
        return {
            key: from_schema(properties[key], key) for key in schema.get("required", properties)
        }
    if kind == "array":
        item = schema.get("items", {})
        return [from_schema(item, name) for _ in range(max(schema.get("minItems", 1), 1))]
    if kind == "integer" or kind == "number":
        return int(schema.get("minimum", 1))
    if kind == "boolean":
        return True

    text = f"{name} 값".strip() if name else "값"
    limit = schema.get("maxLength")
    return text[:limit] if limit is not None else text


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubLLM]:
    """등록된 provider를 `StubLLM`으로 바꾼다. 실제 claude CLI를 부르지 않는다."""
    stub = StubLLM()
    monkeypatch.setitem(registry.BUILTIN_PROVIDERS, PROVIDER_NAME, stub.factory)  # type: ignore[arg-type]
    yield stub


STUB_SEGMENT_SEC = 2.5
"""가짜 ffprobe가 보고할 세그먼트 길이. 실측 경계에서 온 값임을 알아볼 수 있는 값이다."""


class StubTTS:
    """등록된 TTS provider를 대신하는 가짜. 네트워크로 나가지 않는다.

    실제 오디오가 아닌 바이트를 쓰므로 길이 측정(`ffprobe`)도 함께 가짜로 바꿔야 한다 —
    두 경계는 `SpeechSynthesizer` 안에서 붙어 있다.
    """

    name = TTS_PROVIDER_NAME
    supports_word_timings = False

    def __init__(self) -> None:
        self.calls: list[str] = []
        """합성한 문장. 재실행이 무엇을 다시 합성했는지 이 목록이 답한다."""

        self.voice = "stub-voice"
        self.error: BaseException | None = None
        """지정하면 모든 합성이 이것으로 실패한다."""

    def synthesize(self, text: str, destination: Path) -> None:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        destination.write_bytes(b"stub-audio")
        return None

    def factory(self, *, voice: str, options: Mapping[str, Any], timeout_sec: int) -> StubTTS:
        self.voice = voice
        return self

    @property
    def call_count(self) -> int:
        return len(self.calls)


@pytest.fixture
def stub_tts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
    stub_ffmpeg: StubFFmpeg,
) -> Iterator[StubTTS]:
    """등록된 TTS provider를 가짜로 바꾼다. 길이 측정은 `stub_ffmpeg`가 맡는다.

    캐시 경로도 함께 옮긴다. 기본값 `.cache/tts`는 **실행 디렉터리 기준 상대 경로**라
    (PRD 7.5.2) 그대로 두면 테스트가 저장소에 가짜 오디오를 쌓는다. `tmp_path` 밑에
    두지 않는 이유는 그 디렉터리가 테스트에서 `--out`으로 쓰이기 때문이다 — 캐시가
    run 디렉터리 옆에 생기면 run 개수를 세는 쪽이 그것까지 센다.
    """
    stub = StubTTS()
    monkeypatch.setitem(tts_registry.BUILTIN_PROVIDERS, TTS_PROVIDER_NAME, stub.factory)  # type: ignore[arg-type]
    monkeypatch.setitem(
        SPEC["tts"],
        "cache_dir",
        Setting(str(tmp_path_factory.mktemp("tts-cache")), "str", nullable=True),
    )
    yield stub


class StubFFmpeg:
    """FFmpeg 도구 호출 대역 — 길이 측정(`ffprobe`), 합성 트랙과 최종 렌더(`ffmpeg`)를 받는다.

    **하나로 묶여 있는 이유는 `subprocess.run` 하나를 바꾸기 때문이다.** `tts/speech.py`와
    `timeline.py`, `video_renderer.py`는 같은 `subprocess` 모듈을 부르므로 모듈 속성을 갈라
    끼울 수 없고, 대신 명령 이름과 목적지 확장자로 분기한다.

    가짜 오디오 바이트를 진짜 FFmpeg에 넘길 수 없다는 것이 두 대역의 공통 이유다 —
    `StubTTS`가 쓰는 것은 오디오가 아니다. 실제 인코딩을 확인하는 것은 진짜 FFmpeg를 쓰는
    `tests/test_video_renderer.py`의 렌더 테스트다.
    """

    def __init__(self) -> None:
        self.mix_commands: list[list[str]] = []
        """`voice.mp3`를 만든 명령. 어떤 오프셋으로 불렸는지 이 목록이 답한다."""

        self.render_commands: list[list[str]] = []
        """`final_short.mp4`를 만든 명령."""

        self.mix_returncode = 0
        """0이 아니면 합성 트랙 생성이 실패한다."""

        self.render_returncode = 0
        """0이 아니면 최종 렌더가 실패한다."""

    def __call__(
        self, command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        tool = Path(command[0]).stem
        if tool == "ffprobe":
            return subprocess.CompletedProcess(command, 0, f"{STUB_SEGMENT_SEC}\n", "")
        if tool == "ffmpeg":
            # 목적지는 FFmpeg 명령의 마지막 인자다 (`timeline.mix_voice_track`,
            # `video_renderer.build_command`). 확장자가 어느 단계인지 말해 준다.
            destination = Path(command[-1])
            rendering = destination.suffix == ".mp4"
            commands = self.render_commands if rendering else self.mix_commands
            commands.append(command)
            returncode = self.render_returncode if rendering else self.mix_returncode
            if returncode != 0:
                return subprocess.CompletedProcess(
                    command, returncode, "", "가짜 FFmpeg 실패"
                )
            destination.write_bytes(b"stub-video" if rendering else b"stub-voice")
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"대역이 모르는 외부 명령이다: {command[0]}")

    def popen(self, command: list[str], **kwargs: Any) -> StubProcess:
        """`Popen`으로 부르는 자리는 최종 렌더뿐이다 (#30, `_run_with_progress`).

        **`run` 대역과 같은 판단을 지난다** — 명령을 기록하고 목적지 파일을 쓰고 종료 코드를
        정하는 것이 한 곳에 있어야 `render_returncode`로 실패를 만드는 테스트가 두 경로에서
        갈리지 않는다. 달라지는 것은 결과를 어떤 객체로 돌려주는지뿐이다.
        """
        completed = self(command, **kwargs)
        errors = kwargs.get("stderr")
        if completed.stderr and hasattr(errors, "write"):
            errors.write(completed.stderr)
        # 진행 줄도 흘려 준다. 없으면 `-progress`를 읽는 쪽이 훅을 한 번도 부르지 않아,
        # 진행률을 쓰는 코드가 대역 위에서 조용히 통과한다.
        return StubProcess(completed.returncode, "frame=1\nprogress=end\n")

    @property
    def mix_count(self) -> int:
        return len(self.mix_commands)

    @property
    def render_count(self) -> int:
        return len(self.render_commands)


class StubProcess:
    """`subprocess.Popen` 대역. 이미 끝난 프로세스처럼 답한다."""

    def __init__(self, returncode: int, progress: str) -> None:
        self.returncode = returncode
        self.stdout = io.StringIO(progress)

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def poll(self) -> int:
        return self.returncode

    def kill(self) -> None:  # pragma: no cover - 끝난 프로세스는 죽이지 않는다
        pass


class StubHTTP:
    """링크 가져오기 대역 (#95) — `urllib`이 만드는 opener 자리에 끼워진다.

    **소켓 아래가 아니라 opener를 바꾼다.** 상태 코드·`Content-Type`·`Content-Encoding`·
    도착 URL은 거부 판정이 읽는 값 전부이고, 그것들을 헤더 객체로 흉내 내면 실제 응답에서
    같은 자리를 읽는 코드가 그대로 돈다. 대신 리다이렉트를 실제로 따라가지는 않으므로,
    상한이 걸렸는지는 `handlers`에 실린 핸들러로 확인한다.

    `body`는 **바이트다.** 이 경로의 요점이 "디코드하지 않고 추출기에 넘긴다"이므로
    (`source.py`) 문자열을 받으면 그 계약을 검증할 수 없다.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        """열린 요청 1회당 `{"url", "headers", "timeout"}`. 비어 있으면 네트워크에
        나가지 않았다는 뜻이고, 그것을 확인하는 테스트가 있다."""

        self.handlers: tuple[Any, ...] = ()
        """`build_opener`에 넘어간 핸들러. 리다이렉트 상한이 여기 실린다."""

        self.body = b""
        self.status = 200
        self.content_type = "text/html; charset=utf-8"
        self.content_encoding: str | None = None
        self.final_url: str | None = None
        """리다이렉트가 도착한 곳. `None`이면 요청한 URL 그대로다."""

        self.error: BaseException | None = None
        """지정하면 열기가 이것으로 실패한다 (HTTP 오류·TLS 실패·타임아웃)."""

    def build_opener(self, *handlers: Any) -> StubHTTP:
        self.handlers = handlers
        return self

    def open(self, request: Any, timeout: float | None = None) -> StubResponse:
        self.calls.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "timeout": timeout,
            }
        )
        if self.error is not None:
            raise self.error

        headers = Message()
        if self.content_type:
            headers["Content-Type"] = self.content_type
        if self.content_encoding:
            headers["Content-Encoding"] = self.content_encoding
        return StubResponse(
            self.body, self.status, self.final_url or request.full_url, headers
        )


class StubResponse:
    """`http.client.HTTPResponse` 대역. 읽기 상한이 의미를 가지도록 `amt`를 지킨다."""

    def __init__(self, body: bytes, status: int, url: str, headers: Message) -> None:
        self.body = body
        self.status = status
        self.url = url
        self.headers = headers

    def read(self, amt: int | None = None) -> bytes:
        return self.body if amt is None else self.body[:amt]

    def __enter__(self) -> StubResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def stub_http(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubHTTP]:
    """`--url` 경로의 가져오기를 대역으로 바꾼다. **어떤 테스트도 네트워크에 나가지 않는다.**"""
    stub = StubHTTP()
    monkeypatch.setattr(source_module.urllib.request, "build_opener", stub.build_opener)
    yield stub


needs_extractor = pytest.mark.skipif(
    importlib.util.find_spec("trafilatura") is None,
    reason='본문 추출기는 선택 의존성이다 (pip install "youtube-shorts-maker[source]")',
)
"""추출기가 없는 환경에서 skip한다.

**대역으로 바꾸지 않는 이유는 추출 결과가 검증 대상이기 때문이다** — 무엇이 본문으로
뽑히는지가 #95가 고른 것이므로(trafilatura, PRD 14.1) 흉내 내면 동어반복이 된다.
"""

ARTICLE_URL = "https://news.example.com/article/1"

ARTICLE_HEADLINE = "폭염 속 전력 수요 사상 최고"

ARTICLE_PARAGRAPHS = (
    "20일 전력거래소는 이달 들어 최대 전력 수요가 사상 최고치를 기록했다고 밝혔다. "
    "이날 오후 5시 기준 순간 최대 전력은 104.8기가와트로 지난해 같은 기간보다 6.2% 늘었다.",
    "공급 예비율은 8.4%까지 떨어졌다. 예비율이 10% 아래로 내려간 것은 올해 들어 세 번째다. "
    "전력거래소는 예비력이 충분해 수급 경보 발령 단계는 아니라고 설명했다.",
    "한국전력은 취약 계층을 위한 냉방비 지원을 이달 말까지 신청받는다고 안내했다. "
    "지원 대상은 기초생활수급 가구와 차상위 계층이며 신청은 온라인으로도 가능하다.",
    "기상청은 이번 주말까지 서울 낮 최고 기온이 36도 안팎을 오르내릴 것으로 내다봤다. "
    "산업통상자원부는 냉방 수요가 집중되는 오후 시간대에 절전에 동참해 달라고 당부했다.",
)

PAGE_FURNITURE = ("로그인", "회원가입", "많이 본 뉴스", "구독하기", "기사제보")
"""본문에 섞이면 안 되는 껍데기 문구. 아래 페이지가 전부 들고 있다."""

_ARTICLE_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="{charset}">
<title>{title} | 예시신문</title>
</head>
<body>
<nav><a href="/">홈</a> <a href="/login">로그인</a> <a href="/join">회원가입</a></nav>
<article>
<h1>{title}</h1>
<p class="byline">홍길동 기자</p>
{body}
</article>
<aside><h2>많이 본 뉴스</h2><ul><li>구독하기</li><li>기사제보</li></ul></aside>
</body>
</html>
"""


def article_page(
    *,
    charset: str = "utf-8",
    encoding: str = "utf-8",
    paragraphs: tuple[str, ...] = ARTICLE_PARAGRAPHS,
) -> bytes:
    """기사 페이지 하나를 바이트로 만든다.

    **문자열이 아니다** — 이 경로의 요점이 "디코드하지 않고 추출기에 넘긴다"이므로
    (`source.py`) 바이트가 아니면 그 계약을 검증할 수 없다. `charset`과 `encoding`을 따로
    받는 것은 meta 선언과 실제 인코딩을 어긋나게 두는 경우도 만들 수 있어야 하기 때문이다.
    """
    markup = _ARTICLE_PAGE.format(
        charset=charset,
        title=ARTICLE_HEADLINE,
        body="\n".join(f"<p>{paragraph}</p>" for paragraph in paragraphs),
    )
    return markup.encode(encoding)


@pytest.fixture
def stub_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> Iterator[StubFFmpeg]:
    """`ffprobe`·`ffmpeg` 호출을 가짜로 바꾼다.

    **`run`과 `Popen` 둘을 바꾼다** (#30). 최종 렌더만 `Popen`을 쓰는데(진행률을 읽어야 한다)
    하나만 바꿔 두면 **가짜 오디오를 진짜 FFmpeg가 받아** 그 경로의 테스트가 전부 실패한다.
    """
    stub = StubFFmpeg()
    # `speech_module.subprocess`와 `timeline_module.subprocess`,
    # `video_renderer.subprocess`는 같은 모듈 객체다.
    monkeypatch.setattr(speech_module.subprocess, "run", stub)
    monkeypatch.setattr(speech_module.subprocess, "Popen", stub.popen)
    yield stub
