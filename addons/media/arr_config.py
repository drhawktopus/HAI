"""The media add-on's settings: where the user's Sonarr, Radarr, Lidarr, Prowlarr, Seerr and qBittorrent answer, and
their keys. Every key is read from the environment or a mounted secret file (config._secret_value), never written here.
Part of the media add-on (arr_tools, tools_ext_media); the head reads none of these."""
import os

from config import _secret_value

ARR_TIMEOUT = float(os.getenv("ARR_TIMEOUT", "15"))
SONARR_URL = os.getenv("SONARR_URL", "http://sonarr:8989").rstrip("/")
RADARR_URL = os.getenv("RADARR_URL", "http://radarr:7878").rstrip("/")
LIDARR_URL = os.getenv("LIDARR_URL", "http://lidarr:8686").rstrip("/")
PROWLARR_URL = os.getenv("PROWLARR_URL", "http://prowlarr:9696").rstrip("/")
SONARR_APIKEY = _secret_value("SONARR_APIKEY", "SONARR_APIKEY_FILE")
RADARR_APIKEY = _secret_value("RADARR_APIKEY", "RADARR_APIKEY_FILE")
LIDARR_APIKEY = _secret_value("LIDARR_APIKEY", "LIDARR_APIKEY_FILE")
PROWLARR_APIKEY = _secret_value("PROWLARR_APIKEY", "PROWLARR_APIKEY_FILE")

# Seerr: media requests. In a compose stack it is often reachable by its service key (here `overseerr`). Reads are
# open; approve, decline and add are admin-only and confirmed.
SEERR_URL = os.getenv("SEERR_URL", "http://overseerr:5055").rstrip("/")
SEERR_APIKEY = _secret_value("SEERR_APIKEY", "SEERR_APIKEY_FILE")
SEERR_TIMEOUT = float(os.getenv("SEERR_TIMEOUT", "15"))

# qBittorrent's WebUI (the password from a secret file, QBIT_PASS_FILE).
QBIT_URL = os.getenv("QBIT_URL", "http://qbittorrent:8080")
QBIT_USER = os.getenv("QBIT_USER", "admin")
QBIT_PASS = _secret_value("QBIT_PASS", "QBIT_PASS_FILE")
QBIT_TIMEOUT = float(os.getenv("QBIT_TIMEOUT", "15"))

# A Recyclarr sync runs against the *arrs through the hostops broker, so it gets its own budget.
RECYCLARR_SYNC_TIMEOUT = float(os.getenv("RECYCLARR_SYNC_TIMEOUT", "200"))
