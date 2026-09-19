r"""UPCON Windows Installer 빌드 (Inno Setup 6).

    .\.venv\Scripts\python packaging\build_installer.py

- 버전은 upcon/version.py 한 곳에서 읽어 ISCC 에 /DMyAppVersion 으로 넘긴다.
  (ISCC 를 직접 실행하면 0.0.0-dev 로 표시되어 잘못된 빌드임이 드러난다.)
- 설치 대상은 검증된 Portable 빌드(dist/UPCON)다. 먼저 packaging/upcon.spec 으로 빌드해야 한다.
- 결과: dist-installer/UPCON_Setup_<version>.exe
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISCC_CANDIDATES = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)


def find_iscc() -> Path:
    for p in ISCC_CANDIDATES:
        if p.is_file():
            return p
    raise SystemExit(
        "Inno Setup 6 의 ISCC.exe 를 찾지 못했습니다.\n"
        "https://jrsoftware.org/isdl.php 에서 Inno Setup 6 을 설치하세요."
    )


def main() -> int:
    try:                    # 콘솔이 cp949 여도 (c) 기호 등으로 죽지 않게
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--diag", action="store_true",
                     help="HOTFIX-2 진단용 빌드(libx264 강제 launcher 포함, 파일명에 _DIAG 접미사). "
                          "일반 배포본에는 영향 없음 — upcon.iss 의 #ifdef DIAGBUILD 로 완전히 분리됨.")
    args = ap.parse_args()

    from upcon.version import __version__

    dist = ROOT / "dist" / "UPCON"
    exe = dist / "UPCON.exe"
    if not exe.is_file():
        raise SystemExit(
            f"Portable 빌드가 없습니다: {exe}\n"
            "먼저 실행하세요: python -m PyInstaller packaging/upcon.spec --noconfirm"
        )

    iss = ROOT / "packaging" / "upcon.iss"
    out_dir = ROOT / "dist-installer"
    out_dir.mkdir(exist_ok=True)

    icon = ROOT / "upcon" / "resources" / "upcon.ico"
    print(f"UPCON {__version__}")
    print(f"  설치 원본 : {dist}")
    print(f"  아이콘    : {icon if icon.is_file() else '(없음 - 기본 아이콘으로 빌드)'}")

    from upcon.version import COPYRIGHT
    cmd = [str(find_iscc()), f"/DMyAppVersion={__version__}",
           f"/DMyAppCopyright={COPYRIGHT}"]
    if args.diag:
        cmd.append("/DDIAGBUILD=1")
        print("  모드      : HOTFIX-2 진단 빌드 (libx264 강제 launcher 포함)")
    cmd.append(str(iss))
    print("  ISCC      :", " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(ROOT / "packaging"))
    if r.returncode != 0:
        return r.returncode

    suffix = "_DIAG" if args.diag else ""
    produced = out_dir / f"UPCON_Setup_{__version__}{suffix}.exe"
    if produced.is_file():
        mb = produced.stat().st_size / 1048576
        print(f"\n완료: {produced}  ({mb:,.1f} MB)")
    else:
        print(f"\n경고: 예상 파일이 없습니다: {produced}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
