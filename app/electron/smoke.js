// 스모크 시나리오 — 사람 없이 #26 · #27 · #28 · #82 · #79 · #80 · #81 · #83의 완료 조건을 밟는다.
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
// 자막 문구·오버레이 편집 화면 (#83). 오버레이 카드는 프리셋 목록보다 아래에 있다.
const SHOT_OVERLAY = process.env.SHORTS_SMOKE_SHOT_OVERLAY
// 배경 사용자 파일 셋 (#80) — 받는 것 / 받지 않는 것 / 고른 뒤 사라진 것.
const BG = process.env.SHORTS_SMOKE_BG
const BG_BAD = process.env.SHORTS_SMOKE_BG_BAD
const BG_MISSING = process.env.SHORTS_SMOKE_BG_MISSING

/** 편집이 파일까지 갔는지 보는 표식. 한글이라 인코딩 회귀도 함께 걸린다. */
const MARKERS = {
  save: '스모크 · 저장 버튼',
  close: '스모크 · 닫기 저장',
  /** 문제 편집 쪽 표식 (#28). 낭독 문구라 확인 기록이 풀리고 재생성 대상이 된다. */
  answer: '스모크 · 고친 정답',
  /** 자막 문구 (#83). `%`·`:`가 들어 있어 이스케이프 회귀가 렌더에서 함께 걸린다. */
  caption: '스모크 · 고친 자막 71%',
  /** 오버레이 문구 (#83). */
  overlay: '스모크 · 오버레이',
  /**
   * 해설 (#83). **낭독으로 가지 않는 문구라** 이것만 고치면 자막만 낡는다 — 시안의 두 상태가
   * 갈리는 지점이고, 고치기 전에는 해설이 `narration`에 섞여 있어 음성까지 낡음으로 표시됐다.
   */
  explanation: '스모크 · 고친 해설'
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
 * 장면 목록 · 3분할 · 프리뷰 (#27) · 장면 길이(#82) · 프리셋(#79) · 배경 파일(#80) ·
 * 트랙 볼륨(#81).
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
  // **속성 패널이 340 → 384로 넓어졌다** (#83, 확정 스펙 7.4). 오버레이 편집 카드가 그 폭을
  // 요구하고, 최소 창 값은 336이다 — 좌 264 + 우 336이면 프리뷰에 680px이 남는다.
  for (const [width, height, left, right] of [[1440, 900, 296, 384], [1280, 720, 264, 336]]) {
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

  // 10. **트랙 볼륨** (#81). 슬라이더의 눈금은 화면의 것이고 파일에 사는 값은 **선형 게인**이다
  //     (확정 스펙 5장). 그래서 여기서 확인하는 것은 "눈금이 게인으로 바뀌어 저장되는가"와
  //     "그 값이 프리뷰 프레임을 다시 만들지 않는가" 둘이다.
  const audioOf = () => evaluate('window.__smoke.state().project.audio')
  const slide = (testid, value) => evaluate(
    `(() => { const el = document.querySelector('[data-testid="slider-${testid}"]');
       const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
       setter.call(el, String(${value}));
       el.dispatchEvent(new Event('input', { bubbles: true }));
       return el.value })()`
  )

  record('낭독·효과음 볼륨 슬라이더가 둘 다 있다',
    await evaluate(
      '[...document.querySelectorAll(\'.volume__slider\')].length'
    ) === 2)

  // (a) 눈금 → 게인. **앱이 부르는 것과 같은 경로다** — 손잡이를 직접 움직인다.
  await slide('voice', 40)
  const lowered = await until(async () => (await audioOf()).voice_volume === 0.4)
  record('슬라이더 눈금이 선형 게인으로 저장된다', lowered, JSON.stringify(await audioOf()))
  record('화면의 수치가 눈금으로 표시된다',
    (await text('[data-testid="volume-voice"]')) === '40',
    await text('[data-testid="volume-voice"]'))

  await slide('sfx', 0)
  record('효과음 볼륨도 따로 움직인다',
    await until(async () => (await audioOf()).sfx_volume === 0),
    JSON.stringify(await audioOf()))

  // (b) **프레임을 다시 만들지 않는다** — 프리뷰 명령에는 오디오 체인이 아예 없다 (#27).
  //     먼저 프레임이 실제로 만들어지는 편집을 하나 해서 비교 기준을 세운다. 그 편집 뒤에는
  //     `elapsedMs`가 수이고, 볼륨 편집 뒤에도 수로 남으면 캐시가 비었다는 뜻이다.
  // **기본 짝이 아닌 배경을 고른다.** 재시작 쪽(#79)이 "짝을 다시 맞추는 코드가 없다"를
  // 이 run의 조합으로 확인하므로, 여기서 짝으로 되돌리면 그 확인이 통과할 수 없게 된다.
  const nowBackground = (await backgroundOf()).value
  const pickedStyle = await styleOf()
  const nowStyle = presets.caption_styles.find((style) => style.name === pickedStyle)
  const otherBackground = presets.backgrounds.find(
    (preset) => preset.name !== nowBackground && preset.name !== nowStyle.background
  )
  await evaluate(`document.querySelector('[data-testid="presets-background"] .preset[data-value=${quote(otherBackground.name)}]').click()`)
  // **요청이 끝난 것까지 기다린다** (`pending === null`). 프레임만 보면 이 요청이 아직 도는
  // 중에 아래 볼륨 편집이 나가고, 같은 서명 두 요청이 겹치면 **어느 쪽이 렌더를 하는지가
  // 스레드 순서에 달린다** — 캐시 자물쇠는 둘 중 하나만 돌게 하지만(`_PREVIEW_LOCK`) 화면에
  // 서는 것은 나중 요청이므로, 나중 요청이 렌더한 회차에서 이 확인이 흔들렸다.
  const regenerated = await until(async () => {
    const state = await evaluate('window.__smoke.state()')
    return state.pending === null && state.frame !== null && state.frame.elapsedMs !== null
  }, 120, 100)
  const madeAgain = await shown()
  record('배경을 바꾸면 프레임이 실제로 다시 만들어진다', regenerated, JSON.stringify(madeAgain))

  await slide('voice', 70)
  const cached = await until(async () => {
    const state = await evaluate('window.__smoke.state()')
    return state.pending === null && state.frame !== null && state.frame.elapsedMs === null
  })
  record('볼륨만 고치면 프레임이 캐시에서 온다', cached, JSON.stringify(await shown()))
  record('그 프레임이 방금 만든 것과 같다',
    (await shown()).bytes === madeAgain.bytes,
    `${madeAgain.bytes} → ${(await shown()).bytes}`)

  // (c) **원본보다 키울 수 있다** — 상한은 리미터가 잡으므로 계약에 없다 (확정 스펙 5장).
  await slide('voice', 150)
  record('100을 넘겨도 값이 깎이지 않는다',
    await until(async () => (await audioOf()).voice_volume === 1.5),
    JSON.stringify(await audioOf()))

  // (d) 슬라이더 범위 밖 값(손으로 고친 project.json)도 화면이 깎지 않는다.
  await evaluate('window.__smoke.editVolume(\'voice_volume\', 3)')
  const beyond = await until(async () => (await text('[data-testid="volume-voice"]')) === '300')
  record('범위 밖 값은 수치로 그대로 보인다', beyond, await text('[data-testid="volume-voice"]'))
  record('그때 손잡이는 끝에 서고 값은 그대로다',
    await evaluate('document.querySelector(\'[data-testid="slider-voice"]\').value') === '200'
    && (await audioOf()).voice_volume === 3,
    JSON.stringify(await audioOf()))

  // 재시작 확인이 보는 값을 하나로 둔다. 저장 뒤 확인은 `questions-verify`가 한다.
  await slide('voice', 60)
  await until(async () => (await audioOf()).voice_volume === 0.6)
  record('볼륨을 저장한다', await evaluate('window.__smoke.save()') === true)

  // 11. **자막 문구와 텍스트 오버레이** (#83). 길이(#82)와 같은 항목에 얹히는지, 그리고
  //     낡음이 두 종류로 갈리는지가 이 절의 확인 대상이다 (확정 스펙 7.2·7.3).
  const overrides = () => evaluate('window.__smoke.state().project.render.scene_overrides ?? []')
  const overrideOf = async (role, questionId) =>
    (await overrides()).find((item) => item.role === role && (item.question_id ?? null) === questionId) ?? null

  // (a) `countdown`에는 자막 문구 칸이 없다 — 장면에 `text`가 없다 (`seconds`를 세는 장면이다).
  await evaluate('window.__smoke.select(2)')
  await until(async () => (await text('[data-testid="properties-scene"]')).includes('countdown'))
  record('자막 문구가 없는 장면에는 입력이 없다',
    await evaluate('document.querySelector(\'[data-testid="input-자막 문구"]\') === null'))

  // (b) #82가 길이를 얹은 그 장면이다 — **같은 항목에 문구가 함께 붙는지**를 본다.
  await evaluate('window.__smoke.select(3)')
  await until(async () => (await text('[data-testid="properties-scene"]')).includes('answer'))
  // **프레임 인덱스로 기다린다.** 비교 기준을 잡기 전에 그 장면의 프레임이 서 있어야 하고,
  // `ready`만 보면 직전 장면의 프레임에서 곧바로 통과한다 (5(b)와 같은 함정이다).
  const frameAt = (index) => until(async () => {
    const frame = await shown()
    return frame !== null && frame.index === index
  }, 120, 100)
  await frameAt(3)
  const beforeCaption = await shown()
  const originalText = (await evaluate('window.__smoke.state().scenes.scenes[3]')).text
  await evaluate(`window.__smoke.editCaption(3, ${quote(MARKERS.caption)})`)

  const captioned = await until(async () =>
    (await evaluate('window.__smoke.state().shownTexts'))[3] === MARKERS.caption)
  record('자막 문구를 고치면 화면의 장면이 그 문구를 쓴다', captioned,
    JSON.stringify((await evaluate('window.__smoke.state().shownTexts'))[3]))
  record('scenes.json의 문구는 그대로다',
    (await evaluate('window.__smoke.state().scenes.scenes[3]')).text === originalText, originalText)

  const merged = await overrideOf('answer', narrated.question_id)
  record('길이와 문구가 같은 오버라이드 항목에 있다',
    merged !== null && typeof merged.duration === 'number' && merged.text === MARKERS.caption,
    JSON.stringify(merged))

  // (c) **자막만 낡음이다.** 파랑 사각 `↻`이고 음성까지 낡음(`♪`)이 아니다 (확정 스펙 7.3).
  record('자막만 낡았다는 카드가 뜬다',
    await until(async () =>
      await attribute('[data-testid="stale-card"]', 'data-kind') === 'captions'),
    await attribute('[data-testid="stale-card"]', 'data-kind'))
  record('장면 행에도 표시가 붙는다',
    await evaluate('document.querySelectorAll(\'[data-testid="scene-row-stale"]\').length') === 1)
  // 동작은 #77의 몫이라 자리만 있다 — 없으면 다음 사람이 다른 자리에 만든다.
  record('자막 재생성 버튼이 자리에 있고 아직 누를 수 없다',
    await evaluate('(() => { const n = document.querySelector(\'[data-testid="regenerate-captions"]\');'
      + ' return n !== null && n.disabled })()'))
  // **`review`를 건드리지 않는다** — 계산되는 값이라 적을 자리가 없다 (`editedCaptions`).
  record('자막 문구 편집이 review에 적히지 않는다',
    ((await evaluate('window.__smoke.state().project.review')).captions_stale ?? []).length === 0,
    JSON.stringify(await evaluate('window.__smoke.state().project.review')))

  record('고친 문구가 프리뷰 프레임까지 간다', await until(async () => {
    const frame = await shown()
    return frame !== null && frame.index === 3 && beforeCaption !== null
      && frame.bytes !== beforeCaption.bytes
  }, 120, 100), `${beforeCaption && beforeCaption.bytes} → ${JSON.stringify(await shown())}`)

  // (d) 되돌리면 **표시도 함께 사라진다** — `scenes.json`이 비교 기준이라 계산으로 나온다.
  //     그때 **길이 오버라이드는 남아야 한다**: 항목이 통째로 지워지면 #82의 값을 잃는다.
  await evaluate('document.querySelector(\'[data-testid="revert-caption"]\').click()')
  const reverted = await until(async () => {
    const item = await overrideOf('answer', narrated.question_id)
    return item !== null && item.text === undefined
  })
  record('되돌리면 문구가 scenes.json 값으로 돌아간다',
    reverted && (await evaluate('window.__smoke.state().shownTexts'))[3] === originalText,
    JSON.stringify(await overrideOf('answer', narrated.question_id)))
  record('되돌려도 조정한 길이는 남는다',
    typeof (await overrideOf('answer', narrated.question_id)).duration === 'number')
  record('되돌리면 낡음 표시도 사라진다',
    await until(async () => (await text('[data-testid="stale-card"]')) === null))

  // 재시작 확인이 볼 값을 다시 얹는다.
  await evaluate(`window.__smoke.editCaption(3, ${quote(MARKERS.caption)})`)
  await until(async () => (await overrideOf('answer', narrated.question_id)).text === MARKERS.caption)

  // (e) **오버레이** — 문제에 속하지 않는 장면(hook)에 얹어 `question_id` 없는 키도 밟는다.
  const contract = (await evaluate('window.__smoke.state().presets')).overlay
  record('오버레이 계약 목록이 백엔드에서 온다',
    contract !== null && contract.positions.length === 9 && contract.colors.length === 3
    && contract.sizes.length === 3 && contract.weights.length === 3,
    JSON.stringify(contract))

  await evaluate('window.__smoke.select(0)')
  await until(async () => (await text('[data-testid="properties-scene"]')).includes('hook'))
  await frameAt(0)
  const beforeOverlay = await shown()
  await evaluate('document.querySelector(\'[data-testid="overlay-add"]\').click()')
  const added = await until(async () => (await overrideOf('hook', null))?.overlays?.length === 1)
  const overlay = () => evaluate(
    '(() => { const list = (window.__smoke.state().project.render.scene_overrides ?? [])'
    + '.find((item) => item.role === "hook"); return list && list.overlays[0] })()'
  )
  record('오버레이를 추가하면 그 장면의 오버라이드에 쌓인다', added, JSON.stringify(await overlay()))
  record('추가한 오버레이의 웨이트가 번들 웨이트다',
    contract.weights.includes((await overlay()).weight), JSON.stringify(await overlay()))
  record('카드가 화면에 선다',
    await evaluate('document.querySelectorAll(\'[data-testid="overlay-card"]\').length') === 1)

  // **프레임 확인을 여기서 한다** — 방금 추가한 오버레이는 `timing: "scene"`이라 장면 어디서나
  // 보인다. 아래에서 구간을 지정한 뒤에는 **대표 프레임이 장면 한가운데**이므로(#27) 창이 그
  // 시각을 덮지 않으면 프레임에 나타나지 않는다 — 그때 프레임이 그대로인 것은 정상이다.
  record('얹은 오버레이가 프리뷰 프레임까지 간다', await until(async () => {
    const frame = await shown()
    return frame !== null && frame.index === 0 && beforeOverlay !== null
      && frame.bytes !== beforeOverlay.bytes
  }, 120, 100), `${beforeOverlay && beforeOverlay.bytes} → ${JSON.stringify(await shown())}`)

  // 위치는 9칸 격자를 눌러서 고른다 — 이름을 이 파일에 적지 않는다 (프리셋과 같은 이유).
  const cell = contract.positions[contract.positions.length - 1]
  await evaluate(`document.querySelector('[data-testid="overlay-positions"] [data-value=${quote(cell)}]').click()`)
  record('9칸 격자를 눌러 위치를 고른다',
    await until(async () => (await overlay()).pos === cell), JSON.stringify(await overlay()))

  const bigger = contract.sizes[contract.sizes.length - 1]
  await evaluate(`document.querySelector('[data-testid="segmented-overlay-size"] [data-value=${quote(String(bigger))}]').click()`)
  record('크기를 고른다', await until(async () => (await overlay()).size === bigger))

  const heaviest = contract.weights[contract.weights.length - 1]
  await evaluate(`document.querySelector('[data-testid="segmented-overlay-weight"] [data-value=${quote(String(heaviest))}]').click()`)
  record('굵기를 고르면 번들 웨이트만 저장된다',
    await until(async () => (await overlay()).weight === heaviest)
    && contract.weights.includes((await overlay()).weight))

  // 색은 **이름**이 저장된다 — 값을 복사하면 스타일 교체 후 조합 판정이 비켜간다 (7.2).
  const muted = contract.colors[contract.colors.length - 1].name
  await evaluate(`document.querySelector('[data-testid="segmented-overlay-color"] [data-value=${quote(muted)}]').click()`)
  record('색은 값이 아니라 이름으로 저장된다',
    await until(async () => (await overlay()).color === muted), JSON.stringify(await overlay()))

  // 구간 — `"scene"`에서 `{start, dur}`로 갈린다. 장면 끝을 넘으면 렌더가 자른다 (경고 없음).
  await evaluate('document.querySelector(\'[data-testid="segmented-overlay-timing"] [data-value="window"]\').click()')
  const windowed = await until(async () => typeof (await overlay()).timing === 'object')
  record('구간을 지정하면 timing이 객체가 된다', windowed, JSON.stringify((await overlay()).timing))

  await evaluate(`window.__smoke.editOverlay(0, ${quote((await overlay()).id)}, { text: ${quote(MARKERS.overlay)} })`)
  record('오버레이 문구가 저장된다',
    await until(async () => (await overlay()).text === MARKERS.overlay))

  // (f) **삭제에 확인 단계가 있고 확인 전에는 목록이 그대로다** (확정 스펙 7.4).
  await evaluate('document.querySelector(\'[data-testid="overlay-remove"]\').click()')
  const asking = await until(async () =>
    await evaluate('document.querySelector(\'[data-testid="overlay-remove-confirm"]\') !== null'))
  record('삭제를 누르면 확인을 받는다', asking)
  record('확인 전에는 목록이 그대로다', (await overrideOf('hook', null)).overlays.length === 1)
  await evaluate('document.querySelector(\'[data-testid="overlay-remove-cancel"]\').click()')
  record('취소하면 오버레이가 남는다',
    await until(async () =>
      await evaluate('document.querySelector(\'[data-testid="overlay-remove"]\') !== null'))
    && (await overrideOf('hook', null)).overlays.length === 1)

  // 하나 더 얹어 지운다 — **비면 오버라이드 항목까지 사라진다** (빈 목록은 스키마가 거부한다).
  await evaluate('document.querySelector(\'[data-testid="overlay-add"]\').click()')
  await until(async () => (await overrideOf('hook', null)).overlays.length === 2)
  await evaluate('document.querySelectorAll(\'[data-testid="overlay-remove"]\')[1].click()')
  await evaluate('document.querySelectorAll(\'[data-testid="overlay-remove-confirm"]\')[0].click()')
  record('확인하면 그 오버레이만 지워진다',
    await until(async () => (await overrideOf('hook', null)).overlays.length === 1),
    JSON.stringify(await overrideOf('hook', null)))

  // (g) **빈 문구는 적용되지 않는다** — 스키마가 거부하는 값이라 올리면 저장 전체가 실패한다.
  const keptText = (await overlay()).text
  await evaluate(
    `(() => { const el = document.querySelector('[data-testid="input-오버레이 문구"]');
       const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
       setter.call(el, ''); el.dispatchEvent(new Event('input', { bubbles: true })) })()`
  )
  await delay(200)
  record('오버레이 문구를 비우면 값이 적용되지 않는다', (await overlay()).text === keptText, keptText)
  record('그때 입력이 거부 색으로 선다',
    await evaluate('document.querySelector(\'[data-testid="input-오버레이 문구"]\').className')
      .then((names) => names.includes('input--danger')))

  // 다시 채우면 거부 표시가 사라진다 — 고칠 것이 없어진 자리에 남아 있으면 지금 값이 거부된
  // 것으로 읽힌다 (#80의 배경 거부 카드와 같은 판단이다).
  await evaluate(
    `(() => { const el = document.querySelector('[data-testid="input-오버레이 문구"]');
       const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
       setter.call(el, ${quote(MARKERS.overlay)}); el.dispatchEvent(new Event('input', { bubbles: true })) })()`
  )
  record('다시 채우면 거부 표시가 사라진다', await until(async () =>
    await evaluate('document.querySelector(\'[data-testid="input-오버레이 문구"]\').className')
      .then((names) => !names.includes('input--danger'))))

  // **패널을 위로 되돌려 캡처한다.** 9(b)가 배경 컨트롤을 보려고 끝까지 내려 뒀고, 그 상태로
  // 찍으면 이 이슈의 컨트롤(장면 구획의 자막 문구·오버레이 카드)이 화면 밖에 있다.
  await evaluate(
    `(() => { const n = document.querySelector(${quote('[data-testid="properties"] .panel__scroll')});
       n.scrollTop = 0 })()`
  )
  await delay(100)
  await capture(window, record, SHOT_OVERLAY)
  record('자막 문구와 오버레이를 저장한다', await evaluate('window.__smoke.save()') === true)

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

  // 6-2. **해설만 고치면 자막만 낡는다** (#83, 확정 스펙 7.3). 해설은 `caption`으로만 담기고
  //      TTS는 `narrate: true` 장면의 `text`만 읽으므로 음성은 그대로 쓸 수 있다 — 낡음이 한
  //      종류였을 때는 이 경우가 "음성까지 낡음"으로 잘못 표시됐다.
  const editExplanation = (id) => evaluate(
    `(() => { const c = window.__smoke.state().content;
       const next = { ...c, questions: c.questions.map((q) => (q.id === ${id} ? { ...q, explanation: ${quote(MARKERS.explanation)} } : q)) };
       window.__smoke.editContent(next) })()`
  )

  // **2번에 얹는다** — 이 시나리오가 뒤에서 3번을 지우므로, 재시작 뒤에도 남아 있어야 하는
  // 값은 살아남는 문제에 있어야 한다.
  await editExplanation(2)
  const captionsOnly = await until(async () => {
    const review = (await state()).project.review
    return (review.captions_stale ?? []).includes(2) && !review.stale.includes(2)
  })
  record('해설만 고치면 자막만 낡는다', captionsOnly,
    JSON.stringify((await state()).project.review))

  // **확인은 자막 쪽 변화에도 풀린다** (사람이 확인한 것은 그 문제의 내용이다, 확정 스펙 1.4).
  // `unverified`인 3번에만 확인 버튼이 있다.
  await evaluate('window.__smoke.selectItem(3)')
  await until(async () => (await state()).selectedItem === 3)
  await evaluate('document.querySelector(\'[data-testid="acknowledge"]\').click()')
  await until(async () => (await state()).project.review.acknowledged.includes(3))
  await editExplanation(3)
  record('해설을 고쳐도 확인은 풀린다',
    await until(async () => !(await state()).project.review.acknowledged.includes(3)),
    JSON.stringify((await state()).project.review.acknowledged))
  record('그 배지가 음성까지 낡음과 다른 모양·문구다', await evaluate(
    `[...document.querySelectorAll('[data-testid="stale-badge"]')].map((n) => ({
       kind: n.dataset.kind, text: n.textContent.trim(),
       radius: getComputedStyle(n).borderRadius
     }))`
  ).then((badges) => {
    const audio = badges.find((badge) => badge.kind === 'audio')
    const captions = badges.find((badge) => badge.kind === 'captions')
    return Boolean(audio && captions) && audio.text !== captions.text
      && audio.radius !== captions.radius
  }))
  record('두 낡음이 함께 걸린다', await evaluate(
    '(() => { const r = window.__smoke.state().project.review;'
    + ' return r.stale.length > 0 && (r.captions_stale ?? []).length > 0 })()'
  ))

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

  // **`preview` 시나리오가 얹은 자막 문구와 오버레이다** (#83). 세 편집이 항목 하나를
  // 공유하므로, 저장·재시작을 지나 함께 남아 있어야 한다 (PRD 14.1).
  const answerOverride = overrides.find((item) => item.role === 'answer') ?? null
  record('고친 자막 문구가 재시작 뒤에도 유지된다',
    answerOverride !== null && answerOverride.text === MARKERS.caption,
    JSON.stringify(answerOverride))
  record('길이와 문구가 여전히 같은 항목에 있다',
    answerOverride !== null && typeof answerOverride.duration === 'number'
    && typeof answerOverride.text === 'string')

  const hookOverride = overrides.find((item) => item.role === 'hook') ?? null
  record('얹은 오버레이가 재시작 뒤에도 유지된다',
    hookOverride !== null && Array.isArray(hookOverride.overlays)
    && hookOverride.overlays.length === 1
    && hookOverride.overlays[0].text === MARKERS.overlay,
    JSON.stringify(hookOverride))
  record('오버레이 웨이트가 번들 웨이트로 남는다',
    (await evaluate('window.__smoke.state().presets')).overlay.weights
      .includes(hookOverride.overlays[0].weight),
    JSON.stringify(hookOverride.overlays[0]))

  // **계산된 값이라 파일에 없다** — 재시작 뒤에도 같은 답이 나오는지는 `scenes.json`과
  // 오버라이드를 다시 비교해서 나온다 (`editedCaptions`, `orderStale`과 같은 판단이다).
  record('자막이 낡았다는 판단이 재시작 뒤에도 같다',
    state.captionEdits.length === 1 && state.scenes.scenes[state.captionEdits[0]].role === 'answer',
    JSON.stringify(state.captionEdits))
  // scenes.json은 앱이 쓰지 않는다 — 사람이 얹은 값은 전부 project.json에 있다.
  record('scenes.json의 문구는 편집을 지나도 그대로다',
    state.scenes.scenes[3].text !== MARKERS.caption, state.scenes.scenes[3].text)

  record('자막만 낡은 문제가 재시작 뒤에도 남는다',
    (state.project.review.captions_stale ?? []).includes(2),
    JSON.stringify(state.project.review))

  // **`preview` 시나리오가 움직인 볼륨이다** (#81). 파일에 사는 값은 눈금이 아니라 선형
  // 게인이므로, 눈금이 그대로 저장되는 회귀가 있으면 여기서 60이 되어 갈린다.
  record('조정한 볼륨이 재시작 뒤에도 유지된다',
    state.project.audio.voice_volume === 0.6 && state.project.audio.sfx_volume === 0,
    JSON.stringify(state.project.audio))
  record('그 값이 화면의 눈금으로 다시 뜬다',
    await until(async () => await evaluate(
      '(() => { const n = document.querySelector(\'[data-testid="volume-voice"]\'); return n && n.textContent })()'
    ) === '60'))

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
