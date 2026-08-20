"""입력 수집 — 주제 한 줄·원문 파일·링크를 `source.json` 기록으로 옮긴다 (PRD 7.1, #94·#95).

세 갈래(`--topic` / `--url` / `--text-file`) 중 어느 것으로 들어왔는지가 여기서 하나의 값으로
합쳐진다. 파이프라인이 받는 것은 `SourceInput` 하나이고, 그 안에는 **콘텐츠 생성기에 넘길
문자열**과 **run 디렉터리에 남길 기록** 둘만 있다.

- **검증은 run 디렉터리를 만들기 전에 끝낸다.** 없는 파일·빈 파일·상한 초과는 산출물을 하나도
  만들 수 없으므로, 설정·타입·provider 검증과 같은 자리에서 멈춘다 (`main.main`). 빈 run
  디렉터리가 쌓이면 검수할 산출물과 구분되지 않는다.
- **`ContentGenerator` 계약을 바꾸지 않는다.** 생성기가 받는 것은 여전히 `topic` 하나이고,
  원문 입력에서는 그 자리에 **제목**이 간다. 본문을 소비하는 것은 요약·대본(#32)이며 그
  경로를 쓰는 타입이 아직 없다 — 그래서 본문은 `source.json`에만 남는다 (PRD 14.1).
- **`--topic` 경로는 기록을 만들지 않는다.** 주제 한 줄은 CLI 인자 자체가 기록이고
  `config.used.yaml` 옆에 `source.json`을 하나 더 두면 "출처가 있는 입력"과 구분되지 않는다
  (PRD 6.2 표).

`--url`은 이 모듈에 자기 입구를 하나 더 만든다 (`from_url`, #95). 가져오기와 추출이 붙어도
`source.json`을 만드는 자리는 `_record` 하나다. 그 경로가 지키는 것 셋.

- **거부는 관측 가능한 신호에만 건다** — HTTP 상태·`Content-Type`·본문 길이다. "유료인가
  로그인 벽인가 JS 렌더링인가"를 판별하지 않는다: 판별할 수 없다는 것이 실측이고(스파이크
  #31 3장), 판별하려는 구현은 정상 기사를 함께 버린다. 통과한 것은 사람이 본다 (PRD 14.1).
- **본문 추출기는 선택 의존성이다.** `import trafilatura`가 810ms이고 딸려 오는 패키지가
  12개라 `--topic` 실행이 낼 비용이 아니다 — 이 모듈은 `--url`을 실제로 받았을 때만
  import하고, 없으면 **가져오기 전에** 설치 방법을 말하며 멈춘다.
- **HTML 바이트를 그대로 추출기에 넘긴다.** 응답 인코딩이 utf-8이 아닌 한국어 사이트가 아직
  있어(네이버 카페 `MS949`) 우리 코드가 먼저 `.decode()`하면 글자가 깨진 채로 추출된다 —
  원문 파일 경로의 `_decode`가 여기 오면 안 되는 이유다.
"""

from __future__ import annotations

import gzip
import io
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from . import __version__
from .config import Config
from .schemas.source import SCHEMA_VERSION, validate_source

ENCODINGS = ("utf-8-sig", "cp949")
"""원문 파일을 디코드할 순서.

**UTF-8이 먼저다.** UTF-8은 자기 검증형이라 성공하면 그 해석이 맞고, 실패한 뒤의 cp949는
거의 모든 바이트열을 받아 주므로 순서를 뒤집으면 UTF-8 한국어 파일이 조용히 깨진 글자로
읽힌다. `utf-8`이 아니라 `utf-8-sig`인 이유는 BOM이다 — BOM을 남기는 편집기가 Windows에
흔하고, 그것을 벗기지 않으면 **제목 첫 글자에 보이지 않는 문자가 붙는다**(제목은 프롬프트로
간다). BOM이 없는 파일에도 `utf-8-sig`는 안전하다.

cp949는 Windows 한국어 기본 인코딩이다. 사람이 메모장으로 저장한 기사 원문이 그것이다.
"""

