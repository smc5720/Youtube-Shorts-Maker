// 앱 셸 — 프로젝트를 열고, 고친 것을 저장하고, 실패를 그린다 (이슈 #26).
// 그 위에 장면 목록 · 세로형 프리뷰 · 속성 패널 3분할(#27)과 문제 편집 화면(#28)이 있다.
//
// 여기서 정해 두는 것은 편집이 어떻게 생겼는지가 아니라 **편집이 어디에 쌓이고 언제 파일이
// 되는가**다. 공통 편집(#29)과 렌더 실행(#30)이 이 상태 위에 올라온다.
//
// - `project`와 `content`는 통째로 오가는 값이다. 필드를 골라 다시 조립하지 않으므로 스키마가
//   늘어도 앱이 값을 잃지 않는다. **파일이 둘이므로 저장도 둘이다** — 한 번에 두 파일을 쓰고
//   둘 다 성공해야 저장됨으로 돌아온다
// - 저장 여부는 플래그가 아니라 **마지막으로 파일과 같았던 내용(`baseline`)과의 비교**다.
//   고쳤다가 되돌린 것을 "변경"으로 세면 사용자가 없는 변경을 저장하게 된다
// - 프리뷰를 다시 만들지도 같은 비교로 정한다. 프로젝트 내용이 곧 그림을 정하므로, 무엇이
//   프레임에 영향을 주는지 앱이 알 필요가 없다 (`_signature`가 백엔드 쪽 같은 판단이다)
// - **콘텐츠 편집은 프리뷰를 다시 만들지 않는다.** `scenes.json`이 그대로이기 때문이고,
//   새 낭독이 그림에 도달하는 것은 재생성(#77) 뒤다

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { acceptedExtensions, kindForFile, type BackgroundReject } from './background'
import { Header, type View } from './components/Header'
import { ErrorNotice, Notice } from './components/Notice'
import { OpenScreen } from './components/OpenScreen'
import { Preview, type PreviewFrame } from './components/Preview'
import { Properties } from './components/Properties'
import { QuestionScreen } from './components/QuestionScreen'
import { RegenerateNotice, type RegenerateState } from './components/Regenerate'
import {
  RenderScreen,
  type RenderNote,
  type RenderState,
  type RenderWarningItem
} from './components/RenderScreen'
import { SceneList } from './components/SceneList'
import {
  cutsNarration,
  editedCaptions,
  effectiveScenes,
  overrideKey,
  sameOverride
} from './scenes'
import { contentModule } from './types'
import {
  bridge,
  captionsStale,
  isRegenerateProgress,
  isRenderProgress,
  review,
  TIMING_SCENE,
  type ApiError,
  type AppContext,
  type CaptionStylePreset,
  type Content,
  type ContentResult,
  type EnvResult,
  type OpenResult,
  type Overlay,
  type PresetsResult,
  type PreviewResult,
  type Project,
  type RegenerateResult,
  type RenderResult,
  type Review,
  type SaveContentResult,
  type Scene,
  type SceneOverride,
  type Scenes,
  type ScenesResult,
  type SaveResult
} from './protocol'

