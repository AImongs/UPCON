from types import SimpleNamespace
from upcon.core import env

def test_engine_device_indices(monkeypatch):
    monkeypatch.setattr('upcon.core.binaries.find_binary', lambda name: '/engine')
    monkeypatch.setattr(env.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=255, stderr='[0 Apple M4] queueC=1\n[0 Apple M4] fp16=1\n[1 AMD Radeon] queueC=1\n'))
    devices = env._macos_vulkan_devices()
    assert [(d.name, d.vulkan_device_index) for d in devices] == [('Apple M4', 0), ('AMD Radeon', 1)]
    assert devices[0].is_integrated and all(d.vulkan_available for d in devices)

def test_no_metal_is_unavailable(monkeypatch):
    monkeypatch.setattr('upcon.core.binaries.find_binary', lambda name: '/engine')
    monkeypatch.setattr(env.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=255, stderr='vkCreateInstance failed -9'))
    assert env._macos_vulkan_devices() == []

def test_png_input_preserves_bgr_pixels(tmp_path, monkeypatch):
    import subprocess
    from upcon.providers import local_ncnn
    from upcon.core.binaries import ffmpeg_path
    monkeypatch.setattr(local_ncnn.plat, 'IS_MACOS', True)
    pixels = bytes([3, 20, 200, 99, 8, 1]) * 2
    p = local_ncnn._write_input_frame(tmp_path/'frame.bmp', b'', pixels, 2, 2)
    decoded = subprocess.run([str(ffmpeg_path()), '-v', 'error', '-i', str(p), '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'], capture_output=True, check=True)
    assert decoded.stdout == pixels

def test_closed_encoder_stdin(tmp_path):
    import subprocess, sys
    from upcon.core.ffmpeg import check_encoder_exit
    out = tmp_path/'output'
    out.write_bytes(b'ok')
    p = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdin.close()
    check_encoder_exit(p, out)
    assert p.returncode == 0
