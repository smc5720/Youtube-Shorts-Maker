"""주제 → `quiz.json` 내용 (퀴즈 스펙 4장, 이슈 #9).

세 가지가 이 모듈의 형태를 정한다.

- **LLM 호출은 문제 수와 무관하게 1회다.** claude CLI는 호출당 약 6.5초의 프로세스 기동
  오버헤드를 가진다 (스파이크 3장). 문제별로 나눠 부르면 이 고정비가 문제 수만큼 곱해지고,
  얻는 것은 없다 — 문제 세트는 서로 겹치지 않아야 하므로 오히려 한 번에 보는 편이 낫다.
- **모델에게는 모델만 아는 것을 묻는다.** `schema_version`·`type`·`category`·`language`·
  `id`·`countdown_sec`은 코드가 정한다. 넘기는 JSON Schema는 `schemas.quiz`가 산출물
  스키마에서 파생시키므로 필드 이름이 여기 다시 적히지 않는다.
- **난이도 오름차순을 두 겹으로 보장한다.** 프롬프트로 요구하고, 받은 뒤 다시 정렬한다.
  스키마는 이 순서를 강제하지 않으므로(정렬은 값 사이의 관계다) 모델이 어긋나게 내도
  검증에서 걸리지 않는다. 순서가 무너지면 이탈 방지 배치(퀴즈 스펙 2장)가 깨진다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ...config import ConfigError
from ...llm import LLMError, provider_for_role
from ...schemas.quiz import (
    DIFFICULTIES,
    SCHEMA_VERSION,
    TYPE,
    content_json_schema,
    validate_quiz,
)
from .quiz_verifier import MIN_RUNS, verify

if TYPE_CHECKING:
    from ...config import Config

MIN_QUESTIONS = 3
MAX_QUESTIONS = 5
"""한 영상에 담는 문제 수의 허용 범위 (퀴즈 스펙 0장).

