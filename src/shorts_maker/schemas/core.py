"""스키마 검증 primitives — 세 산출물 스키마가 공유한다.

설계 결정 두 가지가 이 모듈의 형태를 정한다.

- **오류를 모아서 던진다.** 첫 위반에서 멈추면 한 필드 고치고 다시 돌려서 다음 위반을 보는
  왕복이 생긴다. `config.py`가 설정 오류를 모아 던지는 것과 같은 이유다.
- **모든 오류에 필드 경로가 붙는다** (`questions[2].difficulty`). 이 JSON들은 사람이 아니라
  LLM과 장면 템플릿이 만든다. 어느 필드가 틀렸는지가 곧 어느 프롬프트·코드를 고쳐야
  하는지이므로, 경로 없는 "검증 실패"는 쓸모가 없다.

**`jsonschema`를 쓰지 않는다.** 이 스키마의 핵심 규칙 — `narrate` 여부로 오디오 필드 요구가
갈리는 확정 검증, 세그먼트 파일명의 인덱스와 장면 배열 위치의 일치 — 가 JSON Schema로
표현되지 않아 어차피 코드로 써야 하고, 조건부 규칙을 `allOf`/`if`로 우회하면 오류 경로가
`allOf[1].then.required`처럼 나와 위 두 번째 원칙이 깨진다. 의존성도 늘지 않는다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TYPE_LABELS = {"str": "문자열", "int": "정수", "float": "실수", "bool": "참/거짓"}


class SchemaError(Exception):
    """산출물 JSON이 스키마를 만족하지 않는다.

    `messages`에 위반이 하나씩 들어 있고 각 항목은 필드 경로로 시작한다. 사람에게 보여줄
    때는 `str(error)`가 파일 경로와 함께 줄바꿈으로 이어 준다.
    """

    def __init__(self, messages: list[str], *, source: Path | None = None) -> None:
        self.messages = messages
        self.source = source
        """검증한 파일. 메모리 위의 dict를 검증했으면 `None`."""
        body = "\n".join(messages)
        super().__init__(f"{source}:\n{body}" if source is not None else body)


class Rule:
    """값 하나를 검증한다. 위반을 `errors`에 append하고 예외는 던지지 않는다."""

    def check(self, value: Any, path: str, errors: list[str]) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class Field:
    """객체 안의 필드 하나 — 규칙과 필수 여부.

    필수 여부는 필드가 아니라 **그 필드를 담은 객체**의 성질이므로 규칙과 분리한다.
    같은 규칙이 어떤 스키마에서는 필수, 다른 곳에서는 선택일 수 있다.
    """

    rule: Rule
    required: bool = True


@dataclass(frozen=True)
class Scalar(Rule):
    """문자열·수·참거짓 하나."""

    kind: str  # "str" | "int" | "float" | "bool"
    nullable: bool = False
    choices: tuple[Any, ...] | None = None
    allow_empty: bool = False  # kind == "str"일 때만
    pattern: str | None = None  # kind == "str"일 때만, 전체 일치
    minimum: float | None = None
    exclusive_minimum: float | None = None
    maximum: float | None = None

    def check(self, value: Any, path: str, errors: list[str]) -> None:
        if value is None:
            if not self.nullable:
                errors.append(f"{path}: {self._expected()} 값이 필요하다. 받은 값: null")
            return

        if not self._accepts(value):
            errors.append(f"{path}: {self._expected()} 값이 필요하다. 받은 값: {describe(value)}")
            return

        if self.choices is not None and value not in self.choices:
            allowed = " | ".join(str(choice) for choice in self.choices)
            errors.append(f"{path}: 허용되지 않는 값 {value!r}. 가능한 값: {allowed}")
            return

        if self.kind == "str":
            self._check_text(value, path, errors)
        else:
            self._check_range(value, path, errors)

    def _accepts(self, value: Any) -> bool:
        # bool은 int의 하위 타입이라 먼저 걸러낸다. `countdown_sec: true`가 정수로
        # 통과하면 안 된다.
        if isinstance(value, bool):
            return self.kind == "bool"
        if self.kind == "str":
            return isinstance(value, str)
        if self.kind == "int":
            return isinstance(value, int)
        if self.kind == "float":
            return isinstance(value, (int, float))  # `duration: 3`을 받아 준다
        return False

    def _check_text(self, value: str, path: str, errors: list[str]) -> None:
        if not self.allow_empty and not value.strip():
            # 공백만 있는 값도 빈 값으로 본다. 자막에 " "가 렌더되는 것이 목적일 수 없다.
            errors.append(f"{path}: 빈 문자열일 수 없다")
        elif self.pattern is not None and re.fullmatch(self.pattern, value) is None:
            errors.append(f"{path}: `{self.pattern}` 형식이어야 한다. 받은 값: {value!r}")

    def _check_range(self, value: float, path: str, errors: list[str]) -> None:
        if self.minimum is not None and value < self.minimum:
            errors.append(f"{path}: {self.minimum} 이상이어야 한다. 받은 값: {value}")
        if self.exclusive_minimum is not None and value <= self.exclusive_minimum:
            errors.append(f"{path}: {self.exclusive_minimum}보다 커야 한다. 받은 값: {value}")
        if self.maximum is not None and value > self.maximum:
            errors.append(f"{path}: {self.maximum} 이하여야 한다. 받은 값: {value}")

    def _expected(self) -> str:
        label = _TYPE_LABELS[self.kind]
        return f"{label} 또는 null" if self.nullable else label


@dataclass(frozen=True)
class Object(Rule):
    """정해진 필드만 가지는 매핑.

    **모르는 필드는 오류다.** 조용히 무시하면 `narate: true` 같은 오타가 "낭독 장면이
    아님"으로 통과해 음성 없는 영상이 나온다. 새 타입이 자기 통과 필드를 추가할 때는
    해당 스키마에 필드를 함께 선언한다 — 스키마가 단일 진실 공급원인 이유다.
    """

    fields: Mapping[str, Field]

    def check(self, value: Any, path: str, errors: list[str]) -> None:
        if not isinstance(value, dict):
            errors.append(f"{path or '최상위'}: 키-값 매핑이 필요하다. 받은 값: {describe(value)}")
            return

        for key in value:
            if key not in self.fields:
                errors.append(f"{_join(path, key)}: 알 수 없는 필드다. {self._available(path)}")

        for key, field in self.fields.items():
            if key in value:
                field.rule.check(value[key], _join(path, key), errors)
            elif field.required:
                errors.append(f"{_join(path, key)}: 필수 필드가 없다")

    def _available(self, path: str) -> str:
        where = f"{path} 아래에" if path else "최상위에"
        return f"{where} 쓸 수 있는 필드: {', '.join(self.fields)}"


@dataclass(frozen=True)
class Array(Rule):
    """같은 규칙을 따르는 항목의 목록. 항목 경로는 `scenes[2]`로 매긴다."""

    item: Rule
    min_items: int = 0

    def check(self, value: Any, path: str, errors: list[str]) -> None:
        if not isinstance(value, list):
            errors.append(f"{path}: 목록이 필요하다. 받은 값: {describe(value)}")
            return

        if len(value) < self.min_items:
            errors.append(f"{path}: 항목이 최소 {self.min_items}개 필요하다. 받은 값: {len(value)}개")

        for index, item in enumerate(value):
            self.item.check(item, f"{path}[{index}]", errors)


# --- 스키마 선언을 짧게 쓰기 위한 생성자 ------------------------------------


def text(
    *,
    required: bool = True,
    nullable: bool = False,
    choices: tuple[str, ...] | None = None,
    allow_empty: bool = False,
    pattern: str | None = None,
) -> Field:
    return Field(
        Scalar(
            "str",
            nullable=nullable,
            choices=choices,
            allow_empty=allow_empty,
            pattern=pattern,
        ),
        required=required,
    )


def integer(
    *,
    required: bool = True,
    minimum: float | None = None,
    maximum: float | None = None,
) -> Field:
    return Field(Scalar("int", minimum=minimum, maximum=maximum), required=required)


def number(
    *,
    required: bool = True,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
    maximum: float | None = None,
) -> Field:
    return Field(
        Scalar(
            "float",
            minimum=minimum,
            exclusive_minimum=exclusive_minimum,
            maximum=maximum,
        ),
        required=required,
    )


def flag(*, required: bool = True) -> Field:
    return Field(Scalar("bool"), required=required)


def section(fields: Mapping[str, Field], *, required: bool = True) -> Field:
    return Field(Object(fields), required=required)


def items(rule: Rule, *, min_items: int = 0, required: bool = True) -> Field:
    return Field(Array(rule, min_items=min_items), required=required)


# --- 파일 단위 스키마 -------------------------------------------------------

Check = Callable[[Any, list[str]], None]
"""구조 검증을 통과한 데이터에 적용하는 추가 규칙. 위반을 `errors`에 append한다."""


@dataclass(frozen=True)
class Schema:
    """산출물 파일 하나의 계약."""

    name: str
    """파일 이름 (`"quiz.json"`). 오류 메시지와 로그에 쓴다."""

    versions: tuple[int, ...]
    """이 코드가 아는 `schema_version` 목록."""

    root: Object

    checks: tuple[Check, ...] = ()
    """필드 하나로 표현되지 않는 규칙 — 조건부 필수, 값 사이의 일관성."""

    def validate(self, data: Any, *, source: Path | None = None) -> None:
        """`data`를 검증하고, 위반이 있으면 전부 모아 `SchemaError`로 던진다."""
        version = data.get("schema_version") if isinstance(data, dict) else None
        # bool은 int의 하위 타입이라 걸러낸다. 타입 자체가 틀렸으면 아래 구조 검증이
        # 지적하므로 여기서는 "정수이지만 모르는 값"만 다룬다.
        numbered = isinstance(version, int) and not isinstance(version, bool)
        if numbered and version not in self.versions:
            # 모르는 버전이면 나머지 필드 검증 결과가 의미 없다. 이 코드가 아는 모양의
            # 파일이 아니므로 "알 수 없는 필드" 목록을 쏟아내는 대신 버전만 지적한다.
            known = ", ".join(str(candidate) for candidate in self.versions)
            raise SchemaError(
                [f"schema_version: 모르는 버전 {version}. 이 코드가 아는 버전: {known}"],
                source=source,
            )

        errors: list[str] = []
        self.root.check(data, "", errors)
        if not errors:
            # 추가 규칙은 구조가 맞다는 것을 전제로 쓴다. 타입 오류가 남은 데이터를
            # 넘기면 규칙 안에서 터진다.
            for check in self.checks:
                check(data, errors)
        if errors:
            raise SchemaError(errors, source=source)

    def load(self, path: Path) -> dict[str, Any]:
        """파일을 읽고 검증해서 돌려준다. 읽기·문법·검증 실패 모두 `SchemaError`다."""
        data = read_json(path)
        self.validate(data, source=path)
        return data


def read_json(path: Path) -> Any:
    """JSON 파일을 읽는다. 실패를 `SchemaError`로 통일해 호출부의 분기를 줄인다."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SchemaError([f"파일을 읽을 수 없다 — {error}"], source=path) from error

    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise SchemaError([f"JSON 문법 오류 — {error}"], source=path) from error


def describe(value: Any) -> str:
    """오류 메시지에 넣을 값 설명. `config.py`의 같은 이름 헬퍼와 형식을 맞춘다."""
    if value is None:
        return "null"
    label = {
        bool: _TYPE_LABELS["bool"],
        int: _TYPE_LABELS["int"],
        float: _TYPE_LABELS["float"],
        str: _TYPE_LABELS["str"],
        dict: "매핑",
        list: "목록",
    }.get(type(value), type(value).__name__)
    return f"{value!r}({label})"


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else str(key)
