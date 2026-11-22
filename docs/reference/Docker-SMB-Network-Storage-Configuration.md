# SundaeStackarr - SMB Network Storage Configuration



## Definition and Mount Points
SMB network shares are configured via `/etc/fstab`. All entries utilize the `_netdev,nofail` options.

| Hostpath (/etc/fstab) | Remote Share |
|-----------------------|---------------|
| `/mnt/media`          | `\\<softserve-address>\Media` (Z:\Media) |
| `/mnt/ssdshared`      | `\\<softserve-address>\SSDShared` (E:\SSDShared) |

## Credentials Management
The remote share access is governed by credentials stored in `/etc/smb-credentials`. This file must be owned by root and set to mode 600.

### Known quirks

**Bridge containers → host-networked services:** `172.18.0.1` (Docker bridge gateway) reaches host-published ports such as qBittorrent's `:8080`. This capability is historical; it was originally a hard requirement under the old host-VPN setup and now simply continues to work but is not mandatory.

**Pi-hole config location:** The configuration must reside on local disk (`/opt/arrstack/pihole/etc-pihole/`). Placing it elsewhere (e.g., `/mnt/ssdshared`) breaks SQLite WAL locking over CIFS/SMB. Do not move the directory to a network mount.

**`apt`:** Plain `sudo apt install` works for package management on this system, as the host VPN has been removed and no longer requires the legacy `apt-vpn` DNS workaround.

**Seerr service identity:** The Compose file key is `overseerr`, and the container name is `seerr`. External services (Caddy/Requestrr) reach it on port 5055 at host header `overseerr`.
