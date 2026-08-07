"""`scenes.json` → `metadata.json` (PRD 7.8, 이슈 #13).

업로드는 사람이 직접 한다 (PRD 7.8). 이 모듈이 만드는 것은 업로드 폼에 붙여넣을 제목 후보
3개·설명·태그이고, 여기서 네트워크로 나가는 것은 LLM provider 호출뿐이다.

세 가지가 이 모듈의 형태를 정한다.

- **`scenes.json`만 읽는다.** "퀴즈 콘텐츠 기반으로 제목을 만든다"를 문자 그대로 구현해
  `quiz.json`을 열면 경계 위반이고 `tests/test_type_boundary.py`가 깨진다 (퀴즈 스펙 1.1).
  제목의 재료는 장면의 `role` / `text` / `caption`뿐이며, 후킹 문장과 질문·정답·해설이
  이미 거기 옮겨져 있으므로 충분하다. **이 모듈은 장면이 퀴즈에서 나왔다는 사실을 모른다** —
  두 번째 타입이 추가돼도 그대로 쓰인다.
- **입력은 초안이어도 된다.** 확정 상태(`validate_scenes_final`)를 요구하지 않는다.
  메타데이터는 오디오 길이와 무관하고, 렌더가 실패해도 남아야 하는 산출물이기 때문이다
  (PRD 6.2 표: 항상 생성).
- **LLM 호출은 1회다.** claude CLI는 호출당 약 6.5초의 프로세스 기동 오버헤드를 가진다
  (스파이크 3장). 제목·설명·태그를 나눠 부를 이유가 없다 — 셋은 같은 재료에서 나오고
  서로 톤이 맞아야 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .llm import LLMError, provider_for_role
from .schemas.metadata import (
    SCHEMA_VERSION,
    TITLE_COUNT,
    content_json_schema,
    validate_metadata,
)
from .schemas.scenes import validate_scenes

if TYPE_CHECKING:
    from .config import Config

LANGUAGE = "ko"
"""한국어 1차 타깃 (PRD 14.1). 확장 여지는 `metadata.json`의 `language` 필드가 담는다.

