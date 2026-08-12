# 스파이크 #25 — 앱 프레임워크와 Python 백엔드 연결 방식

- 이슈: [#25](https://github.com/smc5720/Youtube-Shorts-Maker/issues/25)
- 일자: 2026-08-12
- 재현:
  - 연결 방식 — `.venv\Scripts\python.exe docs\spikes\25-app-framework\harness.py`
    → 원자료 `docs/spikes/25-app-framework/results.json`
  - 프리뷰 비용 — `.venv\Scripts\python.exe docs\spikes\25-app-framework\preview_bench.py`
    → 원자료 `docs/spikes/25-app-framework/preview-results.json`
  - 프로토타입 — `docs/spikes/25-app-framework/prototype/README.md`
    → 측정치 `prototype-results.json`, 화면 `prototype-screenshot.png`
- 실측 환경: Windows 11 (10.0.26100), Python 3.13.2, Node 22.19.0, Electron 43.4.0
  (Chromium 150.0.7871.224 / Node 24.18.1), FFmpeg 8.1.2 full build, Intel 12세대 노트북

---

## 1. 결론

**앱은 Electron + React, 백엔드 연결은 자식 프로세스 stdio(JSON Lines)로 한다.**
Python 런타임은 PyInstaller onedir로 동봉하고, FFmpeg는 동봉하지 않는다.

근거 넷이 동시에 성립한다.

1. **Tauri는 이 환경에서 빌드할 수 없다.** Tauri v2 Windows 요구사항은 MSVC Build Tools +
   Rust 툴체인인데(공식 문서, 3.2) 개발 머신에 `cargo`·`rustc`·`cl.exe`가 모두 없다.
   Electron은 `npm install` 하나로 설치돼 이 스파이크의 프로토타입이 실제로 돌았다.
2. **Tauri의 크기 우위가 이 제품에서는 희석된다.** 앱이 무엇을 쓰든 Python 백엔드(26.9MB)와
   FFmpeg(전체 빌드 231MB, 사용자 설치 요구 시 0MB)를 함께 지고 가야 한다. 프레임워크 차이는
   Electron 런타임 348MB(다운로드 137.7MB) 대 Tauri 수 MB이고, 이것이 유일한 축이 아니다(3.1).
3. **stdio가 모든 축에서 낫거나 같다.** 왕복 지연 중앙값 0.033ms(HTTP 0.172ms, 파일 26.6ms),
   콜드 스타트 182ms(HTTP 673ms), 진행률 100프레임 0.6ms(파일 472ms). **그리고 부모를 강제
   종료했을 때 HTTP·파일 백엔드는 살아남았고 stdio 백엔드만 함께 죽었다**(4.2).
4. **전 경로가 실제로 돌았다.** React → main → Python → 화면 왕복 100회 중앙값 0.2ms,
   실제 `scenes.json` 11장면 로드, 정답 장면 프리뷰 PNG, 837프레임 최종 렌더 진행률까지
   프로토타입이 사람 없이 재현한다(5장, `prototype-screenshot.png`).

**`src/api.py`(PRD 11장)는 HTTP 서버가 아니라 JSON Lines 디스패처가 된다.** 이름은 그대로
두되 성격이 바뀐다 — 포트도, 로컬 인증도, 서버 수명 관리도 없다.

### 이 결론이 규정하지 않는 것

- **Tauri를 실측해서 진 것이 아니다.** 툴체인이 없어 빌드하지 못했고, 크기는 벤더 주장
  ("as little as 600KB", 공식 홈페이지)과 구조적 사실(시스템 WebView2 사용)에 기댔다.
  **되돌리는 비용이 작다는 것이 이 결론의 안전장치다** — 백엔드는 프레임워크를 모르는
  stdio 프로세스이므로, 설치 크기가 제품 요구가 되면 프론트만 갈아 끼운다(8장).
- **Windows에서만 쟀다.** macOS·Linux의 동봉 방식과 경로 해석은 확인하지 않았다.
- **서명·자동 업데이트·설치 프로그램을 다루지 않았다.** 배포 파이프라인은 별도 작업이다.
- **프리뷰의 목표 반응 속도를 만족시키지 못했다.** 프레임 하나에 0.8~2.4초가 들고 그 바닥은
  FFmpeg **프로세스 기동**이다(6장). 이 스파이크는 원인을 특정했을 뿐이고, 해법 선택은 #27이다.
- **보안 검토가 아니다.** 프로토타입은 `contextIsolation`을 켜고 렌더러에서 Node를 가렸지만
  CSP를 설정하지 않아 Electron의 보안 경고가 그대로 뜬다.

---

## 2. 실험 방법

세 가지를 따로 쟀다. **각 실험이 재는 경계가 다르다**는 것이 이 스파이크를 읽는 요령이다.

| 실험 | 경계 | 파일 |
| --- | --- | --- |
| 연결 방식 | Python ↔ Python (전송 계층만) | `harness.py` + `worker.py` |
| 전 경로 | React → Electron main → Python → 화면 | `prototype/` |
| 프리뷰 비용 | FFmpeg 명령 하나 | `preview_bench.py` |

세 실험 모두 **출하 코드를 다시 구현하지 않는다.** 하니스의 `presets` 호출은
`shorts_maker.assets`를, 프로토타입과 프리뷰 벤치는 `schemas`·`video_renderer`를 그대로
부른다. 프리뷰 명령은 최종 렌더 명령(`build_command`)에서 갈라져 나오므로 두 그림이
같은 필터 그래프를 지난다.

앱이 열 run 디렉터리는 `sample_run.py`가 만든다. 대역은 전 구간 스모크와 같은 둘
(`StubLLM`, `ToneTTS`)뿐이고 FFmpeg·스키마·타임라인·렌더는 실제 코드가 지난다.

---

## 3. 프레임워크 — Electron vs Tauri

### 3.1 크기 (측정: Electron / 인용: Tauri)

| 항목 | 값 | 출처 |
| --- | --- | --- |
| Electron 런타임 (풀어 놓은 상태) | **347.6MB** | 실측 — `node_modules/electron/dist` |
| Electron 런타임 (내려받는 zip) | **137.7MB** | 실측 — `electron-v43.4.0-win32-x64.zip` |
| Tauri 앱 | "as little as 600KB" | 인용 — Tauri 공식 홈페이지. **실측 아님** |
| Python 백엔드 (PyInstaller onedir + `assets/`) | **26.9MB** | 실측 (5장) |
| FFmpeg + ffprobe (gyan full build) | **462MB** (231.3 + 231.1) | 실측 — PATH의 실행 파일 |

**FFmpeg가 프레임워크 선택보다 크다.** 전체 빌드를 동봉하면 Electron이든 Tauri든 500MB를
넘고, 동봉하지 않으면 두 후보의 차이(약 340MB)가 설치 크기의 전부가 된다. 이 스파이크는
**FFmpeg를 동봉하지 않는 쪽**을 택한다(5.2).

### 3.2 툴체인과 배포 난이도

| 기준 | Electron | Tauri |
| --- | --- | --- |
| 필요한 툴체인 | Node/npm만 | Node + **Rust + MSVC Build Tools** (공식 문서) |
| 이 머신의 상태 | 갖춰짐 (Node 22.19.0) | **`cargo`·`rustc`·`cl.exe` 없음** |
| 렌더 엔진 | Chromium 150을 앱이 고정 | 시스템 WebView2 (이 머신 151.0.4129.72 설치됨) |
| Python 동반 실행 | `child_process.spawn` — 프로토타입에서 동작 | `externalBin` + 셸 플러그인 권한, 파일명에 타깃 트리플 접미사 필요 (공식 문서) |
| 개발 편의 | 언어 둘 (JS + Python) | 언어 셋 (JS + Rust + Python) |

MSVC Build Tools 설치는 회사 관리 장비에서 관리자 권한을 요구할 수 있다. **"설치하면 된다"가
아니라 "지금 이 저장소에서 빌드가 되는가"가 기준이다.**

렌더 엔진이 고정되는 것은 이 제품에서 이점 쪽이다 — 세로형 캔버스 프리뷰(#27)가 사용자의
WebView2 버전에 따라 다르게 그려지면 편집 화면과 최종 영상의 차이를 판정할 수 없다.

---

## 4. 연결 방식 — stdio vs 로컬 HTTP vs 파일

`worker.py`의 같은 처리부를 세 전송으로 감싸 쟀다. 원자료는 `results.json`.

### 4.1 측정치

| 항목 | stdio | 로컬 HTTP | 파일 교환 |
| --- | --- | --- | --- |
| 콜드 스타트 (프로세스 → 첫 응답) | **182ms** | 673ms | 279ms |
| 작은 왕복 200회 — 중앙값 | **0.033ms** | 0.172ms | 26.64ms |
| 작은 왕복 200회 — p95 | **0.059ms** | 0.286ms | 52.32ms |
| 1MB 왕복 20회 — 중앙값 | 12.37ms | **9.08ms** | 33.75ms |
| 1MB 왕복 20회 — p95 | **13.24ms** | 31.25ms | 58.22ms |
| 진행률 100프레임 총 시간 | **0.60ms** | 1.96ms | 472.48ms |
| 부모 강제 종료 후 백엔드 생존 | **아니오** | 예 | 예 |

1MB 왕복 중앙값만 HTTP가 앞선다(소켓 대 파이프). p95에서 뒤집히므로 **큰 payload가 HTTP를
택할 이유가 되지 않는다.**

### 4.2 고아 프로세스

중간 부모가 백엔드를 띄우고, 하니스가 그 부모만 `taskkill /F`로 죽인 뒤(`/T` 없이) 백엔드의
하트비트 파일이 계속 갱신되는지 봤다.

- **stdio**: stdin이 닫히며 EOF → 백엔드가 스스로 끝났다.
- **HTTP·파일**: 부모가 사라져도 계속 돌았다. 앱이 크래시하면 **렌더 중인 FFmpeg를 안은 채로
  백엔드가 남는다.** 살려 두려면 pid 파일·포트 점유 확인 같은 수명 관리 코드가 앱에 붙는다.

### 4.3 stdio가 지불하는 비용

측정 도중 실제로 밟은 것들이다. 셋 다 한 줄로 막을 수 있지만, 모르면 원인을 찾는 데 시간이 든다.

1. **Windows의 파이프 stdio 기본 인코딩은 콘솔 코드페이지(한국어 Windows는 cp949)다.**
   한글이 든 응답이 cp949로 나가고 Node는 UTF-8로 읽어 **에러 없이 값만 깨진다.**
   `sys.stdout.reconfigure(encoding="utf-8")` 두 줄이 필요하다.
2. **자식으로 띄운 FFmpeg가 백엔드의 stdin을 먹는다.** ffmpeg는 stdin을 조작 입력으로 읽으므로
   `stdin`을 물려주면 프로토콜 줄을 가져가고 렌더가 끝나지 않는다 — 이 스파이크의 첫 스모크가
   3분 타임아웃으로 죽은 원인이 이것이었다. `stdin=subprocess.DEVNULL`.
3. **바이너리는 base64를 지나야 한다.** 프리뷰 PNG 44~58KB가 실제로 문제없이 건너갔고
   (5장), 1MB 왕복이 12ms이므로 프리뷰 해상도에서는 여유가 있다.

HTTP를 택했다면 대신 지불했을 것 — 포트 선정과 충돌 처리, 로컬 포트에 붙는 다른 프로세스를
막을 토큰, 그리고 4.2의 수명 관리.

---

## 5. Python 런타임 배포

### 5.1 PyInstaller onedir — 동작 확인함

```powershell
.venv\Scripts\python.exe -m PyInstaller --noconfirm --name shorts-backend `
  --paths src --collect-submodules shorts_maker --hidden-import shorts_maker.types.quiz `
  docs\spikes\25-app-framework\prototype\backend\server.py
Copy-Item -Recurse assets dist\shorts-backend\assets
```

| 항목 | 값 |
| --- | --- |
| onedir 크기 | 22.4MB (파일 83개) |
| `assets/` 포함 | **26.9MB** |
| 준비 시간 (`ready`까지, 워밍업 후) | 196~291ms — 가상환경 파이썬(178~336ms)과 같은 대 |
| 첫 실행 (파일 캐시 콜드) | 1384ms |
| 동결 상태에서 프리뷰 | 정상 — PNG 44,641바이트 |

**`assets/`를 실행 파일 옆에 둬야 한다.** `assets.ASSETS_DIR`은 `shorts_maker/assets.py`에서
두 단계 위를 보므로, onedir에서는 `dist/shorts-backend/assets`가 그 자리다. 넣지 않으면
프리뷰가 `프리셋 파일이 없다: ...\dist\shorts-backend\assets\backgrounds\presets.json`으로
실패한다 — **경로를 말해 주므로 진단은 쉽다.** `assets.py` 도입부가 미뤄 둔 "앱 동봉 시점의
경로 해석"이 이 한 줄로 정해진다.

`edge-tts`·`pyyaml`은 별도 hook 없이 들어갔고, 타입 레지스트리처럼 **동적으로 import되는 것은
`--hidden-import`가 필요하다** — 타입이 늘면 이 목록도 는다(#8의 레지스트리 구조 때문이다).

### 5.2 FFmpeg는 동봉하지 않는다

- 크기: 전체 빌드가 462MB로 앱 전체를 압도한다(3.1).
- 라이선스: 배포되는 빌드마다 GPL/LGPL 조건이 다르고, 저장소는 이미 **재배포 조건이 없는
  에셋만 담는다는 기준**을 갖고 있다(PRD 8장, #38). 바이너리 동봉은 그 기준을 다시 여는 일이다.
- 현 상태 유지: CLI가 이미 `ffmpeg`·`ffprobe`를 PATH에서 찾고 없으면 그 사실을 말하며 멈춘다.
  앱도 같은 계약을 쓰고, 첫 실행에서 없으면 안내한다(#26).

재검토 조건 — 설치 경험이 제품 요구가 되면 **essentials 빌드**(전체 빌드보다 훨씬 작다)의
라이선스와 크기를 그때 잰다. 이 스파이크는 재지 않았다.

---

## 6. 프리뷰와 최종 렌더의 분리

PRD 7.9는 "빠른 프리뷰와 최종 렌더링을 분리한다"고만 적는다. 그 분리가 무엇을 사는지 쟀다.
원자료는 `preview-results.json` (27.9초 / 837프레임 / 11장면 / 1080x1920, 3회 중앙값).

| 측정 | 값 |
| --- | --- |
| 최종 렌더 1회 | 3,493ms |
| 프리뷰 1프레임 — t=0.5s | 810ms |
| 프리뷰 1프레임 — t=13.95s | 2,006ms |
| 프리뷰 1프레임 — t=27.4s | 2,352ms |
| 같은 프레임을 540x960으로 | 2,027ms (**싸지지 않는다**) |
| 같은 프레임을 270x480으로 | 2,015ms |
| 입력측 `-ss` + `-copyts` | 1,483ms — 출력측(2,352ms)보다 빠르고 **결과 PNG가 바이트까지 동일** |
| 배경만 1프레임 (오버레이 0개) | 1,468ms |
| `ffmpeg -version` (일 없음) | **1,117~1,326ms** |

**바닥은 필터가 아니라 프로세스 기동이다.** 아무 일도 하지 않는 `ffmpeg -version`이 1초를
넘는다(같은 머신에서 `cmd /c ver`는 222ms). 231MB짜리 정적 전체 빌드를 매번 로드하는 비용이고,
그래서 해상도를 1/4로 줄여도 프리뷰가 빨라지지 않는다.

따라서 **#27은 "프레임마다 ffmpeg를 새로 띄우는" 구조를 택하면 안 된다.** 남는 길은 셋이다.

1. **장면 대표 프레임 캐시** — 렌더/열기 시점에 장면당 한 장을 만들어 두고, 편집이 닿은
   장면만 다시 만든다. 구현이 가장 단순하고 이 스파이크의 권고다.
2. **상주 FFmpeg 프레임 서버** — `image2pipe`로 프레임을 계속 받는 프로세스를 하나 띄운다.
   기동 비용을 한 번만 내지만 프로세스 수명·에러 복구가 앱에 붙는다. 별도 검증이 필요하다.
3. **더 작은 FFmpeg 빌드** — 기동 비용의 원인을 줄인다. 5.2의 재검토와 같은 자리다.

세 길 모두 **명령을 `build_command`에서 갈라 만드는 지금 방식과 양립한다.** 프로토타입은
최종 렌더 명령에서 오디오 체인과 인코더 설정만 들어내 PNG 한 장을 뽑았고, 그래서 프리뷰가
최종과 다른 그림을 낼 수 없다.

### 6.1 렌더러에 열려야 하는 것 (#27·#30에 넘긴다)

프로토타입 백엔드가 **명령 리스트를 손으로 수술한다.** 제품에서는 `video_renderer`가 두 가지를
가져야 한다.

- **프리뷰 진입점** — 시각 하나를 받아 프레임 하나를 내는 함수. 앱이 필터 문자열을 파싱하는
  구조는 렌더러가 바뀌는 순간 조용히 깨진다.
- **진행률 보고** — `-progress pipe:1` 파싱은 렌더러의 계약이다. 프로토타입에서 3초 렌더에
  진행 이벤트가 5개 왔다(약 0.5초 간격).

수술하다 밟은 것 둘 —

- `-map [audio]`만 빼면 `alimiter`의 출력이 연결되지 않아 그래프 바인딩이 실패한다.
  **오디오 체인을 필터 그래프에서 함께 들어내야 한다.**
- `-c:v libx264`를 남긴 채 확장자를 `.png`로 주면 **경고 없이 H.264 비트스트림이 그 파일에
  쓰인다.** 눈으로 보기 전까지 성공처럼 보인다.

---

## 7. 프로토타입이 지난 경로

`prototype-results.json` / `prototype-screenshot.png` (Electron 43.4.0, Chromium 150).

| 단계 | 값 |
| --- | --- |
| 백엔드 준비 (`spawn` → `ready`) | 273~333ms |
| `open` — 실제 `project.json` + `scenes.json` | 11장면 / 27.9초 |
| IPC 왕복 100회 (React → main → Python → React) | 중앙값 **0.2ms**, 최대 1ms |
| 프리뷰 PNG (정답 장면) | 1,050ms / 57KB |
| 최종 렌더 | 2,973ms / 837프레임 / 0.4MB |

**한글이 화면까지 온전히 왔다**(4.3의 인코딩 함정을 고친 뒤). 화면의 장면 목록·시각·본문은
전부 Python이 보낸 값이고, 렌더러에는 파일 시스템도 FFmpeg도 `shorts_maker`도 없다.

렌더가 도는 동안에도 백엔드는 다른 요청을 받는다 — 오래 걸리는 메서드만 스레드로 보내고
메인 루프를 비워 두면 된다. **PRD 7.9의 "프리뷰와 최종 렌더의 분리"가 프로세스를 하나 더
띄우는 문제가 아니라는 뜻이다.**

관측한 함정 둘 —

- **Windows에서 Electron의 stdout은 부모 셸에 붙지 않는다.** `console.log`만 두면 스모크가 왜
  멈췄는지 볼 수 없다. main에서 파일로도 남기고, 렌더러의 예외는 `console-message`·
  `preload-error`·`render-process-gone`으로 끌어와야 한다.
- **스모크는 측정치를 먼저 쓰고 캡처를 나중에 한다.** 캡처가 막히면 결과까지 함께 잃는다.

---

## 8. 되돌리는 비용

이 결정이 틀렸을 때 무엇을 다시 쓰는가.

| 바뀌는 것 | 다시 쓰는 것 |
| --- | --- |
| Electron → Tauri | 프론트 셸(main/preload 상당)뿐. **백엔드는 그대로다** — 프레임워크를 모르는 stdio 프로세스이고, Tauri의 `externalBin` 사이드카도 stdout/stdin으로 말한다 |
| stdio → HTTP | 백엔드의 전송 계층과 앱의 수명 관리. 처리부(`handle`)는 그대로다 |
| PyInstaller → 임베디드 Python | 배포 스크립트. 실행 파일 경로만 바뀐다 (`SPIKE_PYTHON`이 프로토타입에서 그 자리다) |

**전송을 프로세스 경계에 둔 것이 세 칸을 모두 작게 만든다.** 반대로 렌더러가 Python을 직접
부르는 구조(예: 프론트에 FFmpeg 호출을 두는 것)를 택했다면 프레임워크 교체가 전면 재작성이 된다.

---

## 9. 후속 이슈에 넘기는 것

- **#26 (앱 골격)** — main/preload/백엔드 3층 배치와 JSON Lines 프로토콜은 프로토타입 그대로
  가져가되, `api.py`를 제품 코드로 새로 쓴다. FFmpeg 미설치 안내가 첫 실행 경로에 필요하다(5.2).
- **#27 (프리뷰)** — 6장의 세 길 중 하나를 고른다. 프레임마다 프로세스를 띄우는 구조는 이미
  기각됐다. 렌더러에 프리뷰 진입점이 열려야 한다(6.1).
- **#29 (편집)** — 편집 상태 필드를 `project.json`에 여는 일이 이제 열렸다. PRD 7.10이
  "앱 프레임워크가 정해진 뒤 추가한다"고 미뤄 둔 항목이다.
- **#30 (앱에서 렌더)** — 진행률과 취소는 프로토타입에서 동작한다(취소는 `JOBS`에서 프로세스를
  찾아 `terminate`). 실패 처리와 재실행은 #36과 겹치므로 경계를 정해야 한다.
- **배포** — 서명·설치 프로그램·자동 업데이트는 이 스파이크 밖이다. FFmpeg essentials 빌드
  재검토도 그때 함께 본다(5.2).
