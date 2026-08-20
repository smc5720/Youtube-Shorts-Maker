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
    /**
     * 배경음악 값 셋 (#35). **화면에 이 값을 고치는 칸이 아직 없다** — D2 확정 스펙에 자리가
     * 없어 경로는 config → `project.json` → 렌더로만 흐른다. 여기 적어 두는 것은 프로젝트를
     * 통째로 주고받는 계약을 문서로 남기기 위함이고, `audio` 섹션은 프리뷰 지문에서 빠져
     * 있으므로(#81) 이 값이 바뀌어도 프레임을 다시 만들지 않는다.
     *
     * 셋 다 없을 수 있다 — 이 필드가 생기기 전에 만들어진 run 디렉터리가 열린다.
     */
    music_volume?: number
    music_duck?: number
    music_duck_fade_sec?: number
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
  /**
   * 사람이 고친 자막 문구 (#83). 장면의 `text` 한 칸을 덮는다.
   *
   * **상단 문구(`heading`)와 해설(`caption`)은 들어오지 않는다** — `heading`은 한 문제의 세
   * 장면이 공유하는 값이라 한 장면에서 고치면 나머지가 갈리고, 해설은 콘텐츠 필드라 문제
   * 편집(#28)이 소유한다 (D2 확정 스펙 7.3).
   */
  text?: string
  /** 사람이 얹은 텍스트 오버레이 (#83). **빈 목록은 스키마가 거부한다** — 앱이 키를 지운다. */
  overlays?: Overlay[]
}

/**
 * 장면에 얹은 텍스트 하나 (`schemas/project.py`의 `_OVERLAY`, D2 확정 스펙 7.2).
 *
 * **후보 목록을 이 파일에 적지 않는다** — 9칸·색·크기·웨이트는 백엔드가 `presets`로 보낸다
 * (`OverlayContract`). 특히 웨이트를 앱이 들면 시안이 적은 400·600이 저장될 수 있고, 그때
 * **화면은 정상이고 렌더에서만** 멈춘다 (확정 스펙 7.1-2).
 */
export interface Overlay {
  id: string
  /** 줄바꿈 허용. 줄 하나가 `drawtext` 하나이고 자동 줄바꿈은 없다 (D1 확정 스펙 7.3). */
  text: string
  /** `top|mid|bottom-left|center|right` 9칸. */
  pos: string
  /** 고른 모서리에서의 거리(px, 1080x1920 기준). */
  offset: { x: number, y: number }
  /** `preset` | `white` | `muted`. 값이 아니라 자막 스타일의 색 역할을 가리킨다. */
  color: string
  size: number
  weight: number
  /** `"scene"`이면 장면 전체, 객체면 장면 시작 기준 구간이다. */
  timing: 'scene' | { start: number, dur: number }
}

/** 장면 전체를 덮는 `timing` (`schemas/project.py`의 `TIMING_SCENE`). */
export const TIMING_SCENE = 'scene'

