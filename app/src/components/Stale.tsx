// 낡음 두 종류 (D2 확정 스펙 7.3, 이슈 #83).
//
// **색은 둘 다 `accent` 파랑이다** — 결함이 아니라 사용자가 해야 할 일이고, 주황으로 그리면
// `flagged`와 같은 종류로 읽힌다 (확정 스펙 4장). 그래서 갈리는 것은 색이 아니라 아이콘
// **모양**(사각 `↻` / 원형 `♪`)과 문구다 — 상태는 색·모양·문구 셋으로 갈려야 한다.
//
// **표시를 지우는 것은 재생성(#77)이다.** 이 이슈는 자리와 상태까지다.

import type { ReactNode } from 'react'

import { Icon, type IconName } from './Icon'

/** `captions`는 자막만, `audio`는 음성까지다. 둘이 함께 걸릴 수 있다. */
export type StaleKind = 'captions' | 'audio'

const KINDS: Record<StaleKind, {
  icon: IconName
  badge: string
  title: string
  body: string
}> = {
  captions: {
    icon: 'refresh',
    badge: '자막 재생성 필요',
    title: '자막만 낡았다',
    body: '자막 문구가 바뀌었다. captions.srt는 아직 옛 문구이고 음성은 그대로 쓴다.'
  },
  audio: {
    icon: 'music',
    badge: '음성까지 재생성 필요',
    title: '음성까지 낡았다',
    body: '낭독 문구가 바뀌었다. 지금 프리뷰와 렌더는 아직 옛 낭독을 쓴다.'
  }
}

/** 목록 행에 붙는 배지. 모양은 CSS가 정한다 (`.vbadge--stale-audio`가 원형이다). */
export function StaleBadge ({ kind }: { kind: StaleKind }) {
  const spec = KINDS[kind]
  return (
    <span
      className={`vbadge vbadge--stale vbadge--stale-${kind}`}
      data-testid="stale-badge"
      data-kind={kind}
    >
      <Icon name={spec.icon} size={12} />
      {spec.badge}
    </span>
  )
}

/**
 * 카드. `children`은 그 상태에서 할 수 있는 동작이 온다.
 *
 * **`audio`에는 S2에서 동작을 주지 않는다** — 재생성은 문제 편집(S3)에서 하고, 여기서는
 * 읽기 전용이다 (확정 스펙 7.3).
 */
export function StaleCard ({ kind, children }: { kind: StaleKind, children?: ReactNode }) {
  const spec = KINDS[kind]
  return (
    <div className="stale-card" data-testid="stale-card" data-kind={kind}>
      <Icon name={spec.icon} className={`stale-card__icon--${kind}`} />
      <div className="stale-card__body">
        <div className="stale-card__title">{spec.title}</div>
        <div className="t-caption">{spec.body}</div>
        {children}
      </div>
    </div>
  )
}
