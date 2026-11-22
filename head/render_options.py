"""Account renderer preference and truthful metadata for the existing image order path."""
import datetime
import json
import ntpath
from pathlib import Path
import re
import threading

import config

# `model` is the base an identity adapter must be trained on (person_record's `base`); `requires` is the factory
# capability that renders it. A FLUX.2 adapter cannot load into Krea 2, so the base is a filter, never a fallback.
# The renderers live on the lead (the roster's `lead` device): each id is the engine plus that device's name, so a saved
# choice and a recorded order keep naming the same renderer, and the label is what a person reads.
RENDER_HOST = config.LEAD_BOX
_HOST_LABEL = config.LEAD_BOX_LABEL
FLUX2_RENDERER = "flux2_%s" % RENDER_HOST
KREA2_RENDERER = "krea2_%s" % RENDER_HOST
RENDERERS = {
    "auto": {"label": "Auto", "host": "", "model": "flux2-dev", "requires": "render.flux2"},
    FLUX2_RENDERER: {"label": "Flux.2 · %s" % _HOST_LABEL, "host": RENDER_HOST, "model": "flux2-dev",
                     "requires": "render.flux2"},
    KREA2_RENDERER: {"label": "Krea 2 · %s" % _HOST_LABEL, "host": RENDER_HOST, "model": "krea2",
                     "requires": "render.krea2"},
}
REFERENCE_RENDERER = "krea2_reference_%s" % RENDER_HOST
RENDERERS[REFERENCE_RENDERER] = {
    "label": "Krea 2 Reference · %s" % _HOST_LABEL, "host": RENDER_HOST, "model": "krea2",
    "requires": "render.krea2", "reference_edit": True, "restricted": True,
}
# A renderer may carry a SECOND adapter (`second_adapter`, `second_adapter_strength`): the factory loads it only when it
# is the one that factory names in its KREA2_SECOND_ADAPTER setting. The core defines none; an extension may add them,
# with the renderer each one stands in for (SECOND_ADAPTER_RENDERER for plain Krea 2, REFERENCE_SECOND_ADAPTER_RENDERER
# for the reference renderer) and the words that ask for it (add_second_adapter_cue).
SECOND_ADAPTER_RENDERER = None
REFERENCE_SECOND_ADAPTER_RENDERER = None
SECOND_ADAPTER_CUE = re.compile(r"(?i)\bboth adapters\b|\bwith (?:the )?second adapter\b")
NO_SECOND_ADAPTER_CUE = re.compile(r"(?i)\b(?:reference|identity)[ -]only\b|\bwithout (?:the )?second adapter\b")


def add_second_adapter_cue(pattern):
    """More words that ask for the second adapter (an extension's)."""
    global SECOND_ADAPTER_CUE
    SECOND_ADAPTER_CUE = re.compile(SECOND_ADAPTER_CUE.pattern + "|" + pattern)


def _load_extensions(here=None):
    """Every module beside this one named render_options_ext_<name>.py calls extend(this module): it may add renderers,
    set the second-adapter renderers and add cue words. A head without such a file has only the renderers above."""
    import glob
    import importlib
    import os
    import sys
    here = here or os.path.dirname(os.path.abspath(__file__))
    for path in sorted(glob.glob(os.path.join(here, "render_options_ext_*.py"))):
        importlib.import_module(os.path.splitext(os.path.basename(path))[0]).extend(sys.modules[__name__])


def available(user):
    """The renderers this user may pick: a restricted one (experimental) goes only to the owner (OWNER_USER)."""
    return {k: v for k, v in RENDERERS.items()
            if not v.get("restricted") or (config.OWNER_USER and user == config.OWNER_USER)}


BASE_LABELS = {"flux2-dev": "Flux.2", "krea2": "Krea 2"}
_PREF_LOCK = threading.Lock()
_SELF = re.compile(
    r"(?i)^\s*(me|myself)\b|\bof\s+(me|myself)\b"
    r"|\b(?:draw|paint|render|depict|illustrate|portray|showing)\s+(me|myself)\b(?!\s+(?:a|an|the|another|some|one)\b)"
    r"|\bmy\s+(portrait|selfie|likeness)\b")


def self_reference(text):
    match = _SELF.search(str(text or ""))
    return next((g for g in match.groups() if g), None) if match else None


def _path(user):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(user or "Guest"))[:64] or "Guest"
    return Path(config.STATE_DIR) / "user-prefs" / (safe + ".renderer.json")


