"""
Manage persistent person identity records.

This module defines and handles storage for identity data (heritage hints, reference photos,
VLM trait consensus) and future identity-library fields (consent scope, trained models, arcface anchors).

Currently implements only schema definition and read/write access. Tier-gating, consent enforcement,
and model loading are deferred pending #20 integration; thus `model_tier` defaults to "swap" without branching logic.

Records are keyed by person ID (currently upload/chat user IDs). Stored in a separate JSON file from roster profiles.
"""
import copy
import datetime
import json
import pathlib

import config

# reserved tiers (identity depth) — the carrier/liveliness ladder; ONLY "swap" is live today
TIERS = ("none", "swap", "kontext", "lora")
# trained-model kinds (#20)
MODEL_KINDS = ("image_lora", "video_lora")
# consent use-categories the scope vocabulary can express (#20). "named" = an explicit allowlist.
CONSENT_USES = ("personal", "named", "public", "commercial")

# one trained-model entry (#20). Stored only — not yet loaded or gated on.
_MODEL_DEFAULT = {
    "kind": "image_lora",   # one of MODEL_KINDS
    "base": None,           # base model id, e.g. "flux2-dev" — drives which pipeline loads it
    "path": None,           # adapter file
    "trigger": None,        # orchestrator-side token (users never see it)
    "steps": None,
    "rank": None,
    "resolution": None,
    "manifest": None,       # training-set pointer
    "trained": None,        # ISO date
    "status": "training",   # training | ready | retired
    "quality": None,        # eval score (arcface vs anchor / eye-verified)
    "notes": "",
}

_DEFAULT = {
    "name": None,
    "heritage": "",          # #19 heritage channel — refines rendering STYLE, never overrides
                             # photo-measured color (e.g. "straight-to-wavy, not tightly coiled")
    "refs": [],              # [{"path": str, "quality": float|None}] — tagged reference photos
    "traits": {},            # persisted VLM trait-consensus (eyes/hair/texture/skin, ...)
    "consent": {             # #20: RESERVED shape (enforcement deferred). scope.uses ⊆ CONSENT_USES
        "granted": False,
        "scope": {
            "uses": [],          # allowed uses; [] = nothing granted
            "allowlist": [],     # named/approved people (governs the "named" use)
            "granted_by": None,  # who set it (ideally the person)
            "granted_date": None,
            "notes": "",
        },
    },
    "model_tier": "swap",    # RESERVED (#20 varies + gates on this; unused now)
    "models": [],            # #20: list of _MODEL_DEFAULT-shaped trained adapters (was `lora`)
    "arcface_anchor": None,  # #20: {"vec_path", "source_refs", "computed"} — likeness-meter ground truth
    "consent_history": [],   # append-only audit trail; enforcement reads 'consent', never this list
}


def _path():
    return pathlib.Path(getattr(config, "PERSON_RECORDS_FILE", None)
                        or (pathlib.Path(config.DOCS_DIR) / "State" / "person-records.json"))


def _load_all():
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(data):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def record(key):
    """The person's record merged over defaults (never KeyErrors; unknown -> all defaults).

    Nested `consent.scope` is always normalized to the full shape so callers can read
    scope keys even on partially-written legacy records.
    """
    rec = copy.deepcopy(_DEFAULT)
    stored = _load_all().get(key)
    if isinstance(stored, dict):
        rec.update(stored)
        c = stored.get("consent")
        if isinstance(c, dict):
            scope = dict(_DEFAULT["consent"]["scope"])
            if isinstance(c.get("scope"), dict):
                scope.update(c["scope"])
            rec["consent"] = {"granted": bool(c.get("granted", False)), "scope": scope}
    return rec


def update(key, **fields):
    """Merge fields into the person's record (creates it if absent). Returns the new record."""
    unknown = set(fields) - set(_DEFAULT)
    if unknown:
        raise KeyError(f"unknown person-record field(s): {sorted(unknown)}")
    data = _load_all()
    rec = data.get(key) if isinstance(data.get(key), dict) else {}
    rec.update(fields)
    data[key] = rec
    _save_all(data)
    return record(key)


# ── heritage channel (the one field #19 uses NOW) ────────────────────────────────────────
def get_heritage(key):
    """Stored heritage style-hint for this person, or '' if none."""
    return record(key).get("heritage", "") or ""


def set_heritage(key, text):
    return update(key, heritage=str(text or ""))


def model_tier(key):
    """RESERVED: identity-depth tier. Always 'swap' today — no caller branches on it yet."""
    t = record(key).get("model_tier", "swap")
    return t if t in TIERS else "swap"


# ── #20 identity-library fields (stored only; behavior deferred) ─────────────────────────
def add_model(key, **fields):
    """Append a trained-model entry (built from _MODEL_DEFAULT). Rejects unknown fields.

    Stored only — nothing loads/gates on it yet. Use for registering a trained LoRA into a
    person's record (e.g. a person's image LoRA when it finishes)."""
    unknown = set(fields) - set(_MODEL_DEFAULT)
    if unknown:
        raise KeyError(f"unknown model field(s): {sorted(unknown)}")
    if "kind" in fields and fields["kind"] not in MODEL_KINDS:
        raise ValueError(f"unknown model kind {fields['kind']!r}; expected one of {MODEL_KINDS}")
    entry = dict(_MODEL_DEFAULT)
    entry.update(fields)
    models = list(record(key).get("models") or [])
    models.append(entry)
    return update(key, models=models)


