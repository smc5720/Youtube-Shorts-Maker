"""`config.yaml` 로딩·병합·검증.

여러 모듈이 공유하는 값을 한곳에 모은다. 우선순위는 **CLI 오버라이드 > `config.yaml` >
기본값**이고, 설정 파일이 없어도 기본값만으로 동작한다.

**검증은 실행 초기에 한 번에 끝낸다.** 파이프라인이 절반 돌고 나서 오타 난 키에서 터지면
run 디렉터리에 반쪽짜리 산출물이 남는다. 그래서 `load_config()`는 run 디렉터리를 만들기
전에 호출하고, 발견한 오류를 첫 개에서 멈추지 않고 전부 모아서 던진다 — 키 하나 고치고
다시 돌려서 다음 오류를 보는 왕복을 줄인다.

키를 추가할 때는 `SPEC`만 고치면 기본값·검증·`--help`가 함께 따라온다. `config.example.yaml`
에도 같은 키를 추가해야 하며, 빠뜨리면 `tests/test_config.py`가 잡는다.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_FILENAME = "config.yaml"


class ConfigError(Exception):
    """설정을 읽거나 검증하는 데 실패했다.

    `messages`에 개별 오류가 하나씩 들어 있다. 사람에게 보여줄 때는 `str(error)`가
    줄바꿈으로 이어 준다.
    """

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("\n".join(messages))


@dataclass(frozen=True)
class Setting:
    """스칼라 설정 하나의 기본값과 허용 타입."""

    default: Any
    kind: str  # "str" | "int" | "float" | "bool"
    nullable: bool = False


_TYPE_LABELS = {"str": "문자열", "int": "정수", "float": "실수", "bool": "참/거짓"}


# 설정 키 전체. 중첩 dict는 섹션, `Setting`은 스칼라 값이다.
SPEC: dict[str, Any] = {
    "llm": {
        # 생성과 검증을 분리한다. 같은 모델로 블라인드 재답변을 시키면 프롬프트만
        # 탈상관되고 모델의 지식은 그대로 공유되므로 검증력이 구조적으로 약하다.
        # 검증 모델을 따로 바꿀 수 있어야 한다 (PRD 14.1, 스파이크 #1 §4.2).
        "generator": {
            "provider": Setting("claude_cli", "str"),
            "model": Setting("opus", "str"),
        },
        "verifier": {
            "provider": Setting("claude_cli", "str"),
            "model": Setting("opus", "str"),
            # 블라인드 재답변 호출 횟수. 문제 수와 무관하다 — 검증기가 문제를 묶어 부른다.
            # 하한(1 이상)은 여기가 아니라 `ShortsType.config_check`가 본다. 재답변이
            # 몇 번 필요한지는 사실 검증을 필수로 두는 타입의 규칙이다 (#10).
            "runs": Setting(2, "int"),
            # 미만이면 flagged. 판정은 검증기(#10)가 아니라 타입의 검수 훅(#11)이 하고,
            # 재답변 불일치·질문 모호는 임계값과 무관한 실검출이라 그 전에 이미 잡힌다.
            # 0.8은 #10의 함정 문제 세트 실측에서 확정됐다 — 의미 있는 범위가
            # (0.5, 0.98)이고 그 가운데다 (docs/spikes/10-verifier-detection.md 5장).
            "confidence_threshold": Setting(0.8, "float"),
        },
        "timeout_sec": Setting(600, "int"),
        "max_retries": Setting(2, "int"),
        # 등록된 provider 이름만 하위 키로 받는다. adapter를 추가하는 이슈가 자기
        # 항목을 여기 함께 넣는다 — 미리 넣어 두면 "알 수 없는 키" 검출과 충돌한다.
        "providers": {
            "claude_cli": {"binary": Setting("claude", "str")},
        },
    },
    "tts": {
        "provider": Setting("edge_tts", "str"),
        # 음성 이름은 provider 문자열을 그대로 통과시킨다. 자체 어휘로 매핑하면
        # Azure 승격이 "같은 음성 이름"이라 싸다는 이득이 사라진다 (스파이크 #2 §5.3).
        "voice": Setting("ko-KR-SunHiNeural", "str"),
    },
    # 낭독 장면의 duration 확정 공식에 쓰는 값 (PRD 7.5.1).
    "timing": {
        "lead_in_sec": Setting(0.30, "float"),
        "tail_sec": Setting(0.50, "float"),
        "min_duration_sec": Setting(1.20, "float"),
    },
    "quiz": {
        # 허용 범위(퀴즈 스펙 0장의 3~5)는 여기서 강제하지 않는다. 범위를 아는 것은
        # 퀴즈 타입이고, 타입이 자기 설정을 확인하는 자리가 `ShortsType.check_config`다.
        "question_count": Setting(4, "int"),
        "countdown_sec": Setting(4, "int"),
        # 낭독·자막 길이 상한. 화면에 들어가는 글자 수 문제이므로 모델이 아니라 여기서
        # 정하고, 생성기가 JSON Schema와 프롬프트 양쪽에 실어 보낸다 (#9).
        "answer_max_len": Setting(20, "int"),
        "explanation_max_len": Setting(60, "int"),
    },
    "render": {
        # null이면 번들 폰트를 찾는다. 실제 탐색과 파일 존재 확인은 #20/#38이 한다.
        "font_path": Setting(None, "str", nullable=True),
        # 프리셋 목록과의 대조는 프리셋이 생기는 #38에서 붙인다. 장면 템플릿(#12)은
        # 이 값을 읽지 않는다 — 배경은 장면별 값이 아니라 `project.json`의 필드다.
        "background": Setting("gradient_default", "str"),
    },
}


@dataclass(frozen=True)
class Config:
    """해석이 끝난 설정."""

    data: dict[str, Any]
    source: Path | None = None
    """읽어 온 설정 파일. 파일 없이 기본값만 썼으면 `None`."""

    def get(self, dotted: str) -> Any:
        """`"llm.generator.model"` 형태의 경로로 값을 읽는다."""
        cursor: Any = self.data
        for index, part in enumerate(dotted.split(".")):
            if not isinstance(cursor, dict) or part not in cursor:
                seen = ".".join(dotted.split(".")[: index + 1])
                raise KeyError(f"설정 키가 없다: {seen}")
            cursor = cursor[part]
        return cursor

    def flatten(self) -> list[tuple[str, Any]]:
        """`("llm.generator.model", "opus")` 쌍을 정의 순서대로 돌려준다. 로그용."""
        return list(_flatten(self.data, ""))


def defaults() -> dict[str, Any]:
    """`SPEC`의 기본값만으로 만든 설정 데이터."""
    return _defaults_from(SPEC)


def load_config(
    explicit_path: Path | None = None,
    *,
    overrides: Mapping[str, Any] | None = None,
    search_from: Path | None = None,
) -> Config:
    """설정을 읽어 기본값과 병합하고 검증한다.

    Args:
        explicit_path: `--config`로 지정한 경로. 주어졌는데 파일이 없으면 오류다.
            지정하지 않으면 `search_from`에서 `config.yaml`을 찾고, 없으면 기본값을 쓴다.
        overrides: CLI 오버라이드. `{"tts.voice": "..."}` 형태의 점 표기 키를 받는다.
            키별 CLI 플래그는 그 값을 실제로 쓰는 이슈가 추가하고, 여기서는 우선순위
            규칙만 제공한다.
        search_from: 기본 설정 파일을 찾을 디렉터리. 기본은 현재 작업 디렉터리.

    Raises:
        ConfigError: 파일을 읽을 수 없거나, 알 수 없는 키·잘못된 타입이 있을 때.
    """
    source = _resolve_source(explicit_path, search_from)
    from_file = _read_yaml(source) if source is not None else {}
    nested_overrides = _nest(overrides or {})

    errors: list[str] = []
    _validate(from_file, SPEC, "", errors)
    _validate(nested_overrides, SPEC, "", errors)
    if errors:
        raise ConfigError(errors)

    data = _merge(_merge(defaults(), from_file), nested_overrides)
    return Config(data=data, source=source)


def _resolve_source(explicit_path: Path | None, search_from: Path | None) -> Path | None:
    if explicit_path is not None:
        # 명시한 경로가 없는 것은 오타다. 조용히 기본값으로 넘어가면 사용자는 자기 설정이
        # 적용됐다고 믿는다. 기본 경로의 부재와 달리 오류로 처리한다.
        if not explicit_path.is_file():
            raise ConfigError([f"설정 파일을 찾을 수 없다: {explicit_path}"])
        return explicit_path

    candidate = (search_from or Path.cwd()) / DEFAULT_CONFIG_FILENAME
    return candidate if candidate.is_file() else None


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError([f"설정 파일을 읽을 수 없다: {path} — {error}"]) from error
    except yaml.YAMLError as error:
        raise ConfigError([f"{path}: YAML 문법 오류 — {error}"]) from error

    if raw is None:  # 빈 파일. 주석만 남긴 config.yaml은 정상이다.
        return {}
    if not isinstance(raw, dict):
        raise ConfigError([f"{path}: 최상위가 키-값 매핑이어야 한다"])
    return raw


def _defaults_from(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _defaults_from(node) if isinstance(node, dict) else node.default
        for key, node in spec.items()
    }


def _flatten(node: dict[str, Any], prefix: str) -> Iterator[tuple[str, Any]]:
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from _flatten(value, path)
        else:
            yield path, value


def _nest(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """점 표기 키를 중첩 dict로 바꾼다. 검증을 파일과 같은 코드로 태우기 위함이다."""
    nested: dict[str, Any] = {}
    for dotted, value in overrides.items():
        parts = dotted.split(".")
        cursor = nested
        for part in parts[:-1]:
            branch = cursor.setdefault(part, {})
            if not isinstance(branch, dict):
                raise ConfigError([f"{dotted}: 상위 키 {part}에 이미 값이 지정돼 있다"])
            cursor = branch
        cursor[parts[-1]] = value
    return nested


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """섹션은 재귀 병합하고 스칼라만 덮어쓴다.

    `tts.voice`만 준 설정 파일이 `tts.provider`를 지우면 안 되므로 섹션을 통째로
    교체하지 않는다.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = _merge(current, value)
        else:
            result[key] = value
    return result


