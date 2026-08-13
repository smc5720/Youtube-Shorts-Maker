// 배경 사용자 파일 (#80) — 고른 경로를 `background`의 두 칸으로 옮긴다.
//
// **형식 목록이 이 파일에 없다.** 확장자도 `kind`도 백엔드가 보낸 표
// (`presets.background_files`, 출처는 `video_renderer.BACKGROUND_FILE_KINDS`)에서 오고,
// 여기 있는 것은 그 표를 읽는 방법뿐이다. 목록을 앱에도 적으면 앱이 받은 파일을 렌더가
// 거부할 수 있고, 그 어긋남은 파일을 고르는 순간이 아니라 렌더 도중에 드러난다 (PRD 14.1).

import type { BackgroundFileFormat } from './protocol'

/** 대화상자 필터에 넘길 확장자 — **점을 뗀다** (Electron의 `filters[].extensions` 형식). */
export function acceptedExtensions (formats: BackgroundFileFormat[]): string[] {
  return formats.map((format) => format.extension.replace(/^\./, ''))
}

/**
 * 고른 경로의 `background.kind`. 목록 밖 형식이면 `null`이고, 그때 값은 적용되지 않는다
 * (D2 확정 스펙 4장의 `danger`).
 *
 * 대소문자를 가리지 않는다 — 사용자 디스크의 파일명은 `.PNG`일 수 있고, 그것은 다른 형식이
 * 아니다. 백엔드도 같은 판단을 한다 (`background_kind`의 `suffix.lower()`).
 */
export function kindForFile (
  filePath: string, formats: BackgroundFileFormat[]
): string | null {
  const dot = filePath.lastIndexOf('.')
  if (dot < 0) return null
  const extension = filePath.slice(dot).toLowerCase()
  return formats.find((format) => format.extension.toLowerCase() === extension)?.kind ?? null
}

/** 거부를 화면에 그리는 데 필요한 것 — 무엇을 골랐고 무엇을 받는가. */
export interface BackgroundReject {
  path: string
  accepted: string[]
}
