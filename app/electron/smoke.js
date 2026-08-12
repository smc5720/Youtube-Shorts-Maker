// 스모크 시나리오 — 사람 없이 #26의 완료 조건을 밟는다.
//
// **UI가 쓰는 경로를 그대로 부른다.** 렌더러에 붙은 `window.__smoke`는 버튼이 부르는 것과
// 같은 `open` / `edit` / `save`이고, 확인 대화상자만 바꿔 끼운다(모달이 뜨면 자동 실행이
// 거기서 멈춘다). 그래서 이 파일이 확인하는 것은 스모크용 코드가 아니라 제품 동작이다.
//
// 시나리오는 `--smoke=<이름>`으로 고르고 나머지는 환경 변수로 받는다. 묶어서 도는 것은
// `app/smoke/run.mjs`이고, **재시작 왕복이 필요해서 프로세스가 나뉜다** — 한 프로세스 안에서
// 다시 여는 것은 "앱을 재시작하고 다시 열었다"의 증거가 되지 못한다.

const fs = require('node:fs')
const path = require('node:path')

const RUN = process.env.SHORTS_SMOKE_RUN
const BROKEN = process.env.SHORTS_SMOKE_BROKEN
const OUT = process.env.SHORTS_SMOKE_OUT
const EXPECT = process.env.SHORTS_SMOKE_EXPECT
const READY = process.env.SHORTS_SMOKE_READY
const SHOT = process.env.SHORTS_SMOKE_SHOT

/** 편집이 파일까지 갔는지 보는 표식. 한글이라 인코딩 회귀도 함께 걸린다. */
const MARKERS = {
  save: '스모크 · 저장 버튼',
  close: '스모크 · 닫기 저장'
}

const TIMEOUT_MS = 120000

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function until (predicate, attempts = 50, gap = 100) {
  for (let index = 0; index < attempts; index += 1) {
    try {
      if (await predicate()) return true
    } catch {
      // 페이지가 아직 준비되지 않았을 뿐이다. 다음 시도에서 다시 본다.
    }
    await delay(gap)
  }
  return false
}

async function runSmoke ({ scenario, window, log, app, backendInfo, externalRequests, setAsk }) {
  const checks = []
  const record = (name, ok, detail) => {
    checks.push({ name, ok: Boolean(ok), detail: detail === undefined ? null : String(detail) })
    log('[smoke]', ok ? 'OK  ' : 'FAIL', name, detail === undefined ? '' : String(detail))
  }
  const evaluate = (code) => window.webContents.executeJavaScript(code, true)
  const quote = (value) => JSON.stringify(value)
  const text = (selector) => evaluate(
    `(() => { const node = document.querySelector(${quote(selector)}); return node && node.textContent })()`
  )
  const attribute = (selector, name) => evaluate(
    `(() => { const node = document.querySelector(${quote(selector)}); return node && node.getAttribute(${quote(name)}) })()`
  )
  const saveState = () => attribute('[data-testid="save-state"]', 'data-state')

  let finished = false
  const finish = (code, { exit = true } = {}) => {
    if (finished) return
    finished = true
    // **측정치를 먼저 쓴다.** 마지막 동작(창 닫기)이 막히면 결과까지 함께 잃는다 —
    // 스파이크 #25에서 실제로 한 번 그렇게 잃었다 (7장).
    if (OUT) {
      const failed = checks.filter((check) => !check.ok)
      fs.writeFileSync(OUT, JSON.stringify({
        scenario,
        ok: failed.length === 0,
        markers: MARKERS,
        backend: backendInfo(),
        checks
      }, null, 2) + '\n')
    }
    if (exit) setTimeout(() => app.exit(code), 200)
  }

  const guard = setTimeout(() => {
    record('시간 안에 끝난다', false, `${TIMEOUT_MS}ms 초과`)
    finish(3)
  }, TIMEOUT_MS)

  try {
    const ready = await until(() => evaluate('Boolean(window.__smoke)'))
    record('렌더러가 준비된다', ready)
    if (!ready) return finish(1)

    const network = () => record('바깥으로 나가는 요청이 없다', externalRequests().length === 0, externalRequests().join(', '))

    if (scenario === 'idle') return await idle({ backendInfo, record, finish })
    if (scenario === 'verify') return await verify({ evaluate, quote, record, finish, network })
    await roundTrip({ window, evaluate, quote, text, attribute, saveState, record, finish, setAsk, network })
  } catch (failure) {
    record('예외 없이 끝난다', false, failure && failure.message)
    finish(1)
  } finally {
    clearTimeout(guard)
  }
}

// --- 시나리오 -------------------------------------------------------------------

