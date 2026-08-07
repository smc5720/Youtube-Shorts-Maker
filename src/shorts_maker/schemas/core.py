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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TYPE_LABELS = {"str": "문자열", "int": "정수", "float": "실수", "bool": "참/거짓"}
_JSON_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}


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

    def to_json_schema(self) -> dict[str, Any]:
        """같은 규칙을 JSON Schema로 옮긴다 — LLM에 `--json-schema`로 넘길 형태다.

        **검증에 이 결과를 쓰지 않는다.** 검증은 위 `check`가 하고, 이 변환은 산출물
        스키마를 생성 프롬프트 쪽에 다시 적지 않기 위한 것이다. 필드 이름과 열거값이
        두 곳에 생기면 한쪽만 고쳐졌을 때 모델이 낡은 모양을 만들어 낸다 (PRD 14.1).

        조건부 규칙(`Schema.checks`)은 옮기지 않는다. JSON Schema로 표현되지 않아
        이 패키지가 `jsonschema`를 쓰지 않는 이유이기도 하다.
        """
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

    def to_json_schema(self) -> dict[str, Any]:
        kind = _JSON_TYPES[self.kind]
        node: dict[str, Any] = {"type": [kind, "null"] if self.nullable else kind}

        if self.choices is not None:
            node["enum"] = [*self.choices, *([None] if self.nullable else [])]
        if self.kind == "str":
            if not self.allow_empty:
                node["minLength"] = 1
            if self.pattern is not None:
                # `check`는 `re.fullmatch`를 쓰지만 JSON Schema의 pattern은 부분 일치다.
                node["pattern"] = f"^(?:{self.pattern})$"
        else:
            if self.minimum is not None:
                node["minimum"] = self.minimum
            if self.exclusive_minimum is not None:
                node["exclusiveMinimum"] = self.exclusive_minimum
            if self.maximum is not None:
                node["maximum"] = self.maximum
        return node


@dataclass(frozen=True)
class Choices(Rule):
    """후보 목록을 **검증 시점에** 조회하는 문자열 필드.

    `Scalar(choices=...)`는 import 시점에 후보가 고정된다. 쇼츠 타입처럼 레지스트리가
    런타임에 정하는 값에 그것을 쓰면, 이 모듈이 import된 순간의 목록으로 굳어 나중에
    등록된 타입의 `scenes.json`이 "허용되지 않는 값"으로 반려된다.
    """

    options: Callable[[], Sequence[str]]
    label: str
    """오류 메시지에서 후보 목록 앞에 붙는 말 ("등록된 타입")."""

    def check(self, value: Any, path: str, errors: list[str]) -> None:
        if not isinstance(value, str):
            errors.append(
                f"{path}: {_TYPE_LABELS['str']} 값이 필요하다. 받은 값: {describe(value)}"
            )
            return

        allowed = tuple(self.options())
        if value not in allowed:
            errors.append(
                f"{path}: 허용되지 않는 값 {value!r}. {self.label}: {' | '.join(allowed)}"
            )

    def to_json_schema(self) -> dict[str, Any]:
        # 후보를 지금 조회해 굳힌다. 파생된 JSON Schema는 호출 1회에 넘기고 버리는
        # 값이므로, `check`와 달리 나중에 등록될 타입을 기다릴 필요가 없다.
        return {"type": "string", "enum": list(self.options())}


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

    def without(self, *names: str) -> Object:
        """일부 필드를 뺀 사본. 파일 스키마에서 부분 스키마를 파생시킬 때 쓴다.

        **없는 이름을 주면 오류다.** 조용히 무시하면 필드 이름을 바꾼 뒤에도 이 호출이
        통과해, 코드가 채울 필드가 LLM에 넘기는 스키마에 남는다.
        """
        unknown = [name for name in names if name not in self.fields]
        if unknown:
            raise ValueError(
                f"{', '.join(unknown)}: 이 스키마에 없는 필드다. "
                f"쓸 수 있는 필드: {', '.join(self.fields)}"
            )
        return Object({key: field for key, field in self.fields.items() if key not in names})

    def to_json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {key: field.rule.to_json_schema() for key, field in self.fields.items()},
            "required": [key for key, field in self.fields.items() if field.required],
            # `check`가 모르는 필드를 오류로 보는 것과 같다. 모델이 필드를 하나 더
            # 지어내면 그 값은 산출물 검증에서 어차피 반려된다.
            "additionalProperties": False,
        }


@dataclass(frozen=True)
class Array(Rule):
    """같은 규칙을 따르는 항목의 목록. 항목 경로는 `scenes[2]`로 매긴다.

    상한(`max_items`)은 하한과 성질이 다르다. 하한은 "비어 있으면 쓸모가 없다"는 요구지만,
    상한은 개수 자체가 계약인 필드를 위한 것이다 — `metadata.json`의 제목 후보 3개처럼
    개수가 흔들리면 그 값을 쓰는 쪽(사람이 셋 중 하나를 고른다)이 깨진다.
    """

    item: Rule
    min_items: int = 0
    max_items: int | None = None

    def check(self, value: Any, path: str, errors: list[str]) -> None:
        if not isinstance(value, list):
            errors.append(f"{path}: 목록이 필요하다. 받은 값: {describe(value)}")
            return

        count = len(value)
        if count < self.min_items or (self.max_items is not None and count > self.max_items):
            errors.append(f"{path}: {self._expected_count()}. 받은 값: {count}개")

        for index, item in enumerate(value):
            self.item.check(item, f"{path}[{index}]", errors)

    def _expected_count(self) -> str:
        if self.max_items is None:
            return f"항목이 최소 {self.min_items}개 필요하다"
        if self.max_items == self.min_items:
            return f"항목이 정확히 {self.min_items}개여야 한다"
        if self.min_items == 0:
            return f"항목이 최대 {self.max_items}개여야 한다"
        return f"항목이 {self.min_items}~{self.max_items}개여야 한다"

    def to_json_schema(self) -> dict[str, Any]:
        node: dict[str, Any] = {"type": "array", "items": self.item.to_json_schema()}
        if self.min_items:
            node["minItems"] = self.min_items
        if self.max_items is not None:
            node["maxItems"] = self.max_items
        return node


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


def choices_from(
    options: Callable[[], Sequence[str]],
    *,
    label: str,
    required: bool = True,
) -> Field:
    return Field(Choices(options, label), required=required)


def flag(*, required: bool = True) -> Field:
    return Field(Scalar("bool"), required=required)


def section(fields: Mapping[str, Field], *, required: bool = True) -> Field:
    return Field(Object(fields), required=required)


def items(
    rule: Rule,
    *,
    min_items: int = 0,
    max_items: int | None = None,
    required: bool = True,
) -> Field:
    return Field(Array(rule, min_items=min_items, max_items=max_items), required=required)


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
