# NEXUS — Terminal Mission Control

A zero-dependency, live system monitor for Linux that looks like a spaceship cockpit.
One Python file. No `pip install` packages, no apt deps, no daemons — it reads
`/proc`, `sysfs` and (when present) `nvidia-smi` directly, renders in-place at 1 Hz,
and rescales itself to whatever terminal size you throw at it.

[![CI](https://github.com/jake1402/nexus/actions/workflows/ci.yml/badge.svg)](https://github.com/jake1402/nexus/actions/workflows/ci.yml)

## What you get

| Panel | Details |
|---|---|
| **CPU** | Aggregate bar + history sparkline, load average (1/5/15 m), live per-core bars labeled `C0…Cn` |
| **Memory** | RAM + swap bars with green → amber → red thresholds and a usage history strip |
| **GPU** | Full hardware census: NVIDIA (util, VRAM, temp, power, driver via `nvidia-smi`), AMD (`amdgpu` sysfs), Intel iGPU — *every* adapter is listed; where no counter is exposed it says so instead of faking numbers |
| **Storage** | Every real mount with a usage bar plus live per-disk R/W rates (sector deltas from `/proc/diskstats`); unmounted whole disks are listed too |
| **Network** | Per-interface ↓/↑ throughput as true per-second rates — never since-boot counters, never placeholder GB/s |
| **Processes** | Top-N by CPU% or RSS; the table grows with terminal height |
| **System** | neofetch-style panel: OS, host, kernel, uptime, shell, CPU model, drive census |

Extras: five color themes (`t`), a Matrix-rain strip (`m`), dynamic layout — two columns on wide terminals (≥ 86 cols), stacked when narrow — with live resize handling.

## Preview

Sample frame captured on a real machine (Ryzen 9 7900X + RTX 3090, 195×50 terminal):

```

╔═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║ ○ NOMINAL   N E X U S  v1.3                                                                                                                                 2026-11-07 14:16:14  Up: 1d 1h 45m║
╚═══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝
┌─[ CPU ]─────────────────────────────────────────────────────────────────────────────────────────────────────────  ┌─[ GPU ]────────────────────────────────────────────────────────────────────
│    7.8%  ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │  GA102 [GeForce RTX 3090]
│  hist                                                                                                             │  no usage counter exposed
│  load  1.93  2.10  2.01  (1/5/15m)                                                                                │  Raphael
│  cores C0–C23 · live per-core CPU%                                                                                │  no usage counter exposed
│  C0  ░░░  24%  C1  ░░░  13%  C2  ░░░   5%  C3  ░░░   5%  C4  ░░░   5%  C5  ░░░   3%  C6  ░░░   2%  C7  ░░░   0%   └────────────────────────────────────────────────────────────────────────────
│  C8  ░░░   2%  C9  ░░░   3%  C10 ░░░   0%  C11 ░░░   0%  C12 ░░░  13%  C13 ░░░   5%  C14 ░░░   5%  C15 ██░  95%
│  C16 ░░░   0%  C17 ░░░   2%  C18 ░░░   0%  C19 ░░░   2%  C20 ░░░   3%  C21 ░░░   2%  C22 ░░░   0%  C23 ░░░   0%   ┌─[ SYSTEM ]─────────────────────────────────────────────────────────────────
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────  │  OS       Linux 7.0.0-42-generic
                                                                                                                    │  Host     jake1402-MS-7D75
┌─[ MEMORY ]──────────────────────────────────────────────────────────────────────────────────────────────────────  │  Kernel   7.0.0-42-generic
│  RAM    24.0/ 57.4G  ███████████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 41.8%  │  Uptime   1d 1h 45m
│  hist  ▃▃▃▃▃▃                                                                                                     │  Shell    unknown
│  Swap    0.0/  8.0G  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 0.0%  │  Pkg      —
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────  │  CPU      AMD Ryzen 9 7900X 12-Core Processor
                                                                                                                    │  Drives   nvme0n1 · nvme1n1 · sda
┌─[ STORAGE / NETWORK ]───────────────────────────────────────────────────────────────────────────────────────────  │  Term     dumb
│  /opt nvme1n1p6 ██████████████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ 47%  R 0.0M/s W 0.2M/s
│  nvme0n1   not mounted  R 0.0M/s W 0.0M/s
│  sda       not mounted  R 0.0M/s W 0.0M/s
│  enp12s0  ↓   0.0 KB/s  ↑   0.0 KB/s
│  wlp13s0  ↓   0.7 KB/s  ↑   0.0 KB/s
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

┌─[ TOP PROCESSES · CPU% ▲ ]──────────────────────────────────────────────────────────────────────────────────────
│     PID  NAME                                                                                    CPU% ▲      RSS
│  ▶      1  bwrap                                                                                    0.0%      2M
│         2  bash                                                                                     0.0%      4M
│         8  python3                                                                                  0.0%     19M
│         9  sed                                                                                      0.0%      3M
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  [q]uit   [t]heme: CYBER   [m]atrix: off  [s]ort: CPU%
  themes: CYBER · NEON · ICE · EMBER · GHOST

```

## Install (Ubuntu)

Requires **Python 3.8+** — already present on Ubuntu 20.04 / 22.04 / 24.04.
That's the *only* requirement.

### Option A — clone & run

```bash
git clone https://github.com/jake1402/nexus.git
cd nexus
python3 nexus.py
```

### Option B — install as a `nexus` command

With [pipx](https://pypa.github.io/pipx/) (recommended) or plain pip:

```bash
git clone https://github.com/jake1402/nexus.git
cd nexus
pipx install .               # or: python3 -m pip install --user .
nexus                        # run it from anywhere
```

(If you don't have `pipx` yet, install it via your distro's package manager
or follow the link above.)

### Option C — one-liner

```bash
git clone -q https://github.com/jake1402/nexus.git /tmp/nexus && bash /tmp/nexus/install.sh
```

`install.sh` copies the app to `~/.local/bin/nexus` (and reminds you if that
directory isn't on your `PATH`). To uninstall, simply delete
`~/.local/bin/nexus`, or run `pipx uninstall nexus`.

## Usage

| Key | Action |
|---|---|
| `q` | Quit (restores your terminal cleanly) |
| `t` | Cycle theme — CYBER → NEON → ICE → EMBER → GHOST |
| `m` | Toggle the Matrix-rain strip |
| `s` | Sort processes by CPU% / MEM |

Just resize the window — the layout reflows on the next frame.

## How it works (and what it refuses to do)

- **CPU** — deltas between reads of `/proc/stat` (aggregate + per-core). No
  sampling tricks, no invented percentages.
- **Memory / swap** — `/proc/meminfo`.
- **Disks** — real mounts from `/proc/mounts` (virtual filesystems excluded,
  bind-mount storms deduped to the shortest mountpoint) with `statvfs` usage,
  and per-disk I/O computed as sector deltas from `/proc/diskstats`.
- **Network** — per-interface rates from `/proc/net/dev`; the first frame after
  start shows no rate until a baseline exists, and idle interfaces pin at `0.0`.
- **GPU** — `nvidia-smi --query-gpu` for NVIDIA; `amdgpu_pm*` sysfs files for
  AMD; an `lspci` fallback lists adapters whose counters aren't exposed, with
  "no usage counter exposed" shown instead of made-up stats.
- **Temperature** — best-effort from `/sys/class/thermal`; hidden when unavailable.

## Development

```bash
make run     # run the app
make test    # offline test suite: frame fitting at many terminal sizes,
             # per-core delta math, mount dedup, net/disk rate deltas, GPU census
make install # install to ~/.local/bin
```

The tests run headless against real `/proc` plus synthetic fixtures — no GPU or
root required.

## License

MIT — see [LICENSE](LICENSE).
