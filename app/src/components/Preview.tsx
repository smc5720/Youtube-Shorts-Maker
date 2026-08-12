// 중앙 프리뷰 — 9:16 정지 프레임 (D2 확정 스펙 3.1, PRD 7.9, 이슈 #27).
//
// **높이에 맞추고 폭을 계산한다.** 시안의 620x348.75는 창 높이 900일 때의 값이고, 최소
// 창(720)에서는 세로가 먼저 걸린다 — 폭을 고정하면 프리뷰가 잘린다 (확정 스펙 3.1). 비율은
// CSS `aspect-ratio`가 잡으므로 이 컴포넌트에 숫자가 없다.
//
// 대기 표현이 두 가지인 것이 이 화면의 핵심이다.
//
// - **다른 장면을 골랐다** → 이전 프레임을 지운다. 남겨 두면 방금 고른 장면의 그림으로 읽힌다
// - **같은 장면의 값이 바뀌었다** → 이전 프레임을 유지하고 갱신 배지를 띄운다. 지우면 값 하나
//   고칠 때마다 화면이 비어 무엇이 어떻게 바뀌는지 비교할 수 없다

import type { ApiError, Scene } from '../protocol'
import { seconds } from '../scenes'
import { Icon } from './Icon'

export interface PreviewFrame {
  index: number
  png: string
  elapsedMs: number | null
}

export function Preview ({ frame, scene, pending, error }: {
  frame: PreviewFrame | null
  scene: Scene | null
  /** 요청이 떠 있는 장면. 없으면 기다리는 것이 없다. */
  pending: number | null
  error: ApiError | null
}) {
  const state = error && !frame
    ? 'error'
    : frame
      ? (pending === null ? 'ready' : 'stale')
      : (pending === null ? 'empty' : 'loading')

  return (
    <section className="preview" data-testid="preview" data-state={state} data-scene={frame?.index ?? ''}>
      <div className="preview__stage">
        <div className="preview__frame">
          {frame
            ? <img className="preview__image" src={`data:image/png;base64,${frame.png}`} alt="" />
            : <Placeholder state={state} error={error} />}
          {state === 'stale' && (
            <span className="badge badge--refresh" data-testid="preview-refresh">
              <Icon name="refresh" size={12} />
              갱신 중
            </span>
          )}
        </div>
      </div>
      <div className="t-caption preview__caption" data-testid="preview-caption">
        {scene && frame
          ? <>장면 {frame.index} · <span className="mono">{scene.role}</span> · {seconds(scene.duration)} 중 대표 프레임{frame.elapsedMs === null ? '' : ` · ${frame.elapsedMs}ms`}</>
          : '장면을 고르면 그 장면의 대표 프레임이 나온다.'}
      </div>
    </section>
  )
}

function Placeholder ({ state, error }: { state: string, error: ApiError | null }) {
  if (state === 'error' && error) {
    return (
      <div className="preview__placeholder preview__placeholder--error">
        <Icon name="alert" size={20} />
        <div className="t-body">{error.message}</div>
        {error.details.length > 0 && (
          <div className="t-caption">{error.details[0]}</div>
        )}
      </div>
    )
  }
  return (
    <div className="preview__placeholder">
      {state === 'loading'
        ? <>
          <Icon name="refresh" size={20} />
          <div className="t-caption">프레임을 만드는 중</div>
        </>
        : <div className="t-caption">장면을 고른다</div>}
    </div>
  )
}