MAX_BYTES_PER_CHAR = 4
"""UTF-8 한 글자의 최대 바이트 수. 읽기 전 크기 검사의 계수다.

바이트 수가 `max_chars * 4`를 넘으면 글자 수도 반드시 `max_chars`를 넘으므로, **그 파일을
메모리에 올리지 않고 거절할 수 있다.** 반대 방향은 성립하지 않아 디코드 뒤에 글자 수를 다시
잰다 — 이 검사는 상한 판정이 아니라 거대한 파일을 읽지 않기 위한 방어선이다.
"""

REPO_URL = "https://github.com/smc5720/Youtube-Shorts-Maker"

USER_AGENT = f"shorts-maker/{__version__} (+{REPO_URL})"
"""가져오기가 밝히는 이름. **브라우저로 위장하지 않는다.**

위장이 필요하지 않다는 것이 실측이다 — 도구 이름으로 기사 네 페이지 전부 200이었고, 반대로
헤더를 **아예 빼면** 매일경제와 위키백과가 403을 준다(위키백과는 UA 정책을 명시한다).
위장 UA는 얻는 것이 없고 차단 정책과 싸우는 코드를 부른다 (스파이크 #31 4장).
"""

ALLOWED_SCHEMES = ("http", "https")
"""받는 링크의 스킴. **화이트리스트여야 한다** — `urllib`은 `file:`·`ftp:`도 열 수 있어서,
막지 않으면 `--url file:///...`이 로컬 파일을 읽는 경로가 된다. 리다이렉트가 도착한 곳도
같은 목록으로 다시 본다."""

HTML_TYPES = ("text/html", "application/xhtml+xml")
"""본문 추출을 시도할 `Content-Type`. PDF·JSON·이미지를 추출기에 넘기면 빈 결과가 나오고,
그때의 사유는 "본문이 없다"가 아니라 "HTML이 아니다"여야 한다."""

FALLBACK_HINT = "페이지 본문을 파일로 저장해 --text-file로 넣는다."
"""거부할 때 함께 내는 안내. **모든 거부에 붙는다** — 이 경로의 실패는 사용자가 고칠 수 없는
것(유료·로그인·JS 렌더링)이 대부분이고, 그때 남는 길이 하나뿐이다 (PRD 14.1)."""


class SourceError(Exception):
    """입력을 읽거나 받아들이는 데 실패했다.

    설정 오류와 같은 성격이다 — 사용자가 준 값이 잘못됐고 산출물을 하나도 만들 수 없다.
    그래서 `main`이 같은 종료 코드로 멈춘다.
    """


@dataclass(frozen=True)
class SourceInput:
    """파이프라인이 받는 입력 하나."""

    topic: str
    """콘텐츠 생성기에 넘어가는 문자열. `--topic`은 인자 그대로, 원문 입력은 제목이다."""

    record: dict[str, Any] | None = None
    """`source.json`에 쓸 내용. `--topic` 경로는 `None`이고 파일을 만들지 않는다.

    **본문을 따로 들지 않는다.** 요약·대본(#32)이 소비할 값은 이 기록의 `text`이고, 같은
    문자열을 필드 둘에 두면 어느 쪽이 원본인지 모호해진다 — 지금 본문을 읽는 코드는 없다.
    """


def from_topic(topic: str) -> SourceInput:
    """`--topic` 입력. 기록도 본문도 없다."""
    return SourceInput(topic=topic)


def from_text_file(path: Path, *, config: Config, now: datetime) -> SourceInput:
    """`--text-file` 입력. 파일을 읽고 `source.json` 내용을 만든다.

    Args:
        path: `--text-file`로 받은 경로.
        config: 상한 두 개를 읽는다 (`source.max_chars`, `source.title_max_len`).
        now: 수집 시각. 호출자가 준다 — run 시작 시각과 같은 시계를 쓰기 위함이고,
            테스트가 시각을 고정할 수 있다.

    Raises:
        SourceError: 파일이 없거나, 읽을 수 없거나, 비어 있거나, 상한을 넘었을 때.
        SchemaError: 만든 기록이 계약을 어겼을 때 — 이 모듈의 결함이다.
    """
    text = _read_text(path, max_chars=config.get("source.max_chars"))
    title = title_from(text, path, max_len=config.get("source.title_max_len"))
    return SourceInput(
        topic=title,
        record=_record(kind="text_file", path=path, title=title, text=text, now=now),
    )


