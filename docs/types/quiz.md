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

이 개념은 `src/shorts_maker/shorts_types.py`의 레지스트리로 구현됐다(#8). 퀴즈의 선언은
`src/shorts_maker/types/quiz/__init__.py`의 `SHORTS_TYPE`이고 두 축(`generator`,
`scene_template`)과 산출물 조건을 여기 모아 둔다 — 레지스트리는 타입 이름과 그 위치만 안다
([PRD 14.1](../PRD.md#14-결정사항)).

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

**이 표는 `tests/test_type_boundary.py`가 강제한다.** `src/shorts_maker/types/` 밖의 모듈이
타입 패키지를 import하거나, `quiz.json` 스키마 이름(`load_quiz` 등)을 import하거나, 타입 전용
산출물 파일명을 문자열로 들고 있으면 테스트가 깨진다.

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
  "schema_version": 1,
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

- `schema_version`: 현재 `1`. 모르는 버전은 오류로 처리한다 (`src/shorts_maker/schemas/`).
- `difficulty`: `easy` | `medium` | `hard`. 장면 배치는 이 순서를 따른다.
- `verify.status`: `verified` | `unverified` | `flagged`. `verified`가 아니면 앱에서 빨간 플래그로 표시하고 사람이 확인해야 한다.
- `verify`는 **`quiz_generator`가 만든 직후에는 없다.** `quiz_verifier`가 채우므로 검증기는
  이 필드를 필수로 요구하지 않는다. `source`는 근거를 대지 못하는 검증도 있으므로 선택이다.

### 3.2 `scenes.json` (파생)

`quiz.json`에서 자동 생성한다. 문제 1개 → 서브 장면 3개(질문/카운트다운/정답). 후킹·CTA는 별도 장면.

장면 템플릿이 만드는 것은 **초안**이다. `narrate: true` 장면의 `duration`은 TTS 단계가
실측값으로 덮어쓴다 (PRD 7.5.1). 아래 예시는 TTS 단계까지 지난 확정 상태다.

```json
{
  "schema_version": 1,
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

> **필드명은 위 이름으로 확정됐다.** 스키마 정의는 `src/shorts_maker/schemas/scenes.py`에
> 있고, 이 문서가 아니라 그 코드가 단일 진실 공급원이다. 검증은 두 단계다 —
> `validate_scenes()`는 초안과 확정 상태를 모두 받고, `validate_scenes_final()`은 모든 장면의
> `duration`과 낭독 장면의 오디오 필드를 요구한다. 낭독이 아닌 장면에는 확정 검증도 오디오
> 필드를 요구하지 않는다. 세그먼트 파일명은 `segment_path(scene_index)`가 만든다.
>
> 스키마가 추가로 요구하는 것 두 가지가 있다. `narrate: true` 장면에는 `text`와
> `target_duration`이 있어야 하고(합성할 문장과 경고 기준이 없으면 TTS 단계가 판단할 근거가
> 없다), `role: "countdown"` 장면에는 `seconds`가 있어야 한다(렌더러가 셀 숫자다). 선언하지
> 않은 필드는 오류다 — `narate`처럼 오타 난 플래그가 "낭독 아님"으로 조용히 통과하면 음성
> 없는 영상이 나온다.

### 3.3 `project.json` 연동

편집 가능한 상태 저장. 퀴즈 타입일 때 `type: "quiz"`와 함께 위 콘텐츠/장면 구조를 참조한다. 앱은 이 파일 기준으로 프리뷰/최종 렌더링을 수행한다 (PRD 7.10).

장면 배열을 `project.json`에 복사하지 않고 `scenes.json` 경로만 참조한다. 초기 상태의 필드
목록과 예시는 [PRD 7.10](../PRD.md#710-프로젝트-파일)에 있고, 편집 상태 필드는 앱
프레임워크가 정해진 뒤 추가된다.

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

신설 모듈: `src/shorts_maker/types/quiz/quiz_generator.py`,
`src/shorts_maker/types/quiz/quiz_verifier.py`, `src/shorts_maker/types/quiz/scene_template.py`.

`quiz_generator`와 `quiz_verifier`는 파이프라인 단계가 아니라 **한 단계 안의 두 모듈**이다.
`SHORTS_TYPE.generator`가 가리키는 것은 `quiz_generator.generate()` 하나이고, 그 안에서
검증기를 부른다 (5.1).

### 4.1 `quiz_generator`가 정하는 것과 묻지 않는 것 (#9)

`quiz_generator`는 **LLM을 1회 부른다.** 문제별로 나눠 부르면 CLI 기동 오버헤드(호출당 약
6.5초, 스파이크 #1 3장)가 문제 수만큼 곱해진다.

모델에게 묻는 것은 `hook` / `cta` / 각 문제의 `question`·`answer`·`explanation`·`difficulty`
뿐이다. 나머지는 코드가 채운다.

| 필드 | 채우는 주체 |
| --- | --- |
| `schema_version` / `type` | 스키마 |
| `category` / `language` | 코드 고정 — `general_knowledge` / `ko` (0장, PRD 14.1) |
| `id` | 난이도 오름차순 정렬 **뒤에** 1부터 다시 매긴다 |
| `countdown_sec` | `quiz.countdown_sec` (모든 문제에 균일) |
| `verify` | `quiz_verifier` (#10). 초안에는 없다 |

- **난이도 오름차순은 프롬프트와 정렬 양쪽에서 보장한다.** 스키마는 값 사이의 순서를 강제하지
  않으므로 프롬프트만으로는 어긋난 배치가 검증을 통과한다.
- `--json-schema`로 넘기는 JSON Schema는 `src/shorts_maker/schemas/quiz.py`의
  `content_json_schema()`가 `quiz.json` 스키마에서 **파생**한다. 프롬프트 쪽에 필드 이름을
  다시 적으면 계약이 두 곳에 생긴다 (PRD 14.1).

설정 세 가지가 생성 결과를 좌우한다.

| 키 | 기본값 | 위반 시 |
| --- | --- | --- |
| `quiz.question_count` | 4 | **3~5 밖이면 run 디렉터리를 만들기 전에 오류.** 범위를 아는 것은 퀴즈 타입이므로 설정 로더가 아니라 타입 선언(`ShortsType.config_check`)이 확인한다 |
| `quiz.answer_max_len` | 20 | JSON Schema의 `maxLength`로 먼저 걸러지고, 그래도 넘으면 **재생성하지 않고 오류로 멈춘다** — 다시 불러도 같은 이유로 같은 결과가 나온다 |
| `quiz.explanation_max_len` | 60 | 위와 같다 |

## 5. 사실 검증 (필수)

주관식 + 상식 + LLM 자동 생성은 **오답 리스크**가 가장 크다. 틀린 정답은 댓글에서 즉시 지적되어 채널 신뢰도를 훼손한다 (PRD 8장: 사실 여부가 중요한 내용은 검수 단계).

- `quiz_verifier`가 각 문제의 정답 정확성을 독립적으로 재평가하고 `verify` 필드를 채운다 (5.1).
- `status != "verified"` 또는 `confidence`가 임계값 미만이면 `flagged` 처리, 앱에서 강조 표시 (5.2).
- 최종 렌더링 전, 플래그된 문제가 있으면 사용자에게 경고한다 (5.2).

### 5.1 검증 방식 (#10)

검증은 **`generate()` 안의 한 단계**다. 레지스트리가 아는 교체 가능한 축은 생성기와 장면
템플릿 둘뿐이고(1장), 검증은 퀴즈 타입이 자기 산출물에 대해 하는 일이므로 세 번째 축이
되지 않는다.

프로브가 두 개다. **둘 다 원래 정답을 프롬프트에 넣지 않는다.**

| 프로브 | 묻는 것 | 잡는 것 |
| --- | --- | --- |
| 블라인드 재답변 (`llm.verifier.runs`회) | 질문만 주고 독립 답변 | 정답이 틀린 문제 |
| 모호성 프로브 (1회) | "이 질문은 답이 하나로 정해지는가" | 정답은 맞지만 질문이 모호한 문제 |

모호성 프로브가 따로 있는 이유는 스파이크 #1 4.2다. *정답이 틀린 게 아니라 질문 표현이
모호한* 유형("한국 최초의 금속활자본은?")은 블라인드 재답변으로 원리상 잡히지 않는다 —
재답변 모델도 같은 근거로 같은 답을 낸다. 이 프로브는 정답 일치 여부와 **독립적으로**
`flagged`를 만든다.

**호출은 문제 수와 무관하게 `runs + 1`회다.** 문제를 한 호출에 묶으면 CLI 기동
오버헤드(호출당 약 6.5초)가 문제 수만큼 곱해지는 것을 피할 수 있고, 묶어도 새는 것은
질문뿐이지 정답이 아니다 — 블라인드성은 "원래 정답이 프롬프트에 없다"로 정의된다.
게다가 한 영상의 문제들은 애초에 같은 주제로 한 번에 생성된 것이라 주제 인접성은 이미
공유돼 있다.

정답 대조는 **정규화 후 포함 관계**로 한다. 표기 차이(`"나일강"`/`"나일 강"`,
`"세종대왕(조선 제4대 왕)"`/`"세종대왕"`, `"H2O"`/`"H₂O"`)를 오답으로 세면 검증이 사실
검사가 아니라 표기 검사가 된다. 두 값이 모두 수일 때만 정확 일치를 요구한다 —
`"234"`가 `"1234"`에 포함된다고 같은 답으로 세면 연도·개수 문제가 무력해진다.

#### `confidence` 산출

```text
confidence = 일치율 × 평균 자기 확신도
  일치율   = 원래 정답과 일치한 재답변 수 ÷ 성공한 재답변 호출 수
  확신도   = 재답변 모델이 자기 답에 대해 보고한 값 (0~1)의 평균
```

일치율만 쓰면 `runs: 2`에서 값이 `{0, 0.5, 1}` 세 개뿐이라 임계값 0.8이 "2회 전부 일치"와
완전히 같은 뜻이 되고, 임계값을 조정할 수 있다는 전제가 허구가 된다. `runs`를 올려
해상도를 얻는 길은 호출이 선형으로 늘어나 버렸다 — 검증은 이미 문제당 비용의 59%다
(스파이크 #1 4.4). 확신도를 곱하면 `runs: 2`에서도 연속값이 나오고, 확신도가 1을 넘지
못하므로 **일치율 0.5인 문제의 상한이 0.5**다 (임계값을 0.5 위에 두는 한 불일치가
통과하지 못한다).

#### 상태 판정

| 조건 | `status` |
| --- | --- |
| 모호성 프로브가 "답이 갈린다"고 답함 | `flagged` |
| 재답변·프로브 호출이 실패해 판단 근거가 없음 | `unverified` |
| 재답변이 원래 정답과 갈림 | `flagged` |
| 위 어디에도 걸리지 않음 | `verified` |

- `verified`는 **"정답이 맞다"가 아니라 "검증 단계가 결함을 찾지 못했다"**는 뜻이다.
- **임계값은 여기서 적용하지 않는다.** `llm.verifier.confidence_threshold`를 읽어
  `verified`를 `flagged`로 내리는 것은 #11이다. 재답변 불일치와 질문 모호는 임계값과 무관한
  실검출이라 바로 `flagged`가 되고, 임계값은 남은 `verified`를 확신도 축에서 자른다.
- 검증이 전부 실패해도 **예외를 던지지 않는다.** 여기서 멈추면 이미 지불한 생성 비용이
  버려지고 사람이 검수할 대상 자체가 사라진다.
- `verify.source`는 판정 사유를 앞에 두고 근거를 잇는다 (`"재답변 불일치 — 2회 중 0회 일치..."`,
  `"질문 모호 — ..."`). #11의 경고가 사유를 여기서 읽는다. **`source`를 필수로 만들지 않는
  이유는 3.1에 있다** — 근거를 대지 못한 검증에는 키를 넣지 않고 빈 문자열을 남기지 않는다.

`llm.verifier.runs`는 1 이상이어야 하며, 위반은 run 디렉터리를 만들기 전에 걸린다
(`ShortsType.config_check`). 재답변이 한 번도 없으면 검증 단계가 이름만 남는다.

검출률 실측과 임계값 근거는 [`docs/spikes/10-verifier-detection.md`](../spikes/10-verifier-detection.md).

### 5.2 임계값 판정과 경고 (#11)

검증기가 산출한 `confidence`를 정책과 대조해 `verify.status`를 확정하는 단계다. 구현은
`quiz_review.py`이고, 파이프라인은 이것을 **콘텐츠 산출물을 쓰기 전에** 부른다 — 판정이
`quiz.json`에 남아야 앱(#30)과 이후 단계가 같은 상태를 본다.

| 확정 전 `status` | 조건 | 확정 후 |
| --- | --- | --- |
| `verified` | `confidence` ≥ `llm.verifier.confidence_threshold` | `verified` |
| `verified` | `confidence` < 임계값 | `flagged` (사유: 임계값 미달) |
| `unverified` | — | `flagged` (검증기가 적은 사유를 그대로 유지) |
| `flagged` | — | `flagged` (사유를 덮어쓰지 않는다) |

- **임계값 키는 `llm.verifier.confidence_threshold` 하나다** (기본값 0.8, 아래 결정 참조).
  같은 뜻의 키를 `quiz` 아래에 새로 만들지 않는다.
- **임계값과 정확히 같은 값은 통과다.** `<`로 자른다.
- **임계값 미달은 재답변 불일치·질문 모호보다 뒤에 온다.** 실검출은 `status`에서 나오고
  (측정 보고서 4장), 임계값은 남은 `verified`를 확신도 축에서 자르는 안전망이다. 이미
  실검출로 잡힌 문제에 "임계값 미달"을 덧쓰면 더 구체적인 사유를 잃는다.
- `unverified`도 `flagged`가 된다. 성공이 아니라 "판단 근거가 없다"이고, 근거 없이
  통과시키면 검증 단계를 둔 뜻이 없다. 원래 사유는 `source`에 남으므로 경고에서
  "검증 미완료"와 "재답변 불일치"는 계속 구분된다.
- **두 번 불러도 결과가 같다.** 사유를 먼저 `source`에 쓰고 경고 문구를 거기서 만든다.

경고는 문제 `id`·질문·`confidence`·적용된 임계값·사유를 담아 콘솔과 `run.log` 양쪽에
남는다 (`--verbose`와 무관하다). **기본 동작은 경고 후 진행이고 종료 코드는 0이다** —
MVP의 검수 주체는 사람이고 사람은 산출물이 있어야 검수한다 (PRD 2장). 중단이 필요한
쪽(배치 실행, 이후의 앱 자동화)은 `--fail-on-flagged`를 지정해 전용 종료 코드로 멈춘다.

파이프라인은 판정 기준을 모른다. 타입이 만든 `ContentIssue`(어느 부분 / 무엇 / 왜) 세 칸을
읽어 경고로 옮길 뿐이며, `confidence`도 임계값도 퀴즈 타입 안에만 있다 (1.1).

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

- 카운트다운 UI 스타일 (숫자 vs 원형 타이머).

검증 임계값은 **#11에서 확정됐다** — `llm.verifier.confidence_threshold` 기본값 **0.8**.
잠정값을 그대로 굳힌 것이 아니라 #10이 함정 문제 세트로 실측해 재확인한 값이다
([측정 보고서](../spikes/10-verifier-detection.md) 5장). 의미 있는 범위가 `(0.5, 0.98)`이고
0.8이 그 가운데다 — 아래로는 `confidence = 일치율 × 확신도`의 성질상 재답변이 갈린 문제의
상한이 0.5라 그 이하로 두면 불일치를 통과시키고, 위로는 관측된 정상 문제의 최솟값이
0.98이라 그 위로 올리면 정상 문제를 잃기 시작한다.

**임계값은 모호성 프로브의 대체재가 아니다.** 측정에서 검증기가 놓친 모호 문제의
`confidence`(0.865~0.99)가 정상 문제 대역(0.98~1.0)과 겹쳤다. 누락을 임계값으로 되찾으려면
0.99를 넘겨야 하고 그러면 정상 문제도 함께 잘린다. 판정 규칙과 게이트 동작은 5.2에 있다.

LLM provider는 **PRD 14.1에서 확정됐다** — 로컬 `claude` CLI 헤드리스 호출, 기본 모델
`claude-opus-5`. 4장의 `quiz_generator`·`quiz_verifier`가 이 경로를 쓴다. 실측치와 provider
인터페이스는 `docs/spikes/1-llm-provider.md`.

5장의 블라인드 검증과 관련해 스파이크 #1이 남긴 단서 두 가지는 **#10에서 처리됐다.**
생성·검증 모델 분리는 `llm.verifier.model`로 열려 있고, 질문이 모호한 유형을 잡는 별도
장치가 모호성 프로브다 (5.1). 둘의 실측 결과는 측정 보고서에 있다.

배경 소스와 언어 타깃은 **PRD 14.1에서 확정됐다** — 배경은 단색/그라디언트 기본 제공 +
사용자 파일 교체(무료 이미지 API는 MVP 이후), 언어는 한국어 1차 타깃이며 `quiz.json`의
`language` 필드로 확장 여지를 유지한다. 6장의 퀴즈 전용 렌더 요소도 PRD 14.1의 렌더링
방식 결정(FFmpeg 명령 직접 생성)을 따른다.
