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

### 1.1 경계 규칙: 공통 파이프라인은 `scenes.json`만 본다

플러그인 경계를 지키는 규칙은 하나다. **TTS·자막·렌더러·메타데이터 생성기는 `scenes.json`만
읽고 `quiz.json`을 열지 않는다.**

| 모듈 | 읽는 것 | 읽지 않는 것 |
| --- | --- | --- |
| `quiz_generator` / `quiz_verifier` | 입력 주제 | — |
| 장면 템플릿 (`scene_template.py`) | `quiz.json` | — |
| `tts` / `captions` / `video_renderer` / `metadata_generator` | `scenes.json` | **`quiz.json`** |

- 렌더에 필요한 타입 전용 정보는 장면 템플릿이 `scenes.json` 필드로 옮겨 담는다.
  예: `countdown_sec`은 `countdown` 장면의 `seconds`·`duration`으로, `explanation`은
  `answer` 장면의 `caption`으로 옮긴다. 렌더러가 `quiz.json`의 `explanation`을 직접 읽으면 안 된다.
- 타입 전용 렌더 요소도 `role` 값으로만 구분한다. 렌더러는 `role: "countdown"`을 보고
  카운트다운 오버레이를 그리지만, 그 장면이 퀴즈에서 나왔다는 사실은 모른다. 다른 타입이
  같은 `role`을 쓰면 같은 오버레이를 그대로 얻는다.
- 세그먼트 오디오 파일명도 장면 인덱스로만 매긴다 (`audio/seg-003.mp3`). `q2_answer.mp3` 같은
  이름은 TTS 모듈에 퀴즈 어휘를 들여오므로 쓰지 않는다.

