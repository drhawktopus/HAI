# Media add-on

This add-on lets HAI work with a media stack you already run: Sonarr, Radarr, Lidarr, Prowlarr, Seerr (Overseerr or
Jellyseerr) and qBittorrent. HAI does not install or package any of them. It talks to your own instances over their
APIs, the same way you would from their web pages.

## What HAI can do with it

- Read: list downloads and their progress, look up series, movies and artists, show pending requests, list quality
  profiles and custom formats, and check why something has not downloaded yet.
- Change: start a search, pick a specific release, import a finished download, pause, resume or remove a download,
  approve, decline or add a request, add a custom format or change its score, and run a Recyclarr sync.

Reading is open to every user. Every change is admin-only, and HAI shows a preview first and waits for the admin to
confirm it before anything happens. A Recyclarr sync is a dry run unless you ask for the real one.

## Installing it

Copy the files in this folder beside the head's own files, at the same relative paths, and rebuild the head. The head
loads any `tools_ext_*.py` it finds next to its tools, so the media tools appear once the files are there. A head
without these files simply has no media tools. The Recyclarr sync runs through the head's hostops sidecar, so that
route needs the sidecar running.

## Settings

Each address is a setting in the head's environment, and each key or password comes from the environment or, better,
from a secret file mounted into the container (set the `_FILE` form to the file's path).

| Setting | Default | What it is |
|---|---|---|
| `SONARR_URL` / `SONARR_APIKEY` (`SONARR_APIKEY_FILE`) | `http://sonarr:8989` | Sonarr's address and API key |
| `RADARR_URL` / `RADARR_APIKEY` (`RADARR_APIKEY_FILE`) | `http://radarr:7878` | Radarr's address and API key |
| `LIDARR_URL` / `LIDARR_APIKEY` (`LIDARR_APIKEY_FILE`) | `http://lidarr:8686` | Lidarr's address and API key |
| `PROWLARR_URL` / `PROWLARR_APIKEY` (`PROWLARR_APIKEY_FILE`) | `http://prowlarr:9696` | Prowlarr's address and API key |
| `SEERR_URL` / `SEERR_APIKEY` (`SEERR_APIKEY_FILE`) | `http://overseerr:5055` | Seerr's address and API key |
| `QBIT_URL` / `QBIT_USER` / `QBIT_PASS` (`QBIT_PASS_FILE`) | `http://qbittorrent:8080`, `admin` | qBittorrent's WebUI address, user name and password |
| `ARR_TIMEOUT`, `SEERR_TIMEOUT`, `QBIT_TIMEOUT` | `15` | Seconds to wait for each service |
| `RECYCLARR_SYNC_TIMEOUT` | `200` | Seconds to wait for a Recyclarr sync |

The defaults are the usual Docker Compose service names, so if HAI runs in the same compose stack as your services,
most of them work as they are. If qBittorrent shares another container's network, for example a VPN container with
`network_mode: service:<vpn>`, its WebUI answers at that container's name, so set `QBIT_URL` to
`http://<vpn container>:8080`. My own stack works that way.
