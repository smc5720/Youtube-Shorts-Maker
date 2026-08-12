// 장면 목록을 화면이 쓰는 모양으로 바꾼다 (이슈 #27).
//
// **여기 있는 것은 표시 규칙이지 콘텐츠 지식이 아니다.** 묶는 기준은 `question_id`가 있다는
// 것 하나이고(`schemas/scenes.py`의 통과 필드), 그 번호가 무엇의 번호인지는 앱도 모른다 —
// 퀴즈 스펙 1.1의 경계가 앱 쪽에서도 같은 모양이다.

import type { Scene } from './protocol'

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
