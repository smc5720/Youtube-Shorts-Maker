"""`assets/sfx/`의 효과음을 FFmpeg 사인 합성으로 생성한다 (#18).

효과음을 외부에서 받아오지 않고 여기서 만드는 이유는 라이선스다. PRD 8장은 "직접 생성한"
소스를 허용하고, 저장소에 커밋되어 데스크톱 앱에까지 동봉되는 파일에는 제3자 조건이 하나도
없는 것이 가장 안전하다. 부수 효과로 길이와 peak를 스펙 값에 정확히 맞출 수 있고 재현된다.

실행하면 `assets/sfx/*.mp3`를 덮어쓰고 CREDITS.md에 적을 측정값을 표준출력에 낸다:

    python tools/generate_sfx.py

이 스크립트는 런타임 코드가 아니다 — 파이프라인은 완성된 mp3만 읽는다. 커밋된 파일이
스펙을 지키는지는 `tests/test_sfx_assets.py`가 확인한다.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SAMPLE_RATE = 48000
"""#18이 정한 규격. `voice.mp3`는 44.1kHz(`timeline._SAMPLE_RATE`)지만 섞기 전에 맞추는
것은 믹싱하는 쪽(#23)이다 — 낭독 세그먼트도 provider 규격으로 와서 그렇게 처리된다."""

CHANNEL_LAYOUT = "stereo"
MP3_BITRATE = "192k"

TARGET_PEAK_DBFS = -14.0
"""정규화 목표. 상한은 -12 dBFS이고 두 파일이 같은 목표로 맞춰지므로 편차 조건(3dB)은
자동으로 충족된다. 2dB를 비워 두는 것은 lame 인코딩이 peak를 조금 밀어 올리기 때문이다."""

ATTACK_SEC = 0.004
"""클릭 방지용 최소 attack. 이보다 짧으면 파형이 0에서 튀어 딸깍 소리가 난다."""


@dataclass(frozen=True)
class Partial:
    """한 성분음. 사인 하나에 지수 감쇠 엔벨로프를 씌운 것."""

    freq_hz: float
    start_sec: float
    length_sec: float
    gain: float = 1.0


@dataclass(frozen=True)
class Sfx:
    """효과음 하나의 정의. `stem`이 곧 `scenes.json`의 `sfx` 값이다 (#18)."""

    stem: str
    what: str
    max_sec: float
    file_sec: float
    """파일을 여기서 끊는다. `curve=exp` 감쇠는 성분음 길이가 끝나기 훨씬 전에 -60dBFS
    아래로 내려가므로, 자르지 않으면 들리지 않는 꼬리가 파일 길이에 남는다. 그 길이를 믹싱
    쪽(#23)이 소리 나는 구간으로 읽으면 트리거 간격 판단이 어긋난다."""

    partials: tuple[Partial, ...] = field(default_factory=tuple)


# 카운트다운 비프는 단발이다 — 3-2-1 각 초에 트리거하는 것은 #23이다.
# 880Hz에 한 옥타브 위를 낮게 얹으면 배경 위에서 또렷하게 들린다.
BEEP = Sfx(
    stem="beep",
    what="카운트다운 비프 (단발)",
    max_sec=1.0,
    file_sec=0.10,
    partials=(
        Partial(freq_hz=880.0, start_sec=0.0, length_sec=0.18),
        Partial(freq_hz=1760.0, start_sec=0.0, length_sec=0.14, gain=0.35),
    ),
)

# 정답 공개는 C5-E5-G5-C6 상승 아르페지오다. 마지막 음만 길게 남겨 여운을 만든다.
CORRECT = Sfx(
    stem="correct",
    what="정답 공개 (상승 아르페지오)",
    max_sec=1.5,
    file_sec=0.68,
    partials=(
        Partial(freq_hz=523.25, start_sec=0.00, length_sec=0.45, gain=0.8),
        Partial(freq_hz=659.25, start_sec=0.09, length_sec=0.45, gain=0.8),
        Partial(freq_hz=783.99, start_sec=0.18, length_sec=0.45, gain=0.8),
        Partial(freq_hz=1046.50, start_sec=0.27, length_sec=0.80),
    ),
)

SFX = (BEEP, CORRECT)


class GenerateError(Exception):
    """효과음을 만들거나 재는 데 실패했다."""


def _filtergraph(sfx: Sfx, gain_db: float) -> str:
    """성분음들을 사인 소스 + 엔벨로프 + `amix`로 옮긴다.

    `amix=normalize=0`은 입력 수로 나누지 않겠다는 뜻이다. 진폭은 정규화 단계가 맡으므로
    여기서 자동으로 줄어들면 두 번 조절하게 된다 (`timeline.mix_voice_track`과 같은 이유).
    """
    chains: list[str] = []
    labels: list[str] = []
    for i, p in enumerate(sfx.partials):
        label = f"p{i}"
        labels.append(f"[{label}]")
        decay = max(p.length_sec - ATTACK_SEC, 0.001)
        chain = (
            f"sine=frequency={p.freq_hz}:sample_rate={SAMPLE_RATE}:duration={p.length_sec}"
            f",volume={p.gain}"
            f",afade=t=in:st=0:d={ATTACK_SEC}"
            f",afade=t=out:st={ATTACK_SEC}:d={decay}:curve=exp"
        )
        if p.start_sec > 0:
            chain += f",adelay={int(round(p.start_sec * 1000))}"
        chains.append(f"{chain}[{label}]")

    mix = (
        f"{''.join(labels)}amix=inputs={len(sfx.partials)}:normalize=0"
        f",atrim=end={sfx.file_sec}"
        f",volume={gain_db}dB"
        f",aformat=sample_rates={SAMPLE_RATE}:channel_layouts={CHANNEL_LAYOUT}[out]"
    )
    return ";".join([*chains, mix])


def _render(sfx: Sfx, out: Path, gain_db: float, codec_args: list[str]) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-v",
        "error",
        "-y",
        "-filter_complex",
        _filtergraph(sfx, gain_db),
        "-map",
        "[out]",
        *codec_args,
        str(out),
    ]
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise GenerateError(f"{sfx.stem} 렌더 실패: {done.stderr.strip()}")


