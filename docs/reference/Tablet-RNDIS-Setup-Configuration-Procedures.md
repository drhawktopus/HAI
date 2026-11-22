# Tablet USB Networking - RNDIS Setup & Configuration Procedures



## 3. RNDIS bring-up traps

### RNDIS configuration: use `sys.usb.config`, not `svc usb setFunctions`

The command `svc usb setFunctions rndis` returns success but creates no interface on GSI hardware. Configure the USB function directly via property:

```bash
setprop sys.usb.config rndis,adb
```

Retain the `,adb` suffix to preserve the control channel.

### Trap 2: Init links adb twice instead of rndis
On some GSI builds, setting `sys.usb.config=rndis,adb` is accepted but results in no `rndis0` interface. The gadget config shows both function slots linked to `ffs.adb`, even though `functions/rndis.gs4` exists and `usb_f_rndis` + `u_ether` are loaded.

```
/config/usb_gadget/g1/configs/b.1/f1 -> functions/ffs.adb
/config/usb_gadget/g1/configs/b.1/f2 -> functions/ffs.adb     <-- should be functions/rndis.gs4
```

Do not adjust the property. Repair the link directly (see §4).

#### Diagnostic logic
Do not gate repairs on Android version; version correlation is unreliable. Gate on the observed condition:

1. `rndis0` interface is missing.
2. `functions/rndis.gs4` exists.
3. `sys.usb.config` includes `rndis`.

