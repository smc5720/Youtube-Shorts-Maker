"""확정 `scenes.json` → `captions.srt` (PRD 7.6, 이슈 #17).

**입력은 확정 상태 하나다.** 초안의 목표치로 타임코드를 계산하는 경로가 있으면 자막이
음성과 어긋나고, 어긋난 정도가 문장마다 달라 후처리로 보정할 수도 없다 (PRD 14.1,
퀴즈 스펙 4장). 그래서 입구에서 `validate_scenes_final()`을 부른다.

- **이 단계도 타입을 모른다.** 재료는 `text`·`caption`이고 분기 조건은 `narrate` 플래그
  뿐이다 (PRD 7.4.1). `role`로 갈라지는 규칙이 없다.
- **큐는 장면당 하나다.** 정답 장면에는 `text`(정답)와 `caption`(해설)이 둘 다 있고 화면
  에서는 함께 뜨지만(D1 확정 스펙 5.4), SRT는 단일 트랙이라 두 큐를 겹칠 수 없다. 한 큐
  안에 두 문구를 담아야 "큐가 겹치지 않는다"와 "해설이 정답 장면 구간에 들어간다"가 동시에
  성립하고, 번인(#20) 결과와 SRT 내용이 어긋나지 않는다.
- **낭독 장면의 큐는 `narration_offset`에서 연다.** 장면 시작이 아니다 — `lead_in`은 말이
  시작되기 전의 여백이다 (PRD 7.5.1). 낭독이 없는 장면은 구간 전체를 덮는다 (PRD 7.6).
- **줄 수 상한을 넘으면 자르지 않고 경고한다.** 원문을 잃는 쪽이 더 나쁘다.

`captions.max_chars_per_line`은 **이 SRT 트랙의 값이고 번인 줄바꿈과 다른 층이다.** D1
확정 스펙은 줄당 글자 수를 요소별로 정한다(질문 13~15자, 정답 10자, 해설 23자 — 5장). 그
값들은 `drawtext` 인스턴스마다 폰트 크기가 다르기 때문에 갈리는 것이고, 렌더러(#20)가
`floor(840 / font_size)`로 자기 몫을 계산한다. 외부 재생기가 여는 SRT에는 폰트 크기라는
개념이 없으므로 트랙 전체에 하나의 값을 쓴다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import PACKAGE_LOGGER
from .schemas.scenes import DURATION_DIGITS, validate_scenes_final

if TYPE_CHECKING:
    from .config import Config

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.captions")

CAPTIONS_NAME = "captions.srt"
"""산출물 파일명 (PRD 6.2). JSON 산출물과 달리 스키마가 없으므로 여기가 단일 진실
공급원이고, 자막을 읽는 쪽(#20, 앱 #29)이 이 이름을 참조한다."""


class CaptionError(Exception):
    """자막을 만들 수 없다."""


@dataclass(frozen=True)
class Cue:
    """SRT 큐 하나. 시각은 영상 시작 기준 초다."""

    start_sec: float
    end_sec: float
    lines: tuple[str, ...]


def build(scenes: Mapping[str, Any], *, config: Config) -> list[Cue]:
    """확정 장면 목록에서 큐 목록을 만든다.

    직렬화(`render`)와 나눠 둔 이유는 큐가 SRT 전용 산물이 아니기 때문이다 — 번인(#20)과
    앱의 자막 편집(#29)이 필요로 하는 것은 텍스트가 아니라 이 목록이고, ASS 지원(#33)이
    붙어도 갈라지는 것은 직렬화 쪽뿐이다.

    Args:
        scenes: `scenes.json` 내용. **확정 상태여야 한다.**
        config: `captions.*` 값을 읽는다.

    Raises:
        CaptionError: 설정값이 자막을 만들 수 없는 값일 때.
        SchemaError: 입력이 확정 상태가 아닐 때 — 초안으로는 타임코드를 계산하지 않는다.
    """
    # 입구에서 확정 검증을 한다. 이 함수는 CLI 말고도 앱 백엔드와 테스트가 직접 부르고,
    # 초안이 들어오면 `duration`이 없어 타임코드가 아예 서지 않는다.
    validate_scenes_final(scenes)
    return build_applied(scenes, config=config)


def build_applied(scenes: Mapping[str, Any], *, config: Config) -> list[Cue]:
    """**확정 검증을 지난 목록에 사람의 편집을 얹은 사본**에서 큐를 만든다 (#77).

    `build`와 갈라 둔 이유는 그 사본이 확정 검증을 통과하지 않기 때문이다 — 사람이 얹은
    길이는 낭독보다 짧을 수 있고(그래서 `project.json`에 산다) 장면 사본에는 스키마가 모르는
    오버레이 키가 붙어 있다 (`video_renderer.apply_scene_overrides`). 검증을 그대로 두면
    **사람이 줄인 길이에서 자막 생성이 실패한다.**

    **초안을 넣는 입구가 아니다.** 여기 오는 목록은 이미 확정 검증을 지난 `scenes.json`에서
    파생된 것이어야 하고, 그 검증은 재생성이 `scenes.json`을 쓰기 전에 자기 자리에서 한다.

    Raises:
        CaptionError: 설정값이 자막을 만들 수 없는 값일 때.
    """
    max_chars = int(config.get("captions.max_chars_per_line"))
    if max_chars < 1:
        # 1 미만이면 어절을 자를 조각이 없어 줄바꿈이 끝나지 않는다. 설정 로더는 범위를
        # 모르므로(config.py) 값을 실제로 쓰는 쪽이 본다.
        raise CaptionError(
            f"captions.max_chars_per_line은 1 이상이어야 한다. 받은 값: {max_chars}"
        )
    max_lines = int(config.get("captions.max_lines"))
    if max_lines < 1:
        raise CaptionError(
            f"captions.max_lines는 1 이상이어야 한다. 받은 값: {max_lines}"
        )

    cues = list(_cues(scenes, max_chars=max_chars))
    _warn_about_overflow(cues, max_lines=max_lines)
    return cues


def render(cues: Sequence[Cue]) -> str:
    """큐 목록을 SRT 텍스트로 옮긴다.

    큐가 하나도 없어도 빈 문자열을 돌려준다 — `captions.srt`는 항상 생성되는 공통
    산출물이고(PRD 6.2 표), 문구가 없는 것과 파일이 없는 것은 다른 상태다.
    """
    blocks = [
        "\n".join([str(number), _timespan(cue), *cue.lines])
        for number, cue in enumerate(cues, start=1)
    ]
    # 마지막 큐 뒤에도 빈 줄을 남긴다. 블록 구분자가 빈 줄이므로 파서가 파일 끝을
    # 특수하게 다루지 않아도 된다.
    return "".join(f"{block}\n\n" for block in blocks)


def _cues(scenes: Mapping[str, Any], *, max_chars: int) -> Iterator[Cue]:
    """장면당 큐 하나. 문구가 없는 장면(`countdown`)은 건너뛴다."""
    start_sec = 0.0
    for scene in scenes["scenes"]:
        # 누계를 매번 반올림한다. #16이 `narration_offset`을 같은 방식으로 계산했으므로,
        # 여기서 잔여를 쌓으면 뒤쪽 장면의 경계가 기록된 오프셋과 어긋난다.
        end_sec = round(start_sec + scene["duration"], DURATION_DIGITS)

        lines: list[str] = []
        for part in (scene.get("text"), scene.get("caption")):
            # 두 문구를 이어 붙이지 않고 각자 접는다. 정답과 해설은 다른 문장이고,
            # 화면에서도 정답 아래에 해설이 따로 뜬다 (D1 확정 스펙 5.4).
            if part:
                lines.extend(wrap(part, max_chars))

        if lines:
            # 낭독 장면은 말이 시작되는 시각에 연다. `narration_offset`은 이미 영상 시작
            # 기준 절대 시각이므로 여기서 누계를 더하지 않는다 (#16).
            yield Cue(
                start_sec=scene.get("narration_offset", start_sec),
                end_sec=end_sec,
                lines=tuple(lines),
            )

        start_sec = end_sec


def wrap(text: str, max_chars: int) -> list[str]:
    """어절 단위로 접는다. 한 어절이 상한을 넘으면 그 어절 안에서 자른다.

    자르는 대상이 어절 하나뿐인 이유는 한국어에서 어절 중간의 줄바꿈이 읽기를 눈에 띄게
    방해하기 때문이다. 상한을 넘는 어절은 어디서 잘라도 방해가 되므로, 그때만 넘침 대신
    자름을 택한다.
    """
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""
        while len(word) > max_chars:
            lines.append(word[:max_chars])
            word = word[max_chars:]
        current = word

    if current:
        lines.append(current)
    return lines


def _warn_about_overflow(cues: Sequence[Cue], *, max_lines: int) -> None:
    """줄 수 상한을 넘은 큐를 경고한다. **자르지 않는다** — 원문을 잃는 쪽이 더 나쁘다.

    **`WARNING`이라 `--verbose` 여부와 무관하게 콘솔과 `run.log` 양쪽에 남는다.**
    """
    for number, cue in enumerate(cues, start=1):
        if len(cue.lines) > max_lines:
            LOGGER.warning(
                "자막 %d번이 %d줄이다 — 상한 %d줄(captions.max_lines)을 넘는다. "
                "내용은 그대로 두었으므로 문장을 줄이거나 상한을 조정한다: %s",
                number,
                len(cue.lines),
                max_lines,
                " / ".join(cue.lines),
            )


def _timespan(cue: Cue) -> str:
    return f"{timecode(cue.start_sec)} --> {timecode(cue.end_sec)}"


def timecode(seconds: float) -> str:
    """`HH:MM:SS,mmm`. SRT의 소수 구분자는 점이 아니라 쉼표다."""
    total_ms = round(seconds * 1000)
    milliseconds = total_ms % 1000
    total_seconds = total_ms // 1000
    return (
        f"{total_seconds // 3600:02d}:"
        f"{total_seconds // 60 % 60:02d}:"
        f"{total_seconds % 60:02d},"
        f"{milliseconds:03d}"
    )
