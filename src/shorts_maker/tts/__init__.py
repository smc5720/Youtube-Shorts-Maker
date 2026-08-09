"""TTS provider 레이어 — 공통 모듈이므로 `types/` 밖이다 (스파이크 #2, 이슈 #14).

호출 지점(#15 세그먼트 합성)은 provider 구현이 아니라 **합성기**를 만들어 문장 하나를
확정된 오디오 세그먼트 하나로 바꾼다.

```python
from shorts_maker.tts import TTSError, create_synthesizer

synthesizer = create_synthesizer(config)
try:
    speech = synthesizer.synthesize(text, run_dir / "audio" / "seg-003.mp3")
except TTSError as error:
    ...
speech.duration_sec   # ffprobe가 잰 실측 길이 (provider 보고값이 아니다)
speech.word_timings   # provider가 주지 않으면 None
```

`llm/`과 같은 구조다 — 계약(`provider.py`) · 재시도 래퍼 · 이름 → adapter 레지스트리 ·
run 디렉터리를 만들기 전에 이름을 확인하는 `validate_tts_provider`. 다른 점은 **길이와
캐시를 다루는 공통 레이어(`speech.py`)가 계약 위에 하나 더 있다**는 것이다.
"""

from __future__ import annotations

from .provider import RetryingProvider, TTSError, TTSProvider, WordTiming
from .registry import (
    UnknownTTSProviderError,
    available_providers,
    create_synthesizer,
    validate_tts_provider,
)
from .speech import Speech, SpeechCache, SpeechSynthesizer, probe_duration

__all__ = [
    "RetryingProvider",
    "Speech",
    "SpeechCache",
    "SpeechSynthesizer",
    "TTSError",
    "TTSProvider",
    "UnknownTTSProviderError",
    "WordTiming",
    "available_providers",
    "create_synthesizer",
    "probe_duration",
    "validate_tts_provider",
]