def _validate(node: Any, spec: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append(f"{path}: 하위 키를 가지는 섹션이 필요하다. 받은 값: {_describe(node)}")
        return

    for key, value in node.items():
        child_path = f"{path}.{key}" if path else str(key)
        if key not in spec:
            errors.append(f"{child_path}: 알 수 없는 설정 키다. {_available(spec, path)}")
            continue

        child_spec = spec[key]
        if isinstance(child_spec, dict):
            _validate(value, child_spec, child_path, errors)
        elif not _accepts(value, child_spec):
            # "정수가 / 문자열이"처럼 조사가 갈리지 않도록 "값이 필요하다"로 고정한다.
            errors.append(
                f"{child_path}: {_expected(child_spec)} 값이 필요하다. 받은 값: {_describe(value)}"
            )


def _accepts(value: Any, setting: Setting) -> bool:
    if value is None:
        return setting.nullable
    # bool은 int의 하위 타입이라 먼저 걸러낸다. `question_count: true`가 정수로
    # 통과하면 안 된다.
    if isinstance(value, bool):
        return setting.kind == "bool"
    if setting.kind == "str":
        return isinstance(value, str)
    if setting.kind == "int":
        return isinstance(value, int)
    if setting.kind == "float":
        return isinstance(value, (int, float))  # `lead_in_sec: 1`을 받아 준다
    return False


def _expected(setting: Setting) -> str:
    label = _TYPE_LABELS[setting.kind]
    return f"{label} 또는 null" if setting.nullable else label


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    label = {
        bool: _TYPE_LABELS["bool"],
        int: _TYPE_LABELS["int"],
        float: _TYPE_LABELS["float"],
        str: _TYPE_LABELS["str"],
        dict: "섹션",
        list: "목록",
    }.get(type(value), type(value).__name__)
    return f"{value!r}({label})"


def _available(spec: dict[str, Any], path: str) -> str:
    where = f"{path} 아래에" if path else "최상위에"
    return f"{where} 쓸 수 있는 키: {', '.join(sorted(spec))}"
