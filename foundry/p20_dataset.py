r"""Dataset Runner: acquires and surveys licensed training corpora.

Runs under the system Python (3.14) to use pyarrow/PIL without polluting
the ai-toolkit venv with non-training dependencies.

Modes:
  survey  — Fetch manifest shards; report composition (sources, licenses,
            resolution, captions). No image downloads.
  acquire — Read manifests, apply filters, and download images/captions to
            the standard layout: train/<prefix>_NNN.jpg + .txt + _manifest.txt.

State files are written to <FOUNDRY_DATASET_ROOT>\_jobs\<name>.state.json. This path
is critical: placing state under the foundry's own jobs folder would cause the render worker
to glob it as a live training run, blocking GPU renders. Dataset work is
CPU-only and must never appear as a GPU job.
"""
import base64
import concurrent.futures
import datetime
import glob
import hashlib
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse

try:
    import foundry_paths as P        # the workspace's ONE settings reader
except ImportError:
    P = None


def _setting(name):
    """A dataset setting: the foundry's reader when this runs beside it, else the environment (same names)."""
    return (P.setting(name, "") if P else os.environ.get(name, "")) or ""


# where dataset jobs and sets live: FOUNDRY_DATASET_ROOT (a setting); empty = no dataset work on this box
JOBS_DIR = os.path.join(_setting("FOUNDRY_DATASET_ROOT"), "_jobs") if _setting("FOUNDRY_DATASET_ROOT") else ""
KEEPAWAKE_HOLD = P.KEEPAWAKE_HOLD if P else os.path.join(os.environ.get("ProgramData") or r"C:\ProgramData", "HAI", "keep-awake.hold")

# Host-specific paths are configured via dataset-paths.json.
# See docstring of _load_paths() for config schema.
PATHS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset-paths.json")


