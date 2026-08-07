"""provider 이름 → adapter, 그리고 config에서 역할별 provider를 만드는 자리.

**등록되지 않은 이름은 run 디렉터리를 만들기 전에 멈춘다.** `main`이 `validate_providers`를
설정·타입 검증과 같은 자리에서 부른다. 파이프라인이 절반 돌고 나서 provider 오타에서
터지면 반쪽짜리 산출물이 남은 run 디렉터리가 검수 대상과 섞인다 (`config.py`·
`shorts_types.py`가 같은 이유로 그렇게 한다).

**역할(`generator` / `verifier`)을 분리한 것은 검증력 때문이다.** 같은 모델로 블라인드
재답변을 시키면 프롬프트만 탈상관되고 모델의 지식은 그대로 공유된다 (스파이크 4.2).
#10이 검증 모델을 바꿔 실측할 수 있도록 호출 경로가 역할을 먼저 받는다.

```python
generator = provider_for_role("generator", config=config)
result = generator.complete_json(system=SYSTEM, prompt=prompt, schema=SCHEMA)
```
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from ..config import Config
from . import claude_cli
from .provider import LLMError, LLMProvider, RetryingProvider

ROLES: tuple[str, ...] = ("generator", "verifier")
"""`llm.<role>` 설정 섹션 이름. 호출 지점은 provider 이름이 아니라 역할을 고른다."""


class ProviderFactory(Protocol):
    """adapter 생성자. `options`는 `llm.providers.<이름>` 섹션 그대로다.

    레지스트리가 `binary` 같은 adapter 전용 키를 알지 않도록 섹션을 통째로 넘긴다.
    """

    def __call__(
        self, *, model: str, options: Mapping[str, Any], timeout_sec: int
    ) -> LLMProvider: ...


BUILTIN_PROVIDERS: Mapping[str, ProviderFactory] = {
    claude_cli.PROVIDER_NAME: claude_cli.create,
}
"""등록된 adapter. 추가하는 이슈가 `config.SPEC`의 `llm.providers`에도 자기 항목을 넣는다."""


class UnknownProviderError(LLMError):
    """등록되지 않은 provider 이름을 지정했다."""

    def __init__(self, name: str, *, setting: str) -> None:
        self.name = name
        available = ", ".join(available_providers())
        super().__init__(
            f"등록되지 않은 LLM provider {name!r} ({setting}). 등록된 provider: {available}",
            retryable=False,
        )


def available_providers() -> tuple[str, ...]:
    return tuple(BUILTIN_PROVIDERS)


def validate_providers(config: Config) -> None:
    """설정이 지정한 provider 이름이 전부 등록돼 있는지 확인한다.

    이름만 본다. 바이너리 존재 여부나 인증 상태는 확인하지 않는다 — 그것은 실행 환경의
    문제이고 첫 호출에서 `LLMError`로 드러난다.

    Raises:
        UnknownProviderError: 등록되지 않은 이름일 때.
    """
    for role in ROLES:
        setting = f"llm.{role}.provider"
        name = config.get(setting)
        if name not in BUILTIN_PROVIDERS:
            raise UnknownProviderError(str(name), setting=setting)


def provider_for_role(role: str, *, config: Config) -> LLMProvider:
    """역할에 해당하는 provider를 만든다. 재시도·호출 기록이 씌워진 상태로 돌려준다.

    Raises:
        ValueError: 정의되지 않은 역할일 때 (호출부의 오타 — 설정 문제가 아니다).
        UnknownProviderError: 설정이 등록되지 않은 provider를 가리킬 때.
    """
    if role not in ROLES:
        raise ValueError(f"정의되지 않은 LLM 역할 {role!r}. 가능한 역할: {', '.join(ROLES)}")

    setting = f"llm.{role}.provider"
    name = str(config.get(setting))
    factory = BUILTIN_PROVIDERS.get(name)
    if factory is None:
        raise UnknownProviderError(name, setting=setting)

    inner = factory(
        model=str(config.get(f"llm.{role}.model")),
        options=_provider_options(config, name),
        timeout_sec=int(config.get("llm.timeout_sec")),
    )
    return RetryingProvider(inner=inner, max_retries=int(config.get("llm.max_retries")))


def _provider_options(config: Config, name: str) -> Mapping[str, Any]:
    """`llm.providers.<name>` 섹션. adapter를 등록만 하고 설정 키를 열지 않았으면 빈 섹션이다."""
    try:
        options = config.get(f"llm.providers.{name}")
    except KeyError:
        return {}
    return options if isinstance(options, dict) else {}