`scenes.json`에는 언어 필드가 없다. 콘텐츠 산출물(`quiz.json`)이 그 값을 들고 있지만 이
모듈은 그 파일을 열 수 없으므로, 언어가 실제로 갈리는 시점에 장면 스키마에 필드를 열거나
config 키를 여는 쪽이 맞다 — 지금 우회 경로를 만들면 경계가 무너진다.
"""

SYSTEM = (
    "너는 한국어 YouTube Shorts 메타데이터 작성기다. "
    "장면 목록을 읽고 클릭을 유도하되 내용과 어긋나지 않는 제목·설명·태그를 만든다. "
    "낚시성 과장이나 영상에 없는 내용을 넣지 않는다. "
    "JSON 외에는 아무것도 출력하지 않는다."
)


def build_prompt(*, scene_lines: str, title_max_len: int, tag_max_count: int) -> str:
    """생성 프롬프트. 재료는 장면의 `role`·`text`·`caption`뿐이다."""
    return (
        f"아래는 만들어질 쇼츠 영상의 장면 목록이다.\n"
        f"\n"
        f"{scene_lines}\n"
        f"\n"
        f"이 영상의 업로드용 메타데이터를 만들어라.\n"
        f"- 제목 후보 {TITLE_COUNT}개를 서로 다른 각도로 만들어라. "
        f"각각 {title_max_len}자 이내이고, 앞쪽 15자만 보여도 무엇에 대한 영상인지 알 수 있어야 한다.\n"
        f"- 설명은 2~3문장으로 쓰고, 영상에 나오지 않는 정보를 넣지 마라.\n"
        f"- 태그는 {tag_max_count}개 이하로, 검색어로 실제 쓰일 법한 한국어 단어를 골라라. "
        f"'#'을 붙이지 마라.\n"
    )


def generate(scenes: Mapping[str, Any], *, config: Config) -> dict[str, Any]:
    """장면 목록에서 `metadata.json` 내용을 만든다.

    Args:
        scenes: `scenes.json` 내용. 초안 상태여도 된다.

    Raises:
        LLMError: 모델이 요구를 만족하는 출력을 내지 못했을 때 (재시도 후에도).
        SchemaError: 입력 장면이나 조립한 결과가 스키마를 만족하지 않을 때.
    """
    # 입력을 검증한다. 이 함수는 CLI 말고도 앱 백엔드와 테스트가 직접 부르는 입구이고,
    # 깨진 장면 목록으로 LLM을 부르면 그 비용이 버려진다.
    validate_scenes(scenes)

    title_max_len = config.get("metadata.title_max_len")
    tag_max_count = config.get("metadata.tag_max_count")

    generator = provider_for_role("generator", config=config)
    result = generator.complete_json(
        system=SYSTEM,
        prompt=build_prompt(
            scene_lines=describe_scenes(scenes),
            title_max_len=title_max_len,
            tag_max_count=tag_max_count,
        ),
        schema=content_json_schema(
            title_max_len=title_max_len, tag_max_count=tag_max_count
        ),
    )

    content = _assemble(result.data, scenes=scenes)
    _check_limits(content, title_max_len=title_max_len, tag_max_count=tag_max_count)
    validate_metadata(content)
    return content


def describe_scenes(scenes: Mapping[str, Any]) -> str:
    """장면 목록을 프롬프트에 실을 줄 목록으로 옮긴다.

    **`role`을 그대로 쓴다.** 역할별 설명문("이것은 질문 장면이다")을 붙이면 이 모듈이
    타입의 구성 방식을 아는 셈이 되고, 새 `role`이 생길 때마다 여기를 고쳐야 한다.
    문구가 없는 장면(`countdown`)은 재료가 없으므로 빠진다.
    """
    lines = []
    for scene in scenes["scenes"]:
        parts = [part for part in (scene.get("text"), scene.get("caption")) if part]
        if parts:
            lines.append(f"[{scene['role']}] {' — '.join(parts)}")
    return "\n".join(lines)


def _assemble(data: Mapping[str, Any], *, scenes: Mapping[str, Any]) -> dict[str, Any]:
    """모델이 낸 부분 결과에 코드가 정하는 필드를 채워 `metadata.json` 내용을 만든다."""
    return {
        "schema_version": SCHEMA_VERSION,
        # 장면이 들고 있는 값을 그대로 옮긴다. 이 모듈이 타입 이름을 아는 것이 아니다.
        "type": scenes["type"],
        "language": LANGUAGE,
        "titles": data["titles"],
        "description": data["description"],
        "tags": data["tags"],
        # `--topic` 경로에는 출처가 없다. `--url` / `--text-file` 경로에서 값을 채우는
        # 것은 #31이며, 그때까지 `null`은 "빠뜨림"이 아니라 "출처 없음"이다.
        "source": None,
    }


def _check_limits(
    content: Mapping[str, Any], *, title_max_len: int, tag_max_count: int
) -> None:
    """config가 정한 상한을 넘겼는지 확인한다.

    **재생성하지 않고 멈춘다.** 상한은 JSON Schema로도 넘어가 CLI가 강제하므로(스파이크
    4.3) 여기까지 온 초과는 모델이 못 맞춘 것이 아니라 상한 자체가 그 콘텐츠에 맞지
    않는다는 신호에 가깝다 — `quiz_generator._check_lengths`와 같은 판단이다.
    """
    violations = [
        f"titles[{index}]: {title_max_len}자 이하여야 한다 (metadata.title_max_len). "
        f"받은 값: {len(title)}자 — {title!r}"
        for index, title in enumerate(content["titles"])
        if len(title) > title_max_len
    ]
    tags = content["tags"]
    if len(tags) > tag_max_count:
        violations.append(
            f"tags: {tag_max_count}개 이하여야 한다 (metadata.tag_max_count). "
            f"받은 값: {len(tags)}개"
        )

    if violations:
        raise LLMError(
            "생성된 메타데이터가 상한을 넘었다. 상한을 올리거나 다시 실행한다:\n"
            + "\n".join(violations),
            retryable=False,
        )