def set_arcface_anchor(key, vec_path, source_refs=None, computed=None):
    """Store the arcface anchor pointer (the likeness meter's ground truth). #20; stored only."""
    return update(key, arcface_anchor={
        "vec_path": str(vec_path),
        "source_refs": list(source_refs or []),
        "computed": computed,
    })


def grant_consent(key, uses, allowlist=None, granted_by=None, granted_date=None, notes=""):
    """Set the person's consent grant. Enforced by check_use (fail-closed) wherever a likeness is used.

    `uses` is a subset of CONSENT_USES; anything not listed is denied. `granted` is True iff any
    use is granted."""
    uses = list(uses or [])
    bad = set(uses) - set(CONSENT_USES)
    if bad:
        raise ValueError(f"unknown consent use(s): {sorted(bad)}; expected subset of {CONSENT_USES}")
    _append_consent_history(key, {
        "action": "grant", "by": granted_by, "date": granted_date or _utc_date(),
        "uses": uses, "allowlist": list(allowlist or []), "notes": notes,
    })
    return update(key, consent={
        "granted": bool(uses),
        "scope": {
            "uses": uses,
            "allowlist": list(allowlist or []),
            "granted_by": granted_by,
            "granted_date": granted_date,
            "notes": notes,
        },
    })


def _utc_date():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_consent_history(key, entry):
    history = list(record(key).get("consent_history") or [])
    history.append(entry)
    update(key, consent_history=history)


def revoke_consent(key, revoked_by=None, reason="", uses=None, remove_users=None,
                   revoked_date=None):
    """Revoke consent, wholly or partially.

    If ``uses`` and ``remove_users`` are omitted, all permissions are revoked
    (scope cleared, allowlist empty). Passing ``uses=[...]`` removes only those
    specific uses; ``remove_users=[...]`` removes specific allowlist entries;
    both arguments may be combined. Because enforcement re-reads the record
    on each check, revocation takes effect immediately without a restart or
    cache invalidation. All changes are appended to ``consent_history`` to
    preserve the audit trail."""
    if uses is not None:
        bad = set(uses) - set(CONSENT_USES)
        if bad:
            raise ValueError(f"unknown consent use(s): {sorted(bad)}; "
                             f"expected subset of {CONSENT_USES}")
    prior = record(key)["consent"]
    scope = prior["scope"]
    if uses is None and remove_users is None:       # full revocation
        new_uses, new_allow = [], []
    else:
        new_uses = [u for u in scope["uses"] if u not in set(uses or [])]
        new_allow = [a for a in scope["allowlist"] if a not in set(remove_users or [])]
    _append_consent_history(key, {
        "action": "revoke", "by": revoked_by, "date": revoked_date or _utc_date(),
        "reason": reason,
        "revoked_uses": sorted(set(scope["uses"]) - set(new_uses)),
        "removed_users": sorted(set(scope["allowlist"]) - set(new_allow)),
        "prior_scope": copy.deepcopy(scope),
    })
    return update(key, consent={
        "granted": bool(new_uses),
        "scope": {
            "uses": new_uses,
            "allowlist": new_allow,
            "granted_by": scope.get("granted_by"),
            "granted_date": scope.get("granted_date"),
            "notes": scope.get("notes", ""),
        },
    })


def resolve(name):
    """Return the canonical person KEY for a display name or key (e.g., 'Alex' -> 'alex'), else None.
    Performs casefold match on record keys or stored `name` fields."""
    if not name:
        return None
    n = str(name).strip().casefold()
    people = _load_all()
    if n in people:
        return n
    for k, rec in people.items():
        if isinstance(rec, dict) and str(rec.get("name", "")).strip().casefold() == n:
            return k
    return None


def check_use(key, use, requesting_user=None, kind=None):
    """Gate a likeness use. Returns (allowed: bool, reason: str).

    `use` ∈ CONSENT_USES. `requesting_user` is the AUTHENTICATED HAI display name (app.py's
    header identity — never a persona). `kind` (e.g. 'image_lora') additionally requires a
    READY model of that kind. An empty allowlist means the granted uses bind for any requester;
    a non-empty allowlist admits ONLY listed users (fail-closed when requesting_user is None)."""
    if use not in CONSENT_USES:
        return (False, "unknown use %r" % (use,))
    rec = record(key)
    c = rec["consent"]
    if not c["granted"]:
        return (False, "no consent granted for %r" % (key,))
    scope = c["scope"]
    if use not in scope["uses"]:
        return (False, "use %r not granted for %r" % (use, key))
    allow = list(scope.get("allowlist") or [])
    if allow and (requesting_user is None or requesting_user not in allow):
        return (False, "user %r not in allowlist for %r" % (requesting_user, key))
    if kind is not None:
        ready = [m for m in (rec.get("models") or [])
                 if m.get("kind") == kind and m.get("status") == "ready"]
        if not ready:
            return (False, "no ready %r model for %r" % (kind, key))
    return (True, "ok")


def usable_models(key, requesting_user=None, use="named"):
    """READY models the requesting user may invoke for this person ([] when denied)."""
    allowed, _ = check_use(key, use, requesting_user)
    if not allowed:
        return []
    return [m for m in (record(key).get("models") or []) if m.get("status") == "ready"]


def visible_people(requesting_user, use="named"):
    """Person keys whose likeness this user may invoke for `use` (drives any people-listing UI)."""
    return sorted(k for k in _load_all() if check_use(k, use, requesting_user)[0])

