# Fellowship tablet intake — canonical Rigby workflow

> 🪄 **Zero-touch / golden-image intake (no setup on the tablet after flash):** see
> `RUNBOOK-Fellowship-Zero-Touch-Golden-Intake.md`. That doc covers the golden image (baked
> first-boot wizard-skip + rooted-debug + durable adb), the sanctioned WiFi-config push (no PSK on
> the command line), running the provisioner as root, the ffmpeg/libc++ and sshd fixes folded in on
> 2026-09-02, and the human-vs-scripted map. Proven on gimli 2026-09-02.

## Status and authority

This is the canonical operator entry point for adding a supported tablet to the Fellowship. Rigby
is the intake host and Tolkien remains the owner of the worker after registration. Intake does not
bypass Kirk, Spock, or Tolkien; it prepares one worker and then registers it through their existing
roster and deployment mechanisms.

The implementation is `/opt/arrstack/tablet-intake/tablet_intake.py` on Rigby. Durable state is in
`/opt/arrstack/tablet-intake/state/`; large images and non-secret worker payloads are local to Rigby
under `/mnt/fellowship/tablet-intake/`. A stopped Codex/Claude session is not state loss: rerun the
same command with the recorded serial to resume.

### Recovered lineage

The implementation preserves the harness built during tablets #5/#6:

- `gsi_flash.sh`: established classic-fastboot A-only X606F sequence;
- `provision_node.sh`: full Termux/Termux:Boot, wake-lock, packages, SSH, doze, agent, detector,
  watchdog, identity, shared-token, and positive-control provisioning;
- `Lab\fellowship\payload`: APKs, boot scripts, binaries, detector model, and control image pulled
  from a working node and hash-verified.

The Rigby runner supplies the missing lifecycle around those proven pieces: unregistered-device
detection, exact model/profile selection, full artifact manifests, destructive gates, physical
prompts, ADB/fastboot transition handling, and durable checkpoints.

### Tablet #7 is not the template

Tablet #7 (`aragorn`, TB-X6C6F) used hardcoded `~/fellowship/t7_unlock.sh` and `t7_flash.sh` plus
manual image staging and roster/network work after an interrupted handoff. It did reuse the repaired
full provisioner. The hardcoded scripts remain historical evidence for the separate X6C6F A/B +
fastbootd profile; they are not the canonical operator interface and must not be copied for X606F.

## Enabled hardware profiles

Intake is fail-closed. A profile is enabled only when its model identity, image, hash, partition
layout, unlock command, and flash mode are explicit in `profiles.json`.

| model | state | OS | path |
|---|---|---|---|
| Lenovo TB-X606F | enabled | LineageOS 18.1 / Android 11 | classic bootloader fastboot, A-only |
| Lenovo TB-X6C6F | known, not yet enabled in the canonical runner | LineageOS 19.1 | A/B + fastbootd |
| Lenovo TB-X605F | known name, no recovered authoritative profile | — | reject |
| Lenovo TB-X505F | live Merry/Pippin evidence conflicts with the requested X605F family list | — | reject pending reconciliation |

Do not infer an alias or flash a near-looking model.

## Operator command

From Rigby:

```bash
python3 /opt/arrstack/tablet-intake/tablet_intake.py inspect --name <fellow-name>
python3 /opt/arrstack/tablet-intake/tablet_intake.py run --name <fellow-name> --serial <serial>
```

`inspect` is read-only. It excludes every serial already in `roster.json`, requires exactly one new
authorized ADB device, reads the model and `sys.oem_unlock_allowed`, selects exactly one profile,
and verifies the image, source archive, vbmeta, and every worker-payload file against SHA-256.

Before the first destructive command it prints and checkpoints:

- detected serial and model/product;
- expected hardware family;
- selected OS/image and SHA-256;
- selected flash path/tooling;
- exact hardware-match verdict and OEM-unlock property.

