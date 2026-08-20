"""산출물 JSON 스키마 — `quiz.json` / `scenes.json` / `project.json` / `metadata.json` /
`source.json`의 단일 진실 공급원.

다섯 파일은 생성 모듈과 렌더러, 편집 앱이 공유하는 계약이다. 필드명과 열거값은 문서가 아니라
**이 패키지가 확정한다** — 문서(퀴즈 스펙 3장, PRD 7.1, 7.5.2, 7.8, 7.10)는 여기 정의된
이름을 설명한다.

각 파일의 스키마는 `schema_version`을 가지며, 이 코드가 모르는 버전은 오류다. 버전 간 변환은
필요해질 때 붙인다.

```python
from shorts_maker.schemas import SchemaError, load_scenes, validate_quiz

quiz = load_quiz(run_dir / "quiz.json")                  # 검증하며 읽는다
scenes = load_scenes(run_dir / "scenes.json", finalized=True)  # TTS 이후 상태를 요구
```
"""

from __future__ import annotations

from .core import Schema, SchemaError
from .metadata import (
    METADATA_SCHEMA,
    TITLE_COUNT,
    load_metadata,
    validate_metadata,
)
from .project import (
    BACKGROUND_KINDS,
    PROJECT_SCHEMA,
    load_project,
    validate_project,
)
from .quiz import (
    DIFFICULTIES,
    QUIZ_SCHEMA,
    VERIFY_STATUSES,
    load_quiz,
    validate_quiz,
)
from .scenes import (
    AUDIO_FIELDS,
    ROLES,
    SCENES_FINAL_SCHEMA,
    SCENES_SCHEMA,
    load_scenes,
    segment_path,
    validate_scenes,
    validate_scenes_final,
)
from .source import (
    SOURCE_KINDS,
    SOURCE_SCHEMA,
    load_source,
    validate_source,
)

__all__ = [
    "AUDIO_FIELDS",
    "BACKGROUND_KINDS",
    "DIFFICULTIES",
    "METADATA_SCHEMA",
    "PROJECT_SCHEMA",
    "QUIZ_SCHEMA",
    "ROLES",
    "SCENES_FINAL_SCHEMA",
    "SCENES_SCHEMA",
    "SOURCE_KINDS",
    "SOURCE_SCHEMA",
    "TITLE_COUNT",
    "VERIFY_STATUSES",
    "Schema",
    "SchemaError",
    "load_metadata",
    "load_project",
    "load_quiz",
    "load_scenes",
    "load_source",
    "segment_path",
    "validate_metadata",
    "validate_project",
    "validate_quiz",
    "validate_scenes",
    "validate_scenes_final",
    "validate_source",
]
