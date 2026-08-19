// 앱 스모크 (#26, #27, #28, #82, #79, #80, #81, #83, #30) — 앱을 실제로 띄워 완료 조건을 밟고
// 결과를 남긴다.
//
// 여덟 번 띄운다. **한 프로세스 안에서 다시 여는 것은 "재시작"의 증거가 되지 못하기 때문이다.**
//
//   1. edit             — 열기 · 스키마 오류 · 편집 표시 · 닫기 확인 · 저장 · 저장하며 닫기 (#26)
//   2. verify           — 다시 띄워서 저장한 것이 그대로 열리는지 (#26)
//   3. preview          — 3분할 · 장면 목록 · 프리뷰 프레임 · 대기 표현 2종 · 총 길이 (#27),
//                         장면 길이 조정 (#82), 자막 스타일·배경 프리셋 교체 (#79),
//                         배경 사용자 파일과 미지원 형식 거부 (#80), 트랙 볼륨 (#81),
//                         자막 문구와 텍스트 오버레이 (#83)
//   4. questions        — 2분할 · 세 상태 표기 · 확인 기록 · 낡음 두 종류 · 순서·추가·삭제
//                         (#28, #83)
//   5. questions-verify — 다시 띄워서 문제 편집·길이·프리셋·배경 파일·볼륨·자막 문구·오버레이가
//                         남았는지 (#28, #82, #79, #80, #81, #83)
//   6. render           — 경고 게이트 · 진행률 · 완료 · 실패 · 다시 시도 · 파일 위치 (#30)
//   7. render-kill      — 렌더 도중 Electron만 강제 종료해 ffmpeg가 남는지 (#30)
//   8. idle             — 띄운 뒤 Electron만 강제 종료해 백엔드가 남는지 (스파이크 4.2)
//
// **3·6·7번은 FFmpeg를 요구한다.** 실제 프레임과 실제 mp4가 그 시나리오의 절반이라 대역으로
// 바꾸면 확인하려는 것이 확인되지 않는다. 4번은 요구하지 않는다 — 그 화면에는 프리뷰가 없고
// 콘텐츠 편집이 프레임을 다시 만들지 않는 것 자체가 확인 대상이다.
//
// **순서가 있다.** 4번이 `run-smoke`의 두 파일을 고치므로 앞선 시나리오보다 뒤에 오고,
// **6번은 그 편집 전부가 실제 렌더를 지나는지를 보므로 마지막 편집 뒤에 온다** — 사람이 얹은
// 길이·자막 문구·오버레이가 mp4까지 가는지가 그 시나리오의 절반이다 (#30).
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
const {
  run: RUN, long: LONG, background: BG, unsupported: BG_BAD,
  // **웨이트 목록을 이 파일에 적지 않는다** (#83). 픽스처가 스키마에서 읽어 보내므로, 시안이
  // 적은 400·600이 되살아나면 아래 확인이 걸린다 (확정 스펙 7.1-2).
  overlay_weights: OVERLAY_WEIGHTS
} = JSON.parse(made.stdout.trim())
record('스모크가 열 run 디렉터리를 만든다', fs.existsSync(path.join(RUN, 'project.json')), RUN)
record('상한을 넘는 run 디렉터리도 만든다', fs.existsSync(path.join(LONG, 'scenes.json')), LONG)
// **run 디렉터리 밖이다** (#80). 앱은 고른 파일을 복사하지 않고 있는 자리를 가리킨다.
record('배경 파일이 run 디렉터리 밖에 있다',
  fs.existsSync(BG) && !BG.startsWith(RUN) && !BG.startsWith(LONG), BG)

// 고른 순간 거부되는 형식. **디코드되지 않는 내용이어도 된다** — 확장자만으로 걸린다.
const BG_MISSING = path.join(WORK, '배경', '사라진배경.png')

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
  // 배경 사용자 파일 (#80) — 받는 것 / 받지 않는 것 / 없는 것 셋을 시나리오가 차례로 고른다.
  SHORTS_SMOKE_BG: BG,
  SHORTS_SMOKE_BG_BAD: BG_BAD,
  SHORTS_SMOKE_BG_MISSING: BG_MISSING,
  SHORTS_SMOKE_OUT: previewOut,
  SHORTS_SMOKE_SHOT: path.join(APP_DIR, 'smoke', 'screenshot-preview.png'),
  SHORTS_SMOKE_SHOT_BG: path.join(APP_DIR, 'smoke', 'screenshot-background.png'),
  SHORTS_SMOKE_SHOT_OVERLAY: path.join(APP_DIR, 'smoke', 'screenshot-overlay.png'),
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

