# 스파이크 #25 프로토타입

React(renderer) → preload → Electron main → Python 백엔드(stdio JSON Lines) → 화면.
**검증용이며 그대로 제품 코드로 쓰지 않는다** (이슈 #25의 Out of scope).

## 돌리는 법

```powershell
# 1. 앱이 열 run 디렉터리를 하나 만든다 (네트워크로 나가지 않는다)
.venv\Scripts\python.exe docs\spikes\25-app-framework\sample_run.py

# 2. 의존성 설치와 실행
cd docs\spikes\25-app-framework\prototype
npm install
npm start        # 창을 띄운다
npm run smoke    # 사람 없이 시나리오를 돌고 스크린샷·측정치를 남긴 뒤 종료한다
```

`npm run smoke`가 남기는 것은 상위 디렉터리의 `prototype-results.json`과
`prototype-screenshot.png`, 그리고 진행 로그 `prototype-smoke.log`다.

`SPIKE_PYTHON` 환경변수로 백엔드를 띄울 파이썬을 바꿀 수 있다 — 동결 배포본
(PyInstaller onedir)으로 같은 시나리오를 돌릴 때 쓴다.

## 파일

| 경로 | 역할 |
| --- | --- |
| `electron/main.js` | 창 생성, 백엔드 spawn, IPC 중계, 스모크 캡처 |
| `electron/preload.js` | 렌더러에 열어 주는 것의 전부 (`window.api`) |
| `src/App.jsx` | 장면 목록·프리뷰·렌더 진행률 화면 |
| `backend/server.py` | JSON Lines 디스패처. 출하 코드(`shorts_maker`)를 그대로 부른다 |
