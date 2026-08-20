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

`outputs/run-{timestamp}/` 디렉터리가 생기고 실행 로그가 `run.log`로, 그 실행이 실제로 쓴 설정
전체가 `config.used.yaml`로 남습니다. 같은 명령을 다시 실행하면 새 run 디렉터리가 생기고 이전
결과는 그대로 보존됩니다.

`config.used.yaml`은 기록이지 입력이 아닙니다 — 고쳐도 그 run이 다시 돌지 않습니다. 대신
그대로 복사해 `--config`로 넘기면 같은 설정으로 새 run을 만들 수 있습니다.

원문 파일에서 만들 수도 있습니다. 첫 비어 있지 않은 줄이 제목이 되고, 받은 원문 전체는 그
run의 `source.json`에 남습니다.

```bash
shorts-maker --text-file 기사.txt
```

링크에서 본문을 뽑을 수도 있습니다. 이 경로에는 **추출기가 따로 필요합니다** — 패키지 12개에
약 55MB라 기본 설치에 넣지 않았고, 없는 상태로 `--url`을 주면 run 디렉터리를 만들기 전에
설치 방법을 안내하며 멈춥니다.

```bash
pip install -e ".[source]"    # 위에서 [dev]로 설치했다면 이미 들어 있습니다
shorts-maker --url "https://news.example.com/article/1"
```

받아들이는 것은 **로그인 없이 열리는 정적 HTML 페이지**입니다. 유료·로그인·JS 렌더링 페이지는
지원하지 않고, 그때는 HTTP 상태·`Content-Type`·추출된 본문 길이 셋 중 하나에 걸려 거부되며
`--text-file`을 안내합니다. 통과한 페이지는 제목과 글자 수가 콘솔에 나오고 본문 전체가
`source.json`에 남습니다 — 본문이 본문인지 보는 것은 사람입니다.

실행이 도중에 실패했다면 **그 run 디렉터리를 이어서 돌릴 수 있습니다.** 없는 산출물만 만들고
있는 것은 그대로 쓰므로, 렌더만 실패했다면 LLM 호출도 음성 합성도 다시 하지 않습니다. 설정은
그 run의 `config.used.yaml`에서 오고, 사람이 앱에서 고친 `project.json`은 손대지 않습니다.

```bash
shorts-maker --resume outputs/run-20260820-101500
shorts-maker --resume outputs/run-20260820-101500 --force render   # 영상이 있어도 다시 인코딩
```

**모델은 부르지 않습니다.** 그래서 `quiz.json`이나 `metadata.json`이 없는 run(콘텐츠 생성이나
메타데이터 단계에서 멈춘 run)은 이어 돌릴 수 없고, 무엇이 없는지 알려주며 멈춥니다 — 그때는
새로 실행합니다. 콘텐츠를 **고쳐서** 반영하려는 것이라면 이어 돌리기가 아니라 앱의 재생성입니다.

| 인자 | 기본값 | 설명 |
| --- | --- | --- |
| `--topic` | (넷 중 하나 필수) | 쇼츠로 만들 주제 한 줄 |
| `--text-file` | (넷 중 하나 필수) | 원문 텍스트 파일. 첫 비어 있지 않은 줄이 제목이 됨 |
| `--url` | (넷 중 하나 필수) | 본문을 추출할 링크. `[source]` extra 필요 |
| `--resume` | (넷 중 하나 필수) | 이어서 돌릴 기존 run 디렉터리 |
| `--type` | `quiz` | 쇼츠 타입 |
| `--out` | `outputs` | run 디렉터리를 만들 상위 경로 |
| `--config` | `./config.yaml` | 설정 파일 경로. 없으면 기본값으로 동작 |
| `--fail-on-flagged` | off | 검수 필요 항목이 있으면 종료 코드 4로 멈춤 (산출물은 그대로 남음) |
| `--force` | (없음) | `--resume`에서 산출물이 있어도 다시 만들 단계: `segments` / `render` |
| `-v`, `--verbose` | off | 디버그 로그를 콘솔에도 출력 (`run.log`에는 항상 남음) |

위 표의 `--type`·`--out`·`--config`·`--fail-on-flagged`는 **생성 실행에서만 뜻이 있습니다** —
`--resume`과 함께 주면 거부합니다. 이어 돌리기는 그 run의 설정 기록과 산출물로 돌기 때문에,
받아 놓고 무시하면 반영됐다고 오해하게 됩니다.

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
> 효과음이 함께 믹스됩니다. **아직 없는 것** — 배경음악과 ducking, 장면 모션입니다. 편집
> 앱은 셸까지 있습니다(아래). 설정 키 일부는 값을 읽어 둘 뿐 아직 소비하는 단계가 없습니다.
>
> **`--text-file`·`--url`은 지금 원문을 기록만 합니다.** 콘텐츠 생성기에 가는 것은 제목이고
> 본문은 `source.json`에만 남습니다 — 본문을 소비하는 요약·대본 단계가 아직 없어서이며, 그
> 사실이 `run.log`에 남습니다.