// **배경 파일은 복사되지 않는다** (#80). 시나리오가 `run-smoke-long`에 고른 파일을 저장했고,
// 값은 있는 자리를 가리키는 절대 경로여야 한다 (PRD 14.1).
const longProject = JSON.parse(fs.readFileSync(path.join(LONG, 'project.json'), 'utf8'))
record('고른 배경이 project.json에 절대 경로로 남는다',
  longProject.background.kind === 'image' && longProject.background.value === BG
  && path.isAbsolute(longProject.background.value),
  JSON.stringify(longProject.background))
record('배경 파일이 run 디렉터리로 복사되지 않는다',
  !fs.readdirSync(LONG).some((name) => name.includes('배경')), fs.readdirSync(LONG).join(', '))

// **파일에 사는 값은 눈금이 아니라 선형 게인이다** (#81, D2 확정 스펙 5장). 시나리오가 슬라이더를
// 60으로 두었으므로 파일에는 0.6이 있어야 하고, 60이 그대로 있으면 렌더가 60배로 돈다.
const volumes = JSON.parse(fs.readFileSync(path.join(RUN, 'project.json'), 'utf8')).audio
record('볼륨이 선형 게인으로 project.json에 남는다',
  volumes.voice_volume === 0.6 && volumes.sfx_volume === 0, JSON.stringify(volumes))

// **사람이 얹은 편집은 전부 `project.json`에 있다** (#82, #83). 자막 문구와 오버레이를
// `scenes.json`에 쓰면 재생성(#77)이 장면을 다시 만들 때 사라지고, 낭독보다 짧은 길이는
// 확정 검증이 거부해 그 run 디렉터리가 다시 열리지 않는다 (PRD 14.1).
const edited = JSON.parse(fs.readFileSync(path.join(RUN, 'project.json'), 'utf8'))
const scenesOnDisk = JSON.parse(fs.readFileSync(path.join(RUN, 'scenes.json'), 'utf8'))
const overrides = edited.render.scene_overrides ?? []
const answerOverride = overrides.find((item) => item.role === 'answer') ?? null
const hookOverride = overrides.find((item) => item.role === 'hook') ?? null

record('길이와 자막 문구가 project.json의 한 항목에 있다',
  answerOverride !== null && typeof answerOverride.duration === 'number'
  && typeof answerOverride.text === 'string',
  JSON.stringify(answerOverride))
record('오버레이도 같은 섹션에 있다',
  hookOverride !== null && Array.isArray(hookOverride.overlays)
  && hookOverride.overlays.length === 1,
  JSON.stringify(hookOverride))
// **화면에는 보이지 않고 렌더에서만 실패하는 종류의 오류다** (확정 스펙 7.1-2).
record('저장된 오버레이 웨이트가 번들 웨이트다',
  OVERLAY_WEIGHTS.includes(hookOverride.overlays[0].weight),
  `${hookOverride.overlays[0].weight} / ${OVERLAY_WEIGHTS.join(',')}`)
record('scenes.json에는 사람이 얹은 값이 없다',
  scenesOnDisk.scenes.every((scene) => !('overlays' in scene))
  && scenesOnDisk.scenes[3].text !== answerOverride.text,
  scenesOnDisk.scenes[3].text)

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

// **배경 파일을 지우고 넘긴다** (#80 완료 조건 — 파일이 사라진 뒤 프로젝트를 열면).
// 다음 시나리오는 새 프로세스라 프리뷰 캐시가 비어 있어, 사라진 배경이 실제로 프리뷰 경로를
// 지난다 — 같은 프로세스에서 지우면 캐시가 옛 프레임으로 답해 아무 일도 일어나지 않는다.
fs.rmSync(BG)

const qVerifyOut = path.join(WORK, 'questions-verify.json')
const qVerify = await wait(electron('questions-verify', {
  SHORTS_SMOKE_RUN: RUN,
  SHORTS_SMOKE_LONG: LONG,
  SHORTS_SMOKE_BG: BG,
  SHORTS_SMOKE_OUT: qVerifyOut,
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
}), 120000)

