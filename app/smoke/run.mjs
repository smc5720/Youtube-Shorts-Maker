// 앱 스모크 (#26, #27, #28, #82, #79) — 앱을 실제로 띄워 완료 조건을 밟고 결과를 남긴다.
//
// 여섯 번 띄운다. **한 프로세스 안에서 다시 여는 것은 "재시작"의 증거가 되지 못하기 때문이다.**
//
//   1. edit             — 열기 · 스키마 오류 · 편집 표시 · 닫기 확인 · 저장 · 저장하며 닫기 (#26)
//   2. verify           — 다시 띄워서 저장한 것이 그대로 열리는지 (#26)
//   3. preview          — 3분할 · 장면 목록 · 프리뷰 프레임 · 대기 표현 2종 · 총 길이 (#27),
//                         장면 길이 조정 (#82), 자막 스타일·배경 프리셋 교체 (#79)
//   4. questions        — 2분할 · 세 상태 표기 · 확인 기록 · 재생성 표시 · 순서·추가·삭제 (#28)
//   5. questions-verify — 다시 띄워서 문제 편집·길이·프리셋이 파일에 남았는지 (#28, #82, #79)
//   6. idle             — 띄운 뒤 Electron만 강제 종료해 백엔드가 남는지 (스파이크 4.2)
//
// **3번은 FFmpeg를 요구한다.** 실제 프레임이 나오는지가 그 시나리오의 절반이라 대역으로
// 바꾸면 확인하려는 것이 확인되지 않는다. 4번은 요구하지 않는다 — 그 화면에는 프리뷰가 없고
// 콘텐츠 편집이 프레임을 다시 만들지 않는 것 자체가 확인 대상이다.
//
// **순서가 있다.** 4번이 `run-smoke`의 두 파일을 고치므로 앞선 시나리오보다 뒤에 온다.
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
const { run: RUN, long: LONG } = JSON.parse(made.stdout.trim())
record('스모크가 열 run 디렉터리를 만든다', fs.existsSync(path.join(RUN, 'project.json')), RUN)
record('상한을 넘는 run 디렉터리도 만든다', fs.existsSync(path.join(LONG, 'scenes.json')), LONG)

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

// --- 3. 장면 목록과 프리뷰 (#27) ----------------------------------------------------

const previewOut = path.join(WORK, 'preview.json')
const preview = await wait(electron('preview', {
  SHORTS_SMOKE_RUN: RUN,
  SHORTS_SMOKE_LONG: LONG,
  SHORTS_SMOKE_OUT: previewOut,
  SHORTS_SMOKE_SHOT: path.join(APP_DIR, 'smoke', 'screenshot-preview.png'),
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
}), 240000)

const previewResult = fs.existsSync(previewOut) ? JSON.parse(fs.readFileSync(previewOut, 'utf8')) : null
results.phases.push({ scenario: 'preview', ...preview, result: previewResult })
if (previewResult) results.checks.push(...previewResult.checks)
record('프리뷰 시나리오가 끝난다', previewResult && previewResult.ok && !preview.timedOut)

// **프리뷰가 최종 렌더 경로를 지나지 않았다는 증거.** 프레임을 여러 장 만든 뒤이므로,
// 여기 mp4가 있다면 프리뷰 명령이 인코더까지 들고 갔다는 뜻이다 (#27 완료 조건).
const leaked = fs.readdirSync(RUN).filter((name) => name.endsWith('.mp4'))
record('프리뷰가 최종 렌더 산출물을 만들지 않는다', leaked.length === 0, leaked.join(', '))

// --- 4. 문제 편집 (#28) -------------------------------------------------------------

// **파일이 둘이므로 두 파일을 함께 본다.** 확인 기록은 project.json에, 확인한 대상은
// 콘텐츠에 있어 한쪽만 확인하면 계약이 지켜졌는지 알 수 없다.
const quizBefore = JSON.parse(fs.readFileSync(path.join(RUN, 'quiz.json'), 'utf8'))

const questionsOut = path.join(WORK, 'questions.json')
const questions = await wait(electron('questions', {
  SHORTS_SMOKE_RUN: RUN,
  SHORTS_SMOKE_OUT: questionsOut,
  SHORTS_SMOKE_SHOT: path.join(APP_DIR, 'smoke', 'screenshot-questions.png'),
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
}), 180000)

const questionsResult = fs.existsSync(questionsOut) ? JSON.parse(fs.readFileSync(questionsOut, 'utf8')) : null
results.phases.push({ scenario: 'questions', ...questions, result: questionsResult })
if (questionsResult) results.checks.push(...questionsResult.checks)
record('문제 편집 시나리오가 끝난다', questionsResult && questionsResult.ok && !questions.timedOut)

