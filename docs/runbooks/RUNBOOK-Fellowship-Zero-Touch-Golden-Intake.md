# Fellowship zero-touch tablet intake — golden image, baked first-boot provisioning, WiFi push (X606F)

**What this solves:** flashing a Fellowship tablet into a state where **nobody has to touch the
tablet after the flash** — no setup wizard, no WiFi typing, no USB-debugging re-auth, no on-screen
"Rooted debugging" toggle. the owner's standing demand since tablet #5. Proven end-to-end on **gimli**
(Lenovo TB-X606F, serial MJ0ETXPE) on 2026-09-02.

This is the companion to `RUNBOOK-Fellowship-Tablet-Intake.md` (the intake runner). Read this one
when the question is *"how do we flash a tablet and not have to set anything up on it"* or *"golden
image / zero touch / baked provisioning / bake WiFi into the flash."*

> ⚠ **X606F ONLY.** The golden image is a modified LineageOS 18.1 GSI system image for the
> **TB-X606F** family. Do NOT flash it to a different SoC (e.g. Sméagol, the Samsung SM-T560NU) —
> that needs its own golden image built the same way from its own stock image. The intake runner's
> `profiles.json` fails closed on non-X606F for exactly this reason.

---

## The human-vs-scripted map (the honest answer to "what still needs a person")

**Irreducibly human — cannot be scripted:**
1. **Plug the tablet into Rigby over USB.**
2. **One physical press of Volume-Up** to confirm the bootloader unlock. This is a hardware
   confirmation the device demands; there is no software path around it. It happens ONCE per tablet,
   at unlock, and the unlock wipes userdata (expected on a fresh tablet).
3. **The router DHCP reservation** (MAC → fixed IP on the RS300). This is the owner's call by policy
   (router changes need his approval) and needs the router password. Everything up to this point
   produces the tablet's MAC + observed IP for him to reserve.

**Scripted / autonomous — no human watching, no taps on the tablet:**
Golden flash → first-boot self-provision (wizard skip + durable adb + rooted debug) → WiFi join →
Termux/agent/detector/watchdog provisioning → VERIFY. All ADB-driven; reboots are issued over ADB,
never by hand.

---

## Part A — the golden image (build ONCE per SoC, reuse for every tablet)

**Artifact:** `/mnt/fellowship/tablet-intake/images/lineage-18.1-golden-raw.img` on Rigby
(raw ext4, ~2.8 GB, sha256 `031f0817b1c7cf6077550f3a4037f9409bcd1e6a9b747013ca85e7d2da742622` as of
2026-09-02). Build script: `/opt/arrstack/tablet-intake/build_golden.sh` (run as root).

`build_golden.sh` takes the pinned stock GSI (`lineage-18.1-20240121-UNOFFICIAL-arm64_bvS.img`) and:
1. `simg2img` — convert Android sparse → raw ext4.
2. **grow + `e2fsck -E unshare_blocks`** — Android system images ship the ext4 `shared_blocks`
   (dedup) feature, which the kernel refuses to mount read-write. Un-sharing is what makes the image
   editable. (This is the step that failed the first build attempt: `mount: wrong fs type`.)
