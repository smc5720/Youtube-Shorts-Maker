// 장면 목록을 화면이 쓰는 모양으로 바꾼다 (이슈 #27).
//
// **여기 있는 것은 표시 규칙이지 콘텐츠 지식이 아니다.** 묶는 기준은 `question_id`가 있다는
// 것 하나이고(`schemas/scenes.py`의 통과 필드), 그 번호가 무엇의 번호인지는 앱도 모른다 —
// 퀴즈 스펙 1.1의 경계가 앱 쪽에서도 같은 모양이다.

import type { Overlay, Project, Scene, SceneOverride } from './protocol'

/** 문제에 속한 장면. 이 셋만 `question_id`로 특정된다 (`schemas/project.py`의 `GROUPED_ROLES`). */
const GROUPED_ROLES: ReadonlyArray<Scene['role']> = ['question', 'countdown', 'answer']

/**
 * 길이를 사람이 고칠 수 없는 역할 (`schemas/project.py`의 `FIXED_DURATION_ROLES`).
 *
 * `countdown`의 `duration`은 `seconds`와 같아야 하고 그 값은 콘텐츠 필드다 — 문제 편집(#28)이
 * 소유한다 (D2 확정 스펙 7.1).
 */
export const FIXED_DURATION_ROLES: ReadonlyArray<Scene['role']> = ['countdown']

/** 이 장면을 가리키는 오버라이드 키. 인덱스가 아니라 역할 + 문제 번호다 (PRD 14.1). */
export function overrideKey (scene: Scene): SceneOverride {
  return GROUPED_ROLES.includes(scene.role) && scene.question_id !== undefined
    ? { role: scene.role, question_id: scene.question_id }
    : { role: scene.role }
}

/** 두 오버라이드가 같은 장면을 가리키는가. `undefined`와 `null`을 같게 본다. */
export function sameOverride (a: SceneOverride, b: SceneOverride): boolean {
  return a.role === b.role && (a.question_id ?? null) === (b.question_id ?? null)
}

function sameScene (override: SceneOverride, scene: Scene): boolean {
  return sameOverride(override, overrideKey(scene))
}

export function overrideFor (project: Project, scene: Scene): SceneOverride | null {
  return (project.render.scene_overrides ?? []).find((item) => sameScene(item, scene)) ?? null
}

/**
 * 사람이 얹은 편집을 반영한 장면 목록.
 *
 * **백엔드의 `video_renderer.apply_scene_overrides`와 같은 규칙이다.** 두 곳에 두는 이유는
 * 프리뷰가 왕복이라서다 — 방금 입력한 길이가 장면 목록과 총 길이에 즉시 보여야 하는데, 그
 * 값을 백엔드에 물어 오는 동안 화면이 옛 값에 머물면 편집 도구가 되지 못한다. 규칙이 "키가
 * 맞는 장면의 값을 갈아 끼운다" 한 줄이고 키·필드 이름이 스키마에서 오므로 갈릴 여지가 좁다.
 */
export function effectiveScenes (scenes: Scene[], project: Project): Scene[] {
  const overrides = project.render.scene_overrides ?? []
  if (overrides.length === 0) return scenes
  return scenes.map((scene) => {
    const override = overrides.find((item) => sameScene(item, scene))
    if (!override) return scene
    // **없는 키는 손대지 않는다** — 길이만 고친 장면의 문구가 사라지면 안 된다
    // (`video_renderer._scene_edits`가 백엔드 쪽 같은 규칙이다).
    const next = { ...scene }
    if (override.duration !== undefined) next.duration = override.duration
    if (override.text !== undefined) next.text = override.text
    return next
  })
}

/** 이 장면에 사람이 얹은 오버레이 (#83). 없으면 빈 목록이다. */
export function overlaysFor (project: Project, scene: Scene): Overlay[] {
  return overrideFor(project, scene)?.overlays ?? []
}

/**
 * 자막 문구가 고쳐진 장면의 **인덱스** (#83).
 *
 * **적어 두지 않고 계산한다.** `render.scene_overrides[].text`와 `scenes.json`의 `text`를
 * 비교하면 나오는 값이라, 기록하면 어느 쪽이 원본인지 모호해진다 (`App.orderStale`과 같은
 * 판단이다). 그래서 **되돌리면 표시도 사라진다** — 콘텐츠 편집의 `stale`이 되돌려도 남는 것과
 * 갈리는 이유는 그쪽에는 비교할 기준이 없기 때문이다 (#28).
 *
 * 값이 원본과 같으면 낡지 않았다 — 키가 있는지가 아니라 문구가 다른지를 본다.
 */
