"""입력 수집 — 주제 한 줄과 원문 파일을 `source.json` 기록으로 옮긴다 (PRD 7.1, 이슈 #94).

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

`--url`은 이 모듈에 자기 입구를 하나 더 만든다 (#95). 가져오기와 추출이 그쪽에 붙어도
`source.json`을 만드는 자리는 `_record` 하나다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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


def title_from(text: str, path: Path, *, max_len: int) -> str:
    """원문에서 제목을 고른다 — 첫 비어 있지 않은 줄, 상한까지 자른다.

    **없으면 파일 이름이다.** 빈 파일은 `_read_text`가 이미 거절하므로 일반 실행에서는 첫
    갈래로 끝나지만, 제목이 비어 있는 결과를 낼 수 없다는 것이 이 함수의 계약이다 — 이 값이
    생성기의 `topic` 자리로 간다.

    상한을 넘긴 줄은 **잘라서 쓴다.** 파일 이름으로 물러나면 주제가 통째로 사라지는데,
    잘린 첫 문장은 무엇에 대한 원문인지를 여전히 담고 있다. 원문 전체는 `source.json`에
    남으므로 여기서 잃는 것이 없다.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:max_len]
    return path.stem or path.name


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
    text = _decode(raw, path).replace("\r\n", "\n").replace("\r", "\n")

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
