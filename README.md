# Youtube-Shorts-Maker

유튜브 쇼츠 제작을 위한 자동 생성 엔진과 세로 영상 편집 앱.

주제·원문·기사 전문·링크를 입력하면 요약 → 대본 → 음성 → 자막 → 편집을 거쳐
**1080×1920 세로 영상(MP4)** 초안을 만들어 줍니다. 사용자는 앱에서 초안을 열어
자막·텍스트·배경·장면 순서를 수정한 뒤 최종 영상을 내보냅니다.

## 파이프라인

```
입력(주제/원문/링크) → 요약 → 대본 → 음성(TTS) → 자막 → 편집 → 1080×1920 MP4
```

## MVP 방향

1차 목표는 YouTube 자동 업로드가 **아니라**, 위 파이프라인으로 **영상 초안을 만들고
앱에서 편집·내보내기**하는 것입니다. 업로드 자동화는 YouTube Data API 정책과 OAuth
구성을 별도 검토한 뒤 **2차 기능**으로 진행합니다.

## 설치와 실행

Python 3.11 이상이 필요합니다 (개발 환경은 3.13에서 확인). 음성 합성 결과의 길이를
`ffprobe`로 재고 최종 영상을 `ffmpeg`로 인코딩하므로 **FFmpeg가 설치되어 PATH에 있어야
합니다.** 파이썬 래퍼 라이브러리는 쓰지 않고 명령을 직접 만들어 부릅니다.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows. macOS·Linux는 source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
shorts-maker --topic "세계 지리 상식 퀴즈"
```

`outputs/run-{timestamp}/` 디렉터리가 생기고 실행 로그가 `run.log`로 남습니다. 같은 명령을
다시 실행하면 새 run 디렉터리가 생기고 이전 결과는 그대로 보존됩니다.

| 인자 | 기본값 | 설명 |
| --- | --- | --- |
| `--topic` | (필수) | 쇼츠로 만들 주제 한 줄 |
| `--type` | `quiz` | 쇼츠 타입 |
| `--out` | `outputs` | run 디렉터리를 만들 상위 경로 |
| `--config` | `./config.yaml` | 설정 파일 경로. 없으면 기본값으로 동작 |
| `-v`, `--verbose` | off | 디버그 로그를 콘솔에도 출력 (`run.log`에는 항상 남음) |

### 설정

설정 파일은 선택입니다. 없으면 전부 기본값으로 동작합니다. 값을 바꾸려면
[`config.example.yaml`](config.example.yaml)을 `config.yaml`로 복사한 뒤 바꿀 키만 남기면
됩니다 — 나머지 키는 기본값을 씁니다.

```bash
cp config.example.yaml config.yaml
```

우선순위는 **CLI 오버라이드 > `config.yaml` > 기본값**입니다. 알 수 없는 키나 타입이 맞지
않는 값은 run 디렉터리를 만들기 **전에** 오류로 걸러지고, 발견된 문제를 한 번에 모아서
보여줍니다. 해석된 설정 전체는 `run.log`에 기록됩니다.

> **현재는 영상 파일까지 나옵니다.** 퀴즈 타입은 주제에서 문제 세트를 만들고 블라인드
> 검증까지 마쳐 `quiz.json`을 남기고, 이어서 `scenes.json`·`metadata.json`, 낭독 장면별
> 세그먼트 오디오(`audio/seg-*.mp3`), 실측 길이로 확정한 타임라인과 `voice.mp3`,
> `captions.srt`, `project.json`을 만든 뒤 규격에 맞는 `final_short.mp4`를 렌더합니다.
> 화면에는 후킹·질문·카운트다운·정답 강조·해설 자막·CTA가 번인되고, 카운트다운 비프와 정답
> 효과음이 함께 믹스됩니다. **아직 없는 것** — 배경음악과 ducking, 장면 모션, 편집 앱,
> `--url` / `--text-file` 입력입니다. 설정 키 일부는 값을 읽어 둘 뿐 아직 소비하는 단계가
> 없습니다.

테스트:

```bash
pytest
```

## 문서

- [PRD](docs/PRD.md) — 입력 기반 YouTube Shorts 자동 생성 MVP 기획서
- [기여 가이드](CONTRIBUTING.md) — 이슈 기반 작업 방식, 라벨 체계, 이슈 리파인먼트 절차

### 쇼츠 타입

- [퀴즈](docs/types/quiz.md) — 상식/지식 퀴즈 쇼츠 (LLM 자동 생성, 주관식 정답 공개형)

## 개발 워크플로

작업은 **GitHub 이슈 단위**로 진행합니다. 새 이슈는 `status: needs-refinement`로
시작해 [리파인먼트](CONTRIBUTING.md#3-이슈-리파인먼트)를 거쳐 `status: ready`가 된 뒤
착수합니다. 자세한 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.
