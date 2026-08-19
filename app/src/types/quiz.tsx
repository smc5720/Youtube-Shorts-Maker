// 퀴즈 타입의 문제 편집 (D2 확정 스펙 3.2, 이슈 #28).
//
// **`quiz.json`의 필드를 아는 유일한 앱 코드다.** 셸은 `ContentItem`만 보고, 여기서 나가는
// 값은 언제나 스키마를 통과하는 콘텐츠 하나다 — 저장은 백엔드가 다시 검증하지만(`save_content`)
// 거기서 걸리는 것은 사용자가 고칠 수 없는 실패다.
//
// **`verify`를 쓰지 않는다.** 그 두 값은 검증기(#10)와 검수 게이트(#11)가 소유하고, 사람이
// 확인한 기록은 `project.json`의 `review`로 간다 (확정 스펙 1.4). 시안의 "확인함으로 표시"가
// `status`를 `verified`로 덮도록 그려져 있는데, 그렇게 하면 다음 실행의 임계값 판정이 조용히
// 통과한다.

import type { Content } from '../protocol'
import { Icon } from '../components/Icon'
// **낡음 카드는 셸이 소유한다** (#83) — 두 종류의 색·모양·문구가 타입의 지식이 아니고,
// 속성 패널(S2)과 이 폼이 같은 것을 그려야 한다 (확정 스펙 7.3).
import { StaleCard } from '../components/Stale'
import { StatusBadge } from '../components/StatusBadge'
import { NumberInput, Segmented, TextArea, TextInput } from '../components/controls'
import type { ContentItem, ContentModule, EditorProps, VerifyStatus } from './index'

interface Verify {
  status: VerifyStatus
  confidence: number
  source?: string
}

interface Question {
  id: number
  question: string
  answer: string
  explanation: string
  difficulty: Difficulty
  countdown_sec: number
  verify?: Verify
}

type Difficulty = 'easy' | 'medium' | 'hard'

const DIFFICULTIES: ReadonlyArray<{ value: Difficulty, text: string }> = [
  { value: 'easy', text: '쉬움' },
  { value: 'medium', text: '보통' },
  { value: 'hard', text: '어려움' }
]

/** 새 문제의 기본값. `countdown_sec`은 D1 확정 스펙의 고정 길이 3초다. */
const NEW_QUESTION = {
  question: '새 질문',
  answer: '정답',
  explanation: '해설을 적는다.',
  difficulty: 'medium' as Difficulty,
  countdown_sec: 3
}

function questions (content: Content): Question[] {
  return (content.questions as Question[] | undefined) ?? []
}

function withQuestions (content: Content, next: Question[]): Content {
  return { ...content, questions: next }
}

/**
 * 낭독으로 가는 문구 — **질문과 정답 둘이다.**
 *
 * **해설은 낭독되지 않는다** (#83, 확정 스펙 7.3). 장면 템플릿이 해설을 `caption`으로만 담고
 * TTS는 `narrate: true` 장면의 `text`만 읽으므로(`narration.py`), 해설을 여기 넣으면 해설만
 * 고친 경우가 "음성까지 낡음"으로 잘못 표시된다 — 낡음이 한 종류였을 때는 결과가 같아서
 * 드러나지 않던 동작이다.
 *
 * 난이도와 카운트다운 길이는 어느 쪽에도 들어가지 않는다 — 그 둘을 고쳐도 오디오도 자막도
 * 그대로다.
 */
function narration (question: Question): string {
  return [question.question, question.answer].join(' ')
}

/**
 * 자막에 가는 문구 — 낭독 문구 + 해설.
 *
 * 낭독 문구를 포함하는 것은 그것이 자막에도 나오기 때문이다 (`captions.py`가 `text`와
 * `caption`을 함께 읽는다). 이 값만 바뀌면 **자막만 낡는다** — 확인 기록은 이쪽 변화에도
 * 풀린다 (사람이 확인한 것은 그 문제의 내용이고, 해설이 달라졌으면 확인한 내용과 다르다).
 */
function captions (question: Question): string {
  return [narration(question), question.explanation].join(' ')
}

function toItem (question: Question): ContentItem {
  const verify = question.verify
  return {
    id: question.id,
    title: question.question,
    // **`verify`가 없는 것은 `unverified`다.** 초안이거나 사람이 지운 것이고, 어느 쪽이든
    // "판단 근거가 없다"이지 통과가 아니다 (퀴즈 스펙 5.2).
    status: verify?.status ?? 'unverified',
    confidence: verify?.status === 'unverified' || verify === undefined ? null : verify.confidence,
    source: verify?.source ?? null,
    tag: DIFFICULTIES.find((option) => option.value === question.difficulty)?.text ?? question.difficulty,
    narration: narration(question),
    captions: captions(question)
  }
}

