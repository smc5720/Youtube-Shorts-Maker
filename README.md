# Youtube-Shorts-Maker

유튜브 쇼츠 제작을 위한 자동 생성 엔진과 세로 영상 편집 앱.

주제·원문 파일·링크 중 하나를 입력하면 콘텐츠 생성 → 검증 → 장면 구성 → 음성(TTS) →
자막 → 렌더를 거쳐 **1080×1920 세로 영상(MP4)** 초안이 나옵니다. 사용자는 앱에서 그 초안을
열어 문제·자막·장면 길이·배경을 고친 뒤 최종 영상을 내보냅니다.

## 파이프라인

```
입력(주제 / 원문 파일 / 링크)
  → 콘텐츠 생성(LLM) → 블라인드 검증 → 장면 구성 → 메타데이터
  → 음성 합성(TTS) → 실측 길이로 타임라인 확정 → 자막
  → 렌더 → final_short.mp4        ← 여기서 앱으로 열어 편집·재렌더
```

CLI 한 번으로 `final_short.mp4`까지 나오고, 앱은 **이미 만들어진 run 디렉터리를 여는 편집
도구**입니다 — 앱이 처음부터 생성하지는 않습니다.

## MVP 방향

1차 목표는 YouTube 자동 업로드가 **아니라**, 위 파이프라인으로 영상 초안을 만들고 앱에서
편집·내보내기하는 것입니다. 업로드 자동화는 YouTube Data API 정책과 OAuth 구성을 별도
검토하는 것까지가 현재 범위이고, 구현 착수를 전제하지 않습니다.

## 준비물

| 필요한 것 | 왜 |
| --- | --- |
| Python 3.11 이상 | 개발 환경은 3.13에서 확인 |
| **FFmpeg / ffprobe** (PATH) | 음성 길이 실측과 영상 인코딩. 파이썬 래퍼 없이 명령을 직접 부릅니다 |
| `claude` CLI (로그인 상태) | 기본 콘텐츠 생성·검증 provider. `llm.providers.claude_cli.binary`로 바꿉니다 |
| 네트워크 | 기본 TTS(Edge TTS)가 외부로 나갑니다 |
| Node.js (앱만) | Electron 43 · Vite 8을 돌릴 수 있는 버전 |

## 설치

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows. macOS·Linux는 source .venv/bin/activate
pip install -e ".[dev]"
```

`[dev]`에는 링크 입력용 추출기(`trafilatura`)가 함께 들어 있습니다. 실행만 할 것이라면
`pip install -e .`(기본) 또는 `pip install -e ".[source]"`(링크 입력 포함)입니다.

## 5분 사용 흐름

```bash
# 1. 주제 하나로 초안 만들기
shorts-maker --topic "세계 지리 상식 퀴즈"

# 2. 나온 run 디렉터리 확인 (outputs/run-20260820-101500/ 형태)
ls outputs

