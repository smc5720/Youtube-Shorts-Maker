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
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .assets import AssetError, background_preset_names, caption_style_names

DEFAULT_CONFIG_FILENAME = "config.yaml"

RUN_CONFIG_FILENAME = "config.used.yaml"
"""run 디렉터리에 남기는 그 실행의 설정 (#92, PRD 14.1).

**이름이 `config.yaml`이 아닌 이유는 이것이 입력이 아니라 기록이기 때문이다.** 같은 이름을
쓰면 run 디렉터리에서 CLI를 돌린 사람이 그것을 설정 파일로 집어 들게 되고, 그때 값은 맞지만
"이 파일을 고치면 그 run이 다시 그 값으로 돈다"는 오해가 따라온다 — 다시 도는 것은 재생성
(#77)이고 그쪽은 이 파일을 **읽기만** 한다.

파일명이 여기 있는 것은 형식과 계약을 이 모듈이 소유하기 때문이다 (`SPEC`). `schemas/`에
스키마를 하나 더 두면 같은 표가 두 곳에 생긴다.
"""


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
    choices: Callable[[], Sequence[str]] | None = None
    """허용 값 목록을 돌려주는 호출체. 값이 아니라 호출체인 이유는 번들 프리셋 이름이
    `assets/`의 파일에서 오기 때문이다 — import 시점에 파일을 열지 않고 검증할 때 읽는다.
    `schemas.core`의 `choices_from`이 같은 이유로 같은 모양이다 (#38)."""


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
        # LLM보다 훨씬 짧다. 문장당 0.3~1.4초로 측정됐고(스파이크 #2 §4.5), 60초를
        # 넘겼다면 기다릴 것이 아니라 네트워크나 엔드포인트를 의심할 상황이다.
        "timeout_sec": Setting(60, "int"),
        "max_retries": Setting(2, "int"),
        # 세그먼트 캐시. **run 디렉터리 밖이다** — run마다 새 디렉터리이므로 안에 두면
        # 반복 실행 비용이 줄지 않는다 (PRD 13). null이면 캐시를 끈다.
        # 재합성이 결정적이라 적중과 미적중이 다른 결과를 내지 않는다 (스파이크 #2 §4.2).
        "cache_dir": Setting(".cache/tts", "str", nullable=True),
        # `tts.providers` 섹션은 없다. edge_tts가 여는 키가 없어 빈 섹션을 미리 만들면
        # "알 수 없는 설정 키" 검출과 충돌한다 — 키를 여는 adapter가 추가될 때 만든다.
    },
    # 원문·링크 입력 (#94, PRD 7.1). `--topic` 실행은 이 섹션을 읽지 않는다.
    "source": {
        # 원문 글자 수 상한. 넘으면 **run 디렉터리를 만들기 전에** 멈춘다.
        # 상한의 목적은 프롬프트 예산이 아니라 사고 방지다 — 실수로 로그 파일이나 덤프를
        # 지정하면 요약(#32)이 그 전체를 모델에 실어 보낸다. 20만 자는 실측에서 가장 긴
        # 정상 추출(위키백과 장문 80,787자, 스파이크 #31)의 두 배 남짓이다.
        "max_chars": Setting(200_000, "int"),
        # 첫 줄을 제목으로 쓸 때의 상한. 이 값이 콘텐츠 생성기의 `topic` 자리로 가므로
        # (원문 입력에서는 본문이 프롬프트로 가지 않는다, PRD 14.1) 문단 하나가 통째로
        # 주제가 되는 것을 막는다. 넘긴 줄은 잘라 쓰고 원문은 `source.json`에 남는다.
        "title_max_len": Setting(80, "int"),
    },
    # 낭독 장면의 duration 확정 공식에 쓰는 값 (PRD 7.5.1).
    "timing": {
        "lead_in_sec": Setting(0.30, "float"),
        "tail_sec": Setting(0.50, "float"),
        "min_duration_sec": Setting(1.20, "float"),
        # 자막 읽기 하한의 두 값. `caption`이 있는 장면에만 걸린다 (#16).
        # `caption_onset_sec`는 하한 계산과 렌더러(#22)가 **같은 값을 읽는다** — 화면에
        # 자막이 뜨는 시각이 하한의 시작점이므로, 갈리면 표시 시간이 계산보다 짧아진다.
        "caption_onset_sec": Setting(0.90, "float"),
        "reading_cps": Setting(12.0, "float"),
    },
    # 트랙 레벨 (#23). `render`가 아니라 여기 있는 이유는 값의 성격이 오디오이기 때문이고,
    # 배경음악 볼륨(#35)도 이 섹션에 온다.
    "audio": {
        # 효과음의 선형 게인. **0이면 효과음이 없는 결과가 나온다.** 기본값 1.0에서 번들
        # 효과음이 낭독보다 peak 9.5dB / RMS 9~10dB 아래이므로(#18의 정규화, 실측은 #23)
        # 추가 감쇠가 필요하지 않다. 올려도 최종 오디오는 리미터가 -1 dBFS에서 잡는다
        # (`audio_mix.LIMITER_CEILING`) — 상한을 여기 두지 않는 것은 그 안전망이 있기
        # 때문이고, 음수만 렌더가 거부한다.
        "sfx_volume": Setting(1.0, "float"),
        # 낭독의 선형 게인 (#81). **여기도 상한이 없다** — 앱의 슬라이더가 100을 넘겨도
        # 최종 오디오는 리미터가 잡고(`audio_mix.LIMITER_CEILING`), 낭독 하나뿐인 체인에서도
        # 게인이 1을 넘으면 리미터가 붙는다. 0이면 낭독이 들리지 않는 결과가 나온다 —
        # `sfx_volume: 0`이 효과음을 아예 만들지 않는 것과 달리 트랙은 그대로 흐른다.
        "voice_volume": Setting(1.0, "float"),
    },
    # `captions.srt`의 줄바꿈 (#17). **번인 줄바꿈과 다른 층이다** — D1 확정 스펙 5장은
    # 줄당 글자 수를 요소별로 정하고(폰트 크기가 달라서 갈린다), 렌더러(#20)가
    # `floor(840 / font_size)`로 자기 몫을 계산한다. 외부 재생기가 여는 SRT에는 폰트
    # 크기라는 개념이 없으므로 트랙 전체에 하나의 값을 쓴다.
    "captions": {
        "max_chars_per_line": Setting(18, "int"),
        # 넘으면 자르지 않고 경고한다 (#17). 원문을 잃는 쪽이 더 나쁘다.
        "max_lines": Setting(4, "int"),
    },
    "quiz": {
        # 허용 범위(퀴즈 스펙 0장의 3~5)는 여기서 강제하지 않는다. 범위를 아는 것은
        # 퀴즈 타입이고, 타입이 자기 설정을 확인하는 자리가 `ShortsType.check_config`다.
        "question_count": Setting(4, "int"),
        # 3은 시안의 3 → 2 → 1 배치와 대응한다 (D1 확정 스펙 3장). **설정값 그대로
        # 남는다** — 4로 올리면 렌더러가 `seconds` 값만큼 세므로 4자리가 나온다.
        "countdown_sec": Setting(3, "int"),
        # 생성 텍스트의 글자 수 상한. 화면에 들어가는 글자 수 문제이므로 모델이 아니라
        # 여기서 정하고, 생성기가 JSON Schema와 프롬프트 양쪽에 실어 보낸다 (#9, #56).
        # 키 이름이 `quiz.<필드>_max_len`인 것은 규약이다 — 생성기가 필드 이름에서 키를
        # 만든다 (`quiz_generator.CAPPED_FIELDS`).
        #
        # 다섯 값 전부 D1 확정 스펙의 레이아웃에서 나왔다 — 그 요소의 **가장 작은 폰트
        # 티어가 담는 최대 글자 수**다. 넘으면 텍스트가 안전 영역을 벗어나므로 렌더에서
        # 자르는 대신 생성 단계에서 막는다 (#56).
        "hook_max_len": Setting(36, "int"),  # 64px 3줄 (확정 스펙 5.1)
        "question_max_len": Setting(45, "int"),  # 56px 3줄 = floor(840/56)×3 (5.2)
        "answer_max_len": Setting(20, "int"),  # T2 2줄 (5.4)
        "cta_max_len": Setting(24, "int"),  # 68px 2줄 (5.5)
        "explanation_max_len": Setting(60, "int"),  # 36px 3줄 안 (5.4)
    },
    # 업로드용 메타데이터 (#13). 상한이 config에 있는 이유는 YouTube의 하드 리밋이 아니라
    # 운영 기준이기 때문이다 — 제목 100자는 허용되지만 모바일 검색 결과에서 잘린다.
    "metadata": {
        "title_max_len": Setting(40, "int"),
        "tag_max_count": Setting(10, "int"),
    },
    "render": {
        # null이면 번들 폰트(`assets/fonts/`, #38)를 쓴다. 사용자 지정 폰트와 번들 사이의
        # 선택 로직은 렌더러(#20)가 소유한다 — 여기는 값만 받는다.
        "font_path": Setting(None, "str", nullable=True),
        # 이름은 `assets/backgrounds/presets.json`이 정한다 (#38). 장면 템플릿(#12)은 이 값을
        # 읽지 않는다 — 배경은 장면별 값이 아니라 `project.json`의 필드다.
        # 기본값은 D1 확정 스펙 6.3에서 기본 자막 스타일과 ◎로 짝지어진 배경이다.
        "background": Setting("deep_navy", "str", choices=background_preset_names),
        # 자막 스타일 프리셋 이름 (`assets/caption-styles/presets.json`, #38). 프리셋이
        # 바꾸는 것은 색과 그림자뿐이고 레이아웃은 3종이 공통이다 (확정 스펙 6.1).
        # 앱에서 스타일을 바꾸는 경로는 `project.json`의 편집 상태이고 #26/#29가 붙인다.
        "caption_style": Setting("impact_yellow", "str", choices=caption_style_names),
        # CTA 화면의 고정 두 줄 (D1 확정 스펙 5.5). **채널 브랜딩이라 config에 있다** —
        # 타입 전용 정보가 아니므로 `scenes.json`에도 `quiz.json`에도 넣지 않고, 채널마다
        # 바꾸는 값을 LLM이 매번 새로 짓게 하지도 않는다. LLM이 짓는 줄은 `scenes[-1].text`
        # 하나다. 줄당 글자 수 상한(punch 9자 / tail 21자) 확인은 렌더러(#20)가 한다.
        "cta_punch": Setting("구독 · 좋아요", "str"),
        "cta_tail": Setting("매일 새 상식 퀴즈", "str"),
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


_RECORD_HEADER = f"""\
# 이 run이 실제로 쓴 설정 전체다. 기록이지 입력이 아니다 — 여기를 고쳐도 이 run이 다시
# 돌지 않는다. 앱의 재생성은 이 값으로 돌고, 그래서 실행 디렉터리의
# {DEFAULT_CONFIG_FILENAME}을 다시 찾지 않는다. 그대로 복사해 --config로 넘길 수 있다.
"""


def serialize_config(config: Config) -> str:
    """설정 기록의 파일 표현 (#92).

    **`config.yaml`과 같은 모양의 YAML이다.** 그래야 `load_config`의 검증(`SPEC`)을 그대로
    지나고, 사람이 복사해 `--config`로 다시 돌릴 수 있다.

    세 인자가 모두 함정을 막는다.

    - `allow_unicode`가 없으면 한국어 값(`cta_punch`)이 `\\uXXXX`로 나간다. 값은 살아 있지만
      diff로 읽을 수 없으므로 기록의 목적이 사라진다.
    - `sort_keys`를 두면 `SPEC`의 정의 순서가 알파벳 순으로 흐트러진다. 이 파일을
      `config.example.yaml`과 나란히 놓고 읽는 것이 사용 방식이다.
    - `width`를 넓히지 않으면 긴 문자열이 여러 줄로 접힌다. 접힌 스칼라도 같은 값으로 다시
      읽히지만, 값 하나가 길어질 때마다 diff에 관계없는 줄이 생긴다.
    """
    body = yaml.safe_dump(
        config.data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=4096,
    )
    return _RECORD_HEADER + body


def load_run_config(run_dir: Path) -> Config:
    """run 디렉터리에 기록된 그 실행의 설정을 읽는다 (#92).

    **현재 작업 디렉터리를 보지 않는다.** `load_config()`를 인자 없이 부르면 cwd의
    `config.yaml`을 찾는데, 그 파일은 생성 이후에 바뀌었을 수도 있고 앱 백엔드에서는 cwd를
    앱이 정한다 — 재생성이 생성 때와 다른 값으로 도는 경로가 그것이다 (PRD 14.1).

    Raises:
        ConfigError: 기록이 없거나(이 파일이 생기기 전에 만들어진 run 디렉터리다) 계약을
            어겼을 때.
    """
    path = run_dir / RUN_CONFIG_FILENAME
    if not path.is_file():
        raise ConfigError(
            [
                f"{run_dir}에 {RUN_CONFIG_FILENAME}이 없다 — 그 실행이 어떤 설정으로 "
                f"돌았는지 알 수 없다.",
                f"{RUN_CONFIG_FILENAME}이 생기기 전에 만들어진 run 디렉터리라면 "
                f"CLI로 다시 생성해야 한다.",
            ]
        )
    return load_config(path)


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
        else:
            violation = _off_the_list(value, child_spec)
            if violation is not None:
                errors.append(f"{child_path}: {violation}")


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


def _off_the_list(value: Any, setting: Setting) -> str | None:
    """`choices`가 있으면 목록 안의 값인지 본다. 위반 문구를 돌려주고 없으면 `None`.

    **목록을 읽다 실패한 것도 설정 오류로 보고한다.** 번들 프리셋 파일이 깨져 있으면
    `AssetError`가 나는데, 그것을 그대로 올려보내면 `load_config`가 `ConfigError` 하나만
    던진다는 계약이 깨지고 `main`이 스택트레이스를 낸다.
    """
    if setting.choices is None or value is None:
        return None

    try:
        allowed = tuple(setting.choices())
    except AssetError as error:
        return f"허용 값 목록을 읽을 수 없다 — {error}"

    if value in allowed:
        return None
    return f"목록에 없는 이름이다: {value!r}. 쓸 수 있는 이름: {', '.join(allowed)}"


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
