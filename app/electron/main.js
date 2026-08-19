// Electron main — 창 하나와 Python 백엔드 하나 (이슈 #26, PRD 14.1).
//
// **백엔드로 나가는 길이 이 파일 하나다.** 렌더러는 Node도 파일 시스템도 프로세스도 보지
// 못하고(`contextIsolation`·`sandbox`), preload가 연 `window.api`로만 말한다. 이 배치를
// 지키면 프레임워크를 갈아도 백엔드는 그대로다 (스파이크 #25 8장).
//
// 백엔드는 `shorts_maker.api`이고 프로토콜은 JSON Lines다. **고아 프로세스를 막는 코드가
// 여기 없는 것이 정상이다** — stdin이 닫히면 백엔드가 스스로 끝난다 (스파이크 4.2).

const { app, BrowserWindow, Menu, dialog, ipcMain, session, shell } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')
const readline = require('node:readline')

const REPO_ROOT = path.resolve(__dirname, '..', '..')
const SMOKE = readFlag('--smoke')

let logPath = null
let backend = null
let backendInfo = { command: null, pid: null, ready: false, failure: null }
let mainWindow = null
let nextId = 1
const pending = new Map()

let signalReady = null
let backendReady = new Promise((resolve) => { signalReady = resolve })

// 렌더러가 저장 상태를 여기로 알린다. 창을 닫을 때 물어볼지가 이 값 하나로 갈린다.
let unsaved = false
let allowClose = false
let pendingSave = null

// --- 로그 ---------------------------------------------------------------------

// **Windows에서 Electron의 stdout은 부모 셸에 붙지 않는다** (스파이크 7장). `console.log`만
// 두면 앱이 왜 빈 화면에서 멈췄는지 볼 방법이 없어서 파일로도 남긴다.
function log (...parts) {
  const line = `[${new Date().toISOString()}] ${parts.join(' ')}`
  console.log(line)
  try {
    if (!logPath) logPath = process.env.SHORTS_APP_LOG || path.join(app.getPath('userData'), 'app.log')
    fs.mkdirSync(path.dirname(logPath), { recursive: true })
    fs.appendFileSync(logPath, line + '\n')
  } catch {
    // 로그를 못 남기는 것이 앱을 멈출 이유는 아니다.
  }
}

function readFlag (name) {
  const match = process.argv.find((argument) => argument === name || argument.startsWith(`${name}=`))
  if (!match) return null
  const [, value] = match.split('=')
  return value ?? true
}

// --- 백엔드 -------------------------------------------------------------------

// 개발 중에는 저장소의 가상환경을, 동결 배포에서는 실행 파일 옆의 onedir을 쓴다
// (스파이크 5.1). `SHORTS_PYTHON`은 다른 인터프리터를 끼우는 자리다.
function backendCommand () {
  const frozen = path.join(
    process.resourcesPath || '',
    'backend',
    process.platform === 'win32' ? 'shorts-backend.exe' : 'shorts-backend'
  )
  if (app.isPackaged && fs.existsSync(frozen)) {
    return { command: frozen, args: [], env: {} }
  }

  const bundled = process.platform === 'win32'
    ? path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(REPO_ROOT, '.venv', 'bin', 'python')
  const python = process.env.SHORTS_PYTHON || (fs.existsSync(bundled) ? bundled : 'python')
  return {
    command: python,
    // `-u`로 버퍼링을 끈다. 버퍼에 남은 응답은 없는 응답과 구분되지 않는다.
    args: ['-u', '-m', 'shorts_maker.api'],
    env: { PYTHONPATH: path.join(REPO_ROOT, 'src') }
  }
}

