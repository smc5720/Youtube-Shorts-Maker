# 편집 앱

`outputs/run-*/`를 프로젝트로 열고 `project.json`을 고쳐 저장하는 앱이다.
스택은 **Electron + React**, 백엔드는 앱이 자식으로 띄우는 **Python 프로세스 + stdio JSON
Lines**다 — 결정과 근거는 [스파이크 #25](../docs/spikes/25-app-framework.md), 화면 기준은
[D2 확정 스펙](../docs/design/d2-app-design-spec.md)에 있다.

**지금은 셸이다** (#26). 여는 것과 저장하는 것, 그리고 실패를 그리는 것까지 있고 화면 안은
비어 있다 — 장면 목록과 프리뷰는 #27, 문제 편집은 #28, 공통 편집은 #29, 렌더 실행은 #30이
채운다. 그래서 **`저장` 버튼을 누를 일이 아직 없다.** 편집을 만들어 내는 화면이 붙기
전까지, 저장 왕복이 실제로 도는지는 스모크가 확인한다.

## 실행

저장소 루트에 `.venv`가 있고 파이썬 의존성이 설치돼 있어야 한다 (루트 README 참조).
FFmpeg는 없어도 앱이 뜨고, 없다는 사실을 첫 화면에서 말한다.

```bash
cd app
npm install
npm start        # 빌드하고 앱을 띄운다
npm run smoke    # 사람 없이 완료 조건을 밟고 app/smoke/results.json을 남긴다
npm run typecheck
```

| 환경 변수 | 쓰임 |
| --- | --- |
| `SHORTS_PYTHON` | 백엔드를 띄울 인터프리터. 기본은 저장소의 `.venv` |
| `SHORTS_APP_LOG` | 앱 로그 파일. 기본은 `userData/app.log` |

**Windows에서 Electron의 stdout은 부모 셸에 붙지 않는다.** 앱이 빈 화면에서 멈췄다면
콘솔이 아니라 로그 파일을 본다 (스파이크 7장).

## 배치

```
app/
  electron/main.js      백엔드 spawn · IPC · 창 · 메뉴 · 닫기 확인
  electron/preload.js   렌더러에 열어 주는 것의 전부 (window.api)
  electron/smoke.js     --smoke 시나리오
  smoke/run.mjs         스모크 오케스트레이션 (앱을 세 번 띄운다)
  smoke/make_run.py     스모크가 열 run 디렉터리를 만든다
  src/                  React 렌더러 — App.tsx, components/, styles/
```

세 층의 경계가 이 앱의 뼈대다.

- **렌더러는 Node도 파일 시스템도 백엔드 프로세스도 보지 못한다.** `contextIsolation`과
  `sandbox`가 켜져 있고 통로는 preload가 연 `window.api`뿐이다
- **main은 프로젝트 내용을 들지 않는다.** 편집 상태는 렌더러가, 파일은 백엔드가 소유한다.
  main이 하는 일은 요청을 나르는 것과 창을 닫을지 판단하는 것뿐이다
- **백엔드는 프레임워크를 모른다.** stdin/stdout으로만 말하므로 프레임워크를 갈아도 그대로다
  (스파이크 8장)

## 알아 둘 것

- **`저장하지 않은 변경` 표시는 동기 IPC로 main에 간다.** 비동기로 보내면 화면이 먼저
  바뀌고 main이 나중에 알아서, 그 틈에 창을 닫으면 확인 없이 닫힌다 — 스모크가 실제로 1ms
  차이로 밟았다. `useLayoutEffect` + `sendSync`가 그 순서를 뒤집지 못하게 한다
- **저장은 임시 파일에 쓰고 교체한다** (`api.write_atomically`). 검증이 쓰기보다 먼저이므로
  계약을 어긴 편집은 파일에 닿지 않고, 쓰다 실패해도 원본이 남는다
- **앱이 죽으면 백엔드도 죽는다.** 그렇게 만드는 코드가 따로 있는 것이 아니라, stdin이
  닫히면 백엔드의 읽기 루프가 끝난다 (스파이크 4.2)
- **네트워크로 나가지 않는다.** 폰트는 `assets/fonts/`의 번들 Pretendard, 아이콘은 번들
  Lucide이고, `index.html`의 CSP가 `default-src 'none'`이다. main이 `file:` 아닌 요청을
  세어 두므로 스모크가 "시도조차 없었다"를 확인한다
