# Fellowship node provisioning — durable adb, node build, offline packages, benchmark notes

How a tablet becomes (and stays) a Fellowship node, moved from [[RUNBOOK]] (Stage 5
of the library realignment). Flashing itself: [[HANDOFF-tablet-lineage-flash]] (A-only
families) / [[FLASH-NEW-TABLET-gandalf-class-X6C6F-from-rigby]] (A/B gandalf class).

**How to read any of this (no root needed, and do NOT use `adb root`):**
Termux's uid (10144) is denied `dumpsys`, `/proc/net/wireless` and even `/proc/uptime` on these PHH
GSIs — every probe returns "UNAVAILABLE", which is a permission wall, not missing data. The `shell`
uid (2000) via `adb` reads all of it. adbd listens on **:5555 on all seven nodes**
(`persist.adb.tcp.port`), verified across a reboot on each.

### ⭐ GIVING A NODE DURABLE adb (and why wireless debugging is the WRONG mechanism)
```bash
adb -s <transport> root                                  # -userdebug GSI (ro.debuggable=1)
adb -s <transport> shell 'setprop persist.adb.tcp.port 5555'
adb -s <transport> shell getprop persist.adb.tcp.port    # must read 5555
adb -s <transport> reboot                                # then :5555 answers with NO pairing
```
* ⚠ **`adb root` is REQUIRED for the setprop** — as plain uid 2000 it returns *"Failed to set
  property"*. `su` is NOT an option on the 18.1 nodes: absent from all six standard paths
  (`/system/xbin/su`, `/system/bin/su`, `/sbin/su`, `/debug_ramdisk/su`, `/su/bin/su`,
  `/system/sbin/su`); only Termux's stub exists, and it prints *"No su program found"* regardless.
* ✅ **IT SURVIVES A REBOOT** — `:5555` answers within a minute of boot with the prop intact and no
  pairing required.
* ⚠ **`persist.adb.tcp.port` PERSISTS on these GSIs; `persist.adb.tls_server.enable` (wireless
  debugging) does NOT.** Do not generalize either to the other.
* ⚠ **WIRELESS DEBUGGING IS THE FRAGILE MECHANISM — never build on it.** Random port per session,
  does not re-arm at boot, so a node on it needs a NEW pairing code from the physical screen after
  EVERY reboot. The PAIRING persists (`~/.android/adb_known_hosts.pb`) — but a stored pairing with
  no listener is useless; they are different things.
* ⭐ **A paired device that IS advertising needs no new code**: `adb mdns services` shows it, then
  `adb connect adb-<SERIAL>-<id>._adb-tls-connect._tcp`. Check `adb devices` and `adb mdns services`
  before asking a human for anything.

⚠ **ssh IS NOT A SUBSTITUTE FOR adb — they are different security principals.** Measured, not
assumed: ssh lands in a sandboxed app uid (`untrusted_app`); adb is `uid=2000 (shell)`.
From ssh: `setprop` is SELinux-denied even for a harmless `debug.*` property, `settings` throws
SecurityException (MANAGE_USERS) with and without `--user 0`, and `content`/`svc` die on
`app_process: inaccessible`. Reaching a box and having authority on it are separate things.
⚠ And `setprop: command not found` over ssh is a **PATH** error, not a permission wall — the binary
is at `/system/bin/setprop`. Use absolute paths before concluding anything is blocked.
Client at `Lab\fellowship\platform-tools\adb.exe`:
```bash
cd "E:/SSDShared/Lab/fellowship/platform-tools" && ./adb.exe connect <lan-address>:5555 && ./adb.exe -s <lan-address>:5555 shell "dumpsys wifi | grep NETWORK_DISCONNECTION_EVENT"
```
⚠ **`adb root` is NOT needed for diagnostics and carries a real cost** — it restarts Termux, which
can drop the node's wake-lock (slowing it 8-12x) and kill its sshd.
Scripts: `Lab\cameras\wholeframe-test\wifi_adb_audit.py`,
`wifi_disconnect_history.py`, `link_quality.py`, `link_soak.py` (on-box soak at `/tmp/link_soak.log`).

**Measured performance** (fp64 DGEMM, N=1024, best-of-3):
* **20.7–21.9 GFLOP/s per octa-core node**, near-linear to 8 processes.
* **42.79 GFLOP/s** aggregate across 2 nodes run concurrently, no degradation.
* Distributed (row-block matmul, verified against numpy): **80.8% of peak lost to data movement** at
  N=3072. Compute scales N³, transfer N² — bigger problems amortise it.

⚠ **Termux's OpenBLAS has threading COMPILED OUT.** `OPENBLAS_NUM_THREADS`/`OMP_NUM_THREADS` do
nothing — 1 thread and 8 threads measure identical. **A single-process numpy benchmark measures ONE
A53 core** and undersells the hardware ~7×. Run **one process per core** (which is how HPL runs
anyway: 1 rank/core, serial BLAS).

⚠ **Python 3.14 defaults to the `forkserver` start method**, which PICKLES `initargs` to every child
— shipping a full matrix to all 8 workers and dwarfing the math (measured 0.69 vs 20.9 GFLOP/s).
Use `mp.set_start_method("fork", force=True)` and set globals **before** forking. Also: `forkserver`
re-imports `__main__` from a path, so `multiprocessing` breaks when a script arrives via stdin —
`scp` the file and run it.

**Node build (no screen interaction needed):**
1. `adb install` the Termux **arm64 GitHub *debug* build** (verify against its published sha256sums).
   ⚠ Must be the **debug** build — it is debuggable, so **`run-as com.termux`** gives a shell inside
   the app context without root. That is the whole remote-control mechanism.