function startBackend () {
  const { command, args, env } = backendCommand()
  backendInfo = { command, pid: null, ready: false, failure: null }
  log('[backend] 실행', command, args.join(' '))

  backend = spawn(command, args, { env: { ...process.env, ...env }, stdio: ['pipe', 'pipe', 'pipe'] })

  readline.createInterface({ input: backend.stdout }).on('line', (line) => {
    let message
    try {
      message = JSON.parse(line)
    } catch {
      log('[backend] 파싱 불가:', line)
      return
    }
    if (message.event === 'ready') {
      backendInfo = { ...backendInfo, pid: message.pid, ready: true, protocol: message.protocol }
      log('[backend] 준비', `pid=${message.pid}`, `protocol=${message.protocol}`)
      signalReady()
      return
    }
    if (message.event) {
      // 진행률처럼 요청과 짝이 없는 알림은 렌더러로 흘린다 (#30이 쓴다).
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('backend-event', message)
      return
    }
    const settle = pending.get(message.id)
    if (!settle) return
    pending.delete(message.id)
    settle.resolve(message)
  })

  backend.stderr.on('data', (chunk) => log('[backend:stderr]', String(chunk).trimEnd()))
  backend.on('error', (failure) => fail(`백엔드를 실행할 수 없다: ${command} — ${failure.message}`))
  backend.on('exit', (code, signal) => {
    log('[backend] 종료', `code=${code}`, `signal=${signal}`)
    if (!allowClose && code !== 0) fail(`백엔드가 예기치 않게 끝났다 (종료 코드 ${code})`)
  })
}

// 백엔드가 없으면 남은 요청은 영원히 답을 못 받는다. **기다리게 두지 않고 실패로 끝낸다** —
// 앱이 응답 없이 멈춘 것과 백엔드가 죽은 것은 화면에서 구분되어야 한다.
function fail (message) {
  backendInfo = { ...backendInfo, ready: false, failure: message }
  log('[backend] 실패', message)
  signalReady()
  for (const [id, settle] of pending) {
    settle.resolve({ id, error: { code: 'backend', message, details: [] } })
  }
  pending.clear()
}

function call (method, params) {
  if (backendInfo.failure) {
    return Promise.resolve({ error: { code: 'backend', message: backendInfo.failure, details: [] } })
  }
  return new Promise((resolve) => {
    const id = nextId++
    pending.set(id, { resolve })
    try {
      backend.stdin.write(JSON.stringify({ id, method, params: params || {} }) + '\n')
    } catch (failure) {
      // 백엔드가 방금 죽어 파이프가 닫혔다. `exit` 핸들러와 경합하므로 여기서도 답을 준다.
      pending.delete(id)
      resolve({ id, error: { code: 'backend', message: `요청을 보내지 못했다 — ${failure.message}`, details: [] } })
    }
  })
}

// --- 창 ------------------------------------------------------------------------

async function createWindow () {
  mainWindow = new BrowserWindow({
    // D2 확정 스펙 3.1 — 1440x900 기준, 최소 1280x720.
    // **`useContentSize`가 필요하다.** 기본값은 창틀을 포함한 크기라 Windows에서 콘텐츠가
    // 1440보다 좁아지고, 그러면 확정 수치(좌 296 / 우 340)가 그 폭에서 나오지 않는다 —
    // 폭 계산이 `100vw`(콘텐츠 폭) 위에 있기 때문이다 (#27, `app.css`의 `.panel--scenes`).
    useContentSize: true,
    width: 1440,
    height: 900,
    minWidth: 1280,
    minHeight: 720,
    backgroundColor: '#0E0F12',
    show: false,
    title: 'YouTube Shorts Maker',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })

  mainWindow.webContents.on('console-message', (event) => {
    log('[renderer]', event.level ?? '', event.message ?? '')
  })
  mainWindow.webContents.on('preload-error', (_event, file, failure) => {
    log('[preload] 실패', file, failure.message)
  })
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    log('[renderer] 프로세스 종료', JSON.stringify(details))
  })
  // **바깥으로 나가는 길을 막는다.** 앱은 네트워크를 쓰지 않으므로(브리프 3.3) 새 창도
  // 페이지 이동도 필요 없다. 링크가 생기면 기본 브라우저로 보낸다.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) shell.openExternal(url)
    return { action: 'deny' }
  })

  mainWindow.on('close', (event) => {
    if (allowClose || !unsaved) return
    event.preventDefault()
    confirmClose()
  })

  await mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  mainWindow.show()
}

