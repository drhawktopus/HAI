# GT 730 / Kepler GPU on Ubuntu 24.04 — nvidia-470 driver + llama.cpp (the noble-updates STUB trap)

How to get an NVIDIA **GeForce GT 730** (or any **Kepler**, compute capability 3.5) working on
**Ubuntu 24.04 (noble)**, and how it performs for LLM inference. Bring-up done 2026-08-28 on `rigby`
(basement x86 node). Search terms: GT 730, Kepler, GK208, nvidia-driver-470, noble-security,
`nvidia-smi` no devices, `GGML_VK_DISABLE_F16`, Vulkan hang, MoE attention offload, `--no-op-offload`,
Vulkan in a container, `Found no drivers`, `vk_icdGetInstanceProcAddr`, GTX 1060 `Xid 79` / `GPU has fallen off
the bus` / `RmInitAdapter failed`, `RIGBY CARD NOT VISIBLE`, rigby card keeper / automatic reboot
(`rigby-card-keeper.timer`, `card_wanted.json`, `card_keeper.json`).

## Context that fits on the card — rigby's A1 MoE (measured 2026-10-09)
**Tested: 65,536 loads, 98,304 fails to create its context.** What the card holds for the attention placement: the
ten full-attention layers' weights (191 MiB) plus their KV = 10 layers × 2 KV heads × 256 dims × 2 (K,V) × 2 bytes =
**20 KiB per token** (f16), plus compute buffers. On 2 GiB: 32k ≈ 1.1 GiB total, 65k ≈ 1.7 GiB, 98k ≈ 2.3 GiB →
ceiling ~72–76k. An 8-bit KV cache halves the 20 KiB (≈140k) — the lab's standard on Mothership; held in reserve.
Probe: `inspect_lab\submit_speed_probe.py --only=gt730-attn-nooff --ctx=N` (the KV is allocated at load, so a clean
load is the test). Why it was measured: 32,768 came from the one-shot menu and agentic A1 turns overran it.

## TL;DR — is it worth it?

**As a whole-model GPU: no. As the attention helper for rigby's MoE: yes, at long context.**

- **Whole model on the card** (2026-08-28): a GT 730 is **2.5–4.3× SLOWER than the CPU** at token generation
  (measured, r=5), and only ties it on prompt-processing. Reason below (no DP4A on Kepler).
