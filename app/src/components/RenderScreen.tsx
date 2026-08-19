// S5 렌더 — 경고 → 진행 → 완료 → 실패 네 카드 (D2 확정 스펙 3.3, 이슈 #30).
//
// **게이트는 하나뿐이다.** 확인 체크박스로 막는 것은 `flagged`·`unverified`이고(퀴즈 스펙 5.2),
// 재생성 필요·짧은 장면 길이·저장되지 않은 변경은 같은 카드에 함께 서지만 렌더를 막지 않는다 —
// 색이 그 셋을 갈라 준다 (확정 스펙 4장: `warn`은 값이 적용되는 경고, `accent`는 할 일).
// 낡음으로 막으면 재생성(#77)이 없는 지금 앱에서 렌더를 아예 할 수 없다.
//
// **퍼센트와 남은 시간을 여기서 계산한다.** 백엔드가 보내는 것은 프레임 수와 장면 인덱스뿐이고
// (`RenderProgressEvent`), 그것을 어떻게 보일지는 이 화면의 몫이다.

import type { RenderProgressEvent, RenderResult, Scene } from '../protocol'
import { Icon } from './Icon'
import { Notice } from './Notice'
import { StatusBadge } from './StatusBadge'
import type { VerifyStatus } from '../types'

/** 확인이 필요한 문제 하나. **셸이 아는 것은 이 넷뿐이다** (`ContentItem`의 경계, #28). */
export interface RenderWarningItem {
  id: number
  /** 목록에서의 자리(1부터). `Q3`은 번호가 아니라 순서다 — S3의 목록과 같은 규칙이다. */
  position: number
  title: string
  status: VerifyStatus
  confidence: number | null
  source: string | null
}

/** 렌더를 막지 않는 경고 하나. `kind`가 색을 정한다 (확정 스펙 4장). */
export interface RenderNote {
  kind: 'warn' | 'todo'
  title: string
  body: string
}

/**
 * 렌더가 어디까지 왔는가.
 *
 * **`idle`과 `done`을 갈라 둔다** — 끝난 뒤에도 결과 카드가 남아야 사용자가 경로를 본다.
 * 다시 시작하면 그때 `running`으로 간다.
 */
export type RenderState =
  | { kind: 'idle' }
  | { kind: 'running', progress: RenderProgressEvent | null }
  | { kind: 'done', result: RenderResult }
  | { kind: 'failed', message: string, details: string[], raw: string }

