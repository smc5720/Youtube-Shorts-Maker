// 앱 셸 — 프로젝트를 열고, 고친 것을 저장하고, 실패를 그린다 (이슈 #26).
// 그 위에 장면 목록 · 세로형 프리뷰 · 속성 패널 3분할이 올라와 있다 (#27).
//
// 여기서 정해 두는 것은 편집이 어떻게 생겼는지가 아니라 **편집이 어디에 쌓이고 언제 파일이
// 되는가**다. 문제 편집(#28), 공통 편집(#29), 렌더 실행(#30)이 이 상태 위에 올라온다.
//
// - `project`는 통째로 오가는 값이다. 필드를 골라 다시 조립하지 않으므로 스키마가 늘어도
//   앱이 값을 잃지 않는다
// - 저장 여부는 플래그가 아니라 **마지막으로 파일과 같았던 내용(`baseline`)과의 비교**다.
//   고쳤다가 되돌린 것을 "변경"으로 세면 사용자가 없는 변경을 저장하게 된다
// - 프리뷰를 다시 만들지도 같은 비교로 정한다. 프로젝트 내용이 곧 그림을 정하므로, 무엇이
//   프레임에 영향을 주는지 앱이 알 필요가 없다 (`_signature`가 백엔드 쪽 같은 판단이다)

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { Header } from './components/Header'
import { ErrorNotice, Notice } from './components/Notice'
import { OpenScreen } from './components/OpenScreen'
import { Preview, type PreviewFrame } from './components/Preview'
import { Properties } from './components/Properties'
import { SceneList } from './components/SceneList'
import {
  bridge,
  type ApiError,
  type AppContext,
  type EnvResult,
  type OpenResult,
  type PreviewResult,
  type Project,
  type Scenes,
  type ScenesResult,
  type SaveResult
} from './protocol'

export function App () {
  const api = bridge()
  const [context, setContext] = useState<AppContext | null>(null)
  const [environment, setEnvironment] = useState<EnvResult | null>(null)
  const [opened, setOpened] = useState<{ runDir: string, path: string } | null>(null)
  const [project, setProject] = useState<Project | null>(null)
  const [scenes, setScenes] = useState<Scenes | null>(null)
  const [baseline, setBaseline] = useState<string | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  const [selected, setSelected] = useState<number | null>(null)
  const [frame, setFrame] = useState<PreviewFrame | null>(null)
  const [pending, setPending] = useState<number | null>(null)
  const [previewError, setPreviewError] = useState<ApiError | null>(null)

  const unsaved = project !== null && baseline !== null && JSON.stringify(project) !== baseline
  // 프리뷰를 다시 만들 근거. 백엔드도 같은 판단을 하므로 이 값이 바뀌었는데 그림이 같으면
  // 캐시가 답하고 왕복이 몇 ms로 끝난다.
  const projectKey = useMemo(() => (project === null ? null : JSON.stringify(project)), [project])

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

    // **장면 목록은 따로 묻는다.** 읽지 못하는 것이 프로젝트를 열지 못할 이유는 아니다 —
    // `project.json`의 값은 여전히 보이고 고칠 수 있어야 한다.
    const listed = await api.call<ScenesResult>('scenes', { run_dir: response.result.run_dir })
    setBusy(false)

    setOpened({ runDir: response.result.run_dir, path: response.result.project_path })
    setProject(response.result.project)
    setBaseline(JSON.stringify(response.result.project))
    setScenes(listed.result?.scenes ?? null)
    setSelected(listed.result ? 0 : null)
    setFrame(null)
    setPending(null)
    setPreviewError(null)
    setError(listed.error ?? null)
    return true
  }, [api])

  const save = useCallback(async () => {
    if (!opened || !project) return false

    // 무엇을 보냈는지를 기준으로 삼는다. 저장 중에 편집이 더 일어나면 그것은 여전히
    // 저장되지 않은 변경이어야 한다.
    const sent = JSON.stringify(project)
    setBusy(true)
    const response = await api.call<SaveResult>('save', { run_dir: opened.runDir, project })
    setBusy(false)
    if (response.error) {
      setError(response.error)
      return false
    }

    setBaseline(sent)
    setError(null)
    return true
  }, [api, opened, project])

  const edit = useCallback((section: 'render', field: string, value: unknown) => {
    setProject((previous) => previous && {
      ...previous,
      [section]: { ...previous[section], [field]: value }
    })
  }, [])

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
      state: () => ({
        unsaved,
        project,
        scenes,
        error,
        opened,
        environment,
        selected,
        pending,
        frame: frame && { index: frame.index, bytes: frame.png.length, elapsedMs: frame.elapsedMs },
        previewError
      })
    }
  })

  const ffmpeg = environment?.tools.ffmpeg
  const scene = scenes && selected !== null ? scenes.scenes[selected] ?? null : null
  return (
    <div className="app">
      <Header
        projectPath={opened?.path ?? null}
        unsaved={unsaved}
        busy={busy}
        canSave={unsaved}
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
        {project && opened
          ? (
            <div className="split">
              {scenes
                ? <SceneList scenes={scenes.scenes} selected={selected} onSelect={setSelected} />
                : <aside className="panel panel--scenes"><div className="panel__head"><span className="t-heading">장면</span></div></aside>}
              <Preview frame={frame} scene={scene} pending={pending} error={previewError} />
              <Properties project={project} scene={scene} index={selected} runDir={opened.runDir} />
            </div>
            )
          : <OpenScreen busy={busy} onOpen={() => { void open() }} />}
      </main>
    </div>
  )
}