const qVerifyResult = fs.existsSync(qVerifyOut) ? JSON.parse(fs.readFileSync(qVerifyOut, 'utf8')) : null
results.phases.push({ scenario: 'questions-verify', ...qVerify, result: qVerifyResult })
if (qVerifyResult) results.checks.push(...qVerifyResult.checks)
record('문제 편집 재시작 시나리오가 끝난다', qVerifyResult && qVerifyResult.ok && !qVerify.timedOut)

// --- 6. 최종 렌더 (#30) -------------------------------------------------------------

// **여기까지의 편집이 그대로 렌더에 들어간다** — 사람이 얹은 길이·자막 문구(`%`·`:` 포함)·
// 오버레이가 실제 mp4를 지나고, 남아 있는 `flagged`·`unverified`가 게이트를 밟는다.
const ffmpegPids = () => {
  if (process.platform === 'win32') {
    const listed = spawnSync('tasklist', ['/FI', 'IMAGENAME eq ffmpeg.exe', '/NH', '/FO', 'CSV'], { encoding: 'utf8' })
    return new Set((listed.stdout || '').split('\n')
      .map((line) => /^"ffmpeg\.exe","(\d+)"/.exec(line.trim()))
      .filter(Boolean).map((match) => match[1]))
  }
  const listed = spawnSync('pgrep', ['-x', 'ffmpeg'], { encoding: 'utf8' })
  return new Set((listed.stdout || '').split('\n').map((line) => line.trim()).filter(Boolean))
}

const renderOut = path.join(WORK, 'render.json')
const render = await wait(electron('render', {
  SHORTS_SMOKE_RUN: RUN,
  SHORTS_SMOKE_OUT: renderOut,
  SHORTS_SMOKE_SHOT_RENDER: path.join(APP_DIR, 'smoke', 'screenshot-render.png'),
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
}), 300000)

const renderResult = fs.existsSync(renderOut) ? JSON.parse(fs.readFileSync(renderOut, 'utf8')) : null
results.phases.push({ scenario: 'render', ...render, result: renderResult })
if (renderResult) results.checks.push(...renderResult.checks)
record('렌더 시나리오가 끝난다', renderResult && renderResult.ok && !render.timedOut)

// **CLI와 같은 규격이어야 한다** — 앱이 부르는 것은 같은 `video_renderer.render`이고, 앱 쪽에
// 명령을 다시 조립하는 코드가 없다는 것이 이 확인의 뜻이다 (PRD 6.3).
const OUTPUT = path.join(RUN, 'final_short.mp4')
record('앱이 실행한 렌더가 mp4를 남긴다', fs.existsSync(OUTPUT), OUTPUT)
if (fs.existsSync(OUTPUT)) {
  const probed = spawnSync('ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration:stream=codec_name,width,height,r_frame_rate',
    '-of', 'json', OUTPUT
  ], { encoding: 'utf8' })
  const media = probed.status === 0 ? JSON.parse(probed.stdout) : null
  const video = media && media.streams.find((stream) => stream.width)
  const audio = media && media.streams.find((stream) => !stream.width)
  record('규격이 1080x1920 30fps h264 + aac이다',
    Boolean(video) && video.width === 1080 && video.height === 1920
    && video.codec_name === 'h264' && video.r_frame_rate === '30/1'
    && Boolean(audio) && audio.codec_name === 'aac',
    JSON.stringify(media && media.streams))

  // **사람이 얹은 길이가 결과에 반영된다** (#82 → #30). 오버라이드는 확정 검증 뒤에 얹히므로
  // 낭독보다 짧은 값도 렌더까지 가고, 그만큼 총 길이가 줄어야 한다.
  const scenesFile = JSON.parse(fs.readFileSync(path.join(RUN, 'scenes.json'), 'utf8'))
  const projectFile = JSON.parse(fs.readFileSync(path.join(RUN, 'project.json'), 'utf8'))
  const applied = scenesFile.scenes.map((scene) => {
    const override = (projectFile.render.scene_overrides ?? []).find((item) =>
      item.role === scene.role && (item.question_id ?? null) === (scene.question_id ?? null))
    return override && typeof override.duration === 'number' ? override.duration : scene.duration
  })
  // 프레임 정렬 총 길이 — `video_renderer.align()`과 같은 계산이다 (PRD 7.7).
  const frames = applied.reduce((sum, duration) => sum + Math.max(1, Math.round(duration * 30)), 0)
  const expected = frames / 30
  const duration = Number(media.format.duration)
  record('총 길이가 사람이 얹은 길이를 반영한다',
    Math.abs(duration - expected) < 0.05,
    `${duration} / ${expected} (오버라이드 ${JSON.stringify(projectFile.render.scene_overrides ?? [])})`)
}