export function RenderScreen ({
  state, warnings, notes, acknowledgedAll, scenes, canRender,
  onAcknowledgeAll, onStart, onReveal, onOpen
}: {
  state: RenderState
  /** 확인이 필요한 문제. 사람이 이미 확인한 것은 여기 오지 않는다 (확정 스펙 1.4). */
  warnings: RenderWarningItem[]
  notes: RenderNote[]
  /** 확인 체크박스 상태. **켜지 않으면 시작이 비활성이다** (확정 동작). */
  acknowledgedAll: boolean
  /** 진행 카드가 역할 이름을 찾는 데 쓴다. 사람이 얹은 편집이 반영된 목록이다. */
  scenes: Scene[]
  /** 장면 목록을 읽지 못했거나 렌더가 도는 중이면 시작할 수 없다. */
  canRender: boolean
  onAcknowledgeAll: (value: boolean) => void
  onStart: () => void
  onReveal: (path: string) => void
  /** 목록의 "열기" — S3의 그 문제로 간다 (확정 스펙 3.3). */
  onOpen: (id: number) => void
}) {
  const running = state.kind === 'running'
  const blocked = warnings.length > 0 && !acknowledgedAll
  return (
    <div className="render" data-testid="render-screen" data-state={state.kind}>
      {warnings.length > 0 && (
        <section className="rcard rcard--warn" data-testid="render-warnings">
          <header className="rcard__head">
            <Icon name="alert" className="notice__icon--warn" />
            <span className="t-heading">확인이 필요한 문제 {warnings.length}개</span>
          </header>
          <ul className="rlist">
            {warnings.map((item) => (
              <li className="rlist__row" key={item.id} data-testid="render-warning" data-id={item.id}>
                <span className="mono rlist__number">Q{item.position}</span>
                <StatusBadge status={item.status} />
                <span className="rlist__title">{item.title}</span>
                {/* `unverified`는 `confidence`가 없다 — 자리에 `—`를 쓴다 (확정 스펙 4장). */}
                <span className="mono rlist__confidence">
                  {item.confidence === null ? '—' : item.confidence.toFixed(2)}
                </span>
                <button
                  type="button"
                  className="button"
                  data-testid="render-warning-open"
                  onClick={() => onOpen(item.id)}
                >
                  열기
                </button>
              </li>
            ))}
          </ul>
          <label className="rcheck" data-testid="render-gate">
            <input
              type="checkbox"
              checked={acknowledgedAll}
              disabled={running}
              onChange={(event) => onAcknowledgeAll(event.target.checked)}
            />
            <span className="t-body">
              위 {warnings.length}개 문제를 확인했고 이대로 렌더한다
            </span>
          </label>
        </section>
      )}

      {notes.length > 0 && (
        <div className="render__notes" data-testid="render-notes">
          {notes.map((note) => (
            <Notice key={note.title} kind={note.kind} title={note.title} testid="render-note">
              {note.body}
            </Notice>
          ))}
        </div>
      )}

      {running && <ProgressCard progress={state.progress} scenes={scenes} />}

      {state.kind === 'done' && (
        <section className="rcard rcard--ok" data-testid="render-done">
          <header className="rcard__head">
            <Icon name="check" className="rcard__icon--ok" />
            <span className="t-heading">렌더 완료</span>
            <span className="mono rcard__meta">{secondsOf(state.result.elapsed_ms)}</span>
          </header>
          {/* **경로는 백엔드가 정한 값 그대로다** (확정 스펙 1.3). 앱이 조립하면 없는 경로를
              가리킬 수 있다. */}
          <div className="mono rcard__path" data-testid="render-output">{state.result.output_path}</div>
          <div className="t-caption">{megabytes(state.result.bytes)}</div>
          <div className="rcard__actions">
            <button
              type="button"
              className="button"
              data-testid="render-reveal"
              onClick={() => onReveal(state.result.output_path)}
            >
              <Icon name="folder" />
              파일 위치 열기
            </button>
          </div>
        </section>
      )}

      {state.kind === 'failed' && (
        <section className="rcard rcard--danger" data-testid="render-failed">
          <header className="rcard__head">
            <Icon name="alert" className="notice__icon--danger" />
            <span className="t-heading">렌더 실패</span>
          </header>
          <div className="t-body">{state.message}</div>
          {state.details.length > 0 && (
            <ul className="notice__details">
              {state.details.map((line) => <li key={line}>{line}</li>)}
            </ul>
          )}
          {/* 원문 오류는 `mono`이고 **사람이 읽는 원인과 갈라져 온다** (`ApiError.raw`).
              한 문자열에 섞이면 화면에서 다시 갈라야 하고 그 분리는 문구를 다듬는 순간
              깨진다. 실행 명령과 stderr를 run 디렉터리에 남기는 것은 #36의 몫이다. */}
          {state.raw && (
            <pre className="mono rcard__raw" data-testid="render-raw">{state.raw}</pre>
          )}
          <div className="rcard__actions">
            <button
              type="button"
              className="button"
              data-testid="render-copy"
              onClick={() => { void navigator.clipboard?.writeText(logText(state)) }}
            >
              로그 복사
            </button>
          </div>
        </section>
      )}

      <div className="render__start">
        <button
          type="button"
          className="button button--primary"
          data-testid="render-start"
          disabled={running || blocked || !canRender}
          onClick={onStart}
        >
          {state.kind === 'failed' ? '다시 시도' : '렌더 시작'}
        </button>
        {blocked && (
          <span className="t-caption" data-testid="render-blocked">
            확인 체크박스를 켜야 시작할 수 있다.
          </span>
        )}
      </div>
    </div>
  )
}

function ProgressCard ({ progress, scenes }: {
  progress: RenderProgressEvent | null
  scenes: Scene[]
}) {
  const percent = progress === null || progress.total_frames === 0
    ? 0
    : Math.min(100, Math.round((progress.frame / progress.total_frames) * 100))
  const scene = progress === null ? null : scenes[progress.scene_index] ?? null
  return (
    <section className="rcard rcard--accent" data-testid="render-progress" data-percent={percent}>
      <header className="rcard__head">
        <Icon name="refresh" className="notice__icon--todo" />
        <span className="t-heading">렌더 중</span>
      </header>
      <div className="rbar">
        <div className="rbar__fill" style={{ width: `${percent}%` }} />
      </div>
      <div className="mono rcard__meta" data-testid="render-progress-label">
        {percent}%
        {scene && progress && ` · ${progress.scene_index + 1}/${scenes.length} ${scene.role}`}
        {/* **남은 시간은 여기서 낸다.** 경과 시간과 진행 비율만 있으면 나오는 값이라
            백엔드가 실어 보내지 않는다. */}
        {progress && percent > 0 && ` · 남은 시간 ${remaining(progress, percent)}`}
      </div>
      <div className="t-caption">
        렌더 중에도 다른 화면을 볼 수 있다. 편집만 잠긴다.
      </div>
    </section>
  )
}

function remaining (progress: RenderProgressEvent, percent: number): string {
  const total = (progress.elapsed_ms / percent) * 100
  return secondsOf(Math.max(0, total - progress.elapsed_ms))
}

function secondsOf (ms: number): string {
  return `${(ms / 1000).toFixed(1)}초`
}

function megabytes (bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function logText (state: { message: string, details: string[] }): string {
  return [state.message, ...state.details].join('\n')
}
