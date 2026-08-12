// 프로토타입 화면 — 장면 목록, 프리뷰 프레임, 최종 렌더 진행률.
//
// 이 파일에는 파일 시스템도 FFmpeg도 `shorts_maker`도 없다. 화면에 보이는 값은 전부
// `window.api.call()`로 Python에서 온 것이다 — #25가 확인하려는 경로가 그것이다.

import React, { useCallback, useEffect, useRef, useState } from 'react'

const PING_SAMPLES = 100

function median (values) {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]
}

export default function App () {
  const [context, setContext] = useState(null)
  const [project, setProject] = useState(null)
  const [selected, setSelected] = useState(0)
  const [preview, setPreview] = useState(null)
  const [progress, setProgress] = useState(null)
  const [render, setRender] = useState(null)
  const [ping, setPing] = useState(null)
  const [error, setError] = useState(null)
  const jobRef = useRef(null)

  useEffect(() => {
    window.api.onEvent((message) => {
      if (message.event === 'progress') setProgress(message)
    })
    window.api.context().then(setContext)
  }, [])

  const open = useCallback(async (runDir) => {
    const opened = await window.api.call('open', { run_dir: runDir })
    setProject(opened)
    return opened
  }, [])

  const measurePing = useCallback(async () => {
    const samples = []
    for (let index = 0; index < PING_SAMPLES; index += 1) {
      const started = performance.now()
      await window.api.call('ping', { index })
      samples.push(performance.now() - started)
    }
    const summary = {
      n: samples.length,
      median_ms: Math.round(median(samples) * 1000) / 1000,
      max_ms: Math.round(Math.max(...samples) * 1000) / 1000
    }
    setPing(summary)
    return summary
  }, [])

  const showPreview = useCallback(async (opened, index) => {
    const scene = opened.scenes[index]
    // 장면 한가운데를 뽑는다. 경계 프레임은 앞뒤 요소가 갈리는 자리라 대표 프레임이 아니다.
    const at = (scene.start + scene.end) / 2
    const frame = await window.api.call('preview', { run_dir: opened.run_dir, at_sec: at })
    setSelected(index)
    setPreview({ ...frame, at, index })
    return frame
  }, [])

  const runRender = useCallback(async (opened) => {
    setRender(null)
    setProgress(null)
    const promise = window.api.call('render', { run_dir: opened.run_dir })
    jobRef.current = promise
    const done = await promise
    setRender(done)
    return done
  }, [])

  // 스모크 시나리오 — 사람 없이 같은 순서를 돌고 결과를 main에 넘긴다.
  useEffect(() => {
    console.log('[smoke] context', JSON.stringify(context))
    if (!context || !context.smoke) return
    let cancelled = false
    ;(async () => {
      const report = { ok: false, run_dir: context.runDir }
      try {
        if (!context.runDir) throw new Error('sample-run이 없다. sample_run.py를 먼저 돌린다')
        const opened = await open(context.runDir)
        console.log('[smoke] open 완료', opened.scenes.length, '장면')
        report.scenes = opened.scenes.length
        report.total_sec = opened.total_sec
        report.ipc_ping = await measurePing()
        console.log('[smoke] ping 완료')
        const answerIndex = opened.scenes.findIndex((scene) => scene.role === 'answer')
        const frame = await showPreview(opened, answerIndex >= 0 ? answerIndex : 0)
        report.preview = { elapsed_ms: frame.elapsed_ms, bytes: frame.bytes }
        console.log('[smoke] preview 완료', frame.elapsed_ms, 'ms')
        const rendered = await runRender(opened)
        console.log('[smoke] render 완료', rendered.elapsed_ms, 'ms')
        report.render = { elapsed_ms: rendered.elapsed_ms, bytes: rendered.bytes, frames: rendered.frames }
        report.ok = true
      } catch (failure) {
        report.error = String(failure)
        setError(String(failure))
      }
      if (!cancelled) {
        // 마지막 상태가 화면에 반영된 뒤 캡처되도록 한 틱 미룬다.
        setTimeout(() => window.api.smokeDone(report), 600)
      }
    })()
    return () => { cancelled = true }
  }, [context, open, measurePing, showPreview, runRender])

  if (!context) return <main className="shell"><p>준비 중…</p></main>

  return (
    <main className="shell">
      <header>
        <h1>스파이크 #25 프로토타입</h1>
        <p className="muted">
          React(renderer) → preload → Electron main → Python(stdio JSON Lines) → 화면
        </p>
        <p className="muted small">
          electron {context.versions.electron} · chromium {context.versions.chrome} ·
          node {context.versions.node} · 백엔드 준비 {context.backendReadyMs}ms
        </p>
      </header>

      {error && <p className="error">{error}</p>}

      <section className="columns">
        <div className="panel">
          <h2>장면 {project ? `(${project.scenes.length})` : ''}</h2>
          {!project && <button onClick={() => open(context.runDir)}>run 디렉터리 열기</button>}
          <ol className="scenes">
            {project?.scenes.map((scene) => (
              <li
                key={scene.index}
                className={scene.index === selected ? 'scene selected' : 'scene'}
                onClick={() => showPreview(project, scene.index)}
              >
                <span className="role">{scene.role}</span>
                <span className="time">{scene.start.toFixed(2)}s–{scene.end.toFixed(2)}s</span>
                <p>{scene.text || <em className="muted">텍스트 없음</em>}</p>
              </li>
            ))}
          </ol>
        </div>

        <div className="panel">
          <h2>프리뷰</h2>
          {preview
            ? (
              <>
                <img className="frame" alt="preview" src={`data:image/png;base64,${preview.png_base64}`} />
                <p className="muted small">
                  {preview.at.toFixed(2)}s 프레임 · {preview.elapsed_ms}ms ·
                  {' '}{(preview.bytes / 1024).toFixed(0)}KB
                </p>
              </>
              )
            : <p className="muted">장면을 고르면 Python이 프레임 하나를 그려 보낸다</p>}
        </div>

        <div className="panel">
          <h2>최종 렌더</h2>
          <button disabled={!project} onClick={() => project && runRender(project)}>
            렌더 실행
          </button>
          {progress && (
            <>
              <div className="bar"><div className="fill" style={{ width: `${progress.ratio * 100}%` }} /></div>
              <p className="muted small">
                {progress.frame}/{progress.total} 프레임 · {(progress.ratio * 100).toFixed(0)}%
              </p>
            </>
          )}
          {render && (
            <p className="ok">
              완료 — {(render.elapsed_ms / 1000).toFixed(1)}초 ·
              {' '}{(render.bytes / 1024 / 1024).toFixed(1)}MB
            </p>
          )}
          {ping && (
            <p className="muted small">
              IPC 왕복 {ping.n}회 — 중앙값 {ping.median_ms}ms / 최대 {ping.max_ms}ms
            </p>
          )}
        </div>
      </section>
    </main>
  )
}