def from_url(url: str, *, config: Config, now: datetime) -> SourceInput:
    """`--url` 입력. 페이지를 가져와 본문을 추출하고 `source.json` 내용을 만든다 (#95).

    **순서가 계약이다.** 추출기 확인 → 가져오기 → 거부 판정 → 기록이고, 첫 단계가 먼저인
    이유는 선택 의존성이 없는 환경에서 네트워크에 나갔다가 실패하지 않기 위해서다.

    Args:
        url: `--url`로 받은 링크.
        config: `source.max_chars` · `source.title_max_len`과 `source.url.*` 넷을 읽는다.
        now: 접근 시각 (PRD 7.1). `from_text_file`과 같은 이유로 호출자가 준다.

    Raises:
        SourceError: 추출기가 없거나, 가져오지 못했거나, 세 거부 신호 중 하나에 걸렸을 때.
        SchemaError: 만든 기록이 계약을 어겼을 때 — 이 모듈의 결함이다.
    """
    extractor = load_extractor()
    _check_scheme(url)

    final_url, raw = _fetch(
        url,
        timeout_sec=config.get("source.url.timeout_sec"),
        max_bytes=config.get("source.url.max_bytes"),
        max_redirects=config.get("source.url.max_redirects"),
    )
    extracted_title, text = _extract(extractor, raw, url=final_url)
    text = _normalize(text)

    min_chars = config.get("source.url.min_chars")
    max_chars = config.get("source.max_chars")
    if not text.strip():
        _reject(f"본문을 추출하지 못했다: {final_url}")
    if len(text) < min_chars:
        _reject(
            f"추출한 본문이 너무 짧다: {final_url} — {len(text):,}자. "
            f"하한은 {min_chars:,}자다 (source.url.min_chars)."
        )
    if len(text) > max_chars:
        _reject(
            f"추출한 본문이 상한을 넘었다: {final_url} — {len(text):,}자. "
            f"상한은 {max_chars:,}자다 (source.max_chars)."
        )

    max_len = config.get("source.title_max_len")
    # 제목 후보 셋. 추출기의 메타데이터가 첫 번째인 이유는 실측에서 세 기사 모두
    # `title`이 정확했기 때문이고(스파이크 #31 2장), 마지막이 URL인 것은 제목이 비어 있는
    # 결과를 낼 수 없다는 계약 때문이다 — 이 값이 생성기의 `topic` 자리로 간다.
    title = (
        _clip(extracted_title, max_len)
        or _first_line(text, max_len=max_len)
        or final_url[:max_len]
    )
    return SourceInput(
        topic=title,
        # **기록에 남는 것은 리다이렉트가 도착한 URL이다.** 단축 링크·AMP 전환을 지나면
        # 사용자가 친 주소는 본문이 있던 곳이 아니고, `source.json`의 `url`은 출처다.
        record=_record(kind="url", url=final_url, title=title, text=text, now=now),
    )


def load_extractor() -> Any:
    """본문 추출기(trafilatura) 모듈을 가져온다. **함수 안에서 import하는 것이 요점이다.**

    선택 의존성이라 없을 수 있고, 있어도 `--topic` 실행이 810ms를 낼 이유가 없다
    (PRD 14.1). 모듈 최상단으로 옮기면 둘 다 깨진다.

    Raises:
        SourceError: `source` extra가 설치되지 않았을 때. 설정 오류와 같은 성격이라
            run 디렉터리를 만들기 전에 멈춘다.
    """
    try:
        import trafilatura
    except ImportError as error:
        raise SourceError(
            "--url에 필요한 본문 추출기가 없다 — trafilatura가 설치돼 있지 않다.\n"
            '설치: pip install "youtube-shorts-maker[source]"\n'
            f"설치하지 않고 진행하려면 {FALLBACK_HINT}"
        ) from error
    return trafilatura


def title_from(text: str, path: Path, *, max_len: int) -> str:
    """원문에서 제목을 고른다 — 첫 비어 있지 않은 줄, 상한까지 자른다.

    **없으면 파일 이름이다.** 빈 파일은 `_read_text`가 이미 거절하므로 일반 실행에서는 첫
    갈래로 끝나지만, 제목이 비어 있는 결과를 낼 수 없다는 것이 이 함수의 계약이다 — 이 값이
    생성기의 `topic` 자리로 간다.

    상한을 넘긴 줄은 **잘라서 쓴다.** 파일 이름으로 물러나면 주제가 통째로 사라지는데,
    잘린 첫 문장은 무엇에 대한 원문인지를 여전히 담고 있다. 원문 전체는 `source.json`에
    남으므로 여기서 잃는 것이 없다.
    """
    return _first_line(text, max_len=max_len) or path.stem or path.name


