"""쇼츠 타입 레지스트리 — 이름에서 콘텐츠 생성기·장면 템플릿·산출물 선언을 찾는다.

퀴즈 스펙 1장이 타입을 1급 개념으로 두고 **콘텐츠 생성기**와 **장면 템플릿** 두 축을
플러그인처럼 교체하도록 정한다. 이 모듈이 그 교체 지점이다.

**이 모듈은 특정 타입을 모른다.** 아는 것은 이름과 선언 모듈의 위치뿐이고, 생성기·템플릿·
산출물 선언은 타입 패키지가 소유한다(`SHORTS_TYPE`). 여기에 `quiz.json` 같은 타입 어휘가
들어오기 시작하면 두 번째 타입을 추가할 때 이 파일부터 고쳐야 하고, 공통 파이프라인이
타입 전용 산출물을 모르는 경계(퀴즈 스펙 1.1 / PRD 7.4.1)에도 구멍이 난다.

조회 시점에 선언 모듈을 import한다. `--type` 선택지와 `--help`는 이름만 있으면 되므로 타입
코드(LLM 클라이언트 등)를 import하지 않고, `schemas`가 이 모듈을 import하는 방향과도
충돌하지 않는다.

```python
shorts_type = get_type(args.shorts_type)      # 미등록이면 UnknownShortsTypeError
content = shorts_type.generator(topic=topic, config=config)
scenes = shorts_type.scene_template(content, config=config)
if shorts_type.produces(SCRIPT_ARTIFACT):     # 퀴즈는 False — 없는 것이 정상이다
    ...
```
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .config import Config

DEFAULT_TYPE = "quiz"

BUILTIN_TYPES: Mapping[str, str] = {
    "quiz": "shorts_maker.types.quiz",
}
"""타입 이름 → 선언 모듈 경로. 모듈은 `SHORTS_TYPE`으로 자기 `ShortsType`을 노출한다."""

DECLARATION_ATTR = "SHORTS_TYPE"

SCRIPT_ARTIFACT = "script.txt"
SUMMARY_ARTIFACT = "summary.json"
"""타입이 생성 여부를 결정하는 선택적 산출물 (PRD 6.2 표).

`source.json`은 여기 없다 — 입력 경로(`--url` / `--text-file`)가 결정하므로 타입의 성질이
아니다. 공통 산출물(`scenes.json` 등)도 없다 — 타입과 무관하게 항상 생성된다.
"""


class ShortsTypeError(Exception):
    """레지스트리 조회·등록 실패."""


class UnknownShortsTypeError(ShortsTypeError):
    """등록되지 않은 타입을 조회했다.

    CLI는 argparse `choices`가 먼저 막지만, 앱 백엔드나 테스트처럼 CLI를 거치지 않는
    호출 경로도 있다. 그 경로에서도 실행 초기에 같은 메시지로 멈춘다.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        available = ", ".join(available_types())
        super().__init__(f"등록되지 않은 쇼츠 타입 {name!r}. 등록된 타입: {available}")


class ContentGenerator(Protocol):
    """입력 → 타입 전용 콘텐츠 산출물의 내용 (퀴즈는 `quiz.json`).

    파일로 쓰는 것은 파이프라인의 몫이다. 생성기는 해당 스키마를 통과하는 dict를 돌려준다.
    타입 내부에 단계가 더 있어도(퀴즈의 블라인드 재답변 검증 #10) 그 결과까지 반영한
    상태여야 한다 — 레지스트리가 아는 축은 생성기와 장면 템플릿 둘뿐이다.
    """

    def __call__(self, *, topic: str, config: Config) -> dict[str, Any]: ...


