"""Wake-on-LAN — delegates to the host-network sidecar.

Bridge containers (arrnet 172.18.x) can't broadcast onto the physical LAN, so the actual magic
packet is sent by the `wol-sidecar` container (network_mode: host). This just calls it.
"""
import httpx
import config


def wake(mac=None, broadcasts=None, port=None):  # broadcasts/port kept for signature compat
    """Wake a NIC whose MAC is configured. No MAC configured -> nothing to wake, no call made."""
    mac = mac or config.device_mac(config.LEAD_BOX)
    if not mac:
        return []
    try:
        r = httpx.get(f"{config.WOL_SIDECAR}/wake", params={"mac": mac}, timeout=5)
        return r.json().get("sent_to", [])
    except Exception:
        return []
