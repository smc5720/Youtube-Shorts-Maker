// #26 스모크 — 앱을 실제로 띄워 완료 조건을 밟고 결과를 남긴다.
//
// 세 번 띄운다. **한 프로세스 안에서 다시 여는 것은 "재시작"의 증거가 되지 못하기 때문이다.**
//
//   1. edit   — 열기 · 스키마 오류 · 편집 표시 · 닫기 확인 · 저장 · 저장하며 닫기
//   2. verify — 다시 띄워서 저장한 것이 그대로 열리는지
//   3. idle   — 띄운 뒤 Electron만 강제 종료해 백엔드가 남는지 (스파이크 4.2)
//
// 실행: npm run smoke   (결과는 app/smoke/results.json)

import { spawn, spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const APP_DIR = fileURLToPath(new URL('..', import.meta.url))
const REPO_ROOT = path.resolve(APP_DIR, '..')
const ELECTRON = require('electron')
const { MARKERS } = require('../electron/smoke.js')

const RESULTS = path.join(APP_DIR, 'smoke', 'results.json')
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), 'shorts-smoke-'))

const results = { phases: [], checks: [] }
const record = (name, ok, detail) => {
  results.checks.push({ name, ok: Boolean(ok), detail: detail === undefined ? null : String(detail) })
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name}${detail === undefined ? '' : ` — ${detail}`}`)
}

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function python () {
  const bundled = process.platform === 'win32'
    ? path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(REPO_ROOT, '.venv', 'bin', 'python')
  return process.env.SHORTS_PYTHON || (fs.existsSync(bundled) ? bundled : 'python')
}

function electron (scenario, environment) {
  return spawn(ELECTRON, ['.', `--smoke=${scenario}`], {
    cwd: APP_DIR,
    env: { ...process.env, ...environment },
    stdio: 'inherit'
  })
}

function wait (child, ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => { child.kill(); resolve({ timedOut: true, code: null }) }, ms)
    child.on('exit', (code) => { clearTimeout(timer); resolve({ timedOut: false, code }) })
  })
}

function alive (pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

// --- 준비 -----------------------------------------------------------------------

const made = spawnSync(python(), [path.join(APP_DIR, 'smoke', 'make_run.py'), '--out', WORK], {
  encoding: 'utf8'
})
if (made.status !== 0) {
  console.error(made.stderr || made.error)
  process.exit(1)
}
const RUN = made.stdout.trim()
record('스모크가 열 run 디렉터리를 만든다', fs.existsSync(path.join(RUN, 'project.json')), RUN)

// 계약을 어긴 사본. **원본을 망가뜨리지 않는다** — 같은 디렉터리를 고쳤다 되돌리면 앞선
// 단계가 무엇을 봤는지가 순서에 좌우된다.
const BROKEN = path.join(WORK, 'broken-run')
fs.cpSync(RUN, BROKEN, { recursive: true })
const broken = JSON.parse(fs.readFileSync(path.join(BROKEN, 'project.json'), 'utf8'))
broken.render.fps = '서른'
fs.writeFileSync(path.join(BROKEN, 'project.json'), JSON.stringify(broken, null, 2) + '\n')

// --- 1. 열기부터 저장까지 ----------------------------------------------------------

const editOut = path.join(WORK, 'edit.json')
const edit = await wait(electron('edit', {
  SHORTS_SMOKE_RUN: RUN,
  SHORTS_SMOKE_BROKEN: BROKEN,
  SHORTS_SMOKE_OUT: editOut,
  SHORTS_SMOKE_SHOT: path.join(APP_DIR, 'smoke', 'screenshot.png'),
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
}), 180000)

const editResult = fs.existsSync(editOut) ? JSON.parse(fs.readFileSync(editOut, 'utf8')) : null
results.phases.push({ scenario: 'edit', ...edit, result: editResult })
if (editResult) results.checks.push(...editResult.checks)
record('편집 시나리오가 끝난다', editResult && editResult.ok && !edit.timedOut)

// "저장하고 닫기"가 실제로 파일까지 갔는지는 앱이 끝난 뒤에만 확인할 수 있다.
const afterClose = JSON.parse(fs.readFileSync(path.join(RUN, 'project.json'), 'utf8'))
record('저장하고 닫기가 파일에 반영된다', afterClose.render.cta_tail === MARKERS.close, afterClose.render.cta_tail)

// --- 2. 재시작하고 다시 열기 --------------------------------------------------------

const verifyOut = path.join(WORK, 'verify.json')
const verify = await wait(electron('verify', {
  SHORTS_SMOKE_RUN: RUN,
  SHORTS_SMOKE_OUT: verifyOut,
  SHORTS_SMOKE_EXPECT: MARKERS.close,
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
}), 120000)

const verifyResult = fs.existsSync(verifyOut) ? JSON.parse(fs.readFileSync(verifyOut, 'utf8')) : null
results.phases.push({ scenario: 'verify', ...verify, result: verifyResult })
if (verifyResult) results.checks.push(...verifyResult.checks)
record('재시작 시나리오가 끝난다', verifyResult && verifyResult.ok && !verify.timedOut)

// --- 3. 강제 종료 뒤 백엔드가 남는가 -------------------------------------------------

const readyPath = path.join(WORK, 'ready.json')
const idle = electron('idle', {
  SHORTS_SMOKE_READY: readyPath,
  SHORTS_SMOKE_OUT: path.join(WORK, 'idle.json'),
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
})

let ready = null
for (let attempt = 0; attempt < 100 && !ready; attempt += 1) {
  await delay(200)
  if (fs.existsSync(readyPath)) ready = JSON.parse(fs.readFileSync(readyPath, 'utf8'))
}
record('백엔드 pid를 확인한다', Boolean(ready && ready.backend), ready && JSON.stringify(ready))

if (ready) {
  // **트리를 죽이지 않는다** (`/T` 없음). 백엔드가 스스로 끝나는지를 보는 것이므로,
  // 자식까지 함께 죽이면 확인하려는 것이 확인되지 않는다 (스파이크 4.2).
  if (process.platform === 'win32') spawnSync('taskkill', ['/F', '/PID', String(ready.electron)])
  else process.kill(ready.electron, 'SIGKILL')

  let gone = false
  for (let attempt = 0; attempt < 50 && !gone; attempt += 1) {
    await delay(200)
    gone = !alive(ready.backend)
  }
  record('앱을 강제 종료하면 백엔드도 사라진다', gone, `pid=${ready.backend}`)
  if (!gone) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/F', '/PID', String(ready.backend)])
    else process.kill(ready.backend, 'SIGKILL')
  }
}
idle.kill()

// --- 4. 번들이 바깥을 가리키지 않는가 -------------------------------------------------

// 실행 중 감시(main의 `onBeforeRequest`)와 **다른 층의 확인이다.** 그쪽은 "시도가 없었다"를,
// 이쪽은 "시도할 대상이 빌드에 없다"를 본다 — 시안의 CDN 링크가 되살아나는 경로가 이쪽이다
// (D2 확정 스펙 1.6).
const DIST = path.join(APP_DIR, 'dist')
const html = fs.readFileSync(path.join(DIST, 'index.html'), 'utf8')
record('index.html이 원격 출처를 참조하지 않는다', !/(src|href)\s*=\s*["']https?:/i.test(html))
record("CSP가 기본을 'none'으로 막는다", html.includes("default-src 'none'"))

const bundled = fs.readdirSync(path.join(DIST, 'assets'))
const remote = bundled
  .filter((name) => name.endsWith('.css'))
  .filter((name) => /url\(\s*["']?https?:/i.test(fs.readFileSync(path.join(DIST, 'assets', name), 'utf8')))
record('CSS가 원격 폰트를 부르지 않는다', remote.length === 0, remote.join(', '))

const fonts = bundled.filter((name) => name.endsWith('.otf'))
record('번들 폰트가 dist에 들어간다', fonts.length === 3, fonts.join(', '))

// --- 결과 -----------------------------------------------------------------------

results.ok = results.checks.every((check) => check.ok)
results.work_dir = WORK
results.electron = process.versions.electron ?? null
fs.writeFileSync(RESULTS, JSON.stringify(results, null, 2) + '\n')

const failed = results.checks.filter((check) => !check.ok)
console.log(`\n${results.checks.length - failed.length}/${results.checks.length} 통과 — ${RESULTS}`)
if (failed.length) console.log(failed.map((check) => `  FAIL ${check.name}`).join('\n'))
process.exit(results.ok ? 0 : 1)
