// 재생성 — 편집을 반영해 장면·오디오·자막을 다시 만든다 (D2 확정 스펙 7.3, 이슈 #77).
//
// **화면 어디에서 시작하든 같은 실행이다.** 문제 편집(S3)의 낡음 카드와 렌더 화면(S5)의
// "재생성 실행"이 같은 백엔드 메서드를 부르고, 갈리는 것은 TTS 재합성이 일어나는지뿐이다 —
// 그 판단은 `audio/segments.json`이 한다 (#15).
//
// **상태 표시는 전역이다.** 진행 중에도 다른 화면을 볼 수 있어야 하는데(렌더와 같은 규칙,
// 확정 스펙 3.3) 시작한 화면에만 진행을 그리면 화면을 옮긴 사용자가 무슨 일이 도는지 알 수
// 없다. 그래서 알림 자리(`body__notices`)에 선다.

import type { ApiError, RegenerateProgressEvent, RegenerateResult } from '../protocol'
import { Icon } from './Icon'
import { Notice } from './Notice'

/**
 * 재생성이 어디까지 왔는가.
 *
 * **`cancelled`를 `failed`와 갈라 둔다** — 사용자가 누른 것이고, 산출물은 이전 상태 그대로다
 * (확정 스펙 4장의 `danger`는 "거부"이지 "내가 멈췄다"가 아니다).
 */
export type RegenerateState =
  | { kind: 'idle' }
  | { kind: 'running', progress: RegenerateProgressEvent | null }
  | { kind: 'done', result: RegenerateResult }
  | { kind: 'cancelled' }
  | { kind: 'failed', error: ApiError }

/**
 * 단계 이름 → 화면 문구 (`shorts_maker.regenerate.STEPS`).
 *
 * **모르는 이름은 그대로 보여 준다.** 동결 배포에서 앱과 백엔드의 세대가 갈릴 수 있고
 * (`ready` 이벤트의 `protocol`), 그때 단계 하나 때문에 진행 표시가 비면 안 된다.
 */
const STEPS: Record<string, string> = {
  content: '콘텐츠 읽는 중',
  scenes: '장면 구성하는 중',
  narration: '낭독 합성하는 중',
  timeline: '타임라인 확정하는 중',
  captions: '자막 만드는 중',
  commit: '산출물 바꿔 끼우는 중'
}

export function stepLabel (progress: RegenerateProgressEvent | null): string {
  if (progress === null) return '준비 중'
  const label = STEPS[progress.step] ?? progress.step
  return progress.total > 0 ? `${label} ${progress.done}/${progress.total}` : label
}

/**
 * 진행·결과·실패를 그리는 알림 하나. `idle`이면 아무것도 그리지 않는다.
 *
 * **진행 중에도 편집만 잠긴다** — 화면 이동은 막지 않는다 (확정 스펙 3.3).
 */
export function RegenerateNotice ({ state, onCancel, onDismiss }: {
  state: RegenerateState
  onCancel: () => void
  onDismiss: () => void
}) {
  if (state.kind === 'idle') return null

  if (state.kind === 'running') {
    return (
      <div className="notice notice--todo" role="status" data-testid="regenerate-progress">
        <Icon name="refresh" className="notice__icon--todo icon--spin" />
        <div className="notice__body">
          <div className="notice__title">재생성 중 — 편집만 잠긴다</div>
          <div className="t-body" data-testid="regenerate-step">
            {stepLabel(state.progress)}
          </div>
        </div>
        {/* **취소는 별도 요청 줄이다** (`api.method_cancel_regenerate`). 다음 단계 경계에서
            멈추므로 누른 뒤 한 단계가 더 돌 수 있고, 교체 전이라 잘린 산출물은 남지 않는다. */}
        <button
          type="button" className="button" data-testid="regenerate-cancel" onClick={onCancel}
        >
          취소
        </button>
      </div>
    )
  }

  if (state.kind === 'failed') {
    return (
      <Notice
        kind="danger"
        title={state.error.message}
        details={state.error.details}
        testid="regenerate-failed"
      >
        이전 scenes.json · captions.srt · voice.mp3 · project.json은 그대로다.
      </Notice>
    )
  }

  const summary = state.kind === 'cancelled'
    ? '재생성을 취소했다 — 산출물은 그대로다'
    : done(state.result)
  return (
    <div className="notice notice--ok" role="status" data-testid="regenerate-done">
      <Icon name="check" className="notice__icon--ok" />
      <div className="notice__body">
        <div className="notice__title">{summary}</div>
      </div>
      <button
        type="button" className="button" data-testid="regenerate-dismiss" onClick={onDismiss}
      >
        <Icon name="close" size={12} />
        닫기
      </button>
    </div>
  )
}

/**
 * 완료 문구. **합성한 세그먼트 수를 말한다** — 자막만 낡은 편집이 TTS 없이 끝난다는 것이
 * 여기서 드러나고, 그것이 실행 경로를 나누지 않은 근거다 (확정 스펙 7.3).
 */
function done (result: RegenerateResult): string {
  const seconds = (result.total_sec ?? 0).toFixed(1)
  return `재생성 완료 — 장면 ${result.scene_count ?? 0}개 · 낭독 재합성 ${result.synthesized ?? 0}개 · 총 ${seconds}초`
}

/** 시작 버튼. 자리마다 문구가 다르고 부르는 것은 같다 (확정 스펙 7.3). */
export function RegenerateButton ({ label, state, disabled, onStart, testid }: {
  label: string
  state: RegenerateState
  disabled?: boolean
  onStart: () => void
  testid: string
}) {
  return (
    <button
      type="button"
      className="button"
      data-testid={testid}
      disabled={state.kind === 'running' || disabled === true}
      onClick={onStart}
    >
      <Icon name="refresh" />
      {state.kind === 'running' ? '재생성 중…' : label}
    </button>
  )
}
