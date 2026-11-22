"""
Kirk Watchdog — off-box health monitor and rollback executor.

Runs on a separate machine as a scheduled-tick task. During a declared self-change
window it may roll Kirk back to a known-good Docker image; outside a window it only
ever reports. Stdlib-only; imports nothing from this repository.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Window file I/O
# ---------------------------------------------------------------------------

def read_window(path):
    """Read the self-change window file.

    Returns:
        dict      — valid JSON with expected keys
        None      — file does not exist (no open window)
        "UNREADABLE" — file exists but cannot be parsed / I/O error
    """
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return "UNREADABLE"


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------

def probe_health(url, timeout_s=5):
    """Probe Kirk's /health endpoint.

    Returns True only when HTTP 200 AND JSON body contains {"ok": true}.
    Any exception (network, TLS, timeout, bad JSON) returns False.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status != 200:
                return False
            body = json.loads(resp.read())
            return body.get("ok") is True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Core decision logic (PURE — no I/O)
# ---------------------------------------------------------------------------

def decide(window, probe_ok, now_epoch, state):
    """Decide what actions to take this tick.

    Args:
        window:     dict (parsed window), None (no file), or "UNREADABLE"
        probe_ok:   bool from probe_health
        now_epoch:  float, current time.time()
        state:      json-safe dict persisted between ticks

    Returns:
        (actions, new_state)
        actions = list of {"kind": str, "why": str}
    """
    actions = [{"kind": "heartbeat", "why": "tick"}]

    # --- helpers to inspect window state ---
    def _is_open_window():
        return (isinstance(window, dict)
                and window.get("open") is True)

    def _deadline_missed():
        if not _is_open_window():
            return False
        try:
            return now_epoch > float(window["deadline_epoch"])
        except (KeyError, ValueError, TypeError):
            return False

    # --- maintain_tag: ONLY when no window AND healthy -------------------
    if window is None and probe_ok:
        actions.append({"kind": "maintain_tag", "why": "healthy outside window — refresh known-good"})

    # --- consecutive-miss counter (only tracks dead probes) --------------
    if probe_ok:
        state["consec_misses"] = 0
    else:
        state.setdefault("consec_misses", 0)
        state["consec_misses"] += 1

    # --- report: 3rd consecutive miss (no window) ------------------------
    if (not _is_open_window()
            and not probe_ok
            and state["consec_misses"] == 3):
        actions.append({
            "kind": "report",
            "why": "probe dead 3 consecutive ticks — no window open, cannot act",
        })

    # --- report: UNREADABLE window ---------------------------------------
    if window == "UNREADABLE":
        actions.append({
            "kind": "report",
            "why": "window file unreadable — status INCONCLUSIVE, not acting",
        })

    # --- report: forgot-to-close (once per occurrence) -------------------
    if (_is_open_window()
            and _deadline_missed()
            and probe_ok):
        key = "forgot_report_" + window.get("opened_at", "")
        if not state.get(key):
            actions.append({
                "kind": "report",
                "why": "deployer forgot to close the window — past deadline but healthy",
            })
            state[key] = True

    # --- rollback: THE ONLY ACT -----------------------------------------
    if (_is_open_window()
            and _deadline_missed()
            and not probe_ok):
        opened_at = window.get("opened_at")
        rb_key = "rolled_back_" + str(opened_at) if opened_at else "rolled_back_any"
        if not state.get(rb_key):
            actions.append({
                "kind": "rollback",
                "why": (f"window open, deadline missed ({now_epoch:.0f} > {window.get('deadline_epoch')}), "
                        f"probe dead — rolling back to known-good"),
            })
            state[rb_key] = True

    return actions, state


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

def read_env_file(path):
    """KEY=VALUE lines of a settings or credentials file; {} when it cannot be read."""
    out = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key and not key.startswith("#"):
                    out[key.strip()] = value.strip()
    except OSError:
        pass
    return out