export function editedCaptions (scenes: Scene[], project: Project): number[] {
  const edited: number[] = []
  scenes.forEach((scene, index) => {
    const override = overrideFor(project, scene)
    if (override?.text !== undefined && override.text !== scene.text) edited.push(index)
  })
  return edited
}

/**
 * 이 장면의 낭독 길이. 낭독이 없는 장면은 `null`이다.
 *
 * **`hook`·`cta`·`countdown`에는 없다** — `narrate: true`는 `question`·`answer`뿐이고 나머지는
 * 고정 길이 장면이다. 2차 시안이 hook 2.1 / cta 2.6을 적었지만 그 값은 존재하지 않으며,
 * 없는 낭독을 근거로 경고를 띄우면 고칠 것이 없는 사용자가 경고를 본다 (확정 스펙 7.1).
 */
export function narrationLength (scene: Scene): number | null {
  return scene.narrate === true ? scene.audio_duration ?? null : null
}

/**
 * 이 길이가 낭독을 자르는가. 자르더라도 **값은 받는다** (확정 스펙 4장의 `warn`).
 *
 * 허용 오차는 `schemas/scenes.py`의 `_TOLERANCE`와 같은 값이다 — 두 값 모두 소수 셋째
 * 자리로 반올림해 기록하므로 같은 값이 부동소수점 비교에서 어긋날 수 있다.
 */
export function cutsNarration (scene: Scene, duration: number): boolean {
  const narration = narrationLength(scene)
  return narration !== null && duration + 1e-6 < narration
}

export interface SceneRow {
  index: number
  scene: Scene
}

export interface SceneGroup {
  /** `null`이면 묶이지 않은 장면(후킹·CTA)이다. */
  questionId: number | null
  rows: SceneRow[]
}

/**
 * 같은 `question_id`를 가진 **연속된** 장면을 한 묶음으로 만든다.
 *
 * 연속으로 제한하는 이유는 장면 배열이 곧 재생 순서이기 때문이다. 번호로만 모으면 순서가
 * 섞인 목록(#29의 장면 순서 변경)에서 화면이 실제 재생과 다른 그림을 그린다.
 */
export function groupScenes (scenes: Scene[]): SceneGroup[] {
  const groups: SceneGroup[] = []
  scenes.forEach((scene, index) => {
    const questionId = scene.question_id ?? null
    const last = groups[groups.length - 1]
    if (last && questionId !== null && last.questionId === questionId) {
      last.rows.push({ index, scene })
      return
    }
    groups.push({ questionId, rows: [{ index, scene }] })
  })
  return groups
}

export const TARGET_MIN_SEC = 45
export const TARGET_MAX_SEC = 60
/** 총 길이 막대의 축. 목표 구간이 56.25%~75%에 오도록 잡은 값이다 (확정 스펙 3.1). */
export const AXIS_MAX_SEC = 80

export function totalDuration (scenes: Scene[]): number {
  return scenes.reduce((sum, scene) => sum + (scene.duration ?? 0), 0)
}

/**
 * 총 길이를 어떻게 그릴지.
 *
 * **목표보다 짧은 것은 경고가 아니다** (확정 스펙 1.8). 3문제 구성은 38초대가 정상 산출이라
 * `total < 45`를 결함으로 그리면 고칠 것이 없는 사용자가 경고를 상시 본다. 경고 색은 유튜브
 * 쇼츠 상한을 넘는 쪽에만 쓴다.
 */
export function durationState (total: number): 'short' | 'target' | 'over' {
  if (total > TARGET_MAX_SEC) return 'over'
  return total < TARGET_MIN_SEC ? 'short' : 'target'
}

/** 장면 행에 적는 제목. 낭독 문구가 없는 장면(카운트다운)은 상단 문구로 대신한다. */
export function sceneTitle (scene: Scene): string {
  return scene.text ?? scene.heading ?? ''
}

export function seconds (value: number): string {
  return `${value.toFixed(1)}초`
}
