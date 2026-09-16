r"""FFmpeg Corresponding Source 패키지 생성 (GPLv3 대응 자료, 개발용).

    .\.venv\Scripts\python scripts\build_ffmpeg_source_package.py

UPCON 이 동봉하는 FFmpeg 바이너리에 대응하는 소스/빌드 자료를 완전성 우선으로 모은다.
결과: dist-ffmpeg-source/UPCON-FFmpeg-Corresponding-Source-<version>.zip

분류 (BtbN 빌드 시스템의 실제 판정 로직을 우리 빌드 조건으로 평가해서 나눈다)
  A  이 바이너리에 실제로 컴파일/정적 링크되는 구성요소  -> 소스 수집 (필수)
  B  System Library 로 판단해 제외한 것                -> SYSTEM-LIBRARIES.txt 에 근거 기록
  C  빌드 도구/환경 (링크되는 코드 없음)                -> 스냅샷으로 보존
  D  이 빌드와 무관 (다른 타깃/변형에서만 쓰임)          -> 제외 + 사유 기록

애매하면 보수적으로 A 로 둔다. 확신 없는 것을 임의로 빼지 않는다.

완전성 검사 (하나라도 어긋나면 **실패**하고 아카이브를 만들지 않는다)
  - 필수 component 누락
  - 리비전 불일치 / 미고정
  - 라이선스 파일 누락
  - 수집 실패
  - BtbN 스냅샷에 patches/ 또는 scripts.d/ 누락
  - 바이너리 버전과 고정 메타데이터 불일치

개발용이며 UPCON 런타임 의존성이 아니다 (표준 라이브러리 + git/CLI 만 사용).
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
OUT_DIR = ROOT / "dist-ffmpeg-source"

# --- 이 바이너리의 신원 (scripts/fetch_binaries.py 와 일치해야 한다) ---
FFMPEG_COMMIT = "1005b294ffdf1b4e75f58d7e98362f82462a6a61"
BTBN_TAG = "autobuild-2026-09-15-13-18"
BTBN_COMMIT = "3e6685eda92f"

# BtbN 빌드 조건 (win64-gpl-shared, FFmpeg 8.1)
TARGET, VARIANT, FFVER = "win64", "gpl-shared", 801

# 링크되는 코드가 없는 빌드 도구/환경 스크립트 -> category C
BUILD_TOOLING = {
    "15-base.sh", "zz-final.sh", "55-rustdedup.sh",
    "47-vulkan/99-enable.sh", "50-vaapi/99-finalize.sh", "45-x11/99-finalize.sh",
}
# System Library 로 보아 소스를 넣지 않는 것 -> category B (근거는 SYSTEM-LIBRARIES.txt)
SYSTEM_LIBRARY_SCRIPTS = {"50-schannel.sh"}

# 이 빌드를 GPL 로 만드는 구성요소 (수집 실패 시 무조건 실패로 처리)
GPL_COMPONENTS = ("x264", "x265", "xvid", "vidstab", "frei0r", "rubberband", "zvbi", "xavs2")

# 업스트림에 '별도 라이선스 파일'이 아예 없는 component.
# 임의로 봐주는 것이 아니라, 실제 소스를 확인한 근거를 함께 기록한다.
LICENSE_IN_SOURCE = {
    "ffnvcodec": ("nv-codec-headers 는 별도 라이선스 파일이 없다. MIT 형식 고지가 각 헤더 "
                  "상단 주석에 들어 있다 (include/ffnvcodec/*.h: \"This copyright notice "
                  "applies to this header file only ... Permission is hereby granted, free "
                  "of charge ...\"). 소스 아카이브 자체에 고지가 포함돼 있다."),
}

LICENSE_NAMES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "LICENSE.GPL", "LICENSE.rst",
                 "COPYING", "COPYING.txt", "COPYING.md", "COPYING.GPL", "COPYING.LGPL",
                 "COPYING.GPLv2", "COPYING.GPLv3", "COPYRIGHT", "COPYRIGHT.txt", "NOTICE")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _retry(fn, what: str, tries: int = 4):
    """일시적인 네트워크 오류로 수집이 실패하지 않게 재시도한다."""
    import time
    last = None
    for n in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:      # noqa: BLE001
            last = e
            if n < tries:
                time.sleep(2 * n)
    raise RuntimeError(f"{what}: {type(last).__name__}: {last}")


def fetch(url: str, timeout: int = 900) -> bytes:
    cache = OUT_DIR / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    cf = cache / (hashlib.sha1(url.encode()).hexdigest() + ".bin")
    if cf.is_file() and cf.stat().st_size > 0:
        return cf.read_bytes()

    def once():
        req = urllib.request.Request(url, headers={"User-Agent": "UPCON-source-packager"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    data = _retry(once, "fetch " + url)
    cf.write_bytes(data)
    return data


def tarball_url(repo: str, rev: str) -> str | None:
    """호스트별 소스 tarball URL. 지원하지 않으면 None (git clone 으로 폴백)."""
    repo = repo.rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if "github.com" in repo:
        return "https://codeload.github.com/" + repo.split("github.com/", 1)[1] + "/tar.gz/" + rev
    for host in ("code.videolan.org", "gitlab.freedesktop.org", "gitlab.com"):
        if host in repo:                                   # GitLab
            path = repo.split(host + "/", 1)[1]
            name = path.rsplit("/", 1)[-1]
            return f"https://{host}/{path}/-/archive/{rev}/{name}-{rev}.tar.gz"
    if "googlesource.com" in repo:                         # Gitiles
        return repo + "/+archive/" + rev + ".tar.gz"
    if "git.savannah.gnu.org" in repo:                     # cgit
        proj = repo.split("/")[-1]
        return f"https://git.savannah.gnu.org/cgit/{proj}.git/snapshot/{rev}.tar.gz"
    return None


def _rmtree(path: Path) -> None:
    """Windows 에서 git 오브젝트는 읽기 전용이라 그냥 지우면 실패한다."""
    import stat

    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    if path.exists():
        shutil.rmtree(path, onerror=onerror)


def git_archive(repo: str, rev: str, work: Path) -> bytes:
    """tarball URL 이 없는 호스트용: 얕은 clone 후 tar 로 묶는다."""
    tmp = work / ("git-" + hashlib.sha1((repo + rev).encode()).hexdigest()[:10])
    _rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True, env=env)
    subprocess.run(["git", "remote", "add", "origin", repo], cwd=tmp, check=True, env=env)
    try:
        subprocess.run(["git", "fetch", "-q", "--depth", "1", "origin", rev],
                       cwd=tmp, check=True, env=env, timeout=1800)
    except subprocess.CalledProcessError:
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=tmp, check=True, env=env, timeout=3600)
    out = subprocess.run(["git", "archive", "--format=tar", rev if rev else "FETCH_HEAD"],
                         cwd=tmp, capture_output=True, env=env)
    if out.returncode != 0:
        out = subprocess.run(["git", "archive", "--format=tar", "FETCH_HEAD"],
                             cwd=tmp, capture_output=True, env=env, check=True)
    import gzip
    data = gzip.compress(out.stdout)
    _rmtree(tmp)
    return data


# ---------------------------------------------------------------- SVN (WebDAV)
def _svn_req(url: str, method: str = "GET", depth: str | None = None, body: bytes | None = None):
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("Authorization", "Basic " + base64.b64encode(b"anonymous:").decode())
    req.add_header("User-Agent", "UPCON-source-packager")
    if depth is not None:
        req.add_header("Depth", depth)
        req.add_header("Content-Type", "text/xml")
    return urllib.request.urlopen(req, timeout=300)


def _svn_retry(url: str, method: str = "GET", depth: str | None = None, body=None):
    return _retry(lambda: _svn_req(url, method, depth, body), "svn " + method + " " + url)


def svn_export(base: str, rev: str) -> bytes:
    """SVN 저장소를 지정 리비전으로 export 해 tar.gz 로 만든다 (svn 클라이언트 불필요).

    HTTP(DAV) 의 baseline-collection 경로 /!svn/bc/<rev>/<path> 를 쓰면 그 리비전의
    트리를 그대로 읽을 수 있다. BtbN 스크립트와 동일하게 anonymous 로 인증한다.
    """
    m = re.match(r"(https?://[^/]+)(/.*)", base.rstrip("/"))
    if not m:
        raise RuntimeError("SVN URL 형식을 이해하지 못했습니다: " + base)
    host, path = m.group(1), m.group(2)
    # 저장소 루트가 호스트 루트가 아닐 수 있다 (예: SourceForge 는 /p/<proj>/svn/ 가 루트).
    # !svn/bc/<rev>/ 를 넣을 위치를 앞에서부터 실제로 시험해 본다.
    parts = [x for x in path.strip("/").split("/") if x]
    root = None
    for i in range(len(parts) + 1):
        cand = host + "".join("/" + x for x in parts[:i]) +             "/!svn/bc/" + str(rev) + "".join("/" + x for x in parts[i:])
        try:
            _svn_req(cand + "/", "PROPFIND", depth="0",
                     body=b'<?xml version="1.0" encoding="utf-8"?>'
                          b'<D:propfind xmlns:D="DAV:"><D:prop><D:resourcetype/></D:prop></D:propfind>')
            root = cand
            break
        except Exception:
            continue
    if root is None:
        raise RuntimeError("SVN baseline-collection 경로를 찾지 못했습니다: " + base)
    path = "/" + "/".join(parts)
    propfind = (b'<?xml version="1.0" encoding="utf-8"?>'
                b'<D:propfind xmlns:D="DAV:"><D:prop><D:resourcetype/></D:prop></D:propfind>')
    ns = {"d": "DAV:"}
    buf = io.BytesIO()
    top = path.strip("/").split("/")[-1] + "-r" + str(rev)
    count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        stack = [root]
        while stack:
            cur = stack.pop()
            r = _svn_retry(cur + "/", "PROPFIND", depth="1", body=propfind)
            tree = ET.fromstring(r.read())
            for resp in tree.findall("d:response", ns):
                href = resp.find("d:href", ns).text
                if href.rstrip("/") == cur.split(host)[-1].rstrip("/"):
                    continue
                rt = resp.find(".//d:resourcetype", ns)
                isdir = rt is not None and rt.find("d:collection", ns) is not None
                full = host + href
                if isdir:
                    stack.append(full.rstrip("/"))
                else:
                    data = _svn_retry(full).read()
                    # root 의 경로 부분을 그대로 떼어내야 최상위가 깔끔해진다
                    root_path = root[len(host):].rstrip("/")
                    rel = href[len(root_path):].lstrip("/") if href.startswith(root_path)                         else href.split("/!svn/bc/" + str(rev) + "/", 1)[-1]
                    info = tarfile.TarInfo(top + "/" + rel)
                    info.size = len(data)
                    info.mtime = 0
                    tf.addfile(info, io.BytesIO(data))
                    count += 1
    if count == 0:
        raise RuntimeError("SVN export 결과가 비어 있습니다: " + base)
    return buf.getvalue()


# ---------------------------------------------------------------- 분류
def eval_enabled(body: str) -> tuple[bool, str]:
    """BtbN 의 ffbuild_enabled() 를 우리 빌드 조건(win64 / gpl-shared / ffver 801)으로 평가."""
    if not body:
        return True, "ffbuild_enabled 없음 -> 기본 활성"
    for raw in re.split(r"(?<=return -1)\s+|(?<=return 1)\s+|(?<=return 0)\s+", body):
        s = raw.strip()
        if not s:
            continue
        if s == "return 0":
            return True, "return 0"
        if s in ("return -1", "return 1"):
            return False, "무조건 비활성"
        m = re.match(r"\[\[ \$TARGET (!=|==) ([^\]]+) \]\] (&&|\|\|) return -?1", s)
        if m:
            op, pat, conn = m.group(1), m.group(2).strip(), m.group(3)
            match = fnmatch.fnmatch(TARGET, pat)
            cond = match if op == "==" else (not match)
            if conn == "&&" and cond:
                return False, f"TARGET {op} {pat} -> 비활성"
            if conn == "||" and not cond:
                return False, f"TARGET {op} {pat} 불만족 -> 비활성"
            continue
        m = re.match(r"\[\[ \$VARIANT == ([^\]]+) \]\] (&&|\|\|) return -?1", s)
        if m:
            pat, conn = m.group(1).strip(), m.group(2)
            match = fnmatch.fnmatch(VARIANT, pat)
            if conn == "&&" and match:
                return False, f"VARIANT == {pat} -> 비활성"
            if conn == "||" and not match:
                return False, f"VARIANT != {pat} -> 비활성"
            continue
        m = re.match(r"\(\( \$\(ffbuild_ffver\) (>=|>|<=|<) (\d+) \)\) (\|\||&&) return -?1", s)
        if m:
            op, val, conn = m.group(1), int(m.group(2)), m.group(3)
            ok = {">=": FFVER >= val, ">": FFVER > val, "<=": FFVER <= val, "<": FFVER < val}[op]
            if conn == "||" and not ok:
                return False, f"ffver {op} {val} 불만족 -> 비활성"
            if conn == "&&" and ok:
                return False, f"ffver {op} {val} 만족 -> 비활성"
            continue
    return True, "조건 통과"


def classify(scripts: dict[str, str]) -> dict[str, dict]:
    out = {}
    for name, text in scripts.items():
        m = re.search(r"ffbuild_enabled\(\)\s*\{(.*?)\n\}", text, re.S)
        body = " ".join(l.strip() for l in (m.group(1).strip().splitlines() if m else [])
                        if l.strip() and not l.strip().startswith("#"))
        en, why = eval_enabled(body)
        repo = re.search(r'^SCRIPT_REPO="([^"]+)"', text, re.M)
        url = re.search(r'^SCRIPT_URL="([^"]+)"', text, re.M)
        rev = re.search(r'^SCRIPT_(COMMIT|TAG|REV|BRANCH)="([^"]+)"', text, re.M)
        loc = (repo or url).group(1) if (repo or url) else ""
        info = {"enabled": en, "reason": why, "repo": loc,
                "rev_kind": rev.group(1) if rev else "", "rev": rev.group(2) if rev else "",
                "is_svn": bool(repo and repo.group(1).startswith("svn") or "svn." in loc),
                "has_source": bool(loc)}
        if not en:
            info["category"] = "D"
            info["category_reason"] = "이 빌드에서 비활성: " + why
        elif name in SYSTEM_LIBRARY_SCRIPTS:
            info["category"] = "B"
            info["category_reason"] = "운영체제 제공 구성요소 (SYSTEM-LIBRARIES.txt 참조)"
        elif name in BUILD_TOOLING or not loc:
            info["category"] = "C"
            info["category_reason"] = "빌드 도구/환경 스크립트 (링크되는 코드 없음)"
        else:
            info["category"] = "A"
            info["category_reason"] = "이 빌드에서 활성 -> 바이너리에 컴파일/링크됨"
        out[name] = info
    return out


def pe_imports(path: Path) -> list[str]:
    """PE import 테이블에서 이 바이너리가 참조하는 DLL 이름을 뽑는다."""
    d = path.read_bytes()
    pe = int.from_bytes(d[0x3C:0x40], "little")
    nsec = int.from_bytes(d[pe + 6:pe + 8], "little")
    optsz = int.from_bytes(d[pe + 20:pe + 22], "little")
    magic = int.from_bytes(d[pe + 24:pe + 26], "little")
    dd = pe + 24 + (112 if magic == 0x20B else 96)
    imp_rva = int.from_bytes(d[dd + 8:dd + 12], "little")
    secs = []
    for i in range(nsec):
        s = pe + 24 + optsz + 40 * i
        vsz = int.from_bytes(d[s + 8:s + 12], "little")
        va = int.from_bytes(d[s + 12:s + 16], "little")
        rawsz = int.from_bytes(d[s + 16:s + 20], "little")
        raw = int.from_bytes(d[s + 20:s + 24], "little")
        secs.append((va, max(vsz, rawsz), raw))

    def r2o(rva):
        for va, sz, raw in secs:
            if va <= rva < va + sz:
                return raw + (rva - va)
        return None

    out, off = [], r2o(imp_rva)
    if off is None:
        return out
    while True:
        ent = d[off:off + 20]
        if len(ent) < 20 or ent == b"\0" * 20:
            break
        nr = int.from_bytes(ent[12:16], "little")
        if nr == 0:
            break
        no = r2o(nr)
        if no is None:
            break
        out.append(d[no:d.index(b"\0", no)].decode("ascii", "replace"))
        off += 20
    return sorted(set(out))


def extract_licenses(tar_bytes: bytes, dest: Path) -> list[str]:
    found = []
    try:
        tf = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz")
    except tarfile.TarError:
        return found
    with tf:
        for m in tf.getmembers():
            base = Path(m.name).name
            up = base.upper()
            looks_license = (up in [n.upper() for n in LICENSE_NAMES]
                             or up.startswith(("LICENSE", "LICENCE", "COPYING", "COPYRIGHT",
                                               "NOTICE", "GPL", "LGPL")))
            if m.isfile() and looks_license and len(Path(m.name).parts) <= 3:
                fo = tf.extractfile(m)
                if fo is None:
                    continue
                dest.mkdir(parents=True, exist_ok=True)
                (dest / base).write_bytes(fo.read())
                found.append(base)
    return found


def collect_component(name: str, info: dict, src_dir: Path, lic_dir: Path,
                      work: Path) -> tuple[dict | None, str | None]:
    """A 분류 component 하나의 소스를 수집. (manifest entry, error) 반환."""
    stem = Path(name).stem
    comp = re.sub(r"^\d+-", "", stem)
    repo, rev = info["repo"], info["rev"]
    if not rev:
        return None, f"{comp}: 리비전이 고정돼 있지 않음"
    data = None
    how = ""
    if info["is_svn"]:
        try:
            data, how = svn_export(repo, rev), f"svn export @r{rev} (WebDAV)"
        except Exception as e:
            return None, f"{comp}: SVN export 실패 {type(e).__name__}: {e}"
    else:
        url = tarball_url(repo, rev)
        if url:
            try:
                data, how = fetch(url), "tarball"
            except Exception:
                data = None
        if data is None:
            try:
                data, how = git_archive(repo, rev, work), "git archive"
            except Exception as e:
                return None, f"{comp}: 수집 실패 {type(e).__name__}: {e}"
    fname = f"{comp}-{rev[:12]}.tar.gz"
    (src_dir / fname).write_bytes(data)
    lics = extract_licenses(data, lic_dir / comp)
    return ({"component": comp, "script": name, "category": "A",
             "reason": info["category_reason"], "upstream": repo,
             "rev_kind": info["rev_kind"], "rev": rev, "method": how,
             "archive": "sources/" + fname, "size": len(data), "sha256": sha256(data),
             "licenses": lics}, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--replace", action="store_true",
                    help="완전성 검사를 통과하면 기존 아카이브를 교체한다")
    a = ap.parse_args()
    from fetch_binaries import FFMPEG_SHA256, FFMPEG_VERSION, FFMPEG_ZIP

    failures: list[str] = []

    print("[1] 바이너리 <-> 메타데이터 교차 확인")
    exe = ROOT / "bin" / "ffmpeg.exe"
    if not exe.is_file():
        raise SystemExit("bin/ffmpeg.exe 없음. scripts/fetch_binaries.py --ffmpeg 먼저 실행")
    ver = subprocess.run([str(exe), "-hide_banner", "-version"],
                         capture_output=True, text=True, errors="replace").stdout
    first = ver.splitlines()[0]
    cfg = next((l for l in ver.splitlines() if l.startswith("configuration:")), "")
    built = next((l for l in ver.splitlines() if l.startswith("built with")), "")
    if FFMPEG_VERSION not in first:
        raise SystemExit(f"버전 불일치: {first} != {FFMPEG_VERSION}")
    if "--enable-gpl" not in cfg:
        raise SystemExit("GPL 빌드가 아님")
    print("    OK", first)

    work = OUT_DIR / ("UPCON-FFmpeg-Corresponding-Source-" + FFMPEG_VERSION)
    if work.exists():
        shutil.rmtree(work)
    (work / "sources").mkdir(parents=True)
    (work / "licenses").mkdir(parents=True)
    src_dir, lic_dir = work / "sources", work / "licenses"
    gitwork = OUT_DIR / "_gitwork"
    gitwork.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    print("[2] FFmpeg 소스")
    ff = fetch("https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/" + FFMPEG_COMMIT)
    (src_dir / f"ffmpeg-{FFMPEG_COMMIT[:12]}.tar.gz").write_bytes(ff)
    ff_lic = extract_licenses(ff, lic_dir / "ffmpeg")
    if not ff_lic:
        failures.append("FFmpeg 라이선스 파일 추출 실패")
    manifest.append({"component": "FFmpeg", "script": "(main)", "category": "A",
                     "reason": "배포 바이너리 본체", "upstream": "https://github.com/FFmpeg/FFmpeg",
                     "rev_kind": "COMMIT", "rev": FFMPEG_COMMIT, "method": "tarball",
                     "archive": f"sources/ffmpeg-{FFMPEG_COMMIT[:12]}.tar.gz",
                     "size": len(ff), "sha256": sha256(ff), "licenses": ff_lic})
    print(f"    OK {len(ff)/1048576:.1f} MB, 라이선스 {ff_lic}")

    print("[3] BtbN 빌드 스크립트 스냅샷 (patches 포함)")
    btbn = fetch("https://codeload.github.com/BtbN/FFmpeg-Builds/zip/" + BTBN_COMMIT)
    (src_dir / f"FFmpeg-Builds-{BTBN_COMMIT}.zip").write_bytes(btbn)
    bz = zipfile.ZipFile(io.BytesIO(btbn))
    names = bz.namelist()
    patches = [n for n in names if "/patches/" in n and n.endswith((".patch", ".diff"))]
    if not any("/scripts.d/" in n for n in names):
        failures.append("BtbN 스냅샷에 scripts.d/ 가 없음")
    if not patches:
        failures.append("BtbN 스냅샷에 patches/ 가 없음")
    scripts = {n.split("/scripts.d/")[-1]: bz.read(n).decode("utf-8", "replace")
               for n in names if "/scripts.d/" in n and n.endswith(".sh")}
    manifest.append({"component": "BtbN/FFmpeg-Builds (build scripts + patches)",
                     "script": "(build system)", "category": "C",
                     "reason": f"빌드 레시피. patch {len(patches)}개, scripts.d {len(scripts)}개 포함",
                     "upstream": "https://github.com/BtbN/FFmpeg-Builds",
                     "rev_kind": "COMMIT", "rev": BTBN_COMMIT, "method": "zip",
                     "archive": f"sources/FFmpeg-Builds-{BTBN_COMMIT}.zip",
                     "size": len(btbn), "sha256": sha256(btbn), "licenses": ["LICENSE"]})
    print(f"    OK scripts.d {len(scripts)}개, patch {len(patches)}개")

    print("[4] 의존성 분류")
    cls = classify(scripts)
    cats = {c: [n for n, v in cls.items() if v["category"] == c] for c in "ABCD"}
    for c in "ABCD":
        print(f"    {c}: {len(cats[c]):3d}")
    return _collect_and_finish(a, work, src_dir, lic_dir, gitwork, manifest, cls, cats,
                               failures, patches, FFMPEG_VERSION, FFMPEG_ZIP, FFMPEG_SHA256,
                               first, cfg, built)


def _collect_and_finish(a, work, src_dir, lic_dir, gitwork, manifest, cls, cats,
                        failures, patches, FFMPEG_VERSION, FFMPEG_ZIP, FFMPEG_SHA256,
                        first, cfg, built) -> int:
    total = len(cats["A"])
    print("[5] category A 소스 수집 (" + str(total) + "개)")
    seen: dict[str, str] = {}
    for i, name in enumerate(sorted(cats["A"]), 1):
        comp = re.sub(r"^\d+-", "", Path(name).stem)
        key = comp + "@" + cls[name]["rev"]
        if key in seen:
            print("    [" + str(i) + "/" + str(total) + "] " + comp.ljust(22) +
                  "(중복 스크립트 - " + seen[key] + " 에서 이미 수집)")
            continue
        seen[key] = name
        entry, err = collect_component(name, cls[name], src_dir, lic_dir, gitwork)
        if err:
            failures.append(err)
            print("    [" + str(i) + "/" + str(total) + "] FAIL " + err)
            continue
        manifest.append(entry)
        if not entry["licenses"]:
            if comp in LICENSE_IN_SOURCE:
                entry["license_note"] = LICENSE_IN_SOURCE[comp]
            else:
                failures.append(comp + ": 라이선스 파일을 찾지 못함 (" + entry["archive"] + ")")
        lic = ",".join(entry["licenses"]) if entry["licenses"] else "라이선스없음"
        print("    [" + str(i) + "/" + str(total) + "] " + comp.ljust(22) +
              str(round(entry["size"] / 1048576, 1)).rjust(7) + " MB  " +
              entry["method"].ljust(14) + lic)

    got = {m["component"] for m in manifest}
    for g in GPL_COMPONENTS:
        if not any(g in c for c in got):
            failures.append("GPL 구성요소 누락: " + g)

    print("[6] System Libraries (PE import 근거)")
    own = {f.name.lower() for f in (ROOT / "bin").iterdir()}
    sysimp = {}
    for f in sorted((ROOT / "bin").iterdir()):
        if f.suffix.lower() not in (".exe", ".dll"):
            continue
        if f.name.startswith("realesrgan") or f.name == "vcomp140.dll":
            continue
        for dll in pe_imports(f):
            if dll.lower() not in own:
                sysimp.setdefault(dll, []).append(f.name)
    print("    외부 import DLL " + str(len(sysimp)) + "개")

    sl = ["UPCON - FFmpeg Corresponding Source : SYSTEM LIBRARIES", "=" * 78, "",
          "GPLv3 의 System Libraries 예외에 해당한다고 보아 소스 패키지에 넣지 않은 항목.",
          "'Microsoft 제품이라서' 가 아니라, 실제 PE import 테이블을 읽어 이 바이너리가",
          "무엇을 어떻게 참조하는지 확인한 결과를 근거로 기록한다.", "",
          "판단 근거",
          "  - 아래 DLL 은 Windows 에 기본 포함되는 운영체제 구성요소이며 UPCON 배포본에",
          "    동봉되지 않는다 (사용자의 Windows 가 제공).",
          "  - FFmpeg 바이너리는 이들을 동적 import 로만 사용한다 (정적 링크 아님).",
          "  - 이 목록에 없는 나머지 외부 구성요소는 전부 정적 링크되므로 sources/ 에 넣었다.",
          "", "-- 이 바이너리가 import 하는 운영체제 DLL --"]
    for dll in sorted(sysimp, key=str.lower):
        sl.append("  " + dll.ljust(38) + " <- " + ", ".join(sorted(sysimp[dll])))
    sl += ["", "-- 빌드 스크립트상 System Library 로 분류한 항목 --"]
    for n in sorted(cats["B"]):
        sl.append("  " + n.ljust(34) + cls[n]["category_reason"])
        sl.append("    -> Windows SChannel (SSPI) 을 TLS 백엔드로 사용. 별도 소스 없음.")
    if not cats["B"]:
        sl.append("  (빌드 스크립트 기준 별도 항목 없음)")
    (work / "SYSTEM-LIBRARIES.txt").write_text("\n".join(sl) + "\n", encoding="utf-8")
    return _write_docs(a, work, gitwork, manifest, cls, cats, failures, patches,
                       FFMPEG_VERSION, FFMPEG_ZIP, FFMPEG_SHA256, first, cfg, built)


def _write_docs(a, work, gitwork, manifest, cls, cats, failures, patches,
                FFMPEG_VERSION, FFMPEG_ZIP, FFMPEG_SHA256, first, cfg, built) -> int:
    print("[7] 문서 생성")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    counts = {c: len([m for m in manifest if m["category"] == c]) for c in "ABCD"}
    B = ["UPCON - FFmpeg Corresponding Source (GPLv3)", "=" * 78, "",
         "Generated              : " + now, "",
         "-- Binary shipped by UPCON --",
         "FFmpeg version         : " + FFMPEG_VERSION,
         "Version string         : " + first,
         "FFmpeg upstream commit : " + FFMPEG_COMMIT,
         "Build provider         : BtbN/FFmpeg-Builds",
         "Build tag              : " + BTBN_TAG,
         "Build scripts commit   : " + BTBN_COMMIT,
         "Build config           : TARGET=" + TARGET + " VARIANT=" + VARIANT +
         " ffver=" + str(FFVER),
         "Original archive       : ffmpeg-" + FFMPEG_VERSION + "-win64-gpl-shared-8.1.zip",
         "Original archive URL   : " + FFMPEG_ZIP,
         "Original archive SHA256: " + FFMPEG_SHA256, "",
         "Toolchain              : " + built, "",
         "-- configure --", cfg.replace("configuration: ", ""), "",
         "-- Files shipped in UPCON (bin/) --"]
    for f in sorted((ROOT / "bin").iterdir()):
        if f.suffix.lower() in (".exe", ".dll"):
            if f.name.startswith("realesrgan") or f.name == "vcomp140.dll":
                continue
            B.append("  " + format(f.stat().st_size, ",").rjust(12) + "  " +
                     sha256(f.read_bytes()) + "  " + f.name)
    B += ["", "-- Scope --",
          "Category A (compiled/linked into this binary, source included) : " + str(counts["A"]),
          "Category B (System Libraries, excluded - SYSTEM-LIBRARIES.txt) : " + str(len(cats["B"])),
          "Category C (build tooling, preserved as snapshot)              : " + str(len(cats["C"])),
          "Category D (not part of this build, excluded)                  : " + str(len(cats["D"])),
          "", "BtbN build patches included in snapshot: " + str(len(patches)),
          "", "-- License --",
          "This build uses --enable-gpl --enable-version3, so GPLv3 (or later) applies.",
          "It is NOT --enable-nonfree, so redistribution is permitted provided the GPL",
          "obligations are met. GPL components linked in: " + ", ".join(GPL_COMPONENTS),
          "",
          "This file records what source and build material was collected for the binary",
          "identified above. It is a record of scope, not a legal assurance."]
    (work / "BUILD-INFO.txt").write_text("\n".join(B) + "\n", encoding="utf-8")

    M = ["UPCON - FFmpeg Corresponding Source : MANIFEST", "=" * 78, "",
         "Category A = 이 바이너리에 컴파일/정적 링크됨 (소스 포함)",
         "Category C = 빌드 도구/레시피 (스냅샷 보존)", ""]
    for m in sorted(manifest, key=lambda x: (x["category"], x["component"].lower())):
        M += ["component : " + m["component"],
              "category  : " + m["category"] + "  (" + m["reason"] + ")",
              "upstream  : " + m["upstream"],
              "revision  : " + m["rev_kind"] + " " + m["rev"],
              "obtained  : " + m["method"],
              "archive   : " + m["archive"],
              "size      : " + format(m["size"], ",") + " bytes",
              "sha256    : " + m["sha256"],
              "licenses  : " + (", ".join(m["licenses"]) if m["licenses"]
                                 else "(별도 파일 없음)")]
        if m.get("license_note"):
            M.append("license note: " + m["license_note"])
        M.append("")
    M += ["", "-- Category D : 이 빌드에 포함되지 않아 제외 --", ""]
    for n in sorted(cats["D"]):
        M.append("  " + n.ljust(34) + cls[n]["reason"])
    M += ["", "-- Category C : 빌드 도구/환경 --", ""]
    for n in sorted(cats["C"]):
        M.append("  " + n.ljust(34) + cls[n]["category_reason"])
    M += ["", "-- Category B : System Libraries --", ""]
    for n in sorted(cats["B"]):
        M.append("  " + n.ljust(34) + cls[n]["category_reason"])
    (work / "SOURCE-MANIFEST.txt").write_text("\n".join(M) + "\n", encoding="utf-8")
    (work / "classification.json").write_text(
        json.dumps({"config": {"TARGET": TARGET, "VARIANT": VARIANT, "ffver": FFVER},
                    "scripts": cls, "manifest": manifest}, indent=1, ensure_ascii=False),
        encoding="utf-8")

    print("[8] 완전성 검사")
    for m in manifest:
        p = work / m["archive"]
        if not p.is_file():
            failures.append(m["component"] + ": 아카이브 파일 없음")
        elif sha256(p.read_bytes()) != m["sha256"]:
            failures.append(m["component"] + ": 체크섬 불일치")
        if not m["rev"]:
            failures.append(m["component"] + ": 리비전 미고정")
    if failures:
        print("")
        print("완전성 검사 실패 - 아카이브를 만들지 않습니다:")
        for f in failures:
            print("  - " + f)
        _rmtree(gitwork)
        if not a.keep_work:
            shutil.rmtree(work, ignore_errors=True)
        return 1
    print("    OK (component / 리비전 / 라이선스 / 체크섬 / patch 확인)")

    print("[9] ZIP 생성")
    zip_path = OUT_DIR / (work.name + ".zip")
    tmp_zip = OUT_DIR / (work.name + ".zip.new")
    raw = nfiles = 0
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, work.name + "/" + f.relative_to(work).as_posix())
                raw += f.stat().st_size
                nfiles += 1
    if zip_path.exists():
        zip_path.unlink()
    tmp_zip.rename(zip_path)
    zsha = sha256(zip_path.read_bytes())
    _rmtree(gitwork)
    if not a.keep_work:
        shutil.rmtree(work, ignore_errors=True)

    print("")
    print("파일명     : " + zip_path.name)
    print("압축 크기  : " + format(zip_path.stat().st_size, ",") + " bytes (" +
          str(round(zip_path.stat().st_size / 1048576, 1)) + " MB)")
    print("해제 크기  : " + format(raw, ",") + " bytes (" + str(round(raw / 1048576, 1)) + " MB)")
    print("파일 수    : " + str(nfiles))
    print("SHA-256    : " + zsha)
    print("component  : A " + str(counts["A"]) + " / C " + str(counts["C"]) +
          "  (제외: B " + str(len(cats["B"])) + ", D " + str(len(cats["D"])) + ")")
    print("patch      : " + str(len(patches)) + "개 (BtbN 스냅샷 내)")
    print("위치       : " + str(zip_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
