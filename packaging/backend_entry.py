"""동결 배포(PyInstaller onedir)의 백엔드 진입점 (이슈 #106, PRD 14.1).

**`shorts_maker/api.py`를 PyInstaller 진입 스크립트로 직접 주지 않는 이유가 이 파일의
존재 이유다.** 진입 스크립트는 `__main__`으로 실행되므로, 패키지 모듈을 그대로 주면 같은
코드가 `__main__`과 `shorts_maker.api` 두 이름으로 각각 import된다 — 모듈 수준 상태
(`PROTOCOL_VERSION`·핸들러 표)가 두 벌이 되고, 그 상태에서 갈리는 버그는 동결본에서만
드러난다. 여기서 한 번 import해서 부르면 이름이 하나다.

개발 중에는 앱이 `python -m shorts_maker.api`를 띄우므로 이 파일을 지나지 않는다
(`app/electron/main.js`의 `backendCommand()`). 두 경로가 같은 `main()`에 도달한다.
"""

from shorts_maker.api import main

if __name__ == "__main__":
    main()