2. ⚠ **`am start` fails with `BackgroundServiceStartNotAllowedException` if the screen is asleep.**
   `input keyevent KEYCODE_WAKEUP` first, then launch to unpack the bootstrap (389 binaries).
3. `apt-get install openssh` → pubkey into `files/home/.ssh/authorized_keys` → `sshd`.

**Offline package install** (node with no WiFi): Termux's apt cache is at
**`/data/data/com.termux/cache/apt/archives/`** — NOT `$PREFIX/var/cache`. `--download-only` on a
networked node → tar → `adb push` → `dpkg -i`.

⚠ **The two X606Fs MUST stay on Android 11 (LOS 18.1).** Android 12+ adds
`config_wifiSaeUpgradeEnabled` (WPA2→WPA3 auto-upgrade); MossMatriarch runs WPA2/WPA3 transition
mode, and the X606F's Android-10-era MediaTek driver cannot complete MFP → `ASSOC-REJECT
status_code=31`, no WiFi. **Upgrading those two past Android 11 will break their networking.** The
X6C6F and both X505Fs are unaffected and run Android 12 fine.

⚠ **Windows/Git-Bash:** `export MSYS_NO_PATHCONV=1` for any `adb push`/`shell` with absolute device
paths, or `/sdcard/x` becomes `C:/Program Files/Git/sdcard/x`.

### 📼 Sméagol — armv7 32-bit intake (SM-T560NU / `gtelwifiue`)

Sméagol is the Fellowship's one **32-bit armv7l** node — the rest of the fleet is arm64. That fact
reaches into every layer (arch tag, Termux build, llama.cpp binary, model choice, capability
declaration) so it gets a dedicated section rather than a footnote.

**Device durable facts** (proven 2026-09-04, do not repeat for reassurance):
- **Model / firmware:** Samsung Galaxy Tab E, SM-T560NU, LineageOS 18.1 (`gtelwifiue`)
- **Wired-only network:** USB-RNDIS at **`10.55.0.18`** (`usb_octet 18`), SSH port **8022**
- **Wired stage server:** **`http://10.55.0.1:8078`** (served from rigby over the USB segment)
- **Arch / runtime:** `armv7l`, 4-core, 1.39 GB RAM
- **Roster capabilities (truthful):** `task:payload`, `task:ping`, `arch:armv7l`, `runtime:32bit`
- **Explicit unavailable capabilities:** `task:detect`, `task:burst`, `ncnn_detect`, `hwdec`, `arm64-native-payload`
- **Reboot survival:** proven — Termux:Boot restores sshd/agent; rigby reasserts RNDIS composition + address.
- **RS300 DHCP reservation:** WiFi MAC reserved 2026-09-04 for the eventual move off USB-only.

**ARMv7 llama-cli runtime** (built 2026-09-07 during Codex-led recovery):
- **Binary path (on-device):** `/home/owner/llama.cpp/build-android-armv7/bin/llama-cli`
- **Source commit:** `70adb1b`
- **Toolchain:** Android NDK **r27c**, `armeabi-v7a`, API level 24
- **Build flags:** `GGML_LLAMAFILE=OFF` (llamafile path assumes 64-bit atomics not available on armv7)
- **Model SHA-256:** `1adf0b11065d8ad2e8123ea110d1ec956dab4ab038eab665614adba04b6c3370`
- **State:** real model generation was proven end-to-end (Phase 5 heterogeneous
  `heterogeneous.fast_role_on_smeagol.chat.sisters` completed on attempt 25 after this recovery).
  Do not re-verify for reassurance.

**Recovery path** (if the ARMv7 build is lost again — the earlier worktree was pruned in Aug):
1. Restore RNDIS at `10.55.0.18:8022` per §4g of `Tablet-RNDIS-Setup-Configuration-Procedures.md`
   (Sméagol's own restore mechanism differs from A11/A12 — see that section's Sméagol exception).
2. Verify the wired stage server at `http://10.55.0.1:8078` responds from rigby.
3. Rebuild llama.cpp at commit `70adb1b` with the toolchain/flags above; land the binary at
   `/home/owner/llama.cpp/build-android-armv7/bin/llama-cli` on Sméagol.
4. Confirm model hash matches the recorded SHA-256 before running any evaluation cell — a mismatched
   model is fit-feasibility evidence only, not answer-quality evidence.

**Why Sméagol is opportunistic, not a specialist slot** (per 2026-09-04 Design walk): Sméagol was
added for the fun of a different SKU, not because a niche needed filling. It is an arch-neutral
text-task slot-in; do not build specialist assumptions around it. The seven `heterogeneous.heavy_role_on_class_b.vlm.*`
Phase 5 cells that were the owner-ruled hard-fit exclusions (Qwen2-VL-2B on class-B) reinforce that a
capability-shaped guard belongs upstream: fit is DISCOVERED at the pre-inference RAM check, not
inferred from the roster.

## provision_node.sh: the keepalive's gateway, the optional detector, and the roster lines (2026-11-19)

- The provisioner derives its folders from its own location and reads the lab's settings (Tolkien's agent from the
  `CODE_MIRRORS` `tolkien` entry; adb from `ADB`, else `platform-tools\adb.exe` beside it).
- When it installs `start-keepalive.sh` it also writes the tablet's gateway into `~/.keepalive.env`, resolved over adb,
  because Termux cannot read the routing table.
- The decoder and detector (`payload/bin/hwdec`, `ncnn_detect`, `payload/det/`) are optional: present, they are delivered
  and proven by the agent's live decoy; absent, the stages are skipped with a note.
- It ends with the two roster lines to add: the Fellowship roster file (`FELLOWSHIP_ROSTER`) and Tolkien's `TABLET_NET`.
