#!/usr/bin/env python3
"""
╔══════════════════════════════════════════╗
║   N E X U S  — Terminal Mission Control  ║
╚══════════════════════════════════════════╝

A cool Linux terminal system monitor with in-place rendering, full hardware
census (every mount + per-disk I/O, every network interface, all GPUs:
NVIDIA / AMD / Intel iGPU), and a neofetch-style system info panel.

No dependencies. Reads /proc + sysfs directly. Press 'q' to quit.

Keys:
  q     Quit
  t     Cycle theme
  m     Toggle matrix rain
  s     Sort processes (CPU% / MEM)
"""

import os
import re
import sys
import time
import glob
import random
import socket
import shutil
import subprocess
import threading
from datetime import datetime
from dataclasses import dataclass, field


# ─── Theme System ────────────────────────────────────────────────

@dataclass
class Theme:
    name: str
    primary: str
    secondary: str
    success: str
    warning: str
    danger: str
    empty_block: str
    text: str
    dim: str


THEMES = [
    Theme("CYBER",  "\033[95m", "\033[94m", "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[97m", "\033[90m"),
    Theme("NEON",   "\033[82m", "\033[84m", "\033[82m", "\033[83m", "\033[81m", "\033[90m", "\033[97m", "\033[90m"),
    Theme("ICE",    "\033[96m", "\033[94m", "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[97m", "\033[90m"),
    Theme("EMBER",  "\033[91m", "\033[93m", "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[97m", "\033[90m"),
    Theme("GHOST",  "\033[90m", "\033[94m", "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[97m", "\033[90m"),
]

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"


# ─── ANSI-aware text helpers (correct alignment with color codes) ──

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def vlen(s):
    """Visible length of a string, ignoring ANSI escape sequences."""
    return len(_ANSI_RE.sub("", s))


def pad(s, width):
    """Pad a (possibly colored) string to `width` visible columns."""
    d = width - vlen(s)
    return s + (" " * d if d > 0 else "")


def strip_ansi(s):
    return _ANSI_RE.sub("", s)


# ─── System Metrics (reads /proc) ────────────────────────────────

@dataclass
class SystemStats:
    cpu_percent: float = 0.0
    ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0
    ram_percent: float = 0.0
    swap_used_mb: float = 0.0
    swap_total_mb: float = 0.0
    disks: list = field(default_factory=list)      # (mount, dev, used_gb, total_gb, pct)
    disk_io: dict = field(default_factory=dict)    # disk -> (r_mib/s, w_mib/s) since last sample
    net_ifaces: list = field(default_factory=list) # (name, rx_kbps, tx_kbps) — real rates
    uptime_sec: float = 0.0
    load_avg: tuple = (0, 0, 0)
    procs: list = field(default_factory=list)
    temp_c: float = -1.0
    cpu_total_ticks: int = 0   # aggregate /proc/stat 'cpu' total (for per-proc CPU%)
    per_core: list = field(default_factory=list)  # live per-core CPU%


def read_cpu_times():
    """Aggregate CPU times plus per-core (idle, total) from /proc/stat."""
    with open("/proc/stat") as f:
        lines = f.readlines()

    def parse(line):
        values = list(map(int, line.split()[1:9]))
        return values[3] + values[4], sum(values)

    idle, total = parse(lines[0])
    per_core = []
    for line in lines[1:]:
        if line.startswith("cpu") and len(line.split()) >= 9:
            per_core.append(parse(line))
    return idle, total, per_core


def read_meminfo():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(':')] = int(parts[1])
    total = info.get("MemTotal", 1) / 1024
    avail = info.get("MemAvailable", info.get("MemFree", 0)) / 1024
    used = total - avail
    swap_total = info.get("SwapTotal", 0) / 1024
    swap_free = info.get("SwapFree", 0) / 1024
    return used, total, (swap_total - swap_free), swap_total


# Filesystem types that represent real storage (everything else — tmpfs,
# proc, cgroup, devpts, ... — is kernel virtual space, not a drive).
REAL_FS_TYPES = {
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "vfat",
    "exfat", "ntfs", "ntfs3", "apfs", "udf", "iso9660", "overlay",
}


def read_mounts():
    """Real filesystem mounts as (mountpoint, device), deduped by device so
    bind-mount storms collapse to the primary (shortest) mountpoint."""
    seen = {}
    try:
        with open("/proc/mounts") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mp, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype not in REAL_FS_TYPES:
            continue
        cur = seen.get(dev)
        if cur is None or len(mp) < len(cur):
            seen[dev] = mp
    out = [(mp, dev) for dev, mp in seen.items()]
    out.sort(key=lambda x: (x[0] != "/", len(x[0]), x[0]))
    return out


def disk_name_for(dev):
    """Map a mount device to its whole-disk name: /dev/nvme1n1p6 -> nvme1n1."""
    m = re.match(r"^/dev/(nvme\d+n\d+|sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|mmcblk\d+|md\d+)(p?\d+)?$", dev)
    return m.group(1) if m else ""


_WHOLE_DISK_RE = re.compile(
    r"^(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+|md\d+)$")


def read_diskstats_raw():
    """Cumulative sectors per WHOLE disk: {disk: (sectors_read, sectors_written)}.
    NOTE: totals since boot — callers delta them (see gather_stats)."""
    out = {}
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                p = line.split()
                if len(p) >= 10 and _WHOLE_DISK_RE.match(p[2]):
                    out[p[2]] = (int(p[5]), int(p[9]))
    except FileNotFoundError:
        pass
    return out


