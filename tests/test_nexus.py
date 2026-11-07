import importlib.util, os, sys, time

spec = importlib.util.spec_from_file_location("nexus", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "nexus.py"))
nx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nx)

class TS:
    def __init__(self, c, r): self.columns, self.lines = c, r

SIZE = [195, 50]
def fake_get_terminal_size(fd=None):
    return TS(*SIZE)
nx.os.get_terminal_size = fake_get_terminal_size
orig_read_cpu_times = nx.read_cpu_times

app = nx.NexusApp()
prev = None

def frames_at(cols, rows, n=3, matrix=False, sort_cpu=True, must_fit=True):
    global prev
    SIZE[:] = [cols, rows]
    app.show_matrix = matrix
    app.sort_by_cpu = sort_cpu
    for _ in range(n):
        stats, prev = nx.gather_stats(prev)
        lines = app.build_frame(stats, None)
        maxw = max(nx.vlen(l) for l in lines)
        assert maxw <= cols, f"OVERFLOW cols: {cols}x{rows} -> widest line {maxw}"
        if must_fit:
            assert len(lines) <= rows - 1, \
                f"OVERFLOW rows: {cols}x{rows} -> {len(lines)} lines (max {rows-1})"
    return lines

# 1) two-column sizes (>=86 cols): frame MUST fit the viewport
for (c, r) in [(195, 50), (220, 60), (140, 45), (120, 38), (100, 30),
               (96, 28), (90, 40), (86, 30)]:
    lines = frames_at(c, r, n=4)
    print(f"ok  {c}x{r}: frame={len(lines)} rows, two_col={app.two_col}, "
          f"left={app.left_w} right={app.right_w}")

# 2) narrow stacked sizes: no crash, width must fit (may scroll on tiny heights)
for (c, r) in [(80, 24), (70, 30), (60, 40)]:
    frames_at(c, r, n=3, must_fit=False)
    print(f"ok  {c}x{r}: stacked, no crash")

# 3) matrix on: fits where there's room, never crashes
for (c, r) in [(195, 50), (220, 60), (140, 45), (120, 38), (100, 30)]:
    frames_at(c, r, n=3, matrix=True)
for (c, r) in [(96, 28), (80, 24)]:
    frames_at(c, r, n=3, matrix=True, must_fit=False)
print("ok  matrix on/off across sizes")

# 4) both sort modes + all themes
for (c, r) in [(195, 50), (86, 30), (80, 24)]:
    for s in (True, False):
        for t in range(len(nx.THEMES)):
            app.theme_idx = t
            frames_at(c, r, n=2, sort_cpu=s, must_fit=(c >= 86 and r >= 28))
print("ok  sort x theme matrix")

# 5) live resize mid-run (per-frame remeasure == SIGWINCH handling)
stats, prev = nx.gather_stats(prev)
for (c, r) in [(195, 50), (80, 24), (160, 44), (100, 30), (195, 50)]:
    SIZE[:] = [c, r]
    lines = app.build_frame(stats, None)
    maxw = max(nx.vlen(l) for l in lines)
    assert maxw <= c and (len(lines) <= r - 1 or c < 86), f"resize fail at {c}x{r}"
print("ok  live resize")

# 6) per-core delta math, verified against a fixed /proc/stat snapshot
N = os.cpu_count() or 2
def fake_read_cpu_times():
    # deterministic "current" snapshot: aggregate + N cores
    return (80, 200, [(60 + i * 3, 130 + i * 6) for i in range(N)])
nx.read_cpu_times = fake_read_cpu_times
prev_pc = [(50 + i * 2, 100 + i * 4) for i in range(N)]
stats, _ = nx.gather_stats((50, 100, prev_pc))
expected = []
for i in range(N):
    didle = (60 + i * 3) - (50 + i * 2)
    dtick = (130 + i * 6) - (100 + i * 4)
    expected.append(max(0, min(100, (1 - didle / dtick) * 100)))
