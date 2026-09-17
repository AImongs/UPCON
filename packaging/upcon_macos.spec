# -*- mode: python ; coding: utf-8 -*-
r"""UPCON macOS .app — STEP MAC-1 최소 빌드 스펙.

이 스펙은 macOS 에서만 실행할 수 있다(BUNDLE() 자체가 macOS 전용 PyInstaller 단계다).
개발 PC는 Windows 라 여기서 직접 빌드/검증할 수 없다 — GitHub Actions macOS Runner 가 빌드한다.

빌드 (macOS 러너에서):
    python -m PyInstaller packaging/upcon_macos.spec --noconfirm

packaging/upcon.spec(Windows 전용, 정식 배포 스펙)은 이 파일이 존재해도 완전히 그대로다 —
같은 이름의 변수(datas/hiddenimports/excludes/a/exe 등)를 쓰지만 서로 다른 스펙 파일이라
PyInstaller 실행 시 하나만 로드되고 절대 섞이지 않는다.

이번 STEP(MAC-1) 구성 — "먼저 앱 실행 + FFmpeg + Cloud Provider가 가능한 macOS 기반":
- Real-ESRGAN weight(models/)와 Windows 전용 FFmpeg/ncnn 실행파일(bin/, .exe)을 동봉하지 않는다.
  로컬 GPU 업스케일은 이번 STEP에서 macOS 미지원이다(upcon.platform.local_upscale_unsupported_reason,
  LocalNcnnProvider.check_availability 가 사용자에게 명확히 안내하고 죽지 않는다).
- FFmpeg/FFprobe 는 이 macOS 빌드에서는 동봉하지 않고 시스템 PATH(예: Homebrew)에서 찾는다
  (upcon.core.binaries.find_binary 의 기존 PATH 폴백을 그대로 쓴다 — 새 코드 아님).
  자체 동봉(그리고 필요한 라이선스/Corresponding Source 의무)은 이번 STEP 범위 밖이며,
  실제로 동봉하게 되면 별도로 라이선스 의무를 조사해야 한다(STEP MAC-1 보고서 참고).
- keyring 백엔드는 macOS Keychain(keyring.backends.macOS)을 명시적으로 포함한다
  (PyInstaller 번들 안에서는 entry-point 자동 탐색이 실패할 수 있어 Windows 와 같은 이유로 필요).
- 아이콘: upcon/resources/upcon.icns 가 있으면 쓰고, 없으면 PyInstaller 기본 아이콘으로 빌드된다
  (Windows spec 의 upcon.ico 패턴과 동일한 '있으면 쓰고 없으면 기본값' 원칙).
- 코드서명/Notarization 은 하지 않는다. Unsigned 개발 빌드다.
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "upcon" / "resources" / "styles.qss"), "upcon/resources"),
    # 정보(About) 화면의 "제3자 라이선스 전문". 이 macOS 빌드는 FFmpeg/Real-ESRGAN 을 동봉하지
    # 않으므로 about_dialog.py 의 THIRD_PARTY_SUMMARY 가 그 사실을 명확히 알리는 macOS 전용
    # 문구를 쓴다(전문 파일 자체는 그대로 참고용으로 동봉).
    (str(ROOT / "docs" / "THIRD_PARTY_NOTICES.md"), "docs"),
]

ICNS = ROOT / "upcon" / "resources" / "upcon.icns"
APP_ICON = str(ICNS) if ICNS.is_file() else None
if APP_ICON:
    datas.append((str(ICNS), "upcon/resources"))

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
    info_plist={
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "Copyright (c) 2026 UPCON",
        # Unsigned 개발 빌드임을 스스로 감추지 않는다 — 코드서명/Notarization 은 이번 STEP에서 하지 않는다.
        "LSApplicationCategoryType": "public.app-category.video",
    },
)