/** 세 목록 모두 문제 `id`이고 `scenes.json`의 `question_id`와 같은 값이다. */
export interface Review {
  /** 사람이 `flagged`/`unverified`를 보고 넘어가기로 한 문제. */
  acknowledged: number[]
  /**
   * **음성까지 낡은 문제** — 낭독 문구가 바뀌었다. 지우는 것은 재생성(#77)이다.
   *
   * `captions_stale`과 겹칠 수 있고, 겹치면 강한 쪽인 이 상태가 화면에 선다 (#83).
   */
  stale: number[]
  /**
   * **자막만 낡은 문제** (#83) — 낭독으로 가지 않는 문구(퀴즈의 해설)만 바뀌어 `voice.mp3`는
   * 그대로 쓸 수 있다 (D2 확정 스펙 7.3).
   *
   * **장면의 자막 문구를 고친 것은 여기 적지 않는다** — `render.scene_overrides[].text`의
   * 존재가 그 기록이고, 그것은 `scenes.json`과 비교하면 나오는 값이다 (`orderStale`과 같은
   * 판단이다). 이 목록은 비교 기준이 "직전 값"뿐인 콘텐츠 편집만 담는다.
   *
   * 이 필드가 생기기 전에 만들어진 run 디렉터리가 있으므로 없을 수 있다.
   */
  captions_stale?: number[]
  /**
   * 사람이 장면에 얹은 편집(길이 #82, 자막 문구 #83)이 `captions.srt`·`voice.mp3`에 아직
   * 반영되지 않은 상태.
   *
   * **목록이 아니라 참·거짓이다** — 길이 하나를 고치면 그 뒤 장면의 시작 시각이 전부 밀려
   * 낡는 대상이 타임라인 전체이고, 문구는 문제에 속하지 않는 `hook`·`cta`에서도 고칠 수 있어
   * 항목 번호로는 표현되지 않는다 (PRD 14.1).
   *
   * **#77에서 문구 편집이 이 칸으로 들어왔다.** 그 전에는 `editedCaptions`(파일 비교)가 그
   * 근거였는데, 재생성이 **얹은 문구로** `captions.srt`를 만들면서 그 비교는 "낡았는가"가
   * 아니라 "고쳤는가"가 됐다 — 비교 기준이 파일에서 사라졌으므로 적어 둔다.
   */
  timeline_stale?: boolean
}

const EMPTY_REVIEW: Review = { acknowledged: [], stale: [] }

/** 없는 `review`를 빈 값으로 읽는다. 호출부마다 `?? []`를 쓰면 한 곳을 빠뜨린다. */
export function review (project: Project): Review {
  return project.review ?? EMPTY_REVIEW
}

