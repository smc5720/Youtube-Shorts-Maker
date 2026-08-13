// 스모크 시나리오 — 사람 없이 #26 · #27 · #28 · #82 · #79 · #80의 완료 조건을 밟는다.
//
// **UI가 쓰는 경로를 그대로 부른다.** 렌더러에 붙은 `window.__smoke`는 버튼이 부르는 것과
// 같은 `open` / `edit` / `save`이고, **대화상자만 바꿔 끼운다**(모달이 뜨면 자동 실행이
// 거기서 멈춘다) — 확인 대화상자(`setAsk`)와 파일 선택(`setPick`) 둘이다. 그래서 이 파일이
// 확인하는 것은 스모크용 코드가 아니라 제품 동작이다.
//
// 시나리오는 `--smoke=<이름>`으로 고르고 나머지는 환경 변수로 받는다. 묶어서 도는 것은
// `app/smoke/run.mjs`이고, **재시작 왕복이 필요해서 프로세스가 나뉜다** — 한 프로세스 안에서
// 다시 여는 것은 "앱을 재시작하고 다시 열었다"의 증거가 되지 못한다.

const fs = require('node:fs')
const path = require('node:path')

const RUN = process.env.SHORTS_SMOKE_RUN
const LONG = process.env.SHORTS_SMOKE_LONG
const BROKEN = process.env.SHORTS_SMOKE_BROKEN
const OUT = process.env.SHORTS_SMOKE_OUT
const EXPECT = process.env.SHORTS_SMOKE_EXPECT
const READY = process.env.SHORTS_SMOKE_READY
const SHOT = process.env.SHORTS_SMOKE_SHOT
// 배경 파일을 고른 뒤의 화면 (#80). 프리셋 상태와 다른 파일이라 둘을 나란히 볼 수 있다.
const SHOT_BG = process.env.SHORTS_SMOKE_SHOT_BG
// 배경 사용자 파일 셋 (#80) — 받는 것 / 받지 않는 것 / 고른 뒤 사라진 것.
const BG = process.env.SHORTS_SMOKE_BG
const BG_BAD = process.env.SHORTS_SMOKE_BG_BAD
const BG_MISSING = process.env.SHORTS_SMOKE_BG_MISSING

