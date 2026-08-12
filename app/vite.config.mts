import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))

export default defineConfig({
  // Electron이 `loadFile`로 여는 페이지라 절대 경로(`/assets/...`)는 파일 시스템 루트를
  // 가리킨다. 상대 경로여야 `dist/index.html` 옆을 본다.
  base: './',
  plugins: [react()],
  server: {
    // 번들 폰트가 `app/` 밖(저장소의 `assets/`)에 있다. 파일을 앱 안으로 복사하면 D1이
    // 관리하는 폰트가 두 벌이 된다 (#38).
    fs: { allow: [REPO_ROOT] }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 앱은 네트워크로 나가지 않으므로 소스맵이 새어 나갈 곳도 없다. 렌더러에서 난 예외의
    // 원본 위치를 로그에서 바로 보려면 있는 편이 낫다 (스파이크 7장의 함정).
    sourcemap: true
  }
})
