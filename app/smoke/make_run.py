"""스모크가 열 run 디렉터리를 만든다 (이슈 #26, #27, #28).

**손으로 쓴 `project.json`을 두지 않는다.** 앱이 여는 것은 파이프라인이 낸 파일이고,
고정된 사본을 두면 스키마가 바뀌었을 때 스모크만 조용히 통과한다. 그래서 초기 상태는
`project.build`가 만든다 — 계약을 아는 코드가 하나뿐이어야 한다는 것과 같은 이유다.

**장면 목록은 손으로 쓴다.** 낭독이 있는 장면은 실측 오디오를 요구하므로(PRD 7.5.1)
파이프라인을 통째로 돌려야 하고, 그러면 이 스모크가 확인하려는 것(앱 화면)보다 TTS·타임라인
대역이 더 커진다. 대신 **역할 구성은 퀴즈 장면 템플릿이 내는 것과 같게** 둔다 — 문제 그룹과
총 길이가 화면에서 갈리는 지점이라 고정 길이 장면만으로는 #27의 완료 조건을 밟지 못한다.

두 개를 만든다.

- `run-smoke` — 28.0초. **목표(45~60초)보다 짧지만 경고가 아니다** (확정 스펙 1.8)
- `run-smoke-long` — 92.5초. 유튜브 쇼츠 상한을 넘어 **경고 색을 쓰는 유일한 경우**

**콘텐츠 산출물도 함께 쓴다** (#28). 파일명은 레지스트리에서 가져오고, 문제 번호는 장면의
`question_id`와 같게 둔다 — 둘이 어긋나면 앱이 "장면 구성이 낡았다"를 처음부터 띄운다.
검증 상태 셋(`flagged` / `verified` / `verify` 없음 = `unverified`)이 한 파일에 다 들어 있어
확정 스펙 4장의 세 표기가 화면에서 한 번에 갈린다.

렌더 산출물은 만들지 않는다. `final_short.mp4`가 없는 것이 프리뷰가 최종 렌더 경로를 지나지
않는다는 증거이기도 하다 (#27).

**배경 사용자 파일도 함께 만든다** (#80). run 디렉터리 밖에 두는 것이 그 기능의 계약이라
(파일을 복사하지 않고 있는 자리를 가리킨다) 여기서도 밖에 둔다.

사용법:
    python app/smoke/make_run.py --out DIR
        → {"run": ..., "long": ..., "background": ..., "unsupported": ...} 한 줄
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from shorts_maker import project  # noqa: E402
from shorts_maker.config import load_config  # noqa: E402
from shorts_maker.run_context import write_artifact  # noqa: E402
from shorts_maker.schemas.project import PROJECT_SCHEMA  # noqa: E402
from shorts_maker.schemas.scenes import SCENES_SCHEMA  # noqa: E402
from shorts_maker.shorts_types import DEFAULT_TYPE, get_type  # noqa: E402

QUESTIONS = [
    {
        "heading": "세계에서 가장 긴 강은?",
        "answer": "나일강",
        "explanation": "나일강은 약 6,650km로 아마존강보다 조금 길다.",
        "difficulty": "easy",
        # 검증기가 결함을 찾은 문제. **사유가 무엇을 고쳐야 하는지 말한다** (확정 스펙 3.2).
        "verify": {
            "status": "flagged",
            "confidence": 0.62,
            "source": "재답변 불일치 — 3회 중 1회만 정답과 일치했다. 다른 답변은 '아마존강'.",
        },
    },
    {
        "heading": "적도가 지나는 대륙은 몇 개인가?",
        "answer": "3개",
        "explanation": "남아메리카·아프리카·아시아 세 대륙을 지난다.",
        "difficulty": "medium",
        "verify": {"status": "verified", "confidence": 0.99},
    },
    {
        "heading": "태양계에서 가장 큰 행성은?",
        "answer": "목성",
        "explanation": "목성의 지름은 지구의 약 11배다.",
        "difficulty": "hard",
        # **`verify`가 아예 없다.** 사람이 손으로 지웠거나 초안 그대로인 상태이고, 앱은
        # 이것을 `unverified`로 읽는다 — 성공이 아니라 "판단 근거가 없다"이다 (퀴즈 스펙 5.2).
        "verify": None,
    },
]


def scenes_for(*, answer_sec: float, hook_sec: float = 2.5, cta_sec: float = 3.0) -> dict[str, Any]:
    """퀴즈 장면 템플릿과 같은 역할 구성. 길이만 인자로 받는다.

    **`question`과 `answer`는 낭독 장면이다** — 장면 템플릿과 같다(`narrate: true`는 그 둘
    뿐이다). 확정 검증이 요구하는 오디오 필드를 함께 채우고, **세그먼트 파일은 만들지
    않는다** — 검증은 이름이 장면 인덱스와 맞는지만 보고(`schemas/scenes.py`) 앱과 프리뷰는
    오디오를 열지 않는다 (프리뷰 명령에는 오디오 체인이 아예 없다, #27).

    낭독 길이를 채워 두는 이유는 **#82의 경고 경로가 낭독 없이는 관찰되지 않는다**는 것이다.
    `hook`·`countdown`·`cta`에는 낭독이 없으므로 비교 대상이 없고, 그것이 확정 스펙 7.1이
    2차 시안에서 고친 지점이다.
    """
    scenes: list[dict[str, Any]] = [
        {"role": "hook", "kicker": "세계 지리 상식", "text": "이 문제 맞힐 수 있나", "duration": hook_sec},
    ]
    for number, question in enumerate(QUESTIONS, start=1):
        scenes += [
            {
                "role": "question",
                "question_id": number,
                "heading": question["heading"],
                "text": question["heading"],
                "duration": 2.0,
                "narrate": True,
                "target_duration": 3.0,
            },
            # countdown의 `duration`은 `seconds`와 같아야 한다 (확정 검증).
            {
                "role": "countdown",
                "question_id": number,
                "heading": question["heading"],
                "duration": 3.0,
                "seconds": 3,
            },
            {
                "role": "answer",
                "question_id": number,
                "heading": question["heading"],
                "text": question["answer"],
                "caption": question["explanation"],
                "duration": answer_sec,
                "narrate": True,
                "target_duration": 3.0,
            },
        ]
    scenes.append({"role": "cta", "text": "다음 문제도 풀어보자", "duration": cta_sec})
    return _with_audio_fields({"schema_version": 1, "type": "quiz", "scenes": scenes})


def _with_audio_fields(data: dict[str, Any]) -> dict[str, Any]:
    """낭독 장면에 TTS가 채우는 필드를 넣는다 (#15, #16).

    `audio`는 **장면 인덱스**로 매기고(확정 검증이 강제한다), 낭독 길이는 장면 길이보다
    0.4초 짧게 둔다 — 실측 오디오 + 패딩으로 확정된 상태의 모양이다 (PRD 7.5.1).
    """
    elapsed = 0.0
    for index, scene in enumerate(data["scenes"]):
        if scene.get("narrate"):
            scene["audio"] = f"audio/seg-{index:03d}.mp3"
            scene["audio_duration"] = round(scene["duration"] - 0.4, 3)
            scene["narration_offset"] = round(elapsed + 0.2, 3)
        elapsed += scene["duration"]
    return data


def content_for() -> dict[str, Any]:
    """장면과 같은 문제 번호를 쓰는 콘텐츠 산출물 (#28).

    `id`가 `scenes.json`의 `question_id`와 어긋나면 앱이 "장면 구성이 낡았다"를 처음부터
    띄운다 — 스모크가 그 표시를 실제 순서 변경으로 확인하려면 시작 상태는 맞아야 한다.
    """
    questions = []
    for number, question in enumerate(QUESTIONS, start=1):
        entry: dict[str, Any] = {
            "id": number,
            "question": question["heading"],
            "answer": question["answer"],
            "explanation": question["explanation"],
            "difficulty": question["difficulty"],
            # 장면의 `seconds`와 같은 값이다. 앱이 고치면 재생성이 장면에 옮긴다 (#77).
            "countdown_sec": 3,
        }
        if question["verify"] is not None:
            entry["verify"] = question["verify"]
        questions.append(entry)

    return {
        "schema_version": 1,
        "type": DEFAULT_TYPE,
        "category": "general_knowledge",
        "language": "ko",
        "hook": "이 문제 맞힐 수 있나",
        "cta": "다음 문제도 풀어보자",
        "questions": questions,
    }


def build_run(out: Path, name: str, scenes: dict[str, Any]) -> Path:
    run_dir = out / name
    run_dir.mkdir(parents=True, exist_ok=True)
    write_artifact(run_dir, SCENES_SCHEMA.name, scenes)
    # 파일명은 레지스트리가 준다. 여기 `quiz.json`을 적으면 타입이 늘었을 때 조용히 낡는다.
    write_artifact(run_dir, get_type(DEFAULT_TYPE).content_artifact, content_for())
    # `search_from`을 주지 않으면 저장소의 config.yaml을 읽어 실행 위치에 좌우된다.
    content = project.build(scenes, config=load_config(search_from=run_dir), run_dir=run_dir)
    write_artifact(run_dir, PROJECT_SCHEMA.name, content)
    return run_dir


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """단색 PNG 한 장. **의존성 없이 만든다** — 스모크에 이미지 라이브러리를 들이지 않는다.

    작아도 된다. 렌더러가 비율을 유지한 채 캔버스를 채우므로(`video_renderer._fill`) 확인
    대상은 해상도가 아니라 "고른 파일이 프레임에 들어왔는가"다 (#80).
    """
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(raw)),
            chunk(b"IEND", b""),
        ]
    )


def build_backgrounds(out: Path) -> dict[str, str]:
    """배경 사용자 파일 두 개 (#80). **run 디렉터리 밖이다** — 그것이 이 기능의 계약이다.

    앱은 파일을 run 디렉터리로 복사하지 않고 있는 자리를 절대 경로로 가리킨다 (PRD 14.1).
    받지 않는 형식(`.webm`)은 확장자만으로 거부되므로 내용이 필요 없다 — 열어 보기 전에
    거부하는 것이 이 결정의 핵심이다.
    """
    files = out / "배경"
    files.mkdir(parents=True, exist_ok=True)
    # 배경 프리셋과 눈에 띄게 다른 색이라 프레임이 바뀐 것이 바이트 수로 드러난다.
    (files / "배경.png").write_bytes(_solid_png(16, 16, (255, 0, 170)))
    (files / "배경.webm").write_bytes(b"decoded by nobody")
    return {
        "background": str(files / "배경.png"),
        "unsupported": str(files / "배경.webm"),
    }


def build(out: Path) -> dict[str, str]:
    return {
        # 2.5 + (2.0 + 3.0 + 2.5) x 3 + 3.0 = 28.0초
        "run": str(build_run(out, "run-smoke", scenes_for(answer_sec=2.5))),
        # 2.5 + (2.0 + 3.0 + 24.0) x 3 + 3.0 = 92.5초 — 상한 초과
        "long": str(build_run(out, "run-smoke-long", scenes_for(answer_sec=24.0))),
        **build_backgrounds(out),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="run 디렉터리를 만들 위치")
    # **UTF-8을 명시한다.** 이 줄은 파이프로 Node가 읽고, Windows에서 파이프 stdout의 기본
    # 인코딩은 콘솔 코드페이지(cp949)라 한글이 든 경로가 **에러 없이 값만 깨진다**
    # (스파이크 #25 4.3, `api._use_utf8`과 같은 이유다).
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(build(parser.parse_args().out), ensure_ascii=False))


if __name__ == "__main__":
    main()
