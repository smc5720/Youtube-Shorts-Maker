// 렌더러가 볼 수 있는 것의 전부. Node도 파일 시스템도 백엔드 프로세스도 여기서 가려진다.
//
// **함수를 열고 객체를 열지 않는다.** `ipcRenderer`를 그대로 노출하면 렌더러가 임의의
// 채널로 말할 수 있게 되어 이 경계가 사라진다.

const { contextBridge, ipcRenderer } = require('electron')

const listen = (channel, handler) => {
  const wrapped = (_event, payload) => handler(payload)
  ipcRenderer.on(channel, wrapped)
  return () => ipcRenderer.off(channel, wrapped)
}

contextBridge.exposeInMainWorld('api', {
  // 백엔드 왕복. 응답은 `{result}` 아니면 `{error: {code, message, details}}`다 —
  // 예외로 던지지 않는 것은 실패가 화면에 그려질 값이기 때문이다 (D2 확정 스펙 4장).
  call: (method, params) => ipcRenderer.invoke('rpc', method, params),
  context: () => ipcRenderer.invoke('context'),
  pickRunDir: () => ipcRenderer.invoke('pick-run-dir'),
  // 배경 파일 (#80). 받는 확장자는 백엔드가 정하므로 렌더러가 실어 보낸다.
  pickBackgroundFile: (extensions) => ipcRenderer.invoke('pick-background-file', extensions),
  // 저장하지 않은 변경이 있는지. 창을 닫을 때 물어볼지가 이 값으로 갈린다.
  // **여기만 동기다** — 비동기로 보내면 화면이 먼저 바뀌고 main이 나중에 알아서, 그 틈에
  // 닫으면 확인 없이 닫힌다.
  setUnsaved: (value) => ipcRenderer.sendSync('set-unsaved', value),
  saveResult: (ok) => ipcRenderer.invoke('save-result', ok),
  onBackendEvent: (handler) => listen('backend-event', handler),
  onSaveRequest: (handler) => listen('save-request', handler),
  onMenu: (handler) => {
    const off = [
      listen('menu-open', () => handler('open')),
      listen('menu-save', () => handler('save'))
    ]
    return () => off.forEach((stop) => stop())
  }
})
