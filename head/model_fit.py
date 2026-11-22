"""Measured Ollama memory requirements; pure arithmetic shared by policy compilation and placement.

Profiles describe TEXT model orders at their measured windows, not rendering/training, intentional offload,
or Strata's separate placement. An unmeasured window is unknown, never proof that a load will fit.
"""
import math

MIB = 1024 * 1024
TRIAGE_PAYLOADS = frozenset(("triage_classify.py", "triage_rewrite.py", "native_history_review.py"))
TEXT_PAYLOADS = TRIAGE_PAYLOADS | {"infer_ollama.py", "helper_window.py"}
OPAQUE_PAYLOADS = frozenset(("a1_order.py",))


def requirement(spec, caps=()):
    """The order's existing model/window, or None for a different allocation protocol.

    Opaque payloads declare model_memory rather than making placement open a plan on another box.
    No caller's context is changed to make it fit. Explicit offload and Strata have different memory semantics.
    """
    spec = spec or {}
    if str(spec.get("mode") or "").lower() == "cpu" or spec.get("load") == "cpu":
        return None
    args = list(spec.get("args") or [])
    base = str(spec.get("payload") or "").replace("\\", "/").rsplit("/", 1)[-1]
    declared = spec.get("model_memory")
    if base == "fit_probe.py" or "--offload-ok" in args or "--strata" in args:
        return None
    if declared and base not in TEXT_PAYLOADS and (
            declared.get("engine", "ollama") != "ollama" or declared.get("offload_ok")):
        return None

    def arg(name):
        try:
            return args[args.index(name) + 1]
        except (ValueError, IndexError):
            return None

    if not declared and base not in TEXT_PAYLOADS | OPAQUE_PAYLOADS and not (arg("--model") and arg("--num-ctx")):
        return None
    declared = declared or {}
    model = arg("--model") or declared.get("model")
    ctx = arg("--num-ctx") or declared.get("num_ctx")
    if not model and base in TRIAGE_PAYLOADS:
        model = next((c[len("triage.model."):] for c in caps if c.startswith("triage.model.")), None)
    if not ctx and base in TRIAGE_PAYLOADS:
        ctx = next((c[len("triage.ctx."):] for c in caps if c.startswith("triage.ctx.")), None)
    try:
        ctx = int(ctx)
    except (TypeError, ValueError):
        ctx = None
    need = {"model": model, "num_ctx": ctx}
    if arg("--host"):
        need["serving_host"] = arg("--host")
    return need


def compile_profiles(documents, rules, engine_version="0.40.5"):
    """documents = iterable of (provenance dict, fit.json). No estimated overhead or invented card sizes."""
    groups = {}
    for source, doc in documents:
        box = doc.get("box")
        if not box:
            continue
        for row in doc.get("rows") or []:
            mem = row.get("spill")
            if not isinstance(mem, dict) or not row.get("model") or not row.get("num_ctx") or not row.get("size"):
                continue
            if any(not isinstance(mem.get(k), (int, float)) for k in
                   ("dedicated_mb", "shared_mb", "card_mb")):
                continue
            if mem["card_mb"] <= 0 or mem["dedicated_mb"] < 0 or mem["shared_mb"] < 0:
                continue
            groups.setdefault((box, row["model"]), []).append((doc.get("measured_at", ""), source, row))
    hosts = {}
    for (box, model), entries in sorted(groups.items()):
        fitted = lambda r: r.get("fits") and r.get("size_vram", 0) >= r["size"]  # noqa: E731
        fits = [r for _, _, r in entries if fitted(r)]
        if not fits:
            continue
        overhead = max(0.0, max(r["spill"]["dedicated_mb"] - r["size"] / MIB for r in fits))
        points, sources = {}, {}
        for _, source, row in sorted(entries, key=lambda e: e[0]):
            mem = row["spill"]
            ctx = int(row["num_ctx"])
            # Windows that fit are measured whole: their dedicated allocation is the requirement. \n# Only windows that spilled use the max(dedicated, size + overhead) estimate,\n# since their dedicated figure is capped by the card limit.
            need = mem["dedicated_mb"] if fitted(row) else max(mem["dedicated_mb"],
                                                               math.ceil(row["size"] / MIB + overhead))
            points[ctx] = {"num_ctx": ctx, "size": int(row["size"]), "required_mib": need,
                           "shared_mib": mem["shared_mb"], "card_mib": mem["card_mb"]}
            sources[source["path"]] = source
        hosts.setdefault(box, {})[model] = {"engine_version": engine_version,
                                           "engine_overhead_mib": round(overhead, 3),
                                           "points": [points[k] for k in sorted(points)],
                                           "sources": list(sources.values())}
    return {"rules": dict(rules), "hosts": hosts}