이 경계가 깨지면 두 번째 타입(스토리·랭킹 등)을 추가할 때 공통 파이프라인 전체를 고쳐야 한다.
PRD 쪽 서술은 [PRD 7.4.1](../PRD.md#741-scenesjson-단일-계약).

## 2. 포맷 / 타임라인

문제당 약 10초, 기본 4문제 기준 약 48초.

> **아래 초 단위 값은 전부 목표치이며 최종 길이가 아니다.** 질문·정답 장면은 낭독이 있으므로
> 실제 `duration`은 합성된 오디오를 실측한 길이 + 앞뒤 패딩으로 확정한다
> ([PRD 7.5.1](../PRD.md#751-타이밍-확정-규칙--duration은-실측-오디오-길이로-정한다)).
> "질문 3초"를 고정값으로 구현하면 자막·정답 강조·효과음이 음성과 어긋난다. 목표치는 확정값이
> 크게 벗어났을 때 경고를 내는 기준으로만 쓴다.

```text
[0~3초]     후킹        강한 첫 문장 (예: "이 상식 4개, 다 맞히면 상위 1%")
── 문제 블록 (문제 수만큼 반복) ──
  [질문 ~3초]   질문 텍스트 크게 표시 + TTS 질문 낭독      ← 실측 확정
  [카운트 4초]  3 → 2 → 1 타이머 오버레이 + 비프음 (생각할 시간)  ← 고정
  [정답 ~3초]   정답 강조 등장 + 한 줄 해설(자막) + 정답 효과음 + TTS 정답 낭독  ← 실측 확정
── 반복 끝 ──
[마지막 3~5초] CTA        "몇 개 맞혔나요? 댓글 + 구독"
```

| 장면 | 길이 결정 | 근거 |
| --- | --- | --- |
| `hook` | 고정 3.0초 | 현재 TTS 범위가 질문·정답뿐이라 낭독이 없다 |
| `question` | 실측 오디오 + 패딩 | 질문 문장 길이가 문제마다 다르다 |
| `countdown` | 고정 `countdown_sec` (기본 4초) | 낭독이 없고, 숫자 전환이 정수 초에 맞아야 한다 |
| `answer` | 실측 오디오 + 패딩 | 정답 낭독 길이가 "나일강"과 "카를 벤츠의 페이턴트 모터바겐"만큼 차이난다 |
| `cta` | 고정 4.0초 | 낭독이 없다 |

- 길이 가이드: 3문제 ≈ 38초 / 4문제 ≈ 48초 / 5문제 ≈ 58초. 이것도 목표 범위이며, 확정 합계가
  PRD 6.3의 45~60초를 벗어나면 경고만 남기고 렌더는 진행한다.
- **난이도 오름차순 배치**(easy → hard)로 이탈을 방지한다. 후킹의 "상위 1%"는 마지막 고난도 문제로 정당화한다.
- 문제당 카운트다운 길이는 난이도에 따라 조정 가능 (`countdown_sec`).
- `hook`·`cta`에 낭독을 추가하기로 결정하면 두 장면은 `narrate: true`가 되고 자동으로 실측
  규칙을 따른다. 고정 길이 목록을 코드에 하드코딩하지 않는다.

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

장면 템플릿이 만드는 것은 **초안**이다. `narrate: true` 장면의 `duration`은 TTS 단계가
실측값으로 덮어쓴다 (PRD 7.5.1). 아래 예시는 TTS 단계까지 지난 확정 상태다.

```json
{
  "type": "quiz",
  "scenes": [
    { "role": "hook", "text": "이 상식 4개, 다 맞히면 상위 1%", "duration": 3.0 },
    { "role": "question", "question_id": 1, "text": "세계에서 가장 긴 강은?",
      "narrate": true, "target_duration": 3.0, "duration": 2.94,
      "audio": "audio/seg-001.mp3", "audio_duration": 2.14, "narration_offset": 3.3 },
    { "role": "countdown", "question_id": 1, "seconds": 4, "duration": 4.0, "sfx": "beep" },
    { "role": "answer", "question_id": 1, "text": "나일강", "caption": "약 6,650km로 세계 최장",
      "narrate": true, "target_duration": 3.0, "duration": 1.72,
      "audio": "audio/seg-003.mp3", "audio_duration": 0.92, "narration_offset": 10.24,
      "sfx": "correct" },
    { "role": "cta", "text": "몇 개 맞혔나요? 댓글로 알려주세요!", "duration": 4.0 }
  ]
}
```

- `target_duration`: 템플릿이 넣은 목표치. 확정값과 크게 벌어졌는지 검증할 때만 쓴다.
- `duration`: 확정 길이. 낭독 장면은 `lead_in + audio_duration + tail`, 최소 `min_duration`.
- `audio`: 세그먼트 파일 경로. 인덱스는 `scenes` 배열 위치(0-based)를 따르므로 낭독이 아닌
  장면 번호는 비어 있다 (위 예시에서 `seg-000`·`seg-002`·`seg-004`가 없다).
- `narration_offset`: `voice.mp3` 안에서 이 세그먼트가 시작하는 시각. 자막 타임코드의 기준이다.
- `audio` / `audio_duration` / `narration_offset`은 TTS 단계가 채운다. 장면 템플릿은 비워 둔다.

> 필드명은 스키마 정의(#7)에서 확정한다. 위 이름은 현재 합의된 초안이다.

### 3.3 `project.json` 연동

편집 가능한 상태 저장. 퀴즈 타입일 때 `type: "quiz"`와 함께 위 콘텐츠/장면 구조를 참조한다. 앱은 이 파일 기준으로 프리뷰/최종 렌더링을 수행한다 (PRD 7.10).

## 4. 파이프라인

퀴즈 타입일 때 PRD의 `summarizer → script_generator` 단계를 아래로 대체한다.

```text
입력(주제)
  → quiz_generator   : LLM으로 Q&A 세트 생성 → quiz.json 초안
  → quiz_verifier    : 2차 LLM으로 각 문제 검증 (정답 정확성, 근거, 신뢰도) → verify 필드 채움
  → scene_planner    : 퀴즈 템플릿으로 scenes.json 초안 생성 (질문/카운트다운/정답 3단 + 후킹/CTA)
  → tts              : narrate 장면별 세그먼트 오디오 생성 → 실측 길이로 duration 확정
                       → 타임라인에 배치한 voice.mp3 합성
  → captions         : 자막(SRT/ASS) 생성 — 질문/정답/해설 포함, 확정 duration·오프셋 기준
  → video_renderer   : FFmpeg 합성 + 퀴즈 전용 오버레이(카운트다운/정답 강조) + 효과음 믹싱
  → metadata_generator
```

`tts` 이후 단계는 모두 확정된 `scenes.json`을 입력으로 받는다. `scene_planner` 초안의 목표치를
기준으로 계산하는 단계가 있으면 안 된다.

퀴즈 타입은 `script.txt`·`summary.json`을 생성하지 않는다. 낭독 대상은 `quiz.json`의 질문·정답
필드이고 요약할 원문이 없다. `--topic` 입력이므로 `source.json`도 없다 (PRD 6.2 표).

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

- 검증 임계값(`confidence`) 기본값. 스파이크 #1에서 블라인드 재답변이 24회 전부 일치해
  임계값을 정할 근거를 만들지 못했다. #10에서 어려운 문제 세트로 재측정한 뒤 확정한다.
- 카운트다운 UI 스타일 (숫자 vs 원형 타이머).

LLM provider는 **PRD 14.1에서 확정됐다** — 로컬 `claude` CLI 헤드리스 호출, 기본 모델
`claude-opus-5`. 4장의 `quiz_generator`·`quiz_verifier`가 이 경로를 쓴다. 실측치와 provider
인터페이스는 `docs/spikes/1-llm-provider.md`.

5장의 블라인드 검증과 관련해 스파이크 #1이 남긴 단서 두 가지가 있다. 첫째, 생성 모델과
검증 모델이 같으면 프롬프트만 탈상관되고 모델의 지식은 공유되므로 검증력이 구조적으로 약하다 —
config에서 `llm.verifier.model`을 분리해 열어 둔다. 둘째, **정답이 틀린 게 아니라 질문 표현이
모호한 유형**(예: "한국 최초의 금속활자본은?" — '현존하는'이 빠져 답이 갈림)은 블라인드
재답변으로는 원리상 잡히지 않는다. 재답변 모델도 같은 근거로 같은 답을 내기 때문이다.
이 유형을 잡으려면 별도 장치가 필요하다.

배경 소스와 언어 타깃은 **PRD 14.1에서 확정됐다** — 배경은 단색/그라디언트 기본 제공 +
사용자 파일 교체(무료 이미지 API는 MVP 이후), 언어는 한국어 1차 타깃이며 `quiz.json`의
`language` 필드로 확장 여지를 유지한다. 6장의 퀴즈 전용 렌더 요소도 PRD 14.1의 렌더링
방식 결정(FFmpeg 명령 직접 생성)을 따른다.