def preference(user):
    try:
        key = json.loads(_path(user).read_text(encoding="utf-8"))["renderer"]
        return key if key in available(user) else "auto"
    except (OSError, ValueError, KeyError, TypeError):
        return "auto"


def automatic_setup(user):
    """Last chosen image setup behind Auto; Auto itself remains an optional UI override."""
    try:
        data = json.loads(_path(user).read_text(encoding='utf-8'))
        key = data.get('automatic_renderer') or data.get('renderer')
        return key if key in available(user) else 'auto'
    except (OSError, ValueError, TypeError):
        return 'auto'


def choose(user, renderer=None):
    key = preference(user) if renderer is None else renderer
    if key not in available(user):
        raise ValueError("Choose one of these image renderers: %s."
                         % ", ".join(v["label"] for v in available(user).values()))
    return key, dict(RENDERERS[key])


def base_of(model):
    """The base an identity adapter was trained on. Records from before the field are all FLUX.2 adapters."""
    return model.get("base") or "flux2-dev"


def models_for(models, rendering):
    """The adapters a renderer can actually load: those trained on its base."""
    return [m for m in models if base_of(m) == rendering["model"]]


def missing_base_reason(name, models, rendering):
    """Why a person with identity models has none for this renderer, naming what they DO have."""
    have = sorted({BASE_LABELS.get(base_of(m), base_of(m)) for m in models})
    want = BASE_LABELS.get(rendering["model"], rendering["model"])
    return ("%s %s a saved identity model for %s, but none has been trained on %s yet, so I haven't queued a "
            "render or substituted a look-alike. Switch Images to %s to use the existing one."
            % (name, "have" if name == "You" else "has", " and ".join(have), want, have[0]))


def save(user, renderer):
    key, _ = choose(user, renderer)
    path = _path(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with _PREF_LOCK:
        try:
            previous = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {}
        setup = key if key != 'auto' else previous.get('automatic_renderer') or previous.get('renderer','auto')
        data = {"renderer": key, "automatic_renderer": setup,
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    return key


def own_identity(user, renderer=None):
    if RENDERERS.get(renderer or preference(user), {}).get("reference_edit"):
        return {"available": False, "reason": "A reference photo is selected automatically for the person you name; browsing photos is optional."}
    import person_record
    key = person_record.resolve(user)
    if not key:
        return {"available": False, "reason": "No saved identity model is registered for your account."}
    all_models = [m for m in person_record.usable_models(key, requesting_user=user, use="personal")
                  if m.get("kind") == "image_lora"]
    if not all_models:
        return {"available": False, "reason": "Your saved identity model is not currently available for use."}
    try:
        _, rendering = choose(user, renderer)
    except ValueError:
        rendering = RENDERERS["auto"]
    models = models_for(all_models, rendering)
    if not models:
        return {"available": False, "reason": missing_base_reason("You", all_models, rendering)}
    return {"available": True, "name": person_record.record(key).get("name") or key,
            "model": ntpath.splitext(ntpath.basename(models[0].get("path") or ""))[0]}


def describe(job):
    """Read the order's actual adapter and host, never a UI selection or wish."""
    lora = job.get("lora_path")
    identity = ("Identity model: %s · %s" % (job.get("person") or "saved identity",
                ntpath.splitext(ntpath.basename(lora))[0]) if lora else
                "No identity model" if "lora_path" in job else "Identity model not recorded")
    if job.get("reference"):
        identity = "Reference: %s · %s · Identity Edit V2" % (
            job["reference"].get("name") or job.get("person"), job["reference"]["filename"])
    host = job.get("claimed_by") or job.get("host") or ""
    label = (RENDERERS.get(job.get("renderer"), {}).get("label") or
             BASE_LABELS.get(job.get("render_model") or "flux2-dev", job.get("render_model")))
    if lora and "strength" in job:
        identity += " · strength %g" % job["strength"]
    if job.get("second_adapter"):
        identity += " · second adapter strength %g" % job["second_adapter_strength"]
    if "seed" in job:
        identity += " · seed %d" % job["seed"]
    if "width" in job and "height" in job:
        identity += " · %d×%d" % (job["width"], job["height"])
    return {"renderer": label, "host": host,
            "identity": identity,
            "description": "%s%s · %s" % (label, (" · " + host) if host else " · compatible factory", identity)}


_load_extensions()
