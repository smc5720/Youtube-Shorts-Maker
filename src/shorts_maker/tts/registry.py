"""provider 이름 → adapter, 그리고 config에서 합성기를 만드는 자리.

`llm/registry.py`와 같은 구조·같은 기준이다. 두 provider 레이어가 다른 모양이면 세 번째를
붙일 때 어느 쪽을 베낄지 매번 정해야 한다.

**등록되지 않은 이름은 run 디렉터리를 만들기 전에 멈춘다.** `main`이
`validate_tts_provider`를 `validate_providers`와 같은 자리에서 부른다. provider 오타로
빈 run 디렉터리가 쌓이면 검수할 산출물과 구분되지 않는다.

```python
synthesizer = create_synthesizer(config)
speech = synthesizer.synthesize("세계에서 가장 긴 강은?", run_dir / "audio" / "seg-003.mp3")
speech.duration_sec  # ffprobe가 잰 실측 길이
```
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from ..config import Config
from . import edge_tts
from .provider import RetryingProvider, TTSError, TTSProvider
from .speech import SpeechCache, SpeechSynthesizer

PROVIDER_SETTING = "tts.provider"


class ProviderFactory(Protocol):
    """adapter 생성자. `options`는 `tts.providers.<이름>` 섹션 그대로다.

    `edge_tts`는 여는 키가 없어 그 섹션 자체가 없지만(빈 섹션을 미리 만들면 "알 수 없는
    설정 키" 검출과 충돌한다), 키를 여는 adapter가 추가될 때 계약을 바꾸지 않도록 자리를
    남긴다 — `llm/registry.py`와 같다.
    """

    def __call__(
        self, *, voice: str, options: Mapping[str, Any], timeout_sec: int
    ) -> TTSProvider: ...


BUILTIN_PROVIDERS: Mapping[str, ProviderFactory] = {
    edge_tts.PROVIDER_NAME: edge_tts.create,
}
"""등록된 adapter. 설정 키를 여는 adapter를 추가하는 이슈가 `config.SPEC`에 `tts.providers`
섹션도 함께 만든다."""


class UnknownTTSProviderError(TTSError):
    """등록되지 않은 provider 이름을 지정했다."""

    def __init__(self, name: str, *, setting: str = PROVIDER_SETTING) -> None:
        self.name = name
        available = ", ".join(available_providers())
        super().__init__(
            f"등록되지 않은 TTS provider {name!r} ({setting}). 등록된 provider: {available}",
            retryable=False,
        )


def available_providers() -> tuple[str, ...]:
    return tuple(BUILTIN_PROVIDERS)


def validate_tts_provider(config: Config) -> None:
    """설정이 지정한 provider 이름이 등록돼 있는지 확인한다.

    이름만 본다. 모듈·바이너리 존재 여부나 네트워크 상태는 확인하지 않는다 — 그것은 실행
    환경의 문제이고 첫 합성에서 `TTSError`로 드러난다 (`llm/registry.py`와 같은 기준).

    Raises:
        UnknownTTSProviderError: 등록되지 않은 이름일 때.
    """
    name = config.get(PROVIDER_SETTING)
    if name not in BUILTIN_PROVIDERS:
        raise UnknownTTSProviderError(str(name))


def create_synthesizer(config: Config) -> SpeechSynthesizer:
    """설정대로 adapter를 만들고 재시도·캐시를 씌워 돌려준다.

    Raises:
        UnknownTTSProviderError: 설정이 등록되지 않은 provider를 가리킬 때.
    """
    name = str(config.get(PROVIDER_SETTING))
    factory = BUILTIN_PROVIDERS.get(name)
    if factory is None:
        raise UnknownTTSProviderError(name)

    inner = factory(
        voice=str(config.get("tts.voice")),
        options=_provider_options(config, name),
        timeout_sec=int(config.get("tts.timeout_sec")),
    )
    provider = RetryingProvider(inner=inner, max_retries=int(config.get("tts.max_retries")))
    return SpeechSynthesizer(provider=provider, cache=_cache(config))


def _cache(config: Config) -> SpeechCache | None:
    """`tts.cache_dir`이 null이면 캐시를 끈다 — 읽지도 쓰지도 않는다."""
    directory = config.get("tts.cache_dir")
    return None if directory is None else SpeechCache(directory=Path(str(directory)))


def _provider_options(config: Config, name: str) -> Mapping[str, Any]:
    """`tts.providers.<name>` 섹션. 설정 키를 열지 않은 adapter는 빈 섹션이다."""
    try:
        options = config.get(f"tts.providers.{name}")
    except KeyError:
        return {}
    return options if isinstance(options, dict) else {}
