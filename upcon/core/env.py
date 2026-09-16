"""시스템/GPU 환경 정보 모델 + 실제 감지.

감지 결과는 "GPU 가 있다" 까지만 말해 준다. "특정 모델을 실행할 수 있다" 는
각 Provider 가 `ModelRequirements` 와 대조하고, 필요하면 self-test 로 확인한다.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)
_CREATE_NO_WINDOW = 0x08000000


class GpuVendor(str, Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    OTHER = "other"
    NONE = "none"


_VENDOR_IDS = {0x10DE: GpuVendor.NVIDIA, 0x1002: GpuVendor.AMD, 0x8086: GpuVendor.INTEL}


@dataclass
class GpuInfo:
    vendor: GpuVendor = GpuVendor.NONE
    name: str = ""                     # 예: "NVIDIA GeForce RTX 5060"
    vram_mb: int = 0
    driver_version: str = ""
    cuda_available: bool = False       # NVIDIA 드라이버가 CUDA 를 보고함
    cuda_compute_capability: str = ""  # 예: "12.0"
    vulkan_available: bool = False
    vulkan_device_index: int = -1      # ncnn -g 에 넘길 인덱스
    is_integrated: bool = False

    @property
    def display_name(self) -> str:
        """사용자 표시용 짧은 이름: 'NVIDIA GeForce RTX 5060' → 'RTX 5060'."""
        n = self.name or "알 수 없는 GPU"
        for prefix in ("NVIDIA GeForce ", "NVIDIA ", "AMD Radeon ", "AMD ", "Intel(R) ", "Intel "):
            if n.startswith(prefix):
                n = n[len(prefix):]
                break
        return n.replace("(TM)", "").replace("(R)", "").strip()


@dataclass
class SystemEnv:
    os_version: str = ""
    cpu_name: str = ""
    ram_mb: int = 0
    gpus: list[GpuInfo] = field(default_factory=list)

    @property
    def primary_gpu(self) -> GpuInfo | None:
        """우선순위: Vulkan 가능한 외장 GPU 중 VRAM 최대 → 그 외 VRAM 최대."""
        if not self.gpus:
            return None
        vk = [g for g in self.gpus if g.vulkan_available and not g.is_integrated]
        pool = vk or [g for g in self.gpus if g.vulkan_available] or self.gpus
        return max(pool, key=lambda g: g.vram_mb)

    def summary(self) -> str:
        if not self.gpus:
            return "GPU 없음"
        return "; ".join(
            f"{g.name} ({g.vram_mb} MB, vulkan={'Y' if g.vulkan_available else 'N'}, cuda={'Y' if g.cuda_available else 'N'})"
            for g in self.gpus
        )


@dataclass(frozen=True)
class ModelRequirements:
    """로컬 모델이 스스로 선언하는 실행 요구사항. Router/Provider 는 이것과 SystemEnv 를 비교한다."""

    model_id: str
    min_vram_mb: int
    requires_cuda: bool = False
    requires_vulkan: bool = False
    supported_vendors: tuple[GpuVendor, ...] = (GpuVendor.NVIDIA, GpuVendor.AMD, GpuVendor.INTEL, GpuVendor.OTHER)
    min_cuda_compute_capability: str = ""  # 예: "7.0" (빈 값이면 제한 없음)
    temporal: bool = False
    needs_download: bool = False           # 본체 미포함, 사용자가 선택한 engine_dir 에 다운로드


# --------------------------------------------------------------------------- Vulkan
def _vulkan_devices() -> list[GpuInfo]:
    """vulkan-1.dll 로 물리 장치를 열거한다 (Vulkan SDK 불필요, 드라이버 런타임만 있으면 됨)."""
    try:
        vk = ctypes.WinDLL("vulkan-1.dll")
    except OSError:
        log.info("vulkan-1.dll 없음 → Vulkan 미지원")
        return []

    VK_STRUCTURE_TYPE_APPLICATION_INFO = 0
    VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO = 1

    class VkApplicationInfo(ctypes.Structure):
        _fields_ = [("sType", ctypes.c_int), ("pNext", ctypes.c_void_p), ("pApplicationName", ctypes.c_char_p),
                    ("applicationVersion", ctypes.c_uint32), ("pEngineName", ctypes.c_char_p),
                    ("engineVersion", ctypes.c_uint32), ("apiVersion", ctypes.c_uint32)]

    class VkInstanceCreateInfo(ctypes.Structure):
        _fields_ = [("sType", ctypes.c_int), ("pNext", ctypes.c_void_p), ("flags", ctypes.c_uint32),
                    ("pApplicationInfo", ctypes.POINTER(VkApplicationInfo)),
                    ("enabledLayerCount", ctypes.c_uint32), ("ppEnabledLayerNames", ctypes.c_void_p),
                    ("enabledExtensionCount", ctypes.c_uint32), ("ppEnabledExtensionNames", ctypes.c_void_p)]

    class VkPhysicalDeviceLimits(ctypes.Structure):
        _fields_ = [("raw", ctypes.c_uint8 * 504)]

    class VkPhysicalDeviceSparseProperties(ctypes.Structure):
        _fields_ = [("raw", ctypes.c_uint32 * 5)]

    class VkPhysicalDeviceProperties(ctypes.Structure):
        _fields_ = [("apiVersion", ctypes.c_uint32), ("driverVersion", ctypes.c_uint32),
                    ("vendorID", ctypes.c_uint32), ("deviceID", ctypes.c_uint32), ("deviceType", ctypes.c_int),
                    ("deviceName", ctypes.c_char * 256), ("pipelineCacheUUID", ctypes.c_uint8 * 16),
                    ("limits", VkPhysicalDeviceLimits), ("sparseProperties", VkPhysicalDeviceSparseProperties)]

    class VkMemoryType(ctypes.Structure):
        _fields_ = [("propertyFlags", ctypes.c_uint32), ("heapIndex", ctypes.c_uint32)]

    class VkMemoryHeap(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint64), ("flags", ctypes.c_uint32)]

    class VkPhysicalDeviceMemoryProperties(ctypes.Structure):
        _fields_ = [("memoryTypeCount", ctypes.c_uint32), ("memoryTypes", VkMemoryType * 32),
                    ("memoryHeapCount", ctypes.c_uint32), ("memoryHeaps", VkMemoryHeap * 16)]

    app = VkApplicationInfo(VK_STRUCTURE_TYPE_APPLICATION_INFO, None, b"UPCON", 1, b"UPCON", 1, (1 << 22))
    info = VkInstanceCreateInfo(VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, None, 0, ctypes.pointer(app), 0, None, 0, None)
    instance = ctypes.c_void_p()
    vk.vkCreateInstance.restype = ctypes.c_int
    vk.vkEnumeratePhysicalDevices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    vk.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    vk.vkGetPhysicalDeviceMemoryProperties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    vk.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    if vk.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(instance)) != 0:
        log.info("vkCreateInstance 실패 → Vulkan 미지원")
        return []

    devices: list[GpuInfo] = []
    try:
        count = ctypes.c_uint32(0)
        vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None)
        handles = (ctypes.c_void_p * max(count.value, 1))()
        vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), handles)
        for i in range(count.value):
            props = VkPhysicalDeviceProperties()
            vk.vkGetPhysicalDeviceProperties(handles[i], ctypes.byref(props))
            mem = VkPhysicalDeviceMemoryProperties()
            vk.vkGetPhysicalDeviceMemoryProperties(handles[i], ctypes.byref(mem))
            vram = 0
            for h in range(mem.memoryHeapCount):
                if mem.memoryHeaps[h].flags & 0x1:  # VK_MEMORY_HEAP_DEVICE_LOCAL_BIT
                    vram = max(vram, mem.memoryHeaps[h].size)
            # deviceType: 1=integrated, 2=discrete, 3=virtual, 4=cpu
            if props.deviceType == 4:
                continue
            devices.append(GpuInfo(
                vendor=_VENDOR_IDS.get(props.vendorID, GpuVendor.OTHER),
                name=props.deviceName.decode("utf-8", "replace"),
                vram_mb=int(vram // (1024 * 1024)),
                vulkan_available=True,
                vulkan_device_index=len(devices),
                is_integrated=(props.deviceType == 1),
            ))
    finally:
        vk.vkDestroyInstance(instance, None)
    return devices


# --------------------------------------------------------------------------- NVIDIA
def _nvidia_devices() -> list[GpuInfo]:
    """NVML(nvidia-ml-py) 로 NVIDIA GPU 상세를 읽는다. 실패 시 nvidia-smi 로 폴백."""
    try:
        import pynvml  # nvidia-ml-py
    except ImportError:
        pynvml = None

    if pynvml is not None:
        try:
            pynvml.nvmlInit()
            try:
                driver = pynvml.nvmlSystemGetDriverVersion()
                driver = driver.decode() if isinstance(driver, bytes) else str(driver)
                out = []
                for i in range(pynvml.nvmlDeviceGetCount()):
                    h = pynvml.nvmlDeviceGetHandleByIndex(i)
                    name = pynvml.nvmlDeviceGetName(h)
                    name = name.decode() if isinstance(name, bytes) else str(name)
                    mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                    major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(h)
                    out.append(GpuInfo(vendor=GpuVendor.NVIDIA, name=name, vram_mb=int(mem.total // (1024 * 1024)),
                                       driver_version=driver, cuda_available=True,
                                       cuda_compute_capability=f"{major}.{minor}"))
                return out
            finally:
                pynvml.nvmlShutdown()
        except Exception as e:  # NVML 없음(비-NVIDIA PC) 은 정상 상황
            log.info("NVML 사용 불가: %s", e)

    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    try:
        r = subprocess.run([smi, "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10, creationflags=_CREATE_NO_WINDOW)
        out = []
        for line in r.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                out.append(GpuInfo(vendor=GpuVendor.NVIDIA, name=parts[0], vram_mb=int(float(parts[1])),
                                   driver_version=parts[2], cuda_available=True,
                                   cuda_compute_capability=parts[3] if len(parts) > 3 else ""))
        return out
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        log.info("nvidia-smi 사용 불가: %s", e)
        return []


# --------------------------------------------------------------------------- 합치기
def _merge(vulkan: list[GpuInfo], nvidia: list[GpuInfo]) -> list[GpuInfo]:
    """Vulkan 장치 목록을 기준으로 NVIDIA 상세(드라이버/CUDA/정확한 VRAM)를 덧붙인다."""
    gpus = list(vulkan)
    used: set[int] = set()
    for g in gpus:
        if g.vendor != GpuVendor.NVIDIA:
            continue
        for j, n in enumerate(nvidia):
            if j in used:
                continue
            if n.name.lower() == g.name.lower() or abs(n.vram_mb - g.vram_mb) < 1024:
                g.driver_version, g.cuda_available = n.driver_version, n.cuda_available
                g.cuda_compute_capability = n.cuda_compute_capability
                g.vram_mb = max(g.vram_mb, n.vram_mb)
                used.add(j)
                break
    # Vulkan 으로 안 보였지만 NVML 엔 있는 GPU (드라이버 문제 등) 도 기록
    for j, n in enumerate(nvidia):
        if j not in used:
            gpus.append(n)
    return gpus


def detect_system_env() -> SystemEnv:
    env = SystemEnv(
        os_version=f"{platform.system()} {platform.release()} ({platform.version()})",
        cpu_name=platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", ""),
    )
    try:
        kernel32 = ctypes.windll.kernel32

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64)]

        ms = MEMORYSTATUSEX(ctypes.sizeof(MEMORYSTATUSEX))
        kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        env.ram_mb = int(ms.ullTotalPhys // (1024 * 1024))
    except Exception:
        pass

    env.gpus = _merge(_vulkan_devices(), _nvidia_devices())
    log.info("system env: %s | RAM %d MB | %s", env.os_version, env.ram_mb, env.summary())
    return env


def free_disk_mb(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return int(shutil.disk_usage(p).free // (1024 * 1024))