def _first_line(text: str, *, max_len: int) -> str:
    """첫 비어 있지 않은 줄을 상한까지 자른다. 없으면 빈 문자열.

    두 입력 경로가 함께 쓴다 — 원문 파일은 이것이 제목 규칙 자체이고, 링크는 추출기가
    제목을 내지 못했을 때의 두 번째 후보다.
    """
    for line in text.splitlines():
        if stripped := line.strip():
            return stripped[:max_len]
    return ""


def _clip(value: str | None, max_len: int) -> str:
    """추출기가 낸 제목을 상한까지 자른다. 공백뿐이면 빈 문자열 — 다음 후보로 넘어간다."""
    return value.strip()[:max_len] if value else ""


def _normalize(text: str) -> str:
    """줄 끝을 LF로 맞춘다. `char_count`가 어디서 왔는지에 좌우되면 안 된다."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _reject(reason: str) -> NoReturn:
    """거부 사유에 `--text-file` 안내를 붙여 던진다. **링크 경로의 유일한 거부 자리다.**"""
    raise SourceError(f"{reason}\n{FALLBACK_HINT}")


def _check_scheme(url: str, *, redirected: bool = False) -> None:
    """http(s)만 받는다. 리다이렉트가 도착한 곳도 같은 검사를 지난다."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme in ALLOWED_SCHEMES and parts.netloc:
        return
    where = "리다이렉트가 도착한 곳" if redirected else "링크"
    _reject(
        f"{where}이 http(s)가 아니다: {url} — "
        f"{' 또는 '.join(ALLOWED_SCHEMES)} 링크만 받는다."
    )


def _to_uri(url: str) -> str:
    """주소에 든 한글을 퍼센트 인코딩한다. **브라우저에서 복사한 링크가 그것이다.**

    요청 줄은 ASCII여야 해서 `http.client`가 한글 경로에서 `UnicodeEncodeError`로 죽는다 —
    `OSError`가 아니라 아래 `_fetch`의 어느 갈래에도 걸리지 않고 스택트레이스가 그대로
    나간다. 브라우저 주소창은 한글을 보여 주고 위키백과·네이버 링크가 실제로 그 모양이므로,
    거부가 아니라 **바꿔서 보낸다.**

    `safe`에 `%`가 있어야 이미 인코딩된 주소를 두 번 인코딩하지 않는다. 호스트(`netloc`)는
    건드리지 않는다 — 국제화 도메인은 퍼센트가 아니라 IDNA이고 그쪽은 `http.client`가 한다.
    """
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        parts._replace(
            path=urllib.parse.quote(parts.path, safe="/%"),
            query=urllib.parse.quote(parts.query, safe="%=&+,;:@/?$!*'()~-._"),
            fragment=urllib.parse.quote(parts.fragment, safe="%"),
        )
    )


class _RedirectLimit(urllib.request.HTTPRedirectHandler):
    """따라갈 리다이렉트 수를 설정값으로 바꾼다 (기본 핸들러는 10회 고정이다)."""

    def __init__(self, limit: int) -> None:
        self.max_redirections = limit


