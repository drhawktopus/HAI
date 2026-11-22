"""The media add-on's route in the host-ops broker: a Recyclarr sync as a one-off container (the same run as the
compose `--profile tools run --rm recyclarr sync`). The image and command are fixed here; the request only chooses a
dry-run (`sync --preview`, the default the orchestrator sends) or the real sync. Loaded by
hostops_sidecar._load_oneoff_routes; the orchestrator's side is arr_tools.recyclarr_sync_preview."""
import os

RECYCLARR_IMAGE = os.getenv("RECYCLARR_IMAGE", "ghcr.io/recyclarr/recyclarr:latest")
# recyclarr's config folder, bound into the one-off: RECYCLARR_BIND, else <the compose folder>/recyclarr (DEPLOY_COMPOSE_DIR)
RECYCLARR_BIND = os.getenv("RECYCLARR_BIND") or ("%s/recyclarr:/config" % os.getenv("DEPLOY_COMPOSE_DIR") if os.getenv("DEPLOY_COMPOSE_DIR") else "")
RECYCLARR_USER = os.getenv("RECYCLARR_USER", "1000:1000")
RECYCLARR_TZ = os.getenv("RECYCLARR_TZ") or os.getenv("TZ") or "UTC"   # the lab's zone unless told otherwise


def recyclarr_sync(payload):
    dry_run = payload.get("preview") in (True, 1, "1", "true")
    return {"image": RECYCLARR_IMAGE, "cmd": ["sync", "--preview"] if dry_run else ["sync"],
            "binds": [RECYCLARR_BIND], "env": ["TZ=" + RECYCLARR_TZ], "user": RECYCLARR_USER,
            "result": {"dry_run": dry_run}}


ONEOFF_ROUTES = {"/recyclarr/sync": recyclarr_sync}
