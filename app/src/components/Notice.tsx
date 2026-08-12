// 실패와 경고를 그리는 자리 (D2 확정 스펙 4장).
//
// **색이 뜻을 가진다.** `danger`는 거부(값이 적용되지 않음), `warn`은 경고(적용되지만 봐야
// 하는 것)다. 스키마 위반은 거부이고, FFmpeg가 없는 것은 지금 당장 막지 않으므로 경고다.

import type { ReactNode } from 'react'

import type { ApiError } from '../protocol'
import { Icon } from './Icon'

export function Notice ({ kind, title, children, details }: {
  kind: 'danger' | 'warn'
  title: string
  children?: ReactNode
  details?: string[]
}) {
  return (
    <div className={`notice notice--${kind}`} role="alert" data-testid={`notice-${kind}`}>
      <Icon name="alert" className={`notice__icon--${kind}`} />
      <div className="notice__body">
        <div className="notice__title">{title}</div>
        {children && <div className="t-body" style={{ color: 'var(--text-2)' }}>{children}</div>}
        {details && details.length > 0 && (
          // **필드 경로가 그대로 온다** (`render.fps: …`). 사용자가 고칠 줄을 바로 찾는다.
          <ul className="notice__details">
            {details.map((line) => <li key={line}>{line}</li>)}
          </ul>
        )}
      </div>
    </div>
  )
}

/** 백엔드가 돌려준 실패 하나. `code`가 무엇을 안내할지 정한다. */
export function ErrorNotice ({ error }: { error: ApiError }) {
  return (
    <Notice kind="danger" title={error.message} details={error.details}>
      {ADVICE[error.code]}
    </Notice>
  )
}

const ADVICE: Record<string, string> = {
  schema: 'project.json이 계약을 어겼다. 아래 항목을 고치면 다시 열 수 있다.',
  not_found: '다른 디렉터리를 골라 다시 시도한다.',
  io: '원본 파일은 그대로다. 디스크 여유와 쓰기 권한을 확인한다.',
  backend: '백엔드 프로세스가 없다. 앱을 다시 시작한다.'
}