# 3. 앱으로 열어 편집하고 최종 렌더
cd app && npm install && npm start
```

앱에서 `Ctrl+O`로 그 run 디렉터리를 열고, 문제·장면을 고친 뒤 **재생성 → 렌더**를 실행하면
같은 run 디렉터리의 `final_short.mp4`가 갱신됩니다.

## CLI

`shorts-maker` 또는 `python -m shorts_maker`로 부릅니다. 실행마다
`outputs/run-{timestamp}/` 디렉터리가 새로 생기고 이전 결과는 그대로 보존됩니다. 실행 로그는
`run.log`, 그 실행이 실제로 쓴 설정 전체는 `config.used.yaml`로 남습니다.

`config.used.yaml`은 기록이지 입력이 아닙니다 — 고쳐도 그 run이 다시 돌지 않습니다. 대신
그대로 복사해 `--config`로 넘기면 같은 설정으로 새 run을 만들 수 있습니다.

### 입력 네 갈래 — 정확히 하나가 필수

**주제 한 줄** — 지금 가장 완성도가 높은 경로입니다.

```bash
shorts-maker --topic "세계 지리 상식 퀴즈"
```

**원문 파일** — 첫 비어 있지 않은 줄이 제목이 되고, 받은 원문 전체는 그 run의
`source.json`에 남습니다.

```bash
shorts-maker --text-file 기사.txt
```

**링크** — 이 경로에는 **추출기가 따로 필요합니다**. 패키지 12개에 약 55MB라 기본 설치에
넣지 않았고, 없는 상태로 `--url`을 주면 run 디렉터리를 만들기 전에 설치 방법을 안내하며
멈춥니다.

```bash
pip install -e ".[source]"    # 위에서 [dev]로 설치했다면 이미 들어 있습니다
shorts-maker --url "https://news.example.com/article/1"
```

받아들이는 것은 **로그인 없이 열리는 정적 HTML 페이지**입니다. 유료·로그인·JS 렌더링
페이지는 지원하지 않고, 그때는 HTTP 상태·`Content-Type`·추출된 본문 길이 셋 중 하나에 걸려
거부되며 `--text-file`을 안내합니다. 통과한 페이지는 제목과 글자 수가 콘솔에 나오고 본문
전체가 `source.json`에, 도착한 주소가 `metadata.json`의 `source`에 남습니다 — 본문이
본문인지 보는 것은 사람입니다.

> **`--text-file`·`--url`은 지금 원문을 기록만 합니다.** 콘텐츠 생성기에 가는 것은 제목이고
> 본문은 `source.json`에만 남습니다 — 본문을 소비하는 요약·대본 단계가 아직 없어서이며,
> 그 사실이 `run.log`에 남습니다.

**이어 돌리기** — 실행이 도중에 실패했다면 그 run 디렉터리를 이어서 돌립니다. 없는 산출물만
만들고 있는 것은 그대로 쓰므로, 렌더만 실패했다면 LLM 호출도 음성 합성도 다시 하지 않습니다.
설정은 그 run의 `config.used.yaml`에서 오고, 사람이 앱에서 고친 `project.json`은 손대지
않습니다.

```bash
shorts-maker --resume outputs/run-20260820-101500
shorts-maker --resume outputs/run-20260820-101500 --force render   # 영상이 있어도 다시 인코딩
```

**모델은 부르지 않습니다.** 그래서 `config.used.yaml`·`scenes.json`·`metadata.json` 중
하나라도 없는 run(콘텐츠 생성이나 메타데이터 단계에서 멈춘 run)은 이어 돌릴 수 없고, 무엇이
없는지 알려주며 멈춥니다 — 그때는 새로 실행합니다. 콘텐츠를 **고쳐서** 반영하려는 것이라면
이어 돌리기가 아니라 앱의 재생성입니다.

### 인자

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
| `--force` | (없음) | `--resume`에서 산출물이 있어도 다시 만들 단계: `segments` / `render`. 여러 번 지정 가능 |
| `-v`, `--verbose` | off | 디버그 로그를 콘솔에도 출력 (`run.log`에는 항상 남음) |
| `--version` | | 버전을 출력하고 종료 |

위 표의 `--type`·`--out`·`--config`·`--fail-on-flagged`는 **생성 실행에서만 뜻이 있습니다** —
`--resume`과 함께 주면 거부합니다. 이어 돌리기는 그 run의 설정 기록과 산출물로 돌기 때문에,
받아 놓고 무시하면 반영됐다고 오해하게 됩니다.

### 종료 코드

| 코드 | 뜻 |
| --- | --- |
| 0 | 성공. **검수 필요(flagged) 항목이 있어도 영상이 나오면 0입니다** |
| 1 | 실행 중 오류 (LLM·TTS·렌더 실패 등). 그 지점까지의 산출물은 남습니다 |
| 2 | 인자가 잘못됨 (argparse) |
| 3 | 설정 오류. run 디렉터리를 만들기 **전에** 걸립니다 |
| 4 | `--fail-on-flagged`를 준 실행에서 검수 필요 항목이 있음 |

### 산출물

| 파일 | 내용 |
| --- | --- |
| `run.log` | 그 실행의 전체 로그. 무엇이 왜 없는지도 여기서 답합니다 |
| `config.used.yaml` | 실제로 쓴 설정 전체. **유일하게 JSON이 아닌 산출물** |
| `quiz.json` | 문제 세트와 블라인드 검증 결과(`verify.status` · `confidence`) — 퀴즈 타입 |
| `scenes.json` | 장면 목록과 확정된 길이·타임라인. **공통 파이프라인이 읽는 유일한 콘텐츠 계약** |
| `metadata.json` | 업로드용 제목·설명·태그 후보 (링크 입력이면 `source`에 출처) |
| `audio/seg-*.mp3` | 낭독 장면별 세그먼트 오디오 |
| `audio/segments.json` | 같은 run 안에서 사람이 교체한 세그먼트를 지키는 재사용 기록 |
| `voice.mp3` | 세그먼트를 타임라인 위에 배치한 낭독 트랙 |
| `captions.srt` | 외부 재생기용 자막 트랙 (화면 번인과는 다른 층) |
| `project.json` | 사람이 앱에서 얹는 편집(`render.scene_overrides` · `review`)이 사는 자리 |
| `final_short.mp4` | 1080×1920 · h264 · yuv420p · 30fps, 오디오 aac 한 스트림 |
| `source.json` | 받은 원문 전체와 출처. **`--text-file`·`--url` 경로만** |

`script.txt`·`summary.json`·`source.json`은 **타입과 입력 경로에 따라 생기지 않으며, 없는
것이 실패가 아닙니다** — 퀴즈 + `--topic` 경로에서는 만들지 않습니다.

화면에는 후킹 → 질문 → 카운트다운(숫자와 진행 바) → 정답 확대·색 전환 → 해설 자막 → CTA가
번인되고, 카운트다운 비프와 정답 효과음이 함께 믹스됩니다.

## 설정

설정 파일은 선택입니다. 없으면 전부 기본값으로 동작합니다. 값을 바꾸려면
[`config.example.yaml`](config.example.yaml)을 `config.yaml`로 복사한 뒤 바꿀 키만 남기면
됩니다 — 나머지 키는 기본값을 씁니다.

```bash
cp config.example.yaml config.yaml
```

우선순위는 **CLI 오버라이드 > `config.yaml` > 기본값**입니다. 알 수 없는 키나 타입이 맞지
않는 값은 run 디렉터리를 만들기 **전에** 오류로 걸러지고, 발견된 문제를 한 번에 모아서
보여줍니다. 해석된 설정 전체는 `run.log`와 그 run의 `config.used.yaml`에 남습니다.

자주 손대는 키만 추려 두면 이렇습니다. 각 키의 뜻과 제약은 `config.example.yaml`의 주석에
있습니다.

| 하려는 것 | 키 |
| --- | --- |
| 문제 수·카운트다운 길이 바꾸기 | `quiz.question_count`(3~5) · `quiz.countdown_sec` |
| 배경·자막 색 바꾸기 | `render.background` · `render.caption_style` |
| CTA 두 줄 바꾸기 | `render.cta_punch` · `render.cta_tail` |
| 음성 바꾸기 | `tts.voice` |
| 생성·검증 모델 갈라 쓰기 | `llm.generator.model` · `llm.verifier.model` (같으면 블라인드 검증력이 약해집니다) |
| 검수 임계값 조정 | `llm.verifier.confidence_threshold` · `llm.verifier.runs` |
| 배경음악 깔기 | `audio.music`(경로) · `audio.music_volume` · `audio.music_duck` |
| 배경에 zoom/pan 걸기 | `render.motion.kind` · `render.motion.strength` |

**배경음악과 배경 모션은 config에만 있습니다** — 앱에 고르는 칸이 없습니다. 둘 다 기본이
꺼짐이고, 음악은 기본 제공하지 않으므로 경로를 직접 적어야 하며 그 경로와 라이선스 확인
책임이 `run.log`에 남습니다. 모션은 이미지·영상 배경에만 붙습니다(프리셋·단색 배경은 어느
값을 줘도 프레임이 같아 필터가 붙지 않습니다).

## 편집 앱

`app/`에 Electron + React 앱이 있습니다. **이미 있는 run 디렉터리를 열어 편집하는 도구**이고,
저장소 루트에 `.venv`와 파이썬 의존성이 있어야 백엔드가 뜹니다. FFmpeg는 없어도 앱이 뜨고,
없다는 사실을 첫 화면에서 말합니다.

```bash
cd app
npm install
npm start          # 빌드하고 앱을 띄운다
```

사용 흐름은 이렇습니다.

1. **열기** — `Ctrl+O`로 `outputs/run-*/`를 고릅니다. 스키마를 어긴 디렉터리는 열리지 않고,
   그때 이미 열려 있던 프로젝트는 그대로 남습니다.
2. **장면 화면** — 장면 목록 · 세로형 프리뷰 · 속성 패널 3분할입니다. 장면을 고르면 대표
   프레임이 뜨고(3~5ms), 값을 고치면 프레임을 다시 만들어 2~3초 기다립니다. 속성 패널에서
   장면 길이 · 자막 문구 · 텍스트 오버레이 · 자막 스타일 · 배경 · 볼륨을 고칩니다.
   자막 스타일 · 배경 · 볼륨은 **장면별이 아니라 프로젝트 전역**입니다.
3. **문제 편집 화면** — 질문·정답·해설·난이도·카운트다운을 고치고, 순서를 바꾸고, 문제를
   더하거나 지웁니다. 이 화면에는 **프리뷰가 없습니다** — 콘텐츠를 고쳐도 `scenes.json`은
   그대로라 그림이 바뀌지 않습니다.
4. **재생성** — 낭독 문구를 고치거나 문제 순서·개수를 바꾸면 앱은 낡았다고 **표시만** 하고,
   장면·오디오·자막을 다시 만드는 것은 재생성 버튼입니다. 낡음은 두 종류입니다(자막만 /
   음성까지).
5. **렌더** — 확인이 필요한 문제(`flagged`·`unverified`)가 있으면 체크박스 하나가 시작을
   막습니다. 낡음·짧은 장면 길이·저장하지 않은 변경은 같은 카드에 서지만 렌더를 막지
   않습니다. **렌더는 앱이 들고 있는 프로젝트로 도므로 저장하지 않은 편집도 결과에
   들어갑니다.** 한 번에 하나만 돌고, 두 번째 요청은 백엔드가 거절합니다.
6. **저장** — `Ctrl+S`. 사람이 얹은 편집은 전부 `project.json`에 들어가고 `scenes.json`은
   앱이 쓰지 않습니다. 저장하지 않고 창을 닫으면 확인을 묻습니다.

| 환경 변수 | 쓰임 |
| --- | --- |
| `SHORTS_PYTHON` | 백엔드를 띄울 인터프리터. 기본은 저장소의 `.venv` |
| `SHORTS_APP_LOG` | 앱 로그 파일. 기본은 `userData/app.log` |

**Windows에서 Electron의 stdout은 부모 셸에 붙지 않습니다.** 앱이 빈 화면에서 멈췄다면
콘솔이 아니라 로그 파일을 봅니다. 배치와 경계는 [`app/README.md`](app/README.md)에 있습니다.

## 테스트

venv의 인터프리터로 돌립니다.

```bash
.venv/Scripts/python.exe -m pytest                          # 전체
.venv/Scripts/python.exe -m pytest tests/test_e2e_smoke.py  # 전 구간만 (약 30초, 렌더 3회)
cd app && npm run smoke && npm run typecheck                # 앱
```

전 구간 스모크(`tests/test_e2e_smoke.py`)는 주제 하나로 `final_short.mp4`까지 지납니다. LLM과
TTS만 픽스처로 바꾸고 FFmpeg와 `ffprobe`는 진짜를 쓰므로 네트워크 없이 돌고, FFmpeg가 없는
환경에서는 그 파일만 건너뜁니다. 앱 스모크의 프리뷰·렌더 시나리오도 FFmpeg를 요구합니다.

### 로컬 실전 모드

픽스처 모드가 지나지 않는 것 — **모델이 낸 문제의 품질, 실제 음성, 화면에 그려진 결과** — 은
사람이 봅니다. 실제 provider로 한 번 돌립니다(`claude` CLI 로그인 + 네트워크 필요).

```bash
shorts-maker --topic "세계 지리 상식" --verbose
```

| 대상 | 확인할 것 |
| --- | --- |
| 산출물 | 위 산출물 표의 파일이 모두 있는가. `script.txt`·`summary.json`·`source.json`은 이 경로에서 **생기지 않아야** 합니다 |
| 규격 | `ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,pix_fmt -of default=nw=1 outputs/run-*/final_short.mp4` → 1080×1920 / `h264` / `yuv420p` / `30/1`, 오디오는 `aac` 한 스트림 |
| 화면 | 후킹 → 질문(`Q1/N`) → 카운트다운 숫자와 진행 바 → 정답 확대·색 전환 → 해설 자막 → CTA 두 줄이 순서대로 나오는가. 글자가 안전 영역을 벗어나거나 줄바꿈이 어색하지 않은가 |
| 소리 | 낭독이 질문·정답 장면 시작과 맞는가, 카운트다운 비프가 초마다 들리는가, 정답 효과음이 정답 장면 앞머리에 오는가, 음량이 튀지 않는가 |
| 자막 | `captions.srt`를 재생기에 얹었을 때 타임코드가 화면과 맞는가 |
| 검수 | 콘솔의 `검수 필요` 경고와 `quiz.json`의 `verify.status`·`source`. **flagged가 있어도 영상은 나오고 종료 코드는 0입니다** — 멈추려면 `--fail-on-flagged`(종료 코드 4)를 붙입니다 |

내용이 마음에 들지 않으면 같은 명령을 다시 실행합니다. 새 run 디렉터리가 생기고 이전
결과는 그대로 남습니다.

## 문제가 생겼을 때

| 증상 | 볼 곳 |
| --- | --- |
| 실행이 시작도 못 하고 멈춘다 (종료 코드 3) | 설정 오류입니다. 콘솔이 문제 키를 모아서 보여줍니다 |
| `ffmpeg`/`ffprobe`를 찾을 수 없다고 한다 | FFmpeg가 PATH에 있는지 확인합니다 |
| 렌더만 실패했다 | `--resume`으로 이어 돌립니다. LLM·TTS를 다시 부르지 않습니다 |
| `--url`이 거부됐다 | 로그인·유료·JS 렌더링 페이지입니다. 본문을 파일로 저장해 `--text-file`로 줍니다 |
| 앱이 빈 화면에서 멈췄다 | `SHORTS_APP_LOG`(기본 `userData/app.log`)를 봅니다 |
| 앱에서 고쳤는데 영상이 그대로다 | 재생성을 실행했는지, 그다음 렌더를 돌렸는지 봅니다 |
| 무엇이 왜 없는지 모르겠다 | 그 run의 `run.log`가 답합니다 |

## 문서

- [PRD](docs/PRD.md) — 기획과 확정 결정 (14장이 결정 목록)
- [기여 가이드](CONTRIBUTING.md) — 이슈 기반 작업 방식, 라벨 체계, 이슈 리파인먼트 절차
- [퀴즈 타입 스펙](docs/types/quiz.md) — 상식/지식 퀴즈 쇼츠 (LLM 자동 생성, 주관식 정답 공개형)
- [D1 영상 디자인 스펙](docs/design/d1-video-design-spec.md) — 화면 좌표·크기·색·타이밍
- [D2 앱 UI 스펙](docs/design/d2-app-design-spec.md) — 토큰·레이아웃·상태 표기
- [`app/README.md`](app/README.md) — 앱의 배치와 경계
- `docs/spikes/` — 결정의 근거가 된 실측 기록

## 개발 워크플로

작업은 **GitHub 이슈 단위**로 진행합니다. 새 이슈는 `status: needs-refinement`로
시작해 [리파인먼트](CONTRIBUTING.md#3-이슈-리파인먼트)를 거쳐 `status: ready`가 된 뒤
착수합니다. 자세한 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.