- **The MoE's ten full-attention layers + their KV on the card, everything else on the CPU** (2026-10-07,
  Qwen3.6-35B-A3B Q3_K_XL at ctx 32,768, Tolkien `llm` orders `speedprobe-rigby-moe-*`, every rewrite graded 5/5):

  | | CPU only | GT 730 attention, `--no-op-offload` |
  |---|---|---|
  | prompt read, 2k / 12k context | 14.4 / 12.3 tok/s | 13.6 / 10.8 tok/s |
  | writing at 2k context | **5.5 tok/s** | 4.3 tok/s |
  | writing at 12k context | 2.7 tok/s | **3.3 tok/s (+25%)** |

  The card's per-layer hops cost short-context writing; CPU attention cost grows with context, so the card wins
  long turns (an agent's long rewrites). **`--no-op-offload` is mandatory:** with llama.cpp's default op offload,
  every prompt batch streams the host-resident weights over PCIe to the card and reading drops to **2.8 tok/s**
  (5× slower), and n-gram speculation's verify batches collapse writing to 0.5 tok/s. N-gram speculation also
  LOSES on this MoE on the CPU (−29 to −34% writing, ~33% draft acceptance) - leave it off.
- Placement (verified against the GGUF: full attention is blocks 3,7,…,39; 191 MiB of weights + ~0.67 GB of KV
  at 32k): `-ngl 99 -ot "blk\.(?!(3|7|11|15|19|23|27|31|35|39)\.).*=CPU" -ot "ffn_=CPU" -ot "output\.=CPU"
  --no-op-offload`. As a Tolkien order: `gpu_layers`, `override_tensor`, `op_offload: false` (tolkien_llm);
  `submit_s10c.py --gt730` / `submit_speed_probe.py` carry it.

## Running the Vulkan build INSIDE a container (Tolkien) — the host loader, not the image's glibc

The 470 driver's Vulkan ICD will not start under a newer distro's glibc inside a container: the loader reports
`Could not get 'vkCreateInstance' via 'vk_icdGetInstanceProcAddr' for ICD libGLX_nvidia.so.0` → `Found no
drivers!`, even with every NVIDIA lib, `/dev/nvidia*`, the ICD json and `--privileged`. Measured with throwaway
containers (2026-10-06): it works only with BOTH (a) the server run under the HOST's loader and libraries and
(b) the host's `/usr/share/glvnd`. Tolkien's compose mounts `/usr/lib/x86_64-linux-gnu:/hostlib:ro`,
`/usr/share/glvnd`, the ICD json and the four `/dev/nvidia*` nodes, and launches
`/hostlib/ld-linux-x86-64.so.2 --library-path /llama/vulkan:/hostlib /llama/vulkan/llama-server …` with NO
`LD_LIBRARY_PATH` (`tolkien_llm.server_argv`). Positive control: the host lists the card with `env -i`. Check from
inside: the same command with `--list-devices` → `Vulkan0: NVIDIA GeForce GT 730 (2048 MiB …)`.

**Root cause (community-confirmed, llama.cpp maintainers):** fast quantized (Q4) inference needs
**integer dot-product intrinsics (DP4A)**, which exist only on **Pascal (2016) or newer** NVIDIA GPUs.
**Kepler (2012) lacks them** — the Vulkan device line shows `int dot: 0`. So a Kepler card must
**dequantize Q4→fp32 and do unaccelerated fp32 math**; the dequant, not the matmul, is the choke, and
the card runs at only ~15% of its paper FLOPS. This is **architectural, not a backend/config issue** —
CUDA 11 on Kepler would lack DP4A too, so the CUDA path (already a yak-shave, below) would not help.
More cores don't rescue it: the parallelism is real, but llama.cpp's quantized path can't reach it on
this silicon. So never put a whole model (or the quantized expert weights) on it; the small attention slice of an
MoE, where the cost is attention over a long KV rather than quantized matmuls, is the use that pays (TL;DR). The driver procedure below is
still worth keeping — it is the reproducible way to light up ANY Kepler card here, and it is not obvious.

## Constraints (why this is fiddly)

- Kepler (cc 3.5) needs the **470 legacy driver** — the last branch that supports it.
- **CUDA 12 dropped Kepler entirely** → you cannot build CUDA code (incl. llama.cpp CUDA) for it with
  a modern toolkit. CUDA 11.x is the last Kepler toolkit, and it has **no Ubuntu 24.04 packages** and
  needs **gcc ≤ 11** (noble ships 13). So the CUDA path is a multi-step yak-shave; prefer **Vulkan**.
- Kernel 6.10+ removed `follow_pfn`, which breaks the 470 module. Ubuntu 24.04's 6.8 kernels are fine
  (470 dkms builds against 6.8.0-31 and 6.8.0-138 both).

## THE TRAP — `noble-updates` ships a STUB 470

On Ubuntu 24.04, a plain `apt install nvidia-driver-470` installs a **stub** package (~21 KB) from the
**`noble-updates`** pocket whose `Depends:` pull in the **535/580** driver — which is **blind to
Kepler**, so `nvidia-smi` reports **no devices** and the card never comes up. The **real** 470 driver
(132 MB, `470.256.02-0ubuntu0.24.04.1`) lives in **`noble-security`**. You must pin the stub out.

## The fix (verified working)

Run as root. Author scripts with a real editor, not a heredoc.

```bash
# 1. Clean slate — compute exact package names from dpkg (apt does NOT glob "nvidia*470*").
PKGS=$(dpkg -l | awk '/nvidia/{print $2}' | grep -viE "^linux-image|^linux-headers")
[ -n "$PKGS" ] && apt-get purge -y $PKGS

# 2. Pin the STUB 470 out of noble-updates so apt takes the real one from noble-security.
cat > /etc/apt/preferences.d/nvidia470 <<'EOF'
Package: *nvidia*470*
Pin: release a=noble-updates
Pin-Priority: -1
EOF
apt-get update

# 3. Confirm the candidate is now the real ...24.04.1 (from noble-security), not ...24.04.5 (stub).
apt-cache policy nvidia-dkms-470 | grep -E "Candidate|24.04"

# 4. Headers for the running GA kernel + dkms, then the driver (dkms builds the module).
apt-get install -y --no-install-recommends linux-headers-$(uname -r) dkms build-essential
apt-get install -y nvidia-driver-470

# 5. VERIFY (do not trust exit 0):
nvidia-smi                                    # want: 470.256.02 / CUDA 11.4, the card listed
dkms status | grep nvidia                     # want: installed, for your kernel(s)
find /usr/lib -name "libnvidia-ml.so.470*"    # real NVML on disk
```

A reboot loads the fresh module cleanly if an old one is resident. No kernel pin is needed long-term
(dkms rebuilds on kernel upgrade), but confirm `dkms status` after any kernel bump.

## Two cards on rigby (2026-10-13): GT 730 + GTX 1060 — pass EVERY `/dev/nvidiaN`, pick the card BY NAME

- **Device nodes follow the slot, not the card.** The 1060 (x16 CPU slot, on a full-width riser: PCIe 3.0 x16) is `nvidia0`;
  the GT 730 (chipset slot) is `nvidia1`. Tolkien's compose passed only `/dev/nvidia0`, so inside the containers
  `--list-devices` and `nvidia-smi` showed ONE card (whichever was nvidia0) — it reads like "Vulkan cannot see the
  730" and is purely the device list. Fixed: both containers get `/dev/nvidia0` AND `/dev/nvidia1`
  (`/opt/arrstack/docker-compose.yml`, backup `.bak-20261013-185319-before-nvidia1`). Inside: `Vulkan0: GTX 1060 6GB`,
  `Vulkan1: GT 730`.
- **Pick by name, per llama.cpp `docs/multi-gpu.md`:** `--list-devices`, then `-dev <VulkanN> -sm none` for one card;
  with two visible cards an unpinned `-ngl` placement is split across BOTH. Tolkien: a placement's `gpu`
  (`gt730` default | `gtx1060`) → `tolkien_llm.vulkan_device` resolves the name; the Kepler shader flags apply to the
  730 only (the 1060 is Pascal: DP4A, so quantized experts belong on it, never on the 730).
- **⚠ `vulkaninfo --summary` HUNG in the driver on the host** (uninterruptible, load 12, `pgrep` hung with it; only a
  power cycle cleared it). Probe with `nvidia-smi`, `lspci` and `llama-server --list-devices` only.
- **Slots:** the chipset slots are PCIe 2.0 (the 730 links x4 there), the CPU slot 3.0 — the 1060 belongs in the CPU
  slot because the streamed-weights prompt read is link-bound. Since 2026-10-26 it sits there on a full-width riser at
  Gen3 x16 (sysfs `max_link_width 16`; it idles at 2.5 GT/s, the normal downclock): ~2K-token reads went 101.5 →
  350 tok/s, 3.5×, against the identical order on the old x1 riser ([[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]]
  § 8). The 730 needs no bandwidth.
- **⚠ `Xid 79, GPU has fallen off the bus` on the 1060 (2026-10-13 19:23 UTC, first sustained load after two short
  probes):** then `RmInitAdapter failed! (0x22:0x56:667)` every 5 s, host `nvidia-smi` lists only the 730, and
  `--list-devices` shows the 730 as `Vulkan0` (the index shifts — another reason to pick by name). **Recovery: a REBOOT
  of rigby** (no power cycle needed; `Lab\bin\run_remote.py rigby <script>` with `systemctl reboot`; verify by
  `uptime -s`, never by ssh answering). ⚠ **A rigby reboot drops the tablets' USB (RNDIS) wire links**: adb health still
  reads OK, but warm / stage servers on the wire IPs are unreachable until Tolkien's USB keeper restores them (10-15: the
  07:09 reboot, restored at 07:43 — `[DESK] USB keeper: <node> restored`). Before Fellowship work after a reboot, wait
  for those lines (r75 was voided by this). Power: the card's 6-pin has been on the separate 600 W PSU **since it was
  installed** (the riser's slot power is rigby's; switch the 600 W on with or before rigby) — the PSU was never a fix,
  both drops happened on this same power setup. Drops so far: 2026-10-13 19:23 UTC (first sustained load) and
  **2026-10-14 20:57:47 UTC AT IDLE** (telemetry: ~25 W, 30 °C, 0 MB, no job since 19:58 UTC; no AER errors before it),
  ~27 h after the reboot that recovered the first, **2026-10-15 09:37:24 UTC, idle again** (~10.5 h after the
  reboot), and **2026-10-17 01:38:26 UTC** (~24 h after the 2026-10-16 01:39 UTC boot; `Xid 79, pid=863622`; then
  1,556 `RmInitAdapter failed` lines; found 06:21 UTC when a placed piece resolved to it), and **2026-10-17 13:45:14 UTC**
  (the FIFTH, ~7 h after the 06:45 UTC boot; 3,610 `RmInitAdapter failed` lines; nothing needed the card until 16:08 UTC,
  when the card keeper rebooted rigby by itself), and **2026-10-24 01:42:46 UTC** (the SIXTH, ~1.7 h after the 00:01 UTC
  boot; `Xid 79, pid=41534`; **54,274** `RmInitAdapter failed` lines over ~36 h — nothing wanted the 1060 until an
  embed-check order at 2026-10-25 13:58:11 UTC; the keeper rebooted rigby at 13:58:44, the order ran by itself after
  the boot. ⚠ A drop is SILENT until an order asks for the card: Fellowship chat turns run on the GT 730, so the 1060
  was dead for a day and a half while its menu rows stayed priced) → the old x1 riser was the leading suspect
  [inferred]; the owner replaced it on 2026-10-26 with a full-width riser (x16 measured). Whether the drops stop is unproven
  until rigby runs longer than the old gaps (2–36 h) without one — check `dmesg | grep -c "Xid 79"` since that boot.
  **Since 2026-10-17 Tolkien no longer VOIDs orders behind a
  drop:** an order placing work on a card its build cannot see WAITS queued (one `RIGBY CARD NOT VISIBLE` DM per drop)
  and runs by itself after the reboot; a placed piece skips rigby's row ([[Fellowship-Order-Taker-Design]] § PLACED
  orders). **And the reboot is automatic — rigby's CARD KEEPER (installed 2026-10-17, the owner: *"adding a quick reboot
  once it sees an order and no card is a good thing to do for now"*).** Tolkien cannot reboot its own host, so while an
  order waits it leaves `card_wanted.json` in its state dir (`/opt/arrstack/tablet-foundry/state`, refreshed every lane
  retry); a root timer on rigby's host (`rigby-card-keeper.timer`, every 120 s; `/usr/local/bin/rigby_card_keeper.py`)
  runs `systemctl reboot` when a card rigby HAS is missing from `nvidia-smi -L` — the cards a fresh marker names (3× its
  stated period), or, **since 2026-10-25, with NO order waiting at all** (the owner: *"Reboot it when we notice it's down"*;
  the 10-24 drop sat ~36 h because nothing asked for the 1060) — BOTH lanes are idle (no `CURRENT_ORDER*.json`) and it
  has not already rebooted for this drop. (Installed 2026-10-25 14:10 UTC, Tolkien gate 966; image = mirror = host
  sha256 `0dfbf314…`; live decoy on rigby with the installed module: real list → none, 1060 removed → reboot, 1060
  removed + busy lane → none.) ⚠ A no-order reboot is announced only in the keeper's journal and `card_keeper.json` —
  Tolkien's DM fires when an order waits. If the card is still
  missing in the boot after its reboot — or a later run finds rigby never rebooted — it GIVES UP (`gave_up` +
  `gave_up_why` in `card_keeper.json`) and reboots no more until the card is seen again; Tolkien then DMs `RIGBY CARD
  STILL MISSING … The card keeper gave up: <why>` (hardware: the riser). It stamps a heartbeat (`checked`, `period_s`)
  on every run, and the DROP DM reports it: `The card keeper (checked N s ago) reboots rigby…`, or `The card keeper is
  SILENT … reboot it by hand` — a dead keeper is named, never assumed. ⚠ The automatic reboot costs the tablets' USB
  wire links until the USB keeper restores them (above). **Check:** `journalctl -u rigby-card-keeper` (one line per
  run: `card keeper: none|reboot|give_up - <why>`), `cat /opt/arrstack/tablet-foundry/state/card_keeper.json`;
  `python3 /usr/local/bin/rigby_card_keeper.py --dry` decides without writing or rebooting (its settings come from
  `/etc/default/tolkien-host`, which the unit loads: `set -a; . /etc/default/tolkien-host` first when running it by hand;
  unset, it prints the names and does nothing). **Install / update** (the
  code ships in the Tolkien image, gated there; `fleet_deploy.py rigby_card_keeper.py …` first):
  `Lab\bin\run_remote.py rigby tolkien-build\host\install_card_keeper.sh 300 --args /mnt/ssdshared/tolkien-build` (the
  folder names where rigby sees the mirror; piped, the script cannot find its own) — copies the image's bytes (refuses
  unless they equal the mirror's), backs up to `/root/rigby-card-keeper-bak-<stamp>/`, writes the units FROM the
  module (one cadence constant), enables the timer, prints a `--dry` verdict. **Stop it:** `systemctl disable --now
  rigby-card-keeper.timer`. Proven 2026-10-17 by a live decoy (`--dry`, private state dir, real `nvidia-smi` + boot
  id): missing card → reboot, present 1060 → none, missing card + busy lane → none. **Proven LIVE on a real drop the
  same day:** the 1060 fell off at 13:45 UTC; at 16:08 a probe order found `gtx1060 is not visible to the cuda build
  (… found: [])`, WAITED and wrote the marker; the keeper's 16:09:49 run logged `card keeper: reboot - order
  dumpprobe-… waits on ['gtx1060'], both lanes idle - rebooting rigby (once per drop)`; rigby was back at 16:10:24
  UTC with both cards listed and the waiting order ran on its own. ⚠ Between the drop and the first order that needed
  the card (2 h 25 min) the keeper correctly did nothing ("no order is waiting on a card") — it is an on-demand
  reboot, not a card watchdog. ⚠ `/proc/driver/nvidia/gpus` still lists a fallen card — presence is the build's own `--list-devices`. Check with
  `dmesg | grep -i xid` via `Lab\bin\run_remote.py`; the boot-time `2.5 GT/s x1` line is the card's idle link state,
  not proof of a bad link. Before submitting 1060 orders after any quiet spell, check the card is there (the by-name
  pin refuses cleanly, so a dropped card costs a VOID order, not a wrong-card result).

## rigby's MODEL WARMER — the 35B kept in RAM (page cache), so an order never pays a 60 s cold load (since 2026-10-30)

the owner 2026-10-25: models on each box's fastest drive, *"and Rigby if we have enough space in RAM to hold it then yeah
shoot why not"*; 2026-10-30 he chose a **warmer over a lock**. rigby has no NVMe: its models sit on a SATA Kingston A400,
so the 17.2 GB `Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf` loads in **~60 s cold** vs **~12 s from the page cache** (2026-10-26).
On 2026-10-30 a claim-review order paid the 60 s for 18 s of work, because rigby had restarted at ~02:08 (not via the
card keeper — the journal ends abruptly at 02:07:59, cause not found) and RAM was empty.
- **What it does:** a root timer on rigby's host (`rigby-model-warmer.timer`, every 120 s = the card keeper's beat;
  `/usr/local/bin/rigby_model_warmer.py`) measures how much of each file in `WARM` sits in the page cache (`mincore`
  over a private map — reads nothing) and reads back ONLY the uncached 64 MiB chunks, ONLY while both of Tolkien's
  lanes are idle (no `CURRENT_ORDER*.json` — an order reads its own model) and the missing part fits `MemAvailable`, at
  `Nice=19` / `IOSchedulingClass=idle`. It never locks pages: the cache stays evictable, so another model is never
  starved. Each run stamps `model_warmer.json` in Tolkien's state dir (`checked`, `period_s`, per file `cached_pct`,
  `action`, `read_gb`, `read_s`) — a dead warmer is visible at 3× its period.
- **Proven 2026-10-30 (live decoy):** 100 % cached → dropped to 0 % (`posix_fadvise DONTNEED`) → the installed warmer,
  through its own service, read 17.23 GB in 43 s → 100 % cached.
- **Check:** `journalctl -u rigby-model-warmer -n 5`, `cat /opt/arrstack/tablet-foundry/state/model_warmer.json`;
  `python3 /usr/local/bin/rigby_model_warmer.py --dry` decides without reading or writing.
- **Settings (2026-11-18):** `/etc/default/tolkien-host` on rigby's host carries `MODEL_WARMER_STATE`, `MODEL_WARMER_MODELS`,
  `MODEL_WARMER_FILES` (comma-separated), `CARD_KEEPER_STATE`, `CARD_KEEPER_CARDS` (comma-separated: a `NAMES` key such as
  `gtx1060`, or the driver's own name); both units load it (`EnvironmentFile=-`), and neither script carries a lab default.
  A `--dry` by hand needs `set -a; . /etc/default/tolkien-host` first.
- **Install / update** (the card keeper's door): ship `rigby_model_warmer.py` through `fleet_deploy.py` first (Tolkien
  gate: `tests/test_model_warmer.py`), then `Lab\bin\run_remote.py rigby tolkien-build\host\install_model_warmer.sh 300
  --args /mnt/ssdshared/tolkien-build`
  — copies the image's bytes, refuses unless they equal the mirror's, backs up to `/root/rigby-model-warmer-bak-<stamp>/`,
  writes its units, enables the timer, runs `--dry`.
- **To keep another model hot:** add its file name to `WARM` — check it fits beside the 35B in rigby's 23 GB first.

## CUDA on rigby's GTX 1060 under driver 470 — the CUDA 11.4 build (2026-10-14; faster than Vulkan)

**Why CUDA, and why 11.4:** on the 1060, CUDA writes the 35B-A3B **14–18 % faster** and reads long prompts **47 %
faster** than Vulkan (#36 r51: 14.2 / 13.8 tok/s at 2K / 12K, read 109 at 12K; with the graphs patch + MTP the
standard order now writes ~16.9 — [[36-Rigby-Whole-Box-Synthesis-GTX1060-2026-10-13]] § 7, order fields in
[[Fellowship-Order-Taker-Design]]). Driver **470 stays** (the GT 730 is
Kepler and needs it), and 470 caps the CUDA runtime at **11.4**; CUDA 11.4 wants gcc ≤ 10, Ubuntu 24.04 ships 13 — so
the build runs in NVIDIA's image `nvidia/cuda:11.4.3-devel-ubuntu20.04` (still published 2026-10-14; ~4 GB pull).
No NVIDIA container runtime is needed to BUILD (the "NVIDIA Driver was not detected" banner is expected).
- **Which tree is LIVE — read the mount, not this line:** `docker inspect tablet-foundry --format '{{range
  .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'`. Since the 10-15 "Update llama please" swap
  `/llama/cuda` = **`/home/owner/llama.cpp-latest/build-cuda-114/bin`**. Its CONTENTS since 2026-10-22 (engine parity,
  #34 B11b stage 2) are the build of **`/home/owner/lab-engine/llama.cpp-620d222`** — upstream `620d2225c`, the same
  llama.cpp ollama 0.40.5 runs on the three GPU foundries, + the graphs patch + lab patches #0/#3/#2 — copied INTO the
  mounted dir by `fleet_deploy.py --engine <labengine order> --only rigby` (the dir's sources are still e467e5b; read
  `fleet_deploy.py --check`'s `tag=` for which build is live, never this tree's git). The e467e5b bin is archived at
  `/home/owner/llama-cuda114-bin-archive-engine-20261022-132334`; `fleet_deploy.py --engine-rollback --only rigby`
  restores the newest `…archive-engine-*`. `/home/owner/llama.cpp-rpcasync` (23b0202 + the RPC lab patches) still serves `/llama/next/*` (x86,
  Vulkan, arm64) — its `build-cuda-114` is mounted nowhere. A patch for rigby's CUDA engine goes into the tree the
  mount names (10-17: patch #0 went into rpcasync off an older version of this line, and the probe ran unpatched).
- **Build** (rigby, `llama-build-rpcasync/build_cuda114_latest.sh` for the live tree; `build_cuda114.sh` is the
  rpcasync recipe): `pip install cmake` inside the image (20.04's 3.16 is too old), `-DGGML_CUDA=ON
  -DCMAKE_CUDA_ARCHITECTURES=61` (Pascal only — **the 730 gets no CUDA kernels**), same options as the Vulkan build
  otherwise, output `build-cuda-114/bin`. Then copy **libcudart.so.11.0, libcublas.so.11, libcublasLt.so.11 AND
  libnccl.so.2** from the image beside the binaries — the first smoke test failed on `libnccl.so.2: cannot open
  shared object file` (cmake found NCCL in the image and linked it). A new llama.cpp VERSION is a separate tree +
  build (`lab-engine\build_cuda114_620d222.sh` is the worked example) swapped in by `fleet_deploy --engine`, which
  refuses while a CUDA llama process runs (WAIT, nothing touched) and byte-compares after the copy. A lab patch on the
  SAME version is an INCREMENTAL rebuild in place
  (`build_cuda_graphs.sh`, `build_slot_pending.sh [tree]`): refuses while a CUDA llama process runs, archives the
  mounted bin first, counts its marker in the built library.
- **Run** under the host loader like the Vulkan build (`/hostlib/ld-linux-x86-64.so.2 --library-path
  /llama/cuda:/hostlib`): the host's `libcuda.so.1` (470) comes from `/hostlib`; `/dev/nvidia-uvm` is already in
  tablet-foundry's device list. Mount as above (compose backup `.bak-20261014-140242-before-cuda-mount` is the
  first, rpcasync-era mount).
- **Keep CUDA off the 730:** `CUDA_VISIBLE_DEVICES=<the 1060's UUID>`, looked up BY NAME from the driver's own files
  `/proc/driver/nvidia/gpus/*/information` (`Model:` / `GPU UUID:`) — tablet-foundry has no `nvidia-smi`. Device
  then lists as `CUDA0: NVIDIA GeForce GTX 1060 6GB`. Tolkien does all of this for `build: "cuda"`
  (`tolkien_llm.card_dir` / `cuda_visible`).
- **CUDA graphs are OFF on Pascal by design:** llama.cpp disables them below Volta (`ggml_cuda_graph_set_enabled`,
  `disable_due_to_gpu_arch`); forcing them off changed nothing (r53). The lab patch
  `lab-engine\patches\cuda_graphs_pre_volta_patch.py` (env `LAB_CUDA_GRAPHS_PRE_VOLTA=1`, OFF unless set)
  turns them on: issue cost 8.0 → 1.1 ms per token, but the time moves into waiting for the card — **+2 %** (r54).
- **Speculation loses here** (n-gram, MTP, draft models): with most experts on the CPU, a k-token check costs ~k× the
  CPU experts (r52a −34 % at 61 % acceptance) — not a CUDA problem, a placement one.
- **Lab patch #0 — the slot save reports its PENDING token (2026-10-17, #34 B11 quick resume):**
  `lab-engine\patches\slot_pending_token_patch.py` + `build_slot_pending.sh [tree]` (default the live tree,
  `llama.cpp-latest`; same shape as the graphs build: refuses while a CUDA llama process runs, archives
  `build-cuda-114/bin` first — `/home/owner/llama-cuda114-bin-archive-llama.cpp-latest-20261017-185243` is the live
  pre-patch copy — incremental rebuild; LIVE since 2026-10-17 18:53Z, marker verified inside the container). `POST /slots/0?action=save` now answers
  `pending_token`: the last token the server sampled, which no cache holds yet. A continue that adds it extends the
  saved state by one token and is lossless (dump probe r4/r5); without it the hybrid rolls back to its prompt-end
  checkpoint. Response field only — no behaviour change, no env gate. ⚠ The server code is in
  `libllama-server-impl.so` (`llama-server` is an 18 KB stub at 23b0202) — check markers THERE; `libllama.so` already
  holds an unrelated `pending_token_idx`. ⚠ The server's slot file stores `server_tokens::serialize()` — a PACKED list
  `[-1][1][count][ids][media]`, not raw ids (`tolkien_llm.slot_file_tokens`).

The 470 driver ships a working **Vulkan 1.2** ICD (`vulkaninfo` enumerates the card). Build llama.cpp
with `-DGGML_VULKAN=1` (deps: `libvulkan-dev vulkan-tools glslc`). **But the Vulkan backend HANGS on
Kepler** — the model loads to VRAM, then deadlocks mid-compute — because GK208 has **no fp16 and no
cooperative-matrix**, and llama.cpp's fp16 shader path locks up. **Disable them:**

```bash
export GGML_VK_DISABLE_F16=1 GGML_VK_DISABLE_COOPMAT=1 GGML_VK_DISABLE_COOPMAT2=1
./llama-bench -m model.gguf -ngl 99 ...
```

With those set it runs clean. The device line should read `... | fp16: 0 | ... | matrix cores: none`.
⚠ If a run hangs anyway, the process may sit in an uninterruptible driver call — the card memory
clears once killed (it is a software hang, not a wedged card); verify with
`nvidia-smi --query-gpu=memory.used --format=csv,noheader` returning `0 MiB`.

## Measured performance (GT 730 vs i5-3470, same llama.cpp binary, r=5, Q4_K_M)

| model | GPU tg64 | CPU tg64 | GPU pp128 | CPU pp128 |
|---|---|---|---|---|
| Qwen2.5-0.5B | 8.84 | **38.03** | 113.1 | 102.4 |
| Qwen2.5-1.5B | 6.65 | **16.98** | 35.3 | 31.9 |
| Qwen3.5-2B | 4.70 | **11.72** | 30.1 | 27.7 |

The CPU wins generation by 2.5–4.3×; the GPU wins prompt-processing by only ~10%. See
2026-08-28-claude-fellowship for the full analysis and the advisory-model serving plan
(CPU + llama-server prompt caching, which is the real performance lever on the no-AVX2 i5).
