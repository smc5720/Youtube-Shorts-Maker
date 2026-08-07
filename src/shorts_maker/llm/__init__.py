"""LLM provider 레이어 — 공통 모듈이므로 `types/` 밖이다 (스파이크 #1, 이슈 #48).

호출 지점(#9 퀴즈 생성, #10 블라인드 검증, #13 메타데이터)은 provider 구현이 아니라
**역할**을 고르고, 스키마를 만족하는 JSON 하나를 받아 온다.

```python
from shorts_maker.llm import LLMError, provider_for_role

generator = provider_for_role("generator", config=config)
try:
    result = generator.complete_json(system=SYSTEM, prompt=prompt, schema=SCHEMA)
except LLMError as error:
    ...
result.data   # 스키마를 통과한 dict
result.model  # 응답이 보고한 실제 모델 ID
```

프롬프트는 여기 없다. 호출 지점이 각자 소유한다.
"""

from __future__ import annotations

from .provider import LLMError, LLMProvider, LLMResult, RetryingProvider
from .registry import (
    ROLES,
    UnknownProviderError,
    available_providers,
    provider_for_role,
    validate_providers,
)

__all__ = [
    "ROLES",
    "LLMError",
    "LLMProvider",
    "LLMResult",
    "RetryingProvider",
    "UnknownProviderError",
    "available_providers",
    "provider_for_role",
    "validate_providers",
]
