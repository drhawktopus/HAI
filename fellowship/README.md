# The Fellowship: Android tablets as a compute cluster

The Fellowship is a set of Android tablets that take work from Tolkien, the order taker on the tablet head. This
folder holds what builds and keeps them: flashing, provisioning, the tablet roster, the USB network segment between
the tablets and the head, the on-tablet WiFi watchdog, the battery governor and a control tool. Tolkien itself is
`tablet-foundry/`; its compose file is `examples/tablet-head/`.

## What you need

- Tablets that take a generic system image (GSI) with a `-userdebug` build, so `adb root` works. The flash procedure
  here is written for the Lenovo TB-X606 family; the other tools need only adb and Termux.
- A machine with adb (Android platform-tools) that reaches the tablets over WiFi. The re-armer and the battery
  governor are Windows scheduled tasks on it; the other tools are Python and bash (Git Bash on Windows).
- A Linux tablet head the tablets plug into over USB. It bridges their USB network interfaces onto one segment and
  runs Tolkien.
- Termux and Termux:Boot APKs, signed with the same key: take both from F-Droid. Put them in `payload/apk/` as
  `com.termux.apk` and `com.termux.boot.apk`.

## Settings

The tools read the lab's settings file, `etc/lab.env` at the repository root: copy `etc/lab.env.example` (it lists
every key) to `etc/lab.env` and fill in what you use. Or point `LAB_SETTINGS_FILE` at another file; the environment
wins over the file. The Fellowship's own keys:

| Setting | What it is |
|---|---|
| `FELLOWSHIP_ROSTER` | The roster file: every tablet's address, adb serial, USB-segment octet, class and charge switch. `roster.example.json` shows its shape. |
| `FELLOWSHIP_USB_SEGMENT` | The USB segment's first three octets (default `10.55.0`); the head is `.1`. |
| `ADB_EXE` | The adb binary (default `adb` on the path). |
| `TABLET_HEAD_NAME` | The tablet head's ssh name. |
| `CODE_MIRRORS` | Its `tolkien` entry is the folder holding `tablet-foundry/` (the provisioner copies Tolkien's on-tablet agent from it). |

## Building a node

1. Make the cluster's ssh key and the fellows' shared token, once:

   ```bash
   ssh-keygen -t ed25519 -N "" -f cluster/tabcluster_ed25519
   python -c "import secrets; print(secrets.token_hex(32))" > cluster/fellow.token
   ```

   Both stay private: the key reaches every tablet, and Tolkien uses both.
2. Flash the tablet: `flash/gsi_flash.sh <serial> <gsi.img>` (its header is the procedure), then
   `flash/bootcheck.sh <serial>` until it reports BOOTED. The first boot takes 5 to 10 minutes.
3. Connect the tablet to WiFi on its screen, plug it into USB, and provision it:
   `flash/provision_node.sh <adb-serial> <name>`. It installs Termux and Termux:Boot, sets adb to listen on port 5555
   at every boot, takes the wake lock, installs the packages, installs the boot scripts and the agent, starts the
   WiFi watchdog and verifies each stage. It ends with the two roster lines to add.
4. Add the tablet to the roster file and to Tolkien's settings, `examples/tablet-head/tolkien.env` (your copy of
   `tolkien.env.example`): its `TABLET_NET` entry, and its `TABLET_CLASSES` and `TABLET_WIRE_OCTETS` entries if you use
   them. Then recreate Tolkien's containers.
5. Bring the USB segment up: `python usbnet_up.py --apply`. It configures each tablet's USB network over WiFi adb,
   then runs `fellowship_bridge.sh` as root on the tablet head through `run_remote.py` (in the tool folder), and
   proves every tablet answers on the segment. Run it again after any reboot of a tablet or of the head.
6. Install the head's USB reach probe, which Tolkien's watchdog uses before it calls the fleet unreachable:
   `python run_remote.py <tablet head> fellowship/install_usb_reach_probe.sh 120 --args <src>:<dest>`, with the
   probe's path as the head sees it and where the watchdog key's forced command should run it. It prints the
   authorized_keys line for that key.

## Keeping them running

- `cluster/provision_watchdog.sh <tablet> ...` installs or updates the on-tablet WiFi watchdog (report mode).
- `cluster/install_rearm_task.ps1` installs the re-armer task: it restarts a tablet's watchdog when Tolkien reports
  it stopped, and reboots a tablet whose Termux app has died.
- `register_battery_governor_task.ps1` installs the battery governor task: it holds each tablet's charge between 50
  and 70 percent, since the tablets stay plugged in.
- `python fellowship_ctl.py status` shows every tablet; `reboot`, `usb-restore` and `boot-recover` take a tablet,
  a class group (`class-a`, `class-b`, `class-c`) or `all`.
- `rigby/install_rigby_host_units.sh` installs the tablet head's own services: the bridge daemon that keeps every
  tablet's USB interface in `br0` (it takes `.1` on `FELLOWSHIP_USB_SEGMENT`), the HTTP model store the tablets
  stage models from, and the share mounter Docker waits for (with Docker's drop-in that orders it after the mounter). Run it as root on the tablet head with this folder copied
  there: `install_rigby_host_units.sh <folder>:<models user>:<models port>:<models dir>:<share>[,<share>...]`. It
  installs only what changed and restarts those services; all three are safe to restart.
