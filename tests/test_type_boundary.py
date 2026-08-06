"""타입 경계 강제 — 퀴즈 스펙 1.1 / PRD 7.4.1, 이슈 #8의 완료 조건.

**공통 파이프라인은 `scenes.json`만 본다.** 규칙을 문서로만 두면 지켜졌는지 확인할 방법이
없으므로 소스를 직접 훑는다. `src/shorts_maker/types/` 밖의 모듈이

- 타입 패키지를 import하거나 (`shorts_maker.types...`),
- 타입 전용 스키마 이름을 import하거나 (`load_quiz` 등 — 이것이 `quiz.json`을 여는 길이다),
- 타입 전용 산출물 파일명을 문자열로 들고 있으면

여기서 실패한다. 경계가 깨진 채로 두 번째 타입을 추가하면 공통 파이프라인 전체를 고쳐야 한다.

검사 대상 이름과 파일명은 **레지스트리와 스키마 패키지에서 유도한다.** 목록을 여기 적어 두면
새 타입이 추가됐을 때 조용히 낡는다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import shorts_maker
from shorts_maker.schemas import core as schema_core
from shorts_maker.schemas import quiz as quiz_schema
from shorts_maker.shorts_types import available_types, get_type
from shorts_maker import schemas, shorts_types

PACKAGE_ROOT = Path(shorts_maker.__file__).resolve().parent
TYPES_PACKAGE = PACKAGE_ROOT / "types"
TYPES_MODULE = "shorts_maker.types"

REGISTRY_MODULE = PACKAGE_ROOT / "shorts_types.py"
"""타입 패키지 경로를 문자열로 아는 유일한 자리. 레지스트리가 조회 시점에 import한다."""

SCHEMA_DEFINITION_ALLOWLIST = frozenset(
    {PACKAGE_ROOT / "schemas" / "quiz.py", PACKAGE_ROOT / "schemas" / "__init__.py"}
)
"""타입 전용 스키마를 **정의하고 재수출하는** 자리. 파일을 여는 것은 types/ 안에서만 한다."""

TYPE_ONLY_NAMES = frozenset(
    name
    for name in schemas.__all__
    # `quiz.py`가 소유한 이름만 고른다. core에서 가져다 쓰는 이름(Schema 등)은 제외한다.
    if getattr(schemas, name) is getattr(quiz_schema, name, None)
    and getattr(schemas, name) is not getattr(schema_core, name, None)
)

TYPE_ONLY_ARTIFACTS = frozenset(
    get_type(name).content_artifact for name in available_types()
)


def python_files() -> list[Path]:
    return sorted(
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if TYPES_PACKAGE not in path.parents
    )


def module_name(path: Path) -> str:
    parts = path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_module(node: ast.ImportFrom, path: Path) -> str:
    """`from ..x import y`의 절대 모듈 이름."""
    if node.level == 0:
        return node.module or ""
    name = module_name(path)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    parts = package.split(".")
    if node.level > 1:
        parts = parts[: len(parts) - (node.level - 1)]
    return ".".join(filter(None, [".".join(parts), node.module or ""]))


def violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == TYPES_MODULE or alias.name.startswith(f"{TYPES_MODULE}."):
                    found.append(f"{path.name}:{node.lineno} 타입 패키지 import {alias.name}")

        elif isinstance(node, ast.ImportFrom):
            module = imported_module(node, path)
            if module == TYPES_MODULE or module.startswith(f"{TYPES_MODULE}."):
                found.append(f"{path.name}:{node.lineno} 타입 패키지 import {module}")
            if path not in SCHEMA_DEFINITION_ALLOWLIST:
                for alias in node.names:
                    if alias.name in TYPE_ONLY_NAMES:
                        found.append(
                            f"{path.name}:{node.lineno} 타입 전용 스키마 import {alias.name}"
                        )

        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in TYPE_ONLY_ARTIFACTS and path not in SCHEMA_DEFINITION_ALLOWLIST:
                found.append(f"{path.name}:{node.lineno} 타입 전용 산출물 이름 {node.value!r}")
            # importlib로 우회하는 길도 막는다. 레지스트리만 이 경로를 안다.
            if node.value.startswith(TYPES_MODULE) and path != REGISTRY_MODULE:
                found.append(f"{path.name}:{node.lineno} 타입 패키지 경로 문자열 {node.value!r}")

    return found


def test_guard_targets_are_not_empty() -> None:
    """유도한 검사 대상이 비면 아래 테스트가 아무것도 검사하지 않고 통과한다."""
    assert "load_quiz" in TYPE_ONLY_NAMES
    assert "quiz.json" in TYPE_ONLY_ARTIFACTS
    assert len(python_files()) > 5


def test_common_pipeline_does_not_reach_into_type_specific_code() -> None:
    found = [message for path in python_files() for message in violations(path)]

    assert found == [], (
        "types/ 밖의 모듈이 타입 전용 코드·산출물에 직접 닿는다. 필요한 정보는 장면 템플릿이 "
        "scenes.json 필드로 옮겨 담고, 타입 라우팅은 shorts_types 레지스트리를 거친다:\n"
        + "\n".join(found)
    )


def test_guard_would_catch_a_violation(tmp_path: Path) -> None:
    """가드가 실제로 잡는지 확인한다 — 통과가 검사 부재를 뜻하지 않아야 한다."""
    offender = tmp_path / "video_renderer.py"
    offender.write_text(
        "from shorts_maker.schemas import load_quiz\n"
        "from shorts_maker.types.quiz import SHORTS_TYPE\n"
        "QUIZ = 'quiz.json'\n",
        encoding="utf-8",
    )

    assert len(violations(offender)) == 3


def test_registry_knows_type_names_but_not_their_vocabulary() -> None:
    """레지스트리가 아는 것은 이름과 선언 모듈의 위치뿐이다.

    타입 어휘(생성기 구현, 산출물 파일명)가 여기로 올라오면 두 번째 타입 추가가 이
    파일부터 시작된다. 문서 문자열은 예시로 파일명을 들 수 있으므로 코드만 본다.
    """
    assert violations(REGISTRY_MODULE) == []
    assert set(shorts_types.BUILTIN_TYPES) == {"quiz"}
    assert shorts_types.BUILTIN_TYPES["quiz"].startswith(TYPES_MODULE)