Any mismatch stops the run.

## Required physical interaction

The runner pauses visibly and checkpoints before every unavoidable action.

1. **First ADB authorization:** unlock the tablet and approve Rigby's USB-debugging key; select
   **Always allow**.
2. **Bootloader unlock:** after the runner starts `fastboot flashing unlock`, press **Volume Up** on
   the tablet to confirm. This is required. the owner directly confirms he performed this physical step
   during all prior tablet intakes; older notes saying no button was needed are incorrect.
3. **Return to fastboot if firmware reboots after unlock:** power completely off, then hold
   **Volume Down + Power**.
4. **First LineageOS boot:** allow 5–10 minutes, complete the setup wizard, connect Wi-Fi, enable
   Developer options and USB debugging, and approve Rigby's key again.
5. **Rooted debugging:** only when the installed GSI actually refuses `adb root`; the runner reports
   the refusal and asks for the toggle rather than assuming every version has it.

Do not enable Android's Wireless debugging. Durable ADB is
`persist.adb.tcp.port=5555`, set through rooted adbd and read back before provisioning.

## X606F flash path

The enabled X606F profile pins:

- image: `lineage-18.1-20240121-UNOFFICIAL-arm64_bvS.img`;
- raw SHA-256: `498a023c1d48b2cbf1281d5766640319ae5fd28b53ff0b91242e88c46ec1330b`;
- source `.xz` SHA-256: `ee126763ee3a03518d829c9ae08204a535342c062936b3124bd0e93e623ff941`;
- classic bootloader fastboot, physical A-only `system`;
- `fastboot flashing unlock` with Volume Up confirmation;
- explicit `system`, `userdata`, and `cache` erases; disabled vbmeta; system flash; explicit ext4
  userdata format. The runner does not use the version-sensitive `fastboot -w` shortcut.

X606F stays on Android 11/LOS 18.1 because Android 12's SAE auto-upgrade breaks this hardware's
Wi-Fi association against MossMatriarch's WPA2/WPA3 transition mode.

## Provisioning and acceptance

The Rigby port of the recovered full provisioner must finish green. It installs and launches the
matched Termux/Termux:Boot APK pair, takes and observes a wake-lock before apt, installs required
packages, establishes SSH, applies doze allowances, deploys the current agent copied from the
deployed Tolkien container, installs the detector and boot/watchdog components, sets `.fellow_name`
and the existing shared worker token, and runs functional checks including ffmpeg execution and the
agent's positive detector self-test.

After provisioning, registration must complete before the node is production-ready:

- reserve its observed per-SSID `wlan0` MAC to an unused production address on MossMatriarch;
- add `<name>.lan` to Pi-hole and verify resolution;
- allocate the next USB segment octet and configure RNDIS through Tolkien's control path;
- add the node to the authoritative Tolkien and scheduler rosters (the scheduler's is the device roster file's
  `tablets` list, `Z:\Documentation\State\device-roster.json`, not `scheduler.py`), with actual hardware family
  `Lenovo TB-X606F` (not an experimental matrix lane);
- rebuild all consumers required by the scheduler/Tolkien coupling and recreate only Rigby's
  `tablet-foundry` + `tablet-watchdog` copies (Sundae's moved copies stay down);
- verify reboot survival, SSH, durable ADB, watchdog, detector positive control, Tolkien health,
  and the normal Fellowship factory status.

Normal intake inherits class values for CPU, RAM, and scheduling. It does not run a private
benchmark campaign. A node-specific performance claim requires separate evidence and scope.

## Durable state and recovery

Inspect state with:

```bash
python3 /opt/arrstack/tablet-intake/tablet_intake.py status
```

Each state record names the last completed stage, serial, model/profile, artifact hashes, physical
actions already acknowledged, observed Wi-Fi identity, commands/results, changed files, and next
expected state. Never repeat unlock or flash merely because the controlling chat/session changed.
