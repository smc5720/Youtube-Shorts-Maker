// 열린 프로젝트가 무엇인지 — **셸이 보여줄 수 있는 전부다.**
//
// 장면 목록과 프리뷰는 #27, 문제 편집은 #28, 자막 스타일·배경·볼륨 편집은 #29가 채운다
// (D2 확정 스펙 0장). 여기서는 `project.json`이 실제로 읽혔다는 것과 그 값이 무엇인지만
// 보인다 — 값은 전부 기계가 만든 것이므로 mono다 (확정 스펙 2.2).

import type { Project } from '../protocol'

export function ProjectSummary ({ project, runDir }: { project: Project, runDir: string }) {
  const { render, background, audio } = project
  const rows: Array<[string, string]> = [
    ['타입', project.type],
    ['run 디렉터리', runDir],
    ['장면', project.scenes],
    ['배경', `${background.kind} · ${background.value}`],
    ['자막 스타일', render.caption_style],
    ['영상 규격', `${render.width}x${render.height} · ${render.fps}fps`],
    ['출력 파일', render.output],
    ['낭독 트랙', audio.voice ?? '(없음)'],
    ['효과음 게인', String(audio.sfx_volume)],
    ['cta 문구', `${render.cta_punch} / ${render.cta_tail}`]
  ]

  return (
    <div className="card summary">
      <div className="t-heading">프로젝트</div>
      <div className="summary__grid">
        {rows.map(([label, value]) => (
          <Row key={label} label={label} value={value} />
        ))}
      </div>
      <div className="t-body summary__next">
        장면 목록과 세로형 프리뷰는 다음 단계에서 이 자리에 들어온다.
      </div>
    </div>
  )
}

function Row ({ label, value }: { label: string, value: string }) {
  return (
    <>
      <div className="t-label">{label}</div>
      <div className="summary__value" data-field={label}>{value}</div>
    </>
  )
}
