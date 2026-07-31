# 기여 가이드

이 저장소는 **GitHub 이슈를 단위로 작업**합니다. 모든 작업은 이슈에서 시작하고,
착수 전 **이슈 리파인먼트(refinement)** 를 거쳐 제목·본문을 다듬은 뒤 진행합니다.

---

## 1. 라벨 체계

라벨은 `prefix:` 로 그룹화되어 있습니다. 각 이슈는 **status / type 각 1개**를 필수로 붙이고,
priority·shorts-type 은 해당될 때 붙입니다.

| 그룹 | 라벨 | 의미 |
| --- | --- | --- |
| **status** | `status: needs-refinement` | 제목·본문을 다듬어야 함 (기본 상태) |
| | `status: ready` | 리파인먼트 완료, DoR 충족 — 착수 가능 |
| | `status: in-progress` | 작업 진행 중 |
| | `status: blocked` | 선행 이슈·외부 요인으로 진행 불가 |
| **type** | `type: feature` | 새 기능 / 개선 |
| | `type: bug` | 버그 수정 |
| | `type: docs` | 문서 추가·수정 |
| | `type: chore` | 빌드·설정·잡무 등 비기능 작업 |
| | `type: spike` | 조사·검증·기술 검토 (산출물: 결론/문서) |
| **priority** | `priority: P0` | 긴급 — 최우선 |
| | `priority: P1` | 높음 — 이번 사이클 목표 |
| | `priority: P2` | 보통 — 여유 될 때 |
| **shorts-type** | `shorts-type: quiz` | 퀴즈 쇼츠 타입 관련 |

> 쇼츠 타입이 추가되면 `shorts-type: <name>` 라벨을 함께 추가합니다.

---

## 2. 이슈 흐름 (라이프사이클)

```
생성 (status: needs-refinement)
      │  ← 리파인먼트 (제목·본문 정리, DoR 충족)
      ▼
status: ready ──▶ status: in-progress ──▶ (PR merge 시 close)
                        │
                        ▼
                  status: blocked  (해소되면 다시 in-progress)
```

- 새 이슈는 기본적으로 `status: needs-refinement` 로 둡니다.
- 리파인먼트를 통과하면 `status: ready` 로 교체합니다.
- 작업을 시작하면 `status: in-progress`, 막히면 `status: blocked`.
- 상태 라벨은 **항상 하나만** 유지합니다 (교체하며 이동).

---

## 3. 이슈 리파인먼트

> **정의**: 착수 전에 이슈의 **제목·본문을 다듬어**, 누가 봐도 무엇을 왜 어떻게
> 하는지 명확하고 바로 작업 가능한 상태로 만드는 과정.

### 3.1 제목 규칙

`type` 접두어 + 명령형 요약 형태로 작성합니다.

```
<type>: <무엇을 한다는 명령형 한 줄>
```

- 예) `feat: 원문 입력에서 요약 문단 생성`
- 예) `fix: 자막 타임코드가 1프레임 밀리는 문제 수정`
- 예) `docs: 퀴즈 타입 스펙에 정답 공개 타이밍 추가`
- 접두어: `feat` / `fix` / `docs` / `chore` / `spike` (라벨 `type:` 과 대응)
- 한 문장, 마침표 없이, 모호어("개선", "정리")만 단독으로 쓰지 않기 — **대상**을 명시.

### 3.2 본문 템플릿

```markdown
## 배경 / 문제
왜 이 작업이 필요한가. 어떤 상황·요구에서 나왔는가.

## 목표 / 범위
- 이번 이슈에서 하는 것
- (필요 시) 이번 이슈에서 하지 않는 것 (Out of scope)

## 완료 조건 (Acceptance Criteria)
- [ ] 검증 가능한 조건 1
- [ ] 검증 가능한 조건 2

## 참고
관련 이슈(#번호), PRD/스펙 링크, 스크린샷 등
```

### 3.3 Definition of Ready (DoR)

아래를 **모두** 충족하면 `status: ready` 로 전환합니다.

- [ ] 제목이 3.1 규칙(`type:` 접두어 + 대상 명시)을 따른다
- [ ] `type` 라벨 1개가 붙어 있다
- [ ] 본문에 **배경 / 목표·범위 / 완료 조건**이 채워져 있다
- [ ] 완료 조건이 **검증 가능**하다 (주관적 표현 없음)
- [ ] 한 이슈가 **하나의 목표**로 좁혀져 있다 (너무 크면 분리)
- [ ] 선행 의존 이슈가 있으면 본문에 링크되어 있다

### 3.4 리파인먼트 절차

1. `status: needs-refinement` 이슈를 연다.
2. 제목을 3.1 규칙에 맞게 다듬는다.
3. 본문을 3.2 템플릿으로 채운다 (부족한 정보는 코멘트로 질문).
4. `type` (및 필요 시 `priority` / `shorts-type`) 라벨을 정리한다.
5. 범위가 크면 하위 이슈로 분리하고 서로 링크한다.
6. DoR(3.3)을 모두 만족하면 `status: needs-refinement` → `status: ready` 로 교체한다.

---

## 4. 작업 & PR

- 착수 시 이슈를 `status: in-progress` 로 바꾸고 브랜치를 만든다.
  - 브랜치명: `<type>/<issue-번호>-<짧은-슬러그>` 예) `feat/12-summary-generation`
- 커밋/PR 본문에 `#<이슈번호>` 를 넣어 이슈와 연결한다.
  - PR 본문에 `Closes #12` 를 쓰면 merge 시 이슈가 자동 close 된다.
- 완료 조건(Acceptance Criteria) 체크박스를 모두 만족했는지 PR에서 확인한다.

---

## 참고: 라벨 재생성

라벨을 다시 만들거나 다른 환경에 복제하려면 `gh label create "<name>" --color <hex> --description "<desc>" --force` 를 사용합니다.