export const quiz: ContentModule = {
  items: (content) => questions(content).map(toItem),

  move: (content, id, delta) => {
    const list = questions(content)
    const from = list.findIndex((question) => question.id === id)
    const to = from + delta
    if (from < 0 || to < 0 || to >= list.length) return content
    const next = [...list]
    next.splice(to, 0, ...next.splice(from, 1))
    return withQuestions(content, next)
  },

  add: (content, reserved) => {
    const list = questions(content)
    // **지금 있는 번호와 장면이 아직 참조하는 번호를 함께 넘긴 값이다.** 빈 번호를 메우면
    // 지운 문제의 장면들이 새 문제의 것으로 읽힌다.
    const id = [...list.map((question) => question.id), ...reserved]
      .reduce((highest, value) => Math.max(highest, value), 0) + 1
    return { content: withQuestions(content, [...list, { id, ...NEW_QUESTION }]), id }
  },

  remove: (content, id) =>
    withQuestions(content, questions(content).filter((question) => question.id !== id)),

  Editor: QuizEditor
}

function QuizEditor ({
  content, item, acknowledged, stale, captionsStale, regenerate, onChange, onAcknowledge
}: EditorProps) {
  const list = questions(content)
  const question = list.find((entry) => entry.id === item.id)
  if (!question) return <div className="t-caption">고른 문제가 없다.</div>

  const patch = (fields: Partial<Question>) => {
    onChange(withQuestions(content, list.map(
      (entry) => (entry.id === item.id ? { ...entry, ...fields } : entry)
    )))
  }

  return (
    <div className="editor" data-testid="quiz-editor" data-question={question.id}>
      {/* **폼 맨 위다** (확정 스펙 3.2). 고쳐야 할 것을 알기 전에 고칠 칸이 먼저 오면
          사용자가 무엇을 보고 고치는지 모른 채 타이핑한다. */}
      {item.status !== 'verified' && (
        <VerifyCard item={item} acknowledged={acknowledged} onAcknowledge={onAcknowledge} />
      )}
      {/* **강한 쪽만 그린다** (#83). 음성까지 낡았으면 자막도 낡았고, 카드 둘이 같은 일을
          두 번 말하면 폼 위가 카드로 덮인다. **버튼은 셸이 만든 것을 그대로 넣는다** (#77) —
          두 카드가 같은 실행을 부르므로 문구만 갈린다. */}
      {stale
        ? <StaleCard kind="audio">{regenerate}</StaleCard>
        : captionsStale && <StaleCard kind="captions">{regenerate}</StaleCard>}

      <TextArea label="질문" value={question.question} rows={3} onChange={(value) => patch({ question: value })} />
      <TextInput label="정답" value={question.answer} onChange={(value) => patch({ answer: value })} />
      <Segmented
        label="난이도"
        value={question.difficulty}
        options={DIFFICULTIES}
        onChange={(value) => patch({ difficulty: value })}
      />
      <NumberInput
        label="카운트다운"
        value={question.countdown_sec}
        min={1}
        max={10}
        unit="초"
        onChange={(value) => patch({ countdown_sec: value })}
      />
      <TextArea label="해설" value={question.explanation} rows={4} onChange={(value) => patch({ explanation: value })} />
    </div>
  )
}

/**
 * 검증 카드 — 사유 · `confidence` · 확인 버튼 (확정 스펙 3.2).
 *
 * **`unverified`도 확인이 필요한 상태로 묶는다**(퀴즈 스펙 5.2). 다만 점선 테두리와 문구로
 * 갈리고 `confidence` 자리에는 `—`가 온다 — 값이 0인 것과 값이 없는 것은 다르다.
 */
function VerifyCard ({ item, acknowledged, onAcknowledge }: {
  item: ContentItem
  acknowledged: boolean
  onAcknowledge: () => void
}) {
  const flagged = item.status === 'flagged'
  return (
    <div
      className={`verify-card verify-card--${item.status}`}
      data-testid="verify-card"
      data-status={item.status}
      data-acknowledged={acknowledged}
    >
      <div className="verify-card__head">
        <StatusBadge status={item.status} />
        <span className="t-caption verify-card__confidence" data-testid="verify-confidence">
          confidence <span className="mono">{item.confidence === null ? '—' : item.confidence.toFixed(2)}</span>
        </span>
      </div>
      {/* 사유는 검증기가 적은 `verify.source`다. 정답을 성만 적는 식의 표기 불일치까지
          **무엇을 고쳐야 하는지가 보이는** 문장으로 들어온다 (확정 스펙 3.2). */}
      <p className="t-body verify-card__reason" data-testid="verify-reason">
        {item.source ?? (flagged ? '검증 단계가 결함을 찾았다.' : '검증이 완료되지 않았다.')}
      </p>
      <div className="verify-card__bar" aria-hidden="true">
        <div
          className={`verify-card__fill verify-card__fill--${item.status}`}
          style={{ width: `${Math.round((item.confidence ?? 0) * 100)}%` }}
        />
      </div>
      <div className="verify-card__foot">
        {acknowledged
          ? (
            <span className="pill pill--saved" data-testid="acknowledged">
              <Icon name="check" size={12} />
              확인함
            </span>
            )
          : (
            <button type="button" className="button" data-testid="acknowledge" onClick={onAcknowledge}>
              <Icon name="check" />
              확인함으로 표시
            </button>
            )}
        {/* 시안이 여기서 `verify.status`를 덮도록 그렸던 자리다. 무엇이 어디에 남는지
            말해 두지 않으면 다음 사람이 같은 곳으로 되돌린다. */}
        <span className="t-caption">검증 결과는 그대로 두고 확인 사실만 프로젝트에 남는다.</span>
      </div>
    </div>
  )
}

