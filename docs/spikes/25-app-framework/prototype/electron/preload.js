// 렌더러가 볼 수 있는 것의 전부. Node도 파일 시스템도 백엔드 프로세스도 여기서 가려진다.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('api', {
  call: (method, params) => ipcRenderer.invoke('rpc', method, params),
  context: () => ipcRenderer.invoke('context'),
  smokeDone: (report) => ipcRenderer.invoke('smoke-done', report),
  onEvent: (handler) => ipcRenderer.on('backend-event', (_event, message) => handler(message))
})
