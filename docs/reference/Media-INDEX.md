# SundaeStackarr Media Stack

This domain documents the Dockerized media-server infrastructure: ingestion pipelines for torrents
and YouTube content, network-storage configuration, and music integration. It is the technical
reference for the Arr stack, the qBittorrent/VPN architecture, and the Discord bot services running
on the local Ubuntu VM.

## Common questions

- How do I update the Docker stacks or check container status? → see [[Docker-Infrastructure-Inventory-and-Operations]]
- Where does the VPN run now that host-level ExpressVPN is retired? → see [[Docker-qBittorrent-and-VPN-Architecture]]
- How are SMB shares mounted and configured on the host? → see [[Docker-SMB-Network-Storage-Configuration]]
- How do I fix "Sign in to confirm you're not a bot" errors in the YouTube pipeline? → see YouTube-Troubleshooting-Credentials
- Why isn't Sonarr importing YouTube episodes automatically? → see [[YouTube-Auto-Import-Pinchflat-Integration]]
- How does the system ensure correct series matching for YouTube content? → see [[YouTube-TVDB-ID-Threading-Reindexing]]
- What prevents the pipeline from grabbing unrelated content during searches? → see [[YouTube-YouTube-Originals-Gate]]

## Pages in this domain

- **[[Docker-Infrastructure-Inventory-and-Operations]]** — Ubuntu VM host, IP addressing, and general inventory of the media Docker stack.
- **[[Docker-qBittorrent-and-VPN-Architecture]]** — Migration of ExpressVPN entirely into Docker containers; host-level service retired.
- **[[Docker-SMB-Network-Storage-Configuration]]** — SMB network shares via `/etc/fstab` using `_netdev,nofail`.
- **[[Docker-JukeBot-Discord-Music-Integration]]** — Discord music bot backed by the local Lidarr/Plex music library.
- **[[Docker-YouTube-Content-Ingestion-Pipeline]]** — YouTube ingestion via the `yt-torznab` and `Pinchflat` containers.
- **[[YouTube-Overview-Configuration]]** — Overview and configuration of the YouTube Torznab pipeline.
- **[[YouTube-TVDB-ID-Threading-Reindexing]]** — How TVDB IDs flow through Sonarr requests for accurate series matching.
- **[[YouTube-Auto-Import-Pinchflat-Integration]]** — The 120-second background import thread, the primary import mechanism.
- **[[YouTube-YouTube-Originals-Gate]]** — Adapter logic that restricts tvsearch responses to genuine YouTube shows.
- **YouTube-Troubleshooting-Credentials** — Troubleshooting expired YouTube cookies and bot-verification errors.