const quizAfter = JSON.parse(fs.readFileSync(path.join(RUN, 'quiz.json'), 'utf8'))
const projectAfter = JSON.parse(fs.readFileSync(path.join(RUN, 'project.json'), 'utf8'))

record('고친 정답이 quiz.json까지 간다',
  quizAfter.questions.some((question) => question.answer === MARKERS.answer),
  quizAfter.questions.map((question) => question.answer).join(' / '))
record('순서 변경이 quiz.json까지 간다',
  quizAfter.questions.map((question) => question.id).join(',') !== quizBefore.questions.map((question) => question.id).join(','),
  quizAfter.questions.map((question) => question.id).join(','))

// **이것이 이 이슈의 계약이다** (D2 확정 스펙 1.4). 사람 확인이 검증기 소유 필드를 덮으면
// 다음 실행의 임계값 판정이 조용히 통과한다.
// **남아 있는 문제끼리 번호로 짝지어 본다.** 시나리오가 지운 문제도 있으므로 배열을
// 통째로 비교하면 이 계약이 아니라 개수가 걸린다.
const verifyOf = (quiz) => new Map(quiz.questions.map((q) => [q.id, JSON.stringify(q.verify ?? null)]))
const before = verifyOf(quizBefore)
const after = verifyOf(quizAfter)
const touched = [...after].filter(([id, entry]) => before.has(id) && before.get(id) !== entry)
record('사람 확인이 verify.status·confidence를 건드리지 않는다',
  touched.length === 0, JSON.stringify(touched))
record('확인 기록이 project.json의 review로 간다',
  Array.isArray(projectAfter.review.acknowledged) && projectAfter.review.stale.includes(1),
  JSON.stringify(projectAfter.review))

// --- 5. 재시작하고 문제 편집 확인 ---------------------------------------------------

const qVerifyOut = path.join(WORK, 'questions-verify.json')
const qVerify = await wait(electron('questions-verify', {
  SHORTS_SMOKE_RUN: RUN,
  SHORTS_SMOKE_OUT: qVerifyOut,
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
}), 120000)

const qVerifyResult = fs.existsSync(qVerifyOut) ? JSON.parse(fs.readFileSync(qVerifyOut, 'utf8')) : null
results.phases.push({ scenario: 'questions-verify', ...qVerify, result: qVerifyResult })
if (qVerifyResult) results.checks.push(...qVerifyResult.checks)
record('문제 편집 재시작 시나리오가 끝난다', qVerifyResult && qVerifyResult.ok && !qVerify.timedOut)

// --- 6. 강제 종료 뒤 백엔드가 남는가 -------------------------------------------------

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

// --- 7. 번들이 바깥을 가리키지 않는가 -------------------------------------------------

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

// --- 8. 프리셋 이름이 앱 코드에 없는가 (#79) -----------------------------------------

// **목록의 출처가 하나여야 한다.** 프리셋은 `assets/`가 소유하고(D1 확정 스펙 6장) 앱은
// 백엔드를 지나서만 그것을 안다 — 동결 배포에서 `assets/`는 백엔드 실행 파일 옆이라 앱에서
// 본 경로와 다르다. 이름을 앱에 적으면 프리셋을 하나 더할 때 **화면의 목록과 렌더가 아는
// 목록이 갈리고**, 그것은 화면만 보고는 드러나지 않는다.
const presetNames = ['caption-styles', 'backgrounds'].flatMap((dir) => Object.keys(
  JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'assets', dir, 'presets.json'), 'utf8')).presets
))

const sources = []
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).forEach((entry) => {
  const full = path.join(dir, entry.name)
  if (entry.isDirectory()) walk(full)
  else if (/\.(ts|tsx|js|mjs|css|html)$/.test(entry.name)) sources.push(full)
})
walk(path.join(APP_DIR, 'src'))
walk(path.join(APP_DIR, 'electron'))
walk(path.join(APP_DIR, 'smoke'))

const hits = sources
  .map((file) => ({ file, found: presetNames.filter((name) => fs.readFileSync(file, 'utf8').includes(name)) }))
  .filter((entry) => entry.found.length > 0)
  .map((entry) => `${path.relative(APP_DIR, entry.file)}: ${entry.found.join(' ')}`)
record('앱 코드에 프리셋 이름이 없다', hits.length === 0, hits.join(' / '))

// --- 결과 -----------------------------------------------------------------------

results.ok = results.checks.every((check) => check.ok)
results.work_dir = WORK
results.electron = process.versions.electron ?? null
fs.writeFileSync(RESULTS, JSON.stringify(results, null, 2) + '\n')

const failed = results.checks.filter((check) => !check.ok)
console.log(`\n${results.checks.length - failed.length}/${results.checks.length} 통과 — ${RESULTS}`)
if (failed.length) console.log(failed.map((check) => `  FAIL ${check.name}`).join('\n'))
process.exit(results.ok ? 0 : 1)
