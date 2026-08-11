"""확정 `scenes.json` + `project.json` → `final_short.mp4` (PRD 6.3·7.7, 이슈 #19).

MoviePy를 쓰지 않고 FFmpeg 명령을 직접 만든다 (PRD 14.1). **오버레이(#20~#23)가 여기에
얹히는 골격이다** — 이 모듈이 정하는 것은 캔버스 규격, 배경, 오디오 트랙, 그리고 장면
경계의 프레임 정렬이고, 그 위에 무엇을 그리는지는 뒤따르는 이슈가 정한다.

- **입력은 `project.json`이다.** 배경·오디오·출력 규격을 config에서 다시 읽지 않는다 —
  값을 정하는 경로가 둘이 되면 앱(#26)이 편집한 프로젝트와 CLI 렌더가 갈린다 (PRD 7.10).
  `scenes.json`을 함께 받는 이유는 프로젝트가 장면 배열을 복사하지 않기 때문이다 (7.4.1).
- **프레임 경계 정렬은 이 모듈이 소유한다** (`align`). `duration`은 밀리초 자리 실수이고
  30fps 한 프레임은 33.33ms라, 장면 시작 시각을 각자 누적하면 오버레이마다 다른 경계를
  갖게 된다 (`schemas/scenes.py`의 `DURATION_DIGITS` 주석).
- **오디오 스트림은 항상 정확히 하나다.** 낭독 장면이 없어도 무음 트랙을 넣고, 효과음이
  얹혀도(#23) 믹스 결과가 하나로 나온다 — 필터 체인은 `audio_mix`가 만들고 이 모듈은 명령에
  옮겨 담는다.
- **이 단계도 타입을 모른다.** `role`로 갈라지는 규칙은 오버레이 쪽에 생기고, 골격이 읽는
  것은 `duration`뿐이다 (PRD 7.4.1).
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PACKAGE_LOGGER, audio_mix, overlay
from .assets import AssetError, background_presets
from .audio_mix import AudioChain, AudioMixError
from .overlay import OverlayError
from .schemas.project import BACKGROUND_KINDS
from .schemas.scenes import validate_scenes_final

LOGGER = logging.getLogger(f"{PACKAGE_LOGGER}.video_renderer")

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
FPS = 30
OUTPUT_NAME = "final_short.mp4"
"""영상 규격과 출력 파일명 (PRD 6.3, 6.2 표).