def _fetch(
    url: str, *, timeout_sec: int, max_bytes: int, max_redirects: int
) -> tuple[str, bytes]:
    """페이지를 가져와 (도착한 URL, 응답 바이트)를 돌려준다.

    **디코드하지 않는다.** 인코딩 협상은 추출기의 몫이다 (모듈 docstring).

    Raises:
        SourceError: 가져오지 못했거나, 200이 아니거나, HTML이 아니거나, 상한을 넘었을 때.
    """
    request = urllib.request.Request(
        _to_uri(url),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            # **압축을 요청하지 않는다.** 받을 이유가 없는 데다, 압축된 응답에 크기 상한을
            # 걸려면 푼 뒤에 한 번 더 재야 한다 (아래 `_gunzip`은 규약을 어기고 압축해
            # 보내는 서버를 위한 것이다).
            "Accept-Encoding": "identity",
        },
    )
    opener = urllib.request.build_opener(_RedirectLimit(max_redirects))

    try:
        with opener.open(request, timeout=timeout_sec) as response:
            final_url = response.url
            status = response.status
            content_type = response.headers.get_content_type()
            content_encoding = (response.headers.get("Content-Encoding") or "").lower()
            # 상한 + 1바이트를 읽는다. 넘겼는지는 그 한 바이트로 갈리고, 상한을 넘는 응답을
            # 끝까지 받아 두지 않는다.
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        # 유료 매체는 대체로 여기서 401/403으로 드러난다 (스파이크 #31 3.1).
        detail = f"HTTP {error.code} {error.reason}"
        if 300 <= error.code < 400:
            detail += f" — 리다이렉트를 따라가지 못했다 (상한 {max_redirects}회)"
        _reject(f"링크를 가져오지 못했다: {url} — {detail}")
    except urllib.error.URLError as error:
        _reject(_connection_reason(url, error.reason))
    except TimeoutError:
        _reject(f"링크를 가져오지 못했다: {url} — {timeout_sec}초 안에 응답이 없다.")
    except OSError as error:
        _reject(f"링크를 가져오지 못했다: {url} — {error}")
    except UnicodeError as error:
        # 국제화 도메인의 IDNA 변환이 실패하는 자리다 (`OSError`가 아니라 여기 있다).
        # 경로의 한글은 `_to_uri`가 이미 넘겼다.
        _reject(f"링크의 주소를 해석할 수 없다: {url} — {error}")

    _check_scheme(final_url, redirected=True)

    # 200이 아닌 성공 응답(204·206 …)에는 볼 본문이 없거나 잘려 있다. `urlopen`이 오류로
    # 던지지 않으므로 여기서 본다.
    if status != 200:
        _reject(f"링크를 가져오지 못했다: {final_url} — HTTP {status}")
    if content_type not in HTML_TYPES:
        _reject(
            f"HTML 페이지가 아니다: {final_url} — Content-Type이 {content_type or '없음'}이다."
        )
    if content_encoding in ("gzip", "x-gzip"):
        raw = _gunzip(raw, max_bytes, url=final_url)
    elif content_encoding not in ("", "identity"):
        _reject(f"응답을 읽을 수 없다: {final_url} — Content-Encoding이 {content_encoding}이다.")
    if len(raw) > max_bytes:
        _reject(
            f"응답이 상한을 넘었다: {final_url} — {max_bytes:,}바이트를 넘겼다 "
            f"(source.url.max_bytes)."
        )
    return final_url, raw


def _connection_reason(url: str, reason: object) -> str:
    """`URLError`의 속을 사람이 고칠 수 있는 문구로 옮긴다.

    **TLS 실패는 원문을 그대로 싣는다.** Python 3.13의 `ssl`은 `VERIFY_X509_STRICT`를
    기본으로 켜서, TLS를 가로채는 사내 프록시의 CA가 basicConstraints를 critical로 표시하지
    않으면 그 환경의 모든 HTTPS 요청이 실패한다 (스파이크 #31 5장). 원문 없이 "가져오지
    못했다"만 남으면 사용자는 링크를 의심하지 프록시를 의심하지 않는다.
    """
    if isinstance(reason, ssl.SSLError):
        return (
            f"TLS 검증에 실패했다: {url} — {reason}\n"
            "TLS를 가로채는 사내 프록시 환경이라면 그 CA 인증서를 SSL_CERT_FILE 환경 변수로 "
            "지정한다."
        )
    if isinstance(reason, TimeoutError):
        return f"링크를 가져오지 못했다: {url} — 응답 시간이 초과됐다."
    return f"링크를 가져오지 못했다: {url} — {reason}"


def _gunzip(raw: bytes, max_bytes: int, *, url: str) -> bytes:
    """`Accept-Encoding: identity`를 무시하고 압축해 보낸 서버를 위한 자리.

    푼 뒤의 크기에도 상한을 건다 — 압축된 5MB가 풀리면서 몇 백 MB가 되는 응답을 그대로
    메모리에 올리지 않는다.
    """
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            return stream.read(max_bytes + 1)
    except (OSError, EOFError) as error:
        _reject(f"응답의 압축을 풀 수 없다: {url} — {error}")


