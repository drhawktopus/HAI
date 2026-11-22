import json
import os
import zoneinfo
import sys

# ---- The settings file (plan step 2.1) ----------------------------------------------------------------------------
# One human-readable file of NAME=value lines (the format of the public settings.env.example and of a Docker env_file),
# read here before any setting below. Where: HAI_SETTINGS_FILE, else settings.env in the state root (HAI_STATE_DIR,
# else DOCS_DIR/State); those three come from the environment only, since the file cannot name its own place. No file
# is fine. THE ENVIRONMENT ALWAYS WINS: a name already set is left alone, so a lab that sets everything in compose
# behaves as before; every other name goes into the environment, so every os.getenv in the head (and in what it
# starts) reads it the same way. Blank lines and #-comments are skipped, `export NAME=value` is accepted, one pair of
# matching quotes around a value is removed. A line that is not NAME=value, a bad name or a name given twice stops the
# head with the file and line (a file read half-right is worse than one not read). A name the head never reads is not
# an error: settings_unknown() lists it and the head logs it at startup. Secrets do not go in this file; their place
# does: NAME_FILE=/path for any secret read through _secret_value.
import re as _re

_SETTING_NAME = _re.compile(r"^[A-Z_][A-Z0-9_]*$")


class SettingsError(ValueError):
    pass


def settings_path(environ=None):
    env = os.environ if environ is None else environ
    named = (env.get("HAI_SETTINGS_FILE") or "").strip()
    if named:
        return named
    state = env.get("HAI_STATE_DIR") or os.path.join(env.get("DOCS_DIR") or "/docs", "State")
    return os.path.join(state, "settings.env")


def parse_settings(text, where="settings file"):
    """{NAME: value} from a settings file's text, or SettingsError naming the line."""
    out, first = {}, {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, eq, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not eq:
            raise SettingsError("%s line %d is not NAME=value: %r" % (where, n, raw))
        if not _SETTING_NAME.match(name):
            raise SettingsError("%s line %d: %r is not a setting name (upper-case letters, digits and _)"
                                % (where, n, name))
        if name in out:
            raise SettingsError("%s line %d sets %s again (first set on line %d)" % (where, n, name, first[name]))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[name], first[name] = value, n
    return out


def load_settings(path=None, environ=None):
    """Read the settings file into the environment without overriding it. Returns what happened: the path, whether
    the file was there, the names it supplied and the names the environment already set."""
    env = os.environ if environ is None else environ
    path = path or settings_path(env)
    report = {"path": path, "found": False, "applied": [], "environment_won": []}
    try:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    except FileNotFoundError:
        return report
    report["found"] = True
    for name, value in parse_settings(text, path).items():
        if name in env:
            report["environment_won"].append(name)
        else:
            env[name] = value
            report["applied"].append(name)
    return report


def settings_known(root=None):
    """Every setting name the code beside this file reads: the constant first argument of each os.getenv,
    environ.get, environ[...] and _secret_value (and _secret_value's NAME_FILE) in its non-test files."""
    import ast
    root = root or os.path.dirname(os.path.abspath(__file__))
    names = set()
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in ("tests", "__pycache__", "backups", "node_modules")
                   and not x.startswith(".")]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                with open(os.path.join(d, f), encoding="utf-8-sig") as fh:
                    tree = ast.parse(fh.read())
            except (OSError, SyntaxError, ValueError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                    fn = getattr(node.func, "attr", getattr(node.func, "id", ""))
                    if fn in ("getenv", "get", "_secret_value"):
                        names.add(node.args[0].value)
                    if fn == "_secret_value" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        names.add(node.args[1].value)
                elif isinstance(node, ast.Subscript) and getattr(node.value, "attr", "") == "environ" \
                        and isinstance(node.slice, ast.Constant):
                    names.add(node.slice.value)
    return {n for n in names if isinstance(n, str) and _SETTING_NAME.match(n)}


def settings_unknown(report=None, root=None):
    """The names in the settings file that the code never reads (most likely a typo)."""
    report = SETTINGS if report is None else report
    known = settings_known(root) | {"HAI_SETTINGS_FILE"}
    return sorted(n for n in report.get("applied", []) + report.get("environment_won", []) if n not in known)


SETTINGS = load_settings()
# --------------------------------------------------------------------------------------------------------------------

DOCS_DIR = os.getenv("DOCS_DIR", "/docs")
STATE_DIR = os.getenv("HAI_STATE_DIR", os.path.join(os.getenv("DOCS_DIR", "/docs"), "State"))
# Every store the orchestrator WRITES roots here so one env moves them all (the deploy gate sets it).

# --- the device roster: the devices this head places work on --------------------------------------------------------
# One record per device, read from HAI_ROSTER_FILE (default `device-roster.json` under the state root): read HERE, once,
# so the settings below, the scheduler and Tolkien all read one copy. A role no device claims falls to the `brain` device,
# except the optional ones: `tolkien` (the box Tolkien runs on: The Tolkien box's factory) and `acquisition` (the box whose dataset agent
# builds sets into its own `dataset_root`); no device holds one = this head has none. No roster file at all: one device
# holds every GPU role, its
# model server at BRAIN_HOST. A roster file that is named but missing, unreadable or invalid stops the import: "could not
# read" is never "one device". Format: RUNBOOK-Orchestrator-Deploy-Procedure § "The device roster file".
ROSTER_ROLES = ("brain", "failover", "head", "advisor", "tolkien", "acquisition")
_GPU_ROLES = ("brain", "failover", "head", "advisor")
_OPTIONAL_ROLES = ("tolkien", "acquisition")
# "lead" is no longer a device role: the lead is a seat a model holds (the menu's crown), not a machine, and the
# machine it sits on is LEAD_BOX below. A roster that still lists it is read with the role ignored.
_RETIRED_ROLES = ("lead",)
ROSTER_FILE = os.getenv("HAI_ROSTER_FILE", "").strip() or os.path.join(STATE_DIR, "device-roster.json")
_CARD_FIELDS = ("vram_gb", "ram_gb", "cpu", "label", "gpu")


class RosterError(RuntimeError):
    """The roster file exists or was named, and cannot be used."""


def one_device_roster():
    """The roster of a head with no roster file: one device holding every GPU role, its model server at BRAIN_HOST. Its
    card sizes are unmeasured (0), so nothing is placed on it by a fit it was never measured to have."""
    return {"devices": [{"name": "local", "label": "This machine", "roles": list(_GPU_ROLES),
                         "url_setting": "BRAIN_HOST", "gpu": None, "vram_gb": 0.0, "ram_gb": 0.0,
                         "cpu": os.cpu_count() or 1, "chat_tier": 0, "measured": False}],
            "tablets": []}


def validate_roster(data):
    """The roster's records, checked: unique lowercase names, the card fields present, known roles, a `brain`, each
    role held at most once, a `parent` that names another device. Raises RosterError naming the first fault. (Whether a
    `url_setting` names a real setting is checked by the scheduler, once every setting exists.)"""
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list) or not data["devices"]:
        raise RosterError("a roster needs a non-empty `devices` list")
    names, held = set(), {}
    for d in data["devices"] + list(data.get("tablets") or []):
        name = d.get("name") if isinstance(d, dict) else None
        if not isinstance(name, str) or not name or name != name.lower() or name in names:
            raise RosterError("device name %r is missing, not lowercase or repeated" % (name,))
        names.add(name)
    for d in data["devices"]:
        card = "tolkien" not in (d.get("roles") or [])     # Tolkien's box: its GPU lane is reached through its desk
        missing = [k for k in (_CARD_FIELDS if card else ("label",)) if k not in d]
        if missing:
            raise RosterError("device %s lacks %s" % (d["name"], ", ".join(missing)))
        d["roles"] = [r for r in (d.get("roles") or []) if r not in _RETIRED_ROLES]
        for r in d["roles"]:
            if r not in ROSTER_ROLES:
                raise RosterError("device %s claims unknown role %r" % (d["name"], r))
            if r in held:
                raise RosterError("role %r is held by both %s and %s" % (r, held[r], d["name"]))
            held[r] = d["name"]
        parent = (d.get("flags") or {}).get("parent")
        if parent is not None and (parent not in names or parent == d["name"]):
            raise RosterError("device %s names parent %r, which is not another device" % (d["name"], parent))
    if "brain" not in held:
        raise RosterError("no device holds the `brain` role")
    return data


def number_devices(data, environ=None):
    """Give each device its settings prefix and apply its NAME setting. Physical machines are PCs, numbered by their
    `pc` field or, without one, in roster order (PC1, PC2, ...); a device whose `flags.parent` names another is a VM on
    that PC, numbered among its siblings (`vm` field, or order): prefix PC<n>_VM<m>. Every machine setting of a device is
    <prefix>_<KEY> (device_setting). <prefix>_NAME, when set, is the device's name (lowercased; its label is the value
    as typed), so a person's own machine names replace the roster's; the parent references follow the rename. Tablets
    are not numbered. Returns `data`, changed in place; a repeated number or a renamed collision raises RosterError."""
    env = os.environ if environ is None else environ
    devs = data["devices"]
    by_name = {d["name"]: d for d in devs}
    used, nxt = set(), 1
    for d in devs:
        if (d.get("flags") or {}).get("parent") is not None:
            continue
        n = d.get("pc")
        if n is None:
            while nxt in used:
                nxt += 1
            n = nxt
        if not isinstance(n, int) or n < 1 or n in used:
            raise RosterError("device %s has pc number %r, which is not a new positive whole number" % (d["name"], n))
        used.add(n)
        d["pc"], d["prefix"] = n, "PC%d" % n
    vms = {}
    for d in devs:
        parent = (d.get("flags") or {}).get("parent")
        if parent is None:
            continue
        host = by_name[parent]
        m = d.get("vm") or (len(vms.setdefault(parent, set())) + 1)
        if m in vms.setdefault(parent, set()):
            raise RosterError("device %s repeats VM number %r on %s" % (d["name"], m, parent))
        vms[parent].add(m)
        d["vm"], d["prefix"] = m, "%s_VM%d" % (host["prefix"], m)
    renamed = {}
    for d in devs:
        given = (env.get(d["prefix"] + "_NAME") or "").strip()
        if given and given.lower() != d["name"]:
            renamed[d["name"]] = given.lower()
        if given:
            d["label"] = given if given.lower() != d["name"] else d.get("label") or given
    if renamed:
        names = [renamed.get(d["name"], d["name"]) for d in devs] + [t["name"] for t in data.get("tablets") or []]
        if len(set(names)) != len(names):
            raise RosterError("the device NAME settings give two devices the same name: %s" % sorted(renamed.values()))
        for d in devs:
            d["name"] = renamed.get(d["name"], d["name"])
            flags = d.get("flags") or {}
            if flags.get("parent") in renamed:
                flags["parent"] = renamed[flags["parent"]]
    return data


def device_setting(name, key, default="", roster=None):
    """A machine setting of device `name`: the value of <its prefix>_<KEY> (PC1_MAC, PC3_VM1_GLANCES, ...), else
    `default`. A device not in the roster has no settings."""
    d = next((d for d in (roster or ROSTER)["devices"] if d["name"] == name), None)
    if not d or not d.get("prefix"):
        return default
    return os.getenv("%s_%s" % (d["prefix"], key), default)


def load_roster(path=None, named=None):
    """(roster, path it came from or None for the one-device roster). `named`: the file was asked for by name (default:
    a path was passed or HAI_ROSTER_FILE is set), so its absence is an error rather than "no roster"."""
    if named is None:
        named = path is not None or bool(os.getenv("HAI_ROSTER_FILE", "").strip())
    path = path or ROSTER_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        if named:
            raise RosterError("the roster file %s does not exist" % path)
        return number_devices(validate_roster(one_device_roster())), None
    except (OSError, ValueError) as exc:
        raise RosterError("the roster file %s cannot be read (%s: %s)" % (path, type(exc).__name__, exc))
    return number_devices(validate_roster(data)), path


ROSTER, ROSTER_SOURCE = load_roster()


def roster_role(role, roster=None):
    """The name of the device holding `role`; a GPU role no device claims falls to the `brain` device; an optional role
    (`tolkien`, `acquisition`) held by no device is None: that head has no such device."""
    if role in _RETIRED_ROLES:     # never a silent fall to the brain: the lead is a seat, its machine is LEAD_BOX
        raise RosterError("%r is not a device role (the lead is a seat): use config.LEAD_BOX" % role)
    devs = (roster or ROSTER)["devices"]
    for want in ((role,) if role in _OPTIONAL_ROLES else (role, "brain")):
        for d in devs:
            if want in (d.get("roles") or []):
                return d["name"]
    if role in _OPTIONAL_ROLES:
        return None
    raise RosterError("no device holds %r or `brain`" % role)


ACQUISITION_HOST = roster_role("acquisition")      # the dataset agent's box, or None: this head has none


def acquisition_set_root(roster=None):
    """Where the acquisition box's dataset agent puts a finished set: that record's `dataset_root` ("" when this head
    has no acquisition box, or its record names none)."""
    name = roster_role("acquisition", roster)
    d = next((d for d in (roster or ROSTER)["devices"] if d["name"] == name), {}) if name else {}
    return d.get("dataset_root") or ""


def roster_label(role, roster=None):
    """The display label of the device holding `role` (what a person reads: tiles, answers, messages)."""
    name = roster_role(role, roster)
    for d in (roster or ROSTER)["devices"]:
        if d["name"] == name:
            return d.get("label") or name
    return name


BRAIN_LABEL, FAILOVER_LABEL = (roster_label(r) for r in ("brain", "failover"))
ACQUISITION_LABEL = roster_label("acquisition") if roster_role("acquisition") else ""


def roster_url(name, roster=None):
    """The model-server address of the roster device `name` (its named setting's value, or its own `url`), or None.
    Call it after import: it reads the setting by name."""
    for d in (roster or ROSTER)["devices"]:
        if d["name"] == name:
            return globals().get(d["url_setting"]) if d.get("url_setting") else d.get("url")
    return None


def roster_box_at(setting, roster=None):
    """The name of the device whose model server is the address in `setting` (e.g. "HEAVY_HOST"), or None."""
    for d in (roster or ROSTER)["devices"]:
        if d.get("url_setting") == setting:
            return d["name"]
    return None
# #34 Phase 7: the placement table compiled from the priced menu (hard-bench\menu_policy.py). Read-only input to
# scheduler.place(family=...); missing or unreadable = today's ranking unchanged (fail-open). Its `contexts` section is
# also where every model's WINDOW comes from (below).
POLICY_TABLE = os.getenv("HAI_POLICY_TABLE", os.path.join(DOCS_DIR, "Datasets", "Project34-Menu", "policy.json"))


def reference_boxes(roster=None):
    """{device: reference box} for the devices that place from another box's measured menu rows until their own are
    measured: the device's `reference_box` roster field, else its <prefix>_REFERENCE_BOX setting (PC1_REFERENCE_BOX).
    The reference is the table's name for a box whose card resembles this one (in the lab's reference menu: mothership,
    bighonker, softserve). A device naming itself names nothing."""
    out = {}
    for d in (roster or ROSTER)["devices"]:
        ref = str(d.get("reference_box") or device_setting(d["name"], "REFERENCE_BOX", roster=roster) or "")
        ref = ref.strip().lower()
        if ref and ref != d["name"]:
            out[d["name"]] = ref
    return out


