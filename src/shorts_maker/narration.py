"""낭독 장면 → 세그먼트 오디오 (PRD 7.5.2, 이슈 #15).

**이 단계는 타입을 모른다.** 입력은 `scenes.json` 하나이고 분기 조건은 `narrate` 플래그
뿐이다 (PRD 7.4.1). 어떤 장면을 읽어 주는지는 장면 템플릿이 이미 정했고, 여기서는 그
플래그가 붙은 장면마다 파일 하나를 만들어 `audio`와 `audio_duration`을 채운다.

- **파일명은 `schemas.scenes.segment_path()`가 만든다.** 규약을 여기 다시 적으면 확정
  검증(`validate_scenes_final`)이 보는 규칙과 두 곳으로 갈라진다.
- **`duration`과 `narration_offset`은 채우지 않는다.** 둘 다 앞선 장면들의 확정 길이
  누계가 있어야 나오므로 한 덩어리이며 #16이 소유한다. 그래서 이 단계를 지난
  `scenes.json`은 아직 `validate_scenes_final()`을 통과하지 않는다.
- **재실행은 이미 있는 세그먼트를 다시 쓴다.** 판단 기준은 `audio/segments.json`에 남긴
  "그 파일을 만든 텍스트"이고, 길이는 그때 기록한 값이 아니라 **파일을 다시 재서** 얻는다
  — 세그먼트는 사람이 개별 교체할 수 있는 파일이므로(PRD 7.5.2) 디스크에 있는 것이 진실
  이다. 캐시(`tts.cache_dir`)는 run 디렉터리 **밖**에서 다른 run과 결과를 공유하는 층이고,
  이 재사용은 같은 run 디렉터리 안에서 사람이 손댄 파일을 지키는 층이다.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import PACKAGE_LOGGER
from .schemas.scenes import (
    AUDIO_FIELDS,
    DURATION_DIGITS,
    SEGMENT_DIR,
    segment_path,
    validate_scenes,
)
from .tts import SpeechSynthesizer, TTSError

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.narration")
"""`shorts_maker` 로거의 자식이라 `run_logging`이 붙인 핸들러로 그대로 흘러간다."""

MANIFEST_NAME = "segments.json"
"""세그먼트를 만든 텍스트의 기록. 계약 파일이 아니라 이 단계의 재실행 상태다 — 읽는 쪽은
여기뿐이고, 지우면 다음 실행이 전부 다시 합성한다(캐시가 살아 있으면 합성 없이 복사한다)."""

MANIFEST_VERSION = 1

SegmentHook = Callable[[int, int, bool], None]
"""`(끝난 개수, 전체 개수, 재사용했는가)` (#77).

**재사용 여부를 함께 준다.** 재생성이 "무엇을 다시 만들었는가"를 보고하는 근거이고, 그 판단은
`segments.json`을 읽는 이 모듈만 할 수 있다 — 호출부가 파일 수정 시각으로 다시 세면 같은 판단이
두 곳에 생긴다.
"""


def manifest_path(run_dir: Path) -> Path:
    """세그먼트 기록의 자리.

    **지우면 다음 실행이 전부 다시 합성한다.** 강제 재합성(#36)이 그 경로이고, 경로를 여기
    두는 이유는 `_Manifest.load`와 두 곳에서 갈리지 않게 하는 것이다.
    """
    return run_dir / SEGMENT_DIR / MANIFEST_NAME


def synthesize_segments(
    scenes: Mapping[str, Any],
    *,
    run_dir: Path,
    synthesizer: SpeechSynthesizer,
    on_segment: SegmentHook | None = None,
) -> dict[str, Any]:
    """`narrate: true` 장면마다 세그먼트를 만들고 오디오 필드를 채운 장면 목록을 돌려준다.

    입력을 바꾸지 않는다. 낭독 장면이 하나도 없으면 `audio/`를 만들지 않고 입력과 같은
    내용을 돌려준다 — 세그먼트는 낭독 장면이 있을 때만 생기는 산출물이다 (PRD 6.2 표).

    Args:
        scenes: `scenes.json` 내용. 초안 상태다.
        run_dir: 이번 run의 출력 디렉터리. 세그먼트 경로의 기준이다.
        synthesizer: `tts.create_synthesizer(config)`가 만든 합성기.
        on_segment: 세그먼트 하나가 끝날 때마다 부른다 (#77). 앱의 재생성이 진행을 그리는
            자리이고, **이 단계만 `n/m`을 낼 수 있다** — 낭독 장면 수를 아는 것이 여기다.
            **취소도 이 훅으로 들어온다**: 예외를 던지면 그 자리에서 멈추고, 그때까지 만든
            세그먼트는 기록과 함께 남아 다음 실행이 이어서 한다.

    Raises:
        TTSError: 합성이 재시도를 다 쓰고도 실패했거나 길이를 재지 못했을 때.
        SchemaError: 입력 장면 목록이 스키마를 만족하지 않을 때.
    """
    # 입력을 검증한다. 이 함수는 CLI 말고도 앱 백엔드와 테스트가 직접 부르는 입구이고,
    # 깨진 장면 목록으로 합성을 시작하면 만들다 만 오디오가 run 디렉터리에 남는다.
    validate_scenes(scenes)

    updated = copy.deepcopy(dict(scenes))
    provider = synthesizer.provider
    manifest = _Manifest.load(run_dir, provider=provider.name, voice=provider.voice)
    total = sum(1 for scene in updated["scenes"] if scene.get("narrate"))
    done = 0

    for index, scene in enumerate(updated["scenes"]):
        if not scene.get("narrate"):
            _drop_audio_fields(scene, index)
            continue

        relative = segment_path(index)
        destination = run_dir / relative
        text = scene["text"]

        reused = manifest.matches(index, text) and destination.is_file()
        if reused:
            LOGGER.debug("세그먼트 재사용 %s — 텍스트가 바뀌지 않았다", relative)
            duration_sec = synthesizer.measure(destination)
        else:
            duration_sec = synthesizer.synthesize(text, destination).duration_sec
            manifest.record(index, text)

        scene["audio"] = relative
        scene["audio_duration"] = _rounded(duration_sec, relative)

        done += 1
        if on_segment is not None:
            on_segment(done, total, reused)

    # 채워 넣은 결과가 계약을 벗어나지 않는지 확인한다. 확정 검증은 아직 쓰지 않는다 —
    # `duration`과 `narration_offset`이 비어 있는 것이 이 단계의 정상 상태다 (#16).
    validate_scenes(updated)
    return updated


def _drop_audio_fields(scene: dict[str, Any], index: int) -> None:
    """낭독 장면이 아닌데 남아 있는 오디오 필드를 지운다.

    이전 실행에서 낭독 장면이었다가 `narrate`가 빠진 경우다. 그대로 두면 "세그먼트 수 =
    낭독 장면 수"가 깨지고(PRD 7.5.2) 확정 검증이 반려한다. 파일은 지우지 않는다 —
    사람이 넣었을 수 있고, 참조가 사라진 파일은 다음 run 디렉터리에 따라오지 않는다.
    """
    stale = [key for key in AUDIO_FIELDS if key in scene]
    for key in stale:
        del scene[key]
    if stale:
        LOGGER.debug(
            "scenes[%d]: 낭독 장면이 아니라 오디오 필드를 지웠다 (%s)", index, ", ".join(stale)
        )


def _rounded(duration_sec: float, relative: str) -> float:
    """기록할 실측 길이. 0초로 반올림되는 값은 합성이 깨진 것이다.

    스키마가 `audio_duration > 0`을 요구하므로 그대로 두면 이 단계의 실패가 스키마 위반
    으로 둔갑해 원인이 가려진다. **#16은 다시 재지 않고 여기 기록된 값을 읽어 `duration`을
    확정하므로** 반올림 자리는 계약 쪽(`schemas.scenes.DURATION_DIGITS`)에서 온다.
    """
    value = round(duration_sec, DURATION_DIGITS)
    if value <= 0:
        raise TTSError(
            f"{relative}: 실측 길이가 {duration_sec}초다 — 합성된 오디오가 비어 있다",
            retryable=False,
        )
    return value


@dataclass
class _Manifest:
    """세그먼트를 만든 `(provider, voice, 텍스트)`의 기록.

    `provider`·`voice`가 파일에 함께 있는 이유는 목소리만 바꿔 같은 run 디렉터리에 다시
    실행하는 경우다 — 텍스트는 그대로이므로 텍스트만 비교하면 이전 목소리의 오디오를
    그대로 쓰게 된다. 캐시 키가 세 값을 모두 쓰는 것과 같은 이유다 (`tts/speech.py`).

    **읽기 실패는 재사용을 포기할 뿐 실행을 멈추지 않는다.** 이 파일이 없거나 깨졌다는
    것은 "이 run에 무엇이 있는지 모른다"는 뜻이고, 그때의 안전한 선택은 다시 합성하는
    것이다.
    """

    path: Path
    provider: str
    voice: str
    texts: dict[int, str] = field(default_factory=dict)

    @classmethod
    def load(cls, run_dir: Path, *, provider: str, voice: str) -> _Manifest:
        path = manifest_path(run_dir)
        manifest = cls(path=path, provider=provider, voice=voice)

        raw = _read_manifest(path)
        if raw is None:
            return manifest
        if raw.get("provider") != provider or raw.get("voice") != voice:
            LOGGER.debug(
                "%s: provider·voice가 %s/%s에서 바뀌어 세그먼트를 다시 합성한다",
                MANIFEST_NAME,
                raw.get("provider"),
                raw.get("voice"),
            )
            return manifest

        try:
            manifest.texts = {
                int(entry["index"]): str(entry["text"]) for entry in raw["segments"]
            }
        except (KeyError, TypeError, ValueError) as error:
            LOGGER.debug("%s를 읽을 수 없어 다시 합성한다: %s", MANIFEST_NAME, error)
            manifest.texts = {}
        return manifest

    def matches(self, index: int, text: str) -> bool:
        return self.texts.get(index) == text

    def record(self, index: int, text: str) -> None:
        """합성한 세그먼트를 기록하고 파일에 반영한다.

        **세그먼트마다 쓴다.** 중간에 실패해도 그때까지 만든 파일은 다음 실행이 재사용할
        수 있어야 한다 — 남은 것을 버리면 재실행이 매번 처음부터 합성한다.
        """
        self.texts[index] = text
        # 디렉터리는 합성기가 이미 만들었다. 낭독 장면이 없으면 여기까지 오지 않으므로
        # 빈 `audio/`가 생기지 않는다.
        self.path.write_text(
            json.dumps(self._content(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _content(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_VERSION,
            "provider": self.provider,
            "voice": self.voice,
            # 사람이 열어 봤을 때 어느 문장이 어느 파일이 됐는지 보여야 한다.
            "segments": [
                {"index": index, "audio": segment_path(index), "text": self.texts[index]}
                for index in sorted(self.texts)
            ],
        }


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """기록을 읽는다. 없거나 이 코드가 모르는 모양이면 `None`."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != MANIFEST_VERSION:
        return None
    return raw
