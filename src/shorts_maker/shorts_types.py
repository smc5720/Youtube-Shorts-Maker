"""지원하는 쇼츠 타입 목록.

지금은 이름만 안다. `--type` 값을 검증하고 기록하는 것이 전부다.
타입별 콘텐츠 생성기와 장면 템플릿을 실제로 물리는 플러그인 레지스트리는 #8에서
이 모듈을 확장한다. 새 타입을 추가할 때 여기 한 곳만 고치면 CLI 검증과 `--help`
출력이 함께 따라온다.
"""

from __future__ import annotations

SUPPORTED_TYPES: tuple[str, ...] = ("quiz",)
DEFAULT_TYPE = "quiz"
