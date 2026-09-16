r"""FFmpeg Corresponding Source 패키지 생성 (GPLv3 대응 자료, 개발용).

    .\.venv\Scripts\python scripts\build_ffmpeg_source_package.py

UPCON 이 실제 동봉하는 FFmpeg 바이너리에 대응하는 소스와 빌드 정보를 한 묶음으로 만든다.
결과: dist-ffmpeg-source/UPCON-FFmpeg-Corresponding-Source-<version>.zip

동봉 FFmpeg 는 --enable-gpl --enable-version3 빌드라 GPLv3 가 적용된다. 바이너리를 재배포하는
쪽(= UPCON)이 대응 소스를 제공할 의무를 진다 (GPLv3 6조). BtbN 저장소에는 준수 문구도,
패키징된 소스 아카이브도 없으므로 우리가 직접 모아 둔다.

넣는 것
  - FFmpeg 소스 (정확한 commit)
  - BtbN FFmpeg-Builds 빌드 스크립트 스냅샷 (정확한 commit) - 모든 의존성의 고정 커밋이 여기 있다
  - 바이너리를 GPL 로 만드는 구성요소의 소스 (x264, x265, xvid, vidstab, frei0r, rubberband, zvbi, xavs2)
  - 각 프로젝트의 LICENSE/COPYING
  - BUILD-INFO.txt, SOURCE-MANIFEST.txt

개발용 스크립트이며 UPCON 런타임 의존성이 아니다 (표준 라이브러리만 사용).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

OUT_DIR = ROOT / "dist-ffmpeg-source"

FFMPEG_COMMIT = "1005b294ffdf1b4e75f58d7e98362f82462a6a61"
BTBN_TAG = "autobuild-2026-09-15-13-18"
BTBN_COMMIT = "3e6685eda92f"

GPL_COMPONENTS = ("x264", "x265", "xvid", "vidstab", "frei0r", "rubberband", "zvbi", "xavs2")

LICENSE_NAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "LICENSE.GPL",
                 "COPYING", "COPYING.txt", "COPYING.md", "COPYING.GPL", "COPYING.LGPL",
                 "COPYING.GPLv2", "COPYING.GPLv3", "COPYRIGHT")


def fetch(url: str) -> bytes:
    print(f"    down {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "UPCON-source-packager"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_url(repo: str, rev: str) -> str | None:
    """git 저장소 + 리비전 -> 소스 tarball URL (github / code.videolan.org)."""
    repo = repo.rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if "github.com" in repo:
        owner_repo = repo.split("github.com/", 1)[1]
        return f"https://codeload.github.com/{owner_repo}/tar.gz/{rev}"
    if "code.videolan.org" in repo:
        path = repo.split("code.videolan.org/", 1)[1]
        name = path.rsplit("/", 1)[-1]
        return f"https://code.videolan.org/{path}/-/archive/{rev}/{name}-{rev}.tar.gz"
    return None


def binary_files() -> list[dict]:
    """UPCON 이 실제 동봉하는 FFmpeg 파일 목록과 해시."""
    names = ["ffmpeg.exe", "ffprobe.exe", "avcodec-62.dll", "avdevice-62.dll",
             "avfilter-11.dll", "avformat-62.dll", "avutil-60.dll",
             "swresample-6.dll", "swscale-9.dll"]
    out = []
    for name in names:
        f = ROOT / "bin" / name
        if f.is_file():
            out.append({"name": name, "size": f.stat().st_size, "sha256": sha256(f.read_bytes())})
    return out


def parse_btbn_pins(scripts_zip: bytes) -> dict:
    """BtbN scripts.d 의 의존성 고정값을 모두 뽑는다."""
    z = zipfile.ZipFile(io.BytesIO(scripts_zip))
    pins = {}
    for n in z.namelist():
        if "/scripts.d/" not in n or not n.endswith(".sh"):
            continue
        text = z.read(n).decode("utf-8", "replace")
        entry = {}
        for key in ("SCRIPT_REPO", "SCRIPT_COMMIT", "SCRIPT_TAG", "SCRIPT_BRANCH",
                    "SCRIPT_REV", "SCRIPT_URL"):
            m = re.search(r'^' + key + r'="([^"]+)"', text, re.M)
            if m:
                entry[key] = m.group(1)
        pins[n.split("/scripts.d/")[-1]] = entry
    return pins


def extract_licenses(tar_bytes: bytes, dest: Path) -> list[str]:
    """tar.gz 최상위의 LICENSE/COPYING 만 뽑아 저장."""
    found = []
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        for m in tf.getmembers():
            base = Path(m.name).name
            if m.isfile() and base in LICENSE_NAMES and len(Path(m.name).parts) <= 2:
                fo = tf.extractfile(m)
                if fo is None:
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                (dest / base).write_bytes(fo.read())
                found.append(base)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-work", action="store_true", help="작업 폴더를 지우지 않는다")
    a = ap.parse_args()

    from fetch_binaries import FFMPEG_SHA256, FFMPEG_VERSION, FFMPEG_ZIP

    print("[1] 배포 바이너리와 메타데이터 교차 확인")
    ffmpeg_exe = ROOT / "bin" / "ffmpeg.exe"
    if not ffmpeg_exe.is_file():
        raise SystemExit("bin/ffmpeg.exe 가 없습니다. scripts/fetch_binaries.py --ffmpeg 먼저 실행")
    ver = subprocess.run([str(ffmpeg_exe), "-hide_banner", "-version"],
                         capture_output=True, text=True, errors="replace").stdout
    first = ver.splitlines()[0]
    if FFMPEG_VERSION not in first:
        raise SystemExit("버전 불일치: binary=" + first + " / 고정값=" + FFMPEG_VERSION)
    cfg = next((l for l in ver.splitlines() if l.startswith("configuration:")), "")
    built_with = next((l for l in ver.splitlines() if l.startswith("built with")), "")
    if "--enable-gpl" not in cfg:
        raise SystemExit("GPL 빌드가 아닙니다.")
    print("    OK " + first)

    work = OUT_DIR / ("UPCON-FFmpeg-Corresponding-Source-" + FFMPEG_VERSION)
    if work.exists():
        shutil.rmtree(work)
    (work / "sources").mkdir(parents=True)
    (work / "licenses").mkdir(parents=True)
    src_dir, lic_dir = work / "sources", work / "licenses"
    manifest = []

    print("[2] FFmpeg 소스")
    ff = fetch("https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/" + FFMPEG_COMMIT)
    ff_name = "ffmpeg-" + FFMPEG_COMMIT[:12] + ".tar.gz"
    (src_dir / ff_name).write_bytes(ff)
    extract_licenses(ff, lic_dir / "ffmpeg")
    manifest.append({"project": "FFmpeg", "version": FFMPEG_VERSION, "commit": FFMPEG_COMMIT,
                     "upstream": "https://git.ffmpeg.org/ffmpeg.git (mirror: github.com/FFmpeg/FFmpeg)",
                     "license": "GPL-3.0-or-later (this build: --enable-gpl --enable-version3)",
                     "archive": "sources/" + ff_name, "size": len(ff), "sha256": sha256(ff)})

    print("[3] BtbN 빌드 스크립트 스냅샷")
    btbn = fetch("https://codeload.github.com/BtbN/FFmpeg-Builds/zip/" + BTBN_COMMIT)
    btbn_name = "FFmpeg-Builds-" + BTBN_COMMIT + ".zip"
    (src_dir / btbn_name).write_bytes(btbn)
    manifest.append({"project": "BtbN/FFmpeg-Builds (build scripts)", "version": BTBN_TAG,
                     "commit": BTBN_COMMIT, "upstream": "https://github.com/BtbN/FFmpeg-Builds",
                     "license": "MIT", "archive": "sources/" + btbn_name,
                     "size": len(btbn), "sha256": sha256(btbn)})
    pins = parse_btbn_pins(btbn)
    print("    고정된 의존성 스크립트 " + str(len(pins)) + "개")

    print("[4] GPL 구성요소 소스")
    unfetched = []
    for comp in GPL_COMPONENTS:
        key = next((k for k in pins if Path(k).stem.split("-", 1)[-1] == comp), None)
        if key is None:
            key = next((k for k in pins if comp in k.lower()), None)
        if key is None:
            unfetched.append({"project": comp, "reason": "BtbN scripts.d 에서 찾지 못함"})
            continue
        p = pins[key]
        repo = p.get("SCRIPT_REPO", "")
        rev = p.get("SCRIPT_COMMIT") or p.get("SCRIPT_TAG") or p.get("SCRIPT_REV") or ""
        url = archive_url(repo, rev) if (p.get("SCRIPT_COMMIT") or p.get("SCRIPT_TAG")) else None
        if not url:
            unfetched.append({"project": comp, "repo": repo, "rev": rev, "script": key,
                              "reason": "git tarball 없음 (SVN 등) - 수동 체크아웃 필요"})
            print("    SKIP " + comp + "  " + repo + "@" + rev)
            continue
        try:
            data = fetch(url)
        except Exception as e:
            unfetched.append({"project": comp, "repo": repo, "rev": rev, "script": key,
                              "reason": "다운로드 실패: " + type(e).__name__})
            print("    FAIL " + comp + ": " + str(e))
            continue
        name = comp + "-" + rev[:12] + ".tar.gz"
        (src_dir / name).write_bytes(data)
        extract_licenses(data, lic_dir / comp)
        manifest.append({"project": comp, "version": rev, "commit": rev, "upstream": repo,
                         "license": "GPL (see licenses/)", "archive": "sources/" + name,
                         "size": len(data), "sha256": sha256(data), "btbn_script": key})
        print("    OK   " + comp.ljust(11) + str(round(len(data) / 1048576, 1)) + " MB")
    return _finish(work, src_dir, lic_dir, manifest, pins, unfetched, a,
                   FFMPEG_VERSION, FFMPEG_ZIP, FFMPEG_SHA256, first, cfg, built_with)


def _finish(work, src_dir, lic_dir, manifest, pins, unfetched, a,
            FFMPEG_VERSION, FFMPEG_ZIP, FFMPEG_SHA256, first, cfg, built_with) -> int:
    print("[5] BUILD-INFO.txt / SOURCE-MANIFEST.txt")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    L = ["UPCON - FFmpeg Corresponding Source (GPLv3)", "=" * 78, "",
         "Generated              : " + now, "",
         "-- Binary shipped by UPCON --",
         "FFmpeg version         : " + FFMPEG_VERSION,
         "Version string         : " + first,
         "FFmpeg upstream commit : " + FFMPEG_COMMIT,
         "Build provider         : BtbN/FFmpeg-Builds",
         "Build tag              : " + BTBN_TAG,
         "Build scripts commit   : " + BTBN_COMMIT,
         "Variant                : win64-gpl-shared (GPL, shared libraries)",
         "Original archive       : ffmpeg-" + FFMPEG_VERSION + "-win64-gpl-shared-8.1.zip",
         "Original archive URL   : " + FFMPEG_ZIP,
         "Original archive SHA256: " + FFMPEG_SHA256, "",
         "Toolchain              : " + built_with, "",
         "-- configure --", cfg.replace("configuration: ", ""), "",
         "-- Files shipped in UPCON (bin/) --"]
    for f in binary_files():
        L.append("  " + format(f["size"], ",").rjust(12) + "  " + f["sha256"] + "  " + f["name"])
    L += ["", "-- License --",
          "This FFmpeg build is configured with --enable-gpl --enable-version3,",
          "therefore the GNU General Public License version 3 (or later) applies.",
          "It is NOT built with --enable-nonfree, so redistribution is permitted",
          "provided the GPL obligations are met.", "",
          "GPL-licensed components linked into this build:",
          "  " + ", ".join(GPL_COMPONENTS), "",
          "-- Scope --",
          "sources/ contains the FFmpeg source at the exact commit, the BtbN build",
          "scripts at the exact commit, and the source of the GPL components above.",
          "The BtbN snapshot pins " + str(len(pins)) + " dependency scripts in total; each records its",
          "upstream repository and exact revision, so any remaining component can be",
          "retrieved reproducibly. See SOURCE-MANIFEST.txt."]
    if unfetched:
        L += ["", "-- Components NOT auto-downloaded --"]
        for u in unfetched:
            L.append("  " + u["project"] + ": " + u.get("repo", "") + "@" + u.get("rev", "") +
                     "  -- " + u["reason"])
        for u in unfetched:
            if u["project"] == "xvid" and u.get("rev"):
                L += ["  xvid uses Subversion (no git tarball). Fetch the exact revision with:",
                      "    svn checkout " + u.get("repo", "") + "@" + u["rev"] + " xvid",
                      "  A Subversion client is required; release tarballs from downloads.xvid.com",
                      "  are DIFFERENT revisions and do NOT correspond to this binary."]
    (work / "BUILD-INFO.txt").write_text("\n".join(L) + "\n", encoding="utf-8")

    M = ["UPCON - FFmpeg Corresponding Source : MANIFEST", "=" * 78, ""]
    for m in manifest:
        M += ["project   : " + m["project"],
              "version   : " + str(m["version"]),
              "commit    : " + str(m.get("commit", "")),
              "upstream  : " + m["upstream"],
              "license   : " + m["license"],
              "archive   : " + m["archive"],
              "size      : " + format(m["size"], ",") + " bytes",
              "sha256    : " + m["sha256"], ""]
    M += ["-- All dependency pins from the BtbN build scripts snapshot --",
          "(script : upstream @ pinned revision)", ""]
    for name in sorted(pins):
        p = pins[name]
        repo = p.get("SCRIPT_REPO") or p.get("SCRIPT_URL") or "(none)"
        rev = (p.get("SCRIPT_COMMIT") or p.get("SCRIPT_TAG") or p.get("SCRIPT_REV")
               or p.get("SCRIPT_BRANCH") or "")
        M.append("  " + Path(name).stem.ljust(34) + " " + repo + " @ " + rev)
    (work / "SOURCE-MANIFEST.txt").write_text("\n".join(M) + "\n", encoding="utf-8")
    (work / "btbn-dependency-pins.json").write_text(json.dumps(pins, indent=1), encoding="utf-8")

    print("[6] ZIP 생성")
    zip_path = OUT_DIR / (work.name + ".zip")
    if zip_path.exists():
        zip_path.unlink()
    total_raw = 0
    nfiles = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, work.name + "/" + f.relative_to(work).as_posix())
                total_raw += f.stat().st_size
                nfiles += 1
    zsha = sha256(zip_path.read_bytes())
    if not a.keep_work:
        shutil.rmtree(work)

    print("")
    print("파일명       : " + zip_path.name)
    print("압축 크기    : " + format(zip_path.stat().st_size, ",") + " bytes (" +
          str(round(zip_path.stat().st_size / 1048576, 1)) + " MB)")
    print("해제 크기    : " + format(total_raw, ",") + " bytes (" +
          str(round(total_raw / 1048576, 1)) + " MB)")
    print("파일 수      : " + str(nfiles))
    print("SHA-256      : " + zsha)
    print("component 수 : " + str(len(manifest)) + "  (자동 미수집 " + str(len(unfetched)) + ")")
    print("위치         : " + str(zip_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
