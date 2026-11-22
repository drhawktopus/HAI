# Flashing a NEW gandalf-class tablet (TB-X6C6F, A/B + dynamic `super`) to LineageOS 19.1 — driven from rigby (Ubuntu)

**Written 2026-08-18 for tablet #7, serial `HA1TXV62`, plugged into `rigby`.**
⚠ **`<lan-address>` appeared here and is DEAD as of 2026-08-21** — rigby's address has moved seven
times and that port now runs point-to-point to mordecai. **Use `ssh rigby`** (it resolves via
`Lab\bin\sshto.py`); reserved addresses are `.82` motherboard NIC / `.79` PCIe NIC.
Every device fact below was read off *this* tablet in fastboot, not carried forward from gandalf.

**Why this doc exists separately from [[HANDOFF-tablet-lineage-flash]]:** that handoff's
§2 procedure is the **X606F/X505F** one — A-only devices with a fixed physical `system` partition,
flashed from **Windows** where the driver bind and the platform-tools version split are the hard
parts. A gandalf-class unit is the inverse on both axes:

| | X606F / X505F (the five) | **X6C6F (gandalf class)** |
|---|---|---|
| partitions | A-only, physical `system` | **A/B (`slot-count: 2`), `system` is LOGICAL inside `super` (5.00 GiB)** |
| flash target | classic fastboot | **fastbootd** (`fastboot reboot fastboot`) |
| host here | Softserve (Windows) | **rigby (Ubuntu)** — no WinUSB bind, no `DeviceInterfaceGUIDs` registry fix, no 34-vs-37 crash split |
| LineageOS | **18.1** on X606F only (SAE/MFP WiFi bug) | **19.1** — X6C6F is unaffected; do not "standardise" onto 18.1 |

## Measured starting state (`fastboot getvar all`, 2026-08-18 on rigby)

```
serialno: HA1TXV62        product: P98928JA1S
unlocked: no              secure: yes            <- BOOTLOADER IS LOCKED
slot-count: 2             current-slot: b        is-userspace: no
partition-size:super: 140000000  (5.00 GiB)      max-download-size: 0x8000000 (128 MB)
has-slot:vbmeta / vbmeta_system / vbmeta_vendor: yes
partition-size:system: ABSENT                    <- confirms system is logical, not physical
```

`unlocked: no` is the whole shape of Phase 1: **the tablet has to go back into Android and onto WiFi
before it can be unlocked at all.** See §4a of the handoff — `sys.oem_unlock_allowed` is
bootloader-side, cannot be baked into an image, and its absence produces
`FAILED (remote: 'Unlock operation is not allowed')` **~291 seconds after the flow starts**, which
invites a wrong theory about a missed on-screen tap.

## Already staged for you (2026-08-18, by the main session)

