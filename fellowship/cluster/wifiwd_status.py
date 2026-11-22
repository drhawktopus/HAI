"""Print Tolkien's WiFi watchdog verdicts as JSON, with the tablets they cover. Runs INSIDE the tablet-foundry container;
the re-armer (rearm_wifi_watchdogs.ps1) pipes this file in over ssh:

    ssh <tablet head> "docker exec -i tablet-foundry python3 -" < wifiwd_status.py

Read-only, and it computes nothing of its own: liveness is tablet_foundry.wifi_watchdog_status(), so the re-armer and
Tolkien cannot disagree about what "down" means. "_nodes" maps every tablet in Tolkien's TABLET_NET to its first
address; the re-armer leaves out the ones on the USB segment, which have no network adb door.
"""
import json
import sys

sys.path.insert(0, "/app")
import tablet_foundry as tf  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
status = tf.wifi_watchdog_status()
status["_nodes"] = {host: net[0] for host, net in tf.TABLET_NET.items()}
print(json.dumps(status))