// 확인 대화상자를 부르는 자리를 하나로 둔다. 스모크가 여기를 바꿔 끼워 시나리오를 돌린다 —
// 모달이 뜨면 자동 실행이 거기서 멈추기 때문이고, 바뀌는 것은 **대답을 얻는 방법**뿐이라
// 닫기를 막을지 판단하는 코드는 그대로 지난다.
let ask = (options) => dialog.showMessageBox(mainWindow, options)

// 파일·디렉터리 선택도 같은 자리를 지난다 (#80). 스모크가 여기를 바꿔 끼워 시나리오를 돌리고
// (모달이 뜨면 자동 실행이 거기서 멈춘다), 바뀌는 것은 **경로를 얻는 방법**뿐이라 고른 값을
// 무엇으로 판정하는지는 그대로 지난다.
let pick = (options) => dialog.showOpenDialog(mainWindow, options)

// 렌더가 만든 파일을 파일 관리자에서 보여주는 자리 (#30). 같은 이유로 바꿔 끼운다 —
// **탐색기 창은 모달이 아니지만 스모크가 도는 동안 창을 쌓는다.** 열지 못해도 화면의
// 경로는 그대로 남으므로 실패는 값으로 돌려준다.
let reveal = (target) => {
  if (!fs.existsSync(target)) return false
  shell.showItemInFolder(target)
  return true
}

// 저장하지 않은 변경이 있는 채로 창을 닫을 때. **버리는 선택지만 주지 않는다** —
// 이 앱에서 잃는 것은 사용자가 검수하며 고친 내용이다.
async function confirmClose () {
  const { response } = await ask({
    type: 'warning',
    buttons: ['저장하고 닫기', '저장하지 않고 닫기', '취소'],
    defaultId: 0,
    cancelId: 2,
    title: '저장하지 않은 변경',
    message: '저장하지 않은 변경이 있다.',
    detail: '닫으면 이 변경은 사라진다.',
    noLink: true
  })
  if (response === 2) return
  if (response === 0) {
    const saved = await requestSave()
    if (!saved) return // 저장이 실패했으면 닫지 않는다. 화면에 원인이 떠 있다.
  }
  allowClose = true
  mainWindow.close()
}

// 저장은 렌더러가 들고 있는 상태로 한다. main이 프로젝트 내용을 따로 들면 두 곳이 갈린다.
function requestSave () {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      if (pendingSave) { pendingSave = null; resolve(false) }
    }, 10000)
    pendingSave = (ok) => { clearTimeout(timer); resolve(ok) }
    mainWindow.webContents.send('save-request')
  })
}

function buildMenu () {
  // 기본 메뉴를 그대로 두지 않는다 — Help의 "Learn More"가 바깥으로 나간다.
  const send = (channel) => () => mainWindow && mainWindow.webContents.send(channel)
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: '파일',
      submenu: [
        { label: '프로젝트 열기…', accelerator: 'CmdOrCtrl+O', click: send('menu-open') },
        { label: '저장', accelerator: 'CmdOrCtrl+S', click: send('menu-save') },
        { type: 'separator' },
        { role: 'quit', label: '종료' }
      ]
    },
    {
      label: '보기',
      submenu: [
        { role: 'reload', label: '새로 고침' },
        { role: 'toggleDevTools', label: '개발자 도구' },
        { type: 'separator' },
        { role: 'resetZoom', label: '실제 크기' },
        { role: 'zoomIn', label: '확대' },
        { role: 'zoomOut', label: '축소' }
      ]
    }
  ]))
}

// --- 준비 ---------------------------------------------------------------------

// 바깥으로 나간 요청. **비어 있어야 한다** — 폰트도 아이콘도 번들에서 오고 데이터는 전부
// 백엔드 파이프를 지난다 (D2 발주서 3.3). CSP가 이미 막지만, 여기서 세어 두면 "막혔다"가
// 아니라 "시도조차 없었다"를 확인할 수 있고 스모크가 그 값을 읽는다.
const externalRequests = []
const LOCAL_SCHEME = /^(file|devtools|blob|data|chrome-extension):/

