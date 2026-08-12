// 백엔드 계약 — `shorts_maker.api`가 보내는 것의 타입 (이슈 #26).
//
// **필드 이름의 출처는 `src/shorts_maker/schemas/`다.** 여기 있는 타입은 그것을 옮겨 적은
// 것이지 정하는 곳이 아니다 (PRD 7.10). 프로젝트를 통째로 넘겨받아 통째로 돌려주므로,
// 스키마에 필드가 늘어도 앱이 값을 잃지 않는다 — 편집하는 필드만 이름으로 안다.

export interface Project {
  schema_version: number
  type: string
  language: string
  scenes: string
  background: { kind: string; value: string }
  audio: { voice: string | null; music: string | null; sfx_volume: number }
  render: {
    width: number
    height: number
    fps: number
    output: string
    caption_style: string
    font_path: string | null
    cta_punch: string
    cta_tail: string
    caption_onset_sec: number
  }
}

/** 실패는 예외가 아니라 값이다. `code`가 화면 분기의 기준이다 (D2 확정 스펙 4장). */
export interface ApiError {
  /** `not_found` | `schema` | `io` | `bad_request` | `unknown_method` | `internal` | `backend` */
  code: string
  message: string
  details: string[]
}

export type Response<T> = { result: T; error?: undefined } | { result?: undefined; error: ApiError }

export interface OpenResult {
  run_dir: string
  project_path: string
  project: Project
}

export interface EnvResult {
  protocol: number
  python: string
  frozen: boolean
  tools: Record<string, { found: boolean; path: string | null }>
}

export interface SaveResult {
  project_path: string
  bytes: number
}

export interface AppContext {
  smoke: string | boolean | null
  backend: { command: string | null; pid: number | null; ready: boolean; failure: string | null }
  logPath: string | null
  /** 바깥으로 나가려던 요청. 비어 있어야 한다 (D2 발주서 3.3). */
  externalRequests: string[]
  versions: { electron: string; chrome: string; node: string }
}

export interface Bridge {
  call<T>(method: string, params?: Record<string, unknown>): Promise<Response<T>>
  context(): Promise<AppContext>
  pickRunDir(): Promise<string | null>
  /** 동기다 — main이 아는 상태가 화면보다 늦으면 확인 없이 닫히는 틈이 생긴다. */
  setUnsaved(value: boolean): void
  saveResult(ok: boolean): Promise<void>
  onBackendEvent(handler: (message: Record<string, unknown>) => void): () => void
  onSaveRequest(handler: () => void): () => void
  onMenu(handler: (action: 'open' | 'save') => void): () => void
}

declare global {
  interface Window {
    api: Bridge
    /** 스모크가 붙잡는 손잡이. `--smoke`로 띄웠을 때만 있다 (`app/smoke/run.mjs`). */
    __smoke?: Record<string, unknown>
  }
}

export const bridge = (): Bridge => window.api
