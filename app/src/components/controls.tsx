// 편집 컨트롤 (D2 확정 스펙 2.3, 이슈 #28).
//
// 값을 들지 않는다 — 전부 제어 컴포넌트이고, 편집이 쌓이는 곳은 `App`의 콘텐츠 상태
// 하나다. #29의 공통 편집 컨트롤도 이 자리를 쓴다.

import type { ReactNode } from 'react'

export function Labeled ({ label, hint, children }: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="control" data-control={label}>
      <span className="t-label control__label">
        {label}
        {hint && <span className="t-caption control__hint">{hint}</span>}
      </span>
      {children}
    </label>
  )
}

export function TextArea ({ label, value, rows = 3, onChange }: {
  label: string
  value: string
  rows?: number
  onChange: (value: string) => void
}) {
  return (
    <Labeled label={label}>
      <textarea
        className="input input--area"
        data-testid={`input-${label}`}
        rows={rows}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Labeled>
  )
}

export function TextInput ({ label, value, onChange }: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Labeled label={label}>
      <input
        className="input"
        data-testid={`input-${label}`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Labeled>
  )
}

/**
 * 정수 입력.
 *
 * **빈 문자열을 값으로 올리지 않는다.** 지우는 동안 `0`이나 `NaN`이 콘텐츠에 들어가면
 * 스키마를 어긴 상태로 저장을 누를 수 있게 되고, 그때 실패하는 것은 저장 전체다.
 */
export function NumberInput ({ label, value, min, max, unit, onChange }: {
  label: string
  value: number
  min: number
  max?: number
  unit?: string
  onChange: (value: number) => void
}) {
  const clamp = (next: number) => Math.max(min, max === undefined ? next : Math.min(max, next))
  return (
    <Labeled label={label}>
      <span className="input input--number">
        <input
          type="number"
          data-testid={`input-${label}`}
          value={value}
          min={min}
          max={max}
          onChange={(event) => {
            const next = Number.parseInt(event.target.value, 10)
            if (Number.isFinite(next)) onChange(clamp(next))
          }}
        />
        {unit && <span className="t-caption">{unit}</span>}
      </span>
    </Labeled>
  )
}

/** 선택지가 서넛인 값. 펼치지 않아도 무엇이 있는지 보인다 (확정 스펙 3.2의 난이도). */
export function Segmented<T extends string> ({ label, value, options, onChange }: {
  label: string
  value: T
  options: ReadonlyArray<{ value: T, text: string }>
  onChange: (value: T) => void
}) {
  return (
    <Labeled label={label}>
      <span className="segmented" data-testid={`segmented-${label}`} role="group">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`segmented__option${option.value === value ? ' segmented__option--on' : ''}`}
            data-value={option.value}
            data-selected={option.value === value}
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
          >
            {option.text}
          </button>
        ))}
      </span>
    </Labeled>
  )
}
