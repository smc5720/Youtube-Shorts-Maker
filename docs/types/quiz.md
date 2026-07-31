# 쇼츠 타입: 퀴즈 (Quiz)

> 이 문서는 [PRD](../PRD.md)의 하위 스펙이다. 쇼츠 "타입"의 첫 구현 대상인 **퀴즈** 타입을 정의한다.

## 0. 결정 요약

| 항목 | 결정 |
|------|------|
| 서브 장르 | 상식/지식 퀴즈 (`general_knowledge`) |
| 문제 수 | 3~5개 연속형 (기본 4개) |
| 정답 형식 | 주관식 (정답 공개형, 선택지 없음) |
| 콘텐츠 확보 | LLM 자동 생성 |
| TTS 범위 | 질문 + 정답 낭독 (해설은 자막만) |
| 사운드 | 카운트다운 비프 / 정답 / 긴장 효과음 기본 탑재 |
| 사실 검증 | 2차 LLM 검증 단계 기본 포함 |

## 1. 타입 플러그인 개념

앞으로 쇼츠 타입이 여러 개(퀴즈, 스토리, 랭킹, 정보 요약 등) 추가된다. 타입을 1급 개념으로 도입한다.

- `project.json` / `scenes.json`에 `type` 필드를 둔다 (예: `"type": "quiz"`).
- 타입별로 두 가지를 플러그인처럼 교체한다.
  - **콘텐츠 생성기**: 입력 → 타입 전용 콘텐츠 산출물 (퀴즈는 `quiz.json`)
  - **장면 템플릿**: 콘텐츠 → `scenes.json` (타입별 장면 레이아웃/타임라인 규칙)
- **렌더러(FFmpeg)** 와 **TTS/자막**은 공통 파이프라인을 공유하되, 타입 전용 렌더 요소(퀴즈의 카운트다운 오버레이, 정답 강조 등)를 추가한다.

## 2. 포맷 / 타임라인

문제당 약 10초, 기본 4문제 기준 약 48초.

```text
[0~3초]     후킹        강한 첫 문장 (예: "이 상식 4개, 다 맞히면 상위 1%")
── 문제 블록 (문제 수만큼 반복) ──
  [질문 3초]    질문 텍스트 크게 표시 + TTS 질문 낭독
  [카운트 4초]  3 → 2 → 1 타이머 오버레이 + 비프음 (생각할 시간)
  [정답 3초]    정답 강조 등장 + 한 줄 해설(자막) + 정답 효과음 + TTS 정답 낭독
── 반복 끝 ──
[마지막 3~5초] CTA        "몇 개 맞혔나요? 댓글 + 구독"
```

- 길이 가이드: 3문제 ≈ 38초 / 4문제 ≈ 48초 / 5문제 ≈ 58초.
- **난이도 오름차순 배치**(easy → hard)로 이탈을 방지한다. 후킹의 "상위 1%"는 마지막 고난도 문제로 정당화한다.
- 문제당 카운트다운 길이는 난이도에 따라 조정 가능 (`countdown_sec`).

## 3. 데이터 스키마

콘텐츠(`quiz.json`)와 렌더 계획(`scenes.json`)을 분리한다. PRD의 `summary.json → scenes.json` 철학과 동일하다. `quiz.json`이 사람이 검수/수정하는 원본이다.

### 3.1 `quiz.json`

```json
{
  "type": "quiz",
  "category": "general_knowledge",
  "language": "ko",
  "hook": "이 상식 4개, 다 맞히면 상위 1%",
  "cta": "몇 개 맞혔나요? 댓글로 알려주세요!",
  "questions": [
    {
      "id": 1,
      "question": "세계에서 가장 긴 강은?",
      "answer": "나일강",
      "explanation": "약 6,650km로 세계에서 가장 긴 강입니다.",
      "difficulty": "easy",
      "countdown_sec": 4,
      "verify": {
        "status": "verified",
        "confidence": 0.95,
        "source": "출처 또는 근거 요약"
      }
    }
  ]
}
```

- `difficulty`: `easy` | `medium` | `hard`. 장면 배치는 이 순서를 따른다.
- `verify.status`: `verified` | `unverified` | `flagged`. `verified`가 아니면 앱에서 빨간 플래그로 표시하고 사람이 확인해야 한다.

### 3.2 `scenes.json` (파생)

`quiz.json`에서 자동 생성한다. 문제 1개 → 서브 장면 3개(질문/카운트다운/정답). 후킹·CTA는 별도 장면.

