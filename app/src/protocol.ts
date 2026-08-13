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
  audio: {
    voice: string | null
    music: string | null
    sfx_volume: number
    /**
     * 낭독의 선형 게인 (#81). **이 필드가 생기기 전에 만들어진 run 디렉터리가 있으므로 없을
     * 수 있고**, 그때의 뜻은 `DEFAULT_VOICE_VOLUME`이다 — 읽는 쪽은 `voiceVolume(project)`를
     * 쓴다.
     */
    voice_volume?: number
  }
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
    /**
     * 사람이 얹은 장면 편집 (#82). **앱이 쓰지만 렌더러가 읽으므로 편집 상태가 아니다** —
     * `review`와 달리 프리뷰 지문에 들어간다.
     *
     * 이 필드가 생기기 전에 만들어진 run 디렉터리가 있으므로 없을 수 있다.
     */
    scene_overrides?: SceneOverride[]
  }
  /**
   * **앱이 소유하는 유일한 섹션이다** (#28). 렌더러가 읽지 않으므로 프리뷰 지문에서도 빠진다
   * (`schemas/project.py`의 `PREVIEW_BLIND_SECTIONS` — `audio`도 같은 이유로 빠져 있다, #81).
   *
   * 이 필드가 생기기 전에 만들어진 run 디렉터리가 있으므로 없을 수 있다. 읽는 쪽은
   * `review(project)`를 쓴다.
   */
  review?: Review
}

/**
 * 장면 하나에 사람이 얹은 편집 (`schemas/project.py`의 `_SCENE_OVERRIDE`).
 *
 * **키가 장면 인덱스가 아니다.** 인덱스는 문제를 추가·삭제하면 밀리고, 그러면 조정한 값이
 * 다른 장면에 붙는다 (#28이 새 문제 번호에서 같은 함정을 밟았다).
 */
export interface SceneOverride {
  role: Scene['role']
  /** `question`·`countdown`·`answer`에만 있다. 그 셋은 번호 없이 특정되지 않는다. */
  question_id?: number
  /** 사람이 조정한 길이. `scenes.json`의 `duration`은 그대로다 (PRD 14.1). */
  duration?: number
}

/** 두 목록 모두 문제 `id`이고 `scenes.json`의 `question_id`와 같은 값이다. */
export interface Review {
  /** 사람이 `flagged`/`unverified`를 보고 넘어가기로 한 문제. */
  acknowledged: number[]
  /** 낭독 문구가 바뀌어 오디오·자막이 낡은 문제. 지우는 것은 재생성(#77)이다. */
  stale: number[]
  /**
   * 장면 길이를 고쳐 `captions.srt`·`voice.mp3`가 어긋난 상태 (#82).
   *
   * **목록이 아니라 참·거짓이다** — 길이 하나를 고치면 그 뒤 장면의 시작 시각이 전부 밀려
   * 낡는 대상이 타임라인 전체다 (PRD 14.1).
   */
  timeline_stale?: boolean
}

const EMPTY_REVIEW: Review = { acknowledged: [], stale: [] }

/** 없는 `review`를 빈 값으로 읽는다. 호출부마다 `?? []`를 쓰면 한 곳을 빠뜨린다. */
export function review (project: Project): Review {
  return project.review ?? EMPTY_REVIEW
}

/**
 * `voice_volume`이 없는 프로젝트가 뜻하는 값 (`schemas/project.py`의 `DEFAULT_VOICE_VOLUME`).
 *
 * **여기 적힌 수가 백엔드와 갈리면 화면이 렌더와 다른 레벨을 말한다** — 프리셋 이름을 앱에
 * 적지 않는 것과 같은 종류의 위험이지만, 이 값은 목록이 아니라 "게인 1.0이 원본 레벨"이라는
 * 사실 하나라 백엔드 왕복을 두지 않는다 (`scenes.ts`의 `FIXED_DURATION_ROLES`와 같은 자리).
 */
export const DEFAULT_VOICE_VOLUME = 1

/** 없는 `voice_volume`을 기본값으로 읽는다. 호출부마다 `?? 1`을 쓰면 한 곳을 빠뜨린다. */
export function voiceVolume (project: Project): number {
  return project.audio.voice_volume ?? DEFAULT_VOICE_VOLUME
}