`project.json`의 `render` 섹션에 그대로 들어가고(`project.build`), 렌더는 그 파일에서 읽은
값을 쓴다. 여기 상수는 초기 상태의 값이지 렌더가 강제하는 값이 아니다 — 앱이 해상도를 바꾸면
바뀐 값으로 렌더된다.
"""

FFMPEG_TIMEOUT_SEC = 900
"""60초 영상 하나를 인코딩하는 상한. 넘겼다면 기다릴 것이 아니라 환경 문제다
(`timeline.FFMPEG_TIMEOUT_SEC`와 같은 성격이고, 인코딩이 있어 값이 크다)."""

CRF = 20
"""libx264 품질. **config 키가 아니다** — 45~60초 세로 영상이라 파일 크기보다 텍스트 경계가
중요해서 기본값 23보다 한 단계 올려 잡았다. 실측으로 고른 값은 아니므로, 번인이 들어간 뒤
(#20) 외곽선 주변이 뭉개지면 이 값부터 본다. 사람이 조정할 값이 되면 그때 config를 연다."""

_AUDIO_BITRATE = "192k"
"""출력 오디오 비트레이트. 샘플레이트·레이아웃은 `audio_mix`가 소유한다 — 효과음을 섞는 쪽이
입력 규격을 맞추므로(#23) 같은 값이 두 모듈에 있으면 한쪽만 바뀔 수 있다."""

_HEX_COLOR = re.compile(r"^#?([0-9A-Fa-f]{6})$")
"""`background.kind`가 `color`일 때의 값. 번들 프리셋(`assets.HEX_COLOR`)보다 느슨하다 —
사람이 `project.json`에 직접 적는 값이라 대소문자와 `#` 유무를 가리지 않는다."""


class RenderError(Exception):
    """최종 영상을 만들 수 없다."""


@dataclass(frozen=True)
class Timeline:
    """프레임 경계에 맞춘 장면 배치.

    장면 하나의 길이를 프레임 수로 바꾸고, 시작 시각을 그 누계에서 낸다. `duration`을 직접
    더하지 않는 이유는 밀리초 자리 실수의 누계가 프레임 경계와 어긋나기 때문이다.
    """

    fps: int
    frames: tuple[int, ...]
    """장면별 프레임 수. 순서는 `scenes.json`의 장면 배열과 같다."""

    @property
    def total_frames(self) -> int:
        return sum(self.frames)

    @property
    def total_sec(self) -> float:
        return self.total_frames / self.fps

    @property
    def starts(self) -> tuple[float, ...]:
        """장면별 시작 시각(초). 영상 시작 기준이고 프레임 경계 위에 있다.

        **오버레이(#20~#22)가 `enable='between(t,a,b)'`에 쓰는 값이다.** 각자 `duration`을
        다시 누적하면 같은 장면의 경계가 요소마다 갈린다.
        """
        starts: list[float] = []
        elapsed = 0
        for count in self.frames:
            starts.append(elapsed / self.fps)
            elapsed += count
        return tuple(starts)

    def span(self, index: int) -> tuple[float, float]:
        """장면 하나의 (시작, 끝) 시각.

        끝은 **다음 장면의 시작과 정확히 같은 값이다** — `start + frames/fps`로 따로 계산하면
        부동소수점 표현이 갈려 `between(t,a,b)` 구간 사이에 한 프레임짜리 틈이 생길 수 있다.
        """
        starts = self.starts
        end = starts[index + 1] if index + 1 < len(starts) else self.total_sec
        return starts[index], end

    @property
    def frame_spans(self) -> tuple[tuple[int, int], ...]:
        """장면별 (시작 프레임, 끝 프레임). 끝은 다음 장면의 시작과 같다.

        **오버레이(#20~#22)가 `enable` 식에 쓰는 값이다.** 초로 반올림한 값을 적으면 그
        반올림이 실제 프레임 시각보다 커졌을 때 요소가 한 프레임 늦게 켜진다. 프레임 번호를
        `n/fps` 나눗셈으로 그대로 넘기면 필터의 `t`와 같은 값이 나온다 (`overlay._enable`).
        """
        spans: list[tuple[int, int]] = []
        elapsed = 0
        for count in self.frames:
            spans.append((elapsed, elapsed + count))
            elapsed += count
        return tuple(spans)


def align(scenes: Mapping[str, Any], *, fps: int = FPS) -> Timeline:
    """확정 장면 목록을 프레임 경계에 맞춘다.

    `frames_i = round(duration_i * fps)` / `start_i = sum(frames_0..i-1) / fps`.

    Raises:
        RenderError: `fps`가 1 미만이거나 확정되지 않은 장면 목록일 때.
    """
    if fps < 1:
        raise RenderError(f"fps는 1 이상이어야 한다. 받은 값: {fps}")

    frames: list[int] = []
    for index, scene in enumerate(scenes["scenes"]):
        duration = scene.get("duration")
        if duration is None:
            raise RenderError(
                f"scenes[{index}]: duration이 없다 — 타임라인 확정을 거치지 않은 장면 목록이다"
            )
        count = round(float(duration) * fps)
        if count < 1:
            # 반 프레임보다 짧은 장면. 0프레임이면 다음 장면과 시작 시각이 같아져 오버레이의
            # `between(t,a,b)`가 빈 구간이 된다.
            LOGGER.warning(
                "scenes[%d](%s): %.3f초는 %dfps에서 한 프레임보다 짧다 — 한 프레임으로 늘린다",
                index,
                scene["role"],
                float(duration),
                fps,
            )
            count = 1
        frames.append(count)

    return Timeline(fps=fps, frames=tuple(frames))


def render(
    project: Mapping[str, Any], scenes: Mapping[str, Any], *, run_dir: Path
) -> Path:
    """`final_short.mp4`를 만들고 그 경로를 돌려준다.

    Args:
        project: `project.json` 내용. 배경·오디오·출력 규격을 여기서 읽는다.
        scenes: `scenes.json` 내용. **확정 상태여야 한다** — 초안의 목표치로 렌더하면
            영상 길이가 `voice.mp3`와 어긋난다.
        run_dir: 이번 run의 출력 디렉터리. 상대 경로의 기준이다.

    Raises:
        RenderError: 배경을 해석할 수 없거나 FFmpeg가 없거나 실패했을 때.
        SchemaError: 장면 목록이 확정 상태가 아닐 때.
    """
    # 입구에서 확정 검증을 한다. 이 함수는 CLI 말고도 앱 백엔드와 테스트가 직접 부른다.
    validate_scenes_final(scenes)

    fps = _int_field(project, "fps")
    timeline = align(scenes, fps=fps)
    # **오버레이를 명령보다 먼저 만든다.** 폰트 경로 오타나 한글을 못 그리는 폰트는 여기서
    # 걸리고, 그때 인코딩은 아직 시작되지 않았다 (#20의 완료 조건).
    overlays = build_overlays(project, scenes, timeline=timeline)
    # 효과음도 인코딩 전에 만든다 (#23). 번들에 없는 `sfx` 이름은 여기서 걸리고, 그때 인코딩은
    # 아직 시작되지 않았다 — 오버레이의 폰트 검증과 같은 자리다.
    audio = build_audio(project, scenes, timeline=timeline)
    command = build_command(
        project,
        run_dir=run_dir,
        total_sec=timeline.total_sec,
        overlays=overlays,
        audio=audio,
    )
    output = run_dir / str(project["render"]["output"])

    # 명령 전문을 남긴다. run.log는 --verbose와 무관하게 DEBUG까지 남으므로(run_context)
    # 실패한 렌더를 손으로 재현할 수 있다.
    LOGGER.debug("렌더 명령 %s", subprocess.list2cmdline(command))
    LOGGER.debug(
        "장면 %d개 / %d프레임 / %.3f초",
        len(timeline.frames),
        timeline.total_frames,
        timeline.total_sec,
    )

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FFMPEG_TIMEOUT_SEC,
        )
    except FileNotFoundError as error:
        raise RenderError(
            "ffmpeg를 찾을 수 없다. FFmpeg를 설치하고 PATH에 넣는다"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise RenderError(
            f"ffmpeg가 {FFMPEG_TIMEOUT_SEC}초 안에 끝나지 않았다: {output}"
        ) from error

    if completed.returncode != 0:
        # stderr도 run.log에 남긴다 — 위 명령 전문과 짝이어야 원인이 드러난다.
        LOGGER.error("ffmpeg stderr %s", completed.stderr.strip())
        raise RenderError(
            f"{output.name}을 만들지 못했다 — ffmpeg 종료 코드 "
            f"{completed.returncode}, stderr {completed.stderr.strip()!r}"
        )

    return output


def build_overlays(
    project: Mapping[str, Any], scenes: Mapping[str, Any], *, timeline: Timeline
) -> list[str]:
    """장면 위에 그릴 `drawtext` 필터 목록 (#20~#22). 수치는 `overlay`가 소유한다.

    **재료를 `project.json`에서 읽는다.** 자막 스타일·폰트·cta 문구를 config에서 다시 읽으면
    값을 정하는 경로가 둘이 되고, 앱(#29)이 프로젝트를 편집해도 CLI 렌더가 그것을 무시한다
    (PRD 7.10, `project.py`).

    Raises:
        RenderError: 프리셋 이름·폰트 경로·cta 문구가 화면으로 옮겨지지 않을 때.
    """
    render_section = project.get("render", {})
    try:
        return overlay.build(
            scenes,
            timeline.frame_spans,
            fps=timeline.fps,
            style=overlay.style_for(_text_field(project, "caption_style")),
            fonts=overlay.resolve_fonts(render_section.get("font_path")),
            cta_punch=_text_field(project, "cta_punch"),
            cta_tail=_text_field(project, "cta_tail"),
            caption_onset=_number_field(project, "caption_onset_sec"),
        )
    except OverlayError as error:
        # 오버레이 실패도 렌더 실패다. 부르는 쪽(main)이 잡는 예외를 하나로 유지한다.
        raise RenderError(str(error)) from error


def build_audio(
    project: Mapping[str, Any], scenes: Mapping[str, Any], *, timeline: Timeline
) -> AudioChain:
    """낭독 위에 효과음을 얹는 오디오 체인 (#23). 트리거 시각은 `audio_mix`가 소유한다.

    **게인을 `project.json`에서 읽는다.** 배경·자막 스타일과 같은 이유로 config를 다시 열지
    않는다 — 앱(#29)이 트랙 볼륨을 편집하면 CLI 렌더가 그 값으로 돌아야 한다 (PRD 7.10).

    Raises:
        RenderError: `sfx` 이름이 번들에 없거나 게인 값이 음수일 때.
    """
    try:
        return audio_mix.build(
            scenes,
            timeline.frame_spans,
            fps=timeline.fps,
            volume=_audio_number(project, "sfx_volume"),
        )
    except AudioMixError as error:
        # 오디오 체인 실패도 렌더 실패다. 부르는 쪽(main)이 잡는 예외를 하나로 유지한다.
        raise RenderError(str(error)) from error


def build_command(
    project: Mapping[str, Any],
    *,
    run_dir: Path,
    total_sec: float,
    overlays: Sequence[str] = (),
    audio: AudioChain | None = None,
) -> list[str]:
    """렌더 명령을 만든다. **FFmpeg를 부르지 않으므로 어디서나 검증할 수 있다.**

    Args:
        overlays: 배경 위에 순서대로 이을 필터 문자열 (`build_overlays`). 비어 있으면
            배경만 그린다 — #19까지의 상태다.
        audio: 오디오 필터 체인 (`build_audio`). `None`이면 낭독만 담는다 — 효과음이 없는
            장면 목록과 같은 결과이고, #22까지의 명령과 같은 모양이다.

    Raises:
        RenderError: `project.json`의 배경·규격 값을 명령으로 옮길 수 없을 때.
    """
    chain = audio if audio is not None else audio_mix.voice_only()
    width = _int_field(project, "width")
    height = _int_field(project, "height")
    fps = _int_field(project, "fps")
    output = run_dir / str(project["render"]["output"])
    length = f"{total_sec:.3f}"

    video_input, video_chain = _background(
        project.get("background", {}),
        run_dir=run_dir,
        width=width,
        height=height,
        fps=fps,
        length=length,
    )
    audio_input = _audio_input(project.get("audio", {}), run_dir=run_dir, length=length)

    # 효과음 입력은 낭독 뒤에 붙는다. `audio_mix`가 그 순서로 인덱스를 매긴다.
    command = ["ffmpeg", "-y", "-v", "error", *video_input, *audio_input, *chain.inputs]
    command += [
        "-filter_complex",
        ";".join(
            [
                # 오버레이는 배경 체인 뒤에 이어 붙는다. 목록 순서가 그리는 순서다.
                f"[0:v]{','.join([video_chain, *overlays])}[video]",
                # 오디오는 낭독 하나이거나 효과음이 얹힌 믹스다 (#23). 어느 쪽이든 마지막
                # 단계가 `[audio]`를 내고, 스트림 수는 정확히 하나로 유지된다 (PRD 7.7).
                *chain.steps,
            ]
        ),
        "-map",
        "[video]",
        "-map",
        "[audio]",
        "-c:v",
        "libx264",
        "-crf",
        str(CRF),
        # 일반 재생기와 YouTube가 요구하는 크로마 서브샘플링 (PRD 6.3).
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        _AUDIO_BITRATE,
        # 총 길이를 여기서 끊는다. 프레임 정렬 값이므로 마지막 프레임이 잘리지 않는다.
        "-t",
        length,
        "-movflags",
        "+faststart",
        str(output),
    ]
    return command


def _background(
    background: Mapping[str, Any],
    *,
    run_dir: Path,
    width: int,
    height: int,
    fps: int,
    length: str,
) -> tuple[list[str], str]:
    """배경 하나를 (입력 인자, `[0:v]`에 걸 필터 체인)으로 옮긴다 (PRD 14.1의 배경 소스).

    **CLI 경로가 실제로 지나는 것은 `preset`뿐이다** — `project.json`을 편집하는 주체가 아직
    없다 (#29, #30). 나머지 세 종류는 앱이 붙기 전까지 단위 테스트가 지킨다.
    """
    kind = background.get("kind")
    value = str(background.get("value", ""))
    size = f"{width}x{height}"

    if kind == "preset":
        try:
            preset = background_presets()[value]
        except AssetError as error:
            raise RenderError(f"배경 프리셋을 읽을 수 없다 — {error}") from error
        except KeyError:
            known = ", ".join(background_presets())
            raise RenderError(
                f"모르는 배경 프리셋이다: {value!r}. 쓸 수 있는 이름: {known}"
            ) from None

        if preset.is_gradient:
            # **`y1`은 높이-1이다.** 높이를 그대로 주면 범위를 벗어난 값이라 필터가 회전
            # 그라디언트 기본 동작으로 조용히 되돌아간다 (D1 확정 스펙 6.2).
            # **`speed=0`도 필수다.** 기본값 0.01이면 그라디언트가 시간에 따라 회전해
            # 50초 지점에서 위아래가 뒤집힌다 — 경고 없이 그림만 달라진다 (#19에서 실측).
            source = (
                f"gradients=s={size}:r={fps}:"
                f"c0={_color(preset.top)}:c1={_color(preset.bottom)}:"
                f"x0=0:y0=0:x1=0:y1={height - 1}:speed=0"
            )
        else:
            source = f"color=c={_color(preset.top)}:s={size}:r={fps}"
        return ["-f", "lavfi", "-t", length, "-i", source], "setsar=1"

    if kind == "color":
        source = f"color=c={_color(value)}:s={size}:r={fps}"
        return ["-f", "lavfi", "-t", length, "-i", source], "setsar=1"

    if kind == "image":
        # 정지 이미지를 영상 길이만큼 반복한다.
        path = str(_source_file(value, run_dir))
        return (
            ["-loop", "1", "-framerate", str(fps), "-t", length, "-i", path],
            _fill(width, height),
        )

    if kind == "video":
        # 배경 영상이 짧으면 되감아 채운다. 길면 `-t`가 끊는다.
        path = str(_source_file(value, run_dir))
        return (
            ["-stream_loop", "-1", "-t", length, "-i", path],
            f"{_fill(width, height)},fps={fps}",
        )

    raise RenderError(
        f"모르는 배경 종류다: {kind!r}. 쓸 수 있는 값: {', '.join(BACKGROUND_KINDS)}"
    )


def _fill(width: int, height: int) -> str:
    """세로 캔버스를 비율 왜곡 없이 채운다. 넘치는 쪽을 잘라 빈 영역을 남기지 않는다."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )


def _audio_input(
    audio: Mapping[str, Any], *, run_dir: Path, length: str
) -> list[str]:
    """오디오 입력 하나. 낭독이 없으면 무음 트랙이다 — 스트림 수가 입력에 따라 달라지면
    효과음을 얹는 #23이 인덱스를 조건부로 계산해야 한다."""
    voice = audio.get("voice")
    if voice is None:
        return [
            "-f",
            "lavfi",
            "-t",
            length,
            "-i",
            f"anullsrc=r={audio_mix.SAMPLE_RATE}:cl={audio_mix.CHANNEL_LAYOUT}",
        ]

    path = _source_file(str(voice), run_dir)
    return ["-i", str(path)]


def _source_file(value: str, run_dir: Path) -> Path:
    """`project.json`의 파일 경로를 푼다.

    경로 값은 run 디렉터리 기준 상대 경로다 (PRD 7.10). 사용자가 자기 배경 파일을 절대
    경로로 적는 것도 받는다 — 그 파일은 run 디렉터리 밖에 있다.
    """
    if not value:
        raise RenderError("파일 경로가 비어 있다")

    path = Path(value)
    resolved = path if path.is_absolute() else run_dir / path
    if not resolved.is_file():
        raise RenderError(f"파일을 찾을 수 없다: {resolved}")
    return resolved


def _color(value: str) -> str:
    """`#RRGGBB` → `0xRRGGBB`. FFmpeg의 색 문법이다."""
    match = _HEX_COLOR.match(value.strip())
    if match is None:
        raise RenderError(f"색은 #RRGGBB 표기여야 한다. 받은 값: {value!r}")
    return f"0x{match.group(1).upper()}"


def _text_field(project: Mapping[str, Any], key: str) -> str:
    """`render` 섹션의 문자열 값. 없으면 어느 키인지 말한다 — 앱이 만든 프로젝트나 사람이
    편집한 파일이 스키마를 지나지 않고 직접 들어올 수 있다."""
    try:
        value = project["render"][key]
    except (KeyError, TypeError):
        raise RenderError(f"project.json의 render.{key}가 없다") from None
    if not isinstance(value, str):
        raise RenderError(f"render.{key}는 문자열이어야 한다. 받은 값: {value!r}")
    return value


def _number_field(project: Mapping[str, Any], key: str) -> float:
    """`render` 섹션의 실수 값. 정수도 받는다 — `caption_onset_sec: 1`은 1.0이다."""
    try:
        value = project["render"][key]
    except (KeyError, TypeError):
        raise RenderError(f"project.json의 render.{key}가 없다") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RenderError(f"render.{key}는 0 이상의 수여야 한다. 받은 값: {value!r}")
    return float(value)


def _audio_number(project: Mapping[str, Any], key: str) -> float:
    """`audio` 섹션의 실수 값 (#23). 정수도 받는다 — `sfx_volume: 1`은 1.0이다.

    `render` 섹션이 아닌 이유는 이것이 트랙의 성격을 정하는 값이기 때문이다. 앱(#26)이 붙일
    트랙별 볼륨도 같은 자리에 온다 (PRD 7.10).
    """
    try:
        value = project["audio"][key]
    except (KeyError, TypeError):
        raise RenderError(f"project.json의 audio.{key}가 없다") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RenderError(f"audio.{key}는 0 이상의 수여야 한다. 받은 값: {value!r}")
    return float(value)


def _int_field(project: Mapping[str, Any], key: str) -> int:
    """`render` 섹션의 정수 값. 스키마가 이미 확인하지만, 앱이 만든 프로젝트도 직접 들어온다."""
    try:
        value = project["render"][key]
    except (KeyError, TypeError):
        raise RenderError(f"project.json의 render.{key}가 없다") from None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RenderError(f"render.{key}는 1 이상의 정수여야 한다. 받은 값: {value!r}")
    return value