```json
{
  "type": "quiz",
  "scenes": [
    { "role": "hook", "text": "이 상식 4개, 다 맞히면 상위 1%", "duration": 3.0 },
    { "role": "question", "question_id": 1, "text": "세계에서 가장 긴 강은?", "duration": 3.0, "narrate": true },
    { "role": "countdown", "question_id": 1, "seconds": 4, "duration": 4.0, "sfx": "beep" },
    { "role": "answer", "question_id": 1, "text": "나일강", "caption": "약 6,650km로 세계 최장", "duration": 3.0, "narrate": true, "sfx": "correct" },
    { "role": "cta", "text": "몇 개 맞혔나요? 댓글로 알려주세요!", "duration": 4.0 }
  ]
}
```

### 3.3 `project.json` 연동

편집 가능한 상태 저장. 퀴즈 타입일 때 `type: "quiz"`와 함께 위 콘텐츠/장면 구조를 참조한다. 앱은 이 파일 기준으로 프리뷰/최종 렌더링을 수행한다 (PRD 7.10).

## 4. 파이프라인

퀴즈 타입일 때 PRD의 `summarizer → script_generator` 단계를 아래로 대체한다.

```text
입력(주제)
  → quiz_generator   : LLM으로 Q&A 세트 생성 → quiz.json 초안
  → quiz_verifier    : 2차 LLM으로 각 문제 검증 (정답 정확성, 근거, 신뢰도) → verify 필드 채움
  → scene_planner    : 퀴즈 템플릿으로 scenes.json 생성 (질문/카운트다운/정답 3단 + 후킹/CTA)
  → tts              : 질문 + 정답 낭독 오디오 생성
  → captions         : 자막(SRT/ASS) 생성 — 질문/정답/해설 포함
  → video_renderer   : FFmpeg 합성 + 퀴즈 전용 오버레이(카운트다운/정답 강조) + 효과음 믹싱
  → metadata_generator
```

신설 모듈: `src/types/quiz/quiz_generator.py`, `src/types/quiz/quiz_verifier.py`, `src/types/quiz/scene_template.py` (구조는 구현 시 확정).

## 5. 사실 검증 (필수)

주관식 + 상식 + LLM 자동 생성은 **오답 리스크**가 가장 크다. 틀린 정답은 댓글에서 즉시 지적되어 채널 신뢰도를 훼손한다 (PRD 8장: 사실 여부가 중요한 내용은 검수 단계).

- `quiz_verifier`가 각 문제의 정답 정확성을 독립적으로 재평가하고 `verify` 필드를 채운다.
- `status != "verified"` 또는 `confidence`가 임계값 미만이면 `flagged` 처리, 앱에서 강조 표시.
- 최종 렌더링 전, 플래그된 문제가 있으면 사용자에게 경고한다.

## 6. 렌더링 요소 (퀴즈 전용)

- **카운트다운 오버레이**: 3-2-1 숫자 애니메이션 (원형 타이머 또는 큰 숫자).
- **정답 강조**: 정답 텍스트 등장 애니메이션 (스케일/색상 강조).
- **효과음**: 카운트다운 비프, 정답 공개, 긴장 사운드. 라이선스가 명확한 무료 효과음만 사용하고 `assets/sfx/`에 번들 (PRD 8장 라이선스 정책 준수).

## 7. 에셋

```text
assets/
  backgrounds/      # 세로형 배경 (공통)
  sfx/
    beep.mp3        # 카운트다운
    correct.mp3     # 정답 공개
    tension.mp3     # 긴장 (선택)
  music/            # 라이선스 확인된 파일만
```

## 8. 편집 앱 연동

- 장면 목록에 후킹 / 문제(질문·카운트다운·정답) / CTA를 구분 표시.
- 문제별로 질문·정답·해설·난이도·카운트다운 길이를 수정 가능.
- 검증 미완료(`flagged`) 문제는 빨간 배지로 표시.
- 문제 순서 변경, 문제 추가/삭제 지원.
- 나머지 편집 기능(자막 스타일, 배경 교체, 오디오 볼륨 등)은 PRD 6.5 공통 기능을 따른다.

## 9. 퀴즈 타입 마일스톤

1. `quiz.json` / `scenes.json`(quiz) 스키마 확정, 타입 필드 도입
2. `quiz_generator` (LLM Q&A 생성)
3. `quiz_verifier` (2차 검증)
4. 퀴즈 `scene_planner` 템플릿
5. TTS(질문+정답) / 자막 연동
6. 렌더러 카운트다운·정답 강조·효과음
7. 앱 편집 UI (문제 편집 + 검증 플래그)

## 10. 미해결 결정사항

- LLM provider (로컬 vs 외부 API) — PRD 14장과 공유되는 결정.
- 검증 임계값(`confidence`) 기본값.
- 카운트다운 UI 스타일 (숫자 vs 원형 타이머).
- 배경 소스 (단색/그라디언트 기본 vs 무료 이미지 API).
- 한국어 1차 타깃 vs 다국어 구조 — PRD 14장과 공유.
