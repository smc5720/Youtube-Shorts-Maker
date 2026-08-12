// S1 프로젝트 열기 — **시안이 아직 없는 화면이다** (D2 확정 스펙 0장: 2차 배치).
// 그래서 토큰과 컴포넌트 인벤토리로만 조립한다. 시안이 오면 이 파일이 그것을 따라간다.

import { Icon } from './Icon'

export function OpenScreen ({ busy, onOpen }: { busy: boolean, onOpen: () => void }) {
  return (
    <div className="center">
      <div className="card open-card">
        <Icon name="folder" size={28} className="open-card__icon" />
        <div className="t-title">프로젝트 열기</div>
        <p className="t-body open-card__hint">
          쇼츠를 생성한 run 디렉터리를 고른다. 그 안의 <span className="mono">project.json</span>이
          편집과 렌더의 입력이다.
        </p>
        <button className="button button--primary button--large" onClick={onOpen} disabled={busy}>
          <Icon name="folder" />
          run 디렉터리 고르기
        </button>
        {/* 산출물 경로는 실제 규격을 적는다 — 시안이 적은 `runs/…`는 없는 경로다 (1.3). */}
        <div className="t-caption">
          기본 위치: <span className="mono">outputs/run-{'{timestamp}'}/</span>
        </div>
      </div>
    </div>
  )
}
