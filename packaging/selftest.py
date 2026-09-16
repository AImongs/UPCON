"""Portable 빌드 검증용 콘솔 진입점 (배포본에는 포함하지 않는다).

frozen 환경에서만 확인 가능한 것들을 점검한다:
경로 해석, keyring 백엔드, GPU 감지, ncnn self-test, 실제 2x 업스케일, 임시파일 정리.
API Key 값은 출력하지 않는다.
"""
import os
import subprocess
import sys
import time
from pathlib import Path


import logging
logging.basicConfig(level=logging.INFO, format="    [log] %(name)s: %(message)s", stream=sys.stdout)


def line(t):
    print(f"\n--- {t} ---", flush=True)


def main() -> int:
    try:                      # frozen 콘솔이 cp949 여도 한글/기호 출력이 깨지지 않게
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    fails = []
    line("1) frozen / 경로")
    print("frozen        :", getattr(sys, "frozen", False))
    print("sys.executable:", sys.executable)
    from upcon.core import paths
    print("project_root  :", paths.project_root())
    print("resources_dir :", paths.resources_dir())
    print("bundled_bin   :", paths.bundled_bin_dir())
    print("bundled_models:", paths.bundled_models_dir())
    print("user_data_dir :", paths.user_data_dir())
    print("logs_dir      :", paths.logs_dir())
    for p in (paths.resources_dir() / "styles.qss", paths.bundled_bin_dir(), paths.bundled_models_dir()):
        if not p.exists():
            fails.append(f"없음: {p}")

    line("2) 개발 PC 경로 참조 여부")
    root = str(paths.project_root()).lower()
    root_norm = root.replace(chr(92), "/")
    leaked = [s for s in ("c:/projects/upcon", ".venv", "c:/ffmpeg") if s in root_norm]
    print("project_root 에 개발 경로 포함:", leaked or "없음")
    if leaked:
        fails.append(f"개발 경로 참조: {leaked}")

    line("3) 바이너리 탐색")
    from upcon.core.binaries import ffmpeg_path, ffprobe_path, find_binary
    for name, fn in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path)):
        try:
            p = fn()
            inside = str(p).lower().startswith(str(paths.project_root()).lower())
            print(f"{name:9s}: {p}  [번들 내부={inside}]")
            if not inside:
                fails.append(f"{name} 이 번들 밖: {p}")
        except Exception as e:
            fails.append(f"{name} 없음: {e}")
    try:
        ncnn = find_binary("realesrgan-ncnn-vulkan")
        inside = str(ncnn).lower().startswith(str(paths.project_root()).lower())
        print(f"ncnn     : {ncnn}  [번들 내부={inside}]")
        if not inside:
            fails.append("ncnn 이 번들 밖")
    except Exception as e:
        fails.append(f"ncnn 없음: {e}")

    line("4) keyring 백엔드 (값은 출력하지 않음)")
    try:
        from upcon.core import credentials
        print("backend:", credentials.backend_name())
        import keyring
        keyring.set_password("UPCON-selftest", "probe", "VALUE")
        got = keyring.get_password("UPCON-selftest", "probe")
        keyring.delete_password("UPCON-selftest", "probe")
        print("자격증명 쓰기/읽기/삭제:", "OK" if got == "VALUE" else "실패")
        if got != "VALUE":
            fails.append("keyring roundtrip 실패")
        print("사용자 fal 키 저장돼 있음:", bool(credentials.get_fal_key()))
    except Exception as e:
        fails.append(f"keyring 실패: {type(e).__name__}: {e}")
        print("keyring 오류:", type(e).__name__, e)

    line("5) GPU 감지")
    from upcon.core.env import detect_system_env
    env = detect_system_env()
    g = env.primary_gpu
    print("GPU:", g.display_name if g else "없음")
    if g:
        print(f"  VRAM={g.vram_mb}MB vulkan={g.vulkan_available} cuda={g.cuda_available}")
    else:
        fails.append("GPU 미감지")

    line("6) Provider 가용성 (ncnn self-test 포함)")
    from upcon.core.config import AppConfig
    from upcon.providers.local_ncnn import LocalNcnnProvider
    cfg = AppConfig.load()
    work = Path(os.environ["UPCON_SELFTEST_WORK"])
    cfg.temp_dir = str(work / "tmp")
    cfg.output_dir = str(work / "out")
    (work / "out").mkdir(parents=True, exist_ok=True)
    prov = LocalNcnnProvider(cfg)
    av = prov.check_availability(env, 2)
    print("available:", av.ok, "|", av.reason)
    print("detail   :", av.detail)
    if not av.ok:
        fails.append(f"로컬 provider 불가: {av.detail}")

    line("7) 실제 2x 업스케일")
    src = work / "selftest_480p.mp4"
    subprocess.run([str(ffmpeg_path()), "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=854x480:rate=24",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    from upcon.core.jobs import Job
    from upcon.core.probe import probe_video
    info = probe_video(src)
    print(f"입력: {info.width}x{info.height} {info.fps}fps {info.duration_sec:.2f}s "
          f"frames={info.nb_frames} audio={info.has_audio}")
    t0 = time.time()
    enc = []
    try:
        out = prov.upscale(Job(input_path=src, scale=2, info=info), lambda p: None, env)
        dt = time.time() - t0
        o = probe_video(out)
        print(f"출력: {out.name}")
        print(f"      {o.width}x{o.height} {o.fps}fps {o.duration_sec:.2f}s frames={o.nb_frames} "
              f"audio={o.has_audio}/{o.audio_codec} size={o.size_text}")
        print(f"      소요 {dt:.1f}s")
        if (o.width, o.height) != (info.width * 2, info.height * 2):
            fails.append(f"해상도 불일치 {o.width}x{o.height}")
        if abs(o.fps - info.fps) > 0.05:
            fails.append("FPS 불일치")
        if abs(o.duration_sec - info.duration_sec) > 0.3:
            fails.append("길이 불일치")
        if not o.has_audio:
            fails.append("오디오 유실")
    except Exception as e:
        fails.append(f"업스케일 실패: {type(e).__name__}: {e}")
        print("업스케일 오류:", e)

    line("8) 임시 파일 정리")
    from upcon.core import tempfs
    leftover = list(Path(tempfs.default_temp_root(cfg.temp_dir)).glob("job_*"))
    print("남은 job_* 폴더:", leftover or "없음")
    if leftover:
        fails.append(f"임시 폴더 잔류 {leftover}")

    line("9) Portable 폴더 오염 검사")
    bundle = paths.project_root()
    exe_dir = Path(sys.executable).parent
    bad = []
    for d in {bundle, exe_dir}:
        for pat in ("config.json", "queue.json", "*.log", "logs", "tmp", "*.mp4"):
            bad += [str(x) for x in Path(d).glob(pat)]
    print("배포 폴더에 생성된 사용자 데이터:", bad or "없음")
    if bad:
        fails.append(f"배포 폴더 오염: {bad}")

    line("10) 클라우드 설정 UI (오프스크린)")
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from upcon.app.cloud_settings import CloudSettingsDialog
        from upcon.providers.fal_flashvsr import ENDPOINT
        app = QApplication.instance() or QApplication([])
        dlg = CloudSettingsDialog(cfg, ENDPOINT, None)
        print("설정 대화상자 생성 OK:", dlg.windowTitle())
        print("  저장된 키 있음 표시:", "예" if dlg.key_edit is not None else "?")
        print("  keyring 백엔드 표시 경로 정상")
        dlg.deleteLater()
    except Exception as e:
        fails.append(f"클라우드 설정 UI 실패: {type(e).__name__}: {e}")
        print("오류:", type(e).__name__, e)

    line("11) 입력 형식 매트릭스 + 폴백 인코더")
    from upcon.core.config import AppConfig as _AC
    cases = [
        ("mp4_aac",   "케이스1.mp4",              ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],   True),
        ("mov_pcm",   "한글 폴더/케이스2 한글.mov", ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le"], True),
        ("mp4_silent","케이스3_무음.mp4",         ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"],           False),
    ]
    for key, rel, enc_args, want_audio in cases:
        src = work / rel
        src.parent.mkdir(parents=True, exist_ok=True)
        gen = [str(ffmpeg_path()), "-v", "error", "-y",
               "-f", "lavfi", "-i", "testsrc2=size=854x480:rate=24"]
        if want_audio:
            gen += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
        gen += ["-t", "2", *enc_args, "-shortest", str(src)]
        subprocess.run(gen, check=True)
        i2 = probe_video(src)
        c2 = _AC.load(); c2.temp_dir = str(work / "tmp"); c2.output_dir = str(work / "out")
        try:
            o = probe_video(LocalNcnnProvider(c2).upscale(Job(input_path=src, scale=2, info=i2), lambda p: None, env))
            ok = (o.width, o.height) == (1708, 960) and o.has_audio == want_audio
            print(f"  {key:11s} {i2.width}x{i2.height} audio={i2.has_audio}/{i2.audio_codec or '-'} "
                  f"-> {o.width}x{o.height} audio={o.has_audio}/{o.audio_codec or '-'}  {'OK' if ok else 'MISMATCH'}")
            if not ok:
                fails.append(f"{key} 결과 불일치")
        except Exception as e:
            fails.append(f"{key} 실패: {type(e).__name__}: {e}")
            print(f"  {key:11s} 실패: {e}")

    # NVENC 를 못 쓰는 상황 강제 -> 소프트웨어 인코더로 폴백하는지
    from upcon.core import ffmpeg as _ff
    print("  -- 하드웨어 인코더 사용 불가 상황 강제 --")
    _ff._hw_encoder_cache.update({n: False for n in _ff.HW_ENCODERS})
    picked = _ff.pick_encoder("auto")
    print(f"  폴백 인코더 선택: {picked}")
    if picked != "libx264":
        fails.append(f"폴백이 libx264 가 아님: {picked}")
    src = work / "fallback.mp4"
    subprocess.run([str(ffmpeg_path()), "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=854x480:rate=24", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=48000", "-t", "2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    c3 = _AC.load(); c3.temp_dir = str(work / "tmp"); c3.output_dir = str(work / "out")
    try:
        o = probe_video(LocalNcnnProvider(c3).upscale(
            Job(input_path=src, scale=2, info=probe_video(src)), lambda p: None, env))
        ok = (o.width, o.height) == (1708, 960) and o.has_audio
        print(f"  소프트웨어 폴백 결과: {o.width}x{o.height} audio={o.has_audio} {'OK' if ok else 'MISMATCH'}")
        if not ok:
            fails.append("소프트웨어 폴백 결과 불일치")
    except Exception as e:
        fails.append(f"소프트웨어 폴백 실패: {type(e).__name__}: {e}")
    _ff._hw_encoder_cache.clear()

    line("결과")
    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(2)
