// 헤더 52px — 제목 · 경로(mono) · 저장 상태 pill · 동작 (D2 확정 스펙 3.1).

import { Icon } from './Icon'

export function Header ({ projectPath, unsaved, busy, canSave, onOpen, onSave }: {
  projectPath: string | null
  unsaved: boolean
  busy: boolean
  canSave: boolean
  onOpen: () => void
  onSave: () => void
}) {
  return (
    <header className="header">
      <span className="t-title header__title">YouTube Shorts Maker</span>
      {projectPath && (
        // **경로는 기계가 만든 값이므로 mono다** (확정 스펙 2.2). 길면 뒤가 남아야 어느
        // run인지 보이고, 시안이 적은 `runs/2026-…`가 아니라 실제 산출물 경로다 (1.3).
        <span className="mono header__path" data-testid="project-path" title={projectPath}>
          {projectPath}
        </span>
      )}
      <span className="header__spacer" />
      {projectPath && <SaveState unsaved={unsaved} />}
      <div className="header__actions">
        <button className="button" onClick={onOpen} disabled={busy}>
          <Icon name="folder" />
          프로젝트 열기
        </button>
        <button className="button button--primary" onClick={onSave} disabled={!canSave || busy}>
          <Icon name="save" />
          저장
        </button>
      </div>
    </header>
  )
}

function SaveState ({ unsaved }: { unsaved: boolean }) {
  // 색만으로 갈리지 않게 아이콘과 문구도 함께 바뀐다 (확정 스펙 4장).
  return unsaved
    ? (
      <span className="pill pill--unsaved" data-testid="save-state" data-state="unsaved">
        <Icon name="alert" size={12} />
        저장되지 않은 변경
      </span>
      )
    : (
      <span className="pill pill--saved" data-testid="save-state" data-state="saved">
        <Icon name="check" size={12} />
        저장됨
      </span>
      )
}
