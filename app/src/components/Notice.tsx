// 실패와 경고를 그리는 자리 (D2 확정 스펙 4장).
//
// **색이 뜻을 가진다.** `danger`는 거부(값이 적용되지 않음), `warn`은 경고(적용되지만 봐야
// 하는 것), `todo`는 사용자가 해야 할 일이다. 스키마 위반은 거부이고, FFmpeg가 없는 것은
// 지금 당장 막지 않으므로 경고이며, 재생성이 필요한 것은 결함이 아니라 할 일이다 —
// **주황으로 그리면 `flagged`와 같은 종류로 읽힌다** (#28).

import type { ReactNode } from 'react'

import type { ApiError } from '../protocol'
import { Icon } from './Icon'

export function Notice ({ kind, title, children, details, testid }: {
  kind: 'danger' | 'warn' | 'todo'
  title: string
  children?: ReactNode
  details?: string[]
  testid?: string
}) {
  return (
    <div className={`notice notice--${kind}`} role="alert" data-testid={testid ?? `notice-${kind}`}>
      <Icon name={kind === 'todo' ? 'refresh' : 'alert'} className={`notice__icon--${kind}`} />
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
  // **어느 파일인지는 `message`가 말한다.** 여기서 파일 이름을 다시 적으면 저장 경로가
  // 둘(프로젝트·콘텐츠)이 된 지금 한쪽이 틀린 안내를 하게 된다 (#28).
  schema: '계약을 어긴 값이 있다. 원본 파일은 그대로이고, 아래 항목이 어느 필드인지 말한다.',
  not_found: '다른 디렉터리를 골라 다시 시도한다.',
  io: '원본 파일은 그대로다. 디스크 여유와 쓰기 권한을 확인한다.',
  render: '프리뷰 프레임을 만들지 못했다. 프로젝트를 읽고 고치는 것은 그대로 된다.',
  backend: '백엔드 프로세스가 없다. 앱을 다시 시작한다.'
}
