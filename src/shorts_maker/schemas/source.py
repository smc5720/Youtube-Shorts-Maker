"""`source.json` 스키마 — 원문·링크 입력이 남기는 입력 기록 (PRD 7.1, 이슈 #94).

**타입이 아니라 입력 경로가 결정하는 산출물이다** (PRD 6.2 표). `--text-file`과 `--url`이
같은 파일을 쓰고 `--topic`은 쓰지 않는다 — 그래서 `shorts_types.py`의 선택적 산출물 목록에
`source.json`이 없다.

**두 입력 경로가 하나의 스키마를 공유한다.** 경로별로 파일을 따로 두면 원문을 소비하는
쪽(요약·대본, #32)이 입력 경로마다 다른 모양을 열게 된다. 대신 경로가 채우지 않는 칸은
`null`이고, 무엇이 필수인지는 `kind`가 정한다 (`_check_kind_fields`).

세 가지가 이 파일의 형태를 정한다.

- **`text`에 본문 전문이 들어간다.** PRD 7.1이 원문 직접 입력에 "파일 경로와 텍스트 길이"만
  요구하지만, 그것만 남기면 run 디렉터리가 자기 입력을 잃는다 — 가리키는 파일은 run 밖에 있고
  바뀌거나 사라진다. 링크 경로는 애초에 남길 파일이 없어 본문 전문이 여기여야 하고(PRD 14.1),
  요약·재실행이 읽는 것도 이 값이다.
- **`char_count`는 `text`에서 파생되지만 필드로 남긴다.** 사람이 "얼마나 들어왔나"를 보는
  값이고 콘솔·로그에 함께 나가는 수다. 파생인 만큼 둘이 갈리지 않게 `checks`가 잰다.
- **`title`은 비어 있을 수 없다.** 이 값이 콘텐츠 생성기의 `topic` 자리로 가므로(#94), null을
  허용하면 "제목이 없는 원문"이 프롬프트에서 빈 주제가 된다. 제목을 만드는 규칙은
  `source.py`가 소유한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import Object, Schema, integer, text

SCHEMA_VERSION = 1
KNOWN_VERSIONS = (1,)

SOURCE_KINDS = ("text_file", "url")
"""입력 경로. CLI 플래그 이름과 대응한다 (`--text-file` / `--url`).

`topic`은 여기 없다 — 주제 한 줄 입력은 이 파일을 만들지 않는다 (PRD 6.2 표).
"""

_REQUIRED_BY_KIND = {"text_file": "path", "url": "url"}
"""`kind`가 반드시 채우는 위치 필드. 나머지 하나는 `null`이어야 한다."""

_ROOT = Object(
    {
        "schema_version": integer(minimum=1),
        "kind": text(choices=SOURCE_KINDS),
        # 위치 필드 둘. **둘 다 필수 키이고 값이 nullable이다** — 키를 빼면 "그 경로가
        # 아니었다"와 "기록하다 빠뜨렸다"가 구분되지 않는다 (`metadata.json`의 `source`와
        # 같은 이유). 어느 쪽이 값을 가져야 하는지는 아래 `checks`가 본다.
        "path": text(nullable=True),
        "url": text(nullable=True),
        "title": text(),
        "text": text(),
        "char_count": integer(minimum=1),
        # 수집 시각 (PRD 7.1의 "접근 시각"). run 시작 시각과 다른 값이다 — 입력 검증은
        # run 디렉터리보다 앞에 있다.
        "collected_at": text(),
    }
)


def _check_kind_fields(data: Any, errors: list[str]) -> None:
    """`kind`가 요구하는 위치 필드만 값을 가진다.

    `--text-file` 기록에 `url`이 함께 있으면 그 값이 어디서 왔는지 말할 수 없고, 반대로
    링크 기록의 `path`는 run 디렉터리 밖의 임시 파일을 가리키는 것처럼 읽힌다.
    """
    kind = data["kind"]
    required = _REQUIRED_BY_KIND[kind]
    if data[required] is None:
        errors.append(f"{required}: {kind} 입력에는 값이 필요하다. 받은 값: null")

    for name in _REQUIRED_BY_KIND.values():
        if name != required and data[name] is not None:
            errors.append(f"{name}: {kind} 입력에는 값이 없어야 한다. 받은 값: {data[name]!r}")


def _check_char_count(data: Any, errors: list[str]) -> None:
    """`char_count`가 `text`의 실제 길이와 같다.

    파생 값이므로 갈릴 여지를 남기지 않는다 — 사람이 콘솔에서 본 글자 수와 요약이 받는
    본문의 길이가 다르면, 어느 쪽이 잘렸는지 파일만 보고는 알 수 없다.
    """
    actual = len(data["text"])
    if data["char_count"] != actual:
        errors.append(
            f"char_count: text의 길이와 같아야 한다. 받은 값: {data['char_count']}, "
            f"실제: {actual}"
        )


SOURCE_SCHEMA = Schema(
    name="source.json",
    versions=KNOWN_VERSIONS,
    root=_ROOT,
    checks=(_check_kind_fields, _check_char_count),
)


def validate_source(data: Any, *, source: Path | None = None) -> None:
    """`source.json` 내용을 검증한다. 위반이 있으면 `SchemaError`."""
    SOURCE_SCHEMA.validate(data, source=source)


def load_source(path: Path) -> dict[str, Any]:
    """`source.json`을 읽고 검증해서 돌려준다."""
    return SOURCE_SCHEMA.load(path)