* `~/fellowship/vbmeta_disabled.img` on rigby — sha256 `8f70c5b7…a6be33`, 4096 B, `AVB0`, `Flags: 3`.
* `~/fellowship/los19.img` on rigby — LineageOS 19.1 GSI, downloaded from
  `sourceforge.net/projects/andyyan-gsi/files/lineage-19.x/lineage-19.1-20250606-UNOFFICIAL-arm64_bvS.img.gz`
  (942,756,055 B compressed). ✅ **VERIFIED on rigby, not assumed**: `.gz` sha256
  `a70b6b05176ed72713d055222427054930b77cac51a89740c0ab623206cb6807` — a byte-for-byte match with the
  hash pinned in 2026-08-03-claude-tablets; decompressed to 2,121,457,664 B with ext4
  magic `53ef` at offset `0x438`, both exactly as recorded. **The lab's own copy was gone** — only the
  18.1 image survived in `E:\SSDShared\Lab\fellowship\flash\`, despite the handoff saying both were
  kept there. Re-stage it there when convenient so the next flash does not re-download.
* `fastboot` 34.0.4-debian and `adb` 1.0.41 are already on rigby, udev rules live, `owner` in
  `plugdev`, and Softserve's `~/.android/adbkey` was copied over on 2026-08-17 — so no
  "allow USB debugging" tap is needed for the *existing* six. **A brand-new device still prompts
  once** (the trust store is on the tablet, not the host). Tick *Always allow*.

---

# PHASE 1 — unlock the bootloader (needs Android + WiFi first)

```bash
ssh rigby
cd ~/fellowship
fastboot devices                       # expect: HA1TXV62  fastboot
fastboot reboot                        # boots into stock Android
```

**On the tablet, by hand:**
1. Run the setup wizard far enough to **connect WiFi**. *OEM unlocking stays greyed out until the
   device can reach Google* — this is not skippable.
2. Settings → About tablet → tap **Build number ×7**.
3. Developer options → **OEM unlocking ON** and **USB debugging ON**.

**Back on rigby — confirm the model and the flag BEFORE reaching for the unlock**, so a refusal is
never ambiguous:

```bash
adb devices                                          # tap "Always allow" on the tablet if prompted
adb -s HA1TXV62 shell getprop ro.product.model       # EXPECT: TB-X6C6F   <-- gate, see note below
adb -s HA1TXV62 shell getprop sys.oem_unlock_allowed # MUST print 1
adb -s HA1TXV62 reboot bootloader
# PHYSICAL GATE: be at the tablet and press Volume Up when its unlock confirmation appears.
fastboot flashing unlock                             # MediaTek: OKAY in 7-21 s, stays in fastboot
fastboot getvar unlocked                             # MUST print: unlocked: yes
```

⚠ **The model check is a real gate, not ceremony.** If it comes back `TB-X606F` this is a frodo/sam
class unit and it must get **18.1** (already staged at
`E:\SSDShared\Lab\fellowship\flash\lineage-18.1-…img`) plus the classic A-only procedure in the
handoff — **not** the fastbootd path below. If it comes back `TB-X505F` it is Qualcomm and unlocks
with `fastboot oem unlock-go` instead.

⚠ Unlock **wipes userdata**, so USB debugging goes off again and `adb reboot bootloader` stops
working. Back to fastboot afterwards is **Vol Down + Power**.

# PHASE 2 — flash

vbmeta is a **physical, slotted** partition and is flashed from the **bootloader** fastboot. Both
slots are done explicitly: the device is A/B and may switch slots at first boot, and an unpatched
vbmeta on the other slot is a bootloop waiting for its turn.

```bash
fastboot --disable-verity --disable-verification flash vbmeta_a vbmeta_disabled.img
fastboot --disable-verity --disable-verification flash vbmeta_b vbmeta_disabled.img
```

`system` is **logical inside `super`**, so it can only be written from **fastbootd**:

```bash
fastboot reboot fastboot
fastboot devices                       # EXPECT: HA1TXV62  fastbootd   <-- the word must change
fastboot flash system los19.img        # ~1.98 GiB into a 5.00 GiB super; sparse-split at 128 MB
```

⚠ **Do NOT `fastboot erase system` on this device.** That is the A-only step from the handoff;
here `system` is a logical partition and flashing resizes it. Erasing is at best a no-op and at
worst removes the partition the flash then expects.

⚠ **If `fastboot devices` is empty in fastbootd**, the device has re-enumerated under Google's USB
id `18d1:4ee0` (a different devnode from the MediaTek `0e8d:201c` it used in bootloader mode). On
Windows that needs its own driver bind; on rigby check `lsusb | grep 18d1` and, if the rule is
missing, `sudo udevadm control --reload && sudo udevadm trigger`.

⚠ **If the flash fails for space**, reclaim the stock `product` partition and retry — nothing in
the GSI uses it:
```bash
fastboot delete-logical-partition product_b     # slot b is current; use product_a if you switched
fastboot flash system los19.img
```

Wipe and boot:
```bash
fastboot -w                            # if this throws std::out_of_range, see fallback below
fastboot reboot
```
Fallback for `-w` (the version split in handoff §2 was measured on the Windows binaries; rigby's
34.0.4-debian is untested for it):
```bash
fastboot reboot bootloader
fastboot erase userdata
fastboot erase metadata
fastboot reboot
```

## ✅ EXECUTED 2026-08-18 on `HA1TXV62` — three corrections from the live run

The procedure above works and booted LineageOS 19.1 first try. Three things it got wrong:

1. **`fastboot flash system` in fastbootd writes `system_a` and MAKES SLOT A ACTIVE — even though
   the bootloader reported `current-slot: b`.** Measured: after `Writing 'system_a' OKAY`, the
   device booted with `ro.boot.slot_suffix = _a` and `ro.lineage.version =
   19.1-20250606-UNOFFICIAL-arm64_bvS`. **Do NOT reach for `--set-active`** — I flagged the
   slot mismatch mid-run as a defect and it was not one; fastbootd handles the switch. Flashing
   vbmeta to **both** slots is what makes this safe either way, so keep doing that.
2. **`fastboot -w` IS A NO-OP ON THIS DEVICE** — returns in **0.004 s** with `wipe task partition
   not found: cache` / `metadata` and **never touches userdata**. It does not error, so it reads
   as a successful wipe. userdata was already cleared by `flashing unlock`, so nothing was harmed,
   but do not count `-w` as the wipe: use `fastboot erase userdata` from the bootloader if you
   actually need one. (Handoff §2 says the same thing about `-w` for the other families; it is
   worse here — there it at least formatted `cache`.)
3. ⭐ **`adb root` is REFUSED on a fresh LOS 19.1 install** — *"ADB Root access is disabled by
   system setting - enable in Settings → System → Developer options"* — and **`settings put global
   adb_root 1` does not unblock it** (the key reads back `1` and adbd still refuses). This is a
   LineageOS **"Rooted debugging"** toggle that needs a physical tap, and it **gates
   `persist.adb.tcp.port=5555`**, which needs root to set. So the Phase 3 order is:
   **Developer options → Rooted debugging ON, THEN `adb root`, THEN the setprop.** The five
   originals predate this and nobody hit it.

⚠ **FIRST BOOT TAKES 5–10 MINUTES.** Measured boots on the five were 35 s / 35 s / 110 s, but the
guide's window is real and calling failure at 4 minutes cost a wasted reflash once already.

# PHASE 3 — ⭐ the FIRST thing after it boots, while it is still on USB

The flash wiped USB debugging again. Run the wizard, **connect WiFi**, re-enable **USB debugging**,
then — before anything else at all:

```bash
adb devices                                            # authorize on-screen if prompted
adb -s HA1TXV62 root                                   # works: -userdebug GSI, ro.debuggable=1
adb -s HA1TXV62 shell 'setprop persist.adb.tcp.port 5555'
adb -s HA1TXV62 shell getprop persist.adb.tcp.port     # MUST read back 5555
```

**This one property is the difference between a node that never bothers you again and a node that
needs a pairing code off its own screen after every reboot.** It was skipped on legolas and cost
~20 manual ADB enables in a day. `setprop` on a `persist.*` property needs root; `adb root` restarts
adbd, which is fine — it comes back listening on 5555, and the property survives reboots.

⚠ **Wireless debugging in Developer options is NOT this mechanism and is a trap** — its port is
random per session and `adb_wifi_enabled=1` does not re-arm TLS at boot on these GSIs. The working
six all have `adb_wifi_enabled = 0`.

# PHASE 4 — addressing (needs you at the router)

Pick the node name first — it is baked into DNS, `.fellow_name`, and every roster below. Suggested
**`aragorn`** at **`<lan-address>`** (reservation ADDED and verified live 2026-08-19, entry 17).

> ⚠ **`.76` IS NOT FREE — it is MOTHERSHIP.** This line recommended it until 2026-08-19.
> And `.77`, also recommended here until that date, is **an Apple Watch** on a plain DHCP lease.
>
> **Never call a LAN address free from ping + ARP + DNS + the reservation table.** A sleeping
> wireless device on a randomized MAC is invisible to all four *at once*: it holds no
> reservation, drops ICMP, ages out of ARP, and has no DNS record. Four checks agreeing was one
> blind spot counted four times.
>
> **The only near-complete census is the 48-row device list on `reservation_add.htm`** (click
> **Add** under Address Reservation). It lists powered-off devices that Attached Devices omits
> — the Vizio at `.70`, an iPhone at `.206`, Amazon at `.202`/`.33`. Check the candidate
> against THAT list first, then confirm with the weak signals. Even it is not proof: read a
> clean result as *no known claimant*, never as *guaranteed free*.

1. **Router (MossMatriarch, <router-address>)** — add a DHCP reservation for the tablet's wlan0 MAC at
   `<lan-address>`. Read the MAC with `adb -s HA1TXV62 shell ip link show wlan0`.
   ⚠ Android uses a **per-SSID randomized MAC** by default — gandalf's reservation is against a
   randomized `82:5E:…` address. Reserve whatever the device actually reports on your SSID, and do
   not change the randomization setting afterwards or the reservation stops matching.
2. **Pi-hole DNS** — `scheduler.tablet_alive()` connects to `<name>.lan`, **not** to the IP in the
   roster, so a node with no DNS record is fail-closed out of the Fellowship with everything else
   correct:
   ```bash
   ssh sundae
   echo "<lan-address> aragorn.lan" >> /opt/arrstack/pihole/etc-pihole/hosts/lan.list
   docker exec pihole pihole reloaddns
   getent hosts aragorn.lan          # verify
   ```
   ⚠ **Never leave a `.bak` in that directory** — Pi-hole loads every file in it. Backups go to
   `../hosts-archive/`.

# PHASE 5 — provision to parity (one command, from Softserve)

`provision_node.sh` asks the *device* for its own IP, so a network serial works fine:

```bash
# in Git Bash on Softserve
adb connect <lan-address>:5555
bash "E:/SSDShared/Lab/fellowship/flash/provision_node.sh" <lan-address>:5555 aragorn
```

It installs Termux + Termux:Boot **from APKs pulled off a live node** (signature must match or boot
scripts silently never run), takes and **verifies** `termux-wake-lock` before apt, installs
openssh/python/numpy/pillow/**ffmpeg**, plants the shared ssh key, sets the doze whitelist, pushes
the agent + `hwdec` + `ncnn_detect` + boot scripts + `wifi_watchdog.sh`, writes `.fellow_name` and
the **shared** `.fellow_token`, and finishes on a positive control.

⚠ **Fixed 2026-08-18, same pass as this doc:** the script's `AGENT=` pointed at
`E:/SSDShared/orchestrator-build/tablet_foundry.py`, **which does not exist** — the agent moved to
`tolkien-build` when Tolkien got its own container mirror, and the preflight loop checks `$AGENT`,
so the script aborted on its first stage for anyone who ran it. Repointed to
`E:/SSDShared/tolkien-build/tablet_foundry.py`. Backup: `provision_node.sh.bak-agentpath-20260818`.

⚠ **The Fellowship is split across two agent generations, found while checking this** — record it,
do not "fix" it blind:

| generation | sha256 (first 16) | nodes |
|---|---|---|
| newer (= `tolkien-build`, Aug 14 16:49) | `38d2d9d7118a15a9` | **gandalf, merry** |
| older | `2ac10eaf7ca80082` | frodo, sam, pippin, legolas |

The new node gets the **newer** one, which is what gandalf runs — correct for a gandalf-class unit
and correct as "the current build". Whether the other four should be brought forward is a separate
question for whoever owns the agent; it is filed in PROJECT-BACKLOG.

## §8a — the `libc++` trap, if ffmpeg is present but will not run

```
CANNOT LINK EXECUTABLE ".../bin/ffmpeg": cannot locate symbol "_ZNSt6__ndk127__from_chars…"
```
The bootstrap ships `libc++ 27c`; `libplacebo` needs 29 and nothing forces the upgrade, leaving
dpkg state `iF`. Fix over ssh on the node:
```bash
apt-get install -y libc++ && dpkg --configure -a
dpkg -l ffmpeg      # must be ii
ffmpeg -version     # EXISTENCE IS NOT FUNCTION -- run it, do not test -x it
```

# PHASE 6 — verify, then wire the roster

**Verify before wiring** — a node that is in the roster and not actually working takes real jobs.

```bash
K=/e/SSDShared/Lab/fellowship/cluster/tabcluster_ed25519
ssh -i $K -p 8022 <lan-address> 'python ~/tablet_foundry.py --detect-selftest'
#   expect "ok": true, label dog @ 0.992 -- the AGENT'S OWN positive control.
#   ⚠ Do NOT call `ncnn_detect <image>`: its interface is <param> <bin> <w> <h> [threads] reading
#     raw frames on stdin. A hand-rolled check invented that call and condemned a good node.
#   ⚠ If it fails, run the SAME check against sam (.73) before believing it -- a control that fails
#     on a known-good node is condemning the instrument, not the subject.

