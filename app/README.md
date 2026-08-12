# 편집 앱

`outputs/run-*/`를 프로젝트로 열고 `project.json`을 고쳐 저장하는 앱이다.
스택은 **Electron + React**, 백엔드는 앱이 자식으로 띄우는 **Python 프로세스 + stdio JSON
Lines**다 — 결정과 근거는 [스파이크 #25](../docs/spikes/25-app-framework.md), 화면 기준은
[D2 확정 스펙](../docs/design/d2-app-design-spec.md)에 있다.

화면이 둘이다.

- **장면** (#26 셸 + #27) — 장면 목록 · 세로형 프리뷰 · 속성 패널 3분할. 장면을 고르면 그
  장면의 대표 프레임이 뜬다. **여기는 아직 읽기 전용이다** — 자막 스타일·배경·길이·볼륨은
  #29가, 렌더 실행은 #30이 속성 패널과 헤더에 채운다
- **문제 편집** (#28) — 좌 목록 + 우 폼 2분할. 질문·정답·해설·난이도·카운트다운을 고치고,
  순서를 바꾸고, 문제를 더하고 지운다. **프리뷰가 없다** — 콘텐츠를 고쳐도 `scenes.json`은
  그대로라 그림이 바뀌지 않는다

**앱에서 고친 것이 곧바로 영상에 반영되지는 않는다.** 낭독 문구를 고치거나 순서·개수를 바꾸면
앱은 오디오·자막·장면이 낡았다고 **표시만** 한다. 다시 만드는 것은 #77이다.

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
  smoke/run.mjs         스모크 오케스트레이션 (앱을 여섯 번 띄운다)
  smoke/make_run.py     스모크가 열 run 디렉터리 둘을 만든다 (정상 길이 / 상한 초과)
  src/                  React 렌더러 — App.tsx, scenes.ts, components/, styles/
  src/types/            **타입 전용 편집기.** 콘텐츠 필드를 아는 유일한 앱 코드다 (#28)
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
- **프리뷰는 최종 렌더 경로를 지나지 않는다.** 명령은 `video_renderer.build_preview_command`가
  만들고 인코더도 오디오도 출력 mp4도 없다. 두 명령이 같은 그림을 내는 것은 배경과 오버레이를
  `_video_stage` 하나에서 함께 받기 때문이다
- **장면을 고르는 것은 즉시(3~5ms), 값을 고치면 2~3초 기다린다.** 프레임을 프로세스 하나로
  전부 만들어 캐시에 넣고, 프로젝트가 바뀌면 캐시를 통째로 버린다
  ([스파이크 #27](../docs/spikes/27-preview-frames.md)). 그래서 대기 표현이 두 가지다 —
  장면 이동은 이전 프레임을 지우고, 값 변경은 이전 프레임을 남긴 채 갱신 배지를 띄운다
- **프리뷰 스모크는 FFmpeg를 요구한다.** 실제 프레임이 나오는지가 그 시나리오의 절반이라
  대역으로 바꾸면 확인하려는 것이 확인되지 않는다. 나머지 다섯 시나리오는 FFmpeg 없이 돈다
- **셸은 콘텐츠의 필드를 모른다** (#28). `quiz.json`의 `question`·`answer`·`explanation`을
  아는 것은 `src/types/`에 등록된 모듈뿐이고, 셸은 `ContentItem` 다섯 칸과 폼 컴포넌트만
  본다 — 백엔드도 파일명과 검증을 레지스트리(`ShortsType.content_schema`)에서 받는다.
  등록되지 않은 타입이면 그 화면만 없고 나머지는 그대로 돈다
- **사람이 `flagged`를 확인해도 `quiz.json`은 바뀌지 않는다.** `verify.status`·`confidence`는
  검증기가 소유하는 판정 입력이라, 앱이 덮으면 다음 실행의 임계값 판정이 조용히 통과한다.
  확인 기록은 `project.json`의 `review.acknowledged`로 간다 (D2 확정 스펙 1.4)
- **`review`는 렌더러가 읽지 않는 유일한 섹션이라 프리뷰를 다시 만들지 않는다.** 확인 버튼
  한 번이 프레임 전부를 다시 만들면 2초가 붙는데 결과는 같은 그림이다
  (`schemas/project.py`의 `APP_STATE_SECTIONS`)
- **네트워크로 나가지 않는다.** 폰트는 `assets/fonts/`의 번들 Pretendard, 아이콘은 번들
  Lucide이고, `index.html`의 CSP가 `default-src 'none'`이다. main이 `file:` 아닌 요청을
  세어 두므로 스모크가 "시도조차 없었다"를 확인한다