def read_net():
    """Cumulative since-boot byte counters per non-loopback interface:
    {iface: (rx_bytes, tx_bytes)}. NOTE: totals, not rates — callers must
    delta between samples (see gather_stats) to get real KB/s figures."""
    out = {}
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                parts = line.split()
                if len(parts) >= 10 and not parts[0].startswith("lo:"):
                    out[parts[0].rstrip(":")] = (int(parts[1]), int(parts[9]))
    except FileNotFoundError:
        pass
    return out


def read_uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def read_loadavg():
    with open("/proc/loadavg") as f:
        parts = f.read().split()[:3]
    return tuple(float(x) for x in parts)


def read_temperature():
    """CPU temperature (best effort). Prefers CPU-typed sensors so the value
    shown under the CPU section is actually a CPU reading, not e.g. a battery
    or GPU zone that happens to be listed first."""
    fallback = None
    if os.path.isdir("/sys/class/thermal"):
        for zone in sorted(os.listdir("/sys/class/thermal")):
            try:
                with open(f"/sys/class/thermal/{zone}/type") as f:
                    ztype = f.read().strip().lower()
                with open(f"/sys/class/thermal/{zone}/temp") as f:
                    t = int(f.read()) / 100.0
            except (FileNotFoundError, PermissionError, ValueError):
                continue
            if "cpu" in ztype or "x86_pkg" in ztype or "soc" in ztype:
                return t
            fallback = t if fallback is None else fallback
    if os.path.isdir("/sys/class/hwmon"):
        for hm in sorted(os.listdir("/sys/class/hwmon")):
            try:
                with open(f"/sys/class/hwmon/{hm}/name") as f:
                    hname = f.read().strip().lower()
            except (FileNotFoundError, PermissionError):
                hname = ""
            try:
                with open(f"/sys/class/hwmon/{hm}/temp1_input") as f:
                    t = int(f.read()) / 1000.0
            except (FileNotFoundError, PermissionError, ValueError):
                continue
            if any(k in hname for k in ("coretemp", "k10temp", "zenpower", "cpu_thermal")):
                return t
            fallback = t if fallback is None else fallback
    return -1.0 if fallback is None else fallback


def read_processes():
    procs = []
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/stat") as f:
                    data = f.read()
                lpar = data.index('(')
                rpar = data.rindex(')')
                name = data[lpar+1:rpar]
                fields_after = data[rpar+2:].split()
                utime = int(fields_after[11])
                stime = int(fields_after[12])
                with open(f"/proc/{pid_dir}/statm") as f:
                    rss_pages = int(f.read().split()[1])
                rss_mb = rss_pages * os.sysconf("SC_PAGE_SIZE") / (1024**2)
                procs.append((int(pid_dir), name, utime + stime, rss_mb))
            except (FileNotFoundError, PermissionError, IndexError, ValueError):
                continue
    except FileNotFoundError:
        pass
    return procs


def gather_stats(prev=None):
    """Sample all metrics. `prev` is the tuple returned by the last call:
    (idle, total, per_core[, net_counters, io_counters, monotonic_ts]).

    CPU%, net KB/s and disk MB/s are deltas since that sample; a 3-tuple
    prev (no baselines) still works and just yields 0.0 rates.
    """
    idle, total, per_core = read_cpu_times()

    cpu_percent = 0.0
    per_core_pct = [0.0] * len(per_core)
    if prev is not None:
        prev_idle, prev_total, prev_pc = prev[0], prev[1], prev[2]
        dtotal = total - prev_total
        didle = idle - prev_idle
        cpu_percent = max(0, min(100, (1 - didle / max(dtotal, 1)) * 100))
        for i in range(min(len(prev_pc), len(per_core))):
            dtick = per_core[i][1] - prev_pc[i][1]
            if dtick > 0:
                didle_c = per_core[i][0] - prev_pc[i][0]
                per_core_pct[i] = max(0, min(100, (1 - didle_c / dtick) * 100))

    ram_used, ram_total, swap_used, swap_total = read_meminfo()

    # Disks: usage per real mount (statvfs), I/O rates per whole disk.
    disks = []
    for mp, dev in read_mounts():
        try:
            st = os.statvfs(mp)
        except OSError:
            continue
        total_gb = st.f_blocks * st.f_frsize / 1024**3
        if total_gb <= 0:
            continue
        used_gb = (st.f_blocks - st.f_bfree) * st.f_frsize / 1024**3
        disks.append((mp, dev, used_gb, total_gb, (used_gb / total_gb) * 100))

    now = time.monotonic()
    net_now = read_net()
    io_now = read_diskstats_raw()
    net_ifaces, disk_io = [], {}
    if prev is not None and len(prev) >= 6:
        dt = max(now - prev[5], 1e-9)
        # Clamp at 0: counters can reset if an iface/disk disappears.
        for name in sorted(net_now):
            p = prev[3].get(name) if isinstance(prev[3], dict) else None
            if p is not None:
                net_ifaces.append((name,
                                   max(0.0, (net_now[name][0] - p[0]) / dt) / 1024,
                                   max(0.0, (net_now[name][1] - p[1]) / dt) / 1024))
        for d in sorted(io_now):
            p = prev[4].get(d) if isinstance(prev[4], dict) else None
            if p is not None:
                disk_io[d] = (max(0.0, (io_now[d][0] - p[0]) * 512 / dt) / 1024**2,
                              max(0.0, (io_now[d][1] - p[1]) * 512 / dt) / 1024**2)
    uptime = read_uptime()
    load = read_loadavg()
    procs = read_processes()
    temp = read_temperature()

    return SystemStats(
        cpu_percent=cpu_percent,
        ram_used_mb=ram_used,
        ram_total_mb=ram_total,
        ram_percent=(ram_used / max(ram_total, 1)) * 100,
        swap_used_mb=swap_used,
        swap_total_mb=swap_total,
        disks=disks,
        disk_io=disk_io,
        net_ifaces=net_ifaces,
        uptime_sec=uptime,
        load_avg=load,
        procs=procs,
        temp_c=temp,
        cpu_total_ticks=total,
        per_core=per_core_pct,
    ), (idle, total, per_core, net_now, io_now, now)