ssh -i $K -p 8022 <lan-address> 'dumpsys deviceidle whitelist' ; # EMPTY from ssh even when correct
adb -s <lan-address>:5555 shell 'dumpsys deviceidle whitelist' | grep termux   # ask over ADB instead

# reboot decoy -- the only proof Termux:Boot actually fires
adb -s <lan-address>:5555 reboot
#   ... wait ~90 s ...
ssh -i $K -p 8022 <lan-address> 'echo SSH_BACK; pgrep -f tablet_foundry.py'
adb connect <lan-address>:5555   # must reconnect with NO pairing code => persist prop survived
```

Then diff against the parity spec — 12 items, derived by measurement from sam:
`E:\SSDShared\Lab\fellowship\cluster\node-parity-spec-sam.txt`. **Regenerate it against a healthy
node first and diff against that**, rather than trusting the file's own age.

**Roster consumers — grep for `legolas` and add the new node everywhere it appears.** That is the
enumeration that catches them; legolas is the most recent addition, so its footprint is the
template:

- [ ] **The device roster** `Z:\Documentation\State\device-roster.json`, its `tablets` list (since
      2026-11-16 `scheduler.TABLETS` is read from this file, not written in `scheduler.py`):
      `{"name": "aragorn", "label": "aragorn", "cpu": 8, "ram_gb": …, "gflops": …}` — no address or
      serial (those live in rigby's `TABLET_NET`). **Fill gflops from a measured `burn.py 1024 20`,
      not from gandalf's 19.25.** The file is read at import, so it takes effect when Kirk
      (`orch_recreate.sh`) and rigby's `tablet-foundry` container next start; no code changes. How the file works:
      [[RUNBOOK-Orchestrator-Deploy-Procedure]] § "The device roster file".
- [ ] Pi-hole `lan.list` (done in Phase 4) and the router reservation
- [ ] Softserve `~/.ssh/config` — an `aragorn` alias next to the other six
- [ ] `E:\SSDShared\Lab\fellowship\cluster\burn_all.sh`, `burn_sum.py`, `fleet_duty.sh`,
      `fleet_duty2.sh` — all carry the node list inline
- [ ] [[Tablet-Cluster-INDEX]] (node table, worker counts), [[Fellowship-Crunch-Integration]]
      §2a capability table (⚠ **still lists only five — legolas is missing from it**),
      [[Network-Infrastructure]], [[RUNBOOK]] tablet table
- [ ] Confirm it appears in `GET http://<sundae-address>:8099/p20/factory/fellowship` — the factory
      name is **`fellowship`**, never `tolkien`

## Decode capability, once it is up

X6C6F is **mt6765**, same family as gandalf/frodo/sam/legolas ⇒ it takes the **main 2K30 stream on
hardware**, not the 704x480 sub stream that merry/pippin are limited to. Confirm rather than assume:
`Lab\cameras\rtsp_describe.py` answers stream/codec/decoder in one command, and a **burst cannot
show you this** — bursts pull recorded playback, which is main-stream only by construction.

---
**See also:** [[HANDOFF-tablet-lineage-flash]] (the flashing half for the other two device
families, the WinUSB bind, the four dead theories) · [[Tablet-Cluster-INDEX]] (front door
for the cluster as a whole) · 2026-08-03-claude-tablets (the original build).