class SceneTemplate(Protocol):
    """콘텐츠 → `scenes.json` 초안.

    돌려주는 dict의 `type`은 이 템플릿이 속한 타입 이름이다. 길이는 목표치
    (`target_duration`)까지만 채운다 — 확정은 실측 오디오 길이가 한다 (PRD 7.5.1).

    렌더에 필요한 타입 전용 정보를 `scenes.json` 필드로 옮겨 담는 것도 여기서 한다.
    이 경계를 넘는 순간 공통 파이프라인이 타입을 알게 된다 (퀴즈 스펙 1.1).
    """

    def __call__(self, content: Mapping[str, Any], *, config: Config) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ShortsType:
    """타입 하나의 선언. 타입 패키지가 소유하고 레지스트리가 조회한다."""

    name: str
    """`--type` 값이자 `scenes.json` / `project.json`의 `type` 값."""

    content_artifact: str
    """타입 전용 콘텐츠 산출물 파일명 (퀴즈는 `quiz.json`)."""

    generator: ContentGenerator
    scene_template: SceneTemplate

    produces_script: bool = False
    """`script.txt`를 만드는가. 내레이션 대본을 쓰는 타입만 참이다."""

    produces_summary: bool = False
    """`summary.json`을 만드는가. 원문 요약(PRD 7.2)을 거치는 타입만 참이다.

    참이어도 입력 경로에 원문이 없으면 생성되지 않는다 — 타입은 "요약 단계를 쓰는가"만
    선언하고, 실제 생성 여부는 입력 경로와의 논리곱이다 (PRD 6.2 표).
    """

    def artifacts(self) -> tuple[str, ...]:
        """이 타입이 만드는 타입 전용 산출물 전부."""
        names = [self.content_artifact]
        if self.produces_script:
            names.append(SCRIPT_ARTIFACT)
        if self.produces_summary:
            names.append(SUMMARY_ARTIFACT)
        return tuple(names)

    def produces(self, artifact: str) -> bool:
        """`artifact`를 이 타입이 만드는지.

        후속 단계는 파일 부재를 실패로 판정하기 전에 이 값을 본다 (PRD 14.1). 조건이
        호출부마다 다시 쓰이면 퀴즈 타입의 정상 동작이 "파일 없음" 오류가 된다.

        Raises:
            ValueError: 타입이 결정하지 않는 산출물을 물었을 때. 조용히 `False`를
                돌려주면 항상 생성되는 공통 산출물을 건너뛰게 된다.
        """
        if artifact == self.content_artifact:
            return True
        if artifact == SCRIPT_ARTIFACT:
            return self.produces_script
        if artifact == SUMMARY_ARTIFACT:
            return self.produces_summary
        raise ValueError(
            f"{artifact!r}은 타입이 생성 여부를 결정하는 산출물이 아니다. "
            f"공통 산출물이거나 입력 경로가 결정한다 (PRD 6.2 표). "
            f"{self.name} 타입이 결정하는 것: "
            f"{', '.join((self.content_artifact, SCRIPT_ARTIFACT, SUMMARY_ARTIFACT))}"
        )


_REGISTRY: dict[str, ShortsType | str] = dict(BUILTIN_TYPES)
"""등록된 타입. 값은 선언 모듈 경로(미해석) 또는 해석된 `ShortsType`이다."""


def available_types() -> tuple[str, ...]:
    """등록된 타입 이름. `--type` 선택지와 스키마의 `type` 후보가 여기서 나온다."""
    return tuple(_REGISTRY)


def get_type(name: str) -> ShortsType:
    """타입 선언을 돌려준다. 처음 조회할 때 선언 모듈을 import한다."""
    try:
        entry = _REGISTRY[name]
    except KeyError:
        raise UnknownShortsTypeError(name) from None

    if isinstance(entry, str):
        entry = _load_declaration(name, entry)
        _REGISTRY[name] = entry  # import는 한 번이면 된다
    return entry


def register(shorts_type: ShortsType) -> None:
    """타입을 등록한다. 저장소 밖의 타입과 테스트용 타입이 쓰는 입구다."""
    if not shorts_type.name.strip():
        raise ShortsTypeError("쇼츠 타입 이름이 비어 있다")
    if shorts_type.name in _REGISTRY:
        raise ShortsTypeError(f"이미 등록된 쇼츠 타입 {shorts_type.name!r}이다")
    _REGISTRY[shorts_type.name] = shorts_type


def unregister(name: str) -> None:
    """등록을 해제한다. 내장 타입은 해제할 수 없다."""
    if name in BUILTIN_TYPES:
        raise ShortsTypeError(f"내장 타입 {name!r}은 해제할 수 없다")
    if _REGISTRY.pop(name, None) is None:
        raise UnknownShortsTypeError(name)


def _load_declaration(name: str, module_path: str) -> ShortsType:
    try:
        module = importlib.import_module(module_path)
    except ImportError as error:
        raise ShortsTypeError(
            f"쇼츠 타입 {name!r}의 선언 모듈 {module_path}을 import할 수 없다 — {error}"
        ) from error

    declared = getattr(module, DECLARATION_ATTR, None)
    if not isinstance(declared, ShortsType):
        raise ShortsTypeError(
            f"{module_path}에 {DECLARATION_ATTR}: ShortsType이 없다. "
            f"타입 선언은 그 타입의 패키지가 소유한다"
        )
    if declared.name != name:
        raise ShortsTypeError(
            f"{module_path}의 {DECLARATION_ATTR}.name이 {declared.name!r}이다. "
            f"레지스트리에 등록된 이름 {name!r}과 같아야 한다"
        )
    return declared