# ─── GPU Monitor (NVIDIA / AMD / Intel, best effort) ──────────────
# Every source reports only what the driver actually exposes. Anything
# unknown stays at its sentinel (-1 / 0) and is rendered as "not exposed"
# — NEXUS never invents a number.

@dataclass
class GpuInfo:
    vendor: str = ""
    name: str = ""
    util: float = -1.0         # %  (-1 = not exposed by driver)
    mem_used_mb: float = 0.0
    mem_total_mb: float = 0.0
    temp_c: float = -1.0
    power_w: float = -1.0
    power_max_w: float = -1.0
    driver: str = ""


def _f(x):
    try:
        return float(x)
    except (ValueError, TypeError):
        return -1.0


def _read_nvidia():
    """All NVIDIA GPUs via nvidia-smi (fast CSV, no units)."""
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,driver_version,utilization.gpu,memory.used,"
             "memory.total,temperature.gpu,power.draw,power.limit",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.5)
        if out.returncode != 0:
            return []
        gpus = []
        for line in out.stdout.strip().splitlines():
            if not line.strip():
                continue
            p = line.split(", ")
            g = GpuInfo(vendor="NVIDIA", name=p[0])
            if len(p) > 1: g.driver = p[1]
            if len(p) > 2: g.util = _f(p[2])
            if len(p) > 3: g.mem_used_mb = _f(p[3])
            if len(p) > 4: g.mem_total_mb = _f(p[4])
            if len(p) > 5: g.temp_c = _f(p[5])
            if len(p) > 6: g.power_w = _f(p[6])
            if len(p) > 7: g.power_max_w = _f(p[7])
            gpus.append(g)
        return gpus
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []


_VENDORS = {"10de": "NVIDIA", "1002": "AMD", "8086": "Intel"}
_GPU_MODULES = ("nvidia", "amdgpu", "i915", "xe")


def _read_sysfs_gpus():
    """Every PCI display controller under /sys/class/drm, with whatever stats
    its driver exposes (util for AMD, VRAM, temp)."""
    gpus = []
    try:
        cards = [c for c in os.listdir("/sys/class/drm") if re.match(r"^card\d+$", c)]
    except FileNotFoundError:
        return gpus
    for card in sorted(cards):
        dev = f"/sys/class/drm/{card}/device"
        uevent = {}
        try:
            with open(os.path.join(dev, "uevent")) as f:
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        uevent[k] = v
        except (FileNotFoundError, PermissionError):
            continue
        pciclass = uevent.get("PCI_CLASS", "")
        try:
            if int(pciclass, 16) >> 16 != 3:   # PCI class 0x03 = display controller
                continue
        except ValueError:
            continue
        vendor_id = uevent.get("PCI_ID", "").split(":")[0].lower()
        vendor = _VENDORS.get(vendor_id, "GPU")
        g = GpuInfo(vendor=vendor,
                    name={"Intel": "Intel iGPU", "AMD": "AMD GPU",
                          "NVIDIA": "NVIDIA GPU"}.get(vendor, "Display adapter"))
        try:
            with open(os.path.join(dev, "gpu_busy_percent")) as f:
                g.util = float(f.read().strip())
        except (FileNotFoundError, ValueError):
            pass
        for used_key, total_key in (("mem_info_vram_used", "mem_info_vram_total"),
                                    ("lmem_used_bytes", "lmem_total_bytes")):
            try:
                with open(os.path.join(dev, used_key)) as f:
                    g.mem_used_mb = int(f.read().strip()) / 1024**2
                with open(os.path.join(dev, total_key)) as f:
                    g.mem_total_mb = int(f.read().strip()) / 1024**2
                break
            except (FileNotFoundError, ValueError):
                continue
        try:
            hw = glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp1_input"))
            if hw:
                with open(hw[0]) as f:
                    g.temp_c = int(f.read().strip()) / 1000.0
        except (FileNotFoundError, ValueError):
            pass
        for mod in _GPU_MODULES:
            if os.path.isdir(f"/sys/module/{mod}"):
                g.driver = mod
                break
        gpus.append(g)
    return gpus


_lspci_cache = None


def _read_lspci_gpus():
    """Last resort (e.g. container without driver/sysfs access): names only,
    straight from lspci. No usage stats — we never invent numbers."""
    global _lspci_cache
    if _lspci_cache is None:
        gpus = []
        try:
            out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=3)
            for line in out.stdout.splitlines():
                m = re.match(r"^[0-9a-f:.]+ (?:VGA compatible controller|3D controller|"
                             r"Display controller): (.+)$", line)
                if not m:
                    continue
                raw = m.group(1).strip()
                name = re.sub(r"\s*\(rev [0-9a-f]+\)\s*$", "", raw)
                name = re.sub(r"^(?:NVIDIA Corporation|Advanced Micro Devices,? Inc\.?\s*\[AMD/ATI\]|"
                              r"Intel Corporation)\s*", "", name)
                low = raw.lower()
                vendor = ("NVIDIA" if "nvidia" in low else
                          "AMD" if "advanced micro devices" in low or "amd/ati" in low else
                          "Intel" if "intel corporation" in low else "GPU")
                gpus.append(GpuInfo(vendor=vendor, name=name))
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        _lspci_cache = gpus
    return _lspci_cache


def read_gpus():
    """All GPUs: NVIDIA via nvidia-smi, AMD/Intel/others via sysfs, lspci as a
    names-only fallback when nothing else is visible."""
    nv = _read_nvidia()
    gpus = list(nv)
    for g in _read_sysfs_gpus():
        if g.vendor == "NVIDIA" and nv:
            continue   # nvidia-smi already listed it with full stats
        gpus.append(g)
    if not gpus:
        gpus = _read_lspci_gpus()
    return gpus[:4]


