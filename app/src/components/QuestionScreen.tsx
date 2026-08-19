// S3 문제 편집 — 좌 360px 목록 + 우 편집 폼 (D2 확정 스펙 3.2, 이슈 #28).
//
// **S2의 3분할이 아니라 2분할이다.** 문제를 고치는 동안 프리뷰는 바뀌지 않는다 —
// `scenes.json`은 그대로이고 새 낭독은 재생성(#77) 뒤에야 나온다. 바뀌지 않는 그림에
// 폭을 내주면 정작 고쳐야 할 폼이 좁아진다.
//
// **폼의 내용은 이 파일이 모른다.** 타입 모듈이 그리고(`src/types/`), 여기는 목록과
// 순서·추가·삭제까지다.

import type { Content } from '../protocol'
import type { ContentItem, ContentModule } from '../types'
import { Icon } from './Icon'
import { RegenerateButton, type RegenerateState } from './Regenerate'
import { StaleBadge } from './Stale'
import { StatusBadge } from './StatusBadge'

export function QuestionScreen ({
  module: type, content, items, selectedId, acknowledged, stale, captionsStale, locked,
  regenerate, onSelect, onChange, onAcknowledge, onAdd, onRemove, onMove, onRegenerate
}: {
  module: ContentModule
  content: Content
  items: ContentItem[]
  selectedId: number | null
  acknowledged: number[]
  /** 음성까지 낡은 문제 (#83). 낭독 문구가 바뀐 것이다. */
  stale: number[]
  /** 자막만 낡은 문제 (#83). 해설처럼 낭독으로 가지 않는 문구만 바뀐 것이다. */
  captionsStale: number[]
  /**
   * 렌더나 재생성이 도는 중인가 (#30, #77). 폼과 목록 동작이 잠긴다 — **판정은 `App`에
   * 있다**(`lockedRef`), 여기서는 그것을 보여 준다 (확정 스펙 3.3).
   */
  locked: boolean
  /** 재생성 상태 (#77). 낡음 카드의 버튼이 이것을 보고 문구를 바꾼다. */
  regenerate: RegenerateState
  onSelect: (id: number) => void
  onChange: (next: Content) => void
  onAcknowledge: (id: number) => void
  onAdd: () => void
  onRemove: (id: number) => void
  onMove: (id: number, delta: number) => void
  /** 낡음 카드의 재생성 (#77). **렌더 화면의 버튼과 같은 실행이다.** */
  onRegenerate: () => void
}) {
  const selected = items.find((item) => item.id === selectedId) ?? null
  const position = items.findIndex((item) => item.id === selectedId)
  const Editor = type.Editor

  return (
    <div className="split split--questions">
      <aside className="panel panel--questions" data-testid="question-list">
        <div className="panel__head">
          <span className="t-heading">문제</span>
          <span className="mono panel__count">{items.length}</span>
        </div>
        <div className="panel__scroll">
          {items.map((item, index) => (
            <button
              key={item.id}
              type="button"
              className={`qrow${item.id === selectedId ? ' qrow--selected' : ''}`}
              data-testid="question-row"
              data-id={item.id}
              data-selected={item.id === selectedId}
              aria-pressed={item.id === selectedId}
              onClick={() => onSelect(item.id)}
            >
              <span className="qrow__top">
                <span className="mono qrow__number">Q{index + 1}</span>
                {/* 확인한 문제는 배지를 지우지 않는다 — 검증 결과는 그대로이고 사람이
                    봤다는 사실이 더해질 뿐이다 (확정 스펙 1.4). */}
                {item.status !== 'verified' && <StatusBadge status={item.status} />}
                {acknowledged.includes(item.id) && (
                  <span className="vbadge vbadge--acknowledged" data-testid="acknowledged-badge">
                    <Icon name="check" size={12} />
                    확인함
                  </span>
                )}
                {/* 장면 목록의 머리글과 같은 규칙이다 — 강한 쪽만 그린다 (#83). */}
                {stale.includes(item.id)
                  ? <StaleBadge kind="audio" />
                  : captionsStale.includes(item.id) && <StaleBadge kind="captions" />}
                <span className="t-caption qrow__tag">{item.tag}</span>
              </span>
              <span className="qrow__title">{item.title}</span>
            </button>
          ))}
        </div>
        <div className="panel__foot">
          <button type="button" className="button" data-testid="question-add" onClick={onAdd}>
            문제 추가
          </button>
        </div>
      </aside>

      <section
        className={`panel panel--editor${locked ? ' panel--locked' : ''}`}
        data-testid="question-editor"
        data-locked={locked}
      >
        {selected
          ? (
            <>
              <div className="panel__head">
                <span className="t-heading">Q{position + 1}</span>
                <span className="panel__actions">
                  <button
                    type="button" className="button" data-testid="question-up"
                    disabled={position <= 0} onClick={() => onMove(selected.id, -1)}
                  >
                    위로
                  </button>
                  <button
                    type="button" className="button" data-testid="question-down"
                    disabled={position >= items.length - 1} onClick={() => onMove(selected.id, 1)}
                  >
                    아래로
                  </button>
                  {/* **마지막 하나는 지울 수 없다.** 문제가 없는 퀴즈는 스키마를 어기고,
                      저장할 때 실패하는 것보다 누를 수 없는 편이 낫다. */}
                  <button
                    type="button" className="button" data-testid="question-remove"
                    disabled={items.length <= 1} onClick={() => onRemove(selected.id)}
                  >
                    삭제
                  </button>
                </span>
              </div>
              <div className="panel__scroll">
                <Editor
                  content={content}
                  item={selected}
                  acknowledged={acknowledged.includes(selected.id)}
                  stale={stale.includes(selected.id)}
                  captionsStale={captionsStale.includes(selected.id)}
                  // **버튼을 셸이 만들어 내려보낸다** (#77). 문구는 낡음 종류에 따라 갈리지만
                  // 부르는 것은 하나이고, 그 판단도 실행도 타입의 지식이 아니다.
                  regenerate={
                    <RegenerateButton
                      label={stale.includes(selected.id) ? '음성까지 재생성' : '자막 재생성'}
                      state={regenerate}
                      disabled={locked}
                      onStart={onRegenerate}
                      testid="stale-regenerate"
                    />
                  }
                  onChange={onChange}
                  onAcknowledge={() => onAcknowledge(selected.id)}
                />
              </div>
            </>
            )
          : (
            <div className="panel__scroll">
              <div className="t-caption">왼쪽에서 문제를 고른다.</div>
            </div>
            )}
      </section>
    </div>
  )
}
