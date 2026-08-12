// 좌측 장면 목록 296px — 역할 구분 · 문제 그룹 · 총 길이 (D2 확정 스펙 3.1, 이슈 #27).
//
// 장면 행은 읽기 전용이다. 순서 변경과 길이 조정은 #29가 이 행 위에 얹는다.
// **문제 머리글에는 검증 배지가 붙고 눌러서 문제 편집으로 간다** (#28) — 같은 상태를
// 두 화면에서 다르게 표현하면 사용자가 별개 문제로 오인한다.

import type { Review } from '../protocol'
import type { ContentItem } from '../types'
import { Icon } from './Icon'
import { StatusBadge } from './StatusBadge'
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

export function SceneList ({ scenes, selected, items, review, onSelect, onOpenItem }: {
  scenes: Scene[]
  selected: number | null
  /** 콘텐츠의 편집 단위. 없으면(편집기 미등록·콘텐츠 없음) 머리글이 번호만 그린다. */
  items: ContentItem[]
  review: Review
  onSelect: (index: number) => void
  /** `null`이면 문제 편집 화면이 없다. 머리글이 버튼이 아니게 된다. */
  onOpenItem: ((id: number) => void) | null
}) {
  const groups = groupScenes(scenes)
  const byId = new Map(items.map((item) => [item.id, item]))
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
                <GroupHead
                  questionId={group.questionId}
                  item={byId.get(group.questionId) ?? null}
                  review={review}
                  onOpenItem={onOpenItem}
                />
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

/**
 * 문제 머리글 — 번호 · 검증 배지 · 확인·재생성 표시.
 *
 * **`verified`에는 배지를 그리지 않는다.** 정상인 것에까지 표시를 붙이면 목록이 배지로
 * 덮여 정작 봐야 할 `flagged`가 묻힌다 (확정 스펙 4장의 `ok`는 렌더 완료 쪽 색이다).
 */
function GroupHead ({ questionId, item, review, onOpenItem }: {
  questionId: number
  item: ContentItem | null
  review: Review
  onOpenItem: ((id: number) => void) | null
}) {
  const badges = (
    <>
      <span className="t-label" data-testid="question-head-number">문제 {questionId}</span>
      {item && item.status !== 'verified' && <StatusBadge status={item.status} />}
      {review.acknowledged.includes(questionId) && (
        <span className="vbadge vbadge--acknowledged" data-testid="acknowledged-badge">
          <Icon name="check" size={12} />
          확인함
        </span>
      )}
      {review.stale.includes(questionId) && (
        <span className="vbadge vbadge--stale" data-testid="stale-badge">
          <Icon name="refresh" size={12} />
          재생성 필요
        </span>
      )}
    </>
  )

  if (!onOpenItem) {
    return <div className="scene-group__head" data-testid="question-head">{badges}</div>
  }
  return (
    <button
      type="button"
      className="scene-group__head scene-group__head--link"
      data-testid="question-head"
      onClick={() => onOpenItem(questionId)}
    >
      {badges}
    </button>
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