// --- 7. 렌더 도중 강제 종료 (#30) ---------------------------------------------------

// **자식 ffmpeg가 남으면 사용자가 앱을 닫은 뒤에 mp4가 완성된다.** 렌더 스레드는 daemon이라
// 백엔드가 끝날 때 그냥 사라지므로, 죽이는 것은 `api.serve`의 `atexit`이다.
//
// **긴 run을 쓴다.** `run-smoke`는 28초 영상이라 이 머신에서 3~4초에 끝나고, 그러면 죽이려는
// 순간에 ffmpeg가 이미 없을 수 있다 — 92.5초 쪽은 10초 넘게 돌아 창이 넉넉하다.
// 배경은 프리셋으로 되돌린다: 이 run이 가리키던 사용자 파일은 5번 시나리오를 위해 지워졌고
// (없는 파일은 명령을 만들기 전에 걸린다) 그러면 렌더가 시작조차 하지 않는다.
const longFixture = JSON.parse(fs.readFileSync(path.join(LONG, 'project.json'), 'utf8'))
longFixture.background = JSON.parse(fs.readFileSync(path.join(RUN, 'project.json'), 'utf8')).background
fs.writeFileSync(path.join(LONG, 'project.json'), JSON.stringify(longFixture, null, 2) + '\n')

const ffmpegBefore = ffmpegPids()
const killReady = path.join(WORK, 'render-kill.json')
const killing = electron('render-kill', {
  SHORTS_SMOKE_RUN: LONG,
  SHORTS_SMOKE_READY: killReady,
  SHORTS_SMOKE_OUT: path.join(WORK, 'render-kill-result.json'),
  SHORTS_APP_LOG: path.join(WORK, 'app.log')
})

let killState = null
for (let attempt = 0; attempt < 600 && !killState; attempt += 1) {
  await delay(100)
  if (fs.existsSync(killReady)) killState = JSON.parse(fs.readFileSync(killReady, 'utf8'))
}
const spawnedFfmpeg = [...ffmpegPids()].filter((pid) => !ffmpegBefore.has(pid))
record('렌더 중 ffmpeg가 떠 있다', Boolean(killState) && spawnedFfmpeg.length > 0,
  `${JSON.stringify(killState)} ffmpeg=${spawnedFfmpeg.join(',')}`)

if (killState) {
  // **트리를 죽이지 않는다** (`/T` 없음) — 백엔드와 그 자식이 스스로 사라지는지를 본다.
  if (process.platform === 'win32') spawnSync('taskkill', ['/F', '/PID', String(killState.electron)])
  else process.kill(killState.electron, 'SIGKILL')

  let gone = false
  for (let attempt = 0; attempt < 100 && !gone; attempt += 1) {
    await delay(200)
    const now = ffmpegPids()
    gone = !alive(killState.backend) && spawnedFfmpeg.every((pid) => !now.has(pid))
  }
  record('렌더 중 앱을 종료하면 백엔드도 ffmpeg도 남지 않는다', gone,
    `backend=${killState.backend} ffmpeg=${spawnedFfmpeg.join(',')}`)
  if (!gone) {
    for (const pid of [killState.backend, ...spawnedFfmpeg]) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/F', '/PID', String(pid)])
      else { try { process.kill(Number(pid), 'SIGKILL') } catch { /* 이미 없다 */ } }
    }
  }
}
killing.kill()

// 잘린 mp4를 치운다 — 부분 산출물을 최종 결과로 오인하지 않게 하는 것은 #36의 몫이고,
// 여기서는 스모크가 남긴 것을 스모크가 정리한다.
fs.rmSync(OUTPUT, { force: true })
fs.rmSync(path.join(LONG, 'final_short.mp4'), { force: true })

// --- 8. 강제 종료 뒤 백엔드가 남는가 -------------------------------------------------

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

// --- 9. 번들이 바깥을 가리키지 않는가 -------------------------------------------------

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

// --- 10. 프리셋 이름이 앱 코드에 없는가 (#79) -----------------------------------------

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
