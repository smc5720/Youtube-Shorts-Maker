"""`metadata.json` 스키마 — 업로드 시 붙여넣을 제목·설명·태그 (PRD 7.8, 이슈 #13).

**타입과 무관한 공통 산출물이다** (PRD 6.2 표). 만드는 쪽(`metadata_generator`)이
`scenes.json`만 읽으므로, 이 스키마에도 타입 전용 어휘가 들어오지 않는다 — `type`은
레지스트리가 아는 이름 중 하나일 뿐이고 그 값으로 분기하는 코드는 없다 (퀴즈 스펙 1.1).

세 가지가 이 파일의 형태를 정한다.

- **`titles`는 정확히 3개다.** 사람이 셋 중 하나를 골라 업로드 폼에 붙여넣는 것이 이
  필드의 용도이므로(PRD 7.8), 개수가 흔들리면 쓰는 쪽이 깨진다. 하한만 두면 1개짜리
  결과도 통과해 "후보"라는 말이 무의미해진다.
- **`source`는 nullable 필수 필드다.** 필드를 통째로 빼면 "출처가 없는 입력 경로였다"와
  "생성기가 빠뜨렸다"가 구분되지 않는다. 값이 있는 것은 `--url` 경로뿐이고(#100), `--topic`과
  `--text-file`은 `null`이다 — 로컬 파일 경로는 업로드 설명에 붙일 출처가 아니다 (PRD 14.1).
- **길이·개수 상한은 여기가 아니라 config가 정한다.** 제목 40자·태그 10개 같은 값은
  YouTube의 하드 리밋이 아니라 모바일 검색 결과에서 잘리지 않게 하려는 운영 기준이고,
  바꾸고 싶은 값이다 (`metadata.title_max_len`, `metadata.tag_max_count`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..shorts_types import available_types
from .core import Object, Scalar, Schema, choices_from, integer, items, text

SCHEMA_VERSION = 1
KNOWN_VERSIONS = (1,)

TITLE_COUNT = 3
"""제목 후보 개수 (PRD 7.8). 스키마가 정확히 이 개수를 강제한다."""

_ROOT = Object(
    {
        "schema_version": integer(minimum=1),
        # 후보는 레지스트리가 정한다. 값의 출처는 `scenes.json`의 같은 필드다.
        "type": choices_from(available_types, label="등록된 타입"),
        "language": text(),
        "titles": items(Scalar("str"), min_items=TITLE_COUNT, max_items=TITLE_COUNT),
        "description": text(),
        # 상한은 config가 정하므로 여기서는 "비어 있지 않다"만 요구한다. 태그가 하나도
        # 없는 메타데이터는 만들 이유가 없다.
        "tags": items(Scalar("str"), min_items=1),
        # 출처 URL. 구조화된 출처 정보(제목·기자·날짜)는 추출기가 함께 내지만, 그것을
        # **요구하는** 경로가 생길 때 필드를 늘린다 — 지금 모양을 정하면 쓰지 않는 계약이
        # 굳는다 (#100).
        "source": text(nullable=True),
    }
)

METADATA_SCHEMA = Schema(name="metadata.json", versions=KNOWN_VERSIONS, root=_ROOT)


# --- 생성기가 LLM에 넘기는 부분 스키마 --------------------------------------

_MODEL_OMITS = ("schema_version", "type", "language", "source")
"""모델에게 묻지 않는 필드 — 전부 코드가 정한다 (버전, 장면에서 읽은 타입, 언어, 출처)."""


def content_json_schema(*, title_max_len: int, tag_max_count: int) -> dict[str, Any]:
    """`metadata_generator`가 `--json-schema`로 넘길 JSON Schema.

    **`METADATA_SCHEMA`에서 파생한다.** 프롬프트 쪽에 필드 이름을 손으로 다시 적으면
    계약이 두 곳에 생기고, 한쪽만 고쳐졌을 때 모델이 낡은 모양을 만들어 낸다 (PRD 14.1).
    `quiz.json` 쪽 `content_json_schema`와 같은 구조다.
    """
    schema = _ROOT.without(*_MODEL_OMITS).to_json_schema()
    schema["properties"]["titles"]["items"]["maxLength"] = title_max_len
    schema["properties"]["tags"]["maxItems"] = tag_max_count
    return schema


def validate_metadata(data: Any, *, source: Path | None = None) -> None:
    """`metadata.json` 내용을 검증한다. 위반이 있으면 `SchemaError`."""
    METADATA_SCHEMA.validate(data, source=source)


def load_metadata(path: Path) -> dict[str, Any]:
    """`metadata.json`을 읽고 검증해서 돌려준다."""
    return METADATA_SCHEMA.load(path)
