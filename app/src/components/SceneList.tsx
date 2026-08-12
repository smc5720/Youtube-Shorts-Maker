// 좌측 장면 목록 296px — 역할 구분 · 문제 그룹 · 총 길이 (D2 확정 스펙 3.1, 이슈 #27).
//
// 읽기 전용이다. 순서 변경과 길이 조정은 #29가 이 행 위에 얹는다.

import {
  AXIS_MAX_SEC,
  TARGET_MAX_SEC,
  TARGET_MIN_SEC,
  durationState,
  groupScenes,
  sceneTitle,
  seconds,
  totalDuration
} from '../scenes'
import type { Scene } from '../protocol'

/** 역할 이름은 기계가 만든 값이라 그대로 mono로 띄운다 (확정 스펙 2.2). */
const ROLE_DOT: Record<Scene['role'], string> = {
  hook: 'neutral',
  question: 'accent',
  countdown: 'neutral',
  answer: 'ok',
  cta: 'neutral'
}

export function SceneList ({ scenes, selected, onSelect }: {
  scenes: Scene[]
  selected: number | null
  onSelect: (index: number) => void
}) {
  const groups = groupScenes(scenes)
  return (
    <aside className="panel panel--scenes" data-testid="scene-list">
      <div className="panel__head">
        <span className="t-heading">장면</span>
        <span className="mono panel__count">{scenes.length}</span>
      </div>
      <div className="panel__scroll">
        {groups.map((group, position) => (
          group.questionId === null
            ? group.rows.map((row) => (
              <Row
                key={row.index}
                index={row.index}
                scene={row.scene}
                selected={selected === row.index}
                onSelect={onSelect}
              />
              ))
            : (
              <div className="scene-group" key={`q${group.questionId}-${position}`} data-question={group.questionId}>
                {/* 머리글에 문제 번호가 온다. 검증 배지는 #28이 이 자리에 붙인다. */}
                <div className="scene-group__head t-label" data-testid="question-head">
                  문제 {group.questionId}
                </div>
                <div className="scene-group__rows">
                  {group.rows.map((row) => (
                    <Row
                      key={row.index}
                      index={row.index}
                      scene={row.scene}
                      selected={selected === row.index}
                      onSelect={onSelect}
                    />
                  ))}
                </div>
              </div>
              )
        ))}
      </div>
      <TotalDuration scenes={scenes} />
    </aside>
  )
}

function Row ({ index, scene, selected, onSelect }: {
  index: number
  scene: Scene
  selected: boolean
  onSelect: (index: number) => void
}) {
  return (
    <button
      type="button"
      className={`scene-row${selected ? ' scene-row--selected' : ''}`}
      data-testid="scene-row"
      data-index={index}
      data-role={scene.role}
      data-selected={selected}
      aria-pressed={selected}
      onClick={() => onSelect(index)}
    >
      {/* 색점은 거들 뿐이고 역할 이름이 그대로 적힌다 — 색만으로 갈리지 않는다 (확정 스펙 4장). */}
      <span className={`scene-row__dot scene-row__dot--${ROLE_DOT[scene.role]}`} aria-hidden="true" />
      <span className="scene-row__title">{sceneTitle(scene)}</span>
      <span className="mono scene-row__role">{scene.role}</span>
      <span className="mono scene-row__duration">{seconds(scene.duration)}</span>
    </button>
  )
}

function TotalDuration ({ scenes }: { scenes: Scene[] }) {
  const total = totalDuration(scenes)
  const state = durationState(total)
  const clamp = (value: number) => Math.max(0, Math.min(100, value))
  return (
    <div className="total" data-testid="total-duration" data-state={state}>
      <div className="total__row">
        <span className="t-label">총 길이</span>
        {/* **경고 색은 60초 초과에만 쓴다** (확정 스펙 1.8). 짧은 쪽은 정보다. */}
        <span className={`mono total__value total__value--${state}`} data-testid="total-value">
          {seconds(total)}
        </span>
      </div>
      <div className="total__bar" aria-hidden="true">
        <div
          className="total__target"
          style={{
            left: `${clamp((TARGET_MIN_SEC / AXIS_MAX_SEC) * 100)}%`,
            width: `${clamp(((TARGET_MAX_SEC - TARGET_MIN_SEC) / AXIS_MAX_SEC) * 100)}%`
          }}
        />
        <div
          className={`total__fill total__fill--${state}`}
          style={{ width: `${clamp((total / AXIS_MAX_SEC) * 100)}%` }}
        />
      </div>
      <div className="t-caption total__scale">
        목표 {TARGET_MIN_SEC}~{TARGET_MAX_SEC}초 · 축 0~{AXIS_MAX_SEC}초
      </div>
    </div>
  )
}