### 편집 앱

`app/`에 Electron + React 앱이 있습니다. run 디렉터리를 열어 장면 목록·정지 프레임 프리뷰를
보고, 문제와 장면 속성(길이·자막 문구·텍스트 오버레이·자막 스타일·배경·볼륨)을 편집한 뒤
재생성과 최종 렌더까지 실행합니다.

```bash
cd app && npm install && npm start
```

자세한 실행 방법과 배치는 [`app/README.md`](app/README.md)를 참고하세요.

### 전 구간 확인

테스트에는 주제 하나로 `final_short.mp4`까지 지나는 **전 구간 스모크
테스트**(`tests/test_e2e_smoke.py`)가 들어 있습니다. LLM과 TTS만 픽스처로 바꾸고 FFmpeg와
`ffprobe`는 진짜를 쓰므로 네트워크 없이 돌고, FFmpeg가 없는 환경에서는 그 파일만
건너뜁니다.

```bash
pytest                          # 전체
pytest tests/test_e2e_smoke.py  # 전 구간만 (약 30초, 렌더 3회)
```

#### 로컬 실전 모드

픽스처 모드가 지나지 않는 것 — **모델이 낸 문제의 품질, 실제 음성, 화면에 그려진 결과** —
은 사람이 봅니다. 실제 provider로 한 번 돌립니다.

- `claude` CLI가 PATH에 있고 로그인되어 있어야 합니다 (`llm.providers.claude_cli.binary`).
- Edge TTS는 네트워크로 나갑니다.

```bash
shorts-maker --topic "세계 지리 상식" --verbose
```

| 대상 | 확인할 것 |
| --- | --- |
| 산출물 | `config.used.yaml`·`quiz.json`·`scenes.json`·`metadata.json`·`audio/seg-*.mp3`·`audio/segments.json`·`voice.mp3`·`captions.srt`·`project.json`·`final_short.mp4`가 모두 있는가. `script.txt`·`summary.json`·`source.json`은 이 경로에서 **생기지 않아야** 합니다 |
| 규격 | `ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,pix_fmt -of default=nw=1 outputs/run-*/final_short.mp4` → 1080×1920 / `h264` / `yuv420p` / `30/1`, 오디오는 `aac` 한 스트림 |
| 화면 | 후킹 → 질문(`Q1/N`) → 카운트다운 숫자와 진행 바 → 정답 확대·색 전환 → 해설 자막 → CTA 두 줄이 순서대로 나오는가. 글자가 안전 영역을 벗어나거나 줄바꿈이 어색하지 않은가 |
| 소리 | 낭독이 질문·정답 장면 시작과 맞는가, 카운트다운 비프가 초마다 들리는가, 정답 효과음이 정답 장면 앞머리에 오는가, 음량이 튀지 않는가 |
| 자막 | `captions.srt`를 재생기에 얹었을 때 타임코드가 화면과 맞는가 |
| 검수 | 콘솔의 `검수 필요` 경고와 `quiz.json`의 `verify.status`·`source`. **flagged가 있어도 영상은 나오고 종료 코드는 0입니다** — 멈추려면 `--fail-on-flagged`(종료 코드 4)를 붙입니다 |

내용이 마음에 들지 않으면 같은 명령을 다시 실행합니다. 새 run 디렉터리가 생기고 이전
결과는 그대로 남습니다.

## 문서

- [PRD](docs/PRD.md) — 기획과 확정 결정 (14장이 결정 목록)
- [기여 가이드](CONTRIBUTING.md) — 이슈 기반 작업 방식, 라벨 체계, 이슈 리파인먼트 절차
- [퀴즈 타입 스펙](docs/types/quiz.md) — 상식/지식 퀴즈 쇼츠 (LLM 자동 생성, 주관식 정답 공개형)
- [D1 영상 디자인 스펙](docs/design/d1-video-design-spec.md) — 화면 좌표·크기·색·타이밍
- [D2 앱 UI 스펙](docs/design/d2-app-design-spec.md) — 토큰·레이아웃·상태 표기
- `docs/spikes/` — 결정의 근거가 된 실측 기록

## 개발 워크플로

작업은 **GitHub 이슈 단위**로 진행합니다. 새 이슈는 `status: needs-refinement`로
시작해 [리파인먼트](CONTRIBUTING.md#3-이슈-리파인먼트)를 거쳐 `status: ready`가 된 뒤
착수합니다. 자세한 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.