def has_profile(profiles, host, model):
    """True when a measured memory profile exists for `model` on `host`. The one lookup: check() refuses a window
    without one, and the weighing passes over such a row."""
    return bool(((profiles or {}).get("hosts", {}).get(host) or {}).get(model))


def fit_ctx(profiles, host, model, ctx_max, sample, fresh=True):
    """Return the largest measured window <= ctx_max that check() passes, or (None, verdict) if none fit.\n    Non-arithmetic refusals (missing profile, stale report, unmeasured engine) are returned at ctx_max unchanged;\n    only fit arithmetic steps down to smaller measured points."""
    first = check(profiles, host, {"model": model, "num_ctx": ctx_max}, sample, fresh=fresh)
    if first.get("ok") or "predicted_mib" not in first:
        return ctx_max, first
    profile = ((profiles or {}).get("hosts", {}).get(host) or {}).get(model) or {}
    for n in sorted({p["num_ctx"] for p in profile.get("points") or [] if p["num_ctx"] < ctx_max}, reverse=True):
        v = check(profiles, host, {"model": model, "num_ctx": n}, sample, fresh=fresh)
        if v.get("ok"):
            return n, v
        first = v
    return None, first


def check(profiles, host, need, sample, fresh=True):
    """Return {ok, why, ...}; missing evidence means WAIT, not a load to find out.

    The counter snapshot measures current adapter use and OUR private allocation together. Replacing our model
    costs other tenants + the target footprint, not current total + another copy of our model.
    """
    if need is None:
        return {"ok": True, "why": "different allocation protocol"}

    def no(why, **extra):
        return dict(ok=False, why="model fit: " + why, **extra)

    model, ctx = need.get("model"), need.get("num_ctx")
    if not model or not isinstance(ctx, int) or ctx <= 0:
        return no("model/window not declared")
    profile = (profiles.get("hosts", {}).get(host) or {}).get(model)
    if not has_profile(profiles, host, model):
        return no("no measured memory profile for %s on %s" % (model, host))
    candidates = [p for p in profile["points"] if p["num_ctx"] >= ctx]
    if not candidates:
        return no("%s @ %d exceeds its measured memory points" % (model, ctx))
    point = min(candidates, key=lambda p: p["num_ctx"])
    if not fresh or not sample or sample.get("stale"):
        return no("no fresh card-memory report")
    if sample.get("ollama_version") != profile["engine_version"]:
        return no("engine version is unmeasured (%s; profile %s)" %
                  (sample.get("ollama_version") or "unknown", profile["engine_version"]))
    mem = sample.get("gpu_memory")
    if not isinstance(mem, dict) or not mem.get("counter_ok"):
        return no("GPU allocation counters could not be read")
    if mem.get("card_mb") != point["card_mib"]:
        return no("card size differs from the measured profile")
    owned, total = mem.get("dedicated_mb"), mem.get("adapter_mb")
    if owned is None and total is None and mem.get("shared_mb") is None:
        # A successful counter query found no allocation for any Ollama/llama-server PID. The existing per-card
        # reporter is the ambient reading; an unsuccessful query above NEVER reaches this path.
        owned, total = 0, sample.get("vram_used_mb")
    if not isinstance(owned, (int, float)) or not isinstance(total, (int, float)) or not 0 <= owned <= total:
        return no("card allocation snapshot is incomplete or inconsistent")
    other = total - owned
    predicted = math.ceil(other + point["required_mib"])
    rules = profiles.get("rules") or {}
    if not all(isinstance(rules.get(k), (int, float)) for k in ("full_margin_mib", "shared_min_mib")):
        return no("spill rules are not declared")
    full_line = point["card_mib"] - rules["full_margin_mib"]
    spill = point["shared_mib"] >= rules["shared_min_mib"] and predicted >= full_line
    detail = {"predicted_mib": predicted, "other_mib": round(other, 3), "required_mib": point["required_mib"],
              "measured_num_ctx": point["num_ctx"], "card_mib": point["card_mib"], "full_line_mib": full_line}
    if predicted > point["card_mib"] or spill:
        return no("%s @ %d needs %d MiB including other tenants; does not fit before load" %
                  (model, ctx, predicted), **detail)
    return dict(ok=True, why="measured fit before load", **detail)
