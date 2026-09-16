# -*- mode: python ; coding: utf-8 -*-
r"""UPCON Portable (onedir) — 정식 배포 스펙.

빌드:
    .\.venv\Scripts\python -m PyInstaller packaging\upcon.spec --noconfirm

구성 (STEP 7-2 에서 검증·확정)
- onedir. onefile 은 쓰지 않는다 (실행마다 수백 MB 를 임시 폴더에 풀어야 하고 문제 추적이 어렵다).
- bin/ 은 scripts/fetch_binaries.py --ffmpeg 가 구성한다:
  FFmpeg n8.1.2 GPL **shared** (ffmpeg/ffprobe + 공유 DLL 7개, ffplay 제외) + realesrgan-ncnn-vulkan.
  static 대비 bin 317MB -> 196MB 이며 기능은 동일함을 능력 검사로 확인했다.
- 동봉 파일은 datas 로 넣어 upcon/core/paths.py 의 project_root()(= sys._MEIPASS) 아래
  bin/ · models/ · upcon/resources/ 에 놓는다.
- 사용자 데이터(config.json, queue.json, logs, tmp)는 번들이 아니라 %LOCALAPPDATA%\UPCON 에 쓴다.
  API Key 는 Windows 자격 증명 관리자(keyring)에만 저장한다.
- UPCON 이 쓰지 않는 Qt 구성요소(QtQuick/QML, QtPdf, qdirect2d)와 한국어/영어 외 번역을 제외한다.
  opengl32sw.dll(소프트웨어 OpenGL 폴백)은 원격 데스크톱/VM 대비로 유지한다.
- PyInstaller 가 bin/ 의 FFmpeg DLL 을 _internal 루트에 중복 복사하는 것을 제거한다.
- packaging/selftest.py 는 개발·검증 전용이며 이 스펙(배포본)에는 포함되지 않는다.
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent
BIN_DIR = ROOT / "bin"

datas = [
    (str(ROOT / "upcon" / "resources" / "styles.qss"), "upcon/resources"),
    # 정보(About) 화면의 "제3자 라이선스 전문" 이 번들 안에서 직접 읽는다.
    (str(ROOT / "docs" / "THIRD_PARTY_NOTICES.md"), "docs"),
]

# 앱 아이콘: 아직 최종 디자인이 없다. upcon/resources/upcon.ico 를 넣으면
# 실행 파일 아이콘과 창 아이콘에 자동으로 쓰이고, 향후 Inno Setup 도 같은 파일을 참조한다.
# 파일이 없으면 icon=None 으로 기본 아이콘으로 정상 빌드된다.
ICON = ROOT / "upcon" / "resources" / "upcon.ico"
APP_ICON = str(ICON) if ICON.is_file() else None
if APP_ICON:
    datas.append((str(ICON), "upcon/resources"))
for f in sorted(BIN_DIR.iterdir()):
    if f.is_file():
        datas.append((str(f), "bin"))
for f in sorted((ROOT / "models").iterdir()):
    if f.is_file():
        datas.append((str(f), "models"))

hiddenimports = [
    "keyring.backends.Windows",
    "win32ctypes.core",
    "win32ctypes.core.ctypes",
    "win32ctypes.core.ctypes._authentication",
    "win32ctypes.core.ctypes._common",
    "win32ctypes.core.ctypes._dll",
    "win32ctypes.core.ctypes._nl_support",
    "win32ctypes.core.ctypes._resource",
    "win32ctypes.core.ctypes._system_information",
    "win32ctypes.core.ctypes._time",
    "win32ctypes.core.ctypes._util",
    "win32ctypes.pywin32.win32cred",
    "win32ctypes.pywin32.win32api",
    "pynvml",
    "fal_client",
]

excludes = [
    "pytest", "_pytest", "torch", "tkinter", "unittest", "pydoc_data",
    "numpy", "matplotlib", "PIL", "setuptools", "pip", "pyinstaller",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtTextToSpeech", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
]

a = Analysis(
    [str(ROOT / "upcon" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# ---- Qt 군더더기 제거 (PyInstaller Qt 훅이 넣은 것 중 UPCON 이 안 쓰는 것) ----
DROP_FILES = {
    "qt6quick.dll", "qt6qml.dll", "qt6qmlmodels.dll", "qt6qmlmeta.dll",
    "qt6qmlworkerscript.dll", "qt6quickparticles.dll", "qt6quickshapes.dll",
    "qt6quicktemplates2.dll", "qt6quickcontrols2.dll",
    "qt6pdf.dll", "qdirect2d.dll",
}
KEEP_LANGS = ("_ko.qm", "_en.qm")

# PyInstaller 가 bin/ 의 FFmpeg DLL 을 의존성으로 한 번 더 _internal 루트에 복사한다.
# ffmpeg.exe/ffprobe.exe 는 자기 폴더(bin/)에서 DLL 을 찾으므로 루트 사본은 순수 중복이다.
FFMPEG_DLLS = {f.name.lower() for f in BIN_DIR.iterdir() if f.suffix.lower() == ".dll"}
_removed = []
_dedup = []

def _keep(entry):
    dest = str(entry[0]).replace("\\", "/")
    name = dest.rsplit("/", 1)[-1].lower()
    if name in FFMPEG_DLLS and not dest.lower().startswith("bin/"):
        _dedup.append(dest)
        return False
    if name in DROP_FILES:
        _removed.append(dest)
        return False
    if "/translations/" in dest.lower() and name.endswith(".qm"):
        if not any(name.endswith(s) for s in KEEP_LANGS):
            _removed.append(dest)
            return False
    return True

a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
print("[upcon] Qt 제외 %d 개 / FFmpeg DLL 중복 제거 %d 개"
      % (len(_removed), len(_dedup)))

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="UPCON",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    icon=APP_ICON,             # upcon/resources/upcon.ico 가 있으면 사용, 없으면 기본 아이콘
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="UPCON")
