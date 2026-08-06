"""스파이크 #2 — 한국어 TTS provider 실측 하네스.

Edge TTS의 한국어 음성 3개로 퀴즈 질문/정답 문장을 합성하고, 이슈 #2의 비교축 중
**프로그램으로 측정 가능한 것만** 기록한다.

측정하는 것
  - 합성 소요 시간 (wall clock)
  - 합성 결과의 실제 재생 길이 — ffprobe 컨테이너 값과 실제 디코딩 값을 따로 재서 비교한다.
    #16이 duration을 실측값으로 확정하므로 "측정할 수 있다"에 그치지 않고
    "두 방법이 일치한다"까지 확인해야 한다.
  - WordBoundary 이벤트 — 개수, 오프셋, 토큰별 지속시간. #17 자막 타임코드의 입력이 된다.
    **이벤트의 text는 정규화된 발음이 아니라 원문 토큰이다** — 확인해 봤고 아니었다.
    그래서 발음은 아래 음절 속도 환산으로 간접 검사한다.
  - 숫자·단위 발음 — 토큰별 지속시간을 음절 수로 환산해 기대 한국어 읽기와 비교한다
    (`analyze_pronunciation`). 청취 없이 "건너뛰거나 영어로 읽는" 실패만 걸러낼 수 있다.
  - 재합성 결정성 — 같은 문장을 여러 번 합성했을 때 길이가 같은지. #16의 재실행 캐시 전제.

측정하지 않는 것
  - 음질·자연성. 청취가 필요하다. 샘플을 samples/에 남긴다.
  - 유료 후보(OpenAI TTS, ElevenLabs). API 키가 필요해 범위에서 제외했다. 문서 조사로 대체한다.
  - Kokoro. 공식 VOICES.md에 한국어가 없어 합성 전에 탈락했다.

실행: python docs/spikes/2-tts-provider/harness.py
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts

HERE = Path(__file__).parent
SAMPLES_DIR = HERE / "samples"
RESULTS_PATH = HERE / "results.json"

VOICES = [
    "ko-KR-SunHiNeural",
    "ko-KR-InJoonNeural",
    "ko-KR-HyunsuMultilingualNeural",
]

# 결정성 확인에 쓸 반복 횟수.
REPEAT_COUNT = 3


@dataclass(frozen=True)
class Sentence:
    """합성할 문장 하나와 그 문장이 검사하려는 것."""

    id: str
    # question | answer 는 실제로 낭독되는 장면 텍스트, explanation_probe 는 해설 길이의
    # 문장이다. 퀴즈 스펙 0장의 TTS 범위는 "질문 + 정답 낭독"이고 해설은 자막만이므로,
    # explanation_probe 는 발음 검사와 길이 상한 확인 용도로만 쓴다.
    role: str
    text: str
    probe: str  # 이 문장으로 확인하려는 발음 위험
    target_sec: float | None = None  # 퀴즈 스펙 2장의 목표치. 확정값과 비교만 한다.


SENTENCES: list[Sentence] = [
    Sentence(
        id="q_plain",
        role="question",
        text="세계에서 가장 긴 강은?",
        probe="기본 질문. 물음표 억양",
        target_sec=3.0,
    ),
    Sentence(
        id="a_short",
        role="answer",
        text="나일강",
        probe="가장 짧은 정답. min_duration 보정이 필요한 하한 사례",
        target_sec=3.0,
    ),
    Sentence(
        id="a_number_unit",
        role="explanation_probe",
        text="약 6,650km로 세계에서 가장 긴 강입니다.",
        probe="쉼표 포함 숫자 + 단위 약어(km)",
        target_sec=3.0,
    ),
    Sentence(
        id="a_date",
        role="explanation_probe",
        text="1969년 7월 20일, 아폴로 11호가 달에 착륙했습니다.",
        probe="연월일 + 서수/호수 읽기",
        target_sec=3.0,
    ),
    Sentence(
        id="a_height_unit",
        role="explanation_probe",
        text="에베레스트산의 높이는 8,848m입니다.",
        probe="고유명사 + 미터 약어(m). m을 '엠'으로 읽는지",
        target_sec=3.0,
    ),
    Sentence(
        id="a_hanja_noun",
        role="explanation_probe",
        text="현존하는 대한민국 최초의 금속활자본은 직지심체요절입니다.",
        probe="한자어 고유명사. 스파이크 #1이 모호 질문 사례로 남긴 문장",
        target_sec=3.0,
    ),
    Sentence(
        id="a_formula",
        role="explanation_probe",
        text="물의 화학식은 H2O입니다.",
        probe="라틴 문자 + 아래첨자 없는 화학식",
        target_sec=3.0,
    ),
    Sentence(
        id="a_foreign_noun",
        role="answer",
        text="페이턴트 모터바겐",
        probe="외국어 고유명사 음역. 긴 편의 정답 낭독 사례",
        target_sec=3.0,
    ),
    Sentence(
        id="a_medium",
        role="answer",
        text="직지심체요절",
        probe="한자어 고유명사 정답 낭독",
        target_sec=3.0,
    ),
    Sentence(
        id="hook",
        role="hook",
        text="이 상식 4개, 다 맞히면 상위 1%",
        probe="후킹 문장. 현 스펙은 낭독하지 않지만 낭독 시 길이를 알아 둔다",
        target_sec=3.0,
    ),
    Sentence(
        id="cta",
        role="cta",
        text="몇 개 맞혔나요? 댓글로 알려주세요!",
        probe="CTA 문장. 위와 같다",
        target_sec=4.0,
    ),
    Sentence(
        id="q_percent",
        role="question",
        text="지구 표면의 약 71%를 덮고 있는 것은?",
        probe="퍼센트 기호",
        target_sec=3.0,
    ),
]


# 숫자·단위·기호가 한국어로 제대로 펼쳐 읽히는지 확인할 토큰과, 올바른 한국어 읽기.
# 청취 없이 확인하는 방법: WordBoundary가 토큰별 지속시간을 주므로, 순수 한글 토큰에서
# 음절 속도를 구한 뒤 이 토큰의 지속시간을 음절 수로 환산해 기대 읽기와 비교한다.
# 엔진이 숫자를 건너뛰거나 영어로 읽으면 환산 음절 수가 기대치에서 크게 벗어난다.
PRONUNCIATION_PROBES: dict[str, dict[str, str]] = {
    "a_number_unit": {"6,650km로": "육천육백오십킬로미터로"},
    "a_date": {"1969년 7월 20일": "천구백육십구년칠월이십일", "11호가": "십일호가"},
    "a_height_unit": {"8,848m입니다": "팔천팔백사십팔미터입니다"},
    "q_percent": {"71%를": "칠십일퍼센트를"},
    "a_formula": {"H2": "에이치투", "O입니다": "오입니다"},
}

HANGUL_SYLLABLE = re.compile(r"[가-힣]")
NON_HANGUL = re.compile(r"[^가-힣\s]")


def hangul_syllables(text: str) -> int:
    return len(HANGUL_SYLLABLE.findall(text))


def is_pure_hangul(token: str) -> bool:
    """숫자·라틴 문자·기호가 섞이지 않은 토큰. 음절 속도 기준선에 쓴다."""
    return bool(token.strip()) and not NON_HANGUL.search(token)


@dataclass
class Synthesis:
    """한 번의 합성 결과."""

    voice: str
    sentence_id: str
    path: Path
    synth_sec: float
    byte_size: int
    word_boundaries: list[dict] = field(default_factory=list)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe_container_duration(path: Path) -> float | None:
    """컨테이너 헤더가 보고하는 길이. 빠르지만 MP3에서는 추정치일 수 있다."""
    result = run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(path),
        ]
    )
    try:
        return round(float(result.stdout.strip()), 3)
    except ValueError:
        return None


def probe_decoded_duration(path: Path) -> float | None:
    """실제로 디코딩해서 얻는 길이. 컨테이너 값과 어긋나는지 확인하는 대조군."""
    result = run(["ffmpeg", "-v", "error", "-stats", "-i", str(path), "-f", "null", "-"])
    matches = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not matches:
        return None
    hours, minutes, seconds = matches[-1]
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


async def synthesize(voice: str, sentence: Sentence, suffix: str = "") -> Synthesis:
    """한 문장을 합성하고 경계 이벤트를 함께 수집한다.

    `boundary`의 기본값은 `SentenceBoundary`다. 문장 단위로 합성하는 이 파이프라인에서는
    문장 경계가 문장 전체 하나뿐이라 쓸모가 없으므로 `WordBoundary`를 명시한다.
    """
    out_path = SAMPLES_DIR / f"{sentence.id}__{voice}{suffix}.mp3"
    communicate = edge_tts.Communicate(sentence.text, voice, boundary="WordBoundary")

    boundaries: list[dict] = []
    audio = bytearray()
    started = time.perf_counter()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
        elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
            boundaries.append(
                {
                    # edge-tts는 100나노초 단위로 준다. 초로 바꿔 기록한다.
                    "offset_sec": round(chunk["offset"] / 10_000_000, 3),
                    "duration_sec": round(chunk["duration"] / 10_000_000, 3),
                    "text": chunk["text"],
                }
            )
    elapsed = time.perf_counter() - started
    out_path.write_bytes(audio)

    return Synthesis(
        voice=voice,
        sentence_id=sentence.id,
        path=out_path,
        synth_sec=round(elapsed, 3),
        byte_size=len(audio),
        word_boundaries=boundaries,
    )


def analyze_pronunciation(records: list[dict]) -> list[dict]:
    """숫자·단위 토큰이 한국어로 펼쳐 읽혔는지 음절 속도로 환산해 검사한다.

    청취 대신 쓰는 객관 지표다. 음질은 이 방법으로 판단할 수 없다.
    """
    findings: list[dict] = []

    for voice in VOICES:
        voice_records = [r for r in records if r["voice"] == voice]

        # 기준선: 순수 한글 토큰의 음절당 소요 시간.
        baseline_syllables = 0
        baseline_seconds = 0.0
        for record in voice_records:
            for event in record["word_boundaries"]:
                if is_pure_hangul(event["text"]):
                    baseline_syllables += hangul_syllables(event["text"])
                    baseline_seconds += event["duration_sec"]
        if not baseline_syllables:
            continue
        sec_per_syllable = baseline_seconds / baseline_syllables

        for record in voice_records:
            probes = PRONUNCIATION_PROBES.get(record["sentence_id"], {})
            if not probes:
                continue
            for event in record["word_boundaries"]:
                expected_reading = probes.get(event["text"])
                if expected_reading is None:
                    continue
                expected = hangul_syllables(expected_reading)
                implied = event["duration_sec"] / sec_per_syllable
                findings.append(
                    {
                        "voice": voice,
                        "sentence_id": record["sentence_id"],
                        "token": event["text"],
                        "expected_reading": expected_reading,
                        "expected_syllables": expected,
                        "token_duration_sec": event["duration_sec"],
                        "sec_per_syllable": round(sec_per_syllable, 4),
                        "implied_syllables": round(implied, 2),
                        # 기준선 자체가 추정이므로 ±35%를 허용 구간으로 둔다. 숫자를
                        # 건너뛰거나 영어로 읽으면 이 구간을 크게 벗어난다.
                        "within_tolerance": abs(implied - expected) <= expected * 0.35,
                    }
                )

    return findings


async def main() -> int:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for voice in VOICES:
        for sentence in SENTENCES:
            print(f"합성 {voice} / {sentence.id}", flush=True)
            result = await synthesize(voice, sentence)
            container = probe_container_duration(result.path)
            decoded = probe_decoded_duration(result.path)
            gap = None
            if container is not None and decoded is not None:
                gap = round(abs(container - decoded), 3)

            spoken = [event["text"] for event in result.word_boundaries]
            last_boundary_end = None
            if result.word_boundaries:
                last = result.word_boundaries[-1]
                last_boundary_end = round(last["offset_sec"] + last["duration_sec"], 3)

            records.append(
                {
                    "voice": voice,
                    "sentence_id": sentence.id,
                    "role": sentence.role,
                    "text": sentence.text,
                    "probe": sentence.probe,
                    "target_sec": sentence.target_sec,
                    "sample": str(result.path.relative_to(HERE)).replace("\\", "/"),
                    "synth_sec": result.synth_sec,
                    "byte_size": result.byte_size,
                    "container_duration_sec": container,
                    "decoded_duration_sec": decoded,
                    "duration_gap_sec": gap,
                    "word_boundary_count": len(result.word_boundaries),
                    "last_boundary_end_sec": last_boundary_end,
                    "spoken_tokens": spoken,
                    "word_boundaries": result.word_boundaries,
                }
            )

    # 결정성 — 같은 문장을 여러 번 합성했을 때 길이가 흔들리는지.
    determinism: list[dict] = []
    probe_voice = VOICES[0]
    probe_sentence = next(s for s in SENTENCES if s.id == "a_number_unit")
    durations: list[float | None] = []
    for attempt in range(REPEAT_COUNT):
        print(f"결정성 확인 {attempt + 1}/{REPEAT_COUNT}", flush=True)
        repeat = await synthesize(probe_voice, probe_sentence, suffix=f"__repeat{attempt + 1}")
        durations.append(probe_decoded_duration(repeat.path))
    determinism.append(
        {
            "voice": probe_voice,
            "sentence_id": probe_sentence.id,
            "decoded_durations_sec": durations,
            "identical": len(set(durations)) == 1,
        }
    )

    payload = {
        "provider": "edge-tts",
        "edge_tts_version": getattr(edge_tts, "__version__", "unknown"),
        "python": sys.version.split()[0],
        "voices": VOICES,
        "repeat_count": REPEAT_COUNT,
        "syntheses": records,
        "determinism": determinism,
        "pronunciation": analyze_pronunciation(records),
    }
    RESULTS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"기록 완료 {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    # 진행 로그가 한국어다. Windows에서 리다이렉트하면 로케일 인코딩(cp949)을 쓰므로 고정한다.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