def _load_paths():
    """Load local host profile; fall back to defaults if file is absent.

    Use `utf-8-sig` to handle BOMs from Windows editors. Fail fatally on parse errors:
    fallback would apply wrong drive letters, risking data written to incorrect disks.
    Absent file means no profile intended; broken file means configuration error."""
    try:
        with open(PATHS_FILE, encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise SystemExit("FATAL: %s exists but could not be read (%s). Refusing to fall back to "
                         "another host's paths — fix or delete it." % (PATHS_FILE, exc))


_PATHS = _load_paths()
JOBS_DIR = _PATHS.get("jobs_dir") or JOBS_DIR

SCRATCH_ROOT = _PATHS.get("scratch_root") or _setting("FOUNDRY_DATASET_SCRATCH")
# Where completed originals are drained to. On Softserve this is the array as a LOCAL path, which
# is the whole reason acquisition belongs there: no SMB share, no credentials, no elevation.
ARCHIVE_ROOT = _PATHS.get("archive_root") or ""
# Stage on SSD to avoid array seek thrash during concurrent downloads,
# then drain sequentially. The cache is bounded so the full corpus never
# fits in memory/scratch.
STAGE_HIGH_WATER_GB = float(_PATHS.get("stage_high_water_gb") or 50)
# F: is also the training working set. Filling it would break a run that has nothing to do with this
# job, so refuse to start rather than discover it at 90%.
MIN_SCRATCH_FREE_GB = float(_PATHS.get("min_scratch_free_gb") or 10)

# Institution names in this data are not ASCII ("Bohuslän Museum", "Nationalmuseum Sverige"). A
# Windows console defaults to cp1252, where printing one of those raises UnicodeEncodeError and kills
# the run — the house `open()`/cp1252 gotcha, in its stdout form. Never let a museum's name end a job.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Hard ceilings. A spec is authored by a language model; these are what stop a plausible-looking
# number from turning into a week of downloading.
MAX_IMAGES = 5000
MAX_SHARDS = 125
DL_THREADS = 8
# Bulk FILE transfers (parquet shards, images): a read timeout between 1 MB chunks, so 60s is the
# "this transfer has died" threshold, not a per-request budget.
HTTP_TIMEOUT = 60
# Metadata requests use short timeouts to avoid blocking workers on stale keep-alive sockets.
API_CONNECT_TIMEOUT = 5
API_READ_TIMEOUT = 15
# Politeness floor for image downloads if a source lacks its own rate limit.
# Fail CLOSED: a missing rate yields this default, not unlimited requests.
DEFAULT_IMAGE_RATE_PER_SEC = 6

# Scoring concurrency constraints:
#
# 1. SERIALIZE per GPU, PARALLELIZE across fleet: one request in flight per card.
#    Partially-offloaded models thrash their KV cache with >1 concurrent request.
#
# 2. Mothership exception: allows 2 workers. qwen3.6:27b is fully resident
#    (16.5 GB / 24 GB), so it handles overlap without eviction.
#
# 3. Softserve yield_to_plex: scoring may use this GPU but cannot be primary;
#    must yield immediately if Plex requests access.
#
# Addresses come from the ONE fleet table (foundry_paths.fleet_hosts); the model and worker
# count are this module's policy. A box with no fleet table has no default fleet.
_OLLAMA_PORT = P.setting("OLLAMA_PORT", "11434") if P else "11434"


def _vlm_endpoint(label, model, workers, **extra):
    """One fleet entry, or None when that box is not in the fleet table."""
    addr = (P.fleet_hosts().get(label) if P else "") or ""
    if not addr:
        return None
    ep = {"label": label, "host": "http://%s:%s" % (addr, _OLLAMA_PORT),
          "model": model, "workers": workers}
    ep.update(extra)
    return ep


DEFAULT_VLM_FLEET = [e for e in [_vlm_endpoint("mothership", "qwen3.6:27b", 2)] if e]

# Excluded from DEFAULT_VLM_FLEET because the 8B model is slower and unreliable at JSON output
# compared to the 27B. Mixing judges with inconsistent formats breaks auditability.
#
# Prior performance measurements were invalidated by CPU offload artifacts and driver states;
# re-evaluate only after verifying VRAM residency (`size_vram == size`) and GPU health.
#
# Kept here so 8GB-capable endpoints can be opted into explicitly via spec["vlm_fleet"].
# Each entry's box comes from the fleet table (a setting); nothing here names an address.
SPARE_VLM_ENDPOINTS = [e for e in [
    _vlm_endpoint("bighonker", "qwen3-vl:8b", 1),
    # Softserve additionally yields: it is the Plex host, so scoring may use that card but can never
    # be primary on it and must leave gracefully the moment Plex wants it back.
    _vlm_endpoint("softserve", "qwen3-vl:8b", 1, yield_to_plex=True),
] if e]

# Builtin fallbacks used if dataset-sources.json is missing/unreadable (see load_sources).
_BUILTIN_SOURCES = {
    "pd12m": {
        "kind": "parquet_shards",
        "dims_known": True,
        "shard_url": ("https://huggingface.co/datasets/Spawning/PD12M/resolve/main/"
                      "metadata/pd12m.%03d.parquet"),
        "shards": 125,
        "columns": ["id", "url", "caption", "width", "height", "mime_type", "license", "source"],
        "rate_per_sec": 25,
        "image_rate_per_sec": 40,
        "dl_threads": 8,
        "selectors": ["sources", "licenses", "caption_any", "caption_none", "min_short_side"],
        "note": ("Public Domain 12M — 12.5M rows, CC0-1.0 + PD-Mark only. Manifest-only: images "
                 "are fetched from pd12m.s3.us-west-2.amazonaws.com, no auth. Selection is on "
                 "SYNTHETIC CAPTION TEXT, so it is broad but imprecise."),
    },
    "met": {
        "kind": "met_api",
        # The object endpoint does NOT return pixel dimensions, so min_short_side cannot be applied
        # at manifest time; acquire() enforces it after decoding instead. That is more accurate
        # anyway — it measures the file we actually got rather than a metadata field.
        "dims_known": False,
        "shards": 1,
        "search": "https://collectionapi.metmuseum.org/public/collection/v1/search",
        "object": "https://collectionapi.metmuseum.org/public/collection/v1/objects/%s",
        "departments_url": "https://collectionapi.metmuseum.org/public/collection/v1/departments",
        # Rate limit enforced to avoid API throttling; failures are silently dropped,
        "rate_per_sec": 10,
        # Stall controls, per-source DATA for the same reason rate_per_sec is: the next API gets
        # tuned by editing JSON. `timeout_sec` is the read budget for ONE object request;
        # `retries` is total attempts (2 = one retry, deliberately low — a third attempt down a
        # sick connection buys nothing and costs a worker); `session_recycle` is how many requests
        # a thread's HTTP session may serve before it is torn down and redialled.
        "timeout_sec": 15,
        "retries": 2,
        "session_recycle": 100,
        # The image host lacks the JSON API's pacer; without separate throttling,
        # high concurrency triggers 403s on the shared edge.
        "image_rate_per_sec": 6,
        "dl_threads": 6,
        "selectors": ["tags", "departments", "medium_any", "caption_any", "caption_none",
                      "min_short_side"],
        "note": ("The Metropolitan Museum of Art Open Access — ~490k objects, CC0, full-resolution "
                 "images. Selected by CURATORIAL subject tags, department and medium rather than a "
                 "caption model's description of the picture, which is the whole reason to prefer "
                 "it for a specific subject. Departments are matched by NAME (fetched live from the "
                 "museum, not hardcoded); the object endpoint does not return departmentId at all."),
    },
    "nga": {
        # NGA has no subject tags; `title_any` is the only subject lever.
        # Substring matching on titles causes false positives (e.g., "Apollo" matches
        # "Apollonia"); pair with classification and expect curation.
        "kind": "bulk_table",
        "label": "National Gallery of Art",
        "dims_known": False,
        "shards": 1,
        "table_url": ("https://raw.githubusercontent.com/NationalGalleryOfArt/opendata/main/"
                      "data/objects.csv"),
        "table_format": "csv",
        "table_max_age_days": 30,
        "table_min_bytes": 40 * 1024 * 1024,
        "columns": {
            "id": "objectid",
            "title": "title",
            "medium": "medium",
            "classification": "classification",
            "artist": "attribution",
            # These two arrive from the JOIN below, not from objects.csv.
            "public_domain": "openaccess",
            "image_url": "iiifurl",
        },
        "public_domain_true": ["true", "1", "yes"],
        # objects.csv has no image URL and no rights flag; published_images.csv has both, keyed
        # back to objectid. Without this join NGA is unusable.
        "join": {
            "table_url": ("https://raw.githubusercontent.com/NationalGalleryOfArt/opendata/main/"
                          "data/published_images.csv"),
            "table_min_bytes": 40 * 1024 * 1024,
            "left_key": "objectid",
            "right_key": "depictstmsobjectid",
            "columns": {"iiifurl": "iiifurl", "openaccess": "openaccess",
                        "width": "width", "height": "height"},
            # Several views per object (primary, verso, details) — keep the primary one, or a
            # training set quietly fills with backs of canvases and frame detail shots.
            "prefer_column": "viewtype",
            "prefer_value": "primary",
        },
        "image_url_template": "%s/full/!1600,1600/0/default.jpg",
        "rate_per_sec": 6,
        "timeout_sec": 20,
        "retries": 2,
        "session_recycle": 100,
        "image_rate_per_sec": 6,
        "dl_threads": 6,
        "license_note": "NGA Open Access (public domain)",
        "selectors": ["title_any", "title_none", "classification_any", "classification_none",
                      "medium_any", "caption_any", "caption_none", "min_short_side"],
        "note": ("The National Gallery of Art's open-data catalogue (~145k objects) filtered "
                 "locally, joined to its published-images table for IIIF URLs and the openaccess "
                 "flag. NO subject tags — select with title_any plus classification. Images come "
                 "straight from the catalogue, so no per-object API calls at all."),
    },
    "met_bulk": {
        # Same museum, same object cache — different SELECTION model. `met` asks the search engine
        # for terms you supply; `met_bulk` filters the published catalogue, so coverage stops
        # depending on whether you guessed the right words.
        "kind": "bulk_table",
        "label": "The Metropolitan Museum of Art",
        "dims_known": False,
        "shards": 1,
        "table_url": ("https://media.githubusercontent.com/media/metmuseum/openaccess/master/"
                      "MetObjects.csv"),
        "table_format": "csv",
        "table_max_age_days": 30,
        "table_min_bytes": 100 * 1024 * 1024,
        # The Met's own column spellings. Another museum's entry names ITS columns here and needs
        # no code at all. `image_url` is absent because the Met does not publish it in the
        # catalogue, so the adapter falls back to resolving winners via the object endpoint.
        "columns": {
            "id": "Object ID",
            "public_domain": "Is Public Domain",
            "tags": "Tags",
            "department": "Department",
            "medium": "Medium",
            "classification": "Classification",
            "title": "Title",
            "artist": "Artist Display Name",
        },
        "public_domain_true": ["true"],
        "license_note": "CC0 (Met Open Access)",
        "object": "https://collectionapi.metmuseum.org/public/collection/v1/objects/%s",
        "departments_url": "https://collectionapi.metmuseum.org/public/collection/v1/departments",
        "rate_per_sec": 10,
        "timeout_sec": 15,
        "retries": 2,
        "session_recycle": 100,
        "image_rate_per_sec": 6,
        "dl_threads": 6,
        "selectors": ["tags", "title_any", "title_none", "departments", "medium_any",
                      "classification_any", "classification_none", "max_tags", "min_tags",
                      "caption_any", "caption_none", "min_short_side"],
        "note": ("The Met's ENTIRE published catalogue (~490k objects, one ~318 MB CSV, cached and "
                 "refreshed monthly) filtered locally — no keyword search, so nothing is missed "
                 "because a search term was not thought of. Selects on curatorial Tags, Department, "
                 "Medium and Classification. The museum's API is then touched only to resolve image "
                 "URLs for rows that already matched, which is a fraction of what `met` spends. "
                 "Prefer this for any new subject; `met` remains for quick keyword exploration."),
    },
}

SOURCES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset-sources.json")


def load_sources():
    """Load source configurations from dataset-sources.json, merging with built-ins.

    Unknown keys in the JSON are preserved to support future adapter fields.
    Adding a new source should only require editing the JSON file; do not modify
    enums or dicts in the tool schema, orchestrator, or runner.
    """
    try:
        with open(SOURCES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        merged = {k: dict(v) for k, v in _BUILTIN_SOURCES.items()}
        for name, cfg in (data or {}).items():
            if isinstance(cfg, dict):
                merged.setdefault(name, {}).update(cfg)
        return merged
    except FileNotFoundError:
        return {k: dict(v) for k, v in _BUILTIN_SOURCES.items()}
    except Exception as exc:
        print("WARN: %s unreadable (%s); using built-in sources" % (SOURCES_FILE, exc), flush=True)
        return {k: dict(v) for k, v in _BUILTIN_SOURCES.items()}


SOURCES = load_sources()


def met_departments(sess, src, cache_dir):
    """The Met's department list, FETCHED not hardcoded. Returns a list of display names.

    The museum publishes this endpoint, so baking a table of numeric IDs in here would go stale
    silently and a hand-picked subset would quietly exclude every department left off it. Worse, the
    IDs turned out to be useless anyway: the OBJECT endpoint does not return `departmentId` (it is
    None on every record), only the display name — so matching had to be by name regardless.
    Cached on the NVMe so it costs one request.
    """
    cp = os.path.join(cache_dir, "_departments.json")
    try:
        if os.path.exists(cp):
            with open(cp, encoding="utf-8") as f:
                return json.load(f)
        url = src.get("departments_url")
        data = sess.get(url, timeout=(API_CONNECT_TIMEOUT, API_READ_TIMEOUT)).json()
        out = [d["displayName"] for d in data.get("departments", []) if d.get("displayName")]
        os.makedirs(cache_dir, exist_ok=True)
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(out, f)
        return out
    except Exception:
        return []


def _dept_matches(obj_dept, wanted, known, job, warned):
    """Case-insensitive substring match of an object's department against the caller's list.

    Names, not ids — nobody should have to know that "Drawings and Prints" is 9, least of all a
    language model writing a filter, and the object endpoint does not expose the id anyway. An entry
    matching no known department is LOGGED once: a typo that silently selects nothing is exactly the
    failure this whole survey-first design exists to prevent.
    """
    if not wanted:
        return True
    d = str(obj_dept or "").lower()
    for w in wanted:
        s = str(w).strip().lower()
        if not s:
            continue
        if known and s not in warned and not any(s in k.lower() for k in known):
            warned.add(s)
            job.log("  ⚠ department %r matches no Met department; known: %s"
                    % (w, ", ".join(sorted(known))[:220]))
        if s and s in d:
            return True
    return False

# Drop common synthetic prefixes to save token budget; they convey no semantic variance.
_CAPTION_BOILERPLATE = re.compile(
    r"^\s*(?:the|this)\s+(?:image|photo|picture)\s+(?:shows|depicts|is\s+of|features)\s+",
    re.I)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Job:
    """Spec + state file. Every phase change is published immediately — a job that dies silently is
    indistinguishable from one still working, which is the failure mode status tools exist to kill."""

    def __init__(self, spec_path):
        with open(spec_path, encoding="utf-8") as f:
            self.spec = json.load(f)
        self.spec_path = spec_path
        self.name = self.spec["name"]
        self.state_path = os.path.join(JOBS_DIR, self.name + ".state.json")
        self.log_path = os.path.join(JOBS_DIR, self.name + ".log")
        self.started = _now()
        self.state = {}

    def log(self, msg):
        line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def publish(self, phase, detail="", **extra):
        self.state.update({
            "name": self.name,
            "mode": self.spec.get("mode"),
            "phase": phase,
            "detail": detail,
            "started": self.started,
            "updated": _now(),
            "requested_by": self.spec.get("requested_by"),
            "out_dir": self.spec.get("out_dir"),
        })
        self.state.update(extra)
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            self.log("state write failed: %s" % exc)


FLEET_STATE_URL = P.head_url() + "/fleet/run-state" if P.head_url() else ""
# name -> address comes from the ONE fleet table (foundry_paths.fleet_hosts()); this is the
# reverse lookup - which box an endpoint's address belongs to. Empty table = no fleet.
FLEET_HOSTS = {addr: name for name, addr in P.fleet_hosts().items()}
# The head's own box can serve an endpoint too, so its address joins the reverse map. Its
# name is a setting (HAI_BOX_NAME); with no head linked there is nothing to add.
_HEAD_HOST = P.head_url().split("//", 1)[-1].split("/")[0].split(":")[0] if P.head_url() else ""
if _HEAD_HOST:
    FLEET_HOSTS.setdefault(_HEAD_HOST, P.setting("HAI_BOX_NAME", "head"))
FLEET_HOST_DEFAULT = P.setting("P20_FLEET_HOST", "")
_fleet_last = [0.0]


def fleet_host_for(url):
    """Map an ollama endpoint to the fleet host name whose GPU it is. Unknown -> the default, so a
    new endpoint degrades to "report it somewhere" rather than reporting nothing at all."""
    try:
        netloc = urllib.parse.urlsplit(url or "").netloc or (url or "")
        return FLEET_HOSTS.get(netloc.split(":")[0].strip(), FLEET_HOST_DEFAULT)
    except Exception:
        return FLEET_HOST_DEFAULT


def fleet_publish(name, state, step=0, total=0, phase="", eta="", ttl=1800,
                  force=False, host=None):
    """Declare a GPU job to the orchestrator: POST /fleet/run-state.

    Side effects:
      1. Updates homepage card progress (seconds/img) for visibility.
      2. Claims the compute card so the scheduler knows the host is occupied.

    Throttled to ~8s cadence; fail-soft by construction (telemetry must never break the job)."""
    if not force and time.time() - _fleet_last[0] < 8.0:
        return
    _fleet_last[0] = time.time()
    try:
        body = json.dumps({"host": host or FLEET_HOST_DEFAULT, "name": name, "state": state,
                           "step": int(step),
                           "total_steps": int(total), "phase": phase, "eta": eta,
                           "ttl": int(ttl)}).encode("utf-8")
        req = urllib.request.Request(FLEET_STATE_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def _session():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = "HAI-P20-dataset/1.0 (homelab; contact via operator)"
    return s


class _SessionPool:
    """Per-thread Session with forced recycle to avoid stale pooled sockets.

        Uses `threading.local()` to isolate sessions per worker, preventing a single
        dead connection from stalling the entire pool. Sessions are recycled every
        `recycle` requests to bound lifetime and handle remote keep-alive drops silently.

        Crucially: `drop()` must be called on timeout/error to discard the current
        session, ensuring the next request dials a fresh connection rather than retrying
        a stuck socket.
    """

    def __init__(self, recycle=100):
        self._local = threading.local()
        self._recycle = max(1, int(recycle or 100))
        self._all = []
        self._lock = threading.Lock()

    def get(self):
        sess = getattr(self._local, "sess", None)
        used = getattr(self._local, "used", 0)
        if sess is None or used >= self._recycle:
            self.drop()
            sess = _session()
            with self._lock:
                self._all.append(sess)
            self._local.sess = sess
            used = 0
        self._local.used = used + 1
        return sess

    def drop(self):
        """Discard this thread's session so the next get() dials fresh."""
        sess = getattr(self._local, "sess", None)
        self._local.sess = None
        self._local.used = 0
        if sess is not None:
            with self._lock:
                try:
                    self._all.remove(sess)
                except ValueError:
                    pass
            try:
                sess.close()
            except Exception:
                pass

    def close(self):
        with self._lock:
            sessions, self._all = list(self._all), []
        for sess in sessions:
            try:
                sess.close()
            except Exception:
                pass


def _download(sess, url, dest, job, what):
    """Stream a file to disk with resume-friendly retries. Returns bytes written."""
    for attempt in range(4):
        try:
            have = os.path.getsize(dest) if os.path.exists(dest) else 0
            headers = {"Range": "bytes=%d-" % have} if have else {}
            r = sess.get(url, stream=True, timeout=HTTP_TIMEOUT, headers=headers)
            if have and r.status_code == 416:
                return have                      # already complete
            if r.status_code not in (200, 206):
                raise OSError("HTTP %s" % r.status_code)
            mode = "ab" if (have and r.status_code == 206) else "wb"
            with open(dest, mode) as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            return os.path.getsize(dest)
        except Exception as exc:
            if attempt == 3:
                raise
            job.log("  retry %d/%d %s: %s" % (attempt + 1, 3, what, str(exc)[:80]))
            time.sleep(2 * (attempt + 1))
    return 0


def _met_caption(obj):
    bits = []
    medium = (obj.get("medium") or "").strip().lower()
    if medium:
        bits.append(medium)
    title = (obj.get("title") or "").strip()
    if title and title.lower() not in ("untitled",):
        bits.append(title)
    tags = [t.get("term") for t in (obj.get("tags") or []) if t.get("term")]
    if tags:
        bits.append(", ".join(tags[:6]).lower())
    who = (obj.get("artistDisplayName") or "").strip()
    if who:
        bits.append("by %s" % who)
    return ". ".join(b for b in bits if b)


def _fetch_met_manifest(job, spec, cache_dir, ids_override=None):
    """Fetch object records from the Met Open Access API.

        Args:
            job: The current pipeline job context.
            spec: Configuration dict including filters (tags, departments).
            cache_dir: Directory for caching fetched JSON payloads.
            ids_override: Optional list of object IDs to fetch directly, bypassing
                keyword search. Use this to avoid vocabulary coverage gaps inherent
                in the search API; when provided, only image URL resolution is performed.
    """
    import pyarrow as pa
    import requests
    src = SOURCES["met"]
    pool = _SessionPool(src.get("session_recycle"))
    sess = pool.get()
    f = spec.get("filters") or {}
    tags = [t for t in (f.get("tags") or []) if str(t).strip()] or list(src.get("default_tags") or [])
    if not tags:
        raise ValueError("a met order names its search tags (filters.tags); this factory's source table has no default")
    want_depts = [d for d in (f.get("departments") or []) if str(d).strip()]
    want_medium = [m.lower() for m in (f.get("medium_any") or [])]
    cap = max(1, min(int(spec.get("scan_limit") or 4000), 12000))
    rate = max(1, int(src.get("rate_per_sec") or 10))
    # Short read budget + few attempts. 60s x 4 workers x 1.5-3.0s backoff was a stall, not a
    # slowdown; see _SessionPool and RUNBOOK "Rules for ANY future bulk pull".
    obj_timeout = (API_CONNECT_TIMEOUT, float(src.get("timeout_sec") or API_READ_TIMEOUT))
    tries = max(1, int(src.get("retries") or 2))

    os.makedirs(cache_dir, exist_ok=True)
    known_depts = met_departments(sess, src, cache_dir)
    warned = set()
    ids, seen = [], set()
    if ids_override is not None:
        for i in ids_override:
            if i not in seen:
                seen.add(i)
                ids.append(i)
        job.log("met: %d candidate ids supplied by the bulk catalogue (no keyword search)"
                % len(ids))
        tags = []
    for tag in tags:
        params = {"tags": "true", "hasImages": "true", "q": tag}
        job.publish("manifest", "searching Met for tag %r" % tag)
        try:
            # A tag search scans ~490k objects and returns thousands of ids, so it gets a longer
            # read budget than a single object fetch — but still a bounded one.
            r = sess.get(src["search"], params=params,
                         timeout=(API_CONNECT_TIMEOUT, max(30.0, obj_timeout[1] * 2)))
            if r.status_code != 200:
                job.log("  met search %r -> HTTP %d (skipped)" % (tag, r.status_code))
                continue
            got = (r.json() or {}).get("objectIDs") or []
        except Exception as exc:
            job.log("  met search %r failed: %s: %s"
                    % (tag, type(exc).__name__, str(exc)[:90]))
            continue
        job.log("met search %r -> %d objects" % (tag, len(got)))
        for i in got:
            if i not in seen:
                seen.add(i)
                ids.append(i)
    ids = ids[:cap]
    job.log("met: %d unique object ids (scan cap %d)" % (len(ids), cap))

    rows, done, errors = [], [0], [0]
    lock = threading.Lock()
    # Must be a Lock (not Semaphore). Shared `last` causes all workers to sleep
    # the same duration and fire together, bypassing the rate limit.
    gate = threading.Lock()
    last = [0.0]
    fail_kinds = {}
    started = time.time()

    def _note(kind):
        with lock:
            fail_kinds[kind] = fail_kinds.get(kind, 0) + 1

    def _fetch(oid):
        """One object, rate-limited and retried. Distinguishes a FETCH FAILURE from a filtered-out
        object — conflating them is what made 4697 throttled requests look like 'nothing matched' —
        and now also distinguishes the KINDS of failure from each other. A 429 and a hung socket
        both used to surface as "unreachable", but they demand opposite responses: slow down versus
        stop reusing the connection. Every failure is recorded by kind and reported at the end."""
        cp = os.path.join(cache_dir, "%s.json" % oid)
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                pass
        kind = "unknown"
        for attempt in range(tries):
            with gate:
                # Serialized: hold the pacer while sleeping, and advance the next slot from the
                # SCHEDULE rather than from "now", so a burst cannot bunch up behind one wait.
                interval = 1.0 / rate
                wait = last[0] + interval - time.time()
                if wait > 0:
                    time.sleep(wait)
                last[0] = max(time.time(), last[0] + interval)
            pause = 1.0
            try:
                r = pool.get().get(src["object"] % oid, timeout=obj_timeout)
            except requests.exceptions.Timeout:
                kind = "timeout >%gs" % obj_timeout[1]
                pool.drop()              # never retry down a socket that just hung
            except Exception as exc:
                kind = "connection (%s)" % type(exc).__name__
                pool.drop()
            else:
                if r.status_code == 200:
                    try:
                        obj = r.json()
                    except Exception:
                        kind = "HTTP 200 but unparseable JSON"
                    else:
                        tmp = cp + ".tmp"
                        try:
                            with open(tmp, "w", encoding="utf-8") as fh:
                                json.dump(obj, fh)
                            os.replace(tmp, cp)   # cache as it lands, atomically
                        except OSError:
                            pass
                        return obj
                elif r.status_code in (403, 429) or r.status_code >= 500:
                    # Retry transient server/edge errors (not permanent denials).
                    kind = "HTTP %d%s" % (r.status_code,
                                          " throttled" if r.status_code in (403, 429) else "")
                    try:
                        pause = max(pause, min(30.0, float(r.headers.get("Retry-After", 0))))
                    except (TypeError, ValueError):
                        pass
                    if r.status_code in (403, 429):
                        pause = max(pause, 2.0)   # backing off is the whole point
                else:
                    # 404 and friends ARE final — the object is gone, and retrying cannot conjure
                    # it. Burning attempts on it just slows the whole scan.
                    _note("HTTP %d" % r.status_code)
                    return None
            if attempt < tries - 1:
                time.sleep(pause)
        _note(kind)
        return None

    def one(oid):
        obj = _fetch(oid)
        if obj is None:
            with lock:
                errors[0] += 1
            return None
        # CC0 only, and only objects that actually carry a full-resolution image.
        if not obj.get("isPublicDomain") or not (obj.get("primaryImage") or "").strip():
            return None
        if not _dept_matches(obj.get("department"), want_depts, known_depts, job, warned):
            return None
        med = (obj.get("medium") or "").lower()
        if want_medium and not any(m in med for m in want_medium):
            return None
        return {
            "id": str(obj.get("objectID")),
            "url": obj["primaryImage"],
            "caption": _met_caption(obj),
            "width": 0, "height": 0,          # not published; enforced after decode
            "mime_type": "image/jpeg",
            "license": "CC0 (Met Open Access)",
            "source": obj.get("department") or "The Metropolitan Museum of Art",
        }

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            for res in workers.map(one, ids):
                with lock:
                    done[0] += 1
                    n, hit, bad = done[0], len(rows), errors[0]
                    if res:
                        rows.append(res)
                        hit += 1
                if n % 100 == 0:
                    el = max(0.001, time.time() - started)
                    job.publish("manifest",
                                "read %d/%d Met objects (%d usable, %d failed) — %.1f/s"
                                % (n, len(ids), hit, bad, n / el))
    finally:
        pool.close()
    el = max(0.001, time.time() - started)
    breakdown = ", ".join("%s x%d" % (k, v)
                          for k, v in sorted(fail_kinds.items(), key=lambda kv: -kv[1]))
    job.log("met: %d usable of %d objects (%d could not be fetched) in %.0fs, %.1f/s"
            % (len(rows), len(ids), errors[0], el, len(ids) / el))
    if breakdown:
        job.log("met fetch failures by kind: %s" % breakdown)
    # Hand the numbers to whoever reports this run. A match count drawn from a scan that couldn't
    # read a fifth of its objects is a FLOOR, not a count, and anything reading it downstream has
    # to be able to say so — otherwise a partial scan is indistinguishable from a complete one.
    job.fetch_stats = {"scanned": len(ids), "unread": errors[0], "kinds": dict(fail_kinds)}
    # An empty result caused by the network is a DIFFERENT problem from an empty result caused by
    # the filter, and saying so is the difference between "fix your filter" and "you got throttled".
    if not rows and errors[0] > len(ids) // 2:
        raise OSError("%d of %d Met object requests failed (%s) — the API was unreachable or "
                      "throttling, so this says nothing about the filters. Cached objects are "
                      "kept; re-running resumes from the cache."
                      % (errors[0], len(ids), breakdown or "no detail recorded"))
    if not rows:
        raise ValueError("no public-domain Met objects matched those tags/departments (%d objects "
                         "read successfully, so this IS a filter result)" % (len(ids) - errors[0]))
    cols = ["id", "url", "caption", "width", "height", "mime_type", "license", "source"]
    return pa.table({c: [r[c] for r in rows] for c in cols})


def _ensure_bulk_table(job, cache_dir, src):
    """Fetch and cache a source's bulk catalogue table.

    Source-specific configuration is defined in the registry entry:
        - table_url, table_format, table_max_age_days, table_min_bytes
        - columns: mapping of expected fields to source-specific column names.
          Omit keys not present in the source.
        - public_domain_true: values treated as public domain (default ["true"])
    """
    url = (src.get("table_url") or "").strip()
    if not url:
        raise ValueError("source declares kind=bulk_table but no table_url — a bulk source is a "
                         "table URL plus a column mapping; see the met_bulk entry for the shape")
    os.makedirs(cache_dir, exist_ok=True)
    fname = os.path.basename(urllib.parse.urlparse(url).path) or "catalogue.csv"
    dest = os.path.join(cache_dir, fname)
    max_age = float(src.get("table_max_age_days") or 30)
    min_bytes = int(src.get("table_min_bytes") or 0)
    if os.path.exists(dest) and os.path.getsize(dest) >= max(1, min_bytes):
        age_days = (time.time() - os.path.getmtime(dest)) / 86400.0
        if age_days <= max_age:
            job.log("catalogue cached: %s (%.0f MB, %.1f days old)"
                    % (fname, os.path.getsize(dest) / 1e6, age_days))
            return dest
        job.log("catalogue %s is %.1f days old (max %.0f) — refreshing" % (fname, age_days, max_age))
    job.publish("manifest", "downloading the %s catalogue (once)" % (src.get("label") or fname))
    n = _download(_session(), url, dest, job, "catalogue")
    job.log("catalogue downloaded: %s (%.0f MB)" % (fname, n / 1e6))
    if min_bytes and n < min_bytes:
        raise OSError("%s came back only %.1f MB — expected at least %.0f MB, refusing to filter "
                      "against a truncated catalogue" % (fname, n / 1e6, min_bytes / 1e6))
    return dest


def _load_join_index(job, cache_dir, src):
    """Index a SECOND published table so its columns can be merged onto the main catalogue.

    Museums routinely split what we need across two files. NGA is the case that forced this: its
    `objects.csv` carries title/medium/classification but NO image URL and NO rights flag, while
    `published_images.csv` carries `iiifurl` and `openaccess` — keyed by `depictstmsobjectid` back
    to `objects.objectid`. Without a join, NGA cannot be used at all; with one, it is data.

    Deliberately generic: the join is declared in the registry, so the next museum that splits its
    catalogue the same way costs a JSON entry rather than another code path.
    """
    import csv
    jn = src.get("join") or {}
    if not jn.get("table_url"):
        return None, None
    csv.field_size_limit(1 << 24)
    jsrc = {"table_url": jn["table_url"], "table_max_age_days": jn.get("table_max_age_days", 30),
            "table_min_bytes": jn.get("table_min_bytes") or 0,
            "label": (src.get("label") or "") + " images"}
    path = _ensure_bulk_table(job, cache_dir, jsrc)
    right_key = jn.get("right_key")
    cols = jn.get("columns") or {}
    if not right_key or not cols:
        raise ValueError("join needs right_key and columns")
    # Keep only the first row per key: NGA publishes several views per object (primary, verso,
    # details) and the first is the primary one. Taking them all would multiply the corpus by
    # viewpoint and quietly fill a training set with backs of canvases.
    prefer = jn.get("prefer_column")
    prefer_val = str(jn.get("prefer_value") or "").strip().lower()
    index, seen = {}, set()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            k = str(rec.get(right_key) or "").strip()
            if not k:
                continue
            if prefer and prefer_val:
                if str(rec.get(prefer) or "").strip().lower() != prefer_val:
                    if k in index:
                        continue        # already have a preferred row
                elif k in seen:
                    continue
                else:
                    seen.add(k)
            elif k in index:
                continue
            index[k] = {ours: rec.get(theirs) for ours, theirs in cols.items()}
    job.log("join: indexed %d rows from %s on %r" % (len(index), os.path.basename(path), right_key))
    return index, jn.get("left_key")


def _filter_bulk_table(job, path, spec, src, cap, join_index=None, join_left=None):
    """Stream any source's catalogue and return the matching rows, mapped to OUR field names.

    Streamed rather than loaded: half a million rows of 54 columns is a lot of memory for a result
    that is a list of ids. Filters are case-insensitive SUBSTRINGS throughout, because published
    values are long and inconsistent and a spec that must reproduce them verbatim selects nothing.
    A filter naming a column this source does not declare is SKIPPED and said out loud — silently
    ignoring it would let a filter look applied when it never ran.
    """
    import csv
    csv.field_size_limit(1 << 24)
    cols = src.get("columns") or {}
    f = spec.get("filters") or {}
    pd_col = cols.get("public_domain")
    pd_true = [str(v).strip().lower() for v in (src.get("public_domain_true") or ["true"])]
    # (spec filter key, column key, keep-if-matched?)
    CHECKS = [("tags", "tags", True), ("departments", "department", True),
              ("medium_any", "medium", True), ("classification_any", "classification", True),
              ("classification_none", "classification", False),
              # Title matching required: subjects may exist only in titles (not tags) or sources
              # may lack tags entirely.
              ("title_any", "title", True), ("title_none", "title", False)]
    active, warned = [], []
    for fkey, ckey, keep in CHECKS:
        wanted = [str(w).strip().lower() for w in (f.get(fkey) or []) if str(w).strip()]
        if not wanted:
            continue
        if not cols.get(ckey):
            warned.append("%s (no %r column in this source)" % (fkey, ckey))
            continue
        active.append((cols[ckey], wanted, keep))
    for w in warned:
        job.log("  ⚠ filter %s cannot be applied and was IGNORED" % w)

    max_tags = int(f.get("max_tags") or 0)
    min_tags = int(f.get("min_tags") or 0)
    tags_col = cols.get("tags")
    if (max_tags or min_tags) and not tags_col:
        job.log("  ⚠ max_tags/min_tags cannot be applied and were IGNORED (no tags column)")
        max_tags = min_tags = 0

    id_col = cols.get("id")
    if not id_col:
        raise ValueError("source's column mapping has no 'id' column")
    url_col = cols.get("image_url") or ""
    rows, read, pd_rows = [], 0, 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for rec in csv.DictReader(fh):
            read += 1
            if join_index is not None:
                # Merge the second table's columns in BEFORE any filtering: for a split catalogue
                # the rights flag and image URL live over there, so filtering first would test
                # fields that are not present yet and drop everything.
                extra = join_index.get(str(rec.get(join_left) or "").strip())
                if extra is None:
                    continue                     # no published image for this object
                rec = dict(rec)
                rec.update({k: v for k, v in extra.items() if v not in (None, "")})
            if pd_col and str(rec.get(pd_col) or "").strip().lower() not in pd_true:
                continue
            pd_rows += 1
            ok = True
            for colname, wanted, keep in active:
                hit = any(w in str(rec.get(colname) or "").lower() for w in wanted)
                if hit != keep:
                    ok = False
                    break
            if not ok:
                continue
            if max_tags or min_tags:
                ntags = len([x for x in str(rec.get(tags_col) or "").split("|") if x.strip()])
                if max_tags and ntags > max_tags:
                    continue
                if min_tags and ntags < min_tags:
                    continue
            rid = str(rec.get(id_col) or "").strip()
            if not rid:
                continue
            rows.append({"_id": rid, "_url": str(rec.get(url_col) or "").strip() if url_col else "",
                         "_rec": rec})
            if len(rows) >= cap:
                job.log("catalogue: hit the %d-row scan cap — RAISE scan_limit to see the rest" % cap)
                break
    job.log("catalogue: %d rows read, %d public domain, %d match the filters" % (read, pd_rows,
                                                                                len(rows)))
    return rows


def _caption_from_columns(rec, cols):
    """Build caption from source columns, prepending 'medium' first.

    Prepending medium prevents the model from conflating physical material
    with the artistic concept being trained (e.g., "oil" vs "oil painting")."""
    bits = []
    for key in ("medium", "title", "classification"):
        col = cols.get(key)
        val = str(rec.get(col) or "").strip() if col else ""
        if val and val.lower() not in ("untitled",):
            bits.append(val.lower() if key == "medium" else val)
    tags_col = cols.get("tags")
    tags = str(rec.get(tags_col) or "").strip() if tags_col else ""
    if tags:
        bits.append(tags.lower()[:120])
    artist_col = cols.get("artist")
    who = str(rec.get(artist_col) or "").strip() if artist_col else ""
    if who:
        bits.append("by %s" % who)
    return ". ".join(b for b in bits if b)


def _fetch_bulk_manifest(job, spec, cache_dir, src):
    """Select from a source's whole published catalogue. Source-agnostic.

    Two image strategies, because museums differ and neither should need new code:
      * the catalogue already carries an image URL  -> the manifest is built from the table alone
        and the pull costs ZERO API calls;
      * it does not (the Met)                       -> resolve per-id against the source's object
        endpoint, for the winners only.
    """
    import pyarrow as pa
    cap = max(1, min(int(spec.get("scan_limit") or 4000), 40000))
    path = _ensure_bulk_table(job, cache_dir, src)
    job.publish("manifest", "filtering the %s catalogue locally" % (src.get("label") or "source"))
    join_index, join_left = _load_join_index(job, cache_dir, src)
    rows = _filter_bulk_table(job, path, spec, src, cap, join_index, join_left)
    if not rows:
        raise ValueError("no public-domain catalogue rows matched those filters (the WHOLE "
                         "catalogue was read, so this IS a filter result, not a coverage gap)")
    cols = src.get("columns") or {}
    if cols.get("image_url"):
        # A IIIF entry is a BASE url, not an image: NGA publishes
        # "https://api.nga.gov/iiif/<uuid>" and you must append a size/format suffix or you get
        # JSON metadata instead of pixels. Declared per-source so the next IIIF museum is data.
        tmpl = src.get("image_url_template")
        if tmpl:
            for r in rows:
                if r["_url"] and "%s" in tmpl:
                    r["_url"] = tmpl % r["_url"].rstrip("/")
        keep = [r for r in rows if r["_url"]]
        job.log("catalogue carries image URLs: %d of %d rows usable, no API calls needed"
                % (len(keep), len(rows)))
        if not keep:
            raise ValueError("catalogue matched %d rows but none carry an image URL in column %r"
                             % (len(rows), cols.get("image_url")))
        out = [{"id": r["_id"], "url": r["_url"],
                "caption": _caption_from_columns(r["_rec"], cols),
                "width": 0, "height": 0, "mime_type": "image/jpeg",
                "license": src.get("license_note") or "public domain (per source catalogue)",
                "source": str(r["_rec"].get(cols.get("department") or "") or
                              src.get("label") or "")} for r in keep]
        names = ["id", "url", "caption", "width", "height", "mime_type", "license", "source"]
        return pa.table({c: [o[c] for o in out] for c in names})
    # No image URL published: resolve the winners against the object endpoint.
    return _fetch_met_manifest(job, spec, cache_dir, ids_override=[r["_id"] for r in rows])


def fetch_manifest(job, src, shards, work_dir):
    """Download the first `shards` manifest shards and return them as one pyarrow Table."""
    import pyarrow.parquet as pq
    import pyarrow as pa
    os.makedirs(work_dir, exist_ok=True)
    sess = _session()
    tables = []
    for i in range(shards):
        url = src["shard_url"] % i
        dest = os.path.join(work_dir, os.path.basename(urllib.parse.urlparse(url).path))
        job.publish("manifest", "shard %d/%d" % (i + 1, shards),
                    shards_done=i, shards_total=shards)
        if not os.path.exists(dest) or os.path.getsize(dest) < 1024:
            n = _download(sess, url, dest, job, "shard %d" % i)
            job.log("shard %d/%d -> %s (%.1f MB)" % (i + 1, shards, dest, n / 1e6))
        else:
            job.log("shard %d/%d cached" % (i + 1, shards))
        tables.append(pq.read_table(dest, columns=src["columns"]))
    return pa.concat_tables(tables)


def scratch_free_gb():
    import shutil
    os.makedirs(SCRATCH_ROOT, exist_ok=True)
    return shutil.disk_usage(SCRATCH_ROOT).free / (1024 ** 3)


def promote(job, scratch_dir, out_dir):
    """Move a completed set off the NVMe scratch onto the SSD hold.

    A move, not a copy: scratch is working space and leaving a second full copy on the training NVMe
    is exactly the pressure this tiering exists to avoid. Same-volume moves are instant; this one
    crosses volumes, so it is a real copy+delete and only runs after the download fully succeeded.
    """
    import shutil
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        raise FileExistsError("%s already has contents — refusing to merge two sets" % out_dir)
    os.makedirs(os.path.dirname(out_dir.rstrip("\\")) or out_dir, exist_ok=True)
    shutil.move(scratch_dir, out_dir)
    job.log("promoted scratch -> hold: %s" % out_dir)
    return out_dir


def survey(job, table, target_dir):
    """What is actually in this corpus — the pass a human reads before spending disk on it."""
    d = table.to_pydict()
    n = table.num_rows

    def tally(col, top=25):
        c = {}
        for v in d[col]:
            k = str(v) if v is not None else "(null)"
            c[k] = c.get(k, 0) + 1
        return sorted(c.items(), key=lambda kv: -kv[1])[:top]

    short = sorted(min(w or 0, h or 0) for w, h in zip(d["width"], d["height"]))
    res = {str(t): sum(1 for s in short if s >= t) for t in (448, 768, 1024, 1536, 2048)}
    out = {
        "rows": n,
        "sources": [{"value": k, "rows": v, "pct": round(100.0 * v / n, 2)} for k, v in tally("source")],
        "licenses": [{"value": k, "rows": v, "pct": round(100.0 * v / n, 2)} for k, v in tally("license")],
        "mime_types": [{"value": k, "rows": v, "pct": round(100.0 * v / n, 2)} for k, v in tally("mime_type", 8)],
        "short_side_percentiles": {p: short[int(n * int(p) / 100)] for p in ("5", "25", "50", "75", "95")},
        "short_side_at_least": {k: {"rows": v, "pct": round(100.0 * v / n, 1)} for k, v in res.items()},
        "caption_sample": [str(c)[:160] for c in d["caption"][:5]],
    }
    path = os.path.join(target_dir, "survey.json")
    os.makedirs(target_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    job.log("survey written: %s" % path)
    return out, path


def select(job, table, filters, limit, src=None):
    """Apply the spec's filters and return the chosen rows.

    Matching on source/licence is case-insensitive SUBSTRING, not equality: the exact strings are
    long and inconsistent across institutions ("The Metropolitan Museum of Art"), and a spec that has
    to reproduce them verbatim will silently select nothing. survey.json reports the exact values.
    """
    d = table.to_pydict()
    want_src = [s.lower() for s in (filters.get("sources") or [])]
    want_lic = [s.lower() for s in (filters.get("licenses") or [])]
    cap_any = [s.lower() for s in (filters.get("caption_any") or [])]
    cap_none = [s.lower() for s in (filters.get("caption_none") or [])]
    # A source that does not publish dimensions (the Met) must NOT be filtered on them here — every
    # row would read as 0px and the whole selection would silently come back empty. acquire()
    # enforces the floor after decoding instead, which measures the real file rather than a field.
    min_short = int(filters.get("min_short_side") or 0)
    if src is not None and not src.get("dims_known"):
        min_short = 0
    seen_hash = set()
    picked = []
    for i in range(table.num_rows):
        src = str(d["source"][i] or "").lower()
        lic = str(d["license"][i] or "").lower()
        cap = str(d["caption"][i] or "")
        capl = cap.lower()
        w, h = d["width"][i] or 0, d["height"][i] or 0
        if want_src and not any(s in src for s in want_src):
            continue
        if want_lic and not any(s in lic for s in want_lic):
            continue
        if min_short and min(w, h) < min_short:
            continue
        if cap_any and not any(s in capl for s in cap_any):
            continue
        if cap_none and any(s in capl for s in cap_none):
            continue
        key = str(d["id"][i])
        if key in seen_hash:
            continue
        seen_hash.add(key)
        picked.append({"id": key, "url": d["url"][i], "caption": cap,
                       "width": w, "height": h,
                       "license": str(d["license"][i] or ""), "source": str(d["source"][i] or "")})
        if len(picked) >= limit:
            break
    return picked


def _caption_for(row, trigger):
    cap = _CAPTION_BOILERPLATE.sub("", str(row["caption"] or "")).strip()
    cap = cap[:1].upper() + cap[1:] if cap else ""
    return ("%s, %s" % (trigger, cap)) if trigger else cap


def acquire(job, rows, out_dir, prefix, trigger, max_side, min_short=0, src=None):
    """Download the selected images into the house training layout, with provenance."""
    from PIL import Image
    import requests
    train = os.path.join(out_dir, "train")
    os.makedirs(train, exist_ok=True)
    # Same politeness discipline as the manifest pass, and per-source for the same reason: a new
    # image host is tuned by editing JSON. Threads and rate are separate knobs because they bound
    # different things — concurrency bounds how many large transfers are open at once, rate bounds
    # how fast we ASK. A serialized lock, not a semaphore (see the manifest pacer).
    src = src or {}
    # Default to polite rate if missing (fail-closed). Explicit 0 disables pacing.
    _rate = src.get("image_rate_per_sec")
    img_rate = float(DEFAULT_IMAGE_RATE_PER_SEC if _rate is None else _rate)
    threads = max(1, min(int(src.get("dl_threads") or DL_THREADS), DL_THREADS))
    dl_gate, dl_last = threading.Lock(), [0.0]

    def _pace():
        if img_rate <= 0:
            return
        with dl_gate:
            interval = 1.0 / img_rate
            wait = dl_last[0] + interval - time.time()
            if wait > 0:
                time.sleep(wait)
            dl_last[0] = max(time.time(), dl_last[0] + interval)
    # Per-thread sessions here too: this is the same shared-Session-across-N-workers shape that
    # stalled the manifest pull, just with 8 workers and multi-MB bodies instead of 4 and small
    # JSON. The read budget stays at HTTP_TIMEOUT because these ARE large transfers; only the
    # connect is held short, since connecting is never the slow part of a healthy download.
    pool = _SessionPool(64)
    results = [None] * len(rows)
    done = [0]
    t0 = time.time()

    def one(i, row):
        # One retry, because a single transient blip used to lose that image permanently — and an
        # image dropped here is a hole in the set that nothing later notices.
        err = None
        for attempt in range(2):
            try:
                _pace()
                r = pool.get().get(row["url"], timeout=(API_CONNECT_TIMEOUT, HTTP_TIMEOUT))
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                err = "%s (attempt %d)" % (type(exc).__name__, attempt + 1)
                pool.drop()
                if attempt == 0:
                    time.sleep(1.0)
        else:
            return (err or "unreachable", None)
        try:
            if r.status_code != 200:
                return ("http %s" % r.status_code, None)
            im = Image.open(io.BytesIO(r.content))
            im.load()
            # Enforce the resolution floor on the DECODED image. For sources that publish dimensions
            # this is a harmless second check; for the Met, which doesn't, it is the only one.
            if min_short and min(im.size) < min_short:
                return ("too small (%dx%d, floor %d)" % (im.size[0], im.size[1], min_short), None)
            if im.mode != "RGB":
                im = im.convert("RGB")
            if max_side and max(im.size) > max_side:
                ratio = max_side / float(max(im.size))
                im = im.resize((max(1, int(im.size[0] * ratio)), max(1, int(im.size[1] * ratio))),
                               Image.LANCZOS)
            return (None, (im, hashlib.sha256(r.content).hexdigest()))
        except Exception as exc:
            return (str(exc)[:100], None)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as workers:
            futs = {workers.submit(one, i, r): i for i, r in enumerate(rows)}
            for fut in concurrent.futures.as_completed(futs):
                i = futs[fut]
                results[i] = fut.result()
                done[0] += 1
                if done[0] % 10 == 0 or done[0] == len(rows):
                    rate = done[0] / max(0.001, time.time() - t0)
                    left = int((len(rows) - done[0]) / max(0.01, rate))
                    job.publish("downloading", "%d/%d images" % (done[0], len(rows)),
                                images_done=done[0], images_total=len(rows),
                                eta="~%d min left" % max(0, left // 60))
    finally:
        pool.close()

    kept, failed = 0, []
    manifest = [
        "# P20 dataset provenance — %s" % job.name,
        "# generated %s by HAI (spec: %s)" % (_now(), os.path.basename(job.spec_path)),
        "# every row is public-domain / CC0 per the source manifest; url is the fetched original",
        "# file\tid\tlicense\tsource\torig_wxh\tsha256\turl",
    ]
    for i, row in enumerate(rows):
        err, payload = results[i] if results[i] else ("no result", None)
        if err or not payload:
            failed.append("%s %s" % (row["id"], err))
            continue
        im, sha = payload
        kept += 1
        base = "%s_%03d" % (prefix, kept)
        im.save(os.path.join(train, base + ".jpg"), "JPEG", quality=95)
        with open(os.path.join(train, base + ".txt"), "w", encoding="utf-8") as f:
            f.write(_caption_for(row, trigger))
        manifest.append("%s.jpg\t%s\t%s\t%s\t%dx%d\t%s\t%s"
                        % (base, row["id"], row["license"], row["source"],
                           row["width"], row["height"], sha[:16], row["url"]))
    with open(os.path.join(out_dir, "_manifest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(manifest) + "\n")
    if failed:
        with open(os.path.join(out_dir, "_failed.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(failed) + "\n")
    return kept, failed


def _load_rubric():
    """The validated scoring rubric, from JSON beside the runner.

    A prompt is DATA, like sources and paths. It also carries its own regression controls (two
    images that must score high, two that must score low) — re-run those before trusting any edit,
    because the failure mode is silent: a looser prompt still returns confident numbers.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "curate_rubric.json")
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _gpu_busy_local():
    """(busy, why) for THIS box's NVIDIA GPU, via nvidia-smi. None if there isn't one.

    Used to keep scoring off Softserve's card whenever Plex might want it. Read locally rather than
    through Glances because the runner is ON that box: one subprocess, no network, no dependency on
    a monitoring service that might itself be down.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
        if out.returncode != 0 or not out.stdout.strip():
            return None, "no nvidia-smi"
        used, total, util = [int(x.strip()) for x in out.stdout.strip().splitlines()[0].split(",")]
        return (used, total, util), ""
    except Exception as exc:
        return None, str(exc)[:60]


def _local_training_owns_gpu():
    """Return (True, reason) if a LoRA trainer currently holds the GPU.

    A second concurrent GPU context poisons the existing one, degrading all
    subsequent spawns. This detection is process-based to function on AMD
    hardware where nvidia-smi telemetry is absent."""
    if os.name != "nt":
        return False, ""
    try:
        # Name -eq python.exe keeps the query from matching its OWN powershell command line, which
        # contains the 'run.py' literal (self-matching probes have bitten this project repeatedly).
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and "
             "$_.CommandLine -like '*run.py*' } | Measure-Object).Count"],
            capture_output=True, text=True, timeout=20)
        n = int((out.stdout or "0").strip() or 0)
        if n > 0:
            return True, "a LoRA training run owns this GPU (%d trainer proc)" % n
    except Exception as exc:
        # Fail CLOSED: unable to prove the card is free is not the same as free.
        return True, "cannot verify trainer state (%s) - yielding" % str(exc)[:40]
    return False, ""


def _plex_wants_gpu(job, cfg, our_vram_mb):
    """True when this host's GPU should be surrendered to a higher-priority claimant.

    Returns True if a local TRAINING run is active, or if Plex is using the GPU on
    Softserve's 3070 Ti (scoring must yield to Plex and cannot be primary there).
    `our_vram_mb` excludes our own VRAM usage to prevent self-eviction loops.
    """
    training, twhy = _local_training_owns_gpu()
    if training:
        return True, twhy
    stats, why = _gpu_busy_local()
    if stats is None:
        return False, "no gpu telemetry (%s)" % why
    used, total, util = stats
    others = max(0, used - our_vram_mb)
    mem_pct = 100.0 * others / max(1, total)
    if mem_pct > float(cfg.get("yield_mem_pct") or 20):
        return True, "other GPU users at %.0f%% VRAM" % mem_pct
    if util > float(cfg.get("yield_util_pct") or 50) and others > 256:
        return True, "GPU util %d%% with %d MB in other processes" % (util, others)
    return False, "gpu free (%.0f%% other VRAM, %d%% util)" % (mem_pct, util)


def _unload_model(host, model):
    """Free the VRAM immediately instead of waiting out keep_alive.

    'Leave gracefully' means the card is actually released when Plex needs it, not in two minutes.
    keep_alive:0 tells ollama to drop the model as soon as this call returns.
    """
    try:
        import requests
        requests.post(host.rstrip("/") + "/api/generate", timeout=30,
                      json={"model": model, "prompt": "", "keep_alive": 0})
    except Exception:
        pass


def _score_image(pool, host, model, prompt, path, max_side, timeout=240, schema=None,
                 num_ctx=4096):
    """One VLM read of one image -> dict. Raises on failure so the caller can count it."""
    import io
    from PIL import Image
    im = Image.open(path)
    im.load()
    if im.mode != "RGB":
        im = im.convert("RGB")
    if max_side and max(im.size) > max_side:
        r = max_side / float(max(im.size))
        im = im.resize((max(1, int(im.size[0] * r)), max(1, int(im.size[1] * r))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode()
    payload = {"model": model, "stream": False, "think": False,
               "messages": [{"role": "user", "content": prompt, "images": [b64]}],
               "options": {"num_ctx": num_ctx}}
    # For thinking models (e.g., qwen3-vl), "think": False is ignored.
    # The schema/grammar constraint is required to prevent verbose reasoning
    # from overflowing num_ctx and truncating the JSON response.
    if schema:
        payload["format"] = schema
    r = pool.get().post(host.rstrip("/") + "/api/chat", data=json.dumps(payload).encode(),
                        timeout=timeout, headers={"Content-Type": "application/json"})
    if r.status_code != 200:
        raise OSError("HTTP %s" % r.status_code)
    msg = r.json()["message"]
    # qwen3-vl is a THINKING model, and ollama 0.32.3 files the grammar-constrained answer under
    # `thinking`, leaving `content` empty -- even with "think": False. So the JSON was perfect and
    # we were reading the wrong field: every image failed in ~2 s with `Expecting value: line 1
    # column 1`, which reads like the model produced nothing. The tell that it was a plumbing bug
    # and not a model bug: empty content with a NON-ZERO eval_count. Take whichever field has text.
    txt = msg.get("content") or msg.get("thinking") or ""
    if schema:
        return json.loads(txt)        # grammar-constrained: parse it straight, no brace hunting
    # Legacy path for callers without a schema: find the JSON inside prose. Kept working, but any
    # new caller should pass a schema instead of hoping.
    return json.loads(txt[txt.index("{"):txt.rindex("}") + 1])


def _resolve_vlm_fleet(job, spec, rub, host, model, workers):
    """Return the list of GPU endpoints and their concurrency limits.

    Constraints:
    - One worker per GPU to avoid KV cache thrashing (model partially offloaded).
    - Exception: 'Mothership' allows 2 workers (27b model fully resident in 24GB VRAM).
    - Fleet is dynamic (probed at runtime); sleeping or missing nodes are dropped.

    Returns [] if only the default single-host path should be used."""
    want = spec.get("vlm_fleet")
    if not want:
        return [{"label": "primary", "host": host, "model": model,
                 "workers": workers, "yield_to_plex": False}]
    if want is True or str(want).lower() in ("auto", "all", "fleet"):
        want = list(rub.get("fleet") or DEFAULT_VLM_FLEET)

    live = []
    for ep in want:
        ep = dict(ep)
        ep.setdefault("workers", 1)
        ep.setdefault("yield_to_plex", False)
        ep["label"] = ep.get("label") or ep.get("host", "?")
        try:
            import requests
            r = requests.get(ep["host"].rstrip("/") + "/api/tags", timeout=8).json()
            have = {m["name"] for m in (r.get("models") or [])}
        except Exception as exc:
            job.log("  fleet: %s unreachable (%s) — skipped" % (ep["label"], type(exc).__name__))
            continue
        if ep["model"] not in have:
            job.log("  fleet: %s has no %s — skipped" % (ep["label"], ep["model"]))
            continue
        if ep.get("yield_to_plex"):
            busy, why = _plex_wants_gpu(job, rub.get("plex_yield") or {}, 0)
            if busy:
                job.log("  fleet: %s yielding to Plex (%s) — skipped" % (ep["label"], why))
                continue
        live.append(ep)
    if not live:
        job.log("  fleet: nothing reachable — falling back to %s" % host)
        return [{"label": "primary", "host": host, "model": model,
                 "workers": workers, "yield_to_plex": False}]
    job.log("fleet: %s" % ", ".join("%s(%s x%d)" % (e["label"], e["model"], e["workers"])
                                    for e in live))
    return live


def curate(job, spec):
    """Score every image in a set with the VLM and rank them for review.

    Non-destructive: outputs are appended to `_scores.jsonl`.
    Existing scores are skipped to allow resuming interrupted runs."""
    rub = _load_rubric()
    prompt = rub["prompt"]
    host = (spec.get("vlm_host") or _default_vlm_host()).strip()
    model = spec.get("vlm_model") or rub.get("model") or "qwen3.6:27b"
    max_side = int(spec.get("max_side_px") or rub.get("max_side_px") or 512)
    workers = max(1, min(int(spec.get("workers") or rub.get("concurrent_workers") or 2), 4))
    fleet = _resolve_vlm_fleet(job, spec, rub, host, model, workers)

    sets = spec.get("sets") or ([spec["set_dir"]] if spec.get("set_dir") else [])
    if not sets:
        raise ValueError("mode=curate needs `sets` (a list of dataset dirs) or `set_dir`")

    pool = _SessionPool(256)
    total_new, total_skipped, failures = 0, 0, []
    for set_dir in sets:
        train = os.path.join(set_dir, "train")
        if not os.path.isdir(train):
            job.log("  ⚠ no train/ under %s — skipped" % set_dir)
            continue
        images = sorted(glob.glob(os.path.join(train, "*.jpg")))
        out_path = os.path.join(set_dir, "_scores.jsonl")
        done = {}
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        rec = json.loads(line)
                        done[rec["file"]] = rec
                    except Exception:
                        continue
        todo = [p for p in images if os.path.basename(p) not in done]
        job.log("curate %s: %d images, %d already scored, %d to do"
                % (os.path.basename(set_dir), len(images), len(done), len(todo)))
        lock = threading.Lock()
        t0, n = time.time(), [0]

        def one(path, ep):
            name = os.path.basename(path)
            try:
                obj = _score_image(pool, ep["host"], ep["model"], prompt, path, max_side)
                rec = {"file": name, "set": os.path.basename(set_dir),
                       "keep_score": obj.get("keep_score"),
                       "figure_count": obj.get("figure_count"),
                       "frame_share": obj.get("frame_share"),
                       "clutter": obj.get("clutter"), "faults": obj.get("faults"),
                       # WHICH model judged this image. Without it a mixed-fleet run produces a
                       # shortlist nobody can audit — two models are not one rubric, and a score
                       # is only comparable to another score from the same judge.
                       "scored_by": ep["model"], "scored_on": ep["label"]}
            except Exception as exc:
                with lock:
                    failures.append("%s %s" % (name, str(exc)[:70]))
                return None
            with lock:
                # Append as it lands: a killed run keeps everything it earned.
                with open(out_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
                done[name] = rec
                n[0] += 1
                if n[0] % 25 == 0 or n[0] == len(todo):
                    el = max(0.001, time.time() - t0)
                    left = int((len(todo) - n[0]) / max(0.001, n[0] / el))
                    job.publish("curating", "%s: %d/%d scored (%.2fs/img)"
                                % (os.path.basename(set_dir), n[0], len(todo), el / n[0]),
                                images_done=n[0], images_total=len(todo),
                                eta="~%d min left" % max(0, left // 60))
            return rec

        if todo:
            # One SHARED queue, not a pre-split of the work: a fast card simply pulls more. Splitting
            # up front would make the run as slow as its slowest GPU, which is the whole thing we are
            # trying to avoid.
            work = queue.Queue()
            for p in todo:
                work.put(p)
            stop = threading.Event()

            def drive(ep, slot):
                consecutive = 0
                while not stop.is_set():
                    # Plex outranks us: re-checked as we go, not just at startup, so a stream that
                    # starts mid-run still gets the card back.
                    if ep.get("yield_to_plex"):
                        busy, why = _plex_wants_gpu(job, rub.get("plex_yield") or {}, 0)
                        if busy:
                            job.log("  %s: yielding to Plex mid-run (%s) — leaving" % (ep["label"], why))
                            _unload_model(ep["host"], ep["model"])
                            return
                    try:
                        path = work.get_nowait()
                    except queue.Empty:
                        return
                    rec = one(path, ep)
                    if rec is None:
                        consecutive += 1
                        # A card that fails repeatedly (asleep, model evicted, OOM) drops out and its
                        # work goes back for someone else. Requeue BEFORE leaving or the image is lost.
                        if consecutive >= 3:
                            work.put(path)
                            job.log("  %s: 3 consecutive failures — dropping this endpoint" % ep["label"])
                            return
                    else:
                        consecutive = 0

            threads = []
            for ep in fleet:
                for slot in range(int(ep.get("workers") or 1)):
                    t = threading.Thread(target=drive, args=(ep, slot),
                                         name="score-%s-%d" % (ep["label"], slot), daemon=True)
                    t.start()
                    threads.append(t)
            for t in threads:
                t.join()
            if not work.empty():
                # Every endpoint left while work remained: finish on the primary rather than
                # silently returning a partial set.
                left = []
                while not work.empty():
                    left.append(work.get_nowait())
                job.log("  %d image(s) unscored after all endpoints left — finishing on %s"
                        % (len(left), fleet[0]["label"]))
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
                    list(ex.map(lambda p: one(p, fleet[0]), left))
        total_new += n[0]
        total_skipped += len(done) - n[0]
        _write_shortlist(job, set_dir, done)
    pool.close()
    if failures:
        job.log("curate: %d failures (e.g. %s)" % (len(failures), "; ".join(failures[:3])))
    job.publish("done", "%d newly scored, %d already had scores, %d failed"
                % (total_new, total_skipped, len(failures)),
                done=True, ok=True, scored_new=total_new, scored_existing=total_skipped,
                scored_failed=len(failures),
                summary=("Scored %d images (%d were already done). Ranked shortlist + contact sheet "
                         "written into each set as _shortlist.html; every score is kept in "
                         "_scores.jsonl so the set can be re-cut at any threshold without "
                         "re-running inference. NOTHING was moved or deleted."
                         % (total_new, total_skipped)))
    job.log("DONE (curate) new=%d existing=%d failed=%d" % (total_new, total_skipped, len(failures)))
    return 0


def _dhash(path, size=12):
    """Perceptual difference hash (144-bit dhash).

    Requires pre-processing for this corpus (faint graphite on cream paper):
      1. Autocontrast: ensures faint lines register against the background.
      2. Crop to content: removes blank margins/mounts so the subject fills the grid.
      3. 12x12 grid: provides finer discrimination than standard 8x8 (64-bit).
    """
    from PIL import Image, ImageOps
    im = Image.open(path)
    im.load()
    g = ImageOps.autocontrast(im.convert("L"), cutoff=1)
    # Bounding box of anything meaningfully darker than the page.
    mask = g.point(lambda v: 255 if v < 200 else 0)
    bbox = mask.getbbox()
    if bbox:
        g = g.crop(bbox)
    g = ImageOps.autocontrast(g, cutoff=2)
    g = g.resize((size + 1, size), Image.LANCZOS)
    px = list(g.getdata())
    bits = 0
    n = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits |= (1 if px[base + col] > px[base + col + 1] else 0) << n
            n += 1
    return bits


def _hamming(a, b):
    return bin(a ^ b).count("1")


def dedupe(job, spec):
    """Find duplicate and near-duplicate images across one or more sets.

        Writes `_duplicates.json` listing each group and the member to KEEP. The
        `publish` step skips the losers. Original files are never moved or deleted.

        Tie-breaking for keeper within a group: highest keep_score (if scored),
        then largest file size, then filename for determinism.
    """
    # Thresholds in bits of 144-bit hash (see _dhash). Calibrated:
    # identical = 0, rescale = 1-4, different view = ~20-30.
    # threshold=14 drops true redundancy; near_threshold=30 marks
    # "similar-but-kept" for review. Drops are unrecoverable.
    threshold = int(spec.get("threshold") if spec.get("threshold") is not None else 14)
    near_threshold = int(spec.get("near_threshold") if spec.get("near_threshold") is not None else 30)
    sets = spec.get("sets") or ([spec["set_dir"]] if spec.get("set_dir") else [])
    if not sets:
        raise ValueError("mode=dedupe needs `sets`")

    scores = {}
    entries = []          # (hash, set_name, filename, path, size, score)
    for set_dir in sets:
        train = os.path.join(set_dir, "train")
        if not os.path.isdir(train):
            job.log("  no train/ under %s - skipped" % set_dir)
            continue
        sp = os.path.join(set_dir, "_scores.jsonl")
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        scores[r["file"]] = r.get("keep_score")
                    except Exception:
                        continue
        files = sorted(glob.glob(os.path.join(train, "*.jpg")))
        job.log("hashing %s: %d images" % (os.path.basename(set_dir), len(files)))
        for n, p in enumerate(files, 1):
            try:
                h = _dhash(p)
            except Exception as exc:
                job.log("  unreadable %s (%s)" % (os.path.basename(p), type(exc).__name__))
                continue
            name = os.path.basename(p)
            entries.append((h, os.path.basename(set_dir), name, p,
                            os.path.getsize(p), scores.get(name)))
            if n % 500 == 0:
                job.publish("dedupe", "%s: hashed %d/%d" % (os.path.basename(set_dir), n, len(files)))

    job.log("hashed %d images total; grouping (threshold %d bits)" % (len(entries), threshold))

    # Exact matches first via the hash itself, then a near-match sweep over the exact-group
    # representatives. Comparing every pair of ~7,500 images is 28M comparisons; bucketing by exact
    # hash first collapses the identical ones for free and leaves far fewer representatives.
    exact = {}
    for e in entries:
        exact.setdefault(e[0], []).append(e)
    reps = sorted(exact.keys())
    parent = {h: h for h in reps}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    near_pairs = []
    for a in range(len(reps)):
        for b in range(a + 1, len(reps)):
            d = _hamming(reps[a], reps[b])
            if threshold > 0 and d <= threshold:
                union(reps[a], reps[b])
            elif d <= near_threshold:
                # Similar enough to be worth a human glance, different enough to keep both.
                near_pairs.append((d, exact[reps[a]][0], exact[reps[b]][0]))
    near_pairs.sort(key=lambda t: t[0])

    groups = {}
    for h in reps:
        groups.setdefault(find(h), []).extend(exact[h])

    dupe_groups, losers = [], []
    for _, members in groups.items():
        if len(members) < 2:
            continue
        ranked = sorted(members, key=lambda e: (-(e[5] or 0), -e[4], e[2]))
        keep, drop = ranked[0], ranked[1:]
        dupe_groups.append({
            "keep": {"set": keep[1], "file": keep[2], "score": keep[5]},
            "drop": [{"set": d[1], "file": d[2], "score": d[5]} for d in drop],
        })
        losers.extend((d[1], d[2]) for d in drop)

    out = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "threshold": threshold, "near_threshold": near_threshold, "images": len(entries),
           "groups": len(dupe_groups), "duplicates": len(losers),
           "drop": ["%s/%s" % (s, f) for s, f in losers],
           "detail": dupe_groups,
           # Kept, not dropped: close enough to notice, far enough apart to be a genuinely
           # different view. Listed so a human can judge rather than a threshold deciding.
           "similar_kept": [{"distance": d, "a": "%s/%s" % (x[1], x[2]), "b": "%s/%s" % (y[1], y[2])}
                            for d, x, y in near_pairs[:400]]}
    dest = spec.get("out") or os.path.join(os.path.dirname(sets[0].rstrip("\\/")),
                                           "_duplicates.json")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)

    job.log("DONE (dedupe) %d images, %d duplicate groups, %d to drop -> %s"
            % (len(entries), len(dupe_groups), len(losers), dest))
    job.publish("done", "%d duplicate groups covering %d extra copies"
                % (len(dupe_groups), len(losers)),
                done=True, ok=True, images=len(entries), groups=len(dupe_groups),
                duplicates=len(losers), report=dest,
                summary=("Hashed %d images and found %d duplicate groups (%d redundant copies). "
                         "NOTHING was moved or deleted - the report lists which copy to keep, and "
                         "publish now skips the rest." % (len(entries), len(dupe_groups), len(losers))))
    return 0


def collect_sets(job, spec):
    """Pull finished image sets from Mothership to the local archive on Softserve.

        Architectural constraints:
        - Mothership (GPU node) handles image processing but cannot write directly to
          the shared storage array due to permission/protocol limitations.
        - Softserve (storage host) pulls via SSH to leverage its local write access.

        Behavior:
        - Copies files (does not move/source-delete), preserving sets on Mothership.
        - Idempotent: skips existing files with matching counts; safe for retries.
    """
    remote_root = spec.get("remote_root") or _setting("FOUNDRY_DATASET_ROOT")
    local_root = spec.get("archive_root") or ARCHIVE_ROOT or _setting("DATASET_ARCHIVE_ROOT")
    host = spec.get("remote_host") or _setting("DATASET_REMOTE_HOST")
    if not (remote_root and local_root and host):
        raise ValueError("mode=publish needs remote_root, archive_root and remote_host (the spec, or the settings "
                         "FOUNDRY_DATASET_ROOT, DATASET_ARCHIVE_ROOT, DATASET_REMOTE_HOST)")
    only = [s.strip() for s in (spec.get("sets") or []) if str(s).strip()]

    # Ask Mothership what it has. One ssh call, list-argv, no shell string building.
    listing = subprocess.run(
        ["ssh", "-o", "LogLevel=ERROR", host,
         "powershell -NoProfile -Command \"Get-ChildItem '%s' -Directory | "
         "Where-Object { Test-Path (Join-Path $_.FullName 'train') } | "
         "ForEach-Object { $_.Name + '|' + (Get-ChildItem (Join-Path $_.FullName 'train') "
         "-Filter *.jpg).Count }\"" % remote_root],
        capture_output=True, text=True, timeout=180)
    if listing.returncode != 0:
        raise OSError("could not list %s on %s: %s" % (remote_root, host,
                                                       (listing.stderr or "")[:150]))
    remote = {}
    for line in listing.stdout.splitlines():
        line = line.strip()
        if "|" in line:
            name, _, count = line.partition("|")
            if name.strip() and count.strip().isdigit():
                remote[name.strip()] = int(count.strip())
    if only:
        remote = {k: v for k, v in remote.items() if k in only}
    job.log("collect: %d set(s) on %s: %s" % (len(remote), host,
                                              ", ".join("%s(%d)" % kv for kv in sorted(remote.items()))))

    pulled, skipped, failed = [], [], []
    for name, rcount in sorted(remote.items()):
        dest = os.path.join(local_root, name)
        have = 0
        train = os.path.join(dest, "train")
        if os.path.isdir(train):
            have = len([f for f in os.listdir(train) if f.lower().endswith(".jpg")])
        if have == rcount and rcount > 0:
            skipped.append(name)
            continue
        job.publish("collecting", "pulling %s (%d images)" % (name, rcount))
        r = subprocess.run(["scp", "-q", "-r", "%s:%s/%s" % (host, remote_root.replace("\\", "/"), name),
                            local_root], capture_output=True, text=True, timeout=7200)
        after = len([f for f in os.listdir(train) if f.lower().endswith(".jpg")]) \
            if os.path.isdir(train) else 0
        # Verify by COUNT, not by exit code: a partial scp can still return 0.
        if r.returncode == 0 and after == rcount:
            pulled.append("%s(%d)" % (name, after))
            job.log("collect: %s -> %s (%d images verified)" % (name, dest, after))
        else:
            failed.append("%s (got %d of %d)" % (name, after, rcount))
            job.log("collect: %s INCOMPLETE — %d of %d" % (name, after, rcount))

    job.publish("done", "pulled %d, already present %d, incomplete %d"
                % (len(pulled), len(skipped), len(failed)),
                done=True, ok=not failed, pulled=pulled, skipped=skipped, failed=failed,
                summary=("Pulled %s onto the array; %d set(s) were already there. %s Sets remain on "
                         "Mothership too — this copies, never moves."
                         % (", ".join(pulled) or "nothing", len(skipped),
                            ("INCOMPLETE: " + "; ".join(failed)) if failed else "")))
    job.log("DONE (collect) pulled=%d skipped=%d failed=%d" % (len(pulled), len(skipped), len(failed)))
    return 0


def publish_candidates(job, spec):
    """Publish top-scoring candidates as PNG thumbnails for HAI inline rendering.

        Requirements:
        - Output format must be .png (serving endpoint rejects others).
        - Filenames must match [A-Za-z0-9._-] (sanitized by serving endpoint).
        - Files must exist on disk before referenced in chat markdown (missing images are stripped).
        - Copies to DOCS_DIR/State/images/<user>/; original datasets remain untouched.
    """
    import io
    from PIL import Image
    sets = spec.get("sets") or ([spec["set_dir"]] if spec.get("set_dir") else [])
    if not sets:
        raise ValueError("mode=publish needs `sets` or `set_dir`")
    images_root = spec.get("images_dir") or _setting("DATASET_IMAGES_DIR")
    user = re.sub(r"[^A-Za-z0-9_-]", "_", str(spec.get("user") or _setting("OWNER_USER") or "owner"))[:60]
    min_score = int(spec.get("min_score") or 5)
    limit = max(1, min(int(spec.get("limit") or 400), 2000))
    thumb_px = int(spec.get("thumb_px") or 384)
    dest = os.path.join(images_root, user)
    os.makedirs(dest, exist_ok=True)

    rows = []
    for set_dir in sets:
        sp = os.path.join(set_dir, "_scores.jsonl")
        if not os.path.exists(sp):
            job.log("  ⚠ %s has no _scores.jsonl — run mode=curate first" % os.path.basename(set_dir))
            continue
        with open(sp, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("keep_score") or 0) >= min_score:
                    r["_dir"] = set_dir
                    rows.append(r)
    # --- drop known duplicates -------------------------------------------------------------
    # The dedupe pass reports which copy to keep; publish simply honours it. Reported, never
    # enforced by dedupe itself, so a bad grouping can be corrected by re-running that pass instead
    # of restoring images.
    dupes = set()
    dupe_report = spec.get("duplicates_file") or os.path.join(
        os.path.dirname(sets[0].rstrip("\\/")), "..", "Documentation", "State",
        "dataset-duplicates.json")
    for cand_path in (spec.get("duplicates_file"), _setting("DATASET_DUPLICATES_FILE")):
        if cand_path and os.path.exists(cand_path):
            try:
                with open(cand_path, encoding="utf-8") as fh:
                    rep = json.load(fh)
                dupes = {x.split("/", 1)[-1] for x in (rep.get("drop") or [])}
                job.log("publish: excluding %d known duplicates (%s)"
                        % (len(dupes), os.path.basename(cand_path)))
            except Exception as exc:
                job.log("  could not read duplicate report (%s) - continuing without it"
                        % type(exc).__name__)
            break
    if dupes:
        before = len(rows)
        rows = [r for r in rows if (r.get("file") or "") not in dupes]
        job.log("publish: %d -> %d after removing duplicates" % (before, len(rows)))

    # Bucket by subject, when an extension supplies the buckets (PUBLISH_SUBJECTS: classify(caption text) -> bucket,
    # order, share). Tags come from caption sidecars (read, not guessed). Without one, every candidate is one bucket.
    subjects = extension_hook("PUBLISH_SUBJECTS") or {"classify": lambda text: "other", "order": ["other"],
                                                       "share": {"other": 1.0}}

    def _subject(r):
        cap_path = os.path.join(r.get("_dir") or "", "train",
                                (r.get("file") or "").replace(".jpg", ".txt"))
        try:
            with open(cap_path, encoding="utf-8", errors="replace") as fh:
                t = fh.read().lower()
        except Exception:
            return "other"
        return subjects["classify"](t)

    for r in rows:
        r["_subject"] = _subject(r)

    # Only include explicitly listed files (inverse of exclude_files).
    # Applied first so subsequent filters narrow within this subset.
    only = set(spec.get("include_files") or [])
    if only:
        before = len(rows)
        rows = [r for r in rows if (r.get("file") or "") in only]
        job.log("publish: include_files %d named: %d -> %d" % (len(only), before, len(rows)))

    # Exclude already-judged files so "next batch" returns new items.
    seen = set(spec.get("exclude_files") or [])
    if seen:
        before = len(rows)
        rows = [r for r in rows if (r.get("file") or "") not in seen]
        job.log("publish: skipping %d already-judged: %d -> %d" % (len(seen), before, len(rows)))

    def _cap_text(r):
        p = os.path.join(r.get("_dir") or "", "train",
                         (r.get("file") or "").replace(".jpg", ".txt"))
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                return fh.read().lower()
        except Exception:
            return ""

    want_cap = [str(x).lower() for x in (spec.get("caption_any") or []) if str(x).strip()]
    if want_cap:
        before = len(rows)
        rows = [r for r in rows if any(w in _cap_text(r) for w in want_cap)]
        job.log("publish: caption filter %s: %d -> %d" % (want_cap[:4], before, len(rows)))

    want_word = [str(x).lower() for x in (spec.get("caption_any_word") or []) if str(x).strip()]
    if want_word:
        import re as _re
        rx = _re.compile(r"\b(?:%s)\b" % "|".join(_re.escape(w) for w in want_word), _re.I)
        before = len(rows)
        rows = [r for r in rows if rx.search(_cap_text(r))]
        job.log("publish: caption_any_word %s: %d -> %d" % (want_word[:4], before, len(rows)))

    # caption_none is what makes "the ones we could NOT bucket" expressible: the museum's subject
    # tags are the only trustworthy signal for who an image depicts, and inferring sex from prose has
    # a substring trap in it ("man" inside "woman"). Everything without a tag goes to a human.
    drop_cap = [str(x).lower() for x in (spec.get("caption_none") or []) if str(x).strip()]
    if drop_cap:
        before = len(rows)
        rows = [r for r in rows if not any(w in _cap_text(r) for w in drop_cap)]
        job.log("publish: caption_none %s: %d -> %d" % (drop_cap[:4], before, len(rows)))

    # --- pick for VARIETY within each bucket -------------------------------------------------
    # Farthest-point selection on the perceptual hash: after each pick, take the candidate that is
    # LEAST like everything already chosen. Score still gates entry (min_score) but no longer
    # dictates order, because 240 high-scoring near-identical etchings is a worse review set than
    # 240 varied ones. Falls back to the plain interleave if hashes cannot be computed.
    def _diverse(pool, want):
        if want <= 0 or not pool:
            return []
        hashes = {}
        for r in pool:
            p = os.path.join(r.get("_dir") or "", "train", r.get("file") or "")
            try:
                hashes[id(r)] = _dhash(p)
            except Exception:
                hashes[id(r)] = None
        ordered = sorted(pool, key=lambda r: (-(r.get("keep_score") or 0), r.get("file") or ""))
        if not any(hashes.values()):
            return ordered[:want]
        chosen = [ordered[0]]
        remaining = ordered[1:]
        mind = {}
        for r in remaining:
            a, b = hashes.get(id(r)), hashes.get(id(chosen[0]))
            mind[id(r)] = _hamming(a, b) if (a is not None and b is not None) else 999
        while remaining and len(chosen) < want:
            best = max(remaining, key=lambda r: (mind.get(id(r), 0), -(r.get("keep_score") or 0)))
            remaining.remove(best)
            chosen.append(best)
            hb = hashes.get(id(best))
            for r in remaining:
                a = hashes.get(id(r))
                if a is not None and hb is not None:
                    d = _hamming(a, hb)
                    if d < mind.get(id(r), 999):
                        mind[id(r)] = d
        return chosen

    order = spec.get("subject_order") or subjects["order"]
    share = spec.get("subject_share") or subjects["share"]
    picked = []
    for bucket in order:
        pool = [r for r in rows if r.get("_subject") == bucket]
        want = int(limit * float(share.get(bucket, 0)))
        got = _diverse(pool, want)
        picked.extend(got)
        job.log("publish: %-6s pool %4d -> picked %3d" % (bucket, len(pool), len(got)))
    # Top up from whatever is left if a bucket was thinner than its share.
    if len(picked) < limit:
        taken = {id(r) for r in picked}
        rest = [r for r in rows if id(r) not in taken]
        picked.extend(_diverse(rest, limit - len(picked)))
    rows = picked[:limit]
    job.log("publish: %d selected (duplicates removed, by subject, variety-ordered)" % len(rows))

    job.log("publish: %d candidates at keep_score >= %d (thumb %dpx) -> %s"
            % (len(rows), min_score, thumb_px, dest))

    # Clear only OUR previous batch, so a re-publish doesn't accumulate stale candidates and never
    # touches images belonging to anything else in that folder.
    for old in glob.glob(os.path.join(dest, "cand_*.png")):
        try:
            os.remove(old)
        except OSError:
            pass

    manifest, made = [], 0
    for i, r in enumerate(rows, 1):
        src = os.path.join(r["_dir"], "train", r["file"])
        if not os.path.exists(src):
            continue
        name = "cand_%03d.png" % i
        try:
            im = Image.open(src)
            im.load()
            if im.mode != "RGB":
                im = im.convert("RGB")
            if max(im.size) > thumb_px:
                sc = thumb_px / float(max(im.size))
                im = im.resize((max(1, int(im.size[0] * sc)), max(1, int(im.size[1] * sc))),
                               Image.LANCZOS)
            im.save(os.path.join(dest, name), "PNG", optimize=True)
        except Exception as exc:
            job.log("  thumb failed %s: %s" % (r["file"], str(exc)[:60]))
            continue
        cap = ""
        tpath = os.path.join(r["_dir"], "train", r["file"].replace(".jpg", ".txt"))
        try:
            with open(tpath, encoding="utf-8") as fh:
                cap = fh.read().strip()[:150]
        except OSError:
            pass
        manifest.append({"n": i, "image": name, "user": user, "set": r.get("set"),
                         "source_file": r.get("file"), "keep_score": r.get("keep_score"),
                         "figure_count": r.get("figure_count"), "faults": r.get("faults"),
                         "caption": cap})
        made += 1

    mpath = os.path.join(os.path.dirname(images_root.rstrip("\\/")), "dataset-candidates.json")
    tmp = mpath + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"generated": _now(), "user": user, "min_score": min_score,
                   "count": made, "candidates": manifest}, fh, indent=1)
    os.replace(tmp, mpath)
    job.log("publish: %d thumbnails written, manifest %s" % (made, mpath))
    job.publish("done", "%d candidates published for chat" % made, done=True, ok=True,
                published=made, manifest=mpath,
                summary=("Published %d top-scoring candidates as PNG thumbnails HAI can show inline "
                         "in chat (keep_score >= %d). Datasets untouched — these are copies."
                         % (made, min_score)))
    job.log("DONE (publish) count=%d" % made)
    return 0


def _write_shortlist(job, set_dir, scored):
    """A contact sheet: thumbnail, score and the model's reason, best first.

    Reviewing 1400 folders of JPEGs is not a thing anyone will actually do; a single scrollable page
    with the score and the WHY next to each image is.
    """
    rows = sorted(scored.values(), key=lambda r: (-(r.get("keep_score") or 0), r.get("file") or ""))
    buckets = {}
    for r in rows:
        buckets.setdefault(r.get("keep_score"), []).append(r)
    parts = ["<!doctype html><meta charset='utf-8'><title>%s — candidates</title>"
             % os.path.basename(set_dir),
             "<style>body{background:#141414;color:#eee;font:14px system-ui;margin:24px}"
             "h2{margin:28px 0 8px;border-bottom:1px solid #444;padding-bottom:4px}"
             ".g{display:flex;flex-wrap:wrap;gap:10px}"
             ".c{width:200px;background:#1e1e1e;border-radius:6px;padding:6px}"
             ".c img{width:100%;height:170px;object-fit:contain;background:#000;border-radius:4px}"
             ".m{font-size:11px;color:#aaa;margin-top:4px;line-height:1.35}"
             ".s{font-weight:700;color:#7fd67f}</style>",
             "<h1>%s — %d scored</h1>" % (os.path.basename(set_dir), len(rows)),
             "<p style='color:#aaa'>Best first. Score is the VLM's, not a decision — nothing here "
             "has been moved or deleted. Full scores in <code>_scores.jsonl</code>.</p>"]
    for score in sorted(buckets, key=lambda s: -(s or 0)):
        group = buckets[score]
        parts.append("<h2>keep_score %s — %d images</h2><div class='g'>" % (score, len(group)))
        for r in group:
            parts.append(
                "<div class='c'><img loading='lazy' src='train/%s'>"
                "<div class='m'><span class='s'>%s</span> · figs %s · %s · %s<br>%s</div></div>"
                % (r["file"], r.get("keep_score"), r.get("figure_count"), r.get("frame_share"),
                   r.get("clutter"), (r.get("faults") or "")[:90]))
        parts.append("</div>")
    dest = os.path.join(set_dir, "_shortlist.html")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    job.log("shortlist written: %s (%d images)" % (dest, len(rows)))


def _dir_size_gb(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total / (1024.0 ** 3)


def _drain_stage(job, stage_dir, dest_dir, name_ok=None):
    """Move completed files from SSD cache to array.

    Files are moved sequentially (not interleaved with downloads) to prevent array fragmentation.

    Args:
        name_ok: Predicate to verify file ownership/validity. Must be supplied by caller;
            defaults to digit-stem rule if None."""
    import shutil
    os.makedirs(dest_dir, exist_ok=True)
    moved, bytes_moved = 0, 0
    if not os.path.isdir(stage_dir):
        return 0
    for fn in sorted(os.listdir(stage_dir)):
        src_p = os.path.join(stage_dir, fn)
        if not os.path.isfile(src_p) or fn.endswith(".part"):
            continue
        # Move only archived originals. Never relocate files not created by this run (e.g., survey.json).
        ok = name_ok or (lambda n: os.path.splitext(n)[0].isdigit())
        if not ok(fn):
            continue
        dst_p = os.path.join(dest_dir, fn)
        try:
            size = os.path.getsize(src_p)
            if os.path.exists(dst_p) and os.path.getsize(dst_p) == size:
                os.remove(src_p)              # already archived; drop the duplicate
                continue
            shutil.move(src_p, dst_p)
            moved += 1
            bytes_moved += size
        except OSError as exc:
            job.log("  drain failed for %s: %s" % (fn, str(exc)[:80]))
    if moved:
        job.log("drained %d files (%.2f GB) -> %s" % (moved, bytes_moved / 1e9, dest_dir))
    return moved


def _priority_ids(job, spec):
    """Return object ids that must be downloaded first, to unblock training runs.

        Priority items are fetched before long-tail fill, ensuring that if a download
        is interrupted or resumed, the critical assets required for immediate use are
        available while less urgent items can wait.

        Args:
            job: Job instance for logging.
            spec: Configuration dict containing 'priority_ids' (explicit list) and/or
                'priority_manifest' (path to a manifest file listing set ids).
    """
    out, seen = [], set()
    for raw in (spec.get("priority_ids") or []):
        s = str(raw).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    for mpath in ([spec.get("priority_manifest")] if spec.get("priority_manifest") else []):
        try:
            with open(mpath, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split("\t")
                    if len(parts) > 1 and parts[1].strip().isdigit():
                        s = parts[1].strip()
                        if s not in seen:
                            seen.add(s)
                            out.append(s)
            job.log("priority: read %d ids from %s" % (len(out), mpath))
        except OSError as exc:
            # Not fatal: a missing priority list must never stop the archive itself. Say so loudly
            # instead, because silently downloading in the wrong ORDER is invisible in the result.
            job.log("  ⚠ priority manifest %r unreadable (%s) — continuing in catalogue order"
                    % (mpath, str(exc)[:70]))
    return out


def archive_originals(job, rows, stage_dir, dest_dir, src=None):
    """Download full-resolution originals into `dest_dir` via a bounded SSD cache.

    Idempotent and resumable: skips files already present in the archive.
    This avoids re-fetching ~200k images (9+ hours) and ensures future
    derivations are local, offline, and unrestricted by rate limits."""
    import requests
    src = src or {}
    # Originals stage in their OWN subdirectory, so the drain can only ever see files this function
    # put there — belt and braces with the digit-name check in _drain_stage.
    stage_dir = os.path.join(stage_dir, "_orig")
    os.makedirs(stage_dir, exist_ok=True)
    os.makedirs(dest_dir, exist_ok=True)
    img_rate = float(src.get("image_rate_per_sec") or 6)
    threads = max(1, min(int(src.get("dl_threads") or 6), DL_THREADS))
    pool = _SessionPool(64)
    gate, last = threading.Lock(), [0.0]
    lock = threading.Lock()
    done, kept, skipped, failed = [0], [0], [0], []
    manifest_path = os.path.join(dest_dir, "_originals_manifest.tsv")
    have = set()
    for fn in os.listdir(dest_dir):
        stem = os.path.splitext(fn)[0]
        if stem.isdigit():
            have.add(stem)
    job.log("archive: %d originals already on the array, %d rows selected" % (len(have), len(rows)))

    def _pace():
        if img_rate <= 0:
            return
        with gate:
            interval = 1.0 / img_rate
            wait = last[0] + interval - time.time()
            if wait > 0:
                time.sleep(wait)
            last[0] = max(time.time(), last[0] + interval)

    def one(row):
        oid = str(row["id"])
        if oid in have:
            with lock:
                skipped[0] += 1
                done[0] += 1
            return None
        url = row["url"]
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif"):
            ext = ".jpg"
        part = os.path.join(stage_dir, oid + ext + ".part")
        final = os.path.join(stage_dir, oid + ext)
        err = None
        for attempt in range(2):
            try:
                _pace()
                r = pool.get().get(url, timeout=(API_CONNECT_TIMEOUT, HTTP_TIMEOUT), stream=True)
                if r.status_code != 200:
                    err = "http %s" % r.status_code
                    if r.status_code in (403, 429):
                        time.sleep(2.0)
                        pool.drop()
                        continue
                    break
                n = 0
                with open(part, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                        n += len(chunk)
                os.replace(part, final)
                with lock:
                    kept[0] += 1
                    done[0] += 1
                return (oid, n, row)
            except Exception as exc:
                err = "%s" % type(exc).__name__
                pool.drop()
                if attempt == 0:
                    time.sleep(1.0)
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass
        with lock:
            failed.append("%s %s" % (oid, err or "unknown"))
            done[0] += 1
        return None

    t0 = time.time()
    lines = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as workers:
            for res in workers.map(one, rows):
                if res:
                    oid, n, row = res
                    lines.append("%s\t%d\t%s\t%s\t%s"
                                 % (oid, n, row.get("license", ""), row.get("source", ""),
                                    row.get("url", "")))
                d = done[0]
                if d % 50 == 0 or d == len(rows):
                    # Drain whenever the cache passes its high-water mark. The SSD therefore holds
                    # a working window, never the corpus — which is the whole point.
                    if _dir_size_gb(stage_dir) >= STAGE_HIGH_WATER_GB:
                        _drain_stage(job, stage_dir, dest_dir)
                    if lines:
                        with open(manifest_path, "a", encoding="utf-8") as fh:
                            fh.write("\n".join(lines) + "\n")
                        lines = []
                    rate = d / max(0.001, time.time() - t0)
                    left = int((len(rows) - d) / max(0.001, rate))
                    job.publish("archiving", "%d/%d originals (%d new, %d already held)"
                                % (d, len(rows), kept[0], skipped[0]),
                                images_done=d, images_total=len(rows),
                                eta="~%d min left" % max(0, left // 60))
    finally:
        pool.close()
        if lines:
            with open(manifest_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        _drain_stage(job, stage_dir, dest_dir)      # always leave the cache empty
    job.log("archive: %d new originals, %d already held, %d failed, in %.0f min"
            % (kept[0], skipped[0], len(failed), (time.time() - t0) / 60))
    if failed:
        with open(os.path.join(dest_dir, "_failed.txt"), "a", encoding="utf-8") as fh:
            fh.write("\n".join(failed) + "\n")
    return kept[0], skipped[0], failed


def archive(job, out_dir, dest_root, name):
    """Copy a finished set to the long-term array, keeping the SSD working copy.

    Non-fatal: failure to reach the remote array does not strand local work;
    errors are recorded in state files for retry rather than failing the job.

    Verifies copies by name+size per file; trusting presence alone risks
    silent corruption from incomplete transfers."""
    import shutil
    dest = os.path.join(dest_root, name)
    copied = total = 0
    for root, _dirs, files in os.walk(out_dir):
        rel = os.path.relpath(root, out_dir)
        target = os.path.join(dest, rel) if rel != "." else dest
        os.makedirs(target, exist_ok=True)
        for fn in files:
            src = os.path.join(root, fn)
            shutil.copy2(src, os.path.join(target, fn))
            copied += 1
            total += os.path.getsize(src)
    missing = []
    for root, _dirs, files in os.walk(out_dir):
        rel = os.path.relpath(root, out_dir)
        target = os.path.join(dest, rel) if rel != "." else dest
        for fn in files:
            a, b = os.path.join(root, fn), os.path.join(target, fn)
            if not os.path.exists(b) or os.path.getsize(a) != os.path.getsize(b):
                missing.append(fn)
    if missing:
        raise OSError("%d of %d files did not land intact (e.g. %s)"
                      % (len(missing), copied, ", ".join(missing[:3])))
    job.log("archived %d files (%.1f MB) -> %s" % (copied, total / 1e6, dest))
    return dest, copied, total


def _dry_run(job, spec, table, shards, src):
    """Estimate filter match count and stats without downloading images."""
    limit = max(1, min(int(spec.get("limit") or 200), MAX_IMAGES))
    probe = select(job, table, spec.get("filters") or {}, MAX_IMAGES, src)
    by_src, by_lic = {}, {}
    for r in probe:
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
        by_lic[r["license"]] = by_lic.get(r["license"], 0) + 1
    shorts = sorted(min(r["width"], r["height"]) for r in probe) or [0]
    median = shorts[len(shorts) // 2]
    top = sorted(by_src.items(), key=lambda kv: -kv[1])[:10]
    # A source whose manifest omits dimensions cannot report a median — saying "0px" would read as
    # "these are all tiny" rather than "not published until download".
    res_note = ("Median short side %dpx. " % median) if src.get("dims_known") else \
               ("Dimensions not published by this source; the %dpx floor is enforced after download. "
                % int((spec.get("filters") or {}).get("min_short_side") or 0))
    # Captions are here to be EYE-CHECKED, so spread the sample across the whole pool. The first
    # five rows are five consecutive objects from one search and read as one homogeneous theme —
    # which is exactly the impression a curator must not be given about a 2000-row mixed pool.
    step = max(1, len(probe) // 12)
    spread = [probe[i]["caption"][:140] for i in range(0, len(probe), step)][:12]
    # A scan that could not read part of its corpus produces a floor, not a count. Say so IN the
    # summary a human or HAI will read, not only in a log line nobody opens.
    stats = getattr(job, "fetch_stats", None) or {}
    unread = int(stats.get("unread") or 0)
    caveat = ""
    if unread:
        caveat = ("INCOMPLETE SCAN: %d of %d objects could not be read (%s), so %d is a FLOOR and "
                  "the mix below is drawn from the rest. " %
                  (unread, stats.get("scanned") or 0,
                   ", ".join("%s x%d" % kv for kv in
                             sorted((stats.get("kinds") or {}).items(), key=lambda kv: -kv[1])[:4])
                   or "no detail", len(probe)))
    job.publish("done", "%d rows match (would take %d)%s"
                % (len(probe), min(len(probe), limit), " — INCOMPLETE SCAN" if unread else ""),
                done=True, ok=True, dry_run=True,
                matched=len(probe), would_take=min(len(probe), limit),
                scan_unread=unread, scan_total=(stats.get("scanned") or None),
                scan_complete=(not unread),
                match_sources=[{"value": k, "rows": v} for k, v in top],
                match_licenses=[{"value": k, "rows": v} for k, v in
                                sorted(by_lic.items(), key=lambda kv: -kv[1])[:5]],
                match_short_side_median=(median if src.get("dims_known") else None),
                sample_captions=spread,
                summary=("DRY RUN — %s%d of %d manifest rows match; would take %d. %sTop: %s. "
                         "Nothing downloaded."
                         % (caveat, len(probe), table.num_rows, min(len(probe), limit), res_note,
                            ", ".join("%s %d" % (k[:26], v) for k, v in top[:5]) or "nothing")))
    job.log("DRY RUN matched=%d unread=%d" % (len(probe), unread))
    return 0


CORPUS_MODES = ("survey", "acquire", "archive")      # the modes that read a licensed corpus's manifest


def extension_modes():
    """{mode: {"starting": spec -> the job's starting line, "run": (job, spec) -> exit code}} from every module beside
    this file named p20_dataset_ext_<name>.py (its MODES). A factory without such a file has no such mode. Loaded when
    a job starts, after this module is complete, so an extension can read this module's shared machinery."""
    modes = {}
    for ext in _extensions():
        modes.update(getattr(ext, "MODES", {}))
    return modes


def _extensions():
    """The extension modules beside this file, in name order."""
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    return [importlib.import_module(os.path.splitext(os.path.basename(p))[0])
            for p in sorted(glob.glob(os.path.join(here, "p20_dataset_ext_*.py")))]


def extension_hook(name, default=None):
    """The first extension's attribute `name`, or `default` when no extension supplies it."""
    for ext in _extensions():
        if hasattr(ext, name):
            return getattr(ext, name)
    return default


def main(spec_path):
    os.makedirs(JOBS_DIR, exist_ok=True)
    job = Job(spec_path)
    hold_taken = False
    try:
        if not os.path.exists(KEEPAWAKE_HOLD):
            os.makedirs(os.path.dirname(KEEPAWAKE_HOLD), exist_ok=True)
            with open(KEEPAWAKE_HOLD, "w", encoding="utf-8") as f:
                f.write("p20_dataset %s\n" % job.name)
            hold_taken = True
        spec = job.spec
        mode = spec.get("mode", "survey")
        if mode == "collect":
            # Softserve PULLS finished sets off Mothership onto the array it owns locally.
            job.publish("starting", "collecting finished sets onto the array")
            return collect_sets(job, spec)
        if mode == "dedupe":
            # Perceptual-hashes finished sets and REPORTS duplicate groups. Reads only; nothing is
            # moved or deleted, because a grouping this cheap to recompute must never cost originals.
            job.publish("starting", "hashing images to find duplicates")
            return dedupe(job, spec)
        if mode == "publish":
            # Reads finished score files; writes thumbnails into the chat images dir.
            job.publish("starting", "publishing candidates for chat")
            return publish_candidates(job, spec)
        ext = extension_modes().get(mode)
        if ext:
            # A mode an extension module adds. It returns before the corpus path, which reads licensed manifests only.
            job.publish("starting", ext["starting"](spec))
            return ext["run"](job, spec)
        if mode == "curate":
            # Reads finished sets from disk; no corpus, no manifest, no downloads.
            job.publish("starting", "curating %s"
                        % ", ".join(os.path.basename(d) for d in
                                    (spec.get("sets") or [spec.get("set_dir") or ""])))
            return curate(job, spec)
        if mode not in CORPUS_MODES:
            raise ValueError("unknown mode %r: refused, never coerced into a corpus read" % (mode,))
        src = SOURCES.get(spec.get("source", "pd12m"))
        if not src:
            raise ValueError("unknown source %r — known: %s"
                             % (spec.get("source"), ", ".join(SOURCES)))
        out_dir = spec["out_dir"]
        scratch = os.path.join(SCRATCH_ROOT, job.name)
        shards = max(1, min(int(spec.get("shards") or 1), MAX_SHARDS, src["shards"]))
        free = scratch_free_gb()
        if free < MIN_SCRATCH_FREE_GB:
            raise OSError("only %.1f GB free on the NVMe scratch (%s), floor is %d GB — refusing to "
                          "start rather than crowd the training working set"
                          % (free, SCRATCH_ROOT, MIN_SCRATCH_FREE_GB))
        # Scratch is transient by definition: clear any debris a previous failed run left, so a retry
        # under the same name cannot silently mix two partial downloads into one set.
        if os.path.isdir(scratch):
            import shutil as _sh
            _sh.rmtree(scratch, ignore_errors=True)
            job.log("cleared stale scratch: %s" % scratch)
        job.publish("starting", "%s from %s" % (mode, spec.get("source")))
        job.log("=== %s | mode=%s source=%s shards=%d scratch=%s hold=%s (%.0f GB free on scratch)"
                % (job.name, mode, spec.get("source"), shards, scratch, out_dir, free))

        # Manifests are downloads, so they cache on the NVMe with everything else we pull.
        cache = os.path.join(SCRATCH_ROOT, "_manifest_cache")
        if src.get("kind") == "met_api":
            table = _fetch_met_manifest(job, spec, os.path.join(cache, "met"))
        elif src.get("kind") == "bulk_table":
            # Cache dir is per-source, EXCEPT that met_bulk deliberately shares met's object cache:
            # same museum, same object records, so whichever fetched them first the other gets free.
            _cd = "met" if spec.get("source") in ("met", "met_bulk") else str(spec.get("source"))
            table = _fetch_bulk_manifest(job, spec, os.path.join(cache, _cd), src)
        else:
            table = fetch_manifest(job, src, shards, cache)
        job.log("manifest rows: %d" % table.num_rows)

        # Dry runs skip the corpus survey (expensive tally/sort) and compute match stats locally.
        if mode != "survey" and spec.get("dry_run"):
            return _dry_run(job, spec, table, shards, src)

        job.publish("surveying", "%d manifest rows" % table.num_rows, manifest_rows=table.num_rows)
        # A survey is a few KB of JSON and no images, so it lands straight on the hold — there is
        # nothing to stage.
        stats, survey_path = survey(job, table, out_dir if mode == "survey" else scratch)
        top = ", ".join("%s %.1f%%" % (s["value"][:28], s["pct"]) for s in stats["sources"][:5])

        if mode == "survey":
            # The exact strings go IN the state file, not just survey.json on this box. Whoever reads
            # the status is the one about to write filters, and the whole point of the survey is that
            # guessing those values is what silently returns an empty dataset.
            job.publish("done", "surveyed %d rows; top sources: %s" % (table.num_rows, top),
                        survey=survey_path, done=True, ok=True,
                        top_sources=[{"value": s["value"], "pct": s["pct"]}
                                     for s in stats["sources"][:15]],
                        license_values=[{"value": s["value"], "pct": s["pct"]}
                                        for s in stats["licenses"][:6]],
                        resolution=stats["short_side_at_least"],
                        summary=("%d rows across %d shards. Sources: %s. %s%% at >=1024px short side."
                                 % (table.num_rows, shards, top,
                                    stats["short_side_at_least"]["1024"]["pct"])))
            job.log("DONE (survey)")
            return 0

        if mode == "archive":
            # The bulk tier: full-resolution originals onto the array, via the bounded SSD cache.
            # No resize, no captions, no training layout — this is raw material, and training sets
            # are derived from it later without touching the network.
            dest_root = (spec.get("archive_root") or ARCHIVE_ROOT or "").strip()
            if not dest_root:
                raise ValueError("mode=archive needs an archive_root (set it in dataset-paths.json "
                                 "on this host, or in the spec) — refusing to guess where 1+ TB of "
                                 "originals should land")
            rows = select(job, table, spec.get("filters") or {}, MAX_IMAGES, src)
            # Frontload what a training run is actually waiting on. sorted() is stable, so within
            # each group the catalogue order is preserved and the pull stays reproducible.
            prio = set(_priority_ids(job, spec))
            if prio:
                rows.sort(key=lambda r: str(r["id"]) not in prio)
                hit = sum(1 for r in rows if str(r["id"]) in prio)
                job.log("priority: %d of %d selected rows will be fetched first (%d priority ids "
                        "supplied, %d not in this selection)"
                        % (hit, len(rows), len(prio), len(prio) - hit))
            dest = os.path.join(dest_root, "_originals", str(spec.get("source") or "src"))
            job.log("archive mode: %d rows selected -> %s (stage %s, high water %.0f GB)"
                    % (len(rows), dest, scratch, STAGE_HIGH_WATER_GB))
            new, held, failed = archive_originals(job, rows, scratch, dest, src)
            job.publish("done", "%d new originals archived (%d already held, %d failed)"
                        % (new, held, len(failed)),
                        done=True, ok=True, originals_new=new, originals_held=held,
                        originals_failed=len(failed), archive_dir=dest,
                        summary=("Archived %d new full-resolution originals to %s; %d were already "
                                 "held and %d failed. Originals are the raw material — training "
                                 "sets are derived from these locally, with no further downloads."
                                 % (new, dest, held, len(failed))))
            job.log("DONE (archive) new=%d held=%d failed=%d" % (new, held, len(failed)))
            return 0

        limit = max(1, min(int(spec.get("limit") or 200), MAX_IMAGES))
        rows = select(job, table, spec.get("filters") or {}, limit, src)
        job.log("selected %d rows (limit %d)" % (len(rows), limit))
        if not rows:
            job.publish("done", "no rows matched the filters", done=True, ok=False,
                        summary=("Nothing matched. The manifest's exact source/licence values are in "
                                 "survey.json — filters match case-insensitive substrings of those."))
            return 0
        if os.path.isdir(os.path.join(out_dir, "train")) and os.listdir(os.path.join(out_dir, "train")):
            raise FileExistsError("%s\\train already has files — pick a new name rather than mixing "
                                  "two selections into one set" % out_dir)
        # TIER 1 — download to the NVMe scratch.
        job.publish("downloading", "0/%d images" % len(rows), images_total=len(rows),
                    scratch=scratch)
        kept, failed = acquire(job, rows, scratch, spec.get("prefix") or job.name.replace("-", "_"),
                               (spec.get("trigger") or "").strip(), int(spec.get("max_side") or 0),
                               min_short=int((spec.get("filters") or {}).get("min_short_side") or 0),
                               src=src)
        with open(os.path.join(scratch, "_spec.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1)

        # TIER 2 — move the finished set to the SSD hold. Only after a successful download, so a
        # failed run leaves its debris on scratch instead of half-populating the holding area.
        job.publish("promoting", "moving %d images to the SSD hold" % kept, images_kept=kept)
        promote(job, scratch, out_dir)

        # TIER 3 — the long-term copy on the array. The SSD hold stays put either way: "hold until
        # the array copy is confirmed" is the floor, and a set whose archive failed must still be
        # somewhere obvious rather than deleted on the strength of a copy that did not happen.
        archived, archive_note, archive_path = False, "", ""
        dest_root = (spec.get("archive_to") or "").strip()
        if dest_root and kept:
            job.publish("archiving", "copying %d files to the array" % kept,
                        images_kept=kept, images_failed=len(failed))
            try:
                archive_path, n, total = archive(job, out_dir, dest_root, job.name)
                archived = True
                archive_note = (" Archived %d files (%.0f MB) to %s; the SSD hold at %s stays as the "
                                "working copy." % (n, total / 1e6, archive_path, out_dir))
            except Exception as exc:
                archive_note = (" NOT archived to the array (%s) — the set is complete and HELD on "
                                "Mothership's SSD at %s, and the copy can be retried."
                                % (str(exc)[:120], out_dir))
                job.log("archive failed: %r" % (exc,))

        job.publish("done", "%d images kept, %d failed%s"
                    % (kept, len(failed), "" if archived else " (not archived)"),
                    done=True, ok=kept > 0, images_kept=kept, images_failed=len(failed),
                    train_dir=os.path.join(out_dir, "train"), survey=survey_path,
                    archived=archived, archive_path=archive_path,
                    summary=("%d images in %s\\train (caption .txt each, _manifest.txt has licence + "
                             "source URL per file). %d failed to fetch.%s"
                             % (kept, out_dir, len(failed), archive_note)))
        job.log("DONE kept=%d failed=%d archived=%s" % (kept, len(failed), archived))
        return 0
    except Exception as exc:
        job.log("FAILED: %r" % (exc,))
        # Name the scratch path in the state: whatever got downloaded before the failure is still
        # there, and "it failed" is a lot less useful than "it failed and the partial set is here".
        job.publish("failed", str(exc)[:300], done=True, ok=False, error=str(exc)[:300],
                    scratch=os.path.join(SCRATCH_ROOT, job.name))
        return 1
    finally:
        # A supervised browser must be closed no matter HOW the order ended — a leaked headed Edge
        # holds its profile lock and the next run cannot reuse the login. stash() parks it here
        # rather than owning a try/finally itself: the same lesson as clear_current_run(), which was
        # correct but lived inside run() and so was skipped by every early exit. Cleanup belongs
        # where the order ends by ANY route.
        _b = getattr(job, "_browser", None)
        if _b is not None:
            try:
                _b.close()
                job.log("browser closed")
            except Exception as exc:
                job.log("browser close failed: %s" % str(exc)[:120])
            job._browser = None
        if hold_taken:
            try:
                os.remove(KEEPAWAKE_HOLD)
            except OSError:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: p20_dataset.py <spec.json>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