def _row_key(row):
    return (row.get("model"), str(row.get("think")), str(row.get("temperature")))


def apply_reference(data, roster=None):
    """The placement table as this head reads it. Each device that names a reference box (reference_boxes) gets a copy
    of that box's rows under its own name, for every (model, think, temperature) it has no row of its own for in that
    lens list, placed right after the original so each lens keeps its order; and that box's windows (`contexts`), memory
    profiles (`model_fits`), offload entries and tier homes, where the device has none of its own. A copied row carries
    `reference_box`, so whoever reads the placement can see whose measurement it is. The device's own rows win, model
    by model, as soon as the menu measures them. The crown (`roles`) is a decision about a box, not a measurement, and
    is not copied. A row for a model the device does not have is set aside by the weighing (scheduler.declares_model),
    as an order naming it is refused. Returns a new document and leaves `data` unchanged; with no reference named, or
    no readable table, `data` itself."""
    refs = reference_boxes(roster) if isinstance(data, dict) and data else {}
    if not refs:
        return data
    import copy
    out = copy.deepcopy(data)
    for fam in (out.get("families") or {}).values():
        for tier in ((fam.get("tiers") or {}) if isinstance(fam, dict) else {}).values():
            for lens, rows in (list(tier.items()) if isinstance(tier, dict) else ()):
                if not isinstance(rows, list):
                    continue
                own = {d: {_row_key(r) for r in rows if isinstance(r, dict) and r.get("box") == d} for d in refs}
                merged = []
                for r in rows:
                    merged.append(r)
                    for d, ref in refs.items():
                        if isinstance(r, dict) and r.get("box") == ref and _row_key(r) not in own[d]:
                            merged.append(dict(r, box=d, reference_box=ref))
                tier[lens] = merged
    for section in (out.get("contexts"), out.get("offload_ram"), (out.get("model_fits") or {}).get("hosts")):
        if isinstance(section, dict):
            for d, ref in refs.items():
                if isinstance(section.get(ref), dict):
                    section[d] = {**section[ref], **(section.get(d) or {})}
    if isinstance(out.get("offload_ok"), list):
        for d, ref in refs.items():
            for pair in list(out["offload_ok"]):
                if isinstance(pair, list) and len(pair) == 2 and pair[0] == ref and [d, pair[1]] not in out["offload_ok"]:
                    out["offload_ok"].append([d, pair[1]])
    for boxes in ((out.get("tier_homes") or {}).values() if isinstance(out.get("tier_homes"), dict) else ()):
        for d, ref in refs.items():
            if ref in boxes and d not in boxes:
                boxes.append(d)
    out["reference"] = dict(refs)
    return out