`config.SPEC`이 아니라 여기 있다. 설정 로더는 타입별 허용 범위를 알 수 없고, 이 값은
길이 가이드(3문제 ≈ 38초 / 5문제 ≈ 58초)와 PRD 6.3의 45~60초에서 나온 퀴즈의 규칙이다.
"""

CAPPED_FIELDS = ("hook", "cta", "question", "answer", "explanation")
"""글자 수 상한이 걸리는 생성 필드. 상한값은 `quiz.<필드>_max_len`에서 온다 (#9, #56).

**이름에서 config 키를 만든다.** 필드마다 키 이름을 따로 적으면 둘이 갈릴 수 있고, 갈리면
스키마에 얹히는 상한과 사람이 설정한 값이 달라진다. 상한이 없는 필드(`difficulty`)는
여기 없다.

전부 D1 확정 스펙의 레이아웃 티어에서 나온 값이라 **넘으면 렌더가 아니라 생성이 멈춘다** —
렌더 단계에서 자르면 화면에서 말이 안 되는 문장이 남고 사람이 검수할 원문이 사라진다.
"""

CATEGORY = "general_knowledge"
"""서브 장르 (퀴즈 스펙 0장). MVP는 상식/지식 퀴즈 하나뿐이라 config 키를 열지 않는다."""

LANGUAGE = "ko"
"""한국어 1차 타깃 (PRD 14.1). 확장 여지는 `quiz.json`의 `language` 필드가 담는다."""

SYSTEM = (
    "너는 한국어 상식 퀴즈 출제기다. "
    "쇼츠 영상용으로 짧고 명확한 주관식 문제를 만든다. "
    "정답은 논란의 여지가 없는 단일 값이어야 한다. "
    "JSON 외에는 아무것도 출력하지 않는다."
)
"""스파이크에서 실측한 시스템 프롬프트 (`docs/spikes/1-llm-provider/harness.py`)."""


def check_config(config: Config) -> None:
    """퀴즈 타입이 요구하는 설정 조건을 확인한다. 타입 선언이 파이프라인에 노출한다.

    run 디렉터리를 만들기 전에 돈다 — 값 하나 때문에 빈 run 디렉터리가 쌓이면 검수할
    산출물과 구분되지 않는다.

    `llm.verifier.runs`는 `llm` 아래 키지만 여기서 본다. 재답변 횟수의 하한을 아는 것은
    사실 검증을 필수로 두는 퀴즈 타입이고(퀴즈 스펙 5장), 설정 로더는 그 요구를 모른다 —
    문제 수 범위를 `config.SPEC`이 아니라 여기서 보는 것과 같은 이유다.

    Raises:
        ConfigError: 문제 수나 재답변 횟수가 허용 범위 밖일 때.
    """
    errors = []

    count = config.get("quiz.question_count")
    if not MIN_QUESTIONS <= count <= MAX_QUESTIONS:
        errors.append(
            f"quiz.question_count: {MIN_QUESTIONS}~{MAX_QUESTIONS} 사이여야 한다. "
            f"받은 값: {count}"
        )

    runs = config.get("llm.verifier.runs")
    if runs < MIN_RUNS:
        errors.append(
            f"llm.verifier.runs: {MIN_RUNS} 이상이어야 한다. 재답변이 한 번도 없으면 "
            f"검증 단계가 이름만 남는다. 받은 값: {runs}"
        )

    if errors:
        raise ConfigError(errors)


def length_caps(config: Config) -> dict[str, int]:
    """필드 이름 → 글자 수 상한. 스키마·프롬프트·사후 확인이 같은 값을 본다 (#56)."""
    return {field: config.get(f"quiz.{field}_max_len") for field in CAPPED_FIELDS}


def build_prompt(*, topic: str, question_count: int, caps: Mapping[str, int]) -> str:
    """생성 프롬프트. 스파이크의 프롬프트에 주제와 다양성 요구를 더한 것이다.

    다양성 요구는 스파이크 4.1의 후속 과제다 — `sonnet`이 동일 프롬프트 3회에서 같은
    문제를 반복 출제했고, 그것이 프롬프트로 완화되는 성질인지가 확인 대상이다.

    **글자 수 상한을 스키마와 여기 양쪽에 싣는다.** 스키마의 `maxLength`만으로는 모델이
    상한을 모르는 채로 쓰다가 끝에서 끊기고, 그러면 문장이 어색하게 잘린 채 상한 안에
    들어온다 — 검증을 통과하므로 아무도 못 잡는다 (#56).
    """
    return (
        f"주제: {topic}\n"
        f"\n"
        f"위 주제로 한국인 일반 시청자 대상 상식 퀴즈 {question_count}문제를 만들어라.\n"
        f"- 문제끼리 소재가 겹치지 않게 하고, 주제 안에서 서로 다른 갈래를 골라라.\n"
        f"- 난이도를 easy → medium → hard 오름차순으로 배치하라. "
        f"첫 문제는 easy, 마지막 문제는 hard로 한다.\n"
        f"- 교과서 대표 예시로 굳어진 소재(예: 세종대왕의 한글 창제, 물의 화학식)는 피하라. "
        f"같은 주제로 다시 실행했을 때 다른 문제가 나와야 한다.\n"
        f"- 질문은 {caps['question']}자, 정답은 {caps['answer']}자, "
        f"해설은 {caps['explanation']}자를 넘기지 마라. "
        f"상한에 맞춰 끊지 말고 그 안에서 끝나는 문장으로 써라.\n"
        f"- 정답이 여러 개로 갈릴 수 있는 문제는 내지 마라. "
        f"'최초', '최대'처럼 기준에 따라 답이 달라지는 표현은 조건을 질문에 명시하라.\n"
        f"- hook은 시청을 붙잡는 첫 문장으로 {caps['hook']}자, "
        f"cta는 댓글·구독을 유도하는 마지막 문장으로 {caps['cta']}자를 넘기지 마라.\n"
    )


def generate(*, topic: str, config: Config) -> dict[str, Any]:
    """주제에서 퀴즈 문제 세트를 만든다. 각 문제의 `verify`까지 채워서 돌려준다.

    **블라인드 검증(#10)은 별도의 플러그인 축이 아니라 여기 안의 한 단계다.** 레지스트리가
    아는 교체 가능한 축은 생성기와 장면 템플릿 둘뿐이고(#8), 검증은 퀴즈 타입이 자기
    산출물에 대해 하는 일이다. 검증기를 세 번째 축으로 올리면 "검증 없는 타입"을 위해
    레지스트리가 선택 축을 하나 더 들고 다녀야 한다.

    Raises:
        ConfigError: 설정이 퀴즈 타입의 요구를 만족하지 않을 때.
        LLMError: 모델이 요구를 만족하는 출력을 내지 못했을 때 (재시도 후에도).
        SchemaError: 조립한 결과가 `quiz.json` 스키마를 만족하지 않을 때.
    """
    # 파이프라인이 이미 불렀더라도 다시 확인한다. 이 함수는 CLI 말고도 앱 백엔드와
    # 테스트가 직접 부르는 입구이고, 범위를 벗어난 값으로 LLM을 부르면 그 비용이 버려진다.
    check_config(config)

    question_count = config.get("quiz.question_count")
    caps = length_caps(config)

    generator = provider_for_role("generator", config=config)
    result = generator.complete_json(
        system=SYSTEM,
        prompt=build_prompt(topic=topic, question_count=question_count, caps=caps),
        schema=content_json_schema(question_count=question_count, caps=caps),
    )

    content = _assemble(result.data, config=config)
    _check_lengths(content, caps=caps)
    # 초안을 먼저 검증한다. 모양이 깨진 초안을 그대로 검증기에 넘기면 재답변·모호성 프로브
    # 호출 비용을 쓰고 나서 같은 이유로 실패한다. 이 시점의 초안에는 `verify`가 없고,
    # 스키마가 그것을 선택 필드로 둔 이유가 이 호출이다.
    validate_quiz(content)

    verify(content, config=config)
    # 검증기가 채운 값도 계약을 지켜야 한다. 순수 파이썬 검사라 비용이 없고, 상태·확신도가
    # 스키마를 벗어나면 `quiz.json`을 쓰기 전에 여기서 걸린다.
    validate_quiz(content)
    return content


def _assemble(data: dict[str, Any], *, config: Config) -> dict[str, Any]:
    """모델이 낸 부분 결과에 코드가 정하는 필드를 채워 `quiz.json` 내용을 만든다."""
    countdown_sec = config.get("quiz.countdown_sec")

    questions = [
        {
            "id": index,
            **question,
            # 난이도별 조정은 스펙 2장이 남긴 여지이지 MVP 요구가 아니다. 균일하게 채운다.
            "countdown_sec": countdown_sec,
        }
        # `sorted`는 안정 정렬이므로 같은 난이도 안에서는 모델이 낸 순서가 유지된다.
        for index, question in enumerate(_by_difficulty(data["questions"]), start=1)
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "type": TYPE,
        "category": CATEGORY,
        "language": LANGUAGE,
        "hook": data["hook"],
        "cta": data["cta"],
        "questions": questions,
    }


def _by_difficulty(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """난이도 오름차순. 순서는 스키마의 `DIFFICULTIES` 선언에서 나온다."""
    return sorted(questions, key=lambda question: DIFFICULTIES.index(question["difficulty"]))


def _check_lengths(content: dict[str, Any], *, caps: Mapping[str, int]) -> None:
    """길이 상한을 넘겼는지 확인한다.

    **재생성하지 않고 멈춘다.** 상한은 JSON Schema로도 넘어가 CLI가 강제하므로(스파이크
    4.3) 여기까지 온 초과는 모델이 못 맞춘 것이 아니라 상한 자체가 그 주제에 맞지
    않는다는 신호에 가깝다. 다시 부르면 같은 이유로 같은 결과가 나오고 호출 비용만
    늘어난다 — `llm.max_retries`가 타임아웃을 재시도하지 않는 것과 같은 판단이다.
    """
    # 어느 필드가 어느 층에 있는지는 `CAPPED_FIELDS`에서 읽어 낸다. 여기 목록을 따로
    # 적으면 상한을 하나 추가할 때 고칠 곳이 늘고, 빠뜨린 필드는 조용히 통과한다.
    targets: list[tuple[str, str, str]] = [
        (field, field, content[field]) for field in CAPPED_FIELDS if field in content
    ]
    targets += [
        (f"questions[{index}].{field}", field, question[field])
        for index, question in enumerate(content["questions"])
        for field in CAPPED_FIELDS
        if field in question
    ]

    violations = [
        f"{path}: {caps[field]}자 이하여야 한다 (quiz.{field}_max_len). "
        f"받은 값: {len(value)}자 — {value!r}"
        for path, field, value in targets
        if len(value) > caps[field]
    ]
    if violations:
        # 호출자(공통 파이프라인)는 퀴즈 타입을 모르므로 타입 전용 예외를 만들지 않는다.
        raise LLMError(
            "생성된 문제가 길이 상한을 넘었다. 상한을 올리거나 주제를 좁힌다:\n"
            + "\n".join(violations),
            retryable=False,
        )
