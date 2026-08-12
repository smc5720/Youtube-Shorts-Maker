// 아이콘 — Lucide 16px / 스트로크 1.5px (D2 확정 스펙 1.6).
//
// **번들이다.** `lucide-react`(ISC)가 빌드 시점에 `dist/`로 들어가므로 실행 중 네트워크로
// 나가지 않는다. 시안의 `!` · `✓` · `×` · `↻` 자리에 각각 아래 넷이 들어간다.

import {
  Check,
  FolderOpen,
  RefreshCw,
  Save,
  TriangleAlert,
  X,
  type LucideIcon
} from 'lucide-react'

export const icons = {
  alert: TriangleAlert,
  check: Check,
  close: X,
  refresh: RefreshCw,
  folder: FolderOpen,
  save: Save
} satisfies Record<string, LucideIcon>

export type IconName = keyof typeof icons

export function Icon ({ name, size = 16, className }: {
  name: IconName
  size?: number
  className?: string
}) {
  const Glyph = icons[name]
  // 뜻은 옆의 문구가 지고 아이콘은 그것을 거든다 — 읽어 주는 도구가 같은 말을 두 번 하지
  // 않도록 숨긴다 (확정 스펙 4장: 상태는 색·모양·문구 셋으로 갈린다).
  return <Glyph size={size} strokeWidth={1.5} className={className} aria-hidden="true" />
}