assert len(stats.per_core) == N, (len(stats.per_core), N)
for a, b in zip(stats.per_core, expected):
    assert abs(a - b) < 0.001, (a, b)
assert abs(stats.cpu_percent - 70.0) < 0.001, stats.cpu_percent
print(f"ok  per-core delta math: {N} cores, values match hand-computed /proc/stat deltas")

# 6b) core grid rendering: legend + every core labeled (real /proc data path)
def real_percents(prev_pc):
    _, _, pc = nx.read_cpu_times()
    out = []
    for i in range(min(len(prev_pc), len(pc))):
        dt = pc[i][1] - prev_pc[i][1]
        out.append(max(0, min(100, (1 - (pc[i][0] - prev_pc[i][0]) / max(dt, 1)) * 100)) if dt > 0 else 0.0)
    return out

nx.read_cpu_times = orig_read_cpu_times
_, _, prev_pc = orig_read_cpu_times()
time.sleep(0.3)
expected = real_percents(prev_pc)
stats, _ = nx.gather_stats((None, None, prev_pc)) if False else (None, None)
# proper call: read a real prev aggregate too
prev_idle, prev_total, prev_pc2 = orig_read_cpu_times()
time.sleep(0.3)
expected = real_percents(prev_pc2)
stats, _ = nx.gather_stats((prev_idle, prev_total, prev_pc2))
SIZE[:] = [195, 50]
lines = app.build_frame(stats, None)
text = "\n".join(nx.strip_ansi(l) for l in lines)
assert f"cores C0–C{len(expected)-1}" in text, "core legend missing"
for i in range(len(expected)):
    assert f"C{i}" in text, f"core label C{i} missing"
assert len(stats.per_core) == len(expected), (len(stats.per_core), len(expected))
print(f"ok  per-core render: {len(expected)} cores listed with legend")

# 7) process table grows with available height (sandbox has few procs -> inject 40)
from dataclasses import replace
fake_procs = [(1000 + i, f"proc{i:02d}", i * 100, 10 + i) for i in range(40)]
SIZE[:] = [195, 60]; app.show_matrix = False
stats_tall = replace(nx.gather_stats(None)[0], procs=fake_procs)
tall = app.build_frame(stats_tall, None)
SIZE[:] = [195, 30]
stats_short = replace(nx.gather_stats(None)[0], procs=fake_procs)
short = app.build_frame(stats_short, None)
def proc_rows(lines):
    n, on = 0, False
    for l in (nx.strip_ansi(x) for x in lines):
        if "TOP PROCESSES" in l:
            on = True
            continue
        if on and l.startswith("└"):
            break
        if on and l.startswith("│") and "PID" not in l:
            n += 1
    return n
print(f"ok  proc table: tall={proc_rows(tall)} rows vs short={proc_rows(short)} rows")
assert proc_rows(tall) > proc_rows(short), "table should grow with height"

# 8) matrix strip spans full frame width
SIZE[:] = [195, 50]; app.show_matrix = True
stats, _ = nx.gather_stats(None)
lines = app.build_frame(stats, None)
rain = [l for l in lines if "·" in l or any(ch.isalpha() for ch in nx.strip_ansi(l)[-20:])]
if rain:
    w = max(nx.vlen(l) for l in lines[-4:])
    assert abs(w - app.frame_w) <= 1, (w, app.frame_w)
print(f"ok  matrix spans full width ({app.frame_w} cols)")

# 9) network section shows a REAL per-second rate per interface, not
#    cumulative totals (regression: /proc/net/dev holds since-boot counters)
calls = {"n": 0}
def fake_read_net():
    calls["n"] += 1
    # simulate eth0: +1 MiB RX and +256 KiB TX per call; wifi idle
    return {"eth0": (calls["n"] * 1048576, calls["n"] * 262144),
            "wlp0s0": (123, 45)}
