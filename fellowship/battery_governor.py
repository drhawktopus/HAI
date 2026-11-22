#!/usr/bin/env python3
"""Fellowship battery governor — hold each tablet's charge in a longevity band.

The tablets are always powered over USB, so they sit at 100% and calendar-age.
This polls each tablet's capacity over WiFi adb and toggles its per-model charge
knob to hold [LOW, HIGH] (default 50-70%: near-ideal storage charge, shallow
cycles, off the 100% top).

FAIL-SAFE BY CONSTRUCTION: the knob resets to charging-ENABLED on reboot, and the
default hardware behaviour is always "charge". Every failure mode here -- an
unreachable tablet, a crash, a missed poll -- leaves that tablet charging
normally. Nothing this governor does can strand a tablet empty; the worst case is
a tablet charges a little past HIGH until the next poll.

Charge knob per family (proven by a status flip Full<->Discharging; the
value is uniform: 0 = stop charging, 1 = charge). adbd runs as root on these
-userdebug GSIs, so the echo writes need no `su`:
  A  mt6765/A11  TB-X606F   /sys/class/power_supply/battery/charging_enable
  B  mt6765/A12  TB-X6C6F   /proc/mtk_battery_cmd/en_power_path
  C  msm8937/A12 TB-X505F   /sys/class/power_supply/battery/charging_enabled
Each tablet's switch is its roster entry's battery_switch.
Family B's dedicated StopCharging_Test node is SELinux-blocked even as root;
en_power_path is the working path.
"""
import argparse
import subprocess

import roster
import time

ADB = roster.S.setting("ADB_EXE", "adb")   # the adb binary (the ADB_EXE setting; "adb" on a Linux host)
LOW, HIGH = 50, 70

# name -> (address, charge switch path): the roster's tablets that have a charge switch
ROSTER = {n: (t["address"], t["battery_switch"]) for n, t in roster.tablets().items() if t.get("battery_switch")}


def adb(ip, *args, timeout=15):
    return subprocess.run([ADB, "-s", f"{ip}:5555", *args],
                          capture_output=True, text=True, timeout=timeout)


def shell(ip, cmd):
    return adb(ip, "shell", cmd).stdout.strip()


def govern_one(name, ip, knob, dry):
    subprocess.run([ADB, "connect", f"{ip}:5555"], capture_output=True, text=True, timeout=10)
    raw = shell(ip, "cat /sys/class/power_supply/battery/capacity")
    try:
        cap = int(raw)
    except ValueError:
        print(f"  {name:8} UNREACHABLE -- skipped (charges normally; fail-safe)")
        return
    status = shell(ip, "cat /sys/class/power_supply/battery/status")
    if cap >= HIGH:
        action, want = "STOP charging", "0"
    elif cap <= LOW:
        action, want = "resume charge", "1"
    else:
        print(f"  {name:8} {cap:3}% {status:12} in band -- leave")
        return
    if dry:
        print(f"  {name:8} {cap:3}% {status:12} WOULD {action}")
        return
    shell(ip, f"echo {want} > {knob}")
    # report what the knob READS, not only the status label: a write that did not take must not read as done
    got = shell(ip, f"cat {knob}")
    if got.strip() in ("", "NA"):
        # the X606F knob is write-only (reads "NA" even right after a write): status is the evidence
        note = "  knob unreadable - judge by status"
    else:
        note = "" if got.strip() == want else f"  KNOB MISMATCH: wrote {want}, reads {got.strip()}"
    print(f"  {name:8} {cap:3}% -> {action:14} (knob {got.strip() or '?'}, status now "
          f"{shell(ip, 'cat /sys/class/power_supply/battery/status')}){note}")


def govern(dry=False, skip=()):
    for name, (ip, knob) in ROSTER.items():
        if name in skip:
            print(f"  {name:8} skipped (--skip)")
            continue
        try:
            govern_one(name, ip, knob, dry)
        except Exception as e:                       # one unreachable tablet must not end the run for the rest
            print(f"  {name:8} UNREACHABLE ({type(e).__name__}) -- skipped (charges normally; fail-safe)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="read + decide, change nothing")
    ap.add_argument("--band", help="override band, e.g. 50,70")
    ap.add_argument("--skip", help="comma-separated tablet names to skip")
    args = ap.parse_args()
    if args.band:
        LOW, HIGH = (int(x) for x in args.band.split(","))
    skip = set(args.skip.split(",")) if args.skip else set()
    print(f"[battery-governor] band {LOW}-{HIGH}%  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    govern(dry=args.dry_run, skip=skip)