/** 없는 `captions_stale`을 빈 목록으로 읽는다 (#83). `review`와 같은 이유로 함수다. */
export function captionsStale (value: Review): number[] {
  return value.captions_stale ?? []
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
   * `unknown_method` | `internal` | `backend` | `busy`
   */
  code: string
  message: string
  details: string[]
  /**
   * 기계가 낸 원문 (#30). 있으면 `mono`로 그리고 손대지 않는다 — ffmpeg stderr가 그것이다.
   *
   * **`details`와 다르다.** 그쪽은 사람이 읽는 줄의 목록(위반한 필드, 다음에 할 일)이다.
   */
  raw?: string
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

/**
 * 텍스트 오버레이가 고를 수 있는 것들 (#83).
 *
 * **배경 형식 목록과 같은 이유로 백엔드에서 온다** — 소유자는 `assets/`가 아니라 스키마와
 * 렌더러지만, 앱이 적어 두면 화면이 허용하는 값과 렌더가 아는 값이 갈린다는 점이 같다.
 */
export interface OverlayContract {
  /** 9칸. 문자열 그대로 `Overlay.pos`에 들어간다. */
  positions: string[]
  /** 색 이름과 그것이 가리키는 자막 스타일의 색 역할. 견본은 그 역할로 찾는다. */
  colors: Array<{ name: string, role: string }>
  sizes: number[]
  /** **번들 웨이트다** — 400·600이 오면 렌더가 `AssetError`로 멈춘다 (확정 스펙 7.1-2). */
  weights: number[]
}

export interface PresetsResult {
  caption_styles: CaptionStylePreset[]
  backgrounds: BackgroundPreset[]
  /** 이 필드가 없는 백엔드 세대가 있을 수 있다 (동결 배포, `ready` 이벤트의 `protocol`). */
  background_files?: BackgroundFileFormat[]
  /** 이 필드가 없는 백엔드 세대에서는 오버레이 편집이 열리지 않는다 (#83). */
  overlay?: OverlayContract
  /**
   * 배경음악으로 받는 확장자 (#35). **`kind`가 없는 것이 `background_files`와 갈리는
   * 지점이다** — 음악에는 확장자가 정하는 종류가 없다. 번들 음악이 없으므로(PRD 8장)
   * 프리셋 목록도 오지 않는다.
   */
  music_files?: string[]
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

/**
 * 최종 렌더의 결과 (#30). 실패는 `ApiError`로 오므로 여기에는 성공만 있다.
 *
 * **경로를 백엔드가 정한다** — 파일명은 `project.json`의 `render.output`이고 자리는 run
 * 디렉터리 루트다. 시안이 적은 `runs/…/out/final.mp4`는 틀린 값이다 (확정 스펙 1.3).
 */
export interface RenderResult {
  output_path: string
  bytes: number
  elapsed_ms: number
}

/**
 * 렌더 진행 알림 (#30). **요청과 짝이 없는 줄로 온다** (`onBackendEvent`).
 *
 * **퍼센트도 남은 시간도 없다.** 그 둘은 이 값에서 화면이 계산한다 — 백엔드가 아는 것은
 * 프레임 수와 그것이 어느 장면인지이고(`video_renderer.RenderProgress`), 어떻게 보일지는
 * 확정 스펙 3.3이 정한다.
 */
export interface RenderProgressEvent {
  event: 'render_progress'
  /** 이 진행이 어느 렌더 요청의 것인가. 재시도하면 이전 렌더의 늦은 알림이 올 수 있다. */
  id: number
  frame: number
  total_frames: number
  /** `scenes.json`의 장면 인덱스. 화면은 이것으로 역할 이름을 찾는다. */
  scene_index: number
  elapsed_ms: number
}

export const RENDER_PROGRESS = 'render_progress'

/** 알림 한 줄이 렌더 진행인가. 다른 `event`가 늘어도 이 함수만 본다. */
export function isRenderProgress (message: unknown): message is RenderProgressEvent {
  const value = message as Partial<RenderProgressEvent> | null
  return value?.event === RENDER_PROGRESS && typeof value.frame === 'number'
}

/**
 * 재생성의 결과 (#77). **실패는 `ApiError`로 오므로 여기에는 성공과 취소만 있다** —
 * 취소는 사용자가 누른 것이고, 오류로 오면 앱이 실패 카드를 그린다.
 */
export interface RegenerateResult {
  cancelled: boolean
  /** 취소되면 아래 값들이 없다 — 만든 것이 없기 때문이다. */
  scene_count?: number
  segment_count?: number
  /** 실제로 합성한 세그먼트 수. 자막만 낡은 편집은 0으로 끝난다 (D2 확정 스펙 7.3). */
  synthesized?: number
  cue_count?: number
  total_sec?: number
  dropped_overrides?: number
  elapsed_ms: number
}

/**
 * 재생성 진행 알림 (#77). 렌더(#30)와 같은 모양이지만 **단위가 프레임이 아니라 단계다.**
 *
 * 단계마다 걸리는 시간이 크게 다르므로(대부분이 세그먼트 합성이다) 퍼센트가 없다 —
 * 어느 단계인지와 그 안의 `n/m`이 전부다.
 */
export interface RegenerateProgressEvent {
  event: 'regenerate_progress'
  id: number
  /** `shorts_maker.regenerate.STEPS`의 한 이름. 모르는 이름은 화면이 그대로 보여 준다. */
  step: string
  /** 세그먼트가 아닌 단계에서는 둘 다 0이다. */
  done: number
  total: number
  elapsed_ms: number
}

export const REGENERATE_PROGRESS = 'regenerate_progress'

export function isRegenerateProgress (
  message: unknown
): message is RegenerateProgressEvent {
  const value = message as Partial<RegenerateProgressEvent> | null
  return value?.event === REGENERATE_PROGRESS && typeof value.step === 'string'
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
  /**
   * 렌더 결과를 파일 탐색기에서 보여준다 (#30). 성공 여부를 돌려준다.
   *
   * **바깥으로 나가는 요청이 아니다** — 파일 관리자를 여는 것이고 네트워크가 없다
   * (`shell.showItemInFolder`). 열지 못해도 완료 카드의 경로는 그대로 남는다.
   */
  reveal(path: string): Promise<boolean>
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