If these conditions hold, relink the function slot to `rndis.gs4`. This logic applies regardless of OS version or device model. Implementations: `tablet_control._build_gadget` (Tolkien's restore and USB keeper) and `usbnet_up.py::gadget_needs_relink()` (Softserve).

### Class A (gandalf, aragorn) `USB keeper: restore-failed` every cycle — `rndis0` missing while `sys.usb.config=rndis,adb` (both gadget slots hold `ffs.adb`)

**Symptom (2026-09-29 → 10-02):** Tolkien's USB keeper DMs `USB keeper: gandalf restore-failed` / `aragorn
restore-failed` every 30 min with `"usb_config": "rndis,adb", "rndis0": "-", "rndis0_state": "-", "offload": "?"`.
The tablets ARE plugged in: rigby's `lsusb` lists both as `17ef:7cc8` (the rndis,adb product id), but no
`enx*` appears for them because the gadget carries no rndis function. `/sys/class/net` on the tablet has no
`rndis0`. Read the slots over adb (root):

```
readlink /config/usb_gadget/g1/configs/b.1/f1   # ffs.adb   <-- should be rndis.gs4 (or f2)
readlink /config/usb_gadget/g1/configs/b.1/f2   # ffs.adb
```

**Cause — specific to class A (MediaTek mt6765 `musb-hdrc`, Android 12 GSI):** its init rc
(`/vendor/etc/init/hw/init.mt6765.usb.rc:358-363`, `/system/etc/init/hw/init.usb.configfs.rc:134-139`) builds
`rndis,adb` with `symlink` only, and only the `sys.usb.config=none` action removes slot links — which also stops
adbd, so it is never used remotely (§4). Once an adb-only configuration has put `ffs.adb` in f1, every later
`setprop sys.usb.config rndis,adb` fails in dmesg with `symlink() failed: File exists` and the property still
reads `rndis,adb`. A restore that only does the setprop therefore reports `restore-failed` forever. The 09-29
trigger was a rigby shutdown (USB host gone 19:24 UTC = the f1 link time 14:24 CDT on both tablets); the keeper's
setprop after rigby came back then filled f2 with `ffs.adb` too (link times 15:07/15:08). Expect it after any rigby
reboot or cable unplug. Class B (A11, same SoC) is restored by the relink anyway; class C (Qualcomm A12) init
rewrites its own links, so neither showed it.

**Fix (in Tolkien since 2026-10-02):** after the A12 setprop, if `sys.usb.config` includes `rndis` and no config
slot links an rndis function, `_build_gadget` runs the §4b relink (f2 → `rndis.gs4`, rebind the UDC). Result on
class A: f1 `ffs.adb`, f2 `rndis.gs4` — the same layout frodo has used since 08-17. A failed restore now
reports `gadget_slots` in its DM. Manual equivalent: §4b, or `POST /fellowship/usb-restore {"target":"class-a"}`.

### `USB keeper: <node> has NO USB HOST` / `restore-failed` with `rndis0_state: down` and the gadget slots CORRECT — the tablet is fine, rigby's USB side is not enumerating it (`error -71`)

**Symptom (2026-10-26, legolas, aragorn, faramir):** the keeper's restore report shows a correct gadget
(`"gadget_slots": ["ffs.adb", "rndis.gs4"]`, `usb_config rndis,adb`, `rndis0` addressed, offload off) and still
`rndis0_state: down`, every 30 min. On the tablet: `dumpsys battery` → `USB powered: true`, and
`cat /sys/class/udc/*/state` → `not attached` with the gadget bound (`cat /config/usb_gadget/g1/UDC` non-empty). On
rigby: the tablet's serial is absent from `/sys/bus/usb/devices/*/serial`, and `journalctl -k -b` repeats
`usb 3-N: device descriptor read/64, error -71` / `device not accepting address … error -71` on every attempt.

**Meaning:** power reaches the tablet and nothing completes the USB handshake on that port — a physical problem on
rigby's side (cable, port, or the connector/header feeding it). A tablet-side restore cannot fix it, and neither did
resetting the controller (unbind/rebind of `0000:00:14.0` via `Lab\bin\run_remote.py rigby`: the same `-71` within
seconds). The three tablets sat on rigby's Intel chipset xHCI root ports 3-1 / 3-2 / 3-3 (aragorn / faramir / legolas),
which had worked on the two earlier boots that day; every attempt failed from the first boot after the GT 730 went back
in. **Fix: a person at rigby** — reseat those cables, or move them to free USB 2.0 ports (the tablets' controllers are
high-speed only, so a USB 3.0 port gains them nothing; the EHCI #2 hub, bus 2, had all 8 ports free that day).
**NEVER FIXED SINCE 2026-10-26 — aragorn, faramir, legolas and pippin have been off rigby's USB ever since (read
2026-11-03 from rigby's kernel journal, every boot back to 10-02):**
- **Through boot 2026-10-26 16:18 UTC:** each boot shows ~19 `rndis_host … register` lines (the whole Fellowship
  enumerating) and ~5 `-71` lines (start-up noise).
- **From boot 2026-10-26 19:48 UTC on, every boot:** 44–223 `-71` failures from the first second, and the four never
  register.
- The break is the evening of 10-26, between those boots: rigby's hardware work that evening (GTX 1060 onto the
  full-width riser; the GT 730 back in, as above).
- It survived every reboot since, including 2026-11-03's (6.8.0-31 → 6.8.0-147; on that boot ports `2-1.1`, `2-1.5`
  and `2-1.6` each fail `-71` ×12 on the EHCI 8-port hub), and `usbnet_up.py --apply`.
- Their serials are absent from `/sys/bus/usb/devices/*/serial`, while their batteries hold steady: power reaches
  them, data does not.

⚠ **Instrument trap that hid the start date:** `docker logs tablet-foundry` begins at the container's last
RECREATION (a Tolkien deploy). Its first "NO USB HOST" line is the keeper's first pass in that container, not when
the link died. A 2026-11-03 reading of "all four dropped at 11-02 11:59:23" was that artifact. Date a USB fault from
the kernel journal (`journalctl -k -b <n>`, `--list-boots`), never from a container log.

**Every software lever tried 2026-11-03, and none enumerated them:**
- rigby reboot (13:57 CT, onto 6.8.0-147);
- `usbnet_up.py --apply`;
- all four tablets rebooted through Tolkien (`POST /fellowship/reboot`, restore; each back in 59–83 s, gadget bound);
- a gadget rebind (`usb-restore`);
- usbcore's old enumeration scheme (`old_scheme_first=Y`, runtime, reverted after; script
  `Lab\bin\linux_stage\rigby_usb_scheme.sh`).

Each tablet reboot DID reach rigby: "new high-speed USB device" on `2-1.1`, `2-1.5`, `2-1.6` (EHCI 8-port hub) and
`3-3` (motherboard xHCI root port), then `-71` / "not responding to setup address" on BOTH controllers, under both
enumeration schemes. So both ends reset cleanly and presence is detected, but the high-speed handshake on the wire
between them fails.

**A COLD power cycle did not fix it either** (the owner cut rigby's power at the plug; boot 2026-11-03 23:18 UTC). The
same four failed `-71` on the same ports (`2-1.1` ×12, `2-1.5` ×12, `2-1.6` ×12, `3-3` ×6). `usbnet_up.py --apply`
after it: 6 of 11 on the wire. So the hub/controller power state is ruled out too.

**THE PORT MAP when all eleven worked vs now (2026-11-04, by serial from the kernel journal of the 10-26 19:13 UTC
boot; script `Lab\bin\linux_stage\port_map_then_now.sh` in the Claude scratchpad, the routing read
`Lab\bin\linux_stage\rigby_usb_routing.sh`):**
- **Then:** aragorn `3-1`, faramir `3-2` and legolas `3-3` (motherboard xHCI `00:14.0`, the USB 3 ports); pippin
  `5-2.2` (the Renesas add-in card's hub).
- **Now:** xHCI ports `3-1` and `3-2` have **nothing connected at all**. The failing (`-71`) devices sit on `3-3` and
  on chipset EHCI #2 (`00:1a.0`) internal hub ports `.1`, `.5` and `.6`, which were USB 2-only ports and unused when
  everything worked.
- The seven working tablets are exactly where they were: gimli, sméagol and boromir on EHCI #2 `.2`/`.3`/`.4`
  (renumbered bus 1 → 2 only); frodo, sam, gandalf and merry on the Renesas card.
- Intel port routing (XUSB2PR/USB3_PSSEN at `0xD0`/`0xD8` = `0xf`) is standard: all four switchable ports are
  handed to xHCI, so it is not a BIOS routing change.
- **Conclusion: the four tablets' cables were moved during the 10-26 riser work, into ports where the handshake
  fails.** The known-good configuration is the "then" column. Restore it before trying any other combination.

What still differs from before 10-26 19:48: the ports these four now use (the topology table had pippin and legolas on
the add-in card's 4-port hub at `5-2.x`), the riser, and the cables. **The fix stands: a person at rigby.** Reseat those four cables or move them to free USB 2.0 ports, and check whether the
10-26 riser/GT 730 work disturbed the USB add-in card or the hub's cabling. Found by the #38 update stager's
before/after check ([[Lab-Update-Inventory-38]]).

**The keeper since 2026-10-26** (`tablet_control._no_host`): a restore that ends with the gadget bound and the controller
`not attached` is DM'd once as `has NO USB HOST - …` and not re-run while the tablet's `udc_state` stays the same; any
change puts the node back on the normal path. An UNBOUND gadget also reads `not attached` — that one still gets the
restore, whose rebind is its cure (§ "Gadget detachment recovery").

### USB RNDIS routing isolation for Android hosts

Android's `netd` daemon owns policy-routing rules and does not recognize `rndis0`. Without explicit intervention, reply traffic destined for the USB segment (`10.55.0.0/24`) falls through to the `wlan0` default route. Layer 2 connectivity (ARP resolution, packet delivery) remains functional, but application-layer return traffic fails.

Isolate USB RNDIS traffic using a dedicated routing table with rules prioritized above `netd` (priority < 10000):

```bash
ip route replace 10.55.0.0/24 dev rndis0 src 10.55.0.<n> table 55
ip rule add to   10.55.0.0/24 lookup 55 pref 500
ip rule add from 10.55.0.0/24 lookup 55 pref 501
```

**Verify routing isolation:**
Run `ip route get 10.55.0.1`. The output must specify `dev rndis0`. Presence of an IP address on the interface does not guarantee traffic utilization.

### Class-A USB TCP stall (ssh hangs at key exchange, ping works) — the TABLET's gadget TX offload (gso/sg on rndis0)

**Symptom:** gandalf / aragorn (class A, mt6765 `musb-hdrc`, Android 12 GSI) answer ping over the wire,
complete the TCP handshake on `10.55.0.<octet>:8022`, then ssh stalls at the key exchange until its
timeout. Over WiFi the same command takes ~0.5 s. Other classes are unaffected.

**Cause (measured 2026-08-22 and again 2026-09-28): the TABLET's `rndis0` comes up with TX offload on
(`gso=1 sg=1`) after every gadget build.** With it on, the large frames the tablet SENDS reach rigby with
wrong TCP checksums: a capture on gandalf's rigby port showed every rigby→tablet frame correct, the
tablet's small ACKs correct, and its 1040-byte KEXINIT segment plus all three retransmits `incorrect`.
Class B nodes read `gso=0 sg=0` and never show it. rigby's `enx`/`br0` offload settings are not the cause
(all `enx` ports already read off and the stall persisted). A test that pushes data rigby→tablet does
NOT exercise this direction — check with an ssh session (tablet → rigby frames), not a one-way push.

**Fix: `usb_offload_off.py rndis0` run ON THE TABLET as root** (Termux python3 over adb; there is no
`ethtool` on the GSI). One copy: `E:\SSDShared\tolkien-build\usb_offload_off.py`, shipped in the Tolkien
image; `--check` prints `offload: on|off`. Applied automatically by:
- **Tolkien** — `/fellowship/status` reports `offload`; the USB link keeper counts `offload: on` as a
  down link and restores it; every `usb-restore` turns it off (from the 2026-09-28 deploy; before that,
  Tolkien's restore path skipped it, which is how class A lost the wire after its gadgets were rebuilt).
- `usbnet_up.py --apply` (`harden_tablet()`), from Softserve.

Manual check/fix through Tolkien's adb (only one adb server may own the tablets):
`adb -s <wifi_ip>:5555 shell /data/data/com.termux/files/usr/bin/python3 /data/local/tmp/usb_offload_off.py rndis0 --check`.

**Operational status: gandalf (`10.55.0.16`) and aragorn (`10.55.0.17`) are full segment members.** Do
NOT remove them from runs or switch them to NCM for reachability; NCM remains a protocol-comparison
option only (§ Switching a tablet between RNDIS and NCM).

### Termux environment constraints and channel privileges

Termux lacks `/tmp`, `ss`, `which`, and a full `ip` binary on its default PATH. These absences cause silent failures in standard diagnostic commands.

#### Missing tools and workarounds

- **No `/tmp` directory**: Commands redirecting output to `/tmp` fail silently. Use `$HOME` for temporary files and logs.
- **No `ss` or `which`**: Network listener checks using `ss` return empty counts, indistinguishable from a missing service. Tool availability checks using `which` fail.  
  *Workaround*: Verify listener status by establishing a TCP connection from the sender:
  ```python
  python3 -c "import socket;s=socket.create_connection(('10.55.0.13',9410),4);s.close();print('CONNECT_OK')"
  ```
- **`ip` not on PATH**: The `ip` command is located at `/system/bin/ip`. Calling `ip` without the full path returns rc=127 (`command not found`). If stderr is discarded, this appears as a successful empty response.
  *Workaround*: Use the full path `/system/bin/ip` for routing diagnostics:
  ```bash
  /system/bin/ip route get 10.55.0.13     # -> 10.55.0.13 dev rndis0 table 55 src 10.55.0.12
  /system/bin/ip route get <lan-address>   # -> <lan-address> dev wlan0 table 1010 src <lan-address>
  ```
  This confirms the specific device and routing table, preventing ambiguous medium comparisons.

  *Fallback if `ip` is unavailable*: Use Python UDP socket `connect()` (no packet sent) to trigger kernel route resolution and read `getsockname()[0]` for the source address:
  ```python
  python3 -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('<lan-address>',9));print(s.getsockname()[0])"
  ```

#### Channel privilege doctrine

Lab doctrine dictates: **The channel determines the UID, and the UID determines what you can see.** The two tablet access channels are not interchangeable.

| channel | uid | capabilities |
|---|--:|---|
| **adb from rigby** (`adb -s <serial> shell`) | 0 (root) *after `adb root`* | Full system access. Includes `ip -o link show` and all §4 configuration commands. **A freshly-rebooted node's adbd may come up as uid 2000 (shell)** — run `adb root` and confirm `adb shell id -u == 0` FIRST, or every §4 write is silently SELinux-refused (§4g). |
| **Termux ssh** (port 8022) | 10135 / 10168 (unprivileged) | Restricted access. `/system/bin/ip` works for `addr show`, `route get`, and `route show table 55`. `ip -o link show` is refused (`Permission denied`). No `su` available. |

**Operational rule**: Perform all §4 configuration via adb. Use Termux ssh only for user-space tasks (Python sinks, benchmark clients). Errors resembling permissions denials or kernel bugs on unprivileged channels are often caused by sending root-channel commands over the unprivileged channel.

## Set `sys.usb.config` via WiFi ADB only

Setting `sys.usb.config` to `none` stops `adbd` immediately, severing the connection used to configure USB. This is the one change that cannot be undone remotely — because it kills adbd itself, **not because the tablets lack root.**

**These tablets are rooted over adb.** They are `-userdebug` GSI builds (`ro.debuggable=1`): `adb root` restarts adbd as uid 0, and some nodes already run adbd as root by default (frodo: `adb shell id` = `uid=0(root)`, context `u:r:su:s0`, with no prior `adb root`, verified 2026-08-28). Root arrives THROUGH adbd — there is no on-device `su` binary and no Magisk — which is exactly why stopping adbd (config `none`) is unrecoverable remotely and needs a physical power cycle. Do **not** read "no su / no Magisk" as "no root": it only means a *stopped* adbd has no on-device fallback to restart it. For privileged reads/writes over adb see [[Fellowship-Node-Provisioning]] and [[HANDOFF-tablet-lineage-flash]].

A physical power cycle restores connectivity after config `none`. The property is not persistent, so the device returns to its default USB configuration on reboot.

Always execute USB configuration changes over WiFi ADB (`persist.adb.tcp.port = 5555`, set fleet-wide). Never use USB ADB for these operations, as reconfiguring the USB channel cuts the management connection.

### 4b. Repair the gadget link — every Android version

**Condition:** `rndis0` is absent AND `readlink /config/usb_gadget/g1/configs/b.1/f2` does not name `rndis.gs4`. Detected by `usbnet_up.py::gadget_needs_relink()`.

**Prerequisite:** The gadget must exist with `functions/rndis.gs4` created. This section repairs an existing gadget link; it is not a initialization step. If the device was freshly booted, run 4c (`setprop`) first to ensure init has built the gadget and functions. See §4c for gadget initialization.

**Execution constraint:** Run detached on the device. Unbinding the UDC drops the USB connection and may terminate the controlling shell. A failed bind leaves no gadget until reboot.

**Procedure:**
```bash
G=/config/usb_gadget/g1
UDC=$(cat $G/UDC)
echo "" > $G/UDC                       # unbind: configfs refuses link edits while bound
rm -f $G/configs/b.1/f2
ln -s $G/functions/rndis.gs4 $G/configs/b.1/f2
echo "$UDC" > $G/UDC                   # rebind
```

**Verification:** Confirm `rndis0` is present in `/sys/class/net/`. adbd WiFi listener survives this operation.

### 4c. Configure RNDIS interface and policy routing on every tablet

Every tablet running the RNDIS host stack must have its USB subsystem configured for rndis,adb, and its network interfaces assigned to the 10.55.0.0/24 subnet using policy-based routing.

```bash
setprop sys.usb.config rndis,adb       # Skip if already set; if no rndis0 appears, execute section 4b first
ip addr add 10.55.0.<n>/24 dev rndis0
ip link set rndis0 up
ip route replace 10.55.0.0/24 dev rndis0 src 10.55.0.<n> table 55
ip rule add to 10.55.0.0/24 lookup 55 pref 500
ip rule add from 10.55.0.0/24 lookup 55 pref 501
```

Replace `<n>` with the tablet's unique host ID within the subnet.

### 4d. rigby — bridge everything

Create a Linux bridge (`br0`) and assign it address `10.55.0.1/24`. Attach all Ethernet interfaces matching the prefix `enx` to the bridge as ports.

```bash
ip link add name br0 type bridge
ip addr replace 10.55.0.1/24 dev br0
ip link set br0 up
for i in /sys/class/net/enx*; do ip link set $(basename $i) master br0 up; done
```

### Switching a tablet between RNDIS and NCM

To switch protocols, set the USB configuration property via ADB (uid 0). Termux SSH lacks permissions for link inspection.
```bash
setprop sys.usb.config ncm,adb        # or rndis,adb
```

**Interface naming convention:**
* NCM presents as `usb0`.
* RNDIS presents as `rndis0`.
* **There is no `ncm0`.** The string `ncm.0` refers to the configfs function, not the network interface. Hardcoding `ncm0` results in address/route application failures and "not reachable" errors.

**Identifying the active interface:**
Do not hardcode interface names. Gadget re-enabling creates new interface indices while old ones linger. Query the tablet via ADB:
```bash
adb -s <serial> shell "ip -o link show" | grep -oE '^[0-9]+: (rndis[0-9]+|ncm[0-9]+|usb[0-9]+)' | sort -n | tail -1
```
Verify the address landed on the identified device (`ip -o -4 addr show dev <iface>`) before assuming connectivity.

**Symptoms of interface identification errors:**
* `Device "ncm0" does not exist` (ignored return value).
* Host interface remains `DOWN` and unbridged.
* Rigby's ARP entry for the tablet reads `FAILED`.

**Android version compatibility:**
* **Android 12:** Editing configfs directly (`ncm_swap.sh`) does not work. Init rewrites the link to `rndis.rndis` on every UDC rebind. Use the `setprop` property method only.
* **Android 11:** The property method is mishandled. Use configfs editing only (§4b).

**Hardware constraints (sam/legolas):**
Devices `sam` and `legolas` cannot run NCM. Their kernels lack the NCM gadget function (`mkdir /config/usb_gadget/g1/functions/ncm.0` fails with ENOENT). The presence of `cdc_ncm` in `/sys/module` indicates only the host-side driver, not gadget support. Only `merry` and `pippin` (Android 12, Qualcomm) support switching.

**Post-switch rebuild requirements:**
A protocol switch tears down four components. All must be rebuilt or the tablet falls back to WiFi silently:
1. **Tablet address:** Assign a new address on the new interface.
2. **Table-55 route:** Rebuild routes (`ip route show table 55` will be empty post-switch). See Trap 3.
3. **Host-side bridge port:** The gadget generates a new random MAC, creating a new `enx*` device and removing the old one. Restore the bridge from Softserve — it drives the rigby-side root step over `run_remote.py`:
   ```bash
   python E:\SSDShared\Lab\fellowship\usbnet_up.py --apply
   ```
   This enslaves every `enx*` by glob into `br0` via `fellowship_bridge.sh` (root) and asserts the member count against the passed roster — the 7 segment octets `11`–`17`, not a hardcoded number — then flushes ARP and probes each octet. It exits non-zero on a partial segment. Do not silence stderr or ignore exit codes; a short bridge (e.g., 5 of 7) leaves the missing tablets unreachable behind a `br0` that otherwise looks healthy. (The older `~/rebridge.sh` on rigby still exists but defaults to a 4-port count — do not use it.)
4. **Rigby ARP cache:** Flush stale ARP entries for the old MAC to prevent ~95% inbound packet loss:
   ```bash
   ip neigh flush dev br0
   ```

**Gadget detachment recovery:**
Repeated switching may leave the gadget unbound (`/sys/class/udc/msm_hsusb/state` reads `not attached`). The host sees fewer interfaces, but no errors are generated. Check UDC state before diagnosing speed issues.
Rebind the controller:
```bash
G=/config/usb_gadget/g1 ; echo "" > $G/UDC ; sleep 2 ; echo msm_hsusb > $G/UDC
```
Confirm `state=configured`.

### 4f. ⚠ Runtime-only configuration: RNDIS interfaces and bridging require post-reboot re-application

`sys.usb.config`, the configfs relink, assigned addresses, and routing rules are **runtime-only** on the tablet side. A rebooted tablet silently drops off the segment; traffic reverts to WiFi with no error reporting. Addresses simply stop answering. Re-apply the tablet-side restore after any reboot (§4g). Check `ip route get` per node before trusting any measurement taken on this segment.

**The HOST-side bridge now self-heals (2026-08-29).** A rigby systemd service, `fellowship-bridge.service` (`/usr/local/sbin/fellowship-bridge-daemon.sh`; source and installer in `Lab\fellowship\rigby`, since 2026-11-19), watches `ip monitor link` and re-enslaves every `enx*` into `br0` — bringing it up, disabling rigby-side TX offload (`sg/gso/tx`), and flushing ARP — on any link event, plus a reconcile at boot. So a returning tablet's fresh `enx` (new MAC/name every boot) is bridged within seconds with **no host step**, which closes the old "re-bridge `br0` after every reboot" gap. It matches on the `enx*` pattern, never a fixed name (the volatility constraint below). `systemctl status fellowship-bridge` on rigby; `journalctl -t fellowship-bridge` shows each enslave. The tablet side (gadget + address + the tablet's own `rndis0`
offload-off, §4g and § "Class-A USB TCP stall") is still per-reboot, and Tolkien's USB link keeper re-applies it.

#### ⚠ RNDIS interface name volatility

The RNDIS gadget generates a **fresh MAC at every boot**. Interface names (`enx*`) are derived from the MAC, so names change between boots. The old name disappears.

**Consequences:**
* Components hard-coding an interface name (bridge commands, netplan stanzas, udev rules keyed on name, monitoring checks) **bridge or watch the wrong set** and report success incorrectly.
* `br0` retains its *old* members. The bridge appears healthy while the returning node sits outside it. The node has a correct address and correct `ip route get` output, but cannot be reached: **tablet-side verification passes while the link is dead.**

**Procedure:** Always re-bridge by GLOB and ASSERT THE COUNT. `usb_interfaces_present` must equal `br0_member_count`. "Enslaved something" and "enslaved everything" produce identical output; count mismatch detection is required.

**Constraint for persistence:** Persistence cannot rely on a list of interface names. A fix must match on the `enx*` pattern or on the USB device path, never on an identity regenerated at every boot.

### 4g. Post-reboot USB restore — `adb root` FIRST, then rebuild the gadget (the uid-2000 trap)

A rebooted tablet drops off the segment (§4f), and its adbd frequently comes back as **uid 2000
(shell), not root** — node-dependent, and *independent of* `persist.sys.usb.config`. As uid 2000
every §4 command fails **silently**: `setprop sys.usb.config` is refused by SELinux and the value
reverts to `none`; `/config/usb_gadget/*` reads `Permission denied`; `usbnet_up.py` cannot read the
gadget and skips the node. A node in this state shows `sys.usb.config=none`, `udc … not attached`,
and a gadget skeleton whose config has no function links. **These are permission failures, not a
wedged controller — do NOT diagnose a hardware or power-cycle problem until `adb shell id -u`
returns `0`.** (2026-08-29: this exact mis-read cost hours on gandalf; the fix was two commands.)

**The easiest path — ask Tolkien (since 2026-08-29).** The tablet-foundry container serves a
token-gated control API on **`0.0.0.0:8740`** (`TOLKIEN_CTL_BIND` in tolkien.env; default 127.0.0.1),
so **any admin/backend-layer device on the LAN or tailnet** can reboot, power off, usb-restore, or
status the tablets — like a direct command to the box. No WAN (rigby has no port-forwards); the token
is the only gate. It does the whole restore over adb and the host bridge self-heals (§4f), so one call
reboots a **class-A/B/C** node and brings it fully back on the segment.

⚠ **The "one call restores" claim does NOT hold for Sméagol** (SM-T560NU, LineageOS 18.1 + armv7 Termux,
USB-RNDIS at `10.55.0.18`). Sméagol's restore path is different: Termux:Boot brings sshd + fellow-agent
back on the device side, and rigby reasserts RNDIS composition and address from the host side; the adb
`setprop sys.usb.config rndis,adb` / configfs relink dance in this section is A11/A12-specific and does
not apply. Real-model generation and reboot survival are proven (2026-09-04). Refer to Sméagol-specific
provisioning notes rather than assuming this section covers it end-to-end.

The token (`TOLKIEN_CTL_TOKEN`) lives in `/opt/arrstack/tolkien.env` on rigby (source of truth) and is
copied into the **credential locker** — `Reference\Credentials\network-credentials.md` § "Tolkien
tablet control" — for retrieval. Call from any device:
```bash
# TOK = the locker token; reboot+restore gandalf (or a group / all):
curl -s -H "X-Tolkien-Token: $TOK" -H "Content-Type: application/json" \
  -d '{"target":"gandalf","restore":true}' http://<rigby-address>:8740/fellowship/reboot
```
Endpoints: `GET /fellowship/status[?target=…]` · `POST /fellowship/reboot {"target","restore":true}` ·
`POST /fellowship/off {"target"}` (power off — **no remote power-ON after**; needs a physical button) ·
`POST /fellowship/usb-restore {"target"}`. `target` = `all` | a group (`class-a` gandalf,aragorn ·
`class-b` frodo,legolas,sam · `class-c` merry,pippin) | comma-separated node names.
⚠ **Group definitions above may be stale as of 2026-09-04** — the Fellowship grew to 11 (added Sméagol
armv7, plus Gimli/Boromir/Faramir as additional TB-X606F class-B). If `tablet_control.py`'s group
table was expanded to include the four new nodes, the enumeration above needs updating; verify against
the deployed code before quoting. No/invalid token
→ 401; token unset → the server fails closed and does not start (order desk unaffected). Code:
`tablet_control.py` (tolkien image); reuses `tablet_foundry.TABLET_NET` + adb helpers.
⚠ **Only ONE adb server may own the tablets at a time.** If a Softserve session is holding them
(`adb devices` lists them), the container reads those nodes `adb=unreachable`; `adb disconnect` on
Softserve releases them. And the FIRST `status` right after a container restart may read nodes
`unreachable` while the container's adb server warms — retry, or use an action verb (reboot/restore
poll `adb root` and self-warm).

**The restore dance, per node** (what Tolkien runs; use directly if Tolkien is down, or via
`fellowship_ctl.py` on Softserve):
```bash
adb -s <wifi_ip>:5555 root                                   # restart adbd as uid 0 — THE step
adb -s <wifi_ip>:5555 shell id -u                            # must print 0 before continuing
# If sys.usb.config is 'none' (unlinked skeleton): build the gadget.
adb -s <wifi_ip>:5555 shell setprop sys.usb.config rndis,adb # A12: links f1->rndis.gs4, f2->ffs.adb, binds UDC
#   ...but only into EMPTY slots. If `readlink .../configs/b.1/f*` still shows no rndis function afterwards
#   (class A after a rigby reboot: both slots ffs.adb), run the §4b relink too — § "Class A … restore-failed".
#   A11 nodes: use the §4b configfs relink instead (the property method is mishandled on A11).
python usbnet_up.py --apply --only <node>                    # segment: address + route + bridge + offload + verify
```
`rndis,adb` always includes `adb`, so it is safe over WiFi adb — **never** set `none` (§4; it stops
adbd and needs a physical power cycle). Confirm the end state from rigby (`ping 10.55.0.<octet>` +
`ssh :8022`), not from the tablet side (§4f interface-name volatility: tablet-side checks pass while
the link is dead).

**Or use the tool that encodes all of the above** (`E:\SSDShared\Lab\fellowship\fellowship_ctl.py`,
sibling to `usbnet_up.py`; audit trail in `fellowship_ctl.log`):
```bash
python fellowship_ctl.py status all                 # per node: adb root?/android/usb.config/rndis0/segment/models
python fellowship_ctl.py usb-restore <node|group|all>   # adb root -> build gadget by android -> usbnet_up --only -> verify
python fellowship_ctl.py reboot <node|group|all> [--restore]
python fellowship_ctl.py boot-recover <node|group|all>  # reboot + usb-restore in one
```
Groups: `all`, `class-a` (gandalf,aragorn), `class-b` (frodo,legolas,sam), `class-c` (merry,pippin).
⚠ Same 2026-09-04 caveat as the Tolkien endpoint enumeration above — Fellowship additions (Sméagol
armv7, plus Gimli/Boromir/Faramir class-B) may not yet be reflected in `fellowship_ctl.py`'s group table.
