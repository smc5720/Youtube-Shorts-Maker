"""번들된 효과음이 규격을 지키는지 — 이슈 #18의 완료 조건.

**바이너리가 산출물인 이슈라서 테스트가 스펙의 유일한 상주 검사다.** 파일을 사람이 교체할
수 있고(더 좋은 소리를 찾았다든지), 그때 길이나 음량이 어긋나면 렌더 결과에서만 드러난다.
여기서 커밋된 파일을 직접 재면 교체 시점에 걸린다.

`tools/generate_sfx.py`를 import하지 않는다 — 생성 도구가 아니라 **저장소에 있는 파일**이
검사 대상이고, 도구의 상수를 가져다 쓰면 도구를 고치면 통과하는 테스트가 된다. 상한값은
이슈 본문에서 온 숫자를 여기 적는다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from shorts_maker.types.quiz.scene_template import ANSWER_SFX, COUNTDOWN_SFX

SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "sfx"

MAX_SEC = {COUNTDOWN_SFX: 1.0, ANSWER_SFX: 1.5}
"""이슈 #18이 정한 길이 상한. 카운트다운 비프는 `countdown_sec` 3초 안에서 초마다 트리거되고
(#23), 정답 효과음은 정답 장면 앞머리를 덮는다."""

PEAK_CEILING_DBFS = -12.0
PEAK_SPREAD_CEILING_DB = 3.0
"""낭독 대비 최종 레벨은 #23이 정한다. 여기서 보는 것은 소스끼리 들쭉날쭉하지 않은가다."""

SAMPLE_RATE = 48000

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg가 PATH에 없다",
)


def sfx_path(stem: str) -> Path:
    return SFX_DIR / f"{stem}.mp3"


def probe(path: Path, entries: str) -> dict[str, str]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "default=nw=1", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
    )


_MAX_VOLUME = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def peak_dbfs(path: Path) -> float:
    """완료 조건이 지정한 측정 명령 — `ffmpeg -af volumedetect`."""
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    found = _MAX_VOLUME.search(completed.stderr)
    assert found is not None, f"{path.name}에서 peak를 읽지 못했다"
    return float(found.group(1))


# --- 이름 계약 --------------------------------------------------------------


def test_the_bundled_stems_are_the_names_the_scene_template_emits() -> None:
    """`scenes.json`의 `sfx` 값이 곧 파일 stem이라는 것이 #18이 확정하는 계약이다.

    장면 템플릿이 이름을 바꾸면 여기서 깨지고, 경로 해석을 하는 #23이 런타임에 파일을 못
    찾는 것보다 먼저 걸린다.
    """
    for stem in (COUNTDOWN_SFX, ANSWER_SFX):
        assert sfx_path(stem).is_file(), f"{stem}.mp3가 assets/sfx/에 없다"


def test_no_unused_sound_is_bundled() -> None:
    """장면 템플릿이 내지 않는 이름은 넣지 않는다 — `tension.mp3`를 뺀 근거다 (#18).

    쓰이지 않는 바이너리는 저장소에서 커지기만 하고, 라이선스 확인 대상만 늘린다.
    """
    bundled = {path.stem for path in SFX_DIR.glob("*.mp3")}
    assert bundled == {COUNTDOWN_SFX, ANSWER_SFX}


def test_every_bundled_file_is_credited() -> None:
    """CREDITS.md에 없는 파일이 섞여 들어오면 출처 없는 바이너리가 배포된다 (PRD 8장)."""
    credits = (SFX_DIR / "CREDITS.md").read_text(encoding="utf-8")
    for path in sorted(SFX_DIR.glob("*.mp3")):
        assert path.name in credits, f"{path.name}이 CREDITS.md에 없다"


# --- 실제 FFmpeg ------------------------------------------------------------


@ffmpeg_required
@pytest.mark.parametrize("stem", sorted(MAX_SEC))
def test_the_container_matches_the_bundle_format(stem: str) -> None:
    """mp3 / 48kHz — #18이 정한 규격. 믹싱 시점의 리샘플은 #23이 한다."""
    streams = probe(sfx_path(stem), "stream=codec_name,sample_rate")

    assert streams["codec_name"] == "mp3"
    assert int(streams["sample_rate"]) == SAMPLE_RATE


@ffmpeg_required
@pytest.mark.parametrize("stem", sorted(MAX_SEC))
def test_each_sound_stays_under_its_length_ceiling(stem: str) -> None:
    duration = float(probe(sfx_path(stem), "format=duration")["duration"])

    assert 0 < duration <= MAX_SEC[stem]


@ffmpeg_required
@pytest.mark.parametrize("stem", sorted(MAX_SEC))
def test_each_sound_leaves_headroom(stem: str) -> None:
    assert peak_dbfs(sfx_path(stem)) <= PEAK_CEILING_DBFS


@ffmpeg_required
def test_the_two_sounds_sit_at_the_same_level() -> None:
    """한쪽만 튀면 #23이 레벨을 정할 때 파일별 보정이 필요해진다."""
    peaks = [peak_dbfs(sfx_path(stem)) for stem in MAX_SEC]

    assert max(peaks) - min(peaks) <= PEAK_SPREAD_CEILING_DB


@ffmpeg_required
@pytest.mark.parametrize("stem", sorted(MAX_SEC))
def test_the_sound_starts_at_the_head_of_the_file(stem: str) -> None:
    """앞머리 무음이 있으면 트리거 시점이 그만큼 밀린다 — 30fps 한 프레임이 0.033초다.

    mp3는 인코더 지연을 담지만 FFmpeg 디코더가 LAME 태그를 읽어 걷어낸다 (#16에서 확인).
    그 전제가 깨지는 파일로 교체되면 여기서 걸린다.
    """
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(sfx_path(stem)),
         "-af", "silencedetect=noise=-60dB:d=0.005", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )

    leading = re.search(r"silence_start:\s*(-?\d+(?:\.\d+)?)", completed.stderr)
    assert leading is None or float(leading.group(1)) > 0.0
