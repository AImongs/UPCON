# -*- mode: python ; coding: utf-8 -*-
r"""UPCON macOS Apple Silicon build.

Includes Real-ESRGAN ncnn/MoltenVK, local AI models, and source-built FFmpeg.
Build: python -m PyInstaller packaging/upcon_macos.spec --noconfirm
Ad-hoc signed development build; no Developer ID or notarization.
"""
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
APP_VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "upcon" / "version.py").read_text(encoding="utf-8")).group(1)

datas = [
    (str(ROOT / "upcon" / "resources" / "styles.qss"), "upcon/resources"),
    # 정보(About) 화면의 "제3자 라이선스 전문". 이 macOS 빌드는 Real-ESRGAN 을 동봉하지 않으므로
    # about_dialog.py 의 THIRD_PARTY_SUMMARY 가 그 사실을 명확히 알리는 macOS 전용 문구를 쓴다
    # (전문 파일 자체는 그대로 참고용으로 동봉. FFmpeg 는 STEP MAC-4 부터 동봉되므로 그 사실은
    # about_dialog.py 쪽에서 FFmpeg 존재 여부와 무관하게 정확한 문구로 갱신했다).
    (str(ROOT / "docs" / "THIRD_PARTY_NOTICES.md"), "docs"),
]

ICNS = ROOT / "upcon" / "resources" / "upcon.icns"
APP_ICON = str(ICNS) if ICNS.is_file() else None
if APP_ICON:
    datas.append((str(ICNS), "upcon/resources"))

# STEP MAC-4B: FFmpeg/ffprobe(arm64) — scripts/build_ffmpeg_macos_arm64.sh 가 빌드해 둔 경우에만
# 포함한다. Windows spec(upcon.spec)이 bin/ 전체를 통째로 datas 에 넣는 것과 같은 방식으로,
# PyInstaller 의 binaries= 의존성 재분석(코드사인/링크 재작성 위험)을 피하기 위해 datas 를 쓴다.
FFMPEG_BIN_DIR = ROOT / "bin-macos-arm64"
if FFMPEG_BIN_DIR.is_dir():
    for f in sorted(FFMPEG_BIN_DIR.iterdir()):
        if f.is_file() and f.name in ("ffmpeg", "ffprobe"):
            datas.append((str(f), "bin"))

datas.append((str(ROOT / "models"), "models"))
datas.append((str(ROOT / "bin" / "realesrgan-ncnn-vulkan"), "bin"))
for license_file in (ROOT / "bin").glob("LICENSE*"):
    datas.append((str(license_file), "bin"))

hiddenimports = [
    "keyring.backends.macOS",
    "fal_client",
]

# Windows 전용 구성요소는 macOS 빌드에 넣지 않는다(애초에 macOS 에 없는 모듈이라 굳이
# excludes 에 적지 않아도 PyInstaller 가 못 찾으면 건너뛰지만, 의도를 명시해 둔다).
excludes = [
    "pytest", "_pytest", "torch", "tkinter", "unittest", "pydoc_data",
    "numpy", "matplotlib", "PIL", "setuptools", "pip", "pyinstaller",
    "pynvml", "win32ctypes", "keyring.backends.Windows",
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

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="UPCON",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
    icon=APP_ICON,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="UPCON")

# BUNDLE() 은 macOS 전용 PyInstaller 단계 — COLLECT 결과를 Info.plist 를 가진 UPCON.app 으로 감싼다.
# Windows spec 에는 이 단계가 없다.
app = BUNDLE(
    coll,
    name="UPCON.app",
    icon=APP_ICON,
    bundle_identifier="com.upcon.app",
    version=APP_VERSION,
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "Copyright (c) 2026 UPCON",
        # Unsigned 개발 빌드임을 스스로 감추지 않는다 — 코드서명/Notarization 은 이번 STEP에서 하지 않는다.
        "LSApplicationCategoryType": "public.app-category.video",
    },
)