def send_report(why, etc_dir, urlopen=urllib.request.urlopen):
    """Send one report through the lab's message relay, the way the factory's notify() does: the relay's address from
    <etc>/lab.env (OVERNIGHT_DM_URL, OVERNIGHT_DM_HOST), its token and the admin's id from <etc>/dm_relay.env.
    Returns "sent" or the reason it was not; never raises (the report is in the tick's log either way)."""
    settings = read_env_file(os.path.join(etc_dir, "lab.env"))
    creds = read_env_file(os.path.join(etc_dir, "dm_relay.env"))
    url, host = settings.get("OVERNIGHT_DM_URL", ""), settings.get("OVERNIGHT_DM_HOST", "")
    token, user_id = creds.get("OVERNIGHT_DM_TOKEN", ""), creds.get("OVERNIGHT_DM_USER_ID", "")
    if not (url and host and token and user_id):
        return "not sent: the relay is not configured in %s" % etc_dir
    req = urllib.request.Request(
        url, data=json.dumps({"user_id": user_id, "message": "[kirk-watchdog] " + why}).encode(),
        headers={"Content-Type": "application/json", "X-DM-Token": token, "Host": host})
    try:
        with urlopen(req, timeout=10) as resp:
            return "sent" if resp.status == 200 else "not sent: relay answered %s" % resp.status
    except Exception as exc:
        return "not sent: %s" % type(exc).__name__


def ssh_prefix(key, target):
    """ssh to the head with the watchdog's own key, never prompting (a prompt would wedge the scheduled task)."""
    return ["ssh", "-i", key, "-o", "BatchMode=yes", target]


def image_name(stack_dir):
    """The orchestrator's image as docker compose names it: <project>-orchestrator, the project being the stack
    folder's name."""
    return stack_dir.rstrip("/").rsplit("/", 1)[-1] + "-orchestrator"


def build_rollback_cmds(ssh, stack_dir):
    """Two SSH commands that roll back to the known-good image.

    1. Tag known-good → latest
    2. Recompose the orchestrator service with --no-build.

    --no-build is LOAD-BEARING: without it docker compose rebuilds from the exact
    broken source tree the rollback exists to escape, defeating the purpose entirely.
    """
    img = image_name(stack_dir)
    tag_cmd = ssh + [
        "docker tag %s:known-good %s:latest" % (img, img)
    ]
    restart_cmd = ssh + [
        "cd %s && docker compose up -d --no-deps --no-build orchestrator" % stack_dir
    ]
    return [tag_cmd, restart_cmd]


def build_tag_cmd(ssh, stack_dir):
    """One SSH command that snapshots the current running image as known-good.

    Used by maintain_tag to update the safety net after successful deploys.
    """
    return ssh + [
        "docker tag $(docker inspect -f '{{.Image}}' orchestrator) %s:known-good" % image_name(stack_dir)
    ]


# ---------------------------------------------------------------------------
# CLI entry point (one tick; scheduler loops externally)
# ---------------------------------------------------------------------------

def main(argv=None):
    """Execute one watchdog tick.

    Returns 0 on success, 1 on unexpected error. Never dies silently.
    """
    parser = argparse.ArgumentParser(description="Kirk Watchdog — single tick")
    parser.add_argument("--state", required=True, help="Path to JSON state file")
    parser.add_argument("--window", required=True, help="Path to window JSON file")
    parser.add_argument("--url", required=True, help="Health-check URL")
    parser.add_argument("--ssh-key", required=True, help="The watchdog's own key for the head")
    parser.add_argument("--ssh-target", required=True, help="user@host the watchdog reaches the head as")
    parser.add_argument("--stack-dir", required=True, help="The head's compose stack folder (e.g. /opt/stack)")
    parser.add_argument("--relay-etc", default="", help="The folder holding the message relay's lab.env and "
                        "dm_relay.env (this box's factory etc); empty = reports are only printed")
    args = parser.parse_args(argv)
    ssh = ssh_prefix(args.ssh_key, args.ssh_target)

    try:
        # Load persisted state (create fresh on first run)
        try:
            with open(args.state, "r") as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}

        # Observe
        window = read_window(args.window)
        probe_ok = probe_health(args.url)
        now_epoch = time.time()

        # Decide
        actions, new_state = decide(window, probe_ok, now_epoch, state)

        # Execute
        for action in actions:
            kind = action["kind"]
            if kind == "rollback":
                for cmd in build_rollback_cmds(ssh, args.stack_dir):
                    subprocess.run(cmd, check=True)
            elif kind == "maintain_tag":
                cmd = build_tag_cmd(ssh, args.stack_dir)
                subprocess.run(cmd, check=True)
            elif kind == "report":
                sent = send_report(action["why"], args.relay_etc) if args.relay_etc else "not sent: no relay given"
                print(f"DM: {action['why']} ({sent})")
            # heartbeat is informational only

        # Save state
        with open(args.state, "w") as f:
            json.dump(new_state, f, indent=2)

        return 0

    except Exception as exc:
        print(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
