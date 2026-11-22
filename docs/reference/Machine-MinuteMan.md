# MinuteMan — Raspberry Pi peer Pi-hole resolver, BLE gateway, DDNS updater

Thin orientation hub for the **MinuteMan** machine: identity, what runs here, and where the depth
lives. Facts are single-homed in the linked pages; the canonical inventory row is in
[[Network-Homelab-Machine-Inventory-Virtualization]].

## Identity

- **Role:** Physical Raspberry Pi 3B+ — the LAN's peer Pi-hole resolver, a Bluetooth-LE gateway, and
  the dynamic-DNS updater.
- **LAN IP:** <minuteman-address> · **Tailscale:** <tailscale-address>
- **OS:** Raspberry Pi OS

## What runs here

- **Peer Pi-hole resolver** — the second on-LAN DNS resolver alongside SundaeStackarr's master → [[Network-Local-Network-Services-DHCP-DNS-Architecture]]
- **bt-proxy — Home Assistant's Bluetooth radio** (ESPHome-compatible proxy, port 6053; HA's Govee
  purifier control and `govee_ble` sensors run through it, since 2026-10-03) → [[RUNBOOK-DO-Pihole-and-MinuteMan-Services]]
- `theengs-gateway.service` — installed, **disabled since 2026-10-03** (bt-proxy + HA `govee_ble` replaced it; its scan churn was the bluetoothd leak) → [[RUNBOOK-DO-Pihole-and-MinuteMan-Services]]
- **bt-mem-watch** — bluetoothd memory watchdog timer (the 2026-10-03 OOM guard) → [[RUNBOOK-DO-Pihole-and-MinuteMan-Services]]
- **NUT server** for the upstairs Eaton UPS (3493) → HomeAssistant-Energy
- **Glances** (61208, Homepage widget) · **Tailscale** · **DDNS updater** → [[Network-External-Connectivity-Remote-Access]]
- Boots to the CLI (`multi-user.target`) since 2026-10-03; Raspberry Pi Connect off, Docker disabled
  (packages left installed). 906 MB RAM, 512 MB SD swap — RAM is the constraint.

## See also

- [[Network-Homelab-Machine-Inventory-Virtualization]] · [[RUNBOOK-DO-Pihole-and-MinuteMan-Services]]
