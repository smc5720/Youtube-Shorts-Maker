// 프로토타입 main 프로세스 — 창을 하나 띄우고, Python 백엔드를 자식으로 붙인다.
//
// 렌더러는 Node를 못 보고(`nodeIntegration: false`, `contextIsolation: true`), preload가
// 열어 둔 `window.api`로만 말한다. 백엔드로 나가는 길이 이 파일 하나뿐이라는 것이
// 이 배치의 요점이다 — 렌더러가 직접 프로세스를 띄우거나 파일을 읽지 않는다.
//
// `--smoke`를 주면 창을 띄운 뒤 렌더러가 시나리오를 자동으로 돌고, 끝나면 화면을 캡처해
// PNG와 측정치 JSON을 남기고 종료한다. 사람 없이 재현 가능한 증거를 남기기 위함이다.

const { app, BrowserWindow, ipcMain } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')
const readline = require('node:readline')

const SMOKE = process.argv.includes('--smoke')
const SPIKE_DIR = path.resolve(__dirname, '..', '..')
const REPO_ROOT = path.resolve(SPIKE_DIR, '..', '..', '..')
const BACKEND = path.join(__dirname, '..', 'backend', 'server.py')
const SAMPLE_ROOT = path.join(SPIKE_DIR, 'sample-run')

// 개발 중에는 저장소의 가상환경을 그대로 쓴다. 배포 시 무엇을 동봉할지는 스파이크 문서 5장.
const PYTHON = process.env.SPIKE_PYTHON
  || path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')

// Windows에서 Electron의 stdout은 부모 셸에 붙지 않는다 — `console.log`만 두면 스모크가
// 왜 멈췄는지 볼 방법이 없다. 그래서 로그를 파일로도 남긴다.
const LOG_PATH = path.join(SPIKE_DIR, 'prototype-smoke.log')
function log (...parts) {
  const line = `[${new Date().toISOString()}] ${parts.join(' ')}`
  console.log(line)
  fs.appendFileSync(LOG_PATH, line + '\n')
}

let backend = null
let nextId = 1
const pending = new Map()
let mainWindow = null
let backendStartedAt = 0
let readyAt = 0
let signalReady = null
// 백엔드의 `ready`가 오기 전에 렌더러가 물으면 기다린다. 기다리지 않으면 준비 시간이
// 0으로 보이고, 화면에 뜨는 값이 실제와 갈린다.
const backendReady = new Promise((resolve) => { signalReady = resolve })

function startBackend () {
  backendStartedAt = performance.now()
  backend = spawn(PYTHON, ['-u', BACKEND], { stdio: ['pipe', 'pipe', 'pipe'] })

  readline.createInterface({ input: backend.stdout }).on('line', (line) => {
    let message
    try {
      message = JSON.parse(line)
    } catch {
      console.error('[backend] 파싱 불가:', line)
      return
    }
    if (message.event === 'ready') {
      readyAt = performance.now() - backendStartedAt
      log('[backend] 준비', `${Math.round(readyAt)}ms`, `pid=${message.pid}`)
      signalReady()
      return
    }
    if (message.event) {
      // 진행률 등 요청과 짝이 없는 메시지는 렌더러로 흘린다.
      if (mainWindow) mainWindow.webContents.send('backend-event', message)
      return
    }
    const settle = pending.get(message.id)
    if (!settle) return
    pending.delete(message.id)
    if (message.error) settle.reject(new Error(message.error))
    else settle.resolve(message.result)
  })

  backend.stderr.on('data', (chunk) => log('[backend:stderr]', String(chunk).trim()))
  backend.on('exit', (code) => log('[backend] 종료', code))
  backend.on('error', (failure) => log('[backend] 실행 실패', failure.message))
}

function call (method, params) {
  return new Promise((resolve, reject) => {
    const id = nextId++
    pending.set(id, { resolve, reject })
    backend.stdin.write(JSON.stringify({ id, method, params }) + '\n')
  })
}

function latestSampleRun () {
  if (!fs.existsSync(SAMPLE_ROOT)) return null
  const runs = fs.readdirSync(SAMPLE_ROOT)
    .filter((name) => name.startsWith('run-'))
    .sort()
  return runs.length ? path.join(SAMPLE_ROOT, runs[runs.length - 1]) : null
}

async function createWindow () {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 860,
    show: !SMOKE,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })
  // 렌더러에서 난 예외를 여기로 끌어온다. 이것이 없으면 화면이 빈 채로 멈춘 이유를 볼 수 없다.
  mainWindow.webContents.on('console-message', (event) => {
    log('[renderer]', event.level ?? '', event.message ?? '')
  })
  mainWindow.webContents.on('preload-error', (_event, preloadPath, failure) => {
    log('[preload] 실패', preloadPath, failure.message)
  })
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    log('[renderer] 프로세스 종료', JSON.stringify(details))
  })

  await mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  if (SMOKE) mainWindow.showInactive()
}

app.whenReady().then(async () => {
  startBackend()

  ipcMain.handle('rpc', (_event, method, params) => call(method, params))
  ipcMain.handle('context', async () => {
    await backendReady
    return {
      smoke: SMOKE,
      runDir: latestSampleRun(),
      python: PYTHON,
      backendReadyMs: Math.round(readyAt * 100) / 100,
      versions: {
        electron: process.versions.electron,
        chrome: process.versions.chrome,
        node: process.versions.node
      }
    }
  })

  // 스모크: 렌더러가 시나리오를 끝내면 화면을 캡처하고 측정치를 남긴 뒤 종료한다.
  ipcMain.handle('smoke-done', async (_event, report) => {
    // **측정치를 먼저 쓴다.** 캡처가 막히면(창이 숨어 있을 때 그런 일이 있다) 결과까지
    // 함께 사라진다 — 실제로 이 스파이크에서 한 번 그렇게 잃었다.
    const payload = {
      ...report,
      backend_ready_ms: Math.round(readyAt * 100) / 100,
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node
    }
    fs.writeFileSync(
      path.join(SPIKE_DIR, 'prototype-results.json'),
      JSON.stringify(payload, null, 2) + '\n'
    )
    log('[smoke] 측정치 기록', JSON.stringify(report.ok))
    try {
      const image = await mainWindow.webContents.capturePage()
      fs.writeFileSync(path.join(SPIKE_DIR, 'prototype-screenshot.png'), image.toPNG())
      log('[smoke] 화면 캡처 완료')
    } catch (failure) {
      log('[smoke] 캡처 실패', failure.message)
    }
    setTimeout(() => app.exit(report.ok ? 0 : 1), 200)
    return true
  })

  await createWindow()
  log('[main] 창 준비 완료', SMOKE ? '(스모크)' : '')

  if (SMOKE) {
    // 무한정 매달리지 않는다. 렌더 한 번이 수십 초이므로 여유를 두고 3분에 끊는다.
    setTimeout(() => {
      log('[smoke] 시간 초과 — 시나리오가 끝나지 않았다')
      app.exit(2)
    }, 180000)
  }
})

app.on('window-all-closed', () => {
  if (backend) backend.stdin.end() // 백엔드는 stdin이 닫히면 스스로 끝난다
  app.quit()
})
