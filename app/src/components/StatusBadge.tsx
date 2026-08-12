// 검증 상태 배지 — 색·아이콘·문구 셋으로 갈린다 (D2 확정 스펙 4장, 이슈 #28).
//
// **색만으로 구분하지 않는다**는 것이 브리프 4.3의 요구였다. `unverified`는 점선 테두리에
// `?` 기호를 쓰고, 문구도 셋이 다르다.
//
// `VerifyStatus`는 타입 어휘가 아니다 — 검증 단계가 있는 타입이면 어느 쪽이든 이 세 상태를
// 낸다(퀴즈 스펙 5.2). 그래서 셸이 그린다.

import type { VerifyStatus } from '../types'
import { Icon } from './Icon'

const TEXT: Record<VerifyStatus, string> = {
  verified: 'verified',
  flagged: 'flagged · 확인 필요',
  unverified: 'unverified · 확인 필요'
}

export function StatusBadge ({ status }: { status: VerifyStatus }) {
  return (
    <span className={`vbadge vbadge--${status}`} data-testid="verify-badge" data-status={status}>
      {status === 'verified' && <Icon name="check" size={12} />}
      {status === 'flagged' && <Icon name="alert" size={12} />}
      {/* 점선과 `?`가 `flagged`와 갈리는 지점이다. 아이콘 자리를 비우면 두 상태가 같은
          모양이 되고 색 하나에만 기대게 된다. */}
      {status === 'unverified' && <span className="vbadge__glyph" aria-hidden="true">?</span>}
      {TEXT[status]}
    </span>
  )
}
