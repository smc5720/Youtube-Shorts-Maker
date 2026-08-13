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
import { SceneList } from './components/SceneList'
import { effectiveScenes, overrideKey, sameOverride } from './scenes'
import { contentModule } from './types'
import {
  bridge,
  review,
  type ApiError,
  type AppContext,
  type CaptionStylePreset,
  type Content,
  type ContentResult,
  type EnvResult,
  type OpenResult,
  type PresetsResult,
  type PreviewResult,
  type Project,
  type Review,
  type SaveContentResult,
  type Scene,
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

  const open = useCallback(async (target?: string) => {
    const runDir = target ?? await api.pickRunDir()
    if (!runDir) return false

    setBusy(true)
    const response = await api.call<OpenResult>('open', { run_dir: runDir })
    if (response.error) {
      // **열지 못한 것이 앱을 멈추지 않는다.** 이미 열려 있던 프로젝트는 그대로 두고
      // 원인만 띄운다 — 고쳐서 다시 고르면 되는 상황이다.
      setBusy(false)
      setError(response.error)
      return false
    }

    // **장면 목록과 콘텐츠는 따로 묻는다.** 읽지 못하는 것이 프로젝트를 열지 못할 이유는
    // 아니다 — `project.json`의 값은 여전히 보이고 고칠 수 있어야 하고, 콘텐츠가 없으면
    // 문제 편집만 닫히면 된다.
    const opening = response.result.run_dir
    const listed = await api.call<ScenesResult>('scenes', { run_dir: opening })
    const loaded = contentModule(response.result.project.type)
      ? await api.call<ContentResult>('content', { run_dir: opening, type: response.result.project.type })
      : null
    setBusy(false)

    setOpened({ runDir: opening, path: response.result.project_path })
    setProject(response.result.project)
    setBaseline(JSON.stringify(response.result.project))
    setScenes(listed.result?.scenes ?? null)
    setContent(loaded?.result?.content ?? null)
    setContentBaseline(loaded?.result ? JSON.stringify(loaded.result.content) : null)
    setView('scenes')
    setSelected(listed.result ? 0 : null)
    setSelectedItem(null)
    setFrame(null)
    setPending(null)
    setPreviewError(null)
    setBackgroundReject(null)
    setError(listed.error ?? null)
    return true
  }, [api])

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

  const edit = useCallback((section: 'render', field: string, value: unknown) => {
    setProject((previous) => previous && {
      ...previous,
      [section]: { ...previous[section], [field]: value }
    })
  }, [])

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
    setProject((previous) => previous && {
      ...previous,
      background: previous.background.kind === 'preset'
        ? { ...previous.background, value: style.background }
        : previous.background,
      render: { ...previous.render, caption_style: style.name }
    })
  }, [])

  /** 배경 프리셋 교체 (#79). 스타일과 독립이다 — 차단할 조합이 없다 (D1 확정 스펙 6.3). */
  const editBackground = useCallback((name: string) => {
    // 프리셋으로 돌아오면 거부 표시도 사라진다 — 고칠 것이 없어진 자리에 남아 있으면
    // 지금 배경이 거부된 것으로 읽힌다.
    setBackgroundReject(null)
    setProject((previous) => previous && {
      ...previous,
      background: { ...previous.background, kind: 'preset', value: name }
    })
  }, [])

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
    setProject((previous) => previous && {
      ...previous,
      background: { ...previous.background, kind, value: picked }
    })
  }, [api, presets])

  /**
   * 장면 길이 조정 (#82).
   *
   * **`scenes.json`을 고치지 않는다.** 낭독보다 짧은 값은 `validate_scenes_final`이 거부하므로
   * 그쪽에 쓰면 저장이 실패하고 그 run 디렉터리가 다시 열리지 않는다 — 값이 살 수 있는 자리는
   * `render.scene_overrides`뿐이다 (PRD 14.1). 렌더러가 읽는 값이라 프리뷰 지문에 들어가고,
   * 그래서 고치면 프레임이 다시 만들어진다.
   *
   * 함께 `review.timeline_stale`을 건다. 길이 하나를 고치면 그 뒤 장면의 시작 시각이 전부
   * 밀려 `captions.srt`·`voice.mp3`가 어긋나고, 낡는 대상이 특정 항목이 아니라 타임라인
   * 전체다 — 그래서 항목 번호 목록인 `stale`이 아니다. 지우는 것은 재생성(#77)이다.
   */
  const editDuration = useCallback((scene: Scene, duration: number) => {
    setProject((previous) => {
      if (!previous) return previous
      const key = overrideKey(scene)
      const current = previous.render.scene_overrides ?? []
      const matched = current.some((item) => sameOverride(item, key))
      const next = matched
        ? current.map((item) => (sameOverride(item, key) ? { ...item, duration } : item))
        : [...current, { ...key, duration }]
      return {
        ...previous,
        render: { ...previous.render, scene_overrides: next },
        review: { ...review(previous), timeline_stale: true }
      }
    })
  }, [])

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

  /** `review`를 고친 프로젝트. 없던 섹션은 빈 값에서 시작한다 (옛 run 디렉터리). */
  const patchReview = useCallback((change: (current: Review) => Review) => {
    setProject((previous) => previous && { ...previous, review: change(review(previous)) })
  }, [])

  /**
   * 콘텐츠 편집이 검수 상태에 미치는 영향 — **규칙 하나다.**
   *
   * 낭독 문구가 바뀐 항목은 확인이 풀리고(확인한 내용과 다른 내용이 확인된 상태로 남지
   * 않는다) 재생성 대상이 된다. 어느 필드가 낭독으로 가는지는 타입 모듈이 알고, 여기서는
   * 값이 달라졌는지만 본다.
   *
   * **한 번 붙은 `stale`은 되돌려도 떨어지지 않는다.** `scenes.json`이 어느 문구에서
   * 만들어졌는지 기록해 두는 곳이 없어 비교 기준이 "지금 화면의 직전 값"뿐이기 때문이다.
   * 낡지 않은 것을 낡았다고 말하는 쪽이 반대보다 안전하고, 표시를 지우는 것은 재생성(#77)이다.
   */
  const editContent = useCallback((next: Content) => {
    if (!type || !content) return
    const before = new Map(type.items(content).map((item) => [item.id, item.narration]))
    const changed = type.items(next)
      .filter((item) => before.has(item.id) && before.get(item.id) !== item.narration)
      .map((item) => item.id)
    setContent(next)
    if (changed.length > 0) {
      // **`current`를 펼친다.** 두 목록만 골라 새 객체를 만들면 `timeline_stale`처럼 나중에
      // 늘어난 칸이 조용히 사라진다 — 스모크가 저장·재시작 뒤에 그것을 밟았다 (#82).
      patchReview((current) => ({
        ...current,
        acknowledged: current.acknowledged.filter((id) => !changed.includes(id)),
        stale: union(current.stale, changed)
      }))
    }
  }, [type, content, patchReview])

  const addItem = useCallback(() => {
    if (!type || !content) return
    const added = type.add(content, reservedIds)
    setContent(added.content)
    // 새 항목은 오디오도 자막도 **아예 없다.** `stale`이 "낡음"과 "없음"을 함께 뜻한다 —
    // 재생성이 해야 할 일이 같기 때문이다.
    patchReview((current) => ({ ...current, stale: union(current.stale, [added.id]) }))
    setSelectedItem(added.id)
  }, [type, content, reservedIds, patchReview])

  const removeItem = useCallback((id: number) => {
    if (!type || !content) return
    const remaining = type.items(content).filter((item) => item.id !== id)
    setContent(type.remove(content, id))
    // 지운 번호는 두 목록에서도 빠진다. 남겨 두면 나중에 같은 번호가 다시 생겼을 때
    // 옛 판단이 새 항목에 붙는다 — 번호를 재사용하지 않는 것과 같은 이유다.
    patchReview((current) => ({
      ...current,
      acknowledged: current.acknowledged.filter((value) => value !== id),
      stale: current.stale.filter((value) => value !== id)
    }))
    setSelectedItem(remaining[0]?.id ?? null)
  }, [type, content, patchReview])

  const moveItem = useCallback((id: number, delta: number) => {
    // **순서만 바뀌면 그 항목의 오디오는 그대로다.** 낡는 것은 `scenes.json`의 장면 배열이고,
    // 그것은 저장하지 않는다 — 두 파일의 번호 나열을 비교하면 나온다 (`orderStale`).
    if (type && content) setContent(type.move(content, id, delta))
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, opened, projectKey, selected])

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
        // 사람이 얹은 편집이 반영된 길이. **`scenes`는 파일 그대로다** — 스모크가 둘을
        // 비교해 `scenes.json`이 바뀌지 않았음을 확인한다 (#82).
        shownDurations: shownScenes.map((item) => item.duration),
        content,
        items,
        error,
        opened,
        environment,
        view,
        selected,
        selectedItem: activeItem,
        orderStale,
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
        {/* **`accent` 파랑이다** — 결함이 아니라 사용자가 해야 할 일이고, 주황으로 그리면
            `flagged`와 같은 종류로 읽힌다 (확정 스펙 4장). 두 화면 모두에 뜬다. */}
        {project && opened && orderStale && (
          <div className="body__notices">
            <Notice kind="todo" title="장면 구성이 낡았다" testid="notice-order-stale">
              문제 순서나 개수가 바뀌었다. 장면 목록·총 길이·프리뷰는 아직 옛 구성이고,
              재생성해야 반영된다.
            </Notice>
          </div>
        )}
        {project && opened
          ? (view === 'questions' && type && content
              ? (
                <QuestionScreen
                  module={type}
                  content={content}
                  items={items}
                  selectedId={activeItem}
                  acknowledged={review(project).acknowledged}
                  stale={review(project).stale}
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
                        onSelect={setSelected}
                        onOpenItem={type && content
                          ? (id) => { setSelectedItem(id); setView('questions') }
                          : null}
                      />
                      )
                    : <aside className="panel panel--scenes"><div className="panel__head"><span className="t-heading">장면</span></div></aside>}
                  <Preview frame={frame} scene={scene} pending={pending} error={previewError} />
                  <Properties
                    project={project}
                    scene={scene}
                    index={selected}
                    runDir={opened.runDir}
                    presets={presets}
                    onDuration={editDuration}
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