_MAX_VOLUME = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def measure_peak_dbfs(path: Path) -> float:
    """`volumedetect`가 보고하는 peak. 완료 조건이 요구하는 측정 명령이 이것이다."""
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    found = _MAX_VOLUME.search(done.stderr)
    if not found:
        raise GenerateError(f"{path.name}의 peak를 읽지 못했다: {done.stderr.strip()[-400:]}")
    return float(found.group(1))


def measure_duration_sec(path: Path) -> float:
    """실측 길이. 측정 경계를 `ffprobe` 하나로 두는 것은 `tts/speech.py`와 같은 이유다."""
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise GenerateError(f"{path.name}의 길이를 재지 못했다: {done.stderr.strip()}")
    return float(done.stdout.strip())


def generate(sfx: Sfx, out_dir: Path) -> tuple[float, float]:
    """`out_dir/<stem>.mp3`를 만들고 (길이, peak)를 돌려준다.

    무손실로 한 번 만들어 peak를 재고, 목표까지의 차이를 gain으로 되먹여 mp3를 뽑는다.
    사인 진폭에서 합성 peak를 손으로 계산하면 감쇠 엔벨로프가 겹치는 지점을 틀린다.
    """
    with tempfile.TemporaryDirectory(prefix="sfx-") as tmp:
        probe = Path(tmp) / f"{sfx.stem}.wav"
        _render(sfx, probe, gain_db=0.0, codec_args=["-c:a", "pcm_s16le"])
        raw_peak = measure_peak_dbfs(probe)

    out = out_dir / f"{sfx.stem}.mp3"
    _render(
        sfx,
        out,
        gain_db=round(TARGET_PEAK_DBFS - raw_peak, 2),
        codec_args=["-c:a", "libmp3lame", "-b:a", MP3_BITRATE],
    )
    return measure_duration_sec(out), measure_peak_dbfs(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "sfx",
        metavar="경로",
        help="mp3를 쓸 디렉터리 (기본: assets/sfx)",
    )
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[Sfx, float, float]] = []
    for sfx in SFX:
        try:
            duration, peak = generate(sfx, args.out_dir)
        except GenerateError as err:
            print(f"실패: {err}", file=sys.stderr)
            return 1
        results.append((sfx, duration, peak))

    print(f"{'파일':<14}{'길이(초)':>10}{'상한':>8}{'peak(dBFS)':>13}")
    for sfx, duration, peak in results:
        print(f"{sfx.stem + '.mp3':<14}{duration:>10.3f}{sfx.max_sec:>8.1f}{peak:>13.1f}")

    peaks = [peak for _, _, peak in results]
    print(f"\npeak 편차: {max(peaks) - min(peaks):.1f} dB (상한 3.0)")

    over = [s.stem for s, d, _ in results if d > s.max_sec]
    loud = [s.stem for s, _, p in results if p > -12.0]
    if over or loud:
        print(f"스펙 위반 — 길이: {over or '없음'} / peak: {loud or '없음'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