export function App () {
  const api = bridge()
  const [context, setContext] = useState<AppContext | null>(null)
  const [environment, setEnvironment] = useState<EnvResult | null>(null)
  // 번들 프리셋 목록 (#79). **프로젝트와 무관하게 한 번 묻는다** — `assets/`의 내용이고
  // run 디렉터리마다 다르지 않다. 읽지 못하면 `null`이고 그때 두 값은 읽기 전용으로 남는다.
  const [presets, setPresets] = useState<PresetsResult | null>(null)
  const [opened, setOpened] = useState<{ runDir: string, path: string } | null>(null)
  const [project, setProject] = useState<Project | null>(null)
  const [scenes, setScenes] = useState<Scenes | null>(null)
  const [content, setContent] = useState<Content | null>(null)
  const [baseline, setBaseline] = useState<string | null>(null)
  const [contentBaseline, setContentBaseline] = useState<string | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  // 받지 않는 형식을 고른 기록 (#80). **프로젝트에 남지 않는다** — 거부는 값이 적용되지 않은
  // 상태이고(확정 스펙 4장) 파일에 남길 것이 없다.
  const [backgroundReject, setBackgroundReject] = useState<BackgroundReject | null>(null)
  const [busy, setBusy] = useState(false)

  const [view, setView] = useState<View>('scenes')
  const [selected, setSelected] = useState<number | null>(null)
  const [selectedItem, setSelectedItem] = useState<number | null>(null)
  const [frame, setFrame] = useState<PreviewFrame | null>(null)
  const [pending, setPending] = useState<number | null>(null)
  const [previewError, setPreviewError] = useState<ApiError | null>(null)

  // 최종 렌더 (#30). **결과가 프로젝트에 남지 않는다** — 렌더는 산출물을 만드는 실행이고
  // 편집 상태가 아니다. 그래서 프로젝트를 다시 열면 이 상태도 처음으로 돌아간다.
  const [render, setRender] = useState<RenderState>({ kind: 'idle' })
  // 경고 카드의 확인 체크박스. **`review.acknowledged`와 다른 값이다** — 그쪽은 문제별
  // 검수 기록이고(#28) 이쪽은 "이번 렌더를 이대로 진행한다"는 한 번의 동의다. 파일에 남기면
  // 다음 실행에서도 확인한 것으로 읽힌다.
  const [renderGate, setRenderGate] = useState(false)
  // 재생성 (#77). **렌더와 같은 자리에 둔다** — 둘 다 산출물을 만드는 실행이고 편집 상태가
  // 아니다. 다른 점은 결과가 run 디렉터리의 파일이라 **끝난 뒤 화면을 다시 읽는다**는 것이다.
  const [regen, setRegen] = useState<RegenerateState>({ kind: 'idle' })
  const rendering = render.kind === 'running'
  const regenerating = regen.kind === 'running'
  // 편집 콜백이 잠금을 볼 때 쓰는 값. 상태를 deps에 넣으면 실행이 시작·종료할 때마다 편집
  // 콜백 전부가 새로 만들어지고, 바뀐 참조가 속성 패널을 통째로 다시 그리게 한다.
  const locked = rendering || regenerating
  const lockedRef = useRef(locked)
  lockedRef.current = locked

  // **두 파일 중 어느 쪽이든 바뀌면 저장되지 않은 변경이다.** 헤더의 pill 하나가 둘을
  // 함께 말하고, 창을 닫을 때 main이 보는 플래그도 이 값이다.
  const unsaved = (
    (project !== null && baseline !== null && JSON.stringify(project) !== baseline) ||
    (content !== null && contentBaseline !== null && JSON.stringify(content) !== contentBaseline)
  )

  // 편집기가 등록되지 않은 타입이면 `null`이고, 그때 문제 편집 화면이 열리지 않는다.
  const type = project === null ? null : contentModule(project.type)
  const items = useMemo(
    () => (type && content ? type.items(content) : []),
    [type, content]
  )
  // 프리뷰를 다시 만들 근거. 백엔드도 같은 판단을 하므로 이 값이 바뀌었는데 그림이 같으면
  // 캐시가 답하고 왕복이 몇 ms로 끝난다.
  const projectKey = useMemo(() => (project === null ? null : JSON.stringify(project)), [project])

  /**
   * 화면이 그리는 장면 목록 — 사람이 얹은 편집을 반영한 것 (#82).
   *
   * **`scenes`는 파일 그대로 둔다.** 장면 목록·총 길이·속성 패널이 이 값을 쓰고, 프리뷰는
   * 백엔드가 같은 규칙으로 얹은 결과를 그린다(`video_renderer.apply_scene_overrides`).
   * 원본을 갈아 끼우면 `orderStale`처럼 파일끼리 비교하는 판정이 편집된 값을 보게 된다.
   */
  const shownScenes = useMemo(
    () => (scenes && project ? effectiveScenes(scenes.scenes, project) : []),
    [scenes, project]
  )

  /**
   * 자막 문구가 고쳐진 장면의 인덱스 (#83).
   *
   * **적어 두지 않고 계산한다** — `orderStale`과 같은 판단이다 (`editedCaptions`). 파일 그대로인
   * `scenes`와 비교해야 하므로 `shownScenes`가 아니다.
   */
  const captionEdits = useMemo(
    () => (scenes && project ? editedCaptions(scenes.scenes, project) : []),
    [scenes, project]
  )

  /**
   * run 디렉터리의 세 파일을 읽어 화면에 앉힌다.
   *
   * **여는 것과 재생성 뒤가 같은 경로다** (#77). 재생성은 앱이 든 값을 받지 않고 파일만
   * 바꾸므로, 화면이 그 파일들을 다시 읽지 않으면 편집이 반영된 장면·낡음 표시가 보이지
   * 않는다. 갈리는 것은 **어디를 보고 있었는가**뿐이라(열기는 처음으로 돌아가고 재생성은
   * 보던 화면에 머문다) 그 초기화는 호출부에 있다.
   */
  const load = useCallback(async (runDir: string) => {
    const response = await api.call<OpenResult>('open', { run_dir: runDir })
    if (response.error) return { opened: false, scenes: null, error: response.error }

    // **장면 목록과 콘텐츠는 따로 묻는다.** 읽지 못하는 것이 프로젝트를 열지 못할 이유는
    // 아니다 — `project.json`의 값은 여전히 보이고 고칠 수 있어야 하고, 콘텐츠가 없으면
    // 문제 편집만 닫히면 된다.
    const opening = response.result.run_dir
    const listed = await api.call<ScenesResult>('scenes', { run_dir: opening })
    const loaded = contentModule(response.result.project.type)
      ? await api.call<ContentResult>('content', { run_dir: opening, type: response.result.project.type })
      : null

    setOpened({ runDir: opening, path: response.result.project_path })
    setProject(response.result.project)
    setBaseline(JSON.stringify(response.result.project))
    setScenes(listed.result?.scenes ?? null)
    setContent(loaded?.result?.content ?? null)
    setContentBaseline(loaded?.result ? JSON.stringify(loaded.result.content) : null)
    return { opened: true, scenes: listed.result?.scenes ?? null, error: listed.error ?? null }
  }, [api])

  const open = useCallback(async (target?: string) => {
    const runDir = target ?? await api.pickRunDir()
    if (!runDir) return false

    setBusy(true)
    const outcome = await load(runDir)
    setBusy(false)
    if (!outcome.opened) {
      // **열지 못한 것이 앱을 멈추지 않는다.** 이미 열려 있던 프로젝트는 그대로 두고
      // 원인만 띄운다 — 고쳐서 다시 고르면 되는 상황이다.
      setError(outcome.error)
      return false
    }

    setView('scenes')
    setSelected(outcome.scenes ? 0 : null)
    setSelectedItem(null)
    setFrame(null)
    setPending(null)
    setPreviewError(null)
    setBackgroundReject(null)
    setRender({ kind: 'idle' })
    setRenderGate(false)
    setRegen({ kind: 'idle' })
    setError(outcome.error)
    return true
  }, [api, load])

  const save = useCallback(async () => {
    if (!opened || !project) return false

    // 무엇을 보냈는지를 기준으로 삼는다. 저장 중에 편집이 더 일어나면 그것은 여전히
    // 저장되지 않은 변경이어야 한다.
    const sent = JSON.stringify(project)
    const sentContent = content === null ? null : JSON.stringify(content)
    setBusy(true)

    const fail = (failure: ApiError) => {
      setBusy(false)
      setError(failure)
      return false
    }

    // **콘텐츠를 먼저 쓴다.** 둘 중 계약을 어길 수 있는 쪽이 이쪽이다 — 질문을 비우면
    // 스키마가 반려한다. 프로젝트를 먼저 쓰면 그 실패에서 확인 기록만 파일에 남아, 확인한
    // 대상이 없는 확인이 된다.
    if (content !== null) {
      const stored = await api.call<SaveContentResult>(
        'save_content', { run_dir: opened.runDir, type: project.type, content }
      )
      if (stored.error) return fail(stored.error)
    }

    const response = await api.call<SaveResult>('save', { run_dir: opened.runDir, project })
    if (response.error) return fail(response.error)

    setBusy(false)
    setBaseline(sent)
    if (sentContent !== null) setContentBaseline(sentContent)
    setError(null)
    return true
  }, [api, opened, project, content])

  /**
   * 프로젝트를 고치는 **단일 지점** — 렌더 중에는 아무것도 바뀌지 않는다 (#30).
   *
   * 확정 스펙 3.3의 "편집만 잠긴다"를 화면에서만 구현하면(컨트롤을 비활성으로 그리는 것)
   * 스모크와 메뉴·단축키가 그 잠금을 지나가고, 그때 **방금 렌더에 넘긴 상태와 화면이 갈린다** —
   * 렌더는 앱이 넘긴 프로젝트로 돌기 때문이다. 그래서 판정이 여기 있고 화면은 그것을 보여
   * 주기만 한다 (`locked`).
   */
  const patchProject = useCallback((change: (current: Project) => Project) => {
    if (lockedRef.current) return
    setProject((previous) => previous && change(previous))
  }, [])

  const edit = useCallback((section: 'render' | 'audio', field: string, value: unknown) => {
    patchProject((previous) => ({
      ...previous,
      [section]: { ...previous[section], [field]: value }
    }))
  }, [patchProject])

  /**
   * 트랙 볼륨 (#81). **선형 게인을 그대로 쌓는다** — 슬라이더의 0~100은 화면의 눈금이고,
   * `project.json`에 사는 값은 게인이다 (D2 확정 스펙 5장).
   *
   * **`review`를 건드리지 않는다.** 볼륨은 `scenes.json`도 `captions.srt`도 낡게 하지 않는다 —
   * 렌더가 오디오를 섞을 때 걸리는 값이라 다시 만들 산출물이 없다 (#82의 장면 길이와 갈린다).
   *
   * 프리뷰도 다시 만들어지지 않는다. 앱이 그 판단을 하는 것이 아니라(프로젝트가 바뀌었으니
   * 요청은 나간다) 백엔드의 지문이 `audio`를 빼기 때문이고, 그래서 왕복이 캐시 적중으로 끝난다
   * (`api._signature`, `PREVIEW_BLIND_SECTIONS`).
   */
  const editVolume = useCallback((track: 'voice_volume' | 'sfx_volume', gain: number) => {
    edit('audio', track, gain)
  }, [edit])

  /**
   * 자막 스타일 교체 — **배경이 기본 짝으로 함께 간다** (#79, D2 확정 스펙 1.1).
   *
   * 짝은 `assets/caption-styles/presets.json`의 `background`이고 백엔드가 실어 보낸다.
   * 시안에 적힌 짝은 둘이 서로 바뀐 값이라(민트↔오렌지) 그것을 옮기면 사용자가 스타일을
   * 고르는 것만으로 D1 확정 스펙 6.3에서 △인 조합에 들어간다.
   *
   * **프리셋이 아닌 배경은 건드리지 않는다.** 사용자 파일(#80)이나 색은 프리셋 이름으로
   * 표현할 수 없는 값이라, 스타일을 고른 것만으로 갈아 끼우면 사용자가 넣은 것이 사라진다.
   * 조합 자체는 막지 않으므로 배경은 이 뒤에 따로 고를 수 있다.
   */
  const editStyle = useCallback((style: CaptionStylePreset) => {
    patchProject((previous) => ({
      ...previous,
      background: previous.background.kind === 'preset'
        ? { ...previous.background, value: style.background }
        : previous.background,
      render: { ...previous.render, caption_style: style.name }
    }))
  }, [patchProject])

  /** 배경 프리셋 교체 (#79). 스타일과 독립이다 — 차단할 조합이 없다 (D1 확정 스펙 6.3). */
  const editBackground = useCallback((name: string) => {
    // 프리셋으로 돌아오면 거부 표시도 사라진다 — 고칠 것이 없어진 자리에 남아 있으면
    // 지금 배경이 거부된 것으로 읽힌다.
    setBackgroundReject(null)
    patchProject((previous) => ({
      ...previous,
      background: { ...previous.background, kind: 'preset', value: name }
    }))
  }, [patchProject])

  /**
   * 배경을 사용자 파일로 교체한다 (#80).
   *
   * **`kind`는 확장자가 정하고 그 표는 백엔드가 보낸다** (`presets.background_files`).
   * 목록 밖 형식은 값을 적용하지 않고 거부를 남긴다 — D2 확정 스펙 4장의 `danger`이고,
   * 값이 적용되는 `warn`(낭독보다 짧은 길이)과 같은 표시를 쓰지 않는다.
   *
   * **파일을 run 디렉터리로 복사하지 않는다.** 대화상자가 준 절대 경로를 그대로 적고,
   * 렌더러는 상대·절대 둘 다 받는다 (`video_renderer._source_file`). 파일이 나중에
   * 사라지면 프리뷰가 그 경로를 말하며 실패하고, 나머지 편집은 그대로 된다.
   */
  const pickBackground = useCallback(async () => {
    const formats = presets?.background_files ?? []
    if (formats.length === 0) return
    const picked = await api.pickBackgroundFile(acceptedExtensions(formats))
    if (!picked) return

    const kind = kindForFile(picked, formats)
    if (kind === null) {
      setBackgroundReject({ path: picked, accepted: formats.map((format) => format.extension) })
      return
    }
    setBackgroundReject(null)
    patchProject((previous) => ({
      ...previous,
      background: { ...previous.background, kind, value: picked }
    }))
  }, [api, patchProject, presets])

  /** `review`를 고친 프로젝트. 없던 섹션은 빈 값에서 시작한다 (옛 run 디렉터리). */
  const patchReview = useCallback((change: (current: Review) => Review) => {
    patchProject((previous) => ({ ...previous, review: change(review(previous)) }))
  }, [patchProject])

  /**
   * 장면 하나에 얹은 편집을 고친다 — **세 편집이 이 함수를 공유한다** (#82의 길이, #83의
   * 자막 문구·오버레이, PRD 14.1).
   *
   * **`scenes.json`을 고치지 않는다.** 낭독보다 짧은 길이는 `validate_scenes_final`이 거부하고
   * (그쪽에 쓰면 저장이 실패해 run 디렉터리가 다시 열리지 않는다), 문구·오버레이는 재생성(#77)이
   * 장면을 다시 만들 때 사라진다. 값이 살 수 있는 자리는 `render.scene_overrides`뿐이다.
   *
   * 렌더러가 읽는 값이라 프리뷰 지문에 들어가고, 그래서 고치면 프레임이 다시 만들어진다.
   */
  const patchOverride = useCallback((
    scene: Scene, change: (current: SceneOverride) => SceneOverride
  ) => {
    patchProject((previous) => {
      const key = overrideKey(scene)
      const current = previous.render.scene_overrides ?? []
      const existing = current.find((item) => sameOverride(item, key)) ?? key
      const next = pruneOverride(change(existing))
      const list = next === null
        // **얹는 값이 하나도 남지 않으면 항목을 지운다.** 스키마가 빈 항목을 거부하므로
        // (`_check_scene_overrides`) 남겨 두면 되돌린 편집 하나가 저장을 막는다.
        ? current.filter((item) => !sameOverride(item, key))
        : current.some((item) => sameOverride(item, key))
          ? current.map((item) => (sameOverride(item, key) ? next : item))
          : [...current, next]
      return { ...previous, render: { ...previous.render, scene_overrides: list } }
    })
  }, [patchProject])

  /**
   * 장면 길이 조정 (#82).
   *
   * 함께 `review.timeline_stale`을 건다. 길이 하나를 고치면 그 뒤 장면의 시작 시각이 전부
   * 밀려 `captions.srt`·`voice.mp3`가 어긋나고, 낡는 대상이 특정 항목이 아니라 타임라인
   * 전체다 — 그래서 항목 번호 목록인 `stale`이 아니다. 지우는 것은 재생성(#77)이다.
   */
  const editDuration = useCallback((scene: Scene, duration: number) => {
    patchOverride(scene, (current) => ({ ...current, duration }))
    patchReview((current) => ({ ...current, timeline_stale: true }))
  }, [patchOverride, patchReview])

  /**
   * 자막 문구 편집 (#83).
   *
   * **`timeline_stale`을 함께 건다** (#77에서 들어왔다). 그 전까지 "자막이 낡았다"의 근거는
   * `scene_overrides[].text`와 `scenes.json`의 비교였는데(`editedCaptions`), 재생성이 **얹은
   * 문구로** `captions.srt`를 만들면서 그 비교는 "낡았는가"가 아니라 "고쳤는가"가 됐다 —
   * 비교 기준이 파일에서 사라졌으므로 적어 둔다. 얹은 편집 둘(길이·문구)이 산출물에 닿는
   * 시점이 같아서 칸도 하나다.
   *
   * 그래서 **되돌려도 이 표시는 남는다.** 낡지 않은 것을 낡았다고 말하는 쪽이 반대보다
   * 안전하고, 지우는 것은 재생성이다 — 콘텐츠 편집의 `stale`과 같은 규칙이 됐다.
   * `editedCaptions`가 그리는 "고쳤다" 표시는 여전히 되돌리면 사라진다.
   */
  const editCaption = useCallback((scene: Scene, text: string) => {
    patchOverride(scene, (current) => ({ ...current, text }))
    patchReview((current) => ({ ...current, timeline_stale: true }))
  }, [patchOverride, patchReview])

  /** 자막 문구를 `scenes.json`의 값으로 되돌린다. 키를 지우는 것이 곧 되돌리기다. */
  const revertCaption = useCallback((scene: Scene) => {
    patchOverride(scene, ({ text: _dropped, ...rest }) => rest)
    patchReview((current) => ({ ...current, timeline_stale: true }))
  }, [patchOverride, patchReview])

  /**
   * 텍스트 오버레이 추가 (#83).
   *
   * **기본값을 계약 목록에서 고른다** — 이름도 크기도 앱이 적지 않는다 (`presets.overlay`).
   * 가운데 후보를 쓰는 것은 목록이 순서를 갖기 때문이고, 그래서 후보가 늘어도 이 코드가
   * 낡지 않는다.
   */
  const addOverlay = useCallback((scene: Scene) => {
    const contract = presets?.overlay
    if (!contract) return
    patchOverride(scene, (current) => {
      const list = current.overlays ?? []
      const middle = <T,>(values: T[]) => values[Math.floor(values.length / 2)]
      return {
        ...current,
        overlays: [...list, {
          id: nextOverlayId(list),
          text: '새 문구',
          pos: middle(contract.positions),
          offset: { x: 0, y: 0 },
          color: contract.colors[0].name,
          size: middle(contract.sizes),
          weight: middle(contract.weights),
          timing: TIMING_SCENE
        }]
      }
    })
  }, [patchOverride, presets])

  const editOverlay = useCallback((scene: Scene, id: string, fields: Partial<Overlay>) => {
    patchOverride(scene, (current) => ({
      ...current,
      overlays: (current.overlays ?? []).map(
        (item) => (item.id === id ? { ...item, ...fields } : item)
      )
    }))
  }, [patchOverride])

  /** **되돌리기가 없다** (확정 스펙 7.4). 확인을 받는 것은 카드 쪽이다. */
  const removeOverlay = useCallback((scene: Scene, id: string) => {
    patchOverride(scene, (current) => ({
      ...current,
      overlays: (current.overlays ?? []).filter((item) => item.id !== id)
    }))
  }, [patchOverride])

  /**
   * `scenes.json`이 아직 참조하는 항목 번호.
   *
   * **콘텐츠에서 지운 번호도 여기 남는다.** 그래서 새 항목이 그 번호를 가져가지 못하고,
   * 옛 장면들이 새 항목의 것으로 읽히는 일이 생기지 않는다 — 재생성(#77)이 장면을 다시
   * 만들면 그때 목록에서 빠진다.
   */
  const reservedIds = useMemo(() => {
    if (!scenes) return []
    const seen = new Set<number>()
    scenes.scenes.forEach((scene) => {
      if (scene.question_id !== undefined) seen.add(scene.question_id)
    })
    return [...seen]
  }, [scenes])

  /**
   * 콘텐츠 편집이 검수 상태에 미치는 영향 — **낡음이 두 종류다** (#83, 확정 스펙 7.3).
   *
   * - 낭독 문구가 바뀌면 **음성까지 낡는다** (`stale`) — `voice.mp3`를 다시 만들어야 한다
   * - 자막에만 가는 문구(퀴즈의 해설)가 바뀌면 **자막만 낡는다** (`captions_stale`) —
   *   음성은 그대로 쓰고 `captions.srt`만 다시 만든다
   *
   * 어느 필드가 어디로 가는지는 타입 모듈이 알고(`ContentItem`의 `narration` / `captions`),
   * 여기서는 값이 달라졌는지만 본다. **확인은 자막 쪽 변화에도 풀린다** — 사람이 확인한 것은
   * 그 문제의 내용이고, 해설이 달라졌으면 확인한 내용과 다르다 (확정 스펙 1.4).
   *
   * **한 번 붙은 낡음은 되돌려도 떨어지지 않는다.** `scenes.json`이 어느 문구에서 만들어졌는지
   * 기록해 두는 곳이 없어 비교 기준이 "지금 화면의 직전 값"뿐이기 때문이다. 낡지 않은 것을
   * 낡았다고 말하는 쪽이 반대보다 안전하고, 표시를 지우는 것은 재생성(#77)이다. (장면의 자막
   * 문구 편집은 갈린다 — 그쪽은 `scenes.json`이 기준이라 되돌리면 표시도 사라진다.)
   */
  const editContent = useCallback((next: Content) => {
    if (!type || !content || lockedRef.current) return
    const before = new Map(type.items(content).map((item) => [item.id, item]))
    const changed = (field: 'narration' | 'captions') => type.items(next)
      .filter((item) => {
        const was = before.get(item.id)
        return was !== undefined && was[field] !== item[field]
      })
      .map((item) => item.id)
    const spoken = changed('narration')
    // 낭독 문구는 자막에도 가므로 이 목록이 위를 포함한다 (`ContentItem.captions`).
    const captioned = changed('captions')
    setContent(next)
    if (spoken.length > 0 || captioned.length > 0) {
      // **`current`를 펼친다.** 몇 개만 골라 새 객체를 만들면 `timeline_stale`처럼 나중에
      // 늘어난 칸이 조용히 사라진다 — 스모크가 저장·재시작 뒤에 그것을 밟았다 (#82).
      patchReview((current) => {
        const stale = union(current.stale, spoken)
        return {
          ...current,
          acknowledged: current.acknowledged.filter((id) => !captioned.includes(id)),
          stale,
          // **강한 쪽에 이미 있는 번호는 넣지 않는다.** 겹쳐도 계약 위반은 아니지만
          // (스키마가 허용한다) 화면에 서는 것은 어차피 `stale`이다.
          captions_stale: union(
            captionsStale(current), captioned.filter((id) => !stale.includes(id))
          )
        }
      })
    }
  }, [type, content, patchReview])

  const addItem = useCallback(() => {
    if (!type || !content || lockedRef.current) return
    const added = type.add(content, reservedIds)
    setContent(added.content)
    // 새 항목은 오디오도 자막도 **아예 없다.** `stale`이 "낡음"과 "없음"을 함께 뜻한다 —
    // 재생성이 해야 할 일이 같기 때문이다.
    patchReview((current) => ({ ...current, stale: union(current.stale, [added.id]) }))
    setSelectedItem(added.id)
  }, [type, content, reservedIds, patchReview])

  const removeItem = useCallback((id: number) => {
    if (!type || !content || lockedRef.current) return
    const remaining = type.items(content).filter((item) => item.id !== id)
    setContent(type.remove(content, id))
    // 지운 번호는 세 목록에서도 빠진다. 남겨 두면 나중에 같은 번호가 다시 생겼을 때
    // 옛 판단이 새 항목에 붙는다 — 번호를 재사용하지 않는 것과 같은 이유다.
    patchReview((current) => ({
      ...current,
      acknowledged: current.acknowledged.filter((value) => value !== id),
      stale: current.stale.filter((value) => value !== id),
      captions_stale: captionsStale(current).filter((value) => value !== id)
    }))
    setSelectedItem(remaining[0]?.id ?? null)
  }, [type, content, patchReview])

  const moveItem = useCallback((id: number, delta: number) => {
    // **순서만 바뀌면 그 항목의 오디오는 그대로다.** 낡는 것은 `scenes.json`의 장면 배열이고,
    // 그것은 저장하지 않는다 — 두 파일의 번호 나열을 비교하면 나온다 (`orderStale`).
    if (type && content && !lockedRef.current) setContent(type.move(content, id, delta))
  }, [type, content])

  const acknowledge = useCallback((id: number) => {
    // **콘텐츠는 건드리지 않는다.** `verify.status`와 `confidence`는 검증기(#10)와 검수
    // 게이트(#11)가 소유한다 (확정 스펙 1.4).
    patchReview((current) => ({ ...current, acknowledged: union(current.acknowledged, [id]) }))
  }, [patchReview])

  /**
   * 장면 구성이 낡았는가 — 콘텐츠의 항목 순서와 `scenes.json`의 `question_id` 나열 비교.
   *
   * **적어 두지 않는다.** 두 파일에서 바로 나오는 값이라 기록하면 어느 쪽이 원본인지 모호해지고,
   * 재시작 뒤에도 같은 답이 나온다.
   */
  const orderStale = useMemo(() => {
    if (!scenes || items.length === 0) return false
    const inScenes: number[] = []
    scenes.scenes.forEach((scene) => {
      const id = scene.question_id
      if (id !== undefined && inScenes[inScenes.length - 1] !== id) inScenes.push(id)
    })
    return inScenes.join(',') !== items.map((item) => item.id).join(',')
  }, [scenes, items])

  /**
   * 렌더 전에 확인해야 하는 문제 (#30, 확정 스펙 3.3).
   *
   * **`verified`가 아닌 것 전부다** — `unverified`도 "판단 근거가 없다"이므로 확인 대상이고
   * (퀴즈 스펙 5.2), 사람이 확인한 것(`review.acknowledged`)은 여기서 빠진다 (확정 스펙 1.4).
   *
   * **콘텐츠의 필드를 보지 않는다.** 셸이 아는 것은 `ContentItem`의 다섯 칸이고, 어느 필드가
   * 검증 상태인지는 타입 모듈의 지식이다 (#28의 경계).
   */
  const renderWarnings = useMemo<RenderWarningItem[]>(() => {
    if (!project) return []
    const acknowledged = review(project).acknowledged
    return items
      .map((item, index) => ({ item, position: index + 1 }))
      .filter(({ item }) => item.status !== 'verified' && !acknowledged.includes(item.id))
      .map(({ item, position }) => ({
        id: item.id,
        position,
        title: item.title,
        status: item.status,
        confidence: item.confidence,
        source: item.source
      }))
  }, [items, project])

  /**
   * 렌더를 막지 않는 경고들 (#30).
   *
   * **게이트는 `flagged` 하나뿐이다.** 낡음으로 막으면 재생성(#77)이 없는 지금 앱에서 렌더를
   * 아예 할 수 없고, 짧은 길이와 저장하지 않은 변경은 값이 이미 적용된 상태다 — 셋 다 알리는
   * 것으로 끝내고 색으로 종류를 갈라 준다 (확정 스펙 4장).
   */
  const renderNotes = useMemo<RenderNote[]>(() => {
    if (!project) return []
    const state = review(project)
    const notes: RenderNote[] = []
    if (orderStale) {
      notes.push({
        kind: 'todo',
        title: '장면 구성이 낡았다',
        body: '문제 순서나 개수가 바뀌었다. 지금 렌더하면 옛 구성으로 나온다.'
      })
    }
    if (state.stale.length > 0) {
      notes.push({
        kind: 'todo',
        title: `음성까지 낡은 문제 ${state.stale.length}개`,
        body: '낭독 문구가 바뀌었다. voice.mp3와 captions.srt는 아직 옛 문구다.'
      })
    }
    const onlyCaptions = captionsStale(state).filter((id) => !state.stale.includes(id))
    if (onlyCaptions.length > 0) {
      notes.push({
        kind: 'todo',
        title: '자막이 낡았다',
        body: '자막 문구가 바뀌었다. 번인 자막은 고친 문구로 나오지만 captions.srt는 옛 문구다.'
      })
    }
    // **장면에 얹은 편집 둘이 이 한 칸을 쓴다** (#77). 길이와 자막 문구가 산출물에 닿는 시점이
    // 같고(재생성 한 번), 문구 쪽은 재생성이 얹은 문구로 `captions.srt`를 만들면서 파일 비교로
    // 낡음을 말할 수 없게 됐다 — `captionEdits`는 이제 "고쳤다"이지 "낡았다"가 아니다.
    if (state.timeline_stale === true) {
      notes.push({
        kind: 'todo',
        title: '장면 편집이 반영되지 않았다',
        body: '장면 길이나 자막 문구를 고쳤다. voice.mp3와 captions.srt는 아직 그 편집 전이다.'
      })
    }
    // **낭독보다 짧은 길이는 값이 적용되는 경고다** (#82). 렌더는 그 길이로 돌고 낭독이 잘린다.
    const cut = shownScenes.filter((scene) => cutsNarration(scene, scene.duration)).length
    if (cut > 0) {
      notes.push({
        kind: 'warn',
        title: `낭독보다 짧은 장면 ${cut}개`,
        body: '그 장면의 낭독이 잘린 채로 렌더된다.'
      })
    }
    // **렌더는 앱이 들고 있는 프로젝트로 돈다** (`api.method_render`). 파일을 다시 읽지 않으므로
    // 저장하지 않은 편집도 결과에 들어가고, 그 사실을 말하지 않으면 파일과 영상이 갈린 것을
    // 사용자가 알 방법이 없다.
    if (unsaved) {
      notes.push({
        kind: 'warn',
        title: '저장하지 않은 변경이 있다',
        body: '화면의 값 그대로 렌더된다. 파일에는 아직 없으므로 저장은 따로 해야 한다.'
      })
    }
    return notes
  }, [project, orderStale, shownScenes, unsaved])

  /** 재생성으로 해소되는 낡음이 하나라도 있는가 (#77). 버튼을 그릴지의 기준이다. */
  const stale = renderNotes.some((note) => note.kind === 'todo')

  /**
   * 최종 렌더를 시작한다 (#30).
   *
   * **프리뷰와 같은 입력이다** — 파일이 아니라 지금 화면의 프로젝트를 넘긴다. 백엔드가 같은
   * 검증을 지나므로 계약을 어긴 값은 여기서도 걸린다 (`api.method_render`).
   */
  const startRender = useCallback(async () => {
    if (!opened || !project || lockedRef.current) return false

    setRender({ kind: 'running', progress: null })
    const response = await api.call<RenderResult>(
      'render', { run_dir: opened.runDir, project }
    )
    if (response.error) {
      // **거절은 실패가 아니다.** 백엔드가 두 번째 렌더를 막았다는 뜻이고 첫 렌더는 그대로
      // 돌고 있다 — 그 상태를 실패 카드로 덮으면 화면이 "실패"를 말하는 동안 인코딩이 계속
      // 돈다. 그래서 공용 알림으로만 알린다 (`api.method_render`의 `busy`).
      if (response.error.code === 'busy') {
        setError(response.error)
        return false
      }
      setRender({
        kind: 'failed',
        message: response.error.message,
        details: response.error.details,
        // 없으면 빈 문자열이다 — 카드가 빈 `mono` 상자를 그리지 않는다.
        raw: response.error.raw ?? ''
      })
      return false
    }
    setRender({ kind: 'done', result: response.result })
    setError(null)
    return true
  }, [api, opened, project])

  /**
   * 편집을 반영해 장면·오디오·자막을 다시 만든다 (#77).
   *
   * **렌더·프리뷰와 입력이 갈린다** — 프로젝트를 넘기지 않고 `run_dir`과 `type`만 보낸다.
   * 재생성은 run 디렉터리의 산출물 집합을 콘텐츠에 맞추는 실행이라 저장하지 않은 값으로
   * 돌리면 콘텐츠 파일과 `scenes.json`이 서로 다른 문구를 들게 된다. 그래서 **먼저
   * 저장하고**(실패하면 시작하지 않는다) 끝난 뒤 세 파일을 다시 읽는다.
   *
   * 화면은 보던 자리에 머문다 — 문제 편집에서 눌렀는데 장면 목록으로 튀면 방금 고친 문제가
   * 어떻게 됐는지 볼 수 없다.
   */
  const regenerate = useCallback(async () => {
    if (!opened || !project || lockedRef.current) return false
    if (unsaved && !(await save())) return false

    setRegen({ kind: 'running', progress: null })
    const response = await api.call<RegenerateResult>(
      'regenerate', { run_dir: opened.runDir, type: project.type }
    )
    if (response.error) {
      // **거절은 실패가 아니다** (`startRender`와 같은 규칙). 돌고 있는 재생성의 상태를
      // 덮으면 화면이 "실패"를 말하는 동안 합성이 계속 돈다.
      if (response.error.code === 'busy') {
        setError(response.error)
        return false
      }
      setRegen({ kind: 'failed', error: response.error })
      return false
    }

    if (response.result.cancelled) {
      // **산출물이 이전 상태 그대로다** — 다시 읽을 것이 없다.
      setRegen({ kind: 'cancelled' })
      return false
    }
    // 파일이 바뀌었으므로 화면이 다시 읽는다. 프리뷰는 `scenes`가 갈리는 순간 지문이 바뀌어
    // 새 프레임을 받는다 (`api._signature`).
    const outcome = await load(opened.runDir)
    setRegen({ kind: 'done', result: response.result })
    setError(outcome.error)
    return true
  }, [api, opened, project, unsaved, save, load])

  /**
   * 재생성을 멈춘다 — **별도 요청 줄이다** (`api.method_cancel_regenerate`).
   *
   * 재생성은 백그라운드 스레드로 나가 디스패치 루프가 비어 있으므로 이 줄이 도착한다.
   * 실제로 멈추는 것은 다음 단계 경계이고, 교체 전이라 잘린 산출물이 남지 않는다.
   */
  const cancelRegenerate = useCallback(async () => {
    await api.call('cancel_regenerate')
  }, [api])

  /**
   * 진행 알림을 받는다. **요청과 짝이 없는 줄이라 응답 경로가 아니다** (`onBackendEvent`).
   *
   * `id`가 작은 알림은 버린다 — main이 요청마다 증가시키는 값이므로 큰 쪽이 최신이고, 다시
   * 시도한 뒤 이전 렌더의 남은 줄이 도착해도 화면이 거꾸로 가지 않는다. 앱은 자기 요청의
   * `id`를 모르지만(그 번호는 main이 매긴다) **최신인지는 이 비교로 충분하다.**
   */
  const progressId = useRef(0)
  useEffect(() => api.onBackendEvent((message) => {
    // **두 실행이 같은 규칙을 쓴다** (#30의 렌더, #77의 재생성). 알림 종류를 늘릴 때 여기
    // 하나만 보면 되도록 판별을 `protocol`의 함수에 둔다.
    if (isRenderProgress(message)) {
      if (message.id < progressId.current) return
      progressId.current = message.id
      setRender((current) => (
        current.kind === 'running' ? { kind: 'running', progress: message } : current
      ))
      return
    }
    if (isRegenerateProgress(message)) {
      if (message.id < progressId.current) return
      progressId.current = message.id
      setRegen((current) => (
        current.kind === 'running' ? { kind: 'running', progress: message } : current
      ))
    }
  }), [api])

  // 고른 것이 없으면 첫 항목이다. 상태에 미리 넣지 않는 이유는 목록이 바뀔 때(삭제) 그
  // 값이 사라진 항목을 가리킬 수 있기 때문이다.
  const activeItem = selectedItem !== null && items.some((item) => item.id === selectedItem)
    ? selectedItem
    : items[0]?.id ?? null

  // 늦게 온 응답이 최신 프레임을 덮어쓰지 않게 하는 표. 프리뷰는 백엔드에서 스레드로 돌아
  // 응답 순서가 요청 순서와 다를 수 있다.
  const requestSeq = useRef(0)

  // **대기 표현이 두 가지다** (#27 완료 조건). 고른 장면이 바뀌면 이전 프레임을 지우고,
  // 같은 장면에서 값만 바뀌면 이전 프레임을 남긴 채 갱신 배지를 띄운다 — 후자에서 화면을
  // 비우면 값 하나 고칠 때마다 비교할 대상이 사라진다.
  useEffect(() => {
    if (!opened || !project || !projectKey || selected === null) return

    const ticket = ++requestSeq.current
    setFrame((previous) => (previous && previous.index === selected ? previous : null))
    setPending(selected)

    void api.call<PreviewResult>('preview', {
      run_dir: opened.runDir,
      project,
      scene_index: selected
    }).then((response) => {
      if (ticket !== requestSeq.current) return
      setPending(null)
      if (response.error) {
        setFrame(null)
        setPreviewError(response.error)
        return
      }
      setPreviewError(null)
      setFrame({
        index: response.result.scene_index,
        png: response.result.png,
        elapsedMs: response.result.elapsed_ms
      })
    })
    // `project`가 아니라 `projectKey`로 건다 — 같은 내용의 새 객체가 재요청을 만들면
    // 편집하지 않았는데도 FFmpeg가 돈다.
    //
    // **`scenes`는 객체 그대로 건다** (#77). 이 값이 새로 오는 것은 열 때와 재생성 뒤뿐이고,
    // 그때는 백엔드가 파일에서 읽는 장면이 갈렸다는 뜻이라 프레임을 다시 받아야 한다 —
    // 프로젝트만 보면 재생성이 프로젝트를 건드리지 않은 경우에 화면이 옛 그림에 머문다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, opened, projectKey, scenes, selected])

  useEffect(() => {
    void api.context().then(setContext)
    // 첫 화면에서 외부 도구를 확인한다. **여는 것은 막지 않는다** — `project.json`을 읽고
    // 고치는 데 FFmpeg가 필요 없고, 실제로 필요한 시점에 다시 걸린다 (스파이크 5.2).
    void api.call<EnvResult>('env').then((response) => {
      if (response.result) setEnvironment(response.result)
    })
    // 프리셋도 첫 화면에서 묻는다 (#79). **실패해도 여는 것을 막지 않는다** — 목록이 없으면
    // 두 값이 읽기 전용으로 남고 나머지 편집은 그대로 된다.
    void api.call<PresetsResult>('presets').then((response) => {
      if (response.result) setPresets(response.result)
    })
  }, [api])

  // 창을 닫을 때 물어볼지는 main이 판단한다 — 렌더러가 사라진 뒤에도 답을 알아야 한다.
  // **화면을 그리기 전에 알린다** (`useLayoutEffect` + 동기 IPC). 순서가 뒤집히면 사용자
  // 눈에는 "저장되지 않은 변경"이 떠 있는데 창은 확인 없이 닫힌다.
  useLayoutEffect(() => { api.setUnsaved(unsaved) }, [api, unsaved])

  useEffect(() => api.onSaveRequest(() => { void save().then((ok) => api.saveResult(ok)) }), [api, save])

  useEffect(() => api.onMenu((action) => {
    if (action === 'open') void open()
    else void save()
  }), [api, open, save])

  // 스모크가 붙잡는 손잡이. `--smoke`로 띄웠을 때만 달리고, **UI가 쓰는 것과 같은 경로**를
  // 부른다 — 스모크 전용 저장 경로를 두면 확인한 것이 제품 동작이 아니게 된다.
  useEffect(() => {
    if (!context?.smoke) return
    window.__smoke = {
      open,
      save,
      edit,
      select: (index: number) => setSelected(index),
      // 문제 편집도 **UI가 부르는 것과 같은 경로**다 (#28). 스모크 전용 저장 경로를 두면
      // 확인한 것이 제품 동작이 아니게 된다.
      view: (next: View) => setView(next),
      selectItem: (id: number) => setSelectedItem(id),
      // **UI가 부르는 것과 같은 함수다.** 스모크는 장면 객체 대신 인덱스로 가리킬 뿐이다 —
      // 별도 저장 경로를 두면 확인한 것이 제품 동작이 아니게 된다.
      editDuration: (index: number, duration: number) => {
        const target = shownScenes[index]
        if (target) editDuration(target, duration)
      },
      // 자막 문구와 오버레이도 **UI가 부르는 것과 같은 함수다** (#83). 스모크는 장면 객체
      // 대신 인덱스로 가리킬 뿐이고, 카드의 위치·색·삭제 확인은 직접 눌러서 밟는다.
      editCaption: (index: number, text: string) => {
        const target = shownScenes[index]
        if (target) editCaption(target, text)
      },
      revertCaption: (index: number) => {
        const target = shownScenes[index]
        if (target) revertCaption(target)
      },
      addOverlay: (index: number) => {
        const target = shownScenes[index]
        if (target) addOverlay(target)
      },
      editOverlay: (index: number, id: string, fields: Partial<Overlay>) => {
        const target = shownScenes[index]
        if (target) editOverlay(target, id, fields)
      },
      // **UI가 부르는 것과 같은 함수다** (#81). 화면의 슬라이더는 눈금을 게인으로 바꿔 이
      // 함수를 부르고, 스모크는 게인을 직접 준다 — 눈금과 게인의 매핑은 화면의 지식이다.
      editVolume,
      // 렌더도 **UI가 부르는 것과 같은 함수다** (#30). 게이트 체크박스는 카드에서 직접 누르지만
      // 손잡이를 함께 두는 이유는, 확인이 필요한 문제가 있는 run에서 시작이 비활성인 것을
      // 보려면 스모크가 그 값을 껐다 켜야 하기 때문이다.
      render: startRender,
      renderGate: (value: boolean) => setRenderGate(value),
      // 재생성도 **UI가 부르는 것과 같은 함수다** (#77). 저장 → 실행 → 다시 읽기까지 한
      // 함수 안에 있으므로, 스모크가 저장을 따로 부르지 않아도 그 순서가 지켜진다.
      regenerate,
      cancelRegenerate,
      editContent,
      acknowledge,
      addItem,
      removeItem,
      moveItem,
      state: () => ({
        unsaved,
        project,
        // **프리셋 교체에는 손잡이를 두지 않았다** (#79). 스모크가 카드를 직접 누르므로 —
        // 컨트롤이 카드 셋이라 클릭이 곧 제품 경로다. 목록만 여기서 읽어 이름을 적지 않는다.
        // 배경 파일(#80)도 같다 — 버튼을 누르고 대화상자는 main에서 바꿔 끼운다.
        presets,
        // 받지 않는 형식을 골랐을 때만 값이 있다. 화면의 거부 카드와 같은 값이다 (#80).
        backgroundReject,
        scenes,
        // 사람이 얹은 편집이 반영된 길이·문구. **`scenes`는 파일 그대로다** — 스모크가 둘을
        // 비교해 `scenes.json`이 바뀌지 않았음을 확인한다 (#82, #83).
        shownDurations: shownScenes.map((item) => item.duration),
        shownTexts: shownScenes.map((item) => item.text ?? null),
        // 계산된 값이라 파일에 없다 (#83). 재시작 뒤에도 같은 답이 나오는지를 스모크가 본다.
        captionEdits,
        content,
        items,
        error,
        opened,
        environment,
        view,
        selected,
        selectedItem: activeItem,
        orderStale,
        // 렌더 상태 (#30). **결과 객체를 그대로 준다** — 경로·바이트·경과 시간이 백엔드에서 온
        // 값이고, 스모크가 그 파일을 직접 열어 규격을 확인한다.
        render,
        renderGate,
        renderWarnings,
        renderNotes,
        // 재생성 상태 (#77). `progress`가 단계 이름이라 스모크가 어느 단계까지 갔는지 본다.
        regen,
        stale,
        pending,
        frame: frame && { index: frame.index, bytes: frame.png.length, elapsedMs: frame.elapsedMs },
        previewError
      })
    }
  })

  const ffmpeg = environment?.tools.ffmpeg
  const scene = selected !== null ? shownScenes[selected] ?? null : null
  return (
    <div className="app">
      <Header
        projectPath={opened?.path ?? null}
        unsaved={unsaved}
        busy={busy}
        canSave={unsaved}
        view={view}
        // 편집기가 등록되지 않은 타입이면 문제 편집으로 갈 수 없다. 화면을 막는 것이 아니라
        // 그 화면만 없다 — 장면 목록과 프리뷰는 그대로 돈다.
        canEditContent={Boolean(type && content)}
        locked={locked}
        onView={setView}
        onOpen={() => { void open() }}
        onSave={() => { void save() }}
      />
      <main className={project && opened ? 'body body--split' : 'body'}>
        {(context?.backend.failure || (ffmpeg && !ffmpeg.found) || error) && (
          <div className="body__notices">
            {context?.backend.failure && (
              <Notice kind="danger" title="백엔드를 실행할 수 없다" details={[context.backend.failure]}>
                프로젝트를 열고 저장하는 모든 동작이 백엔드를 지난다.
              </Notice>
            )}
            {ffmpeg && !ffmpeg.found && (
              // 경고다 — 여는 것과 고치는 것은 막지 않는다. FFmpeg는 동봉하지 않는다
              // (스파이크 5.2). 프리뷰가 그 자리에서 다시 걸린다.
              <Notice kind="warn" title="FFmpeg가 PATH에 없다">
                프로젝트를 열고 고치는 데는 필요 없지만, 프리뷰와 최종 렌더는 FFmpeg를 쓴다.
                설치하고 PATH에 넣은 뒤 앱을 다시 시작한다.
              </Notice>
            )}
            {error && <ErrorNotice error={error} />}
          </div>
        )}
        {/* **재생성 상태는 화면과 무관하게 선다** (#77). 진행 중에도 다른 화면을 볼 수 있고
            (확정 스펙 3.3), 시작한 화면에만 그리면 옮긴 사용자가 무슨 일이 도는지 알 수 없다. */}
        {project && opened && regen.kind !== 'idle' && (
          <div className="body__notices">
            <RegenerateNotice
              state={regen}
              onCancel={() => { void cancelRegenerate() }}
              onDismiss={() => setRegen({ kind: 'idle' })}
            />
          </div>
        )}
        {/* **`accent` 파랑이다** — 결함이 아니라 사용자가 해야 할 일이고, 주황으로 그리면
            `flagged`와 같은 종류로 읽힌다 (확정 스펙 4장). 편집하는 두 화면에 뜬다 —
            렌더 화면은 같은 사실을 자기 경고 목록에 담으므로 여기서 또 그리면 한 화면에
            같은 알림이 두 번 선다 (#30). */}
        {project && opened && orderStale && view !== 'render' && (
          <div className="body__notices">
            <Notice kind="todo" title="장면 구성이 낡았다" testid="notice-order-stale">
              문제 순서나 개수가 바뀌었다. 장면 목록·총 길이·프리뷰는 아직 옛 구성이고,
              재생성해야 반영된다.
            </Notice>
          </div>
        )}
        {project && opened
          ? (view === 'render'
              ? (
                <RenderScreen
                  state={render}
                  warnings={renderWarnings}
                  notes={renderNotes}
                  acknowledgedAll={renderGate}
                  scenes={shownScenes}
                  // 장면 목록을 읽지 못하면 렌더할 것이 없다 — 확정 `scenes.json`이 입력이다.
                  canRender={scenes !== null}
                  // 낡음을 해소하는 실행이 이 화면에도 있다 (#77). **막지는 않는다** — 게이트는
                  // `flagged` 하나뿐이고 낡은 채로 렌더하는 것은 사용자의 선택이다.
                  regenerate={regen}
                  canRegenerate={stale}
                  onAcknowledgeAll={setRenderGate}
                  onStart={() => { void startRender() }}
                  onRegenerate={() => { void regenerate() }}
                  onReveal={(path) => { void api.reveal(path) }}
                  onOpen={(id) => { setSelectedItem(id); setView('questions') }}
                />
                )
              : view === 'questions' && type && content
              ? (
                <QuestionScreen
                  locked={locked}
                  module={type}
                  content={content}
                  items={items}
                  selectedId={activeItem}
                  acknowledged={review(project).acknowledged}
                  stale={review(project).stale}
                  captionsStale={captionsStale(review(project))}
                  // 낡음 카드의 재생성 버튼 (확정 스펙 7.3). **두 종류가 같은 실행을 부른다** —
                  // 갈리는 것은 TTS 재합성이 일어나는지뿐이고 그 판단은 백엔드에 있다.
                  regenerate={regen}
                  onRegenerate={() => { void regenerate() }}
                  onSelect={setSelectedItem}
                  onChange={editContent}
                  onAcknowledge={acknowledge}
                  onAdd={addItem}
                  onRemove={removeItem}
                  onMove={moveItem}
                />
                )
              : (
                <div className="split">
                  {scenes
                    ? (
                      <SceneList
                        scenes={shownScenes}
                        selected={selected}
                        items={items}
                        review={review(project)}
                        captionEdits={captionEdits}
                        onSelect={setSelected}
                        onOpenItem={type && content
                          ? (id) => { setSelectedItem(id); setView('questions') }
                          : null}
                      />
                      )
                    : <aside className="panel panel--scenes"><div className="panel__head"><span className="t-heading">장면</span></div></aside>}
                  <Preview frame={frame} scene={scene} pending={pending} error={previewError} />
                  <Properties
                    locked={locked}
                    project={project}
                    scene={scene}
                    index={selected}
                    runDir={opened.runDir}
                    presets={presets}
                    captionEdited={selected !== null && captionEdits.includes(selected)}
                    // **S2에서는 읽기 전용이다** — 재생성은 문제 편집에서 한다 (확정 스펙 7.3).
                    audioStale={Boolean(
                      scene?.question_id !== undefined &&
                      review(project).stale.includes(scene.question_id)
                    )}
                    onDuration={editDuration}
                    onCaption={editCaption}
                    onRevertCaption={revertCaption}
                    // 계약 목록을 받지 못하면 오버레이 편집이 열리지 않는다 — 앱이 후보를
                    // 지어내면 화면이 허용한 값을 렌더가 거부한다 (#80의 배경 형식과 같다).
                    overlays={presets?.overlay
                      ? {
                          contract: presets.overlay,
                          onAdd: addOverlay,
                          onChange: editOverlay,
                          onRemove: removeOverlay
                        }
                      : null}
                    onVolume={editVolume}
                    onStyle={editStyle}
                    onBackground={editBackground}
                    onPickBackground={() => { void pickBackground() }}
                    backgroundReject={backgroundReject}
                  />
                </div>
                ))
          : <OpenScreen busy={busy} onOpen={() => { void open() }} />}
      </main>
    </div>
  )
}

/**
 * 두 목록을 합치되 중복을 만들지 않는다.
 *
 * `review`의 두 목록은 집합의 뜻이고 스키마가 중복을 거부한다(`_check_review_ids_are_unique`).
 * 확인 버튼을 두 번 누르는 것만으로 저장이 실패하면 원인이 화면에서 드러나지 않는다.
 */
function union (current: number[], added: number[]): number[] {
  return [...current, ...added.filter((id) => !current.includes(id))]
}

/** 오버라이드 항목이 실제로 얹는 값 (`schemas/project.py`의 `OVERRIDE_EDITS`). */
const OVERRIDE_EDITS = ['duration', 'text', 'overlays'] as const

/**
 * 빈 값을 걷어낸 오버라이드 항목. 남는 것이 없으면 `null`이다.
 *
 * **스키마가 둘을 거부한다** — 얹는 값이 없는 항목과 빈 오버레이 목록이다. 되돌린 편집이
 * 껍데기로 남으면 저장이 그때부터 실패하고, 원인은 사용자가 방금 한 동작과 이어지지 않는다.
 */
function pruneOverride (override: SceneOverride): SceneOverride | null {
  const next: SceneOverride = { ...override }
  if (next.overlays !== undefined && next.overlays.length === 0) delete next.overlays
  return OVERRIDE_EDITS.some((key) => next[key] !== undefined) ? next : null
}

/**
 * 새 오버레이의 `id`.
 *
 * **장면 안에서만 유일하면 된다** — 다른 장면의 목록과 섞이지 않고 이 번호를 참조하는 산출물도
 * 없다. 지운 번호를 다시 쓰는 것이 #28의 문제 번호와 갈리는 이유가 그것이다.
 */
function nextOverlayId (existing: Overlay[]): string {
  const used = existing.map((item) => Number.parseInt(item.id.replace(/^o/, ''), 10))
  return `o${used.reduce((highest, value) => Math.max(highest, value || 0), 0) + 1}`
}