def _extract(extractor: Any, raw: bytes, *, url: str) -> tuple[str | None, str]:
    """HTML 바이트에서 (제목, 본문)을 뽑는다. 못 뽑으면 `(None, "")`.

    `url`을 함께 넘기는 것은 추출기가 상대 경로와 사이트 규칙을 해석하는 데 쓰기 때문이다.
    """
    try:
        document = extractor.extract(
            raw, url=url, output_format="json", with_metadata=True
        )
    except Exception as error:  # noqa: BLE001 - 입력이 네트워크에서 온 임의의 바이트다
        # 추출기가 깨진 마크업에서 무엇을 던지는지는 계약이 아니다. 스택트레이스를 그대로
        # 내보내면 사용자가 할 수 있는 일(--text-file)이 화면에서 사라진다.
        _reject(f"본문을 추출하지 못했다: {url} — {type(error).__name__}: {error}")
    if not document:
        return None, ""
    data = json.loads(document)
    return data.get("title"), data.get("text") or ""


def _read_text(path: Path, *, max_chars: int) -> str:
    """원문 파일을 읽어 문자열로 돌려준다. 상한·빈 파일·인코딩 실패를 여기서 거른다."""
    # 부재와 "파일이 아니다"를 갈라 말한다. 아래 읽기도 둘 다 실패하지만 그때의 메시지는
    # 원인이 인코딩이나 권한인 것처럼 읽힌다.
    if not path.exists():
        raise SourceError(f"원문 파일을 찾을 수 없다: {path}")
    if not path.is_file():
        raise SourceError(f"원문 파일이 아니다: {path}")

    try:
        size = path.stat().st_size
    except OSError as error:
        raise SourceError(f"원문 파일을 읽을 수 없다: {path} — {error}") from error

    if size > max_chars * MAX_BYTES_PER_CHAR:
        raise SourceError(
            f"원문 파일이 상한을 넘었다: {path} — {size:,}바이트. "
            f"상한은 {max_chars:,}자다 (source.max_chars)."
        )

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SourceError(f"원문 파일을 읽을 수 없다: {path} — {error}") from error

    # 줄 끝을 LF로 맞춘다. **글자 수가 저장한 운영체제에 좌우되면 안 된다** — 같은 기사를
    # Windows에서 저장하면 CRLF가 되어 줄 수만큼 `char_count`가 늘고, 그 수는 콘솔에도 나가고
    # 상한 판정에도 쓰인다. 본문을 소비하는 쪽(#32)에도 `\r`은 의미가 없다.
    text = _normalize(_decode(raw, path))

    if not text.strip():
        raise SourceError(f"원문 파일이 비어 있다: {path}")
    if len(text) > max_chars:
        raise SourceError(
            f"원문 파일이 상한을 넘었다: {path} — {len(text):,}자. "
            f"상한은 {max_chars:,}자다 (source.max_chars)."
        )
    return text


def _decode(raw: bytes, path: Path) -> str:
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SourceError(
        f"원문 파일의 인코딩을 알 수 없다: {path} — "
        f"{' 또는 '.join(ENCODINGS)}로 읽을 수 없다. UTF-8로 다시 저장한다."
    )


def _record(
    *,
    kind: str,
    title: str,
    text: str,
    now: datetime,
    path: Path | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """`source.json` 내용을 만든다. **두 입력 경로가 공유하는 유일한 자리다** (#95).

    위치 필드 둘 중 하나만 값을 가진다는 규칙은 스키마가 확인한다.
    """
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        # **사람이 준 경로를 그대로 남긴다.** 절대 경로로 바꾸면 같은 원문으로 돌린 두 run의
        # 기록이 실행 위치에 따라 달라진다. 구분자만 `/`로 맞춘다 — Windows 경로를 JSON에
        # 그대로 쓰면 역슬래시가 두 겹으로 이스케이프되어 사람이 읽는 파일에 노이즈가 되고,
        # `Path`로 다시 여는 데는 어느 쪽이든 차이가 없다.
        "path": path.as_posix() if path is not None else None,
        "url": url,
        "title": title,
        "text": text,
        "char_count": len(text),
        "collected_at": now.isoformat(timespec="seconds"),
    }
    validate_source(record)
    return record
