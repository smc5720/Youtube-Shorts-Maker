// 앱 셸 — 프로젝트를 열고, 고친 것을 저장하고, 실패를 그린다 (이슈 #26).
//
// **화면 안을 채우지 않는다.** 장면 목록과 프리뷰(#27), 문제 편집(#28), 공통 편집(#29),
// 렌더 실행(#30)이 각각 이 상태 위에 올라온다. 그래서 여기서 정해 두는 것은 편집이 어떻게
// 생겼는지가 아니라 **편집이 어디에 쌓이고 언제 파일이 되는가**다.
//
// - `project`는 통째로 오가는 값이다. 필드를 골라 다시 조립하지 않으므로 스키마가 늘어도
//   앱이 값을 잃지 않는다
// - 저장 여부는 플래그가 아니라 **마지막으로 파일과 같았던 내용(`baseline`)과의 비교**다.
//   고쳤다가 되돌린 것을 "변경"으로 세면 사용자가 없는 변경을 저장하게 된다

import { useCallback, useEffect, useLayoutEffect, useState } from 'react'

import { Header } from './components/Header'
import { ErrorNotice, Notice } from './components/Notice'
import { OpenScreen } from './components/OpenScreen'
import { ProjectSummary } from './components/ProjectSummary'
import {
  bridge,
  type ApiError,
  type AppContext,
  type EnvResult,
  type OpenResult,
  type Project,
  type SaveResult
} from './protocol'

export function App () {
  const api = bridge()
  const [context, setContext] = useState<AppContext | null>(null)
  const [environment, setEnvironment] = useState<EnvResult | null>(null)
  const [opened, setOpened] = useState<{ runDir: string, path: string } | null>(null)
  const [project, setProject] = useState<Project | null>(null)
  const [baseline, setBaseline] = useState<string | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  const unsaved = project !== null && baseline !== null && JSON.stringify(project) !== baseline

  const open = useCallback(async (target?: string) => {
    const runDir = target ?? await api.pickRunDir()
    if (!runDir) return false

    setBusy(true)
    const response = await api.call<OpenResult>('open', { run_dir: runDir })
    setBusy(false)
    if (response.error) {
      // **열지 못한 것이 앱을 멈추지 않는다.** 이미 열려 있던 프로젝트는 그대로 두고
      // 원인만 띄운다 — 고쳐서 다시 고르면 되는 상황이다.
      setError(response.error)
      return false
    }

    setOpened({ runDir: response.result.run_dir, path: response.result.project_path })
    setProject(response.result.project)
    setBaseline(JSON.stringify(response.result.project))
    setError(null)
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
      state: () => ({ unsaved, project, error, opened, environment })
    }
  })

  const ffmpeg = environment?.tools.ffmpeg
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
      <main className="body">
        {context?.backend.failure && (
          <Notice kind="danger" title="백엔드를 실행할 수 없다" details={[context.backend.failure]}>
            프로젝트를 열고 저장하는 모든 동작이 백엔드를 지난다.
          </Notice>
        )}
        {ffmpeg && !ffmpeg.found && (
          // 경고다 — 지금 막는 것이 없다. FFmpeg는 동봉하지 않는다 (스파이크 5.2).
          <Notice kind="warn" title="FFmpeg가 PATH에 없다">
            프로젝트를 열고 고치는 데는 필요 없지만, 프리뷰와 최종 렌더는 FFmpeg를 쓴다.
            설치하고 PATH에 넣은 뒤 앱을 다시 시작한다.
          </Notice>
        )}
        {error && <ErrorNotice error={error} />}
        {project && opened
          ? <ProjectSummary project={project} runDir={opened.runDir} />
          : <OpenScreen busy={busy} onOpen={() => { void open() }} />}
      </main>
    </div>
  )
}