/** `scenes.json`의 장면 하나 (`schemas/scenes.py`). 확정 상태만 앱에 온다. */
export interface Scene {
  role: 'hook' | 'question' | 'countdown' | 'answer' | 'cta'
  duration: number
  text?: string
  /** 블록 내내 상단에 유지되는 문구. 정답 장면에서는 `text`가 정답, 이쪽이 질문이다. */
  heading?: string
  kicker?: string
  caption?: string
  narrate?: boolean
  audio?: string
  audio_duration?: number
  narration_offset?: number
  seconds?: number
  sfx?: string
  /** **장면 목록의 묶는 기준이다.** 앱은 이 필드가 있다는 것만 알고 무엇의 번호인지는 모른다. */
  question_id?: number
}

export interface Scenes {
  schema_version: number
  type: string
  scenes: Scene[]
}

/** 실패는 예외가 아니라 값이다. `code`가 화면 분기의 기준이다 (D2 확정 스펙 4장). */
export interface ApiError {
  /**
   * `not_found` | `schema` | `io` | `render` | `assets` | `bad_request` |
   * `unknown_method` | `internal` | `backend`
   */
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

/**
 * 번들 자막 스타일 프리셋 하나 (#79).
 *
 * **이름도 색도 백엔드에서 온다.** 프리셋은 `assets/`가 소유하고(D1 확정 스펙 6장) 앱은
 * `assets/`를 직접 읽지 못한다 — 동결 배포에서 그 디렉터리는 백엔드 실행 파일 옆이다.
 */
export interface CaptionStylePreset {
  name: string
  label: string
  /**
   * **기본으로 짝지어진 배경 프리셋 이름이다** (D1 확정 스펙 6.3의 ◎). 조합을 막는 값이
   * 아니라 스타일을 고를 때 배경이 함께 가는 근거다 — 9조합 전부 고를 수 있다.
   */
  background: string
  /** 색 역할 → `#RRGGBB` (`assets.CAPTION_COLOR_ROLES`). 견본을 그리는 데 쓴다. */
  colors: Record<string, string>
}

export interface BackgroundPreset {
  name: string
  label: string
  /** 1개면 단색, 2개면 위→아래 2스톱 그라디언트다 (D1 확정 스펙 6.2). */
  stops: string[]
}

/**
 * 배경으로 받는 사용자 파일 한 형식 (#80).
 *
 * **목록은 렌더러가 소유한다** (`video_renderer.BACKGROUND_FILE_KINDS`). 앱이 자기 목록을
 * 들면 앱이 받은 파일을 렌더가 거부할 수 있고, 그 어긋남은 파일을 고르는 순간이 아니라
 * 렌더 도중에 드러난다 (PRD 14.1).
 */
export interface BackgroundFileFormat {
  /** 소문자 확장자, 점 포함 (`.png`). */
  extension: string
  /** 이 확장자가 정하는 `background.kind`. */
  kind: string
}

export interface PresetsResult {
  caption_styles: CaptionStylePreset[]
  backgrounds: BackgroundPreset[]
  /** 이 필드가 없는 백엔드 세대가 있을 수 있다 (동결 배포, `ready` 이벤트의 `protocol`). */
  background_files?: BackgroundFileFormat[]
}

export interface ScenesResult {
  scenes_path: string
  scenes: Scenes
}

/**
 * 타입 전용 콘텐츠 산출물 (퀴즈는 `quiz.json`, 이슈 #28).
 *
 * **내용의 모양을 여기서 정하지 않는다.** 백엔드도 파일명과 검증을 레지스트리에서 받고
 * (`api._content_schema`) 이 계층은 통째로 옮길 뿐이다. 필드를 아는 것은 `src/types/`의
 * 타입 모듈뿐이고, 그 경계가 퀴즈 스펙 1.1이 앱 쪽에 그은 선이다.
 */
export interface ContentResult {
  content_path: string
  content: Content
}

export type Content = Record<string, unknown>

export interface SaveContentResult {
  content_path: string
  bytes: number
}

export interface PreviewResult {
  scene_index: number
  /** 프레임을 정하는 입력 전부의 지문. 바뀌면 캐시가 비고 다시 만든다. */
  signature: string
  scene_count: number
  /** 이번 호출이 FFmpeg를 지났는가. `false`면 캐시에서 왔다. */
  generated: boolean
  elapsed_ms: number | null
  /** PNG의 base64. `data:image/png;base64,`를 붙이면 그대로 `<img>`에 넣는다. */
  png: string
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
  /**
   * 배경 파일 하나를 고른다 (#80). 취소하면 `null`이다.
   *
   * **받는 확장자를 인자로 넘긴다.** 대화상자 필터를 main이 만들지만 목록은 백엔드가
   * 소유하므로(`presets.background_files`), main이 자기 목록을 들면 그 순간 두 벌이 된다.
   */
  pickBackgroundFile(extensions: string[]): Promise<string | null>
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
