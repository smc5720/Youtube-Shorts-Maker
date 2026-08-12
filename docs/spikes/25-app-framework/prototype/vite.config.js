import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `base: './'`가 필요하다 — Electron이 `file://`로 여는 번들이라 절대 경로(`/assets/...`)는
// 디스크 루트를 가리켜 아무것도 로드되지 않는다.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true }
})
