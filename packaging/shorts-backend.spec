# PyInstaller 스펙 — 앱이 자식으로 띄우는 백엔드를 onedir로 동결한다 (이슈 #106).
#
# 방식은 PRD 10장·14.1이 정했고 스파이크 25 5.1이 실측했다. 이 파일은 그 명령을 옮겨 적은
# 것이 아니라 **두 가지를 실측 명령에서 고친다.**
#
# 1. **hidden import를 손으로 적지 않는다.** 타입 선언 모듈은 이름으로 조회되는 시점에
#    import되므로(`shorts_types.get_type`) 정적 분석에 걸리지 않는다. 스파이크는
#    `--hidden-import shorts_maker.types.quiz`를 손으로 줬고 "타입이 늘면 이 목록도 는다"고
#    적었다 — 그 목록은 이미 `BUILTIN_TYPES`가 갖고 있어서, 여기서 읽으면 두 번째 사본이
#    생기지 않는다. 누락되면 동결본이 그 타입을 **읽기 전용으로** 열어 조용히 드러난다.
# 2. **`trafilatura`를 제외한다.** `source.load_extractor`의 import는 함수 안에 있지만
#    PyInstaller는 바이트코드를 훑어 중첩된 import도 찾아내므로, 두지 않으면 약 55MB가
#    동결본에 들어온다. 앱은 생성을 하지 않아 `--url` 경로를 지나지 않고, 없는 상태에서
#    부르면 그 함수가 설치 방법을 말하며 멈춘다 — `pyproject.toml`이 적어 둔 승격 조건
#    ("앱이 생성까지 하게 되면")이 아직 아니다.
#
# **`assets/`는 여기서 넣지 않는다.** PyInstaller 6의 `datas`는 전부 `_internal/`로 가는데
# `assets.ASSETS_DIR`이 보는 자리는 실행 파일 옆이다(`_internal/shorts_maker/assets.py`에서
# 두 단계 위 = dist 루트). 복사는 `app/scripts/build-backend.mjs`가 한다.
#
# **`console=True`인 것이 의도다.** 백엔드는 stdio로만 말하므로(`api._use_utf8`이 첫 줄
# 전에 `sys.stdout.reconfigure`를 부른다) 창 없는 서브시스템으로 빌드하면 그 호출이
# `None`에서 터질 수 있다. 콘솔 창을 숨기는 것은 띄우는 쪽의 `windowsHide`다.

import sys
from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parent
SRC = REPO_ROOT / "src"

# 아래 `BUILTIN_TYPES` 조회를 위해 저장소 소스를 스펙 평가 시점의 경로에 올린다.
# `shorts_types`는 표준 라이브러리만 import하므로(무거운 것은 `TYPE_CHECKING` 뒤에 있다)
# 스펙 평가가 타입 코드를 끌어오지 않는다.
sys.path.insert(0, str(SRC))

from shorts_maker.shorts_types import BUILTIN_TYPES  # noqa: E402

a = Analysis(
    [str(REPO_ROOT / "packaging" / "backend_entry.py")],
    pathex=[str(SRC)],
    hiddenimports=sorted(BUILTIN_TYPES.values()),
    excludes=["trafilatura"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="shorts-backend",
    console=True,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="shorts-backend",
    upx=False,
)