app.whenReady().then(async () => {
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
    if (LOCAL_SCHEME.test(details.url)) return callback({})
    externalRequests.push(details.url)
    log('[net] 바깥 요청을 막았다', details.url)
    callback({ cancel: true })
  })

  startBackend()
  buildMenu()

  ipcMain.handle('rpc', (_event, method, params) => call(method, params))
  ipcMain.handle('context', async () => {
    await backendReady
    return {
      smoke: SMOKE,
      backend: backendInfo,
      logPath,
      externalRequests,
      versions: {
        electron: process.versions.electron,
        chrome: process.versions.chrome,
        node: process.versions.node
      }
    }
  })
  ipcMain.handle('pick-run-dir', async () => {
    const { canceled, filePaths } = await pick({
      title: '프로젝트를 열 run 디렉터리를 고른다',
      defaultPath: path.join(REPO_ROOT, 'outputs'),
      properties: ['openDirectory']
    })
    return canceled ? null : filePaths[0]
  })
  // 배경 사용자 파일 (#80). **받는 확장자를 렌더러에서 받는다** — 목록은 백엔드가 소유하고
  // (`presets.background_files`) 여기서 다시 적으면 세 번째 사본이 생긴다.
  //
  // **필터가 거부를 대신하지 않는다.** "모든 파일"을 함께 두는 것은 사용자가 이름을 직접
  // 입력할 수 있고 플랫폼마다 필터의 강제력이 다르기 때문이다 — 받지 않는 형식을 고르는
  // 길이 실제로 있으므로, 판정은 값을 적용하는 쪽(렌더러 `App.pickBackground`)에 있다.
  ipcMain.handle('pick-background-file', async (_event, extensions) => {
    const accepted = Array.isArray(extensions) ? extensions : []
    const { canceled, filePaths } = await pick({
      title: '배경으로 쓸 이미지나 영상을 고른다',
      properties: ['openFile'],
      filters: [
        { name: `배경 파일 (${accepted.map((name) => `.${name}`).join(' ')})`, extensions: accepted },
        { name: '모든 파일', extensions: ['*'] }
      ]
    })
    return canceled ? null : filePaths[0]
  })
  // 렌더 결과를 파일 탐색기에서 보여준다 (#30). **`ask`·`pick`과 같은 자리를 지난다** —
  // 스모크가 여기를 바꿔 끼우지 않으면 자동 실행 중에 탐색기 창이 뜬다.
  ipcMain.handle('reveal', (_event, target) => reveal(String(target)))
  // **동기다.** `invoke`로 받으면 렌더러가 화면을 고친 뒤에도 main이 잠깐 옛 값을 들고
  // 있고, 그 사이에 창을 닫으면 확인 없이 닫힌다 — 스모크가 실제로 1ms 차이로 밟았다.
  // 렌더러를 잠깐 세우더라도 "화면에 보이는 상태 = main이 아는 상태"가 성립해야 한다.
  ipcMain.on('set-unsaved', (event, value) => {
    unsaved = Boolean(value)
    event.returnValue = true
  })
  ipcMain.handle('save-result', (_event, ok) => {
    if (pendingSave) { const settle = pendingSave; pendingSave = null; settle(Boolean(ok)) }
  })

  await createWindow()
  log('[main] 창 준비 완료', SMOKE ? `(스모크: ${SMOKE})` : '')

  if (SMOKE) {
    const { runSmoke } = require('./smoke.js')
    runSmoke({
      scenario: SMOKE,
      window: mainWindow,
      log,
      app,
      backendInfo: () => backendInfo,
      externalRequests: () => externalRequests,
      setAsk: (handler) => { ask = handler },
      setPick: (handler) => { pick = handler },
      setReveal: (handler) => { reveal = handler }
    })
  }
})

app.on('window-all-closed', () => {
  // stdin을 닫으면 백엔드가 EOF로 끝난다 (스파이크 4.2). 죽이지 않아도 되는 이유다.
  if (backend) backend.stdin.end()
  app.quit()
})