3. mount rw, **append the `FELLOWSHIP_ZEROTOUCH` block to `/system/bin/phh-on-boot.sh`**
   (append-only preserves the file's inode, mode, and SELinux context), unmount.

Run it as root (the owner, or `run_remote.py rigby build_golden.sh`):
```bash
sudo bash /opt/arrstack/tablet-intake/build_golden.sh   # prints [golden] ... and DONE + sha256
```

### What the baked block does (runs as root at first boot)
`phh-on-boot.sh` is the phh root hook, started by init at `sys.boot_completed=1` (service in
`/system/etc/init/vndk.rc`). The appended block, backgrounded at the end of the hook:
```sh
settings put global device_provisioned 1     # skip the setup wizard
settings put secure user_setup_complete 1
settings put global adb_enabled 1
printf 1 > /data/adbroot/enabled ; chmod 600  # the phh "Rooted debugging" gate file
setprop persist.adb.tcp.port 5555             # durable adb after every reboot, no pairing
setprop ctl.restart adbd
touch /data/adbroot/.fellow_baked             # idempotency marker
```

⚠ **Rooted debugging needs ONE more reboot to take effect on a fresh flash.** The `adb_root` daemon
(`/system/bin/adb_root`, from `adb_root.rc`) reads `/data/adbroot/enabled` **once, early at boot**,
and sets `service.adb.root`. The baked hook creates that file ~48 s into boot (after the hook's own
`sleep 10` + `sleep 30`), which is *after* the daemon already checked. So `adb root` works from the
**second** boot onward (the file persists in /data). The intake drives that reboot over ADB — it is
**not** a tap on the tablet. **Refinement TODO:** add `setprop ctl.restart adb_root` to the baked
block so root comes up on the *first* boot; harmless either way since reboots are ADB-driven.

---

## Part B — flashing a tablet with the golden image

Use fastboot **34.0.4** (Google build in `/opt/arrstack/tablet-intake/bin/` — flashes large images;
the Debian 37.x crashes on large flashes). After the one Volume-Up unlock:

- **Fresh tablet:** full flash (erase system/userdata/cache, `--disable-verity --disable-verification
  flash vbmeta`, `flash system <golden>`, `format:ext4 userdata`) — this is what the runner's
  `flash_x606f` already does; point it at the golden image instead of stock.
- **Re-baking a node that already booted** (as done on gimli): `flash system <golden>` only, `/data`
  preserved. vbmeta stays verity-disabled from the original unlock, so the modified system boots.

Boot, wait for the hook (~48 s), then **one ADB-driven reboot** to activate rooted debugging.

---

## Part C — WiFi without typing the PSK (the sanctioned method)

`Reference\Fellowship-WiFi-Watchdog.md`: **connecting with the passphrase on the command line is
prohibited** (`cmd wifi connect-network <ssid> wpa2 <passphrase>` leaks the house PSK into `ps`/logs).
The saved network is pushed as a **config file** instead.

**Template:** `/opt/arrstack/tablet-intake/secrets/fellowship_wifi_template.xml` (600). It is a live
node's `WifiConfigStore.xml` with the `<RandomizedMacAddress>` line **removed** so each tablet mints
its own unique stable per-SSID MAC (never inherits another node's — that would collide). The PSK is a
plaintext `<PreSharedKey>` (no keystore binding on this GSI), so the file is portable across tablets.

Push it (needs root/adb-root on the target):
```
# stage via cat (adb push does remote fchown fail on this GSI), install with correct owner+context
cat fellowship_wifi_template.xml | adb -s <serial> shell "cat > /data/local/tmp/wcs.xml"
adb -s <serial> shell "cp /data/local/tmp/wcs.xml /data/misc/apexdata/com.android.wifi/WifiConfigStore.xml
  && chown system:system /data/misc/apexdata/com.android.wifi/WifiConfigStore.xml
  && chmod 600 /data/misc/apexdata/com.android.wifi/WifiConfigStore.xml
  && restorecon /data/misc/apexdata/com.android.wifi/WifiConfigStore.xml && rm /data/local/tmp/wcs.xml"
adb -s <serial> reboot     # framework loads the saved network and associates on boot
```
On gimli this associated to MossMatrix on first boot and pulled a DHCP address in ~6 s.

---

## Part D — provisioning MUST run as ROOT

`provision_node_rigby.sh` ssh's to the node using `/opt/arrstack/secrets/tabcluster_key`, which is
`root:root 600`. **Run the intake runner as root** or every ssh-delivered step fails with "Load key
… Permission denied" (which surfaces misleadingly as "ssh not reachable"). The runbook's plain
`python3 tablet_intake.py …` example assumes a root shell. Use:
```
python E:\SSDShared\Lab\bin\run_remote.py rigby <script-that-runs-the-runner> 900
```
(adb is unaffected — root's adb client uses the existing adb-server on 5037.)

### Two provisioner bugs fixed 2026-09-02 (folded into `provision_node_rigby.sh`)
1. **ffmpeg / libc++ / libplacebo skew.** A fresh Termux bootstrap trails the live repo, so
   `apt install ffmpeg` pulls a libplacebo needing a newer libc++ than the bootstrap shipped — ffmpeg
   is present but **cannot link** (`cannot locate symbol …__from_chars_floating_point… libplacebo.so`),
   which VERIFY reports as "no hevc decoder". Fix: `apt-get upgrade` (force-confold) **before** the
   install brings libc++ current.
2. **sshd dies at the watchdog step.** The `wifi_watchdog` stage runs `adb root`/`adb unroot`; each
   restarts adbd, which kills processes launched from an adb shell — including the transient sshd that
   delivered the payload. `.fellow_*` and VERIFY (both over ssh, both *after* the watchdog) then got
   "Connection refused". Fix: re-establish sshd after the watchdog, before those steps (idempotent —
   no-op if a durable Termux:Boot sshd is already up).

---

## Part E — what's still MANUAL in the runner (the one-command TODO)

As of 2026-09-02 the intake **runner** (`tablet_intake.py`) still flashes the **stock** image and its
`wait_postflash()` prompts a human to complete the wizard / join WiFi / enable debugging. To make
intake genuinely one command (`run --name X --serial Y`, as root, + the one Volume-Up press), wire:
1. `flash_x606f` → flash the **golden** image.
2. `wait_postflash()` → drop the manual-setup prompt; just wait for adb `device` (the hook enables
   adb; `ro.adb.secure=0` means no key approval), then issue the ADB reboot for first-boot root.
3. a **wifi-push stage** (Part C) between postflash and durable_adb.
4. run the whole thing as root (Part D).

Until that wiring lands, follow Parts B–D by hand. Validate the wiring on the **next real X606F
tablet** — do not reprovision a healthy node to test it.

---

## Registration (unchanged, still deliberately manual — changes shared state)
After VERIFY prints `PROVISIONED: <name> at <ip>`: reserve the MAC→IP at the router (the owner), then add
the node to `TABLET_NET` (orchestrator `tablet_foundry.py`), `roster.json`, and the other roster
consumers (scheduler, tablet_fulfil, `rearm_wifi_watchdogs.ps1`, `Lab\bin\probe.py`,
`Lab\bin\capability.py`), plus `gimli.lan` DNS, then deploy and confirm the container that restarted.