orig_read_net = nx.read_net
nx.read_net = fake_read_net
stats1, prev1 = nx.gather_stats(None)
assert stats1.net_ifaces == [], \
    "first frame has no baseline -> must be empty, not a bogus number"
time.sleep(0.5)
stats2, _ = nx.gather_stats(prev1)
by_name = {n: (rx, tx) for n, rx, tx in stats2.net_ifaces}
# 1 MiB / ~0.5s == 2048 KB/s; 256 KiB / ~0.5s == 512 KB/s (jitter allowed)
assert 1500 < by_name["eth0"][0] < 3000, f"rx rate wrong: {by_name.get('eth0')}"
assert 400 < by_name["eth0"][1] < 650, f"tx rate wrong: {by_name.get('eth0')}"
assert by_name["wlp0s0"] == (0.0, 0.0), "unchanged counter must be exactly 0.0"
nx.read_net = orig_read_net
print(f"ok  net rate delta per-iface: eth0 rx={by_name['eth0'][0]:.0f} KB/s "
      f"tx={by_name['eth0'][1]:.0f} KB/s; idle iface pinned at 0.0 (no fake GB/s)")

# 11) hardware census: multiple mounts deduped by device, real per-disk I/O rates
import io as _io
# 11a) real read_mounts against a synthetic /proc/mounts: virtual fs excluded,
#      bind-mount storm collapses to the shortest mountpoint, sorted with / first
fake_mounts_file = "\n".join([
    "/dev/nvme0n1p2 / ext4 rw 0 0",
    "/dev/sdb1 /home ext4 rw 0 0",
    "/dev/sdb1 /home/user/sub/bind ext4 rw 0 0",   # bind -> dedupes to /home
    "tmpfs /run tmpfs rw 0 0",                      # virtual fs -> excluded
    "/dev/sr0 /media/cdrom iso9660 ro 0 0",
]) + "\n"
real_open = open
def fake_open(path, *a, **k):
    if str(path) == "/proc/mounts":
        return _io.StringIO(fake_mounts_file)
    return real_open(path, *a, **k)
nx.open = fake_open
assert nx.read_mounts() == [("/", "/dev/nvme0n1p2"), ("/home", "/dev/sdb1"),
                            ("/media/cdrom", "/dev/sr0")], nx.read_mounts()
del nx.open
# 11b) gather_stats: statvfs per mount + sector-delta I/O rates
orig_read_mounts = nx.read_mounts
orig_diskstats = nx.read_diskstats_raw
nx.read_mounts = lambda: [("/", "/dev/nvme0n1p2"), ("/tmp", "/dev/sr0"),
                          ("/home", "/dev/sdb1")]
nx.read_diskstats_raw = lambda: {"nvme0n1": (50_000, 10_000), "sdb": (7_000, 3_000)}
s_a, prev_a = nx.gather_stats(None)
assert [mp for mp, *_ in s_a.disks] == ["/", "/tmp", "/home"], s_a.disks
assert s_a.disk_io == {}, "first frame has no baseline -> empty, not fake rates"
time.sleep(0.4)
# bump counters: exactly +1M sectors read on nvme0n1 (~488 MiB), +100k written on sdb
nx.read_diskstats_raw = lambda: {"nvme0n1": (1_050_000, 10_000), "sdb": (7_000, 103_000)}
s_b, prev_b = nx.gather_stats(prev_a)
dt = max(prev_b[5] - prev_a[5], 1e-9)   # exact dt gather_stats used
assert s_b.disk_io["nvme0n1"] == ((1_000_000 * 512 / dt) / 1024**2, 0.0), s_b.disk_io
assert s_b.disk_io["sdb"] == (0.0, (100_000 * 512 / dt) / 1024**2), s_b.disk_io
nx.read_mounts, nx.read_diskstats_raw = orig_read_mounts, orig_diskstats
# render: multi-mount frame shows both mounts + I/O suffix on wide terminal
SIZE[:] = [195, 50]
fake_stats = replace(s_b, disks=[("/", "/dev/nvme0n1p2", 45.0, 100.0, 45.0),
                                 ("/home", "/dev/sdb1", 12.0, 50.0, 24.0)],
                     disk_io={"nvme0n1": (512.0, 0.0), "sdb": (0.0, 48.8)},
                     net_ifaces=[("eth0", 2048.0, 512.0)])