/** 편집이 파일까지 갔는지 보는 표식. 한글이라 인코딩 회귀도 함께 걸린다. */
const MARKERS = {
  save: '스모크 · 저장 버튼',
  close: '스모크 · 닫기 저장',
  /** 문제 편집 쪽 표식 (#28). 낭독 문구라 확인 기록이 풀리고 재생성 대상이 된다. */
  answer: '스모크 · 고친 정답'
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

async function runSmoke ({
  scenario, window, log, app, backendInfo, externalRequests, setAsk, setPick
}) {
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
    if (scenario === 'preview') {
      return await preview({ window, evaluate, quote, attribute, text, record, finish, network, setPick })
    }
    if (scenario === 'questions') {
      return await questions({ window, evaluate, quote, attribute, text, saveState, record, finish, network })
    }
    if (scenario === 'questions-verify') {
      return await questionsVerify({ evaluate, quote, record, finish, network })
    }
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
  // **`open`이 끝난 것과 화면이 그것을 반영한 것은 다르다.** `window.__smoke`는 렌더마다
  // 다시 달리므로, 커밋 전에 읽으면 직전 렌더의 값이 온다.
  const settled = await until(async () => Boolean((await evaluate('window.__smoke.state()')).project))
  const state = await evaluate('window.__smoke.state()')
  record('저장한 편집이 유지된다', settled && state.project.render.cta_tail === EXPECT, state.project && state.project.render.cta_tail)
  record('다시 연 직후에는 변경이 없다', state.unsaved === false)
  network()
  finish(0)
}

/**
 * 장면 목록 · 3분할 · 프리뷰 (#27) · 장면 길이(#82) · 프리셋(#79) · 배경 파일(#80).
 *
 * **FFmpeg가 있어야 한다.** 이 시나리오가 확인하는 것의 절반이 실제 프레임이라, 명령만
 * 맞는지 보는 대역으로 바꾸면 확인하려는 것이 확인되지 않는다. 명령이 최종 렌더 경로를 지나지
 * 않는다는 것은 `tests/test_video_renderer.py`가 FFmpeg 없이도 지킨다.
 */
async function preview (host) {
  const { window, evaluate, quote, attribute, text, record, finish, network, setPick } = host

  const rect = (selector) => evaluate(
    `(() => { const n = document.querySelector(${quote(selector)}); if (!n) return null;
       const r = n.getBoundingClientRect(); return { w: r.width, h: r.height } })()`
  )
  const previewState = () => attribute('[data-testid="preview"]', 'data-state')
  const ready = () => until(async () => await previewState() === 'ready', 120, 100)

  record('장면이 있는 run을 연다', await evaluate(`window.__smoke.open(${quote(RUN)})`) === true)
  // 실패하면 화면 상태를 그대로 남긴다 — `data-state`만으로는 기다린 것이 무엇이었는지
  // (프레임이 안 왔는지, 실패했는지) 구분되지 않는다.
  const firstFrame = await ready()
  record('첫 프레임이 나온다', firstFrame, JSON.stringify(await evaluate(
    '(() => { const s = window.__smoke.state();'
    + ' return { selected: s.selected, pending: s.pending, frame: s.frame, error: s.previewError } })()'
  )))

  // 1. 3분할 폭 — 두 창 크기에서 확정 수치가 그대로 나오는지 (확정 스펙 3.1)
  for (const [width, height, left, right] of [[1440, 900, 296, 340], [1280, 720, 264, 300]]) {
    window.setContentSize(width, height)
    await delay(300)
    const scenes = await rect('[data-testid="scene-list"]')
    const properties = await rect('[data-testid="properties"]')
    const near = (value, want) => Math.abs(value - want) <= 1
    record(
      `${width}x${height}에서 좌 ${left} / 우 ${right}`,
      near(scenes.w, left) && near(properties.w, right),
      `${scenes.w.toFixed(1)} / ${properties.w.toFixed(1)}`
    )

    // 2. 9:16을 유지하고 **잘리지 않는다** — 폭이 아니라 높이에 맞춘다 (확정 스펙 3.1)
    const stage = await rect('.preview__stage')
    const frame = await rect('.preview__frame')
    record(
      `${width}x${height}에서 프리뷰가 9:16이다`,
      Math.abs(frame.w / frame.h - 9 / 16) < 0.01,
      `${frame.w.toFixed(1)}x${frame.h.toFixed(1)} = ${(frame.w / frame.h).toFixed(4)}`
    )
    record(
      `${width}x${height}에서 프리뷰가 잘리지 않는다`,
      frame.h <= stage.h + 1 && frame.w <= stage.w + 1,
      `프레임 ${frame.h.toFixed(1)} ≤ 무대 ${stage.h.toFixed(1)}`
    )
  }
  window.setContentSize(1440, 900)
  await delay(300)

  // 3. 역할 구분과 문제 그룹 (퀴즈 스펙 8장, 확정 스펙 3.1)
  const roles = await evaluate(
    '[...document.querySelectorAll(\'[data-testid="scene-row"]\')].map((n) => n.dataset.role)'
  )
  const wanted = ['hook', 'question', 'countdown', 'answer', 'cta']
  record('다섯 역할이 모두 구분되어 보인다', wanted.every((role) => roles.includes(role)), roles.join(','))

  const groups = await evaluate(
    `[...document.querySelectorAll('.scene-group')].map((n) => ({
       head: n.querySelector('[data-testid="question-head-number"]').textContent.trim(),
       roles: [...n.querySelectorAll('[data-testid="scene-row"]')].map((r) => r.dataset.role)
     }))`
  )
  record(
    '문제마다 세 장면이 머리글 아래 한 그룹으로 묶인다',
    groups.length === 3 && groups.every((group) =>
      group.roles.join(',') === 'question,countdown,answer' && /^문제 \d+$/.test(group.head)),
    JSON.stringify(groups)
  )

  // 4. 선택하면 그 장면의 프레임으로 바뀐다
  const shown = () => evaluate('window.__smoke.state().frame')
  const first = await shown()
  await evaluate('window.__smoke.select(3)')
  record('다른 장면을 고르면 프리뷰가 그 장면으로 간다', await until(async () => {
    const frame = await shown()
    return frame && frame.index === 3
  }))
  const answer = await shown()
  record('장면마다 다른 프레임이다', first && answer && first.bytes !== answer.bytes,
    `${first && first.bytes} → ${answer.bytes}`)
  record('속성 패널이 고른 장면을 따라간다', (await text('[data-testid="properties-scene"]')).includes('answer'))

  // 5. **대기 표현 2종.** 값을 고치면 캐시가 통째로 비므로 그때만 2초짜리 대기가 생기고,
  //    두 표현이 화면에 오래 서 있어 관찰할 수 있다. 장면 이동만으로는 캐시가 답해서
  //    (3~5ms) 어느 쪽이었는지 화면에서 구분되지 않는다 — 그것이 이 설계가 산 것이다
  //    (`docs/spikes/27-preview-frames.md` 3장).
  const before = await shown()

  // **프리셋 이름을 여기 적지 않는다** (#79). 목록은 백엔드가 `assets/`에서 읽어 보내므로,
  // 이름을 스모크에 박으면 프리셋이 바뀔 때 이 파일도 고쳐야 한다 — 그리고 확인하려는 것
  // (출처가 하나)이 확인되지 않는다.
  const presets = await evaluate('window.__smoke.state().presets')
  record('프리셋 목록이 백엔드에서 온다',
    presets !== null && presets.caption_styles.length === 3 && presets.backgrounds.length === 3,
    JSON.stringify(presets && {
      styles: presets.caption_styles.map((style) => style.name),
      backgrounds: presets.backgrounds.map((preset) => preset.name)
    }))

  const styleOf = () => evaluate('window.__smoke.state().project.render.caption_style')
  const backgroundOf = () => evaluate('window.__smoke.state().project.background')
  const opening = await styleOf()
  const otherStyle = presets.caption_styles.find((style) => style.name !== opening)

  // (a) 같은 장면의 값 변경 → **이전 프레임을 유지한다.** 지우면 값 하나 고칠 때마다
  //     화면이 비어 무엇이 어떻게 바뀌는지 비교할 수 없다.
  await evaluate(`window.__smoke.edit('render','caption_style',${quote(otherStyle.name)})`)
  const stale = await until(async () => await previewState() === 'stale')
  const kept = await shown()
  record(
    '같은 장면의 값이 바뀌면 이전 프레임을 유지한 채 갱신 표시가 뜬다',
    stale && kept !== null && kept.index === 3 && kept.bytes === before.bytes,
    JSON.stringify(kept)
  )
  record('갱신 배지가 떠 있다', Boolean(await text('[data-testid="preview-refresh"]')))

  // (b) 그 대기 중에 다른 장면으로 옮긴다 → **이전 프레임을 지운다.** 남겨 두면 방금 고른
  //     장면의 그림으로 읽힌다.
  await evaluate('window.__smoke.select(1)')
  record(
    '다른 장면을 고르면 이전 프레임이 지워진다',
    await until(async () => await previewState() === 'loading' && await shown() === null)
  )
  record('그 뒤 새 장면의 프레임이 온다', await until(async () => {
    const frame = await shown()
    return frame !== null && frame.index === 1
  }, 120, 100))

  // **프레임 인덱스로 기다린다.** `ready`만 보면 직전 장면의 프레임이 서 있는 화면에서
  // 곧바로 통과한다 — 상태는 이미 `ready`이기 때문이다.
  await evaluate('window.__smoke.select(3)')
  await until(async () => {
    const frame = await shown()
    return frame !== null && frame.index === 3
  }, 120, 100)
  const changed = await shown()
  record('갱신된 프레임이 바뀐 값을 반영한다',
    changed !== null && changed.index === 3 && changed.bytes !== before.bytes,
    `${before.bytes} → ${changed && changed.bytes}`)

  // 6. 총 길이 — 짧은 쪽은 정보, 넘치는 쪽만 경고다 (확정 스펙 1.8)
  record('목표보다 짧아도 경고가 아니다',
    await attribute('[data-testid="total-duration"]', 'data-state') === 'short',
    await text('[data-testid="total-value"]'))

  await capture(window, record)

  // 7. **장면 길이 조정** (#82). 값이 `scenes.json`이 아니라 `project.json`에 쌓인다 —
  //    낭독보다 짧은 값은 확정 검증이 거부하므로 그쪽에 쓰면 저장이 실패한다 (PRD 14.1).
  const totalBefore = await text('[data-testid="total-value"]')

  // (a) `countdown`은 고칠 수 없다 — `duration`이 `seconds`와 같아야 하고 그 값은 콘텐츠
  //     필드다 (확정 스펙 7.1). 값은 보이지만 컨트롤이 없다.
  await evaluate('window.__smoke.select(2)')
  await until(async () => (await text('[data-testid="properties-scene"]')).includes('countdown'))
  record('카운트다운에는 길이 입력이 없다',
    await evaluate('document.querySelector(\'[data-testid="input-길이"]\') === null'))
  record('카운트다운도 길이는 보인다',
    (await text('[data-testid="properties-scene"]')).includes('초'))

  // (b) 낭독이 있는 장면 — 낭독보다 짧게 줄이면 **경고하고 값은 받는다** (확정 스펙 4장)
  await evaluate('window.__smoke.select(3)')
  await until(async () => (await text('[data-testid="properties-scene"]')).includes('answer'))
  const narrated = await evaluate('window.__smoke.state().scenes.scenes[3]')
  record('낭독 길이가 표시된다',
    typeof narrated.audio_duration === 'number'
    && (await text('[data-testid="properties-scene"]')).includes('낭독'),
    JSON.stringify(narrated.audio_duration))

  const short = Math.max(0.4, Number((narrated.audio_duration - 1).toFixed(1)))
  await evaluate(`window.__smoke.editDuration(3, ${short})`)
  const warned = await until(async () =>
    Boolean(await text('[data-testid="notice-duration-warn"]')))
  const edited = await evaluate('window.__smoke.state()')
  record('낭독보다 짧으면 경고가 뜬다', warned, await text('[data-testid="notice-duration-warn"]'))
  record('그래도 값은 적용된다', edited.shownDurations[3] === short,
    `${edited.shownDurations[3]} (요청 ${short})`)
  record('scenes.json의 duration은 그대로다',
    edited.scenes.scenes[3].duration === narrated.duration
    && narrated.duration !== short,
    `${edited.scenes.scenes[3].duration} vs ${short}`)
  record('값은 project.json의 render.scene_overrides에 쌓인다',
    edited.project.render.scene_overrides.some((item) =>
      item.role === 'answer' && item.question_id === narrated.question_id
      && item.duration === short),
    JSON.stringify(edited.project.render.scene_overrides))
  // **목록이 아니라 참·거짓이다** — 길이 하나를 고치면 그 뒤 장면의 시작 시각이 전부 밀려
  // 낡는 대상이 타임라인 전체다 (PRD 14.1).
  record('타임라인이 낡았다는 표시가 걸린다', edited.project.review.timeline_stale === true,
    JSON.stringify(edited.project.review))
  record('총 길이가 그 값을 따라간다',
    (await text('[data-testid="total-value"]')) !== totalBefore,
    `${totalBefore} → ${await text('[data-testid="total-value"]')}`)

  // (c) 프리뷰가 다시 만들어진다 — 장면의 대표 프레임은 그 장면의 한가운데다
  const shortened = await until(async () => {
    const frame = await shown()
    return frame !== null && frame.index === 3 && frame.bytes !== changed.bytes
  }, 120, 100)
  record('고친 길이가 프리뷰 프레임까지 간다', shortened,
    `${changed && changed.bytes} → ${JSON.stringify(await shown())}`)

  // (d) 파일까지 간다. 재시작 뒤 확인은 `questions-verify`가 한다.
  record('길이 조정을 저장한다', await evaluate('window.__smoke.save()') === true)

  // 8. **자막 스타일과 배경 프리셋 교체** (#79). 목록은 5(a)에서 이미 받았다 — 프리셋 이름을
  //    이 파일에 적지 않는 이유가 거기 있다.
  // **화면의 목록이 백엔드 목록과 같은지**를 본다 — 개수만 세면 셋 중 둘만 그려도 통과한다.
  const drawn = (testid) => evaluate(
    `[...document.querySelectorAll('[data-testid=${quote(`presets-${testid}`)}] .preset')].map((n) => n.dataset.value)`
  )
  record('화면의 스타일 목록이 백엔드 목록과 같다',
    (await drawn('caption-style')).join(',') === presets.caption_styles.map((s) => s.name).join(','),
    (await drawn('caption-style')).join(','))
  record('화면의 배경 목록이 백엔드 목록과 같다',
    (await drawn('background')).join(',') === presets.backgrounds.map((p) => p.name).join(','),
    (await drawn('background')).join(','))

  // 지금 고른 것이 아닌 스타일 — 5(a)가 `otherStyle`을 이미 골라 뒀으므로 값이 바뀌는 쪽이다.
  const current = await styleOf()
  const other = presets.caption_styles.find((style) => style.name !== current)
  const frameBefore = await shown()

  await evaluate(`document.querySelector('[data-testid="presets-caption-style"] .preset[data-value=${quote(other.name)}]').click()`)
  const picked = await until(async () => await styleOf() === other.name)
  record('자막 스타일을 눌러서 고른다', picked, `${current} → ${await styleOf()}`)
  record('고른 카드만 선택 표시가 붙는다',
    (await evaluate(
      `[...document.querySelectorAll('[data-testid="presets-caption-style"] .preset')]
         .filter((n) => n.dataset.selected === "true").map((n) => n.dataset.value)`
    )).join(',') === other.name)

  // **기본 짝은 `presets.json`이 정한다** — 시안이 적은 짝은 민트와 오렌지가 서로 바뀐
  // 값이라, 그것을 옮기면 사용자가 스타일 선택만으로 △ 조합에 들어간다 (확정 스펙 1.1).
  record('스타일을 고르면 배경이 기본 짝으로 함께 바뀐다',
    await until(async () => (await backgroundOf()).value === other.background),
    JSON.stringify(await backgroundOf()))

  // 기본 짝이 **아닌** 조합도 고를 수 있다 — 매트릭스에 차단 대상이 없다 (D1 확정 스펙 6.3).
  const off = presets.backgrounds.find((preset) => preset.name !== other.background)
  await evaluate(`document.querySelector('[data-testid="presets-background"] .preset[data-value=${quote(off.name)}]').click()`)
  const combined = await until(async () => (await backgroundOf()).value === off.name)
  record('기본 짝이 아닌 조합도 고를 수 있다', combined,
    `${other.name} × ${JSON.stringify(await backgroundOf())}`)
  record('배경만 바꿔도 자막 스타일은 그대로다', await styleOf() === other.name)
  record('배경 kind는 preset으로 남는다', (await backgroundOf()).kind === 'preset')

  // 프리뷰까지 간다 — 두 값 모두 렌더러가 읽으므로 지문이 바뀌고 프레임이 다시 만들어진다.
  const restyled = await until(async () => {
    const frame = await shown()
    return frame !== null && frameBefore !== null && frame.index === 3
      && frame.bytes !== frameBefore.bytes
  }, 120, 100)
  record('고른 스타일·배경이 프리뷰 프레임까지 간다', restyled,
    `${frameBefore && frameBefore.bytes} → ${JSON.stringify(await shown())}`)

  record('프리셋 교체를 저장한다', await evaluate('window.__smoke.save()') === true)

  // 9. **배경 사용자 파일** (#80). 대화상자만 바꿔 끼운다 — 고른 경로를 무엇으로 판정하는지는
  //    제품 코드가 그대로 지난다 (확인 대화상자를 `setAsk`로 바꾸는 것과 같은 자리다).
  let asked = null
  const pickReturns = (target) => setPick(async (options) => {
    asked = options
    return { canceled: false, filePaths: [target] }
  })
  const pick = async () => {
    await evaluate('document.querySelector(\'[data-testid="pick-background"]\').click()')
  }

  // (a) 받지 않는 형식 — **거부이고 값이 적용되지 않는다**(확정 스펙 4장의 `danger`).
  //     `warn`(낭독보다 짧은 길이)과 같은 표시를 쓰면 무엇이 반영됐는지가 화면에서 갈리지 않는다.
  const bgBefore = JSON.stringify(await backgroundOf())
  pickReturns(BG_BAD)
  await pick()
  const rejected = await until(async () =>
    Boolean(await text('[data-testid="notice-background-reject"]')))
  record('받지 않는 형식은 거부되고 받는 형식을 말한다', rejected,
    await text('[data-testid="notice-background-reject"]'))
  record('거부하면 배경 값은 그대로다', JSON.stringify(await backgroundOf()) === bgBefore, bgBefore)
  // **필터 목록도 백엔드에서 온다.** main이 자기 목록을 들면 화면이 받는 형식과 렌더가 아는
  // 형식이 갈린다 — 확장자를 이 파일에 적지 않는 이유가 그것이다.
  record('대화상자 필터가 백엔드가 준 형식 목록이다',
    asked.filters[0].extensions.join(',')
    === presets.background_files.map((format) => format.extension.slice(1)).join(','),
    JSON.stringify(asked.filters))

  // (b) 받는 형식 — 확장자가 `kind`를 정하고 경로는 있는 자리를 가리킨다 (복사하지 않는다).
  const beforeFile = await shown()
  pickReturns(BG)
  await pick()
  const applied = await until(async () => (await backgroundOf()).value === BG)
  record('고른 파일이 배경이 된다', applied, JSON.stringify(await backgroundOf()))
  record('확장자가 kind를 정한다', (await backgroundOf()).kind === 'image',
    JSON.stringify(await backgroundOf()))
  record('거부 표시가 사라진다', (await text('[data-testid="notice-background-reject"]')) === null)
  record('고른 파일 경로가 화면에 뜬다',
    (await text('[data-testid="background-file"]')).includes('배경.png'),
    await text('[data-testid="background-file"]'))

  const withFile = await until(async () => {
    const frame = await shown()
    return frame !== null && frame.index === 3 && beforeFile !== null
      && frame.bytes !== beforeFile.bytes
  }, 120, 100)
  record('고른 배경이 프리뷰 프레임까지 간다', withFile,
    `${beforeFile && beforeFile.bytes} → ${JSON.stringify(await shown())}`)
  // 프레임이 캔버스를 채우고 비율이 왜곡되지 않는지는 눈으로 봐야 한다 — 그 증거를 남긴다.
  // 속성 패널을 끝까지 내리는 것은 이 컨트롤이 프리셋 목록 **아래**에 있기 때문이다.
  await evaluate(
    `(() => { const n = document.querySelector(${quote('[data-testid="properties"] .panel__scroll')});
       n.scrollTop = n.scrollHeight })()`
  )
  await delay(100)
  await capture(window, record, SHOT_BG)

  // (c) **자막 스타일을 골라도 파일 배경은 그대로다** — 프리셋 이름으로 표현되지 않는 값이라
  //     기본 짝으로 갈아 끼우면 사용자가 넣은 것이 사라진다 (PRD 7.9, #79).
  const styleNow = await styleOf()
  const another = presets.caption_styles.find((style) => style.name !== styleNow)
  await evaluate(`document.querySelector('[data-testid="presets-caption-style"] .preset[data-value=${quote(another.name)}]').click()`)
  await until(async () => await styleOf() === another.name)
  record('스타일을 골라도 사용자 파일 배경은 그대로다',
    (await backgroundOf()).value === BG, JSON.stringify(await backgroundOf()))

  // (d) 고른 뒤 파일이 사라지면 — **원인이 경로와 함께 뜨고 앱은 살아 있다.** 앱이 고르는
  //     순간 존재를 확인하지 않는 이유가 여기 있다: 확인해도 그 뒤에 지워질 수 있다.
  pickReturns(BG_MISSING)
  await pick()
  const missing = await until(async () => {
    const failure = await evaluate('window.__smoke.state().previewError')
    return failure !== null && failure.message.includes('사라진배경.png')
  }, 120, 100)
  record('없는 배경 파일은 프리뷰가 경로와 함께 말한다', missing,
    JSON.stringify(await evaluate('window.__smoke.state().previewError')))
  record('그래도 앱은 살아 있다', await evaluate('Boolean(window.__smoke)') === true)

  // (e) 프리셋으로 돌아온다 — 파일 배경에서 나가는 길이 목록뿐이라, 목록을 숨기면 앱에서
  //     넣은 배경을 앱에서 되돌릴 수 없다.
  const back = presets.backgrounds[0]
  await evaluate(`document.querySelector('[data-testid="presets-background"] .preset[data-value=${quote(back.name)}]').click()`)
  const restored = await until(async () => (await backgroundOf()).kind === 'preset')
  record('프리셋 카드를 누르면 파일 배경에서 돌아온다', restored,
    JSON.stringify(await backgroundOf()))
  record('배경 파일 교체를 저장한다', await evaluate('window.__smoke.save()') === true)

  record('상한을 넘는 run을 연다', await evaluate(`window.__smoke.open(${quote(LONG)})`) === true)
  record('60초를 넘으면 경고로 바뀐다',
    await until(async () => await attribute('[data-testid="total-duration"]', 'data-state') === 'over'),
    await text('[data-testid="total-value"]'))

  // **재시작 뒤 확인은 파일 배경으로 한다** (#80). 이쪽 run에 남기는 이유는 `run-smoke`가
  // 프리셋 조합을 재시작 검증에 쓰고 있기 때문이다 (#79) — 한 run에 둘을 겹치면 어느 쪽도
  // 확인되지 않는다.
  pickReturns(BG)
  await pick()
  const longPicked = await until(async () => (await backgroundOf()).value === BG)
  record('상한 run에도 배경 파일을 고른다', longPicked, JSON.stringify(await backgroundOf()))
  record('그 배경을 저장한다', await evaluate('window.__smoke.save()') === true)

  network()
  finish(0)
}

/**
 * 문제 편집 (#28) — 2분할 · 세 상태 표기 · 확인 기록 · 재생성 표시 · 순서 변경.
 *
 * **FFmpeg를 요구하지 않는다.** 이 화면에는 프리뷰가 없고(확정 스펙 3.2) 콘텐츠 편집은
 * 프레임을 다시 만들지 않는다 — 그것이 `review`를 프리뷰 지문에서 뺀 이유이기도 하다.
 */
async function questions (host) {
  const { window, evaluate, quote, attribute, text, saveState, record, finish, network } = host
  const state = () => evaluate('window.__smoke.state()')
  const rect = (selector) => evaluate(
    `(() => { const n = document.querySelector(${quote(selector)}); if (!n) return null;
       const r = n.getBoundingClientRect(); return { w: r.width, h: r.height } })()`
  )

  record('문제가 있는 run을 연다', await evaluate(`window.__smoke.open(${quote(RUN)})`) === true)
  const loaded = await until(async () => Boolean((await state()).content))
  record('콘텐츠 산출물이 함께 열린다', loaded, JSON.stringify((await state()).items))

  // 1. **장면 목록의 문제 머리글에 배지가 붙는다** — #30의 렌더 전 경고와 같은 표기다.
  const heads = await evaluate(
    `[...document.querySelectorAll('[data-testid="question-head"]')].map((n) => ({
       number: n.querySelector('[data-testid="question-head-number"]').textContent.trim(),
       badge: (n.querySelector('[data-testid="verify-badge"]') || { dataset: {} }).dataset.status || null,
       link: n.tagName
     }))`
  )
  record('문제 머리글이 세 개이고 번호가 붙는다',
    heads.length === 3 && heads.every((head) => /^문제 \d+$/.test(head.number)), JSON.stringify(heads))
  record('verified 문제에는 배지가 없고 나머지 둘에만 붙는다',
    heads.filter((head) => head.badge).length === 2, JSON.stringify(heads.map((head) => head.badge)))
  record('문제 머리글이 눌러서 이동하는 버튼이다', heads.every((head) => head.link === 'BUTTON'))

  // 2. 머리글을 눌러 문제 편집으로 간다 — 화면이 **2분할**이다 (확정 스펙 3.2)
  await evaluate('document.querySelectorAll(\'[data-testid="question-head"]\')[0].click()')
  const opened = await until(async () => (await state()).view === 'questions')
  record('머리글을 누르면 문제 편집으로 간다', opened && (await state()).selectedItem === 1)
  record('프리뷰가 없는 2분할이다',
    (await rect('[data-testid="question-list"]')) !== null && (await rect('.preview__frame')) === null)

  // 3. 세 상태가 색 외에 **문구와 모양으로도** 갈린다 (확정 스펙 4장)
  const badges = await evaluate(
    `[...document.querySelectorAll('[data-testid="question-row"] [data-testid="verify-badge"]')].map((n) => ({
       status: n.dataset.status,
       text: n.textContent.trim(),
       border: getComputedStyle(n).borderStyle,
       glyph: Boolean(n.querySelector('.vbadge__glyph'))
     }))`
  )
  const flagged = badges.find((badge) => badge.status === 'flagged')
  const unverified = badges.find((badge) => badge.status === 'unverified')
  record('flagged와 unverified가 다른 문구를 쓴다',
    flagged && unverified && flagged.text !== unverified.text, JSON.stringify(badges.map((b) => b.text)))
  record('unverified만 점선이고 `?` 기호를 쓴다',
    unverified && unverified.glyph && unverified.border === 'dashed' && flagged.border === 'solid',
    JSON.stringify(badges.map((b) => `${b.status}:${b.border}`)))

  // 4. `unverified`는 `confidence` 자리에 `—`가 온다 — 0인 것과 없는 것은 다르다
  await evaluate('window.__smoke.selectItem(3)')
  await until(async () => (await state()).selectedItem === 3)
  record('unverified의 confidence 자리가 —다',
    (await text('[data-testid="verify-confidence"]')).includes('—'),
    await text('[data-testid="verify-confidence"]'))

  // 5. 확인 처리 — **콘텐츠의 `verify`는 그대로다** (확정 스펙 1.4)
  await evaluate('window.__smoke.selectItem(1)')
  await until(async () => (await state()).selectedItem === 1)
  record('flagged 사유가 무엇을 고칠지 말한다',
    (await text('[data-testid="verify-reason"]')).includes('아마존강'),
    await text('[data-testid="verify-reason"]'))

  const beforeVerify = JSON.stringify((await state()).content.questions[0].verify)
  await evaluate('document.querySelector(\'[data-testid="acknowledge"]\').click()')
  const acknowledged = await until(async () => (await state()).project.review.acknowledged.includes(1))
  record('확인하면 project.json의 review에 번호가 들어간다', acknowledged,
    JSON.stringify((await state()).project.review))
  record('확인해도 quiz.json의 verify는 그대로다',
    JSON.stringify((await state()).content.questions[0].verify) === beforeVerify, beforeVerify)
  record('확인 뒤 저장되지 않은 변경이 뜬다', await until(async () => await saveState() === 'unsaved'))

  // 6. 낭독 문구를 고치면 **확인이 풀리고 재생성 대상이 된다**
  await evaluate(
    `(() => { const c = window.__smoke.state().content;
       const next = { ...c, questions: c.questions.map((q) => (q.id === 1 ? { ...q, answer: ${quote(MARKERS.answer)} } : q)) };
       window.__smoke.editContent(next) })()`
  )
  const released = await until(async () => {
    const review = (await state()).project.review
    return !review.acknowledged.includes(1) && review.stale.includes(1)
  })
  record('낭독 문구를 고치면 확인이 풀리고 stale에 들어간다', released,
    JSON.stringify((await state()).project.review))
  record('재생성 필요 카드가 뜬다', Boolean(await text('[data-testid="stale-card"]')))
  // **`accent` 파랑이다** — 주황이면 `flagged`와 같은 종류로 읽힌다 (확정 스펙 4장).
  const staleColor = await evaluate(
    'getComputedStyle(document.querySelector(\'[data-testid="stale-badge"]\')).color'
  )
  const accent = await evaluate(
    '(() => { const p = document.createElement("i"); p.style.color = "var(--accent)";'
    + ' document.body.append(p); const c = getComputedStyle(p).color; p.remove(); return c })()'
  )
  record('재생성 표시가 accent 파랑이다', staleColor === accent, `${staleColor} / ${accent}`)

  // 7. 카운트다운·난이도는 낭독이 아니다 — 고쳐도 새로 stale이 붙지 않는다
  const before = JSON.stringify((await state()).project.review.stale)
  await evaluate(
    `(() => { const c = window.__smoke.state().content;
       const next = { ...c, questions: c.questions.map((q) => (q.id === 2 ? { ...q, countdown_sec: 5 } : q)) };
       window.__smoke.editContent(next) })()`
  )
  await delay(200)
  record('카운트다운만 고치면 재생성 대상이 늘지 않는다',
    JSON.stringify((await state()).project.review.stale) === before,
    JSON.stringify((await state()).project.review.stale))

  // 8. 순서 변경 — **장면 구성이 낡았다**가 뜬다 (저장하지 않고 두 파일에서 계산한다)
  record('처음에는 장면 구성이 낡지 않았다', (await state()).orderStale === false)
  await evaluate('window.__smoke.moveItem(1, 1)')
  const reordered = await until(async () => (await state()).orderStale === true)
  record('순서를 바꾸면 장면 구성이 낡았다고 뜬다', reordered,
    JSON.stringify((await state()).items.map((item) => item.id)))
  record('그 알림이 화면에 있다', Boolean(await text('[data-testid="notice-order-stale"]')))

  // 9. 추가 / 삭제
  await evaluate('window.__smoke.addItem()')
  const added = await until(async () => (await state()).items.length === 4)
  record('문제를 추가하면 목록이 늘고 재생성 대상이 된다',
    added && (await state()).project.review.stale.includes(4),
    JSON.stringify((await state()).project.review.stale))
  await evaluate('window.__smoke.removeItem(4)')
  record('추가한 문제를 지우면 재생성 대상에서도 빠진다', await until(async () => {
    const now = await state()
    return now.items.length === 3 && !now.project.review.stale.includes(4)
  }), JSON.stringify((await state()).project.review.stale))

  // **장면이 아직 참조하는 번호는 새 문제에 주지 않는다.** 3번은 `scenes.json`에 장면 셋을
  // 갖고 있어서, 지운 자리에 새 문제가 그 번호를 가져가면 옛 장면들이 새 문제의 것으로
  // 읽힌다 — 배지도 재생성 표시도 번호를 따라간다.
  await evaluate('window.__smoke.removeItem(3)')
  await until(async () => (await state()).items.length === 2)
  await evaluate('window.__smoke.addItem()')
  const fresh = await until(async () => (await state()).items.length === 3)
  const ids = (await state()).items.map((item) => item.id)
  record('장면이 참조하는 번호를 새 문제에 주지 않는다',
    fresh && !ids.includes(3) && ids.includes(4), JSON.stringify(ids))

  // 재시작 쪽이 보는 상태를 단순하게 두려고 방금 추가한 것만 되돌린다.
  await evaluate(`window.__smoke.removeItem(${Math.max(...ids)})`)
  record('정리 뒤 문제가 둘이다',
    await until(async () => (await state()).items.length === 2),
    JSON.stringify((await state()).items.map((item) => item.id)))

  await capture(window, record)

  // 10. 저장 — 두 파일이 함께 간다. 결과 확인은 재시작 쪽(questions-verify)이 한다
  record('저장이 성공한다', await evaluate('window.__smoke.save()') === true)
  record('저장 뒤 상태가 저장됨으로 돌아온다', await until(async () => await saveState() === 'saved'))
  const leftovers = fs.readdirSync(RUN).filter((name) => name.includes('.tmp-'))
  record('임시 파일이 남지 않는다', leftovers.length === 0, leftovers.join(', '))

  network()
  finish(0)
}

/**
 * 앱을 다시 띄운 쪽. 문제 편집이 두 파일에 남아 다시 열리는지 본다 (#28).
 *
 * 뒤이어 배경 사용자 파일이 남았는지도 본다 (#80) — **그 파일은 이 앱이 뜨기 전에 지워졌다.**
 * 사라진 배경은 프리뷰가 경로와 함께 말하고, 그 실패는 FFmpeg 없이도 난다 (`_source_file`은
 * 명령을 만들기 전에 걸린다).
 */
async function questionsVerify ({ evaluate, quote, record, finish, network }) {
  record('재시작한 앱이 같은 프로젝트를 연다', await evaluate(`window.__smoke.open(${quote(RUN)})`) === true)
  const settled = await until(async () => Boolean((await evaluate('window.__smoke.state()')).content))
  const state = await evaluate('window.__smoke.state()')

  record('고친 정답이 유지된다', settled && state.content.questions.some(
    (question) => question.answer === MARKERS.answer), JSON.stringify(state.content.questions.map((q) => q.answer)))
  record('재생성 대상 표시가 유지된다', state.project.review.stale.includes(1),
    JSON.stringify(state.project.review))
  record('확인 기록은 풀린 채로 유지된다', !state.project.review.acknowledged.includes(1))
  // 순서를 바꾼 뒤 저장했으므로 장면과 어긋난 상태가 그대로 열려야 한다.
  record('장면 구성이 낡았다는 판단이 재시작 뒤에도 같다', state.orderStale === true,
    JSON.stringify(state.items.map((item) => item.id)))
  // **`preview` 시나리오가 얹은 값이다** (#82). 앱을 세 번 띄운 뒤에도 남아 있어야 한다 —
  // 같은 프로세스에서 다시 여는 것은 "재시작하고 다시 열었다"의 증거가 되지 못한다.
  const overrides = state.project.render.scene_overrides ?? []
  record('조정한 장면 길이가 재시작 뒤에도 유지된다',
    overrides.some((item) => typeof item.duration === 'number'),
    JSON.stringify(overrides))
  record('타임라인이 낡았다는 표시도 유지된다', state.project.review.timeline_stale === true,
    JSON.stringify(state.project.review))

  // **`preview` 시나리오가 고른 프리셋이다** (#79). 그쪽이 기본 짝이 아닌 조합을 남겼으므로,
  // 짝을 다시 맞추는 코드가 어딘가에 있으면 여기서 갈린다.
  const paired = (await evaluate('window.__smoke.state().presets'))
    .caption_styles.find((style) => style.name === state.project.render.caption_style)
  record('고른 자막 스타일과 배경이 재시작 뒤에도 유지된다',
    Boolean(paired) && state.project.background.kind === 'preset'
    && state.project.background.value !== paired.background,
    `${state.project.render.caption_style} × ${JSON.stringify(state.project.background)}`)
  record('다시 연 직후에는 변경이 없다', state.unsaved === false)

  // **배경 사용자 파일** (#80). `preview` 시나리오가 `run-smoke-long`에 고른 파일이고,
  // 그 파일은 이 앱이 뜨기 전에 지워졌다 — 값은 그대로 남아 있고 원인은 프리뷰가 말한다.
  // **다른 run에 남긴 이유**는 `run-smoke`가 프리셋 조합의 재시작 검증을 쓰고 있어서다 (#79).
  record('상한 run도 다시 연다', await evaluate(`window.__smoke.open(${quote(LONG)})`) === true)
  const withFile = await until(async () =>
    (await evaluate('window.__smoke.state().project.background')).value === BG)
  const background = await evaluate('window.__smoke.state().project.background')
  record('고른 배경 파일이 재시작 뒤에도 유지된다',
    withFile && background.kind === 'image' && path.isAbsolute(background.value),
    JSON.stringify(background))
  record('배경 파일은 run 디렉터리 밖을 가리킨다',
    !background.value.startsWith(LONG), background.value)

  // 파일이 사라진 뒤 연 프로젝트 — **원인이 경로와 함께 뜨고 앱은 죽지 않는다.**
  // 프리뷰 캐시가 비어 있는 새 프로세스라 이 호출이 실제로 배경을 열러 간다.
  const named = await until(async () => {
    const failure = await evaluate('window.__smoke.state().previewError')
    return failure !== null && failure.message.includes('배경.png')
  }, 120, 100)
  record('사라진 배경 파일의 경로가 화면에 뜬다', named,
    JSON.stringify(await evaluate('window.__smoke.state().previewError')))
  record('배경이 없어도 앱은 살아 있고 프로젝트는 열려 있다',
    await evaluate('Boolean(window.__smoke.state().project)') === true)

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
async function capture (window, record, target = SHOT) {
  if (!target) return
  try {
    fs.writeFileSync(target, (await window.webContents.capturePage()).toPNG())
    record('화면을 캡처한다', true, target)
  } catch (failure) {
    record('화면을 캡처한다', false, failure && failure.message)
  }
}

function readProject (runDir) {
  return JSON.parse(fs.readFileSync(path.join(runDir, 'project.json'), 'utf8'))
}

module.exports = { runSmoke, MARKERS }