/** 열기 → 오류 → 편집 → 닫기 확인 → 저장 → 저장하며 닫기. */
async function roundTrip (host) {
  const { window, evaluate, quote, text, attribute, saveState, record, finish, setAsk, network } = host

  // 1. 연다 — 헤더에 경로가 뜨고 한글이 온전하다
  record('run 디렉터리를 연다', await evaluate(`window.__smoke.open(${quote(RUN)})`) === true)
  const shown = await text('[data-testid="project-path"]')
  record('헤더에 프로젝트 경로가 뜬다', Boolean(shown) && shown.endsWith('project.json'), shown)

  const onDisk = readProject(RUN)
  const body = await evaluate('document.body.innerText')
  record(
    '한글이 화면까지 온전히 온다',
    body.includes(onDisk.render.cta_punch) && onDisk.render.cta_punch.includes('구독'),
    onDisk.render.cta_punch
  )
  record('처음에는 저장된 상태다', await saveState() === 'saved')
  await capture(window, record)

  // 2. 계약을 어긴 파일은 원인을 말하고, 앱은 살아 있다
  record('계약을 어긴 project.json은 열리지 않는다', await evaluate(`window.__smoke.open(${quote(BROKEN)})`) === false)
  const notice = await text('[data-testid="notice-danger"]')
  record('원인이 필드 경로와 함께 뜬다', Boolean(notice) && notice.includes('render.fps'), notice)
  record('앱이 죽지 않는다', await evaluate('Boolean(window.__smoke)') === true)
  const kept = await text('[data-testid="project-path"]')
  record('열려 있던 프로젝트는 그대로다', Boolean(kept) && kept.includes('run-smoke'), kept)

  // 3. 고치면 헤더가 그것을 말한다
  await evaluate(`window.__smoke.open(${quote(RUN)})`)
  await evaluate(`window.__smoke.edit('render','cta_tail',${quote(MARKERS.save)})`)
  record('저장하지 않은 변경이 헤더에 뜬다', await until(async () => await saveState() === 'unsaved'))

  // 4. 그 상태로 닫으려 하면 확인을 받는다
  let asked = null
  setAsk(async (options) => { asked = options; return { response: 2 } })
  window.close()
  await delay(500)
  record('닫기 전에 확인을 받는다', asked !== null && asked.buttons.length === 3, asked && asked.buttons.join(' / '))
  record('취소하면 창이 닫히지 않는다', !window.isDestroyed())

  // 5. 저장하면 파일이 바뀌고 임시 파일이 남지 않는다
  record('저장이 성공한다', await evaluate('window.__smoke.save()') === true)
  record('저장 뒤 상태가 저장됨으로 돌아온다', await until(async () => await saveState() === 'saved'))
  record('편집이 파일에 반영된다', readProject(RUN).render.cta_tail === MARKERS.save)
  const leftovers = fs.readdirSync(RUN).filter((name) => name.includes('.tmp-'))
  record('임시 파일이 남지 않는다', leftovers.length === 0, leftovers.join(', '))
  record('unsaved 표시가 attribute로 확인된다', await attribute('[data-testid="save-state"]', 'data-state') === 'saved')

  // 6. 다시 고친 뒤 "저장하고 닫기" — 결과 확인은 재시작 쪽(verify)이 한다
  await evaluate(`window.__smoke.edit('render','cta_tail',${quote(MARKERS.close)})`)
  await until(async () => await saveState() === 'unsaved')
  network()
  setAsk(async () => ({ response: 0 }))
  finish(0, { exit: false })
  window.close()
}

/** 앱을 다시 띄운 쪽. 저장한 것이 그대로 열리는지만 본다. */
async function verify ({ evaluate, quote, record, finish, network }) {
  record('재시작한 앱이 같은 프로젝트를 연다', await evaluate(`window.__smoke.open(${quote(RUN)})`) === true)
  const state = await evaluate('window.__smoke.state()')
  record('저장한 편집이 유지된다', state.project && state.project.render.cta_tail === EXPECT, state.project && state.project.render.cta_tail)
  record('다시 연 직후에는 변경이 없다', state.unsaved === false)
  network()
  finish(0)
}

/** 아무 일도 하지 않고 떠 있는다. 강제 종료 뒤 백엔드가 남는지 보는 쪽이 쓴다. */
async function idle ({ backendInfo, record, finish }) {
  const ready = await until(async () => Boolean(backendInfo().pid))
  record('백엔드가 붙는다', ready)
  fs.writeFileSync(READY, JSON.stringify({
    electron: process.pid,
    backend: backendInfo().pid
  }) + '\n')
  finish(0, { exit: false })
}

// 화면을 남긴다. **측정치를 먼저 쓰는 것과 같은 이유로 실패해도 시나리오를 멈추지 않는다.**
async function capture (window, record) {
  if (!SHOT) return
  try {
    fs.writeFileSync(SHOT, (await window.webContents.capturePage()).toPNG())
    record('화면을 캡처한다', true, SHOT)
  } catch (failure) {
    record('화면을 캡처한다', false, failure && failure.message)
  }
}

function readProject (runDir) {
  return JSON.parse(fs.readFileSync(path.join(runDir, 'project.json'), 'utf8'))
}

module.exports = { runSmoke, MARKERS }