lines = app.build_frame(fake_stats, None)
text = "\n".join(nx.strip_ansi(l) for l in lines)
assert "/home" in text and "sdb1" in text, "second mount missing from STORAGE box"
assert "R 512.0M/s" in text, "per-disk I/O rate not rendered"
print("ok  disk census: bind-mount dedup, real sector->MiB/s deltas, multi-mount render")

# 12) GPU census: every adapter listed, unknown stats shown as 'not exposed'
gpus = [nx.GpuInfo(vendor="NVIDIA", name="GeForce RTX 3090", util=78.0,
                   mem_used_mb=12_300, mem_total_mb=24_576, temp_c=61.0,
                   power_w=285.0, power_max_w=350.0, driver="550.54.15"),
        nx.GpuInfo(vendor="AMD", name="AMD GPU", util=12.0, driver="amdgpu")]
SIZE[:] = [195, 50]
lines = app.build_frame(fake_stats, gpus)
text = "\n".join(nx.strip_ansi(l) for l in lines)
assert "GeForce RTX 3090" in text and "550.54.15" in text, "NVIDIA name+driver missing"
assert "AMD GPU" in text and "amdgpu" in text, "AMD iGPU missing"
# stats-less adapter must say so instead of faking numbers
gpus2 = [nx.GpuInfo(vendor="Intel", name="Intel iGPU")]
lines2 = app.build_frame(fake_stats, gpus2)
text2 = "\n".join(nx.strip_ansi(l) for l in lines2)
assert "Intel iGPU" in text2 and "no usage counter exposed" in text2
# no GPUs at all -> honest fallback line
lines3 = app.build_frame(fake_stats, [])
assert "no GPU detected" in "\n".join(nx.strip_ansi(l) for l in lines3)
print("ok  gpu census: multi-GPU render, driver shown, 'not exposed' instead of fake stats")

# 12b) lspci fallback parser (names only — used when smi/sysfs are invisible)
class FakeProc:
    returncode = 0
    stdout = ("01:00.0 VGA compatible controller: NVIDIA Corporation GA102 [GeForce RTX 3090] (rev a1)\n"
              "10:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Raphael (rev c2)\n"
              "00:02.0 Host bridge: Intel Corporation Device 4638\n")
orig_run = nx.subprocess.run
nx.subprocess.run = lambda *a, **k: FakeProc()
nx._lspci_cache = None
parsed = nx._read_lspci_gpus()
nx.subprocess.run = orig_run
assert [g.name for g in parsed] == ["GA102 [GeForce RTX 3090]", "Raphael"], parsed
assert all(g.util < 0 and g.mem_total_mb == 0 for g in parsed), "fallback must not invent stats"
print("ok  lspci fallback: real names, zero invented numbers")

# 13) SYSTEM panel lists every whole disk (hardware census completeness)
SIZE[:] = [195, 50]
lines = app.build_frame(replace(fake_stats, disk_io={"nvme0n1": (1.0, 2.0), "sda": (0.0, 0.0)}), [])
text = "\n".join(nx.strip_ansi(l) for l in lines)
assert "Drives" in text and "nvme0n1 · sda · sdb" in text, \
    f"Drives row missing from SYSTEM panel: {text[-400:]}"
print("ok  system panel: Drives row lists whole disks")

# 10) temperature reader never crashes and returns -1.0 or a float
import tempfile, shutil as _sh
t = nx.read_temperature()
assert t == -1.0 or isinstance(t, float), t
print(f"ok  temperature: {t} (best-effort, hidden when unavailable)")

print("\nALL TESTS PASSED")