# ─── neofetch-style system info (gathered once at startup) ───────

def count_packages():
    """Best-effort package count, fast and non-blocking."""
    try:  # Debian / Ubuntu
        n = 0
        with open("/var/lib/dpkg/status") as f:
            for line in f:
                if line.startswith("Package:"):
                    n += 1
        return n
    except (FileNotFoundError, PermissionError):
        pass
    for cmd in (["rpm", "-qa", "--quiet"], ["pacman", "-Q"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if out.returncode == 0 and out.stdout.strip():
                return len(out.stdout.splitlines())
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def gather_fetch_info():
    """Collect static system facts once (OS, kernel, host, shell, ...)."""
    info = {}
    # OS pretty name (os-release -> NAME/VERSION -> kernel fallback)
    os_name = None
    try:
        data = {}
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip().strip('"')
        if data.get("PRETTY_NAME"):
            os_name = data["PRETTY_NAME"]
        elif data.get("NAME"):
            os_name = f"{data['NAME']} {data.get('VERSION', '')}".strip()
    except (FileNotFoundError, PermissionError):
        pass
    if not os_name:
        try:
            os_name = f"Linux {os.uname().release}"
        except Exception:
            os_name = "Linux"
    info["os"] = os_name
    # Kernel
    try:
        info["kernel"] = os.uname().release
    except Exception:
        info["kernel"] = "—"
    # Host / model
    host = None
    for path in ("/sys/class/dmi/id/product_name", "/sys/devices/virtual/dmi/id/product_name"):
        try:
            with open(path) as f:
                h = f.read().strip()
                if h:
                    host = h
                    break
        except (FileNotFoundError, PermissionError):
            continue
    info["host"] = host or socket.gethostname()
    # Shell
    info["shell"] = os.environ.get("SHELL", "unknown").rsplit("/", 1)[-1]
    # CPU model
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    info["cpu_model"] = line.split(":", 1)[1].strip()
                    break
    except (FileNotFoundError, PermissionError):
        pass
    # Terminal
    info["term"] = os.environ.get("TERM", "") or ""
    # Packages (cached)
    info["packages"] = count_packages()
    return info


def color_palette():
    """A row of 8 ANSI color swatches (neofetch-style)."""
    bg = ["40", "41", "42", "43", "44", "45", "46", "47"]
    return "".join(f"\033[{c}m  \033[0m" for c in bg)


# ─── Rendering Helpers ───────────────────────────────────────────

def bar(percent, width=30, theme=None):
    """Unicode block bar with a bright head. Color shifts by usage."""
    percent = max(0, min(100, percent))
    filled = int((percent / 100) * width)
    if percent < 50:
        color = theme.success
    elif percent < 80:
        color = theme.warning
    else:
        color = theme.danger

    filled_str = "█" * filled
    if 0 < filled < width:
        filled_str = filled_str[:-1] + BOLD + "█" + RESET
    empty_str = "░" * (width - filled)
    return f"{color}{filled_str}{theme.empty_block}{empty_str}{RESET}"


def sparkline(history, width=30, theme=None):
    """Tiny vertical-bar history strip (▁▂▃▄▅▆▇█)."""
    if not history:
        return " " * width
    chars = " ▁▂▃▄▅▆▇█"
    out = ""
    for v in history[-width:]:
        idx = min(8, max(0, int(v / 12.5)))
        ch = chars[idx]
        if v > 80:
            out += f"{theme.danger}{ch}"
        elif v > 50:
            out += f"{theme.warning}{ch}"
        else:
            out += f"{theme.success}{ch}"
    return out + RESET


def format_uptime(seconds):
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    mins = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {mins}m"
    elif hours > 0:
        return f"{hours}h {mins}m"
    else:
        return f"{mins}m"


def format_size(kb):
    """Format a KB/s rate with sensible units (one decimal for small rates)."""
    if kb < 100:
        return f"{kb:.1f} KB/s"
    elif kb < 1024:
        return f"{kb:.0f} KB/s"
    elif kb < 1024 * 1024:
        return f"{kb/1024:.1f} MB/s"
    else:
        return f"{kb/(1024*1024):.2f} GB/s"


def format_mb(mb):
    """Compact memory size: 512M or 1.3G."""
    if mb >= 1024:
        return f"{mb/1024:.1f}G"
    return f"{mb:.0f}M"


# ─── Matrix Rain (background effect) ─────────────────────────────

class MatrixRain:
    def __init__(self, width=76, height=3):
        self.width = width
        self.height = height
        self.columns = [[random.randint(0, 255) for _ in range(height)] for _ in range(width)]
        self.chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789@#$%&*+=?!"

    def step(self):
        for i in range(self.width):
            # Append a DIM int (not a string!) so bright comets fall & fade.
            self.columns[i].append(random.randint(0, 30))
            self.columns[i].pop(0)
            if random.random() < 0.02:
                self.columns[i] = [random.randint(0, 255) for _ in range(self.height)]

    def resize(self, width):
        """Adapt to terminal resizes without resetting the rain."""
        width = max(10, int(width))
        if width == self.width:
            return
        while len(self.columns) < width:
            self.columns.append([random.randint(0, 30) for _ in range(self.height)])
        del self.columns[width:]
        self.width = width

    def render(self, theme):
        lines = []
        for row in range(self.height):
            line = ""
            for col in range(self.width):
                val = self.columns[col][row]
                if val > 200:
                    line += f"{theme.success}{self.chars[random.randint(0, len(self.chars)-1)]}{RESET}"
                elif val > 100:
                    line += f"{theme.primary}{self.chars[random.randint(0, len(self.chars)-1)]}{RESET}"
                else:
                    line += f"{DIM}·{RESET}"
            lines.append(line)
        return "\n".join(lines)


# ─── Main App ────────────────────────────────────────────────────

class NexusApp:
    def __init__(self):
        self.theme_idx = 0
        self.show_matrix = False
        self.sort_by_cpu = True
        self.running = True
        self.prev_cpu = None
        self.frame_count = 0
        self.cpu_history = []       # last 30 CPU samples for sparkline
        self.ram_history = []       # last 30 RAM samples
        self.prev_line_count = 0    # for in-place clearing
        self._prev_proc_ticks = {}  # pid -> cumulative ticks (for per-proc CPU%)
        self._prev_cpu_total = None # aggregate total ticks from last frame
        self._gpu_cache = None      # cached list of GpuInfo
        self._gpu_ts = 0.0          # timestamp of last GPU query
        self._last_uptime = 0.0     # set each frame; safe default for standalone calls

        # Gather static system facts once.
        self.fetch = gather_fetch_info()

        # Responsive layout: measured on startup and re-measured every frame
        # (see _relayout) so the UI scales to whatever space the terminal gives.
        self.term_cols = self.term_rows = None   # first _relayout() fills these in
        self.matrix = MatrixRain(width=80, height=3)
        self._relayout()

        self.input_thread = threading.Thread(target=self._keyboard_listener, daemon=True)
        self.input_thread.start()

    def _relayout(self):
        """Re-measure the terminal and recompute panel sizes (live resize)."""
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
            cols, rows = size.columns, size.lines
        except (OSError, ValueError):
            cols, rows = self.term_cols or 100, self.term_rows or 30
        if cols < 20 or rows < 8:
            if self.term_cols is None:
                # First measurement unusable (e.g. a pty with no winsize yet):
                # seed sane defaults so the first frame can render; a real
                # size will correct this on a later frame.
                cols, rows = 100, 30
            else:
                return   # keep last good layout
        if cols == self.term_cols and rows == self.term_rows:
            return
        self.term_cols, self.term_rows = cols, rows
        # two columns fit from 86 cols down (min panels: 50 + gap 2 + 30)
        if cols >= 86:
            self.two_col = True
            self.gap = 2
            self.right_w = max(30, int((cols - 2) * 0.40))
            self.left_w = max(50, cols - 2 - self.gap - self.right_w)
        else:
            self.two_col = False
            self.gap = 0
            self.left_w = max(46, cols - 2)   # stacked: right panel spans full width
            self.right_w = self.left_w
        if self.two_col:
            self.frame_w = self.left_w + self.gap + self.right_w
        else:
            self.frame_w = self.left_w          # stacked: right panel sits below
        self.matrix.resize(self.frame_w - 2)   # rain rows carry a 2-col indent

    @property
    def theme(self):
        return THEMES[self.theme_idx]

    def _keyboard_listener(self):
        import termios
        import tty
        fd = sys.stdin.fileno()
        try:
            old_settings = termios.tcgetattr(fd)
        except termios.error:
            return   # stdin is not a TTY — no keys to read, stay quiet
        try:
            tty.setcbreak(fd)
            while self.running:
                ch = sys.stdin.read(1)
                if ch == 'q':
                    self.running = False
                elif ch == 't':
                    self.theme_idx = (self.theme_idx + 1) % len(THEMES)
                elif ch == 'm':
                    self.show_matrix = not self.show_matrix
                elif ch == 's':
                    self.sort_by_cpu = not self.sort_by_cpu
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    # ── In-place renderer: no full-screen clear ──────────────────
    def _write_frame(self, lines):
        """Overwrite the previous frame line-by-line (no screen flicker)."""
        buf = ["\033[H"]  # move cursor to top-left (no clear)
        for i, line in enumerate(lines):
            if i > 0:
                buf.append("\n")
            buf.append("\033[K")  # clear from cursor to end of THIS line
            buf.append(line)
        extra = self.prev_line_count - len(lines)
        for _ in range(max(0, extra)):
            buf.append("\n\033[K")
        self.prev_line_count = len(lines)
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def _proc_cpu_percent(self, procs, total_ticks):
        """Real-time per-process CPU% = tick delta since last frame."""
        result = {}
        if self._prev_cpu_total is not None and total_ticks > self._prev_cpu_total:
            dtick = total_ticks - self._prev_cpu_total
            for pid, name, ctime, rss in procs:
                prev = self._prev_proc_ticks.get(pid)
                if prev is not None:
                    delta = max(0, ctime - prev)
                    result[pid] = (delta / dtick) * 100.0
        self._prev_proc_ticks = {pid: ctime for pid, name, ctime, rss in procs}
        self._prev_cpu_total = total_ticks
        return result

    # ── Section primitives (width-aware, ANSI-safe) ──────────────
    def _top(self, title, color, W):
        pre = "┌─[ " + title + " ]"
        return f"{color}{BOLD}┌─[ {title} ]{'─' * max(2, W - len(pre))}{RESET}"

    def _bot(self, color, W):
        return f"{color}{BOLD}└{'─' * (W - 1)}{RESET}"

    def _row(self, color, content):
        return f"{color}{BOLD}│{RESET}  {content}"

    def _barrow(self, color, prefix, percent, suffix="", W=None):
        """A 'prefix [gauge] suffix' row whose bar auto-sizes to fit column W.

        This guarantees the row never overflows its column regardless of how
        wide the numeric prefix (e.g. 'RAM   14.1/ 57.4G') is.
        """
        if W is None:
            W = self.left_w
        avail = W - 3 - vlen(prefix) - vlen(suffix)   # 3 == visible width of "│  "
        bw = max(6, avail)
        return self._row(color, f"{prefix}{bar(percent, bw, self.theme)}{suffix}")

    # ── Left column: live stats ──────────────────────────────────
    def build_left_top(self, stats, compact=False):
        t = self.theme
        W = self.left_w
        L = []
        gw = max(12, W - 16)   # main gauge bar width

        # CPU
        L.append(self._top("CPU", t.secondary, W))
        L.append(self._barrow(t.secondary, f"{stats.cpu_percent:5.1f}%  ", stats.cpu_percent))
        if not compact:
            L.append(self._row(t.secondary, f"hist  {sparkline(self.cpu_history, gw, t)}"))
        la = stats.load_avg
        L.append(self._row(t.secondary, f"load  {la[0]:.2f}  {la[1]:.2f}  {la[2]:.2f}  {DIM}(1/5/15m){RESET}"))
        if not compact:
            L.extend(self._core_grid(stats.per_core))
        if stats.temp_c > 0:
            L.append(self._row(t.secondary, f"temp  {stats.temp_c:.0f}°C  {bar(min(100, stats.temp_c), gw // 2, t)}"))
        L.append(self._bot(t.secondary, W))
        L.append("")

        # MEMORY
        L.append(self._top("MEMORY", t.primary, W))
        L.append(self._barrow(t.primary, f"RAM   {stats.ram_used_mb/1024:5.1f}/{stats.ram_total_mb/1024:5.1f}G  ",
                              stats.ram_percent, f" {stats.ram_percent:.1f}%"))
        if not compact:
            L.append(self._row(t.primary, f"hist  {sparkline(self.ram_history, gw, t)}"))
        swap_pct = (stats.swap_used_mb / max(stats.swap_total_mb, 1)) * 100
        if stats.swap_total_mb > 0:
            L.append(self._barrow(t.primary, f"Swap  {stats.swap_used_mb/1024:5.1f}/{stats.swap_total_mb/1024:5.1f}G  ",
                                  swap_pct, f" {swap_pct:.1f}%"))
        L.append(self._bot(t.primary, W))
        L.append("")

        # STORAGE / NETWORK — every real mount, per-disk I/O, per-iface rates
        L.append(self._top("STORAGE / NETWORK", t.success, W))
        max_disks = 2 if compact else 4
        max_ifaces = 2 if compact else 4
        shown = stats.disks[:max_disks]
        mw = min(10, max((len(mp) for mp, *_ in shown), default=1))
        for mp, dev, used_gb, total_gb, pct in shown:
            dev_disp = dev[5:] if dev.startswith("/dev/") else dev
            prefix = f"{mp:<{mw}} {dev_disp[:9]:<9} "
            suffix = f" {pct:.0f}%"
            if not compact:
                io = stats.disk_io.get(disk_name_for(dev))
                if io:
                    io_s = f"  R {io[0]:.1f}M/s W {io[1]:.1f}M/s"
                    # keep the bar at least 12 cells wide, else drop the I/O text
                    if W - 3 - vlen(prefix) - len(suffix) - len(io_s) >= 12:
                        suffix += io_s
            L.append(self._barrow(t.success, prefix, pct, suffix))
        if len(stats.disks) > max_disks:
            L.append(self._row(t.success, f"{DIM}+{len(stats.disks)-max_disks} more mounts{RESET}"))
        # whole disks present in hardware but not mounted (census completeness)
        if not compact:
            covered = {disk_name_for(dev) for _, dev, *_ in stats.disks if disk_name_for(dev)}
            for d in sorted(set(stats.disk_io) - covered)[:2]:
                r, w = stats.disk_io[d]
                L.append(self._row(t.success, f"{d:<9} {DIM}not mounted{RESET}  R {r:.1f}M/s W {w:.1f}M/s"))
        for name, rx, tx in stats.net_ifaces[:max_ifaces]:
            L.append(self._row(t.success, f"{name:<8} ↓ {format_size(rx):>10}  ↑ {format_size(tx):>10}"))
        if len(stats.net_ifaces) > max_ifaces:
            L.append(self._row(t.success, f"{DIM}+{len(stats.net_ifaces)-max_ifaces} more interfaces{RESET}"))
        L.append(self._bot(t.success, W))
        L.append("")

        return L

    # ── Per-core CPU grid (real data from /proc/stat) ────────────
    def _core_grid(self, percents):
        """Labeled per-core bars: 'C0 [bar] 34%', wrapped to fit the column.

        Picks the bar width that needs the fewest rows (larger bars on ties),
        so 8 cores get one clean row while 64+ cores wrap gracefully.
        """
        t = self.theme
        n = len(percents)
        if n <= 1:
            return []
        inner = self.left_w - 3   # visible width after "│  "
        rows = [self._row(t.secondary, f"cores {DIM}C0–C{n-1} · live per-core CPU%{RESET}")]

        def unit(i, cbw):
            return f"C{i:<2} {bar(percents[i], cbw, t)} {percents[i]:3.0f}%"

        best = None  # ((rows, -cbw), cbw, groups)
        for cbw in (14, 10, 8, 6, 5, 4, 3):
            widths = [vlen(unit(i, cbw)) for i in range(n)]
            if max(widths) > inner:
                continue
            groups, cur, curw = [], [], 0
            for i, w in enumerate(widths):
                add = w + (2 if cur else 0)
                if cur and curw + add > inner:
                    groups.append(cur)
                    cur, curw = [i], w
                else:
                    cur.append(i)
                    curw += add
            groups.append(cur)
            cand = (len(groups), -cbw)
            if best is None or cand < best[0]:
                best = (cand, cbw, groups)
        if best is None:   # too narrow for bars — plain index list
            return [self._row(t.secondary, f"cores {DIM}C0–C{n-1}{RESET}")]
        _, cbw, groups = best
        for grp in groups:
            rows.append(self._row(t.secondary, "  ".join(unit(i, cbw) for i in grp)))
        return rows

    # ── Process table (height scales with terminal) ──────────────
    def build_procs_box(self, stats, n, cpu_pct):
        if n <= 0:
            return []   # no vertical room — section hidden until space allows
        t = self.theme
        W = self.left_w
        L = []
        sort_label = "CPU%" if self.sort_by_cpu else "MEM"
        arrow = " ▲"
        L.append(self._top(f"TOP PROCESSES · {sort_label}{arrow}", t.warning, W))
        procs = list(stats.procs)
        if self.sort_by_cpu:
            procs.sort(key=lambda x: cpu_pct.get(x[0], 0.0), reverse=True)
        else:
            procs.sort(key=lambda x: x[3], reverse=True)
        procs = procs[:max(1, n)]

        pid_w, cpu_w, rss_w = 6, 7, 7
        name_w = max(8, W - (3 + 2 + pid_w + 1 + cpu_w + 1 + rss_w + 2))
        if self.sort_by_cpu:
            cpu_h, rss_h = f"CPU%{arrow}", "RSS"
        else:
            cpu_h, rss_h = "CPU%", f"RSS{arrow}"
        L.append(self._row(t.warning, f"{'PID':>{pid_w}}  {'NAME':<{name_w}} {cpu_h:>{cpu_w+1}} {rss_h:>{rss_w+1}}"))
        for idx, (pid, name, ctime, rss) in enumerate(procs):
            nm = name[:name_w] if len(name) > name_w else name
            pct = cpu_pct.get(pid, 0.0)
            mark = "▶" if idx == 0 else " "
            hl = BOLD if idx == 0 else ""
            end = RESET if idx == 0 else ""
            L.append(self._row(t.warning, f"{mark} {hl}{pid:>{pid_w}}  {nm:<{name_w}} {pct:>{cpu_w-1}.1f}% {format_mb(rss):>{rss_w}}{end}"))
        L.append(self._bot(t.warning, W))
        return L

    # ── Right column: GPU (all of them) + system info ────────────
    def build_right(self, stats, gpus):
        t = self.theme
        W = self.right_w
        R = []
        gpus = gpus or []

        # GPU — every adapter: discrete + integrated
        R.append(self._top("GPU", t.danger, W))
        if not gpus:
            R.append(self._row(t.danger, f"{DIM}no GPU detected{RESET}"))
        for i, gpu in enumerate(gpus):
            label = gpu.name or f"{gpu.vendor} GPU"
            if len(gpus) > 1 and not gpu.name:
                label += f" #{i+1}"
            drv = f" {DIM}· {gpu.driver}{RESET}" if gpu.driver else ""
            R.append(self._row(t.danger, f"{BOLD}{label[:max(6, W-4)]}{RESET}{drv}"))
            has_stats = False
            if gpu.util >= 0:
                R.append(self._barrow(t.danger, f"util  {gpu.util:3.0f}%  ", gpu.util, "", W=W))
                has_stats = True
            if gpu.mem_total_mb > 0:
                vpct = (gpu.mem_used_mb / max(gpu.mem_total_mb, 1)) * 100
                R.append(self._barrow(t.danger, f"vram  {gpu.mem_used_mb/1024:.1f}/{gpu.mem_total_mb/1024:.1f}G ",
                                      vpct, f" {vpct:.0f}%", W=W))
                has_stats = True
            if gpu.temp_c > 0:
                R.append(self._row(t.danger, f"temp  {gpu.temp_c:.0f}°C"))
                has_stats = True
            if gpu.power_w > 0 and gpu.power_max_w > 0:
                ppct = (gpu.power_w / max(gpu.power_max_w, 1)) * 100
                R.append(self._barrow(t.danger, f"pwr   {gpu.power_w:.0f}/{gpu.power_max_w:.0f}W ",
                                      ppct, "", W=W))
                has_stats = True
            elif gpu.power_w > 0:
                R.append(self._row(t.danger, f"pwr   {gpu.power_w:.0f}W"))
                has_stats = True
            if not has_stats:
                R.append(self._row(t.danger, f"{DIM}no usage counter exposed{RESET}"))
        R.append(self._bot(t.danger, W))
        R.append("")

        # SYSTEM (neofetch-style)
        R.append(self._top("SYSTEM", t.primary, W))

        fw = max(8, W - 3 - 10)   # value field width
        disk_names = ({disk_name_for(dev) for _, dev, *_ in stats.disks if disk_name_for(dev)}
                      | set(stats.disk_io))
        rows = [
            ("OS", self.fetch.get("os", "—")),
            ("Host", self.fetch.get("host", "—")),
            ("Kernel", self.fetch.get("kernel", "—")),
            ("Uptime", format_uptime(self._last_uptime)),
            ("Shell", self.fetch.get("shell", "—")),
            ("Pkg", str(self.fetch.get("packages") or "—")),
            ("CPU", self.fetch.get("cpu_model", "—")),
            ("Drives", " · ".join(sorted(disk_names)) or "—"),
            ("Term", self.fetch.get("term", "—") or "—"),
        ]
        for k, v in rows:
            val = str(v)
            if len(val) > fw:
                val = val[:fw-1] + "…"
            R.append(self._row(t.primary, f"{DIM}{k:<9}{RESET}{val}"))
        R.append("")
        R.append(color_palette())

        return R

    # ── Full-width header / footer ───────────────────────────────
    def build_header(self, stats, TW):
        t = self.theme
        fc = self.frame_count
        pulse = "●" if (fc % 4) < 2 else "○"
        worst = max(stats.cpu_percent, stats.ram_percent,
                    max((p for *_, p in stats.disks), default=0))
        if worst > 85:
            sc, sl = t.danger, "CRITICAL"
        elif worst > 60:
            sc, sl = t.warning, "WARNING"
        else:
            sc, sl = t.success, "NOMINAL"

        inner_w = max(10, TW - 2)   # box is exactly TW wide (borders included)
        top = "╔" + "═" * inner_w + "╗"
        bot = "╚" + "═" * inner_w + "╝"
        status = f"{sc}{pulse} {sl}{RESET}"
        brand  = f"{t.primary}{BOLD}N E X U S{RESET}  {DIM}v1.3{RESET}"
        up     = format_uptime(stats.uptime_sec)
        stamp  = f"{DIM}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}"
        # Widest combination that fits; degrade date -> brand as the box narrows.
        for left, right in ((f"{status}   {brand}", f"{stamp}  Up: {up}"),
                            (f"{status}   {brand}", f"Up: {up}"),
                            (status, f"{stamp}  Up: {up}"),
                            (status, f"Up: {up}")):
            if vlen(left) + vlen(right) + 1 <= inner_w:
                break
        inner = " " + pad(left, max(1, inner_w - vlen(right) - 1)) + right
        return [f"{t.primary}{BOLD}{top}{RESET}",
                f"{t.primary}{BOLD}║{RESET}{inner}{RESET}{t.primary}{BOLD}║{RESET}",
                f"{t.primary}{BOLD}{bot}{RESET}"]

    def build_footer(self):
        t = self.theme
        sort_label = "CPU%" if self.sort_by_cpu else "MEM"
        l1 = (f"{DIM}  [q]uit   [t]heme: {self.theme.name:<6}"
              f"  [m]atrix: {'ON' if self.show_matrix else 'off':<3}"
              f"  [s]ort: {sort_label}{RESET}")
        l2 = f"{DIM}  themes: {' · '.join(th.name for th in THEMES)}{RESET}"
        return [l1, l2]

    def _merge(self, left, right):
        h = max(len(left), len(right))
        gap = " " * self.gap
        out = []
        for i in range(h):
            l = left[i] if i < len(left) else ""
            r = right[i] if i < len(right) else ""
            out.append(pad(l, self.left_w) + gap + r)
        return out

    # ── Compose the full frame ───────────────────────────────────
    def _compose(self, stats, gpus, n_procs, cpu_pct, compact):
        left = self.build_left_top(stats, compact) + self.build_procs_box(stats, n_procs, cpu_pct)
        right = self.build_right(stats, gpus)
        if self.two_col:
            header = self.build_header(stats, self.frame_w)
            body = self._merge(left, right)
        else:
            header = self.build_header(stats, self.left_w)
            body = left + [""] + right
        footer = self.build_footer()
        lines = [""] + header + body + footer + [""]

        # Optional matrix rain strip at the very bottom (full frame width).
        if self.show_matrix:
            lines.append("")
            for mline in self.matrix.render(self.theme).split("\n"):
                lines.append("  " + mline)
        return lines

    def build_frame(self, stats, gpus):
        self._relayout()
        self._last_uptime = stats.uptime_sec
        if self.show_matrix:
            self.matrix.step()
        cpu_pct = self._proc_cpu_percent(stats.procs, stats.cpu_total_ticks)

        # Fill vertical space: size the process table to whatever room is left.
        mat_rows = 4 if self.show_matrix else 0           # blank + 3 rain rows
        compact = (self.term_rows - mat_rows) <= 34       # short terminals drop hist/core rows
        left_top = self.build_left_top(stats, compact)
        right = self.build_right(stats, gpus)
        if self.two_col:
            body_fixed = max(len(left_top) + 3, len(right))   # +3 = procs box chrome
        else:
            body_fixed = len(left_top) + 4 + len(right)       # stacked: box chrome + separator
        n_procs = self.term_rows - 1 - 8 - body_fixed - mat_rows
        n_procs = max(3, min(18, n_procs))

        lines = self._compose(stats, gpus, n_procs, cpu_pct, compact)
        while len(lines) > self.term_rows - 1 and n_procs > 0:   # trim; 0 hides the box
            n_procs -= 1
            lines = self._compose(stats, gpus, n_procs, cpu_pct, compact)
        return lines

    def run(self):
        try:
            while self.running:
                self.frame_count += 1
                stats, self.prev_cpu = gather_stats(self.prev_cpu)

                # Update sparkline histories (keep last 30).
                self.cpu_history.append(stats.cpu_percent); self.cpu_history = self.cpu_history[-30:]
                self.ram_history.append(stats.ram_percent); self.ram_history = self.ram_history[-30:]

                # GPU: cache for ~2s so nvidia-smi doesn't add per-frame latency.
                if time.time() - self._gpu_ts >= 2.0 or self._gpu_cache is None:
                    self._gpu_cache = read_gpus()
                    self._gpu_ts = time.time()

                lines = self.build_frame(stats, self._gpu_cache)
                self._write_frame(lines)
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            sys.stdout.write(f"\033[2J\033[H{RESET}")
            sys.stdout.flush()
            print("  NEXUS terminated. Stay cool. ✦")


# ─── Entry Point ─────────────────────────────────────────────────

def main():
    if sys.platform != "linux":
        print("NEXUS requires Linux (reads /proc).")
        print(f"Current platform: {sys.platform}")
        sys.exit(1)
    if not sys.stdout.isatty():
        print("NEXUS needs an interactive terminal (TTY).")
        sys.exit(1)

    t = THEMES[0]
    print(f"\n{t.primary}{BOLD}  Starting NEXUS...{RESET}\n")
    time.sleep(0.3)

    NexusApp().run()


if __name__ == "__main__":
    main()
