# -*- mode: python ; coding: utf-8 -*-
r"""UPCON macOS .app — STEP MAC-1 최소 빌드 스펙.

이 스펙은 macOS 에서만 실행할 수 있다(BUNDLE() 자체가 macOS 전용 PyInstaller 단계다).
개발 PC는 Windows 라 여기서 직접 빌드/검증할 수 없다 — GitHub Actions macOS Runner 가 빌드한다.

빌드 (macOS 러너에서):
    python -m PyInstaller packaging/upcon_macos.spec --noconfirm

packaging/upcon.spec(Windows 전용, 정식 배포 스펙)은 이 파일이 존재해도 완전히 그대로다 —
같은 이름의 변수(datas/hiddenimports/excludes/a/exe 등)를 쓰지만 서로 다른 스펙 파일이라
PyInstaller 실행 시 하나만 로드되고 절대 섞이지 않는다.

이번 구성 — "Apple Silicon Cloud-first 앱 (STEP MAC-1) + FFmpeg 동봉 (STEP MAC-4)":
- Real-ESRGAN weight(models/)와 Windows 전용 ncnn 실행파일(bin/*.exe)을 동봉하지 않는다.
  로컬 GPU 업스케일은 이번 STEP에서도 macOS 미지원이다(upcon.platform.local_upscale_unsupported_reason,
  LocalNcnnProvider.check_availability 가 사용자에게 명확히 안내하고 죽지 않는다).
- FFmpeg/FFprobe(arm64, GPL — libx264 하나만 추가한 최소 구성): bin-macos-arm64/ 에
  scripts/build_ffmpeg_macos_arm64.sh 로 "우리가 정확히 아는 FFmpeg n8.1.2 + x264 소스"에서
  직접 빌드해 두면(STEP MAC-4B, 출처/커밋/SHA-256/configure/라이선스/Corresponding Source
  확보 방법은 docs/MACOS_FFMPEG_SOURCE.md 참고 — osxexperts.net 등 제3자 사전 빌드본은 더 이상
  쓰지 않는다) 이 스펙이 자동으로 datas 에 포함해 .app 안에 넣는다 — 단 PyInstaller 의 macOS
  BUNDLE() 단계는 datas 로 넣은 항목이 실제 Mach-O 실행파일이면 "binary vs. data
  reclassification" 으로 Contents/MacOS/ 가 아니라 Contents/Frameworks/ 아래로 재배치한다
  (Apple 앱 번들 관례 — Windows onedir 에는 이 구분이 없다; STEP MAC-4B CI 에서 실측 확인).
  그래서 upcon.core.paths.bundled_bin_dir() 가 macOS 프리즈 빌드에서는 Contents/Frameworks/bin
  도 함께 확인하도록 되어 있다 — upcon.core.binaries.find_binary 의 "동봉 bin/ 우선 → PATH
  폴백" 구조 자체는 그대로다.
  받아두지 않았으면(로컬 개발 중 등) 이전처럼 시스템 PATH(예: Homebrew)로 자동 폴백한다 —
  fetch 를 안 해도 빌드 자체는 깨지지 않는다.
- keyring 백엔드는 macOS Keychain(keyring.backends.macOS)을 명시적으로 포함한다
  (PyInstaller 번들 안에서는 entry-point 자동 탐색이 실패할 수 있어 Windows 와 같은 이유로 필요).
- 아이콘: upcon/resources/upcon.icns (STEP MAC-3, scripts/make_icns.py 로 upcon.png 원본에서
  생성 — 새 디자인이 아니라 기존 브랜드를 그대로 재사용) 를 쓴다. 없으면 PyInstaller 기본
  아이콘으로 빌드된다(Windows spec 의 upcon.ico 패턴과 동일한 '있으면 쓰고 없으면 기본값' 원칙).
- 코드서명은 Developer ID 로는 하지 않는다(Unsigned 개발 빌드, STEP MAC-6 이후 과제).
  단 동봉된 FFmpeg/ffprobe 는 Apple Silicon 실행 자체를 위한 최소 ad-hoc 서명을
  build-macos.yml 이 PyInstaller 빌드 "이후"(.app 안 최종 바이너리에) 적용한다.
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent

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
