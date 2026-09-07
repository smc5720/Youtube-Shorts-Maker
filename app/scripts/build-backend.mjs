// 백엔드를 PyInstaller onedir로 동결한다 (이슈 #106). `npm run dist`의 첫 단계다.
//
// **파이썬을 찾는 규칙이 `electron/main.js`의 `backendCommand()`와 같아야 한다** — 개발 중
// 앱을 띄우는 인터프리터로 빌드해야 동결본이 같은 의존성 트리를 갖는다. `SHORTS_PYTHON`이
// 그 자리이고, 없으면 저장소 `.venv`, 그것도 없으면 PATH의 `python`이다.
//
// **`assets/` 복사가 이 스크립트에 있는 이유는 스펙이 넣을 수 없기 때문이다.** PyInstaller
// 6의 `datas`는 전부 `_internal/`로 가는데 `assets.ASSETS_DIR`이 보는 자리는 실행 파일
// 옆이다 (스파이크 25 5.1, `packaging/shorts-backend.spec` 도입부).

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const APP_ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const REPO_ROOT = path.resolve(APP_ROOT, '..')

// PyInstaller 산출물은 저장소 `build/` 아래 둔다 — `.gitignore`가 이미 무시하고, 기본값
// (`dist/`)은 `app/dist`(Vite 산출물)와 이름이 겹쳐 헷갈린다.
const DIST_PATH = path.join(REPO_ROOT, 'build', 'backend-dist')
const WORK_PATH = path.join(REPO_ROOT, 'build', 'backend-work')
const OUT_DIR = path.join(DIST_PATH, 'shorts-backend')
const EXE = path.join(OUT_DIR, process.platform === 'win32' ? 'shorts-backend.exe' : 'shorts-backend')

function resolvePython () {
  if (process.env.SHORTS_PYTHON) return process.env.SHORTS_PYTHON
  const bundled = process.platform === 'win32'
    ? path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(REPO_ROOT, '.venv', 'bin', 'python')
  return fs.existsSync(bundled) ? bundled : 'python'
}

function fail (message) {
  console.error(`[build-backend] ${message}`)
  process.exit(1)
}

const python = resolvePython()
console.log(`[build-backend] 파이썬: ${python}`)

const version = spawnSync(python, ['-m', 'PyInstaller', '--version'], { encoding: 'utf-8' })
if (version.status !== 0) {
  fail(
    'PyInstaller를 찾지 못했다. 배포본을 만들려면 빌드 의존성이 필요하다:\n' +
    `  ${python} -m pip install -e ".[dist]"`
  )
}
console.log(`[build-backend] PyInstaller ${version.stdout.trim()}`)

const build = spawnSync(python, [
  '-m', 'PyInstaller',
  '--noconfirm',
  '--distpath', DIST_PATH,
  '--workpath', WORK_PATH,
  path.join(REPO_ROOT, 'packaging', 'shorts-backend.spec')
], { stdio: 'inherit' })
if (build.status !== 0) fail(`PyInstaller가 실패했다 (종료 코드 ${build.status}).`)

if (!fs.existsSync(EXE)) fail(`실행 파일이 나오지 않았다: ${EXE}`)

// **넣지 않으면 프리셋 조회부터 실패한다** — 앱은 배경·자막 스타일 목록을 백엔드의
// `presets`로만 얻는다 (#79, #80, #83).
const assetsFrom = path.join(REPO_ROOT, 'assets')
const assetsTo = path.join(OUT_DIR, 'assets')
fs.rmSync(assetsTo, { recursive: true, force: true })
fs.cpSync(assetsFrom, assetsTo, { recursive: true })

// 실행 파일 옆이 맞는지 확인한다. `_internal/assets`에 들어가면 경로는 있는데 백엔드가
// 못 찾는 상태가 되고, 그때 드러나는 곳은 빌드가 아니라 프리뷰다.
const presets = path.join(assetsTo, 'backgrounds', 'presets.json')
if (!fs.existsSync(presets)) fail(`assets/ 복사가 어긋났다: ${presets}가 없다.`)

console.log(`[build-backend] 완료: ${OUT_DIR}`)
