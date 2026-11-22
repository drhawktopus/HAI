# Fellowship Samsung Tablet Intake via Heimdall (SM-T560NU / gtelwifiue, Download Mode, TWRP + LineageOS sideload)

**What this is:** the intake path for **Samsung** tablets, which flash from **Download Mode via Heimdall**
(NOT fastboot) and are **invisible to adb/fastboot** in that state. Added 2026-09-03 for **Sméagol**
(Samsung Galaxy Tab E 9.6 Wi-Fi, SM-T560NU, codename gtelwifiue, Snapdragon 410) — the fleet's first
Samsung/Snapdragon node. The runner `/opt/arrstack/tablet-intake/tablet_intake.py` auto-detects a
Download-Mode device (`heimdall detect`) and takes the Samsung branch (`inspect_samsung` / `flash_samsung`);
everything else is the existing X606F fastboot path.

⚠ **This family is UNPROVEN end-to-end.** The non-destructive inspection is proven live; the destructive
flash (Heimdall recovery + TWRP sideload) must be run **interactively with eyes on the device** — physical
mode transitions (Download→TWRP) and TWRP's GUI cannot be automated. Prove it once by hand, then trust it.

## Prerequisites (the human/physical side — done once, off the runner)

- Device **factory-wiped**, **OEM Unlocking enabled**, **USB debugging enabled** (before Download Mode).
- Device in **Samsung Download Mode**: power off; hold **VOLUME DOWN + HOME + POWER**; press **VOLUME UP**
  to continue. Connect to Rigby with a **data-capable** cable.
- On Rigby: **Heimdall installed** (`command -v heimdall`), and it can access the device as user `owner`
  (verified — no root needed for detect/print-pit/flash on Rigby's udev).
- Assets staged under **`/mnt/fellowship/tablet-intake/roms/`** (this is `ASSETS`, distinct from the
  runner root `/opt/arrstack/tablet-intake` = `ROOT`):
  - LineageOS 18.1 zip + its real sha256 in the profile's `image` / `image_sha256`.
  - TWRP recovery `.img` + its real sha256 in `recovery` / `recovery_image_sha256`.
- Profile in `profiles.json` with `flash_path: "heimdall-download-mode"`, `enabled: true`, correct
  `hardware_family` / `codename` / `lineage` / `android`. (Optional `recovery_partition`, default `RECOVERY`.)

## Step 1 — Inspect (non-destructive, safe to run anytime)

```
ssh rigby
cd /opt/arrstack/tablet-intake
python3 tablet_intake.py inspect --name smeagol
```
This runs `heimdall detect` → `heimdall print-pit` (partition evidence) → verifies the LOS zip + TWRP
recovery by **sha256** → prints the **SAMSUNG safety gate** and checkpoints `stage=safety_gate_passed`.
It refuses (`blocked_pit_mismatch`) if the PIT lacks the expected recovery partition — that is the identity
check, because Download Mode exposes **no `ro.product.model`** (identity = operator intake + PIT signature).
Review the safety gate before continuing.

## Step 2 — Flash (DESTRUCTIVE — run interactively, at the device)

```
python3 tablet_intake.py run --name smeagol
```
Resumes from the safety gate and pauses at each physical gate (press Enter to proceed):
1. Confirm the destructive gate.
2. Runner: `heimdall flash --RECOVERY <twrp>.img --no-reboot` (replaces stock recovery — reversible).
3. **You:** boot into TWRP — power off, then hold **VOLUME UP + HOME + POWER**; release when TWRP appears
   (Keep Read Only if asked).
4. Runner waits for TWRP's adb, then `twrp format data` + wipe cache/dalvik.
5. **You:** in TWRP, **Advanced → ADB Sideload**, swipe to start.
6. Runner: `adb sideload <lineage-18.1>.zip`.
7. **You:** **Reboot → System** (first boot takes several minutes).
8. **You:** complete the LineageOS wizard — connect **MossMatrix** Wi-Fi, enable Developer options +
   USB debugging + **Rooted debugging**, approve Rigby's key.
9. Runner resumes: `durable_adb` (adb root, persistent tcp:5555) → `provision_node_rigby.sh` → node up.

⚠ **Samsung is a NON-golden path.** There is no baked first-boot hook (that is X606F-only, see
`RUNBOOK-Fellowship-Zero-Touch-Golden-Intake.md`), so the wizard / Wi-Fi / debugging toggles are **manual**
(the runner's `wifi_push` golden step is skipped). A future golden image for this SoC could close that.

## Step 3 — Production registration

Same as any node: add to `roster.json` (the intake's own, on rigby), `TABLET_NET`, the scheduler's device roster (`Z:\Documentation\State\device-roster.json`, its `tablets` list), `tablet_fulfil`, watchdog rearm,
`probe.py`/`capability.py`, DNS; confirm Tolkien sees it; run health checks. See
`RUNBOOK-Fellowship-Tablet-Intake.md`.

## Runner internals (for the next maintainer)

- Dispatch: `execute()` calls `heimdall_detect()`; a Download-Mode device → `inspect_samsung` +
  `flash_samsung`; otherwise the X606F fastboot path. `is_samsung` is stored in state so resume stays on
  the right branch.
- Samsung state is keyed on `state/pending.json` until TWRP's adb serial appears (Download Mode has no
  serial), then it moves to `state/tablet-<serial>.json`. Stages: `safety_gate_passed → recovery_flashed
  → twrp_adb_ready → system_sideloaded → flashed → …`.
- `verify_artifacts_samsung` checks only `image`+`recovery` (the X606F `verify_artifacts` needs
  `vbmeta`/`source_archive`, which Samsung profiles do not have — do not point a Samsung profile at it).

## History / provenance

The Samsung runner support was first built by HAI (its coding runner) to the pre-flash boundary, but that
work (worktree `wt-46d1…`, commit `dd1977c`) was **pruned and unrecoverable** by 2026-09-03; Claude rebuilt
it directly and proved `inspect_samsung` live. The gaps that made HAI unable to finish it itself are
recorded in `LLM-Project\HAI-Autonomy-Gaps-Fellowship-Intake.md` (epic backlog). Related:
[[RUNBOOK-HAI-Host-Admin-SSH]], `RUNBOOK-HAI-Host-Admin-SSH.md`.
