// 헤더 52px — 제목 · 경로(mono) · 화면 전환 · 저장 상태 pill · 동작 (D2 확정 스펙 3.1).

import { Icon } from './Icon'

/**
 * S2(장면·프리뷰) · S3(문제 편집) · S5(렌더). 셋이 서로 다른 분할이라 화면이 갈린다
 * (확정 스펙 3.1·3.2·3.3).
 *
 * **렌더도 화면이지 모달이 아니다** (#30). 진행 중에 다른 화면을 볼 수 있어야 하므로
 * (확정 스펙 3.3) 덮어 버리는 표현을 쓸 수 없다.
 */
export type View = 'scenes' | 'questions' | 'render'

export function Header ({
  projectPath, unsaved, busy, canSave, view, canEditContent, rendering,
  onView, onOpen, onSave
}: {
  projectPath: string | null
  unsaved: boolean
  busy: boolean
  canSave: boolean
  view: View
  canEditContent: boolean
  /** 렌더가 도는 중인가 (#30). 저장은 잠기고 화면 이동은 열려 있다. */
  rendering: boolean
  onView: (next: View) => void
  onOpen: () => void
  onSave: () => void
}) {
  return (
    <header className="header">
      <span className="t-title header__title">YouTube Shorts Maker</span>
      {projectPath && (
        <span className="viewswitch" data-testid="view-switch" role="group">
          <ViewTab view="scenes" current={view} text="장면" onView={onView} />
          {/* 편집기가 등록되지 않은 타입에는 이 탭이 없다. 렌더는 콘텐츠를 몰라도 된다. */}
          {canEditContent && (
            <ViewTab view="questions" current={view} text="문제 편집" onView={onView} />
          )}
        </span>
      )}
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
        <button className="button" onClick={onSave} disabled={!canSave || busy || rendering}>
          <Icon name="save" />
          저장
        </button>
        {/* **렌더는 화면 이동이지 실행이 아니다** (#30). 여기서 곧바로 시작하면 확인이 필요한
            문제를 사용자가 보기 전에 인코딩이 돈다 — 게이트는 S5의 체크박스다. */}
        {projectPath && (
          <button
            className="button button--primary"
            data-testid="header-render"
            data-selected={view === 'render'}
            onClick={() => onView('render')}
          >
            렌더
          </button>
        )}
      </div>
    </header>
  )
}

function ViewTab ({ view, current, text, onView }: {
  view: View
  current: View
  text: string
  onView: (next: View) => void
}) {
  return (
    <button
      type="button"
      className={`viewswitch__tab${view === current ? ' viewswitch__tab--on' : ''}`}
      data-view={view}
      data-selected={view === current}
      aria-pressed={view === current}
      onClick={() => onView(view)}
    >
      {text}
    </button>
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