def load_policy(path=None, roster=None):
    """The placement table at `path` (default POLICY_TABLE) as this head reads it (apply_reference); {} when it cannot
    be read. Every reader of the table goes through this or applies apply_reference itself."""
    try:
        with open(path or POLICY_TABLE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return apply_reference(data, roster) if isinstance(data, dict) else {}


def device_label(name, roster=None):
    """What a person reads for device `name` (its label, else its name)."""
    d = next((d for d in (roster or ROSTER)["devices"] if d["name"] == name), {})
    return d.get("label") or name


def lead_box(roster=None, table=None):
    """The machine the lead seat sits on. The lead is a seat a model holds, the menu's crown (`roles.crown` in the
    placement table), so its machine is the crown's `box` when that is a card in the roster. With no readable crown
    (no menu yet, or the table cannot be read) it is the card with the most memory, the first in roster order on a
    tie: never "unknown", and on a one-device head the one device."""
    devs = (roster or ROSTER)["devices"]
    cards = [d for d in devs if "tolkien" not in d.get("roles", []) and float(d.get("vram_gb") or 0) > 0] or \
        [d for d in devs if "tolkien" not in d.get("roles", [])]
    try:
        box = ((load_policy(table, roster).get("roles") or {}).get("crown") or {}).get("box")
    except AttributeError:
        box = None
    if box in {d["name"] for d in cards}:
        return box
    return max(cards, key=lambda d: (float(d.get("vram_gb") or 0), -devs.index(d)))["name"]


LEAD_BOX = lead_box()
LEAD_BOX_LABEL = device_label(LEAD_BOX)


def _measured_contexts(path=None):
    """{box: {model: ctx}} - each model's largest measured fully-resident window per box, from the policy table's
    `contexts` section (menu_policy writes it from menu_fill.TESTED_CTX, the fit the menu itself runs at). Read ONCE at
    import, so the brain constants and the per-model table are one snapshot. {} when unreadable: the in-code table
    below is then the fallback, and hard-bench test_ctx_stores_agree.py keeps that table equal to the fit."""
    try:
        c = load_policy(path).get("contexts") or {}
        return {b: {m: int(v) for m, v in row.items()} for b, row in c.items() if isinstance(row, dict)}
    except Exception:
        return {}


MEASURED_CONTEXTS = _measured_contexts()

# --- Tier-1 brain (primary) ---
# Every box's address is a setting, never a literal: with no settings the head has no fleet. Placement, the
# context table and the claim store key a card by its URL, so three tiers sharing one URL ARE one box. With a
# roster each tier keeps its own placeholder until its setting names it; with NO roster (one device holding every
# role) the failover and heavy tiers default to BRAIN_HOST, so every tier resolves to that one model server.
BRAIN_HOST = os.getenv("BRAIN_HOST", "http://brain:11434")
_ONE_DEVICE = ROSTER_SOURCE is None
BRAIN_MODEL = os.getenv("BRAIN_MODEL", "")
NUM_CTX = int(os.getenv("NUM_CTX", "8192"))
# The brain box's 9B desktop-aware fit is 19,968 (campaigns/fit-20261019-desktop): 20,480 spills beside a
# 594 MiB desktop. The policy table is authoritative; this fallback and triage_local.json must agree with it.
# hard-bench test_ctx_stores_agree.py pins the stores so alternating chat and orders does not reload the model.
BRAIN_NUM_CTX = int(os.getenv("BRAIN_NUM_CTX", str(MEASURED_CONTEXTS.get(roster_box_at("BRAIN_HOST"), {}).get(BRAIN_MODEL, 19968))))

# --- Failover brain (second box) + availability gate ---
FAILOVER_HOST = os.getenv("FAILOVER_HOST", BRAIN_HOST if _ONE_DEVICE else "http://failover:11434")
FAILOVER_MODEL = os.getenv("FAILOVER_MODEL", "")
# Glances is asked only where a device's <prefix>_GLANCES is set (device_setting(name, "GLANCES")); an empty
# setting means the box reports no live metrics and placement falls back to its own minute report.
FAILOVER_MAX_GPU_MEM_PCT = float(os.getenv("FAILOVER_MAX_GPU_MEM_PCT", "20"))   # need ~6.5GB free
FAILOVER_MAX_GPU_UTIL_PCT = float(os.getenv("FAILOVER_MAX_GPU_UTIL_PCT", "50"))
FAILOVER_MAX_TRANSCODES = int(os.getenv("FAILOVER_MAX_TRANSCODES", "1"))         # tautulli fallback gate

# --- Heavyweight (the big local model server) ---
HEAVY_HOST = os.getenv("HEAVY_HOST", BRAIN_HOST if _ONE_DEVICE else "http://heavy:11434")
HEAVY_MODEL = os.getenv("HEAVY_MODEL", "")
# Measured fully-resident fit on the 7900 XTX: 125,952 (spill-aware re-fit on ollama 0.40.5, campaigns\fit-20261015 -
# menu_fill.TESTED_CTX); must equal the lead's triage_local row. (A1 / Terminal-Bench measurement arms keep their
# own pinned 122,880 for comparability with earlier arms.)
HEAVY_NUM_CTX = int(os.getenv("HEAVY_NUM_CTX", str(MEASURED_CONTEXTS.get(roster_box_at("HEAVY_HOST"), {}).get(HEAVY_MODEL, 125952))))


def judgment_requires():
    """What a judgment compute order (code-cleanup drafting, library synthesis, history review) asks the desk for: the
    box that serves the lead for ollama-served judgment work. The lead is a POSITION (the standing heavy model), read
    when the order is placed; a call site never names a model."""
    return ["order.compute", "model." + HEAVY_MODEL]


# Small-model fallback on the lead's machine (the legacy brain ladder). Used before the failover box, incl. Quick mode.
LEAD_BOX_BRAIN_MODEL = device_setting(LEAD_BOX, "BRAIN_MODEL", "")
# Wait for wake-on-LAN settle before polling; GPU host is slow to load models.
BRAIN_WAKE_WAIT = int(os.getenv("BRAIN_WAKE_WAIT", "20"))
BRAIN_WAKE_POLL = float(os.getenv("BRAIN_WAKE_POLL", "2"))
# Each model's standing context on each box = its measured fully-resident fit: the spill-aware re-fit on ollama 0.40.5
#, the same values as menu_fill.TESTED_CTX - hard-bench
# tests/test_ctx_stores_agree.py fails when this table, triage_local.json and the menu's table disagree.
# A call sizes its window from THIS table by model, so the model already on a card is never reloaded at a different
# size, and a smaller model never gets a window that spills it. The standing models come last so an env-overridden
# HEAVY/BRAIN model or context still wins. qwen3.6:35b-a3b keeps its older 84,992: the re-fit could not place it
# without spilling at any context and called "no longer fits" unproven.
MODEL_NUM_CTX = {}


def _add_ctx(host, table):
    """Merge one box's measured windows into the table. A head with one model server has every tier
    on the same host, so the tiers union into one entry instead of the last one overwriting the rest."""
    MODEL_NUM_CTX.setdefault(host, {}).update(table)


# Each device's measured windows are its roster record's `model_contexts` (the lab's measurements are data, never
# code; a served-by-Strata model carries its serving plan's whole trained context). The standing BRAIN/HEAVY entries
# follow each, as before.
def _roster_contexts(setting):
    name = roster_box_at(setting)
    return dict(next((d for d in ROSTER["devices"] if d["name"] == name), {}).get("model_contexts") or {})


_add_ctx(HEAVY_HOST, {**_roster_contexts("HEAVY_HOST"), **({HEAVY_MODEL: HEAVY_NUM_CTX} if HEAVY_MODEL else {})})
_add_ctx(BRAIN_HOST, {**_roster_contexts("BRAIN_HOST"), **({BRAIN_MODEL: BRAIN_NUM_CTX} if BRAIN_MODEL else {})})
_add_ctx(FAILOVER_HOST, _roster_contexts("FAILOVER_HOST"))
# The policy table's measured windows WIN over the in-code values above (which are only the fallback when the table
# cannot be read); entries the fit never measured (the Strata model, qwen3.6:35b-a3b) keep their in-code value; the
# standing BRAIN/HEAVY entries are re-applied last so an env-set model or context still wins.
for _setting in ("HEAVY_HOST", "BRAIN_HOST", "FAILOVER_HOST"):
    _add_ctx(globals()[_setting], MEASURED_CONTEXTS.get(roster_box_at(_setting), {}))
if HEAVY_MODEL:
    MODEL_NUM_CTX[HEAVY_HOST][HEAVY_MODEL] = HEAVY_NUM_CTX
MODEL_NUM_CTX[BRAIN_HOST][BRAIN_MODEL] = BRAIN_NUM_CTX


# Models a box serves through llama-swap (an engine ollama cannot run - Strata for Flash-Next), keyed by model: the
# box's ollama URL (placement, accounting and the silence watch stay keyed on it) -> its llama-swap base. A call for
# one of these speaks OpenAI chat/completions to llama-swap, which starts the engine on demand and unloads it after
# its ttl; a call for an ollama model on the same box first asks llama-swap to free the card. RUNBOOK-Ollama-Host-
# Operations § "Flash-Next through llama-swap".
# A box serves a model through llama-swap (an engine ollama cannot run) only when BOTH the model name
# and its base URL are configured; otherwise no model routes through a swap engine.
SWAP_MODEL = os.getenv("SWAP_MODEL", "").strip()
LEAD_BOX_SWAP = device_setting(LEAD_BOX, "SWAP").strip()
SWAP_MODELS = ({SWAP_MODEL: {"host": HEAVY_HOST, "base": LEAD_BOX_SWAP}}
               if SWAP_MODEL and LEAD_BOX_SWAP else {})


# The Tolkien box's chat window: an address only chat_window's door understands - a turn there is an OpenAI turn
# served through the Tolkien box's `chat.window` order on Tolkien. A loopback discard port, so a call that ever bypassed the door
# is refused at once (never a DNS wait - the gate's.invalid lesson) and the caller's fallback serves the turn.
TOLKIEN_WINDOW_URL = "http://127.0.0.1:9/tolkien-window"
# A pinned JOB's window on the Tolkien box's card is 49,152: measured on that card, the read and write rates hold
# from 32,768 through 49,152 and fall at 65,536, whose extra cache also costs the card ~770 MiB of room for layers.
# What a job does inside this window is the lever (ollama_client._compact_line, _coding_tools_after_open), not
# the window.
TOLKIEN_JOB_CTX = 49152
# THE RUNAWAY GUARD for a job on the Tolkien box: a turn that has THOUGHT this many tokens and called no tool is stopped, its
# thinking summarised with the conversation and the turn asked again (ollama_client: the same recovery as a turn that
# filled its window, only sooner). The A1 family has had this (34-A1 spec § Runaway guard, 20,480,
# measured there with thinking off); when A1's summary line crossed to this loop on 11-07 the guard did not, and nobody
# decided that
# Measured for THIS regime(`scratchpad\turn_gap_all.py`, the 13 saved runs on the 35B, thinking on): of 224
# turns that went on to call a tool none thought past 15,760 tokens (p99 11,881); the turns that ran away thought until
# the window ended, 24,667 to 30,212. At 20,480 it cuts 0 of the 224. It counts THINKING, not the whole reply: one real
# turn wrote 22,329 tokens because its tool call carried a whole file.
# The Tolkien box only. The GPU boxes have NO guard on purpose: their thinking models' real turns are as long as their runaways
# (qwen3.8:27b up to 46,111, at xhigh 71,288), so "any cap would cut real thinking (a sandbag)" (same spec).
TOLKIEN_RUNAWAY_THINK = 20480
# The card clock a box's chat WINDOW holds while it serves (p20_foundry `gpu_clock`: a number locks, {"floor": N} keeps
# at least N with the card's own boost above it): each roster device's `window_gpu_clock` (its `why` says why). A floor
# holds only while a window serves, so the card idles as it likes otherwise.
# JSON env override: HAI_WINDOW_GPU_CLOCK='{"<device>": {"floor": N}}'; '{}' turns it off.
WINDOW_GPU_CLOCK = (json.loads(os.environ["HAI_WINDOW_GPU_CLOCK"]) if os.getenv("HAI_WINDOW_GPU_CLOCK") is not None
                    else {d["name"]: d["window_gpu_clock"] for d in ROSTER["devices"] if "window_gpu_clock" in d})


def swap_base(host, model):
    """llama-swap's base URL when `model` on `host` is served through it, else None. The Tolkien box's window speaks the same
    OpenAI wire, so every model at TOLKIEN_WINDOW_URL is served "through" its own base."""
    if str(host or "").rstrip("/") == TOLKIEN_WINDOW_URL:
        return TOLKIEN_WINDOW_URL
    row = SWAP_MODELS.get(model)
    return row["base"] if row and str(host or "").rstrip("/") == row["host"].rstrip("/") else None


def swap_box_base(host):
    """llama-swap's base on `host` when it serves any model there, else None."""
    for row in SWAP_MODELS.values():
        if str(host or "").rstrip("/") == row["host"].rstrip("/"):
            return row["base"]
    return None


def ctx_for(host, model, default):
    """The standing context for `model` on `host`; `default` for a model the fit table has not measured there."""
    return MODEL_NUM_CTX.get(str(host or "").rstrip("/"), {}).get(model, default)


LEAD_BOX_BRAIN_NUM_CTX = int(device_setting(LEAD_BOX, "BRAIN_NUM_CTX",
                                            str(ctx_for(HEAVY_HOST, LEAD_BOX_BRAIN_MODEL, BRAIN_NUM_CTX))))
FAILOVER_NUM_CTX = int(os.getenv("FAILOVER_NUM_CTX", str(ctx_for(FAILOVER_HOST, FAILOVER_MODEL, NUM_CTX))))

# --- Wake-on-LAN: a NIC is woken only when its MAC is configured; an empty MAC means no WoL ---
def _roster_device(name, roster=None):
    return next((d for d in (roster or ROSTER)["devices"] if d["name"] == name), {})


def device_mac(name, roster=None):
    """The wake MAC of device `name`: its record's `mac`, else its <prefix>_MAC setting; "" when it has neither (no
    WoL)."""
    return _roster_device(name, roster).get("mac") or device_setting(name, "MAC", "", roster) or ""


def role_mac(role, roster=None):
    """The wake MAC of the device holding `role` (device_mac of it)."""
    return device_mac(roster_role(role, roster), roster)


def roster_aliases(name, roster=None):
    """The words a person may call device `name` by: its record's `aliases` and its <prefix>_ALIASES setting (comma
    separated, lowercased)."""
    extra = tuple(a.strip().lower() for a in device_setting(name, "ALIASES", "", roster).split(",") if a.strip())
    return tuple(_roster_device(name, roster).get("aliases") or ()) + extra


def wake_device(text):
    """Which machine a wake request names: the lead's machine for its name or its words (or "big guns"), the brain's
    for its name or its words ("brain", or exactly "big"), else None."""
    t = (text or "").lower()
    brain = roster_role("brain")
    if LEAD_BOX in t or any(k in t for k in roster_aliases(LEAD_BOX) + ("big guns",)):
        return LEAD_BOX
    if brain in t or any(k in t for k in roster_aliases(brain) + ("brain",)) or t == "big":
        return brain
    return None


AUTO_WAKE_PRIMARY = os.getenv("AUTO_WAKE_PRIMARY", "1") not in ("0", "false", "False", "")
# The directed broadcast the magic packet goes to. The limited broadcast is the universal default;
# a lab that needs its subnet's directed broadcast adds it here.
WOL_BROADCASTS = [b.strip() for b in os.getenv("WOL_BROADCASTS", "255.255.255.255").split(",") if b.strip()]
WOL_PORT = int(os.getenv("WOL_PORT", "9"))
WOL_SIDECAR = os.getenv("WOL_SIDECAR", "http://172.18.0.1:9119")  # host-net sidecar that broadcasts on the LAN
WAKE_TIMEOUT = int(os.getenv("WAKE_TIMEOUT", "45"))   # The lead wakes in <30s; 45 = margin, then fail FAST
WAKE_POLL = int(os.getenv("WAKE_POLL", "5"))
# Settle delay before loading the 27B model to separate boot/PSU spin-up from GPU inrush.
# Prevents stacked surge on shared UPS.
LEAD_BOX_WAKE_SETTLE = float(device_setting(LEAD_BOX, "WAKE_SETTLE", "5"))

# --- Tautulli (Plex activity: routing cross-check + check_plex tool) ---
TAUTULLI_URL = os.getenv("TAUTULLI_URL", "http://tautulli:8181")
# TAUTULLI_APIKEY is defined below after _secret_value so it uses the secrets helper (never hardcoded).

# --- Home Assistant (ha_get_devices / ha_control_device tools) ---
HA_URL = os.getenv("HA_URL", "http://localhost:8123")


# --- Secrets: a setting's value from its env var, else from the root-only file its *_FILE env var names ---
def _secret_value(env_name, file_env_name):
    value = os.getenv(env_name, "").strip()
    if value:
        return value
    path = os.getenv(file_env_name, "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as secret_file:
                return secret_file.read().strip()
        except OSError:
            return ""
    return ""


HA_TOKEN = _secret_value("HA_TOKEN", "HA_TOKEN_FILE")

# (The media add-on's settings live in arr_config.py.)

# --- Plex Media Server (WRITE side: library scan / metadata refresh). An empty PLEX_URL means no
# Plex is linked: the write tools report that plainly rather than dialing a box that is not there.
# (Tautulli stays the read side; the media add-on's settings live in arr_config.py.) ---
PLEX_URL = os.getenv("PLEX_URL", "").rstrip("/")
PLEX_TOKEN = _secret_value("PLEX_TOKEN", "PLEX_TOKEN_FILE")
PLEX_TIMEOUT = float(os.getenv("PLEX_TIMEOUT", "20"))

# Tautulli read-side API key — secrets-wired (matches other keys: env or file mount). No hardcoded default.
TAUTULLI_APIKEY = _secret_value("TAUTULLI_APIKEY", "TAUTULLI_APIKEY_FILE")

# --- media_probe tool: ffprobe a media file for audio/subtitle tracks (dual-audio detection). Scoped to
# MEDIA_DIR (mounted read-only); the tool refuses any path outside it. ---
MEDIA_DIR = os.getenv("MEDIA_DIR", "/media")
MEDIA_PROBE_TIMEOUT = float(os.getenv("MEDIA_PROBE_TIMEOUT", "30"))

# --- Write-action risk assessment (deterministic; Project #11) + audit trail. Every pending write action is
# scored LOW/MED/HIGH by rules in pending_actions (no model): bulk size, destructive flags, data loss. The
# preview shows a banner for MED/HIGH; every preview/confirm/deny is appended to AUDIT_FILE (JSON lines). ---
RISK_BULK_MED = int(os.getenv("RISK_BULK_MED", "5"))     # >= this many affected items -> at least MED
RISK_BULK_HIGH = int(os.getenv("RISK_BULK_HIGH", "25"))  # >= this many -> HIGH
AUDIT_FILE = os.getenv("AUDIT_FILE", os.path.join(STATE_DIR, "audit", "ops-audit.jsonl"))

# --- Tier-2 secondary semantic check (action_review): a SMALL model reviews "does this write action match
# what the user actually asked?" before the confirm. Secondary signal only (never a gate); fail-soft (skips
# if the model is unreachable). Runs only on MED/HIGH-risk pending actions. Defaults to the fast 8B (the brain box)
# — point ACTION_REVIEW_HOST/MODEL at the 27B if you'd rather use the heavyweight. ---
ACTION_REVIEW_ENABLED = os.getenv("ACTION_REVIEW_ENABLED", "1") not in ("0", "false", "False", "")
ACTION_REVIEW_HOST = os.getenv("ACTION_REVIEW_HOST", "").strip() or BRAIN_HOST
ACTION_REVIEW_MODEL = os.getenv("ACTION_REVIEW_MODEL", "").strip() or BRAIN_MODEL
ACTION_REVIEW_TIMEOUT = float(os.getenv("ACTION_REVIEW_TIMEOUT", "25"))

# Advisory-only post-write check: identifies unsupported assertions in doc diffs. It runs as a desk ORDER: the box, model, context and thinking are the weighed menu row's - there is no host or model knob here.
CLAIM_REVIEW_ENABLED = os.getenv("CLAIM_REVIEW_ENABLED", "1") not in ("0", "false", "False", "")
# A diff smaller than this is a typo fix, not a claim — not worth a model call.
CLAIM_REVIEW_MIN_DIFF = int(os.getenv("CLAIM_REVIEW_MIN_DIFF", "300"))
# Bounded so one huge edit cannot blow the context window. RUNBOOK.md is 326 KB; a full-file send
# would produce a degenerate 1-token answer (the documented SHORTANS failure), not an error.
CLAIM_REVIEW_MAX_DIFF = int(os.getenv("CLAIM_REVIEW_MAX_DIFF", "12000"))
# os.getenv("DOCS_DIR") inline (not the constant below) to avoid NameError at import time.
CLAIM_REVIEW_STATE_DIR = os.getenv(
    "CLAIM_REVIEW_STATE_DIR",
    os.path.join(STATE_DIR, "claim-review"))

# --- Archived photos become descriptions. A chat is ARCHIVED when it is outside the user's most recently
# used CHATS_KEPT_PER_USER - the factory's
# helper_window.CHATS_KEPT_PER_USER is the same rule; keep the two equal) or archived by hand.
PHOTO_ARCHIVE_STATE_DIR = os.getenv("PHOTO_ARCHIVE_STATE_DIR", os.path.join(STATE_DIR, "photo-archive"))
CHATS_KEPT_PER_USER = int(os.getenv("CHATS_KEPT_PER_USER", "5"))
PHOTO_DESCRIBE_AFTER_S = int(os.getenv("PHOTO_DESCRIBE_AFTER_S", str(14 * 86400)))

# --- McCoy: the triage advisor (mccoy.py) - the model proposes, code decides. A small decision model (Ollama
# /v1/systemone, tev1:latest) proposes tier/family/kind with a probability each; code trusts the tier only at or
# above MCCOY_THRESHOLD, else the turn goes to the lead. MCCOY_MODE "shadow" (default) tags every Auto turn in
# the background and logs the row beside the route it actually took - routing never changes; "off" does nothing.
# The threshold and mccoy_questions.json are pinned together: a reworded question set voids the threshold.
# Nothing in the shadow path may raise into a chat turn. ---
MCCOY_HOST = os.getenv("MCCOY_HOST", FAILOVER_HOST)                # the advisor's box; defaults to the failover brain
MCCOY_MODEL = os.getenv("MCCOY_MODEL", "")
MCCOY_MODE = os.getenv("MCCOY_MODE", "shadow")                     # "shadow" | "off"
MCCOY_THRESHOLD = float(os.getenv("MCCOY_THRESHOLD", "0.90"))         # the KIND and coding-FAMILY line
# #34 Phase 8 tuning: the
# TIER has its own line. With the v2 wording (mccoy_questions.json) every line 0.70-0.90 made 0 wrong-lane routes and sent 0
# lead requests below; 0.80 keeps 25 of 108 below-lead requests off the lead (the live v1 wording at 0.90 kept 3). The KIND
# stays at MCCOY_THRESHOLD: at 0.80 it called 3 chats jobs (a chat made background is a user kept waiting).
# 0.70 ~16:5x: zero wrong-lane routes at 0.70 in TWO
# runs (tuning_measure.md; tuning_v3.md on the 135 rows that reach McCoy: 13 of 74 simple requests kept off the lead vs 8 at
# 0.80); the first error in both runs sits at 0.65-0.66 ("Did you finish it yet?" -> Fellowship chat, not the status tool).
MCCOY_TIER_THRESHOLD = float(os.getenv("MCCOY_TIER_THRESHOLD", "0.70"))
# area C step 3 (design § 6 step 8a): McCoy's DEPTH answer ("deep" = worth a wait) is trusted only at this line. The 10-24
# replay (Datasets\Project34-Menu\depth_replay_v2.md, the shipped wording): tev1 sure of depth on 6 of 108 labelled rows at
# 0.90 (5 right, 0 under-routes) and 14 at 0.80 (13 right, 1 over-wait) - the tier's line is kept (the error that does
# not make a user wait is the one to keep), 0.80 is the recorded candidate; every Auto turn LOGS depth + confidence
# (#35's corpus); a lower line comes from a larger measurement, never by hand.
# 0.80 since the Phase 8 tuning: 16 of 16 right on the hand-labelled real requests, 13 of 14 on the mined labels
# (depth_replay_v2.md), every error an over-wait - never a deep answer called quick.
MCCOY_DEPTH_THRESHOLD = float(os.getenv("MCCOY_DEPTH_THRESHOLD", "0.80"))
# Quick mode's "about the same time" (scheduler.weigh_rows mode="quick"): rows within this many seconds of the fastest
# count as fast and quality decides among them. 3 s is
# - the starting value for HAI 1.9; tune it from the users'
# feedback and the placement records (each quick placement notes every row's gap).
QUICK_TIME_SLACK_S = float(os.getenv("QUICK_TIME_SLACK_S", "3"))
MCCOY_TIMEOUT = float(os.getenv("MCCOY_TIMEOUT", "30"))            # the FALLBACK clock only (post_supervised)
# McCoy's model STAYS LOADED on the boxes whose ollama holds two models: his call there carries keep_alive -1 (until
# evicted). Default: the advisor's own box, whose health daemon's duty 7 drops it back to one model at a time when the
# media server needs the room. Elsewhere the box's keep_alive. A one-device head (no roster) keeps McCoy resident
# nowhere: on one card he would hold room the person's own model needs, and each would keep reloading the other.
def mccoy_resident_hosts(setting, one_device, advisor_box):
    """The boxes McCoy stays loaded on: the MCCOY_RESIDENT_HOSTS setting when it is set (empty = none), else none on a
    one-device head, else the advisor's own box (none when the roster names no advisor)."""
    raw = setting if setting is not None else ("" if one_device else (advisor_box or ""))
    return {h.strip() for h in raw.split(",") if h.strip()}


MCCOY_RESIDENT_HOSTS = mccoy_resident_hosts(os.getenv("MCCOY_RESIDENT_HOSTS"), _ONE_DEVICE, roster_role("advisor"))
MCCOY_EXCERPT = int(os.getenv("MCCOY_EXCERPT", "200"))            # request chars kept in a log row
MCCOY_LOG_DIR = os.getenv("MCCOY_LOG_DIR", os.path.join(STATE_DIR, "mccoy"))
# McCoy ACTIVE (HAI-Agent-Surface-Design build step 4, "Triage - the advisor; unsure => the 3.8"): an Auto turn of a
# user named here is SERVED by McCoy's verdict (mccoy.route) instead of shadowed - widened by name like
# HAI_CHAT_WINDOWS. Empty = shadow for everyone. A machine user named
# here is opted in deliberately, so the "machine" special route does not skip it.
MCCOY_ACTIVE_USERS = {u.strip() for u in os.getenv("MCCOY_ACTIVE_USERS", "").split(",") if u.strip()}

# The lab's owner: the profile that gets owner-only features (the experimental renderers, the admin's upload corpus,
# the owner's render strength) and the name prompts and messages give whoever approves changes. Unset: nobody gets
# those features, and the text says "the admin".
OWNER_USER = os.getenv("OWNER_USER", "").strip()
OWNER = OWNER_USER or "the admin"
OWNER_S = (OWNER_USER + "'s") if OWNER_USER else "the admin's"
PROFILES = [p.strip() for p in os.getenv("PROFILES", OWNER_USER or "admin").split(",") if p.strip()]   # the household's profiles
KEEP_ALIVE = os.getenv("KEEP_ALIVE", "5m")
# Per-host keep_alive overrides (how long Ollama keeps the model resident after a request).
# The failover box shares its GPU with Plex, so unload it sooner to free VRAM. Default 2m.
# The lead (default responder): unload soon after use to free VRAM for other PC use — the
# model reloads from SSD in ~18s, and the OS stays awake 30m (UNATTENDSLEEP) + a keep-awake
# watcher so a quick follow-up skips the cold WoL/HDD spin-up entirely. Default 5m.
# Each is that machine's <prefix>_KEEP_ALIVE setting.
FAILOVER_KEEP_ALIVE = device_setting(roster_role("failover"), "KEEP_ALIVE", "2m")
LEAD_BOX_KEEP_ALIVE = device_setting(LEAD_BOX, "KEEP_ALIVE", "5m")
KEEP_ALIVE_OVERRIDES = {FAILOVER_HOST: FAILOVER_KEEP_ALIVE, HEAVY_HOST: LEAD_BOX_KEEP_ALIVE}

# Gaming-busy probe: when the lead's machine is AWAKE, SSH in and read free VRAM before loading a big model - don't
# compete with a running game (lead_ssh). Its <prefix>_SSH_HOST / _SSH_USER / _SSH_KEY settings; an empty host means no
# probe is possible, which is fail-open (nothing is blocked); an empty user or key means ssh's own default for it.
LEAD_BOX_SSH_HOST = device_setting(LEAD_BOX, "SSH_HOST")
LEAD_BOX_SSH_USER = device_setting(LEAD_BOX, "SSH_USER")
LEAD_BOX_SSH_KEY = device_setting(LEAD_BOX, "SSH_KEY")
LEAD_BOX_MIN_FREE_MB = int(device_setting(LEAD_BOX, "MIN_FREE_MB", "19000"))
# SDXL needs ~7GB VRAM; lower threshold than the 27B model (19GB) to avoid
# unnecessary blocking on typical desktop + ComfyUI overhead.
IMG_MIN_FREE_MB = int(os.getenv("IMG_MIN_FREE_MB", "7000"))
# The SDXL-architecture checkpoint every fast-tier/identity workflow renders with. Base SDXL is
# 2023-vintage (famously weak eyes); RealVisXL_V5.0 is shelved on the lead for the bake-off —
# flip per-run via env, promote the winner here.
IMG_SDXL_CHECKPOINT = os.getenv("IMG_SDXL_CHECKPOINT", "sd_xl_base_1.0.safetensors")
# Tier-2 genetics trait detection (#19 P3c): a VISION model reads per-parent binary phenotypes
# (epicanthic fold etc.) that landmarks can't. Qwen3-VL 8B (2026 SOTA-per-VRAM — NOT the older
# qwen2.5-vl) lives beside the 8B brain on the brain box; fail-soft everywhere it's used.
VLM_HOST = os.getenv("VLM_HOST", "").strip() or HEAVY_HOST
VLM_MODEL = os.getenv("VLM_MODEL", "")
# Only one request may touch the lead's 27B at a time (one shared GPU; concurrent heavy
# requests would double-WoL + thrash the model in/out of VRAM). A second heavy request waits
# for the slot, then finds the 27B already warm. The wait has no clock: the waiter is
# told its place, a rough ETA and the reroute, and leaves on its turn or a Stop (app._LeadSlot).
# DOCS_DIR and POLICY_TABLE are defined at the TOP of this file: the model windows are read from the policy table.
# The library-triage share as this container sees it (compose mounts it rw). Tools that take UNC
# args for the factories map onto this to verify a file actually exists before submitting.
LIBRARY_TRIAGE_MOUNT = os.getenv("LIBRARY_TRIAGE_MOUNT", "/library-triage")

# --- Per-user tool gating (LAN-trusted: based on the self-asserted profile name) ---
# ADMIN_USERS may use the profile-management tools. HA_CONTROL_USERS may CHANGE Home
# Assistant device state (everyone may still READ state via ha_get_devices). Default-deny:
# anyone not listed cannot control the house. Enforced at dispatch in tools.run_tool AND by
# filtering the tool list the model sees.
ADMIN_USERS = {u.strip() for u in os.getenv("ADMIN_USERS", OWNER_USER or "admin").split(",") if u.strip()}
# Power users: beside the admins, the only accounts that may pin an exact model and box (row_pins; #34 checklist D2,
# No house control - that stays ADMIN_USERS.
POWER_USERS = {u.strip() for u in os.getenv("POWER_USERS", "").split(",") if u.strip()}
# "priority and fairness"; #34 B4a, ): within quick requests and within jobs, these users' waiting
# requests go first. Never preempts a running job and never puts anyone's quick question behind a long job.
PRIORITY_USERS = {u.strip() for u in os.getenv("PRIORITY_USERS", OWNER_USER).split(",") if u.strip()}
HA_CONTROL_USERS = {u.strip() for u in os.getenv("HA_CONTROL_USERS", OWNER_USER or "admin").split(",") if u.strip()}
# The per-user heavy-model list (an env list of who may force the 27B) is RETIRED: every user has the same controls and a pin
# to a busy lead QUEUES with a notice, ETA and reroute offer - the slot is the guard now (#34 design § 5, HAI-Agent-Surface-Design § "EVERY USER, THE SAME CONTROLS").
# #37 Quick Resume saves: a finished run's ledger is kept in State/hai-runs-saved, newest N 0 = keep none; < 0 = keep every run (model testing). A run
# pinned on request (POST /runs/save) is kept in State/hai-runs-pinned and never rotated.
RUN_SAVES_KEEP = int(os.getenv("HAI_RUN_SAVES_KEEP", "5"))
# Automation identities. Queries from these users MUST provide an explicit chat_id;
# they must NOT rely on resolve_chat() active-chat fallback (prevents machine traffic
# from bleeding into human live chats). Enforced in app._run_query_core.
MACHINE_USERS = {u.strip() for u in os.getenv("MACHINE_USERS", "ClaudeSupervisor").split(",") if u.strip()}
PROFILES_FILE = os.getenv("PROFILES_FILE", os.path.join(DOCS_DIR, "State", "profiles.json"))
# #19/#20 crossover person records (identity substrate) — schema store; behavior deferred to #20
PERSON_RECORDS_FILE = os.getenv("PERSON_RECORDS_FILE",
                                os.path.join(DOCS_DIR, "State", "person-records.json"))
CLAUDE_TRIGGERS = ("claude:", "ask claude")
TOOL_LOOP_MAX = int(os.getenv("TOOL_LOOP_MAX", "5"))

# Diagnostic threshold for answers with no tool calls: below this length, a SHORTANS entry
# is logged. This is a logging aid only; it does not alter execution flow.
SHORTANS_CHARS = int(os.getenv("SHORTANS_CHARS", "250"))
# A coding tool loop has no turn or search count. It ends on the model's answer, on
# its job's deadline, or after this many consecutive turns that only repeat calls already made on an
# unchanged workspace (same rule as the A1 harness's NO_PROGRESS_REPEAT).
CODING_IDLE_TURNS = int(os.getenv("CODING_IDLE_TURNS", "3"))
# The STALL rule (A1's NO_PROGRESS_STALL): this many tool-calling turns in a row that bring the workspace back only to
# states it has already been in, with no new observation. A1 set 8 on 207 replayed exercises (its solves' longest such
# run: 4). Re-measured for HAI's own jobs(Claude Home scratchpad `stall_replay.py --calls-only`, 21 saved
# runs, 1,444 tool-calling turns): the longest stretch a model came back from was 3, so 8 keeps more than A1's margin.
CODING_STALL_TURNS = int(os.getenv("CODING_STALL_TURNS", "8"))
# Image generation runs only when a ComfyUI base URL is configured; empty means the image tools say
# plainly that no renderer is linked.
COMFY_URL = os.getenv("COMFY_URL", "")
IMG_GEN_ENABLED = os.getenv("IMG_GEN_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
IMG_GEN_TIMEOUT = int(os.getenv("IMG_GEN_TIMEOUT", "420"))       # Flux cold ~205s; headroom ×2
IMG_GEN_DAILY_QUOTA = int(os.getenv("IMG_GEN_DAILY_QUOTA", "20"))  # Quota-exempt users (not admins; no admin tools)
IMG_UNLIMITED_USERS = {u.strip() for u in os.getenv("IMG_UNLIMITED_USERS", "").split(",") if u.strip()}
# Add this to BM25 score for pinned docs before relevance gate + sort.
# Pinned items rank higher and clear the gate easier; unpinned remain searchable.
# 0 disables.
RAG_PIN_BOOST = float(os.getenv("RAG_PIN_BOOST", "5.0"))
# Tamper guard: tests must be READ-ONLY observers. Fingerprints non-test source
# before/after suite; mutation causes verify FAIL. Fail-open on infra errors,
# fail-closed on detection. Covers fix-round re-verifies.
OVERNIGHT_SUITE_TAMPER_GUARD = os.getenv("OVERNIGHT_SUITE_TAMPER_GUARD", "true").strip().lower() in ("1", "true", "yes", "on")
# Heartbeat interval in turns; 0 disables.
CODING_HEARTBEAT_TURNS = int(os.getenv("CODING_HEARTBEAT_TURNS", "10"))
SELF_ENGINEERING_MAX_PASSES = int(os.getenv("SELF_ENGINEERING_MAX_PASSES", "8"))
SELF_ENGINEERING_STALL_LIMIT = int(os.getenv("SELF_ENGINEERING_STALL_LIMIT", "2"))
# --- Project #12 Phase 6: self-engineering backlog (overnight autonomy substrate) ---
# The human-owned backlog dir is mounted READ-ONLY at /selfeng (the :ro mount is the mechanical enforcement
# of "the approved-to-run-overnight flag is human-only-writable"); HAI's proposals dir is a SEPARATE rw mount
# at /selfeng-proposals. DIRECTORY mounts (not single-file) so editing backlog.yaml on the host doesn't stale
# the container's bind handle (single-file CIFS bind mounts go stale on replace).
SELFENG_BACKLOG = os.getenv("SELFENG_BACKLOG", "/selfeng/backlog.yaml")
SELFENG_PROPOSALS = os.getenv("SELFENG_PROPOSALS", "/selfeng-proposals/proposals.yaml")
# Persistent done-ledger: tracks completed items across runs so they never re-execute.
# Machine-owned state (rw mount), keeping backlog.yaml a pure human declaration.
# To deliberately re-run an item, delete its entry from this file.
SELFENG_COMPLETED = os.getenv("SELFENG_COMPLETED", "/selfeng-proposals/completed.json")
SELFENG_PLANS_DIR = os.getenv("SELFENG_PLANS_DIR", "/selfeng-proposals/plans")
# Phase 4 (UI redesign): planning (DECOMPOSITION — a think tool) is open to ALL users, but user planning
# lives in a SEPARATE, UNPRIVILEGED layer from the self-eng backlog. A non-admin's plans + approvals land
# in their OWN dir under the writable State subtree (NOT RAG-indexed, like the overnight/absurd state), and
# their "approve" only saves a candidate to THEIR dashboard. ONLY an admin's plans go to SELFENG_PLANS_DIR
# and only an admin (host-only promote.py, ro backlog) can feed the runnable/overnight pipeline — the
# kernel guard is untouched. So opening planning never reaches the privileged execution layer.
PLANNING_OPEN_TO_ALL = os.getenv("PLANNING_OPEN_TO_ALL", "1").lower() not in ("0", "false", "no")
USER_PLANS_DIR = os.getenv("USER_PLANS_DIR", os.path.join(STATE_DIR, "user-plans"))
OVERNIGHT_PLANNER_MODEL = os.getenv("OVERNIGHT_PLANNER_MODEL", "")
OVERNIGHT_PLAN_CRITIC_MODEL = os.getenv("OVERNIGHT_PLAN_CRITIC_MODEL", "")
OVERNIGHT_PLAN_NUM_CTX = int(os.getenv("OVERNIGHT_PLAN_NUM_CTX", "32768"))
OVERNIGHT_PLAN_MAX_ROUNDS = int(os.getenv("OVERNIGHT_PLAN_MAX_ROUNDS", "2"))   # 3 didn't converge; 2 = faster loop
# Phase 8 v2 GROUNDING: index the orchestrator's own source and inject the most relevant slices into
# the planner + critic so a plan to change EXISTING code is grounded in the REAL stack (auto — no
# hand-fed file lists). Greenfield commissions retrieve nothing above MIN_SIM and stay ungrounded. Source
# dir is the baked app root (/app); injected code is capped so it can't overflow the plan ctx.
GROUNDING_SOURCE_DIR = os.getenv("GROUNDING_SOURCE_DIR", "/app")
GROUNDING_NAMESPACE = os.getenv("GROUNDING_NAMESPACE", "selfcode")
GROUNDING_TOPK = int(os.getenv("GROUNDING_TOPK", "6"))
GROUNDING_MAX_CHARS = int(os.getenv("GROUNDING_MAX_CHARS", "8000"))
GROUNDING_CHUNK_MAX_CHARS = int(os.getenv("GROUNDING_CHUNK_MAX_CHARS", "1600"))
GROUNDING_MIN_SIM = float(os.getenv("GROUNDING_MIN_SIM", "0.30"))
# Phase 6B-2 overnight worker: per-item HYBRID retry cap (variance attempts before BAIL; the
# coding-runner's Phase-3 recovery handles identical-failure fingerprinting + baseline restore).
OVERNIGHT_MAX_ATTEMPTS = int(os.getenv("OVERNIGHT_MAX_ATTEMPTS", "2"))
# #37: a run a restart interrupted
# starts again by itself - the item in flight first, on its coding task, its drive from the last saved turn.
OVERNIGHT_RESUME_AFTER_RESTART = os.getenv("OVERNIGHT_RESUME_AFTER_RESTART", "true").strip().lower() in (
    "1", "true", "yes", "on")
# Per-item DRIVE budget bound: cap how many self-eng continuation passes a single item's 27B drive
# may use, scaled by remaining run budget, so one slow item can't starve the 6C adversarial ladder
# (the validation showed a ~6-min/pass drive eating a whole 900s budget → ladder skipped). The drive
# reserves OVERNIGHT_LADDER_RESERVE_SECONDS for the ladder; EST_PASS_SECONDS is a conservative
# per-pass estimate used only for the cap math. Always >= 1 pass; capped at SELF_ENGINEERING_MAX_PASSES.
OVERNIGHT_EST_PASS_SECONDS = int(os.getenv("OVERNIGHT_EST_PASS_SECONDS", "360"))
OVERNIGHT_LADDER_RESERVE_SECONDS = int(os.getenv("OVERNIGHT_LADDER_RESERVE_SECONDS", "300"))

# Gate FAILS CLOSED. Default ceiling $0.10/kWh; tune per live curve.
ENERGY_GATE_ENABLED = os.getenv("ENERGY_GATE_ENABLED", "1") not in ("0", "false", "False", "")
ENERGY_PRICE_CEILING = float(os.getenv("ENERGY_PRICE_CEILING", "0.10"))   # $/kWh; run allowed at/below
ENERGY_PSP_ENTITY = os.getenv("ENERGY_PSP_ENTITY", "")     # the utility's hourly-price entity in Home Assistant; empty = no price


def lab_zone(name):
    """The zone for a timezone name. A name tzdata does not know (a typo in TZ) gives UTC and says so once, rather than
    taking every module that stamps a date down with it."""
    try:
        return zoneinfo.ZoneInfo(name)
    except Exception:
        print("config: timezone %r is not a known zone; using UTC" % (name,), file=sys.stderr)
        return zoneinfo.ZoneInfo("UTC")


# The lab's wall clock: notes, daily files, memory dates and every human-facing time. The container's TZ (the compose
# and the one-machine example set it); HAI_TZ overrides it; UTC when neither is set. Modules read LAB_ZONE, never a name.
LAB_TZ = os.getenv("HAI_TZ") or os.getenv("TZ") or "UTC"
LAB_ZONE = lab_zone(LAB_TZ)
LAB_TZ = LAB_ZONE.key
ENERGY_HA_TIMEOUT = float(os.getenv("ENERGY_HA_TIMEOUT", "8"))
# THE PRICE OF THE HOUR as a placement input. The placeholder is the stated average price, used whenever the PSP
# pipeline cannot be read; the placement record says which it used, never a silent zero. The peak and extreme lines
# stay UNSET until they come from the home's price history or are named by the admin (no arbitrary limits): an
# unset line never engages. Peak = slow and low on energy (every placement weighs energy before the line and
# before home); extreme = Auto work HOLDS with a notice, and a user's pin still answers.
ENERGY_PRICE_PLACEHOLDER = float(os.getenv("ENERGY_PRICE_PLACEHOLDER", "0.11"))
ENERGY_PEAK_PRICE = float(os.environ["ENERGY_PEAK_PRICE"]) if os.getenv("ENERGY_PEAK_PRICE") else None
ENERGY_EXTREME_PRICE = float(os.environ["ENERGY_EXTREME_PRICE"]) if os.getenv("ENERGY_EXTREME_PRICE") else None
# --- Phase 7B: overnight scheduler ("run-when-cheap"). A queued request fires the moment the price
# drops to cheap + nothing's running + there's approved work, or expires after OVERNIGHT_SCHEDULE_MAX_WAIT_HOURS.
OVERNIGHT_SCHEDULER_INTERVAL = int(os.getenv("OVERNIGHT_SCHEDULER_INTERVAL", "300"))
HAI_JOBS_TICK_S = int(os.getenv("HAI_JOBS_TICK_S", "30"))   # how often Kirk looks for a desk HAI job whose time has come
WATCH_TICK_SECONDS = int(os.getenv("WATCH_TICK_SECONDS", "60"))
OVERNIGHT_SCHEDULE_MAX_WAIT_HOURS = int(os.getenv("OVERNIGHT_SCHEDULE_MAX_WAIT_HOURS", "18"))
# Optional webhook pinged on overnight run completion. OFF by default (empty string).
OVERNIGHT_NOTIFY_WEBHOOK = _secret_value("OVERNIGHT_NOTIFY_WEBHOOK", "OVERNIGHT_NOTIFY_WEBHOOK_FILE")
# Optional Discord DM via local admin bot. OFF by default; requires token and user_id.
OVERNIGHT_DM_URL = os.getenv("OVERNIGHT_DM_URL", "http://hawkadminbot:8477/dm")
OVERNIGHT_DM_TOKEN = _secret_value("OVERNIGHT_DM_TOKEN", "OVERNIGHT_DM_TOKEN_FILE")
OVERNIGHT_DM_USER_ID = os.getenv("OVERNIGHT_DM_USER_ID", "")
# The DM relay's token for the head's other notices (the claim sweep, quick previews). Empty = those notices are off.
HAWKADMIN_DM_TOKEN = _secret_value("HAWKADMIN_DM_TOKEN", "HAWKADMIN_DM_TOKEN_FILE")
# P20 render worker (the lead) auth for /p20/* endpoints. EMPTY = endpoints disabled (fail-closed);
# set the same value in the lead's foundry <root>\etc\dm_relay.env (P20_WORKER_TOKEN=...).
P20_WORKER_TOKEN = _secret_value("P20_WORKER_TOKEN", "P20_WORKER_TOKEN_FILE")
# Bail adjudication kill switch: when true, writes `.adjudicate-request.json` for
# runs with bail_human/test-tampering items. An external watcher feeds these to an
# LLM service to verify REAL-vs-HALLUCINATED findings against sandbox git.
# Set to 'false' to disable entirely.
OVERNIGHT_ADJUDICATE_REQUEST = os.getenv("OVERNIGHT_ADJUDICATE_REQUEST", "true").strip().lower() in ("1", "true", "yes", "on")

# Workspace stale-check (D0 staleness guard): at overnight item start, compare the workspace's
# top-level *.py against live (the orchestrator's own /app). If the workspace is BEHIND live AND has
# no undeployed commits beyond its last sync-to-live baseline, refuse with a clear error naming the
# stale files. Kill switch: set to 'false' to disable entirely (e.g. during migration). Default true.
OVERNIGHT_WORKSPACE_STALE_CHECK = os.getenv("OVERNIGHT_WORKSPACE_STALE_CHECK", "true").strip().lower() in ("1", "true", "yes", "on")
# Live source root the stale-check hashes to fingerprint the deployed code. Inside the orchestrator
# container this is the baked-in app dir (/app); overridable for tests/other layouts.
SELFENG_LIVE_ROOT = os.getenv("SELFENG_LIVE_ROOT", "/app")

# Guard against fake DONE status: reject if acceptance requires new tests but the diff adds none.
# Fails open on error. Kill switch: 'false' disables. Default true.
OVERNIGHT_ACCEPTANCE_DELTA_CHECK = os.getenv("OVERNIGHT_ACCEPTANCE_DELTA_CHECK", "true").strip().lower() in ("1", "true", "yes", "on")

# --- Phase 6C: adversarial review ladder. A guaranteed FLOOR critique (never zero) + time-scaled
# escalation to bigger LOCAL critics. tier[0] is the floor; the DECORRELATED tiers use a model
# DIFFERENT from the 27B author (same-model self-review shares blind spots = weakest). est_seconds
# is a conservative (high) per-critique cost used only for the remaining_time -> tier selection.
import json as _json
# Adversarial ladder tiers (cheapest->priciest). Tiers add decorrelated critics
# (not bigger models) as budget allows, since unioning findings increases recall.
# est_seconds covers the whole pool including warm-up; all tiers fit on 24GB GPU.
# The critics beyond the floor are the lab's choice: ADVERSARY_CRITIC_MODELS (comma separated, cheapest first);
# each one adds the next tier. With none set there is only the floor.
ADVERSARY_CRITIC_MODELS = [m.strip() for m in os.getenv("ADVERSARY_CRITIC_MODELS", "").split(",") if m.strip()]
_ADVERSARY_TIERS_DEFAULT = [{"name": "floor", "models": [HEAVY_MODEL], "est_seconds": 120, "swap": False}] + [
    {"name": name, "models": [HEAVY_MODEL] + ADVERSARY_CRITIC_MODELS[:i], "est_seconds": 120 + 120 * i, "swap": True}
    for i, name in ((1, "pair"), (2, "trio")) if len(ADVERSARY_CRITIC_MODELS) >= i]
# Overnight DRIVER escalation: if the default 27b driver BAILS an item (the deterministic verifier never
# passes), retry with progressively bigger LOCAL coders before giving up. None = the default 27b. The
# Claude tier (ultimate fallback) is deferred — unattended Claude calls need token guardrails + opt-in.
OVERNIGHT_DRIVER_LADDER = [None] + [m.strip() for m in os.getenv(
    "OVERNIGHT_DRIVER_LADDER", "").split(",") if m.strip()]
try:
    ADVERSARY_TIERS = _json.loads(os.getenv("ADVERSARY_TIERS_JSON", "")) or _ADVERSARY_TIERS_DEFAULT
except Exception:
    ADVERSARY_TIERS = _ADVERSARY_TIERS_DEFAULT
ADVERSARY_HOST = os.getenv("ADVERSARY_HOST", "").strip() or HEAVY_HOST   # critics run on the lead
ADVERSARY_NUM_CTX = int(os.getenv("ADVERSARY_NUM_CTX", "16384"))
# Max diff chars sent to a critic. Was an 8000 cap that TRUNCATED real diffs (a ~10.5KB unification diff
# lost its tail — run_thinker's delegation + the integration tests — so critics raised false "incomplete/
# truncated/duplication-remains" findings that never converged). 24000 chars (~6k tokens) fits the 16k ctx
# with room for the response; when a diff DOES exceed it, clip_for_review appends a note so the critic
# doesn't mistake OUR truncation for the author's incomplete work.
ADVERSARY_DIFF_MAX_CHARS = int(os.getenv("ADVERSARY_DIFF_MAX_CHARS", "24000"))
ADVERSARY_TIMEOUT = float(os.getenv("ADVERSARY_TIMEOUT", "600"))
ADVERSARY_FIX_RESERVE = int(os.getenv("ADVERSARY_FIX_RESERVE", "180"))   # time reserved for one fix+reverify round
ADVERSARY_MARGIN = int(os.getenv("ADVERSARY_MARGIN", "60"))              # safety margin against the budget ceiling
ADVERSARY_MAX_ROUNDS = int(os.getenv("ADVERSARY_MAX_ROUNDS", "2"))       # legacy/interactive cap: bail-to-human at this round
# Overnight "drive to convergence" ceiling: with the convergence guard on (stall_on_no_progress),
# the ladder keeps fixing while findings DROP and bails only on a real stall / out-of-time, so this
# is just a generous safety ceiling -- big enough to walk 5 findings down to 0 one-at-a-time, not a
# 2-round guillotine. The point of overnight is that time is cheap; spend it converging.
OVERNIGHT_LADDER_MAX_ROUNDS = int(os.getenv("OVERNIGHT_LADDER_MAX_ROUNDS", "12"))
# Patience for the overnight ladder's stall guard: tolerate this many CONSECUTIVE non-improving
# re-critiques before bailing-to-human, keeping the best round's result. Critic finding counts are
# noisy stochastic draws (a correct solution's re-critique can blip UP a round); >1 stops a single blip
# from bailing an on-track run. 1 == the old single-round stall; 2 is a conservative default.
OVERNIGHT_LADDER_STALL_WINDOW = int(os.getenv("OVERNIGHT_LADDER_STALL_WINDOW", "2"))
# Authors may dispute LOW-severity findings; medium+ are fix-only.
# A critic quorum must uphold the dispute to drop it from convergence counts.
# Default ON (behind flag as kill-switch); QUORUM=2 resists single lenient critics.
ADVERSARY_DISPUTE_ENABLED = os.getenv("ADVERSARY_DISPUTE_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
ADVERSARY_DISPUTE_QUORUM = int(os.getenv("ADVERSARY_DISPUTE_QUORUM", "2"))
# All severities are disputable. To mitigate noise from hallucinated regressions,
# high/critical disputes require a stricter quorum (see ADVERSARY_DISPUTE_QUORUM_STRICT)
# and verbatim diff evidence.
ADVERSARY_DISPUTE_SEVERITIES = tuple(s.strip().lower() for s in
    os.getenv("ADVERSARY_DISPUTE_SEVERITIES", "low,medium,high,critical").split(",") if s.strip())
# Quorum for HIGH/CRITICAL disputes: every pool critic must uphold (3 = the full trio; on smaller tiers
# this exceeds the pool so such disputes simply can't be upheld there — conservative by construction).
ADVERSARY_DISPUTE_QUORUM_STRICT = int(os.getenv("ADVERSARY_DISPUTE_QUORUM_STRICT", "3"))
# Critical/high findings must quote the diff verbatim. Unverified quotes
ADVERSARY_EVIDENCE_ANCHOR = os.getenv("ADVERSARY_EVIDENCE_ANCHOR", "true").strip().lower() in ("1", "true", "yes", "on")
ADVERSARY_EVIDENCE_MIN_CHARS = int(os.getenv("ADVERSARY_EVIDENCE_MIN_CHARS", "12"))
ADVERSARY_EVIDENCE_SEVERITIES = tuple(s.strip().lower() for s in
    os.getenv("ADVERSARY_EVIDENCE_SEVERITIES", "critical,high").split(",") if s.strip())
# Evidence packet (files + call sites) complements the diff for critic review.
# Limits constrained by ADVERSARY_NUM_CTX and diff headroom to fit context window.
ADVERSARY_CONTEXT_ENABLED = os.getenv("ADVERSARY_CONTEXT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
ADVERSARY_CONTEXT_MAX_CHARS = int(os.getenv("ADVERSARY_CONTEXT_MAX_CHARS", "20000"))
ADVERSARY_CONTEXT_MAX_FILES = int(os.getenv("ADVERSARY_CONTEXT_MAX_FILES", "6"))
ADVERSARY_CONTEXT_MAX_FILE_CHARS = int(os.getenv("ADVERSARY_CONTEXT_MAX_FILE_CHARS", "8000"))
ADVERSARY_CONTEXT_MAX_REF_LINES = int(os.getenv("ADVERSARY_CONTEXT_MAX_REF_LINES", "80"))
# Severity grounding: critical/high findings must cite the violated acceptance
# criterion or invariant. Findings without a 'violated' field are ungrounded
# opinions and are downgraded to advisory. This works alongside evidence anchoring
# so that criticals are both quoted (anchored) and rule-breaking (grounded).
ADVERSARY_SEVERITY_GROUNDING = os.getenv("ADVERSARY_SEVERITY_GROUNDING", "true").strip().lower() in ("1", "true", "yes", "on")

SELFENG_DRIVE_CONTEXT = os.getenv("SELFENG_DRIVE_CONTEXT", "true").strip().lower() in ("1", "true", "yes", "on")
SELFENG_DRIVE_CONTEXT_MAX_CHARS = int(os.getenv("SELFENG_DRIVE_CONTEXT_MAX_CHARS", "12000"))
SELFENG_DRIVE_CONTEXT_MAX_FILES = int(os.getenv("SELFENG_DRIVE_CONTEXT_MAX_FILES", "6"))
SELFENG_INVARIANTS_PATH = os.getenv("SELFENG_INVARIANTS_PATH",
                                    os.path.join(DOCS_DIR, "Reference", "HAI-INVARIANTS.md"))
SELFENG_INVARIANTS_MAX_CHARS = int(os.getenv("SELFENG_INVARIANTS_MAX_CHARS", "4000"))

# Seconds a 'running' query may sit uncompleted before being marked expired
# (unusable but retained in history). Default: 30 minutes.
GRACE_TTL_SEC = int(os.getenv("GRACE_TTL_SEC", "1800"))
# How many seconds completed/failed/expired entries are retained before purge_retired()
# permanently deletes them (register() purges opportunistically — no scheduler). Default 24 h.
RUN_RETENTION_SEC = int(os.getenv("RUN_RETENTION_SEC", "86400"))
# If every pool critic fails, retry this many times. On exhaustion, flag for human
# instead of defaulting to "fixed" (which can silently pass gamed solutions).
ADVERSARY_CRITIC_RETRIES = int(os.getenv("ADVERSARY_CRITIC_RETRIES", "1"))
# #2 anti-rationalization: if the critics re-raise the SAME critical/high finding across >= this many
# SEPARATE critiques, the fixer never honestly cleared it (it argued the finding away) -> flag for a
# human, don't resolve as converged. 0 disables. 3 = a critical that survived two fix rounds before "vanishing".
ADVERSARY_CRITICAL_RECUR_LIMIT = int(os.getenv("ADVERSARY_CRITICAL_RECUR_LIMIT", "3"))
# #3 test-tampering guard: editing / renaming / deleting an EXISTING test to force a pass ("keep every
# existing test passing" means KEEP them) -> flag for a human. Adding NEW tests is fine.
ADVERSARY_TAMPER_GUARD = os.getenv("ADVERSARY_TAMPER_GUARD", "true").strip().lower() in ("1", "true", "yes", "on")
# Convergence blocks only on these severities; others are advisory.
# Empty string disables gating (all block, legacy). Tunable via env.
ADVERSARY_MATERIALITY_SEVERITIES = tuple(s.strip().lower() for s in
    os.getenv("ADVERSARY_MATERIALITY_SEVERITIES", "critical,high").split(",") if s.strip())

# --- D1 risk-tiered review (density phase; 09-DENSITY-PLAN.md). A mechanical risk score per item picks how
# much of the ladder to run: low = single (floor) critic tier + few rounds; medium = full tiers, fewer rounds;
# high = today's full ladder. Invariant-surface touches + force_adversarial ALWAYS get the full ladder. Gated
# OFF by default so the first run A/Bs against baseline stage-timings; flip ADVERSARY_RISK_TIERING=true to
# enable. Thresholds env-tunable without a rebuild. NEVER weakens anchor/materiality/tamper/acceptance-delta. ---
ADVERSARY_RISK_TIERING = os.getenv("ADVERSARY_RISK_TIERING", "false").strip().lower() in ("1", "true", "yes", "on")
ADVERSARY_RISK_LOW_MAX_LINES = int(os.getenv("ADVERSARY_RISK_LOW_MAX_LINES", "25"))
ADVERSARY_RISK_LOW_MAX_FILES = int(os.getenv("ADVERSARY_RISK_LOW_MAX_FILES", "2"))
ADVERSARY_RISK_HIGH_MIN_LINES = int(os.getenv("ADVERSARY_RISK_HIGH_MIN_LINES", "120"))
ADVERSARY_RISK_HIGH_MIN_FILES = int(os.getenv("ADVERSARY_RISK_HIGH_MIN_FILES", "5"))
ADVERSARY_RISK_LOW_MAX_ROUNDS = int(os.getenv("ADVERSARY_RISK_LOW_MAX_ROUNDS", "1"))
ADVERSARY_RISK_MEDIUM_MAX_ROUNDS = int(os.getenv("ADVERSARY_RISK_MEDIUM_MAX_ROUNDS", "4"))
# Escalate-on-signal (the guardrail that makes low-tiering safe): when a reduced-tier (low/medium) run's
# INITIAL critique surfaces a MATERIAL critical/high after anchoring, promote to the full tier pool for the
# fix + re-critique loop — so a real defect never gets FEWER eyes than a high-risk item. On by default
# whenever tiering is on; the full ladder still applies to high/forced items regardless.
ADVERSARY_RISK_ESCALATE = os.getenv("ADVERSARY_RISK_ESCALATE", "true").strip().lower() in ("1", "true", "yes", "on")

REACH_TIMEOUT = float(os.getenv("REACH_TIMEOUT", "4"))
# After a quick probe to a base URL TIMES OUT, probes to it within this window answer "could not ask" without waiting
#. 60 s = the reporters' cadence - the deck calls
# a box DOWN on missed one-minute reports. A success clears it.
PROBE_QUIET_S = float(os.getenv("PROBE_QUIET_S", "60"))
# ollama_client.can_think asks each box's /api/show whether a model thinks. The deploy gate sets this to 0 so no test
# dials a real box (its unknown = no think flag); the tests of the probe itself turn it back on and fake the box.
THINK_CAPS_PROBE = os.getenv("HAI_THINK_CAPS_PROBE", "1") != "0"
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "1.5"))
# Cap the RAG context injected per query so the prompt always leaves room to generate. The 8B's num_ctx
# is 8192; an over-budget prompt (big uploaded chunks + tool schemas + system) fills the whole window and
# the model emits a 1-token stub (done_reason='length'). ~6000 chars ≈ 1500 tokens of context.
RAG_CHUNK_MAX_CHARS = int(os.getenv("RAG_CHUNK_MAX_CHARS", "1600"))
RAG_CONTEXT_MAX_CHARS = int(os.getenv("RAG_CONTEXT_MAX_CHARS", "6000"))
# Live web-search grounding for time-sensitive questions (injected into the user message like RAG ctx; capped
# so it can't overflow the small 8B window — same reason RAG context is capped).
WEB_CONTEXT_MAX_CHARS = int(os.getenv("WEB_CONTEXT_MAX_CHARS", "3500"))
# get_document returns a WHOLE library document (not a chunk) for review/analysis tasks. Cap so the full
# text + the rest of the prompt still fits the window (most of his drafts are well under this).
GET_DOC_MAX_CHARS = int(os.getenv("GET_DOC_MAX_CHARS", "12000"))
# Auto-reload the RAG corpus when docs change. Checked at most once per this many seconds (throttle),
# so editors (Claude/Codex) never have to POST /reload manually. 0 = check every query.
RAG_RELOAD_CHECK_INTERVAL = float(os.getenv("RAG_RELOAD_CHECK_INTERVAL", "15"))

USERCORPORA_DIR = os.getenv("USERCORPORA_DIR", "/usercorpora")

# --- RAG namespaces: isolated knowledge bases, each a list of *.md corpus dirs. The homelab
# namespace is the curated Documentation library; per-user namespaces are walled off from it (and
# live in Workspace) so a nursing answer never pulls homelab docs and vice-versa.
RAG_NAMESPACES = {
    "homelab": [
        os.path.join(DOCS_DIR, "Reference"),
        os.path.join(DOCS_DIR, "LLM-Project"),
        os.path.join(DOCS_DIR, "Runbook"),
    ],
    # A user's own corpus is a registry entry (namespace + corpus folder), merged in by apply_user_registry.
    # PERSONA-SHARED reference corpus for the Nursing persona: general nursing methodology (EBP/PICOT,
    # levels of evidence + appraisal, APA 7th, NCLEX blueprint basics) shared by ALL nursing users on top
    # of their OWN private corpus. Curated reference (read-as-truth) in the Documentation library — NOT any
    # one user's private uploads. Live clinical evidence still comes from search_evidence (PubMed).
    "nursing_shared": [
        os.path.join(DOCS_DIR, "NursingCorpus"),
    ],
    # The owner's own writable corpus (OWNER_USER, lowercased: ADMIN_UPLOAD_NAMESPACE names the same one), as an
    # upload/save target. The owner's chat stays grounded in the shared homelab library (namespace_for_user ->
    # homelab); this is only where self-serve uploads land, so files can be dropped in from anywhere without writing
    # into the read-as-truth library. No owner set: no such namespace.
    **({OWNER_USER.lower(): [os.path.join(USERCORPORA_DIR, OWNER_USER.lower())]} if OWNER_USER else {}),
    # PERSONA-SHARED reference corpus for the Law persona: US law with one state's focus (federal
    # foundation + that state's statutes). Curated reference (read-as-truth), lives in the Documentation
    # library, NOT a user's private upload target. Any user wearing the Law persona grounds in this on
    # top of their own corpus.
    "law": [
        os.path.join(DOCS_DIR, "LawCorpus"),
    ],
}
# Namespaces that are READ-AS-TRUTH (never written to by user uploads). Everything else is a personal,
# writable corpus a user can upload into. Keeping this as a deny-list means a NEW personal namespace is
# uploadable by default (no per-namespace flag to remember).
READONLY_NAMESPACES = {"homelab", "law", "nursing_shared"}
# Maps users to their grounded namespace. Unlisted users get no grounding (None),
# which prevents casual access to internal docs. This mapping is trust-based,
# not a security boundary.
USER_NAMESPACE = {
    kv.split(":", 1)[0].strip(): kv.split(":", 1)[1].strip()
    for kv in os.getenv(
        "USER_NAMESPACE",
        # EPHEMERAL GROUNDED TEST USERS, so a grounded experience is tested cleanly (never as a real profile):
        # test-homelab grounds in the homelab library, test-nursing in the shared nursing corpus, test-law in the
        # law corpus. Plain `test` stays ungrounded. All `test*` users are ephemeral (see is_test_user) so they
        # never persist history. Real users come from this setting or the registry (apply_user_registry).
        "test-homelab:homelab,test-nursing:nursing_shared,test-law:law",
    ).split(",") if ":" in kv
}
DEFAULT_USER_NAMESPACE = os.getenv("DEFAULT_USER_NAMESPACE", "").strip() or None
# Admins (and any user tasting the Nursing persona without a nursing identity) preview the tutor grounded in
# the SHARED nursing corpus — the general reference, NOT any one student's private drafts.
NURSING_PREVIEW_NAMESPACE = os.getenv("NURSING_PREVIEW_NAMESPACE", "nursing_shared").strip() or None

# Directories users may fetch by name but which are deliberately excluded from BM25/embedding indexing.
# `rag.py` ignores this dict; only `tools._user_corpus_files` uses it.
#
# Constraint: Keep using `glob("*.md")` (not rglob). This prevents `Reference/Credentials/`
# from leaking into the corpus. Enforced by tests/test_credentials_exclusion.py.
FETCH_ONLY_DIRS = {
    "homelab": [
        os.path.join(DOCS_DIR, "Journal"),
    ],
}


def namespace_for_user(user):
    """Resolve the RAG namespace a user is grounded in. Explicit mapping wins; admins default to
    the homelab library; everyone else gets DEFAULT_USER_NAMESPACE (None = ungrounded)."""
    if user in USER_NAMESPACE:
        return USER_NAMESPACE[user]
    if user in ADMIN_USERS:
        return "homelab"
    return DEFAULT_USER_NAMESPACE


def is_test_user(user):
    """Any `test*` user (case-insensitive) is an EPHEMERAL ghost — never persists history. Covers plain
    `test` (ungrounded) AND the grounded test users (test-homelab/test-nursing/test-law) so we can test a
    GROUNDED experience without ever using a real profile as a guinea pig."""
    return bool(user) and str(user).casefold().startswith("test")


def is_nursing_namespace(ns):
    """A namespace that gets the nursing assistant experience (modes + EBP prompt). Convention: any
    namespace named 'nursing*' (e.g. 'nursing', 'nursing_<name>'), so a new nursing user just needs a
    'nursing_<name>' namespace + their own corpus dir."""
    return bool(ns) and ns.startswith("nursing")


def is_writable_namespace(ns):
    """A personal namespace a user may UPLOAD into (their own corpus). True for any configured namespace
    that isn't read-as-truth (homelab). Used to gate /upload and to show the upload button — decoupled
    from nursing, so any general user with a personal corpus can self-serve uploads."""
    return bool(ns) and ns in RAG_NAMESPACES and ns not in READONLY_NAMESPACES


# Users grounded in a READ-AS-TRUTH shared namespace (admins -> homelab) still need a personal corpus to
# UPLOAD / save-into. This is an explicit corpus-OWNERSHIP map, deliberately NOT a
# role check: it does not let an admin bypass the read-only rule, it gives them their own space.
ADMIN_UPLOAD_NAMESPACE = {OWNER_USER: OWNER_USER.lower()} if OWNER_USER else {}


def _assert_admin_upload_targets_writable():
    """Enforce the ADMIN_UPLOAD_NAMESPACE -> RAG_NAMESPACES coupling at config load. Every admin
    upload target must name a real, writable namespace, or that admin would silently get 'no upload
    button + graceful refusal' (safe, but invisible). Fail loudly at startup instead of drifting to a
    silent no-op at upload time."""
    for user, ns in ADMIN_UPLOAD_NAMESPACE.items():
        if not is_writable_namespace(ns):
            raise ValueError(
                "ADMIN_UPLOAD_NAMESPACE[" + repr(user) + "] = " + repr(ns) +
                " is not a writable RAG namespace. Add it to RAG_NAMESPACES (kept out of "
                "READONLY_NAMESPACES) so that admin still has a self-serve upload inbox.")


_assert_admin_upload_targets_writable()


def upload_namespace_for_user(user):
    """Where a user's self-serve UPLOADS / saves land (decoupled from chat-grounding). Everyone else
    uploads into their own grounded personal corpus (their namespace). A user
    grounded in a read-as-truth shared namespace instead lands in their OWN personal
    corpus, so the read-only library stays pristine and the architect still has a
    self-serve inbox. Returns None when the user has no writable target (graceful refusal upstream)."""
    if user in ADMIN_UPLOAD_NAMESPACE:
        return ADMIN_UPLOAD_NAMESPACE[user]
    ns = namespace_for_user(user)
    return ns if is_writable_namespace(ns) else None


def writable_corpus_dir(user):
    """(corpus_dir, namespace) for a user whose upload namespace is a WRITABLE personal corpus, else (None, ns).
        ONE derivation for where a user's uploads live: /upload writes there and look_at_upload reads from there
    ."""
    ns = upload_namespace_for_user(user)
    if not is_writable_namespace(ns):
        return None, ns
    dirs = RAG_NAMESPACES.get(ns) or []
    return (dirs[0] if dirs else None), ns


# --- Personas ("hats"): the SELECTED assistant behavior + domain framing, DECOUPLED from identity
# (namespace/corpus/memory). A user's namespace still drives RAG grounding + privacy; the persona only
# drives the system prompt + domain framing, so any permitted user can "wear" any persona and stay
# themselves. General = default assistant; Nursing = the EBP tutor (with sub-modes); Law = legal-info
# framing (behavior first; a law corpus + sub-modes are follow-on).
PERSONAS = ("general", "nursing", "law")


def is_valid_persona(p):
    return p in PERSONAS


# Per-user DEFAULT persona — a STARTING hat, NOT an identity lock (any permitted user may switch): this setting
# (user:persona,...) or a registry entry's `persona`; everyone else General.
USER_DEFAULT_PERSONA = {
    kv.split(":", 1)[0].strip(): kv.split(":", 1)[1].strip()
    for kv in os.getenv("USER_DEFAULT_PERSONA", "").split(",") if ":" in kv
}
# The home's Home Assistant area ids, as the tools name them. (The words that mean a machine in a request are that
# device's <prefix>_ALIASES setting: roster_aliases.)
HA_AREAS = os.getenv("HA_AREAS", "").strip() or "living_room, kitchen, bedroom, office"
# Who the persona prompts are written for: the Nursing assistant's student, and the Law persona's default user.
NURSING_PERSONA_USER = os.getenv("NURSING_PERSONA_USER", "").strip() or "the user"
LAW_PERSONA_USER = os.getenv("LAW_PERSONA_USER", "").strip()
# The Law persona's state and where its law is read, as settings; unset, the persona speaks of "your state" and the
# state's official sites.
LAW_STATE = os.getenv("LAW_STATE", "").strip()
LAW_STATE_SOURCES = os.getenv("LAW_STATE_SOURCES", "").strip()


def default_persona_for(user):
    """The user's starting persona. Explicit map wins; else infer Nursing from a nursing namespace; else
    General. (A nursing user with no explicit default still lands in Nursing.)"""
    p = USER_DEFAULT_PERSONA.get(user)
    if is_valid_persona(p):
        return p
    if is_nursing_namespace(namespace_for_user(user)):
        return "nursing"
    return "general"


# Persona-shared reference corpora — the "two corpus layers." A persona can carry a SHARED domain corpus
# that any user wearing it grounds in, ON TOP OF their own private corpus (never another user's private
# uploads — the shared corpus is curated reference). Only wired for Law today; Nursing keeps its current
# single-corpus behavior (unchanged), so the nursing path stays byte-identical.
PERSONA_SHARED_NAMESPACE = {
    "law": os.getenv("LAW_SHARED_NAMESPACE", "law"),
    "nursing": os.getenv("NURSING_SHARED_NAMESPACE", "nursing_shared"),
}


def shared_namespace_for_persona(persona):
    """The persona-shared reference namespace for a persona, or None if it has none / isn't configured."""
    ns = PERSONA_SHARED_NAMESPACE.get(persona)
    return ns if ns in RAG_NAMESPACES else None


# --- Data-driven per-user corpora (onboarding auto-provisioning) ---------------------------------
# New HAI friends get a private corpus WITHOUT a per-user mount or code edit: their corpus is a real
# subdir under the ONE shared FRIENDS_CORPORA_DIR mount, and a registry JSON maps profile -> namespace.
# Merged into RAG_NAMESPACES + USER_NAMESPACE (+ USER_DEFAULT_PERSONA) at import; /admin/provision_user
# mkdirs the subdir, appends the registry, and applies it live. Hand-authored config entries always win.
FRIENDS_CORPORA_DIR = os.getenv("FRIENDS_CORPORA_DIR", os.path.join(USERCORPORA_DIR, "_friends"))
USERCORPORA_REGISTRY = os.getenv("USERCORPORA_REGISTRY", os.path.join(FRIENDS_CORPORA_DIR, "_registry.json"))
PROVISION_TOKEN = _secret_value("PROVISION_TOKEN", "PROVISION_TOKEN_FILE")   # shared secret gating /admin/provision_user


def load_user_registry():
    try:
        with open(USERCORPORA_REGISTRY, encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save_user_registry(reg):
    os.makedirs(os.path.dirname(USERCORPORA_REGISTRY), exist_ok=True)
    tmp = USERCORPORA_REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        _json.dump(reg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, USERCORPORA_REGISTRY)


def apply_user_registry(reg=None):
    """Merge registry entries into the live namespace/persona maps. Idempotent; a value already set (this module's
    settings) wins (setdefault). An entry's corpus dir is its `corpus` (relative to USERCORPORA_DIR, or absolute) or
    else FRIENDS_CORPORA_DIR/<namespace>; an entry with no namespace only sets the user's default persona."""
    reg = load_user_registry() if reg is None else reg
    for profile, info in (reg or {}).items():
        info = info or {}
        if not profile:
            continue
        ns = info.get("namespace")
        if ns:
            corpus = (os.path.join(USERCORPORA_DIR, info["corpus"]) if info.get("corpus")
                      else os.path.join(FRIENDS_CORPORA_DIR, ns))
            RAG_NAMESPACES.setdefault(ns, [corpus])
            USER_NAMESPACE.setdefault(profile, ns)
        persona = info.get("persona")
        if persona and persona != "general" and is_valid_persona(persona):
            USER_DEFAULT_PERSONA.setdefault(profile, persona)
    return reg


apply_user_registry()   # merge onboarding-provisioned users at import


def grounding_namespaces(identity_ns, persona, user=None):
    """The corpus layers for a request: the user's own identity namespace, their personal upload library (if distinct), and the persona-shared reference corpus. Results are de-duplicated and order-preserving. This is the single source of truth for both passive seed-grounding and active document tools. Never include another user's private corpus."""
    own_uploads = upload_namespace_for_user(user) if user else None
    seen, out = set(), []
    for n in (identity_ns, own_uploads, shared_namespace_for_persona(persona)):
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out
# The FALLBACK clock for a silent /api/chat call, used ONLY when its box has no fresh load report to ask
# (ollama_client._SilenceWatch). A call whose box reports work waits as long as the work takes: a long
# re-read can outlast any fixed clock with no output, and a clock that fires then kills a healthy job.
CHAT_TIMEOUT = float(os.getenv("CHAT_TIMEOUT", "240"))
CHAT_RETRIES = int(os.getenv("CHAT_RETRIES", "2"))
# HAI's own model turns through a factory WINDOW order. Off until
# the live proof; the share holds each window's items as this container sees it and as the factories reach it.
# HAI_CHAT_WINDOWS: "" / "0" = off; "*" or "1" = every user; else a comma list of users (the live proof runs as
# ClaudeSupervisor first, then widens) - read per call by chat_window.enabled_for.
CHAT_WINDOWS_USERS = {u.strip() for u in os.getenv("HAI_CHAT_WINDOWS", "").split(",") if u.strip() and u.strip() != "0"}
CHAT_WINDOWS = bool(CHAT_WINDOWS_USERS)
CHAT_WINDOWS_DIR = os.getenv("HAI_CHAT_WINDOWS_DIR", "/tabjobs/chat-windows")
# The share as the factories reach it. Empty means no shared staging: a head on one machine uses its
# own CHAT_WINDOWS_DIR, and a head with no fleet places no window orders at all.
CHAT_WINDOWS_UNC = os.getenv("HAI_CHAT_WINDOWS_UNC", "")
# The Tolkien box's chat window: the same window folder as
# Tolkien mounts it. OFF until proven live, then widened by name like CHAT_WINDOWS was - the users are read per turn from
# <STATE_DIR>/<the Tolkien box>-window.json {"users": ["ClaudeSupervisor"] | ["*"]} (chat_window.tolkien_window_enabled_for), so turning it on
# needs no redeploy; absent file = off, and the Tolkien box rows keep the whole-order Fellowship turn (fellowship_turn).
CHAT_WINDOWS_TOLKIEN = os.getenv("HAI_CHAT_WINDOWS_TOLKIEN", "/ssdshared/tabfoundry-jobs/chat-windows")
STATUS_HEARTBEAT = float(os.getenv("STATUS_HEARTBEAT", "8"))

# --- Conversation context / compaction ---
HISTORY_MAX_MESSAGES = int(os.getenv("HISTORY_MAX_MESSAGES", "200"))
CONTEXT_RECENT_MESSAGES = int(os.getenv("CONTEXT_RECENT_MESSAGES", "12"))
# The conversation's share of the chat brain's MEASURED window (half of BRAIN_NUM_CTX tokens at the ~4 chars/token
# history.context_state counts by), not a fixed 18,000 chars. Overflow inside
# a turn is the model call's own 90 %-of-window compaction (ollama_client). This is the default for a chat with no known
# window; a chat with one gets the same share of THAT window.
CONTEXT_CHAR_BUDGET = int(os.getenv("CONTEXT_CHAR_BUDGET", str(2 * BRAIN_NUM_CTX)))
COMPACT_SOURCE_CHAR_LIMIT = int(os.getenv("COMPACT_SOURCE_CHAR_LIMIT", "30000"))

# --- Isolated coding runner ---
CODING_RUNNER_URL = os.getenv("CODING_RUNNER_URL", "http://coding-runner:8100").rstrip("/")
CODING_RUNNER_TOKEN = os.getenv("CODING_RUNNER_TOKEN", "")
CODING_RUNNER_TOKEN_FILE = os.getenv("CODING_RUNNER_TOKEN_FILE", "")
if not CODING_RUNNER_TOKEN and CODING_RUNNER_TOKEN_FILE:
    try:
        with open(CODING_RUNNER_TOKEN_FILE, encoding="utf-8") as _token_file:
            CODING_RUNNER_TOKEN = _token_file.read().strip()
    except OSError:
        CODING_RUNNER_TOKEN = ""
CODING_RUNNER_TIMEOUT = float(os.getenv("CODING_RUNNER_TIMEOUT", "75"))
# A TEST run is timed by the RUNNER (its CODING_TEST_TIMEOUT_SECONDS, 300 s by default, which it kills and reports as
# timed_out with the output so far). The caller must wait longer than that, or a slow run comes back as "coding runner
# unavailable: timed out" with no output while the runner keeps going.
# This is only the first-call floor; coding_client learns the runner's own figure from each result's `timeout_seconds`.
CODING_TEST_TIMEOUT = float(os.getenv("CODING_TEST_TIMEOUT", "360"))
CODING_TEST_MARGIN = 60.0     # past the runner's own kill, for its reply to travel back

# --- Independent coding review (always on the same lead host as the 27B lead) ---
CODING_REVIEW_HOST = HEAVY_HOST
CODING_REVIEW_MODEL = os.getenv("CODING_REVIEW_MODEL", HEAVY_MODEL)
# Fallback only: review/critic calls size their window by model with ctx_for (35b-a3b fits 84,992 since q8 KV).
CODING_REVIEW_NUM_CTX = int(os.getenv("CODING_REVIEW_NUM_CTX", "16384"))
CODING_REVIEW_TIMEOUT = float(os.getenv("CODING_REVIEW_TIMEOUT", "600"))

# --- Bounded multi-pass effort modes ---
THINK_HARD_BUDGET_SECONDS = int(os.getenv("THINK_HARD_BUDGET_SECONDS", "1800"))   # ~30-min CEILING (not a target;
# the convergence loop stops EARLY when the answer converges/stalls -- oomph as-needed, never forced compute)
THINK_HARD_MAX_PASSES = int(os.getenv("THINK_HARD_MAX_PASSES", "12"))   # draft + pool + iterate-to-convergence rounds
DEEP_TRACE_MAX_CHARS = int(os.getenv("DEEP_TRACE_MAX_CHARS", "12000"))
# Think-hard's critic = the LEAD_BOX itself - the same 3.8 now, a critic on another box
# when Spock places critics). The old second critic (qwen3.6:35b-a3b on the SAME card) unloaded the 3.8 on every pass
# (a ~42 s reload + a full re-read) and ran with thinking OFF - only the lead's model gets the priced effort - so it
# reviewed cold; with the critic blind to the reference notes it produced the "unsupported" findings of 10-16.
# A list here still unions findings (deliberation.merge_decorrelated) if a pool is configured again.
# How much of a critic's window its EVIDENCE may fill (app._critic_room_chars): the critic's own window, minus the room
# it needs to think and answer, minus the task + response under review. Both numbers are MEASURED:
# - the reply room = the longest thinking answer the lab has recorded on the 3.8;
# - characters per token = the MINIMUM seen on the 3.8's tokenizer across 46 real prompts, rounded down, so the estimate can only under-fill the window, never overflow it.
CRITIC_REPLY_RESERVE_TOKENS = int(os.getenv("CRITIC_REPLY_RESERVE_TOKENS", "28672"))
CRITIC_CHARS_PER_TOKEN = int(os.getenv("CRITIC_CHARS_PER_TOKEN", "2"))
_THINK_HARD_POOL_DEFAULT = HEAVY_MODEL
THINK_HARD_CRITIC_POOL = [m.strip() for m in
    os.getenv("THINK_HARD_CRITIC_POOL", _THINK_HARD_POOL_DEFAULT).split(",") if m.strip()]

# --- "Absurd" effort: the SAME think-hard convergence engine, run ASYNC with a much bigger budget. The
# user picks a max-time from ABSURD_TIME_CHOICES; the run goes detached (the lead slot serialized like
# any heavy request), persists its result, and notifies on completion. It still STOPS EARLY on
# convergence -- a bigger ceiling buys depth only when the task actually needs it, never forced compute.
ABSURD_ENABLED = os.getenv("ABSURD_ENABLED", "1").lower() not in ("0", "false", "no")
# (key, human label, seconds). Clamped by deliberation.ABSURD_MAX_BUDGET_SECONDS regardless.
ABSURD_TIME_CHOICES = [
    ("5m", "5 minutes", 300),   # a short budget a live proof of the clock, the soft hand-over and the hard cut can finish in
    ("1h", "1 hour", 3600),
    ("3h", "3 hours", 10800),
    ("4h", "4 hours", 14400),
    ("8h", "8 hours", 28800),
    ("all_night", "All night", 36000),
]
ABSURD_MAX_PASSES = int(os.getenv("ABSURD_MAX_PASSES", "80"))   # Users exempt from price-based launch deferral (run regardless of current energy cost).
# Defaults to admins.
ABSURD_ENERGY_UNBOUNDED_USERS = set(
    u.strip() for u in os.getenv("ABSURD_ENERGY_UNBOUNDED_USERS", ",".join(sorted(ADMIN_USERS))).split(",")
    if u.strip())
ABSURD_STATE_DIR = os.getenv("ABSURD_STATE_DIR", os.path.join(STATE_DIR, "absurd"))
# Absurd completion pings reuse the overnight webhook by default (same "unattended run self-reports" need).
ABSURD_NOTIFY_WEBHOOK = _secret_value("ABSURD_NOTIFY_WEBHOOK", "ABSURD_NOTIFY_WEBHOOK_FILE") or OVERNIGHT_NOTIFY_WEBHOOK

REMEDY_STATE_DIR = os.getenv("REMEDY_STATE_DIR", os.path.join(STATE_DIR, "hai-remedies"))

# --- Escalation feed (HAI-authored, job A): local ack-bookkeeping for coding-escalation packets
#     surfaced to humans. Same cross-user/durable pattern as remedies — persisted so the record
#     outlives container recreates.
ESCALATION_STATE_DIR = os.getenv("ESCALATION_STATE_DIR", os.path.join(STATE_DIR, "hai-escalations"))

# --- User corpus contributions (admin-gated additions to a persona-SHARED corpus). Cross-user + durable:
#     a user submits, an admin approves later, so the queue is persisted (not the in-memory pending_actions).
CONTRIB_STATE_DIR = os.getenv("CONTRIB_STATE_DIR", os.path.join(STATE_DIR, "contributions"))
CONTRIB_MAX_CHARS = int(os.getenv("CONTRIB_MAX_CHARS", "40000"))
# Submit-side notify reuses the same Discord webhook as overnight/absurd unless overridden.
CONTRIB_NOTIFY_WEBHOOK = _secret_value("CONTRIB_NOTIFY_WEBHOOK", "CONTRIB_NOTIFY_WEBHOOK_FILE") or OVERNIGHT_NOTIFY_WEBHOOK
# Deep-link target in the Discord notification: the head's admin review panel.
CONTRIB_PANEL_URL = os.getenv("CONTRIB_PANEL_URL", "")   # the review panel's address, for the DM's link

# --- Nursing think-hard: an optional MEDICAL second-opinion critique (MedGemma) that runs IN ADDITION
#     to the general qwen critique, as a user checkbox. It's completion-only (no tools), so MedGemma's
#     lack of tool-calling doesn't matter. It costs a 27B model swap on the lead (24GB can't hold both
#     27Bs), so nursing think-hard gets one extra pass slot + a larger time budget to absorb the swaps.
#     NURSING_CRITIQUE_ENABLED is a server kill-switch; the per-request checkbox (q.medical_critique) is
#     the actual opt-in. If MedGemma is absent or returns unparseable JSON, the critique degrades to
#     'unavailable' (the qwen critique + first pass still stand).
NURSING_CRITIQUE_MODEL = os.getenv("NURSING_CRITIQUE_MODEL", "")
NURSING_CRITIQUE_ENABLED = os.getenv("NURSING_CRITIQUE_ENABLED", "1").lower() not in ("0", "false", "no")
NURSING_THINK_HARD_MAX_PASSES = int(os.getenv("NURSING_THINK_HARD_MAX_PASSES", "5"))   # general(4) + 1 medical slot
NURSING_THINK_HARD_BUDGET_SECONDS = int(os.getenv("NURSING_THINK_HARD_BUDGET_SECONDS", "1200"))  # pool + ADDITIVE medical + refine (incl. MedGemma swap)

# Self-serve document upload: a nursing user uploads a PDF/DOCX/txt/md, tags its intent (source to
# cite / their own draft for voice+context / assignment rubric), and it's extracted to markdown and
# written into THEIR corpus dir (RAG auto-reloads). Only nursing namespaces are writable upload targets.
# 1 GB The file is
# read into memory whole, so this is bounded by Kirk's RAM, not by taste.
UPLOAD_MAX_BYTES = int(os.getenv("UPLOAD_MAX_BYTES", str(1024 * 1024 * 1024)))

# --- Semantic retrieval: hybrid BM25 + embeddings. Embeddings come from an Ollama embed model on the
#     brain hosts; if none is reachable, retrieval silently falls back to BM25-only (today's behavior).
#     The BM25 relevance gate is preserved unchanged; a chunk is ALSO pulled in when its cosine similarity
#     to the query clears EMBED_MIN_SIM (an ABSOLUTE floor, so off-topic queries still get nothing). Final
#     order = Reciprocal Rank Fusion of the BM25 and cosine rankings.
EMBED_MODEL = os.getenv("EMBED_MODEL", "")
# Embeddings need an embedding model actually set: with none, retrieval is BM25 only (said once at startup).
EMBED_ENABLED = os.getenv("EMBED_ENABLED", "1").lower() not in ("0", "false", "no") and bool(EMBED_MODEL)
# The brain box is the PRIMARY embed host. STANDING RULE: The failover box is the Hyper-V/Plex host — FAILOVER ONLY,
# never the primary (keep its GPU free for Plex). The brain box's Ollama must be current enough to serve
# /api/embed (older builds 404); when it can't, embeds fall back to the failover box failover, then BM25-only.
EMBED_HOSTS = [h.strip() for h in os.getenv("EMBED_HOSTS", BRAIN_HOST + "," + FAILOVER_HOST).split(",") if h.strip()]
EMBED_TIMEOUT = float(os.getenv("EMBED_TIMEOUT", "30"))          # read timeout (a big batch can take a few s)
EMBED_CONNECT_TIMEOUT = float(os.getenv("EMBED_CONNECT_TIMEOUT", "3"))  # fail fast past an unreachable host
EMBED_CACHE_DIR = os.getenv("EMBED_CACHE_DIR", os.path.join(STATE_DIR, "embed-cache"))
EMBED_MIN_SIM = float(os.getenv("EMBED_MIN_SIM", "0.55"))  # cosine floor for a semantic-only chunk
# How long to stop dialing an embed host that just failed. The brain box (the PRIMARY) sleeps on a 30-min
# idle timer by design, and without this every embed call pays EMBED_CONNECT_TIMEOUT before reaching
# the always-on failover. Short on purpose: a woken host must return to service on its own.
EMBED_HOST_COOLDOWN = float(os.getenv("EMBED_HOST_COOLDOWN", "60"))
# Hosts to keep short-lived transient work (embeds) OFF of, to preserve capacity for the load-
# bearing services those boxes run. The failover box is the default LOAD-BEARING host — a transient blip there competes with services users depend
# on, which is a costlier tradeoff than the brain box's chat model occasionally reloading. BH's 3070
# is usually idle and is the natural home for embeds; when the lead is running the 27b, BH has
# even less to do. Comma-separated URLs. Deprioritises rather than excludes: if only avoided hosts
# are up, use them rather than losing retrieval entirely.
EMBED_AVOID_HOSTS = [h.strip() for h in
                     os.getenv("EMBED_AVOID_HOSTS", FAILOVER_HOST).split(",")
                     if h.strip()]
# Timeout for heavyweight calls queuing behind a RENDER. Training runs are refused immediately (no wait).
COLLISION_GATE_WAIT = int(os.getenv("COLLISION_GATE_WAIT", "300"))
# nomic-embed-text is asymmetric — it wants task prefixes (docs vs query). Override if swapping models.
EMBED_DOC_PREFIX = os.getenv("EMBED_DOC_PREFIX", "search_document: ")
EMBED_QUERY_PREFIX = os.getenv("EMBED_QUERY_PREFIX", "search_query: ")
RRF_K = int(os.getenv("RRF_K", "60"))
# Retrieval depth for search_docs. Keep >=8 to avoid missing key chunks
# in long documents (e.g., syllabi) that rank below top-4.
DOCS_TOPK = int(os.getenv("DOCS_TOPK", "8"))

# Tool selection via embedding retrieval (nomic-embed-text) over descriptions,
# personal corpus, and in-flight state. Avoids keyword matching/regex which
# cannot handle ambiguous intent (e.g., "candidates" could mean resumes or fantasy drafts).
TOOL_SELECT_ENABLED = os.getenv("TOOL_SELECT_ENABLED", "1").lower() not in ("0", "false", "no")
# Share of the context window the tool schemas may occupy. At 0.30 the 27B (65,536) never engages
# selection at today's 19k heavy toolbox, while the 8B (16,384) gets a 4,915-token box and keeps 70%
# of its window to actually think in.
TOOL_SELECT_BUDGET_FRAC = float(os.getenv("TOOL_SELECT_BUDGET_FRAC", "0.30"))
# Thresholds use the LEAD_BOX over the query's median cosine, not raw cosine.
# Raw scores are compressed (e.g., nomic-embed-text 0.36-0.76), making absolute
# floors unreliable; the lead is scale-free and separates intent cleanly.
TOOL_SELECT_MIN_LEAD = float(os.getenv("TOOL_SELECT_MIN_LEAD", "0.05"))
# Also require a share of the TOP lead, so a confident request gets a tight box of the tools that are actually
# relevant rather than everything above the waterline. There is NO tool-count cap (removed - relevance alone decides; the context budget above is the only ceiling.
TOOL_SELECT_TOP_FRAC = float(os.getenv("TOOL_SELECT_TOP_FRAC", "0.35"))
# Uncertainty is the picks DISAGREEING with each other, not the top score being low — measured, and
# the distinction is the whole ballgame. "dedupe the met-form-v2 dataset" leads by 0.107 and "review
# the candidates for my fantasy football league" by 0.105: indistinguishable. But the first picks six
# tools that all mean the same thing (mean pairwise cosine 0.805) and the second picks a scatter of
# dataset + request + library tools (0.732). Scored on lead alone, a UNANIMOUS read was being reported
# as doubt. Observed range 0.667 (junk) to 0.814 (clean infra request).
TOOL_SELECT_COHESION = float(os.getenv("TOOL_SELECT_COHESION", "0.76"))
# The asker's own corpus is retrieved per request anyway; these top chunks EXPAND the query so tool
# matching is grounded in that person's documents ("candidates" means whatever it means to THEM).
# The conversation is how a PERSON disambiguates "I'm done with those" — not a state file. The model
# is handed this history already; the selector reading the same thing removes an asymmetry, and it
# works for domains that have no in-flight state at all. Tool results are excluded (big, and data
# rather than intent).
TOOL_SELECT_HISTORY_TURNS = int(os.getenv("TOOL_SELECT_HISTORY_TURNS", "4"))
TOOL_SELECT_HISTORY_CHARS = int(os.getenv("TOOL_SELECT_HISTORY_CHARS", "800"))
TOOL_SELECT_HISTORY_WEIGHT = float(os.getenv("TOOL_SELECT_HISTORY_WEIGHT", "0.6"))
TOOL_SELECT_CORPUS_K = int(os.getenv("TOOL_SELECT_CORPUS_K", "3"))
TOOL_SELECT_CORPUS_CHARS = int(os.getenv("TOOL_SELECT_CORPUS_CHARS", "600"))
# How far the corpus reading is allowed to STEER the raw one (halved when the corpus answered only
# weakly). This is a blend, not a max, because the corpus must be able to redirect away from a tool,
# not merely add: "review the candidates" against a corpus of hiring notes is about DOCUMENTS.
TOOL_SELECT_CORPUS_WEIGHT = float(os.getenv("TOOL_SELECT_CORPUS_WEIGHT", "0.5"))
# Nudge for tools whose in-flight state is actually open (a live candidate review). State is a FACT,
# so it earns a boost — but not an override, or "close that review" would hijack unrelated requests.
TOOL_SELECT_STATE_BOOST = float(os.getenv("TOOL_SELECT_STATE_BOOST", "0.12"))

# --- SearXNG (web search) ---
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080").rstrip("/")

# --- The specialist who READS a draft for the lead's medical errand, with no tools. The window is its fit probe's maximum.
MEDICAL_REVIEW_MODEL = os.getenv("MEDICAL_REVIEW_MODEL", "")
MEDICAL_REVIEW_NUM_CTX = int(os.getenv("MEDICAL_REVIEW_NUM_CTX", "25600"))

# --- PubMed evidence search (search_evidence tool) — fabrication-proof citation engine for the
#     nursing-tutor feature. NCBI E-utilities, no key required (3 req/s); an NCBI_API_KEY raises it to 10/s.
PUBMED_EUTILS_URL = os.getenv("PUBMED_EUTILS_URL", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils")
NCBI_API_KEY = _secret_value("NCBI_API_KEY", "NCBI_API_KEY_FILE")
EVIDENCE_RECENCY_YEARS = int(os.getenv("EVIDENCE_RECENCY_YEARS", "5"))
EVIDENCE_MAX_RESULTS = int(os.getenv("EVIDENCE_MAX_RESULTS", "5"))
EVIDENCE_TIMEOUT = float(os.getenv("EVIDENCE_TIMEOUT", "12"))
EVIDENCE_ABSTRACT_CHARS = int(os.getenv("EVIDENCE_ABSTRACT_CHARS", "700"))  # per-article abstract cap

# --- read_config tool: allow-listed, read-only host config dirs (bind-mounted RO into the
#     container; NEVER mount secrets — compose override / .env / secrets/ carry HA_TOKEN etc.) ---
# --- host-ops broker (Phase 4): the orchestrator is socket-free; it asks the `hostops` sidecar (which holds
# the docker socket) to perform whitelisted host/docker actions. Token-authed; protected containers can't be
# restarted via the broker even on a confirmed call. ---
# The suffix that makes a tablet's name resolvable on this network (".lan" behind a Pi-hole, often ".local"); "" =
# bare names. The same setting name the tablet foundry reads.
TABLET_DNS_SUFFIX = os.getenv("TABLET_DNS_SUFFIX", "")
# The tablet foundry's inbox as the HOST sees it (a camera scan order names it for the operator); "" = unknown.
TABLET_INBOX_HOST = os.getenv("TABLET_INBOX_HOST", "")
# The compose stack's folder on the head's box (the deploy and workspace tools live beside it); "" = unknown.
STACK_DIR = os.getenv("DEPLOY_COMPOSE_DIR", "")
HOSTOPS_URL = os.getenv("HOSTOPS_URL", "http://hostops:9120").rstrip("/")
HOSTOPS_TOKEN = _secret_value("HOSTOPS_TOKEN", "HOSTOPS_TOKEN_FILE")
HOSTOPS_TIMEOUT = float(os.getenv("HOSTOPS_TIMEOUT", "30"))
# Registered-host SSH admin (#34/#35): an SSH command can be slow (a device flash runs minutes), so its
# broker call gets its own generous budget instead of the 30s default.
HOSTOPS_HOST_EXEC_TIMEOUT = float(os.getenv("HOSTOPS_HOST_EXEC_TIMEOUT", "1200"))
WATCHTOWER_RUN_TIMEOUT = float(os.getenv("WATCHTOWER_RUN_TIMEOUT", "300"))   # pulls + recreates can take a while
# Containers the broker never stops or restarts. A deployment adds its own (a VPN container another service's network
# lives in, say) through HOSTOPS_PROTECTED; the default protects the broker, the head and DNS.
HOSTOPS_PROTECTED = {c.strip() for c in os.getenv(
    "HOSTOPS_PROTECTED", "hostops,orchestrator,pihole").split(",") if c.strip()}

# --- disk_usage tool (Phase 4): read-only free-space + folder-size visibility on the mounts the orchestrator
# can see. Free space is instant (statvfs); folder sizes (du) are opt-in + timeout-bounded since the media
# array is 45T. Path-scoped to these roots — refuses anything outside. ---
DISK_ROOTS = {
    "media": os.getenv("DISK_MEDIA_DIR", "/media"),          # the 45T library array (RO mount)
    "downloads": os.getenv("DISK_DOWNLOADS_DIR", "/downloads"),  # SSD download staging (RO mount)
    "docs": os.getenv("DISK_DOCS_DIR", "/docs"),            # the documentation/State volume
}
DISK_DU_TIMEOUT = float(os.getenv("DISK_DU_TIMEOUT", "25"))

CONFIG_READ_DIRS = [d.strip() for d in os.getenv(
    "CONFIG_READ_DIRS", "/hostcfg/homepage,/hostcfg/caddy,/hostcfg/pihole").split(",") if d.strip()]
CONFIG_READ_MAX_BYTES = int(os.getenv("CONFIG_READ_MAX_BYTES", "16384"))
# write_config tool: ADMIN-gated targeted find/replace into these dirs (default = the read dirs).
# Requires the matching bind mounts to be read-WRITE (:rw) in docker-compose.override.yml.
CONFIG_WRITE_DIRS = [d.strip() for d in os.getenv(
    "CONFIG_WRITE_DIRS", ",".join(CONFIG_READ_DIRS)).split(",") if d.strip()]

# --- fetch_url tool: allow-listed LAN host:port targets for live HTTP GET (no arbitrary SSRF) ---
# The head's own LAN-reachable base URL, so a head can read its own status endpoint over the LAN.
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "").strip()


def _host_port(url):
    """host:port for a URL, or "" when the URL is empty or unparseable."""
    try:
        from urllib.parse import urlparse
        u = urlparse(str(url or ""))
        if not u.hostname:
            return ""
        return f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}"
    except Exception:
        return ""


_explicit_allow = os.getenv("LAN_FETCH_ALLOW")
LAN_FETCH_ALLOW = {hp.strip() for hp in (_explicit_allow or "").split(",") if hp.strip()}
if _explicit_allow is None:
    # No setting given: allow exactly the boxes the settings name, reduced to host:port. An explicit
    # LAN_FETCH_ALLOW (even an empty one) wins and is taken literally.
    for _url in (BRAIN_HOST, FAILOVER_HOST, HEAVY_HOST, HA_URL, ORCHESTRATOR_URL,
                 *(device_setting(_d["name"], "GLANCES") for _d in ROSTER["devices"])):
        _hp = _host_port(_url)
        if _hp:
            LAN_FETCH_ALLOW.add(_hp)
LAN_FETCH_MAX_BYTES = int(os.getenv("LAN_FETCH_MAX_BYTES", "16384"))
LAN_FETCH_TIMEOUT = float(os.getenv("LAN_FETCH_TIMEOUT", "15"))
# Public pages: read as text in pages of WEB_FETCH_PAGE_CHARS (offset reads on, so nothing is cut). The download
# bound only protects the container's memory from a runaway response, and it says so when it binds.
WEB_FETCH_PAGE_CHARS = int(os.getenv("WEB_FETCH_PAGE_CHARS", str(LAN_FETCH_MAX_BYTES)))
WEB_FETCH_MAX_DOWNLOAD = int(os.getenv("WEB_FETCH_MAX_DOWNLOAD", str(32 * 1024 * 1024)))
WEB_FETCH_MAX_REDIRECTS = int(os.getenv("WEB_FETCH_MAX_REDIRECTS", "10"))
WEB_FETCH_TIMEOUT = float(os.getenv("WEB_FETCH_TIMEOUT", "30"))
WEB_FETCH_USER_AGENT = os.getenv("WEB_FETCH_USER_AGENT", "HAI-research-reader/1.0 (read-only; homelab)")
