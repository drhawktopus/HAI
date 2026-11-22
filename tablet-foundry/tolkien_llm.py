"""`llm` order kind: serves a single model via llama-server on one of two units:

  rigby   The head unit (rigby) running on CPU only.
  voltron rigby plus healthy wire tablets serving the model as one machine via ggml RPC.

Both arms run identical tasks with the same prompt, sampling, and scorer; the only variable is layer placement.

    {"kind": "llm", "name": "...", "model": "Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf",
     "payload": "/ssdshared/tabfoundry-jobs/payloads/map_swarm_code_hard_v2.py",
     "tasks": ["roman_to_int", ...], "replicates": 1, "arms": ["rigby", "voltron"]}

If `draft_host` and `draft_model` are set on a rigby arm, the target model stays on rigby's CPU and only the
draft model runs on the remote tablet. The draft host must not hold any layers of the target model.

Legacy orders used kind "voltron" with arms "local"/"pooled"; normalize() maps these.

Binaries and models are read-only mounts. TOLKIEN_LLAMA_BIN (x86 llama-server + libs), TOLKIEN_LLAMA_ARM64
(ggml-rpc-server + libomp.so for tablets). The ARM64 binaries must be from the same llama.cpp commit as x86
to ensure RPC protocol compatibility. Voltron backends are started via ssh (for Termux environment) and killed
via adb as root, because a setsid'd rpc-server is invisible to its own uid over ssh while holding the port."""
import functools
import glob
import hashlib
import importlib.util
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import types
import urllib.request
import uuid
import zlib

import grade_jobs  # baked from the GPU foundries' build (Dockerfile COPY --from=foundry): one way to read an answer
import helper_window as HW  # baked the same way: rigby's chat window is the GPU boxes' window program (run_window)
import tablet_foundry as tf

LLAMA_BIN = os.environ.get("TOLKIEN_LLAMA_BIN", "/llama/bin")
LLAMA_ARM64 = os.environ.get("TOLKIEN_LLAMA_ARM64", "/llama/arm64")
# rigby's GT 730 (Kepler, 2 GB): only the host's Vulkan build drives it, and its fp16 / cooperative-matrix shader paths
# hang on Kepler - RUNBOOK-GT730-Kepler-nvidia470-Ubuntu-24.04 § "Running llama.cpp on a Kepler card"
LLAMA_VULKAN = os.environ.get("TOLKIEN_LLAMA_VULKAN", "/llama/vulkan")
# 470 driver requires host's Vulkan loader + libraries (read-only /hostlib) and /usr/share/glvnd; image's glibc causes ICD to fail vkCreateInstance.
HOSTLIB = os.environ.get("TOLKIEN_HOSTLIB", "/hostlib")
KEPLER_VK_ENV = {"GGML_VK_DISABLE_F16": "1", "GGML_VK_DISABLE_COOPMAT": "1", "GGML_VK_DISABLE_COOPMAT2": "1"}
MODELS = os.environ.get("TOLKIEN_MODELS", "/models")
# Tolkien's share: the folder its inbox and outbox sit in (TABLET_OUTBOX's parent, tablet_foundry's default included).
# What it publishes for other desks, and the engine files it accepts, live there.
JOBS_ROOT = os.path.dirname(os.environ.get("TABLET_OUTBOX", "/ssdshared/tabfoundry-jobs/outbox").rstrip("/"))
MODELS_MANIFEST = os.environ.get("TOLKIEN_MODELS_MANIFEST", JOBS_ROOT + "/rigby-models.json")
RPC_PORT = int(os.environ.get("TOLKIEN_LLM_RPC_PORT", "50052"))
SERVER_PORT = int(os.environ.get("TOLKIEN_LLM_PORT", "8091"))
LOAD_TIMEOUT_S = float(os.environ.get("TOLKIEN_LLM_LOAD_S", "2400"))
# An answer is ended by SILENCE, never by elapsed time (the same window as infer_ollama).
IDLE_S = float(os.environ.get("TOLKIEN_LLM_IDLE_S", "600"))
PROBE_S = 180.0   # a loaded server answers a 4-token probe well inside this (rigby's slowest measured generation,
                  # Voltron 7B ~0.5 tok/s, is ~8 s for 4 tokens), so only a server that produces nothing fails it
LIVE_EVERY_S = 5.0
THREADS = int(os.environ.get("TOLKIEN_LLM_THREADS", "4"))
# warm: each healthy tablet holds the TABLET model in its own resident llama-server (started, watched and killed by Tolkien) and answers turns in parallel
ARMS = ("rigby", "voltron", "warm")
KINDS = ("llm", "voltron")
_LEGACY_ARMS = {"local": "rigby", "pooled": "voltron"}
SHAPE = {"rigby": "rigby", "voltron": "S5-voltron", "warm": "S1-warm"}
TABLET_DIR = "llm"                                   # ~/llm on the tablet, where the payloads keep llama.cpp
WARM_PORT = int(os.environ.get("TOLKIEN_WARM_PORT", "8093"))
WARM_LOAD_S = 600       # rigby's GT 730 serves as a tablet-class warm node, running the full model locally via Vulkan
WARM_730 = "rigby-730"
WARM_730_PORT = int(os.environ.get("TOLKIEN_WARM_730_PORT", "8095"))
WARM_730_THREADS = 2    # the model is all on the card; rigby's 4 cores belong to the A3B beside it
GT730_LOCK = threading.Lock()   # the GT 730 is one card: a warm node on it, or a rigby order placed on it, never both
GT730_HOLDER = [""]


def warm_wants_730(spec):
    """A warm order serves on the GT 730 too when it says warm_gt730: true. Whether it should is placement policy
    (#34's priced menu -> Spock's table), never a factory default. A busy 730 or a model missing from rigby's models
    mount skips the node, never the order."""
    return spec.get("warm_gt730") is True


def normalize(spec):
    """A copy in today's vocabulary: kind "llm", arms rigby/voltron (legacy voltron/local/pooled accepted)."""
    s = dict(spec)
    s["kind"] = "llm"
    # an order that names no arms gets rigby + voltron, as before warm existed: warm is only ever asked for by name
    s["arms"] = [_LEGACY_ARMS.get(a, a) for a in spec.get("arms", ["rigby", "voltron"])]
    return s


def uses_tablets(spec):
    return any(a in ("voltron", "warm") for a in normalize(spec)["arms"]) or bool(spec.get("draft_host"))


def warm_only(spec):
    """A warm order runs on the tablets alone: rigby's lane is not claimed (its own work carries on beside it)."""
    return normalize(spec)["arms"] == ["warm"]


def lane_of_spec(spec):
    """"rigby" for an order that needs rigby alone (the rigby lane); "fleet" for everything else."""
    if (spec.get("kind") or "").strip().lower() == "bench":
        return "fleet" if bench_tablets(spec) else "rigby"
    if (spec.get("kind") or "").strip().lower() in (EMBED_KIND, WINDOW_KIND, "page.render"):
        return "rigby"
    if (spec.get("kind") or "").strip().lower() in KINDS and not uses_tablets(spec):
        return "rigby"
    return "fleet"


# Embedding orders use nomic-embed-text (same GGUF weights as HAI) to ensure output compatibility.
# The `out.json` delivery format is fixed to match the reader in `menu_policy.embed_deliveries`.
# Hosts must be "rigby"; "fleet" pricing does not apply to embeds (J stays None).
EMBED_KIND = "embed"
EMBED_DEFAULT_CTX = 2048


def embed_defects(spec):
    d = []
    for k in ("name", "model", "texts", "deliver_to"):
        if not spec.get(k):
            d.append("missing required field: %s" % k)
    m = spec.get("model")
    if m is not None and (not isinstance(m, str) or not m.endswith(".gguf") or "/" in m or "\\" in m):
        d.append("model must be a .gguf file name in the models mount (no path)")
    if (spec.get("where") or "rigby") != "rigby":
        d.append("embed runs on rigby for now (a tablet's embed row is its own measurement, not yet built)")
    texts = spec.get("texts")
    if texts is not None:
        ok = (isinstance(texts, list) and texts
              and all(isinstance(t, dict) and isinstance(t.get("id"), str) and t.get("id")
                      and isinstance(t.get("text"), str) and t.get("text") for t in texts))
        if not ok:
            d.append("texts must be a non-empty list of {id, text}")
        elif len({t["id"] for t in texts}) != len(texts):
            d.append("texts ids must be unique")
    r = spec.get("rounds", 3)
    if isinstance(r, bool) or not isinstance(r, int) or r < 1:
        d.append("rounds must be a positive integer")
    ml = spec.get("min_loop_s", 0)
    if isinstance(ml, bool) or not isinstance(ml, (int, float)) or ml < 0:
        d.append("min_loop_s must be a non-negative number")
    gpu, ft = spec.get("gpu"), spec.get("fit_target")
    if gpu is not None and gpu not in GPU_NAMES:
        d.append("gpu must be one of %s" % (sorted(GPU_NAMES),))
    if gpu is not None and ft is None:
        d.append("gpu names the card for a placement: it needs fit_target (llama.cpp's own fitter)")
    if ft is not None and (isinstance(ft, bool) or not isinstance(ft, int) or ft < 0):
        d.append("fit_target must be a non-negative integer (MiB the card keeps free)")
    if ft is not None and gpu is None:
        d.append("fit_target needs gpu (which card)")
    if spec.get("build") not in BUILDS:
        d.append("build must be next, cuda, hotstore or absent (the live build)")
    if spec.get("build") in CUDA_DIRS and gpu != "gtx1060":
        d.append("build %s is the GTX 1060's (sm_61 only): it needs gpu gtx1060" % spec["build"])
    ctx = spec.get("ctx", EMBED_DEFAULT_CTX)
    if isinstance(ctx, bool) or not isinstance(ctx, int) or ctx < 64:
        d.append("ctx must be an integer >= 64")
    return d


def _embed_call(text, timeout=120):
    body = json.dumps({"input": text}).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d/v1/embeddings" % SERVER_PORT, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def run_embed(spec, log, progress, stop_event=None, tick=None):
    """-> (out, error). `out` is the GPU payload's out.json shape for THIS worker: host "rigby", model, placement {cold,
    load_s = the per-order server's start to /health, gpu, fit_target, build}, dims, one row per call {id, round,
    wall_s, prompt_eval_count}, the vectors (the menu's cross-box agreement check), summary {calls, rounds, loop_s,
    loop_j None, s_per_call_median, meter None}. The server is stopped on every route; a stop slip ends the loop and
    reports what was measured. error = None, or why (the partial `out` still describes what ran)."""
    model_path = os.path.join(MODELS, spec["model"])
    ctx = int(spec.get("ctx", EMBED_DEFAULT_CTX))
    rounds, min_loop = int(spec.get("rounds", 3)), float(spec.get("min_loop_s", 0))
    texts = list(spec["texts"])
    marked = rounds * len(texts)
    out = {"host": "rigby", "model": spec["model"], "served": "http://127.0.0.1:%d" % SERVER_PORT,
           "placement": {"cold": True, "load_s": None, "gpu": spec.get("gpu"), "fit_target": spec.get("fit_target"),
                         "build": spec.get("build"), "size_vram": None, "size": None},
           "dims": None, "rows": [], "vectors": {}, "summary": {},
           "probes": [{"id": t["id"], "text": t["text"]} for t in texts]}
    proc, error = None, None
    try:
        proc, out["placement"]["load_s"] = start_server(
            model_path, ctx, None, os.path.join(tf.STATE_DIR, "llm_embed.log"), stop_event, tick=tick,
            gpu=spec.get("gpu") or "gt730", fit_target=spec.get("fit_target"), build=spec.get("build"),
            threads=spec.get("threads"), extra=["--embeddings"])
        log("embed: %s loaded in %ss %s" % (spec["model"], out["placement"]["load_s"],
                                             ("on %s" % spec["gpu"]) if spec.get("gpu") else "on rigby's CPU"))
        t_loop, rnd, calls = time.time(), 0, 0
        while rnd < rounds or (time.time() - t_loop) < min_loop:
            rnd += 1
            for t in texts:
                if stop_event is not None and stop_event.is_set():
                    out["summary"]["stopped"] = True
                    raise RuntimeError("stopped by slip after %d calls" % calls)
                c0 = time.time()
                r = _embed_call(t["text"])
                wall = time.time() - c0
                vec = ((r.get("data") or [{}])[0] or {}).get("embedding") or []
                if not vec:
                    out["rows"].append({"id": t["id"], "round": rnd, "error": "no embedding in the reply"})
                    continue
                out["rows"].append({"id": t["id"], "round": rnd, "wall_s": round(wall, 4),
                                    "prompt_eval_count": (r.get("usage") or {}).get("prompt_tokens")})
                out["vectors"].setdefault(t["id"], vec)
                out["dims"] = out["dims"] or len(vec)
                calls += 1
                if calls <= marked:
                    progress(calls, marked, t["id"])
            if tick is not None:
                tick("round %d: %d calls, loop %.0f s" % (rnd, calls, time.time() - t_loop))
        loop_s = time.time() - t_loop
        walls = sorted(r["wall_s"] for r in out["rows"] if "wall_s" in r)
        errs = sum(1 for r in out["rows"] if "error" in r)
        out["summary"].update({
            "calls": calls, "rounds": rnd, "loop_s": round(loop_s, 2), "loop_j": None, "j_per_call": None,
            "s_per_call_median": round(walls[len(walls) // 2], 4) if walls else None,
            "s_per_call_mean": round(sum(walls) / len(walls), 4) if walls else None,
            "meter": None, "errors": errs,
            "verdict": "%d calls, median %.1f ms (rigby: J unmetered per order - the menu prices it from the plug's "
                       "marginal watts)" % (calls, 1000 * walls[len(walls) // 2] if walls else float("nan"))})
        if not calls:
            error = "no embedding call succeeded"
        elif errs:
            error = "%d of %d calls returned no embedding" % (errs, calls + errs)
    except Exception as e:                                                    # noqa: BLE001 - reported, never dropped
        error = "%s: %s" % (type(e).__name__, str(e)[:300])
        out["summary"].setdefault("calls", len([r for r in out["rows"] if "wall_s" in r]))
    finally:
        stop_server(proc)
    return out, error


# rigby keeps the chat model warm between HAI turns. Like GPU factories, one llama-server instance serves the entire window order (started per route, stopped on exit) rather than spawning a process per request. Clients consume the stream via the OpenAI wire format (chunked data: lines).
WINDOW_KIND = "chat.window"
WINDOW_POLL_S = 0.2        # how often rigby's window lists its items folder on the jobs share (helper_window.serve poll_s)


def window_defects(spec):
    spec = launch_spec(spec)           # judged as it will launch (the model's production profile under the order)
    d = []
    for k in ("name", "model", "items_dir"):
        if not spec.get(k):
            d.append("missing required field: %s" % k)
    m = spec.get("model")
    if m is not None and (not isinstance(m, str) or not m.endswith(".gguf") or "/" in m or "\\" in m):
        d.append("model must be a .gguf file name in the models mount (no path)")
    it = spec.get("items_dir")
    if it is not None and (not isinstance(it, str) or not it.startswith("/")):
        d.append("items_dir must be an absolute path Tolkien sees (the jobs share mount)")
    for k in ("ctx", "idle_ttl"):
        v = spec.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            d.append("%s must be a positive integer" % k)
    gpu, ft = spec.get("gpu"), spec.get("fit_target")
    if gpu is not None and gpu not in GPU_NAMES:
        d.append("gpu must be one of %s" % (sorted(GPU_NAMES),))
    if ft is not None and (isinstance(ft, bool) or not isinstance(ft, int) or ft < 0):
        d.append("fit_target must be a non-negative integer (MiB the card keeps free)")
    if spec.get("build") not in BUILDS:
        d.append("build must be next, cuda, hotstore or absent (the live build)")
    # window launch knobs are validated via llm_defects because they use the same server launch path as LLM orders
    knobs = {k: spec[k] for k in WINDOW_LAUNCH_KNOBS if k in spec}   # an explicit null crosses: it turns one off
    if knobs:
        placed = {k: spec[k] for k in ("gpu", "fit_target", "build", "reasoning") if spec.get(k) is not None}
        d += llm_defects(dict(knobs, **placed, arms=["rigby"], name=spec.get("name") or "window",
                              model=spec.get("model") or "window.gguf", payload="window", tasks=["window"]))
    return d


# the launch knobs a chat window carries from its weighed menu row (rigby_server_kwargs' keys; no draft model - a
# window serves one model)
WINDOW_LAUNCH_KNOBS = ("spec_type", "draft_max", "gpu_layers", "override_tensor", "op_offload", "parallel", "ubatch",
                       "kepler_flags", "threads", "load_mode", "op_offload_min_batch", "engine_env", "expert_hot",
                       "moe_cache_mib")


def window_vision_args(model):
    """llama-server's arguments for a window's model to see, or None. The projector on the CPU: the 1060 is full by design (the fitter keeps 1,024 MiB spare
    and 900 MiB of projector would cost expert layers on every written token, to serve an occasional picture). RAM has room."""
    import rigby_profiles
    f = rigby_profiles.VISION.get(model)
    path = os.path.join(MODELS, f) if f else None
    return ["-mm", path, "--no-mmproj-offload"] if path and os.path.exists(path) else None


# Do not re-implement window logic here; this module delegates to helper_window.serve (shared via the GPU foundry build) to prevent drift. Only rigby-specific launch and state retention logic belongs in this file.
WINDOW_SLOTS_KEPT = 50     # RIGBY_ARRAY is the persistent storage path for older chat states. It must point to a mounted volume (e.g. /bigshared) rather than ephemeral container storage, as older states are moved here and must not be deleted.
RIGBY_ARRAY = os.environ.get("TOLKIEN_ARRAY_SLOTS", "/bigshared/HAI/chat-slots")


def _chat_state_keeping(sd, stem):
    """-> (retain_fn, fetch_fn, which rule). The array reachable: the rule every box keeps (helper_window.retain, five per
    user, older ones moved to rigby's folder on the array, brought back when that beats a re-read). Not reachable: rigby's
    newest WINDOW_SLOTS_KEPT, because the fleet's rule never deletes and on rigby there is nowhere to move them."""
    box = os.path.join(RIGBY_ARRAY, "rigby")
    if os.path.isdir(RIGBY_ARRAY):
        return (lambda user: HW.retain(user, slot_dir=sd, array_dir=box),
                lambda name, rate: HW.fetch_from_array(name, rate, slot_dir=sd, array_dir=box), "five per user + the array")
    return (lambda user: HW.retain_newest(sd, stem, WINDOW_SLOTS_KEPT), lambda name, rate: "absent",
            "the newest %d here: the array (%s) is not mounted" % (WINDOW_SLOTS_KEPT, RIGBY_ARRAY))


def read_grid(ubatch):
    """(n_batch, n_ubatch) of a server start_server launches: `-ub U`, and `-b U` only above llama-server's default 2048."""
    ub = int(ubatch) if ubatch else 512
    return (ub if ub > 2048 else 2048), ub


def _yield_on_stop(stop_event, items_dir, done):
    """A stop slip reaches the window as the GPU factories' YIELD (p20_foundry.yield_live_window writes the same file): a
    turn its caller marked cut_ok is parked mid-answer and saved, any other finishes, then the window ends."""
    while not done.is_set():
        if stop_event.wait(0.2):
            try:
                open(os.path.join(items_dir, HW.YIELD), "w").close()
            except OSError:
                pass
            return


def run_window(spec, log, stop_event=None, tick=None, clock=time.time, sleep=time.sleep, on_turn=None):
    """-> (summary, error). One llama-server for the window, Kirk's items answered by helper_window.serve until the window
    ends; the server is stopped on every route. error = None, or why it could not serve (the summary says what it served)."""
    items_dir = spec["items_dir"]
    summary = {"served": 0, "load_s": None, "why_ended": None}
    proc, error, conv = None, None, None
    a = types.SimpleNamespace(model=spec["model"], num_ctx=int(spec.get("ctx") or 4096), llama=True, strata=None,
                              host="http://127.0.0.1:%d" % SERVER_PORT, live=True, think=False, seed_base=0,
                              temperature=float(spec.get("temperature", 0.6)), idle_ttl=spec.get("idle_ttl"),
                              read_grid=None)
    work = os.path.join(tf.STATE_DIR, "windows", spec["name"])      # the window's resume rows and progress marks
    done = threading.Event()
    try:
        # nobody to answer (it already closed, or its owner is gone): end BEFORE the engine loads, the GPU boxes' rule
        gone = "it already closed" if os.path.exists(os.path.join(items_dir, HW.CLOSE)) \
            else HW.owner_verdict(items_dir, clock, live=True)
        if gone:
            summary["why_ended"] = "not started: %s" % gone
            log("window %s: not started (%s)" % (spec["name"], gone))
        else:
            # the production launch, as an llm order's rigby arm (one derivation): the model's profile under the window
            kw = rigby_server_kwargs(launch_spec(spec))
            a.read_grid = read_grid(kw.get("ubatch"))
            sd = slots_dir()
            os.makedirs(sd, exist_ok=True)
            kw["slot_save_path"] = sd
            kw["extra"] = window_vision_args(spec.get("model"))
            proc, summary["load_s"] = start_server(
                os.path.join(MODELS, spec["model"]), a.num_ctx, None, os.path.join(tf.STATE_DIR, "llm_window.log"),
                stop_event, tick=tick, **kw)
            summary["launch"] = {k: v for k, v in kw.items() if v not in (None, False)}
            log("window %s: %s loaded in %ss" % (spec["name"], spec["model"], summary["load_s"]))
            stem = "conv-%s-%d-" % (re.sub(r"[^A-Za-z0-9.]+", "-", a.model), a.num_ctx)
            keep, fetch, rule = _chat_state_keeping(sd, stem)
            summary["chat_states"] = rule
            log("window %s: chat states kept by %s" % (spec["name"], rule))
            conv = HW.ConvSlots(a, slot_dir=sd, hot=HW.HotStarts(a, slot_dir=sd), retain_fn=keep, fetch=fetch)
            if stop_event is not None:
                threading.Thread(target=_yield_on_stop, args=(stop_event, items_dir, done), daemon=True).start()
            os.makedirs(work, exist_ok=True)
            served = [0]

            def beat(row):
                served[0] += 1
                if tick is not None:
                    tick("window turn %d" % served[0])
                if on_turn is not None:
                    try:
                        on_turn(served[0])
                    except Exception:                   # a claim write must never cost a served turn
                        pass
            rows, why = HW.serve(a, items_dir, os.path.join(work, "progress"), os.path.join(work, "rows.partial.jsonl"),
                                 types.SimpleNamespace(source=None), sleep=sleep, clock=clock, chat=HW.own_engine_chat(a),
                                 card_waiting=lambda: False, idle_ttl=a.idle_ttl, conv_slots=conv, poll_s=WINDOW_POLL_S,
                                 on_row=beat)
            summary.update(served=len(rows), why_ended=why)
    except SystemExit as e:
        # helper_window's hung-engine verdict: an answer went silent and a 4-token probe got nothing (rc 2 on a GPU box)
        error = "the engine hung: a silent answer, and a 4-token probe got nothing (rc %s)" % e.code
    except Exception as e:                                                    # noqa: BLE001 - reported, never dropped
        error = "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        done.set()
        if proc is not None and conv is not None and conv.current:
            conv.save_current("window closed: %s" % summary.get("why_ended"))     # never raises
        stop_server(proc)
        try:
            open(os.path.join(items_dir, HW.CLOSE), "w").close()     # Kirk reads an ended window from it
        except OSError:
            pass
        shutil.rmtree(work, ignore_errors=True)
    return summary, error


def llm_defects(spec):
    if spec.get("placement") and not spec.get("arms"):
        # never let normalize()'s legacy default (rigby + Voltron) run an order whose worker was never chosen
        return ["a placed order (`placement`) names no worker: Tolkien resolves it when collecting it from the desk "
                "(resolve_placed) - send placed orders through Kirk's desk, or name arms + model"]
    spec = normalize(spec)
    if spec["arms"] == ["rigby"]:
        spec = launch_spec(spec)       # judged as it will launch: the model's production profile under the order
    d = []
    if spec.get("warm_nodes") is not None and (warm_nodes(spec) is None or spec["arms"] != ["warm"]):
        d.append("warm_nodes is a positive int on a warm order (a placed piece: one answer, one node)")
    dp = spec.get("dump_probe")
    if dp is not None:          # #34 B11's measurement: the server itself is the subject - no payload, no tasks
        ctx = spec.get("ctx", 2048)
        d += dump_probe_defects(dp, ctx if isinstance(ctx, int) and not isinstance(ctx, bool) else 0)
        if spec["arms"] != ["rigby"]:
            d.append("dump_probe runs on rigby alone (arms [\"rigby\"]): it prices rigby's own server restart")
        if spec.get("draft_model") or spec.get("draft_host"):
            d.append("dump_probe prices the production launch without a draft model")
    for k in ("name", "model") + (() if dp is not None else ("payload",)):
        if not spec.get(k):
            d.append("missing required field: %s" % k)
    tasks = spec.get("tasks")
    if dp is None and (not isinstance(tasks, list) or not tasks or not all(isinstance(t, str) and t for t in tasks)):
        d.append("llm needs tasks: a non-empty list of task ids")
    arms = spec["arms"]
    if not arms or any(a not in ARMS for a in arms):
        d.append("llm arms must be a non-empty subset of %s" % (ARMS,))
    r = spec.get("replicates", 1)
    if not isinstance(r, int) or r < 1:
        d.append("replicates must be a positive integer")
    dm = spec.get("draft_model")
    if dm is not None and (not isinstance(dm, str) or not dm or "/" in dm or "\\" in dm):
        d.append("draft_model must be a model file name in the models mount (no path)")
    if spec.get("reasoning") is not None and spec["reasoning"] not in ("on", "off", "auto"):
        d.append("reasoning must be on, off or auto")
    st = spec.get("spec_type")
    if st is not None and st not in SPEC_TYPES:
        d.append("spec_type must be one of %s" % (SPEC_TYPES,))
    if st in ("draft-simple",) and not dm:
        d.append("spec_type draft-simple needs a draft_model")
    gl, ot = spec.get("gpu_layers"), spec.get("override_tensor")
    if gl is not None and (isinstance(gl, bool) or not isinstance(gl, int) or gl < 0):
        d.append("gpu_layers must be a non-negative integer (llama-server -ngl on rigby's GT 730)")
    if ot is not None and (not isinstance(ot, list) or not ot
                           or not all(isinstance(x, str) and x.count("=") == 1 and all(x.split("=")) for x in ot)):
        d.append("override_tensor must be a non-empty list of '<tensor regex>=<buffer>' strings (llama-server -ot)")
    if ot is not None and gl is None:
        d.append("override_tensor needs gpu_layers (a placement without the GPU build places nothing)")
    oo = spec.get("op_offload")
    if oo is not None and (not isinstance(oo, bool) or gl is None):
        d.append("op_offload must be true or false and needs gpu_layers (llama-server --op-offload / --no-op-offload)")
    if (gl is not None or ot is not None) and "voltron" in arms:
        d.append("gpu_layers / override_tensor are a rigby-arm placement; voltron offloads to the tablets")
    gpu, ft = spec.get("gpu"), spec.get("fit_target")
    gpus = gpu_list(gpu)
    if gpu is not None and (not gpus or any(g not in GPU_NAMES for g in gpus) or len(set(gpus)) != len(gpus)):
        d.append("gpu must be one of %s, or a list of distinct ones (the fitter spreads over them)" % (sorted(GPU_NAMES),))
    if gpu is not None and gl is None and ft is None:
        d.append("gpu names the card for a placement: it needs gpu_layers or fit_target")
    if isinstance(gpu, list) and gl is not None:
        d.append("a list of cards is the fitter's (fit_target); gpu_layers places on one card")
    if spec.get("kepler_flags") is not None and (not isinstance(spec["kepler_flags"], bool) or gpu is None):
        d.append("kepler_flags must be true or false and needs gpu (the GT 730's shader flags on a placement without it)")
    if ft is not None:
        fts = ft if isinstance(ft, list) else [ft]
        if not fts or any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in fts):
            d.append("fit_target must be a non-negative integer (MiB each card keeps free - llama.cpp's own fitter), "
                     "or one per card in gpu")
        elif isinstance(ft, list) and len(ft) != len(gpus or ["gt730"]):
            d.append("fit_target as a list needs one margin per card in gpu, in the same order")
        if gl is not None or ot is not None:
            d.append("fit_target hands placement to llama.cpp's fitter; gpu_layers / override_tensor place by hand - not both")
        if "voltron" in arms:
            d.append("fit_target is a rigby-arm placement; voltron offloads to the tablets")
    if spec.get("build") not in BUILDS:
        d.append("build must be next (the async-RPC build), cuda / hotstore (the GTX 1060's CUDA builds) or absent "
                 "(the live build)")
    if spec.get("build") in CUDA_DIRS and (gpu != "gtx1060" or (gl is None and ft is None)):
        d.append("build %s is the GTX 1060's (sm_61 only): it needs gpu gtx1060 and a placement" % spec["build"])
    eh = spec.get("expert_hot")
    if eh is not None:
        if spec.get("build") != "hotstore" or arms != ["rigby"]:
            d.append("expert_hot is the hotstore build's (rigby arm only)")
        if (not isinstance(eh, dict) or set(eh) - {"slots", "dwell", "hyst"} or eh.get("slots") == 0
                or not _int_in(eh.get("slots"), -1, 512)
                or ("dwell" in eh and not _int_in(eh["dwell"], 0, 1000000))
                or ("hyst" in eh and (isinstance(eh["hyst"], bool) or not isinstance(eh["hyst"], (int, float))
                                      or not 0 <= eh["hyst"] <= 100))):
            d.append("expert_hot must be {slots: -1 (fit to free VRAM) or 1..512, dwell?: syncs 0..1000000, "
                     "hyst?: ratio 0..100}")
    mc = spec.get("moe_cache_mib")
    if mc is not None:
        # llama.cpp's merged expert cache (PR #29887): hot experts copied to the card in an LRU of this many MiB, misses
        # read from the CPU copy. In the `cuda` and `next` builds (the flag is in their binaries; the hotstore build has
        # its own, older store - expert_hot); it needs a card, and it lives in the fitter's free margin
        if spec.get("build") not in ("cuda", "next") or arms != ["rigby"] or (ft is None and gl is None):
            d.append("moe_cache_mib is the cuda / next build's expert cache: rigby arm only, with a card placement")
        if not _int_in(mc, 1, 16384):
            d.append("moe_cache_mib must be an integer 1..16384 (MiB of VRAM for the expert cache)")
    par = spec.get("parallel")
    if par is not None and (isinstance(par, bool) or not isinstance(par, int) or par < 1 or "voltron" in arms):
        d.append("parallel must be a positive integer (llama-server -np), a rigby-arm setting")
    ub = spec.get("ubatch")
    if ub is not None and (isinstance(ub, bool) or not isinstance(ub, int) or ub < 1 or "voltron" in arms):
        d.append("ubatch must be a positive integer (llama-server -ub), a rigby-arm setting")
    for knob, lo, hi in (("threads", 1, 64), ("op_offload_min_batch", 1, 1 << 20)):
        v = spec.get(knob)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi or "voltron" in arms):
            d.append("%s must be an integer %d..%d, a rigby-arm setting" % (knob, lo, hi))
    if spec.get("load_mode") is not None and (spec["load_mode"] not in LOAD_MODES or "voltron" in arms):
        d.append("load_mode must be one of %s (llama-server --load-mode), a rigby-arm setting" % (LOAD_MODES,))
    ee = spec.get("engine_env")
    if ee is not None:
        if (not isinstance(ee, dict) or not ee or set(ee) - set(ENGINE_ENV) - set(ENGINE_ENV_PATHS)
                or not all(_int_in(v, 0, 1000000) for k, v in ee.items() if k in ENGINE_ENV)
                or not all(engine_env_path_ok(v) for k, v in ee.items() if k in ENGINE_ENV_PATHS)):
            d.append("engine_env must map %s to integers and %s to a file under %s (no ..)"
                     % (sorted(ENGINE_ENV), sorted(ENGINE_ENV_PATHS), ENGINE_ENV_PATH_ROOT))
        if "voltron" in arms:
            d.append("engine_env is a rigby-arm setting")
    dmx = spec.get("draft_max")
    if dmx is not None:
        if isinstance(dmx, bool) or not isinstance(dmx, int) or not 1 <= dmx <= 64:
            d.append("draft_max must be an integer 1..64 (tokens drafted per check)")
        if not (st or dm):
            d.append("draft_max needs speculation: spec_type or draft_model")
    dh = spec.get("draft_host")
    if dh is not None:
        if dh not in tf.WIRE_OCTET:
            d.append("draft_host must be a wire tablet (%s)" % ", ".join(sorted(tf.WIRE_OCTET)))
        if not dm:
            d.append("draft_host needs a draft_model (the model that runs on that tablet)")
        if arms != ["rigby"]:
            d.append("draft_host is a rigby-arm placement: arms must be [\"rigby\"]")
        if st not in (None, "draft-simple"):
            d.append("draft_host drafts with its draft_model: spec_type must be draft-simple or unset")
        if gl is not None or ot is not None:
            d.append("draft_host runs on the CPU build; gpu_layers / override_tensor do not combine with it")
    if "warm" in arms:
        if len(arms) != 1:
            d.append("warm runs alone: its model is a TABLET model (~/llm/<model>.gguf), not one of rigby's")
        m = str(spec.get("model") or "")
        if m.endswith(".gguf") or "/" in m or "\\" in m:
            d.append("warm model is the tablet model name without .gguf (as the tablet payloads name it)")
        if gl is not None or ot is not None or dm or st:
            d.append("warm takes no rigby placement or speculation knobs")
    wg = spec.get("warm_gt730")
    if wg is not None and (not isinstance(wg, bool) or arms != ["warm"]):
        d.append("warm_gt730 is true/false on a warm order only (default true: rigby's GT 730 serves as one more warm "
                 "node; false leaves it out)")
    tc = spec.get("turn_cap")
    if tc is not None and (isinstance(tc, bool) or not isinstance(tc, int) or tc < 1
                           or (isinstance(spec.get("ctx"), int) and tc >= spec["ctx"])):
        d.append("turn_cap is a positive int below ctx (the A1 runaway guard's per-call cap; compaction keeps one "
                 "reply's room)")
    pc = spec.get("price_ceiling_s")
    if pc is not None and (isinstance(pc, bool) or not isinstance(pc, (int, float)) or pc <= 0):
        d.append("price_ceiling_s must be a positive number of seconds (a measured price, stated per order)")
    ab = spec.get("at_cap_brake")
    if ab is not None and (isinstance(ab, bool) or not isinstance(ab, (int, float)) or not 0 < ab <= 1):
        d.append("at_cap_brake must be a share in (0, 1] (the family's stop-rule share, stated per order)")
    items = spec.get("items")
    if items is not None:
        ok = (isinstance(items, list) and items and isinstance(tasks, list)
              and all(isinstance(i, list) and len(i) == 2 and i[0] in tasks and isinstance(i[1], int)
                      and 1 <= i[1] <= (r if isinstance(r, int) else 0) for i in items))
        if not ok:
            d.append("items must be a non-empty list of [task, replicate] pairs drawn from tasks x replicates")
    return d


def is_tool(mod):
    """A tool payload (map_tool_swarm_hard): one JSON tool call per request, graded by score_tool, not code."""
    return hasattr(mod, "score_tool")


def is_agentic(mod):
    """An AGENTIC payload (the A1 family, `a1_payload.py`): one item is a whole tool-using run on an exercise, through
    the lab's own harness and Inspect, graded by the exercise's hidden tests. `run_agentic` replaces complete()."""
    return hasattr(mod, "run_agentic")


def control_of(mod):
    """(name, task) of the payload's known-positive control."""
    name = "control_add" if "control_add" in mod.CONTROL_TASKS else sorted(mod.CONTROL_TASKS)[0]
    return name, mod.CONTROL_TASKS[name]


def ship_inspect_log(path, order_name):
    """An agentic item's Inspect log is a deliverable (the transcript of every model call): copied to
    OUTBOX/inspect/<order>/ as it lands. Fail-soft like ship_to_outbox - the results dir keeps the authoritative copy."""
    try:
        dst = os.path.join(tf.OUTBOX, "inspect", order_name)
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(path, os.path.join(dst, os.path.basename(path)))
        return True
    except Exception:                                                        # noqa: BLE001
        return False


def prompt_of(mod, tdef):
    return mod.PREAMBLE.format(req=tdef["req"]) if is_tool(mod) else tdef["prompt"] + mod.SUFFIX


def n_checks(tdef):
    return len(tdef["checks"]) if "checks" in tdef else (1 if tdef.get("gold_args") is None else 2)


def load_scorer(path):
    """The tablets' payload module, proven on a known-good answer before any model time is spent."""
    spec = importlib.util.spec_from_file_location("llm_payload", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if is_agentic(mod):
        why = mod.self_test()                    # reference solution passes the hidden tests, the stub fails them
        if why:
            raise RuntimeError("scorer broken: %s" % why)
        return mod, None
    if is_tool(mod):
        _, ctl = control_of(mod)
        good = json.dumps({"tool": ctl["gold_tool"], "arguments": ctl["gold_args"] or {}})
        passed, total, err = mod.score_tool(good, ctl["gold_tool"], ctl["gold_args"])
        if total == 0 or passed != total:
            raise RuntimeError("scorer broken: failed the control's own gold call (%r)" % (err,))
        return mod, None
    work = tempfile.mkdtemp(prefix="llm_grade_")
    passed, total, err = mod.score("def _probe():\n    return 41 + 1\n", ["lambda: _probe() == 42"], work)
    if (passed, total) != (1, 1):
        raise RuntimeError("scorer broken: failed a known-good program (%r)" % (err,))
    return mod, work


def grade(mod, work, task_def, answer):
    if is_tool(mod):
        passed, total, err = mod.score_tool(answer or "", task_def["gold_tool"], task_def["gold_args"])
        return {"ok": total > 0 and passed == total, "checks_passed": passed, "checks_total": total, "error": err}
    code = grade_jobs.answer_code(mod, answer or "")     # ONE grader with the GPU factories and the menu
    if not code.strip():
        return {"ok": False, "checks_passed": 0, "checks_total": len(task_def["checks"]), "error": "no_code_extracted"}
    passed, total, err = mod.score(code, task_def["checks"], work)
    return {"ok": total > 0 and passed == total, "checks_passed": passed, "checks_total": total, "error": err,
            "code": code}


# --- llama-server ---------------------------------------------------------------------------------------------

SPEC_TYPES = ("draft-simple", "draft-mtp", "ngram-simple", "ngram-map-k", "ngram-mod", "ngram-cache")
LOAD_MODES = ("auto", "none", "mlock", "mmap+mlock")
# engine instrument switches an order may set, nothing else: GGML_SCHED_PROFILE=N (the lab's scheduler profile, a
# summary to the server log every N graphs; lab-engine\patches\sched_profile_23b0202.py), Vulkan's per-op GPU
# timer, and Vulkan's op fusion off (a control)
# LAB_CUDA_GRAPHS_PRE_VOLTA: the lab patch (lab-engine\patches\cuda_graphs_pre_volta_patch.py) that lets the
# GTX 1060 (Pascal) use CUDA graphs, which llama.cpp disables below Volta
# LLAMA_EXPERT_HITRATE / LLAMA_EXPERT_DEBUG (hotstore build): the hot-slot hit rate per decoded token / each re-sync's
# swap count, to the server log
ENGINE_ENV = ("GGML_SCHED_PROFILE", "GGML_VK_PERF_LOGGER", "GGML_VK_DISABLE_FUSION", "GGML_CUDA_DISABLE_GRAPHS",
              "LAB_CUDA_GRAPHS_PRE_VOLTA", "LLAMA_EXPERT_HITRATE", "LLAMA_EXPERT_DEBUG", "LAB_HOTSTORE_BLOCK")
# engine switches that take a FILE (hotstore build, lab-engine\patches\hotstore_block_patch.py):
# LAB_EXPERT_HOT_RANKING = a per-layer expert ranking (lab-engine\research\split-inference\expert_ranking.py) that fills the hot slots once, frozen.
# Only files on the payloads share, by absolute path
ENGINE_ENV_PATHS = ("LAB_EXPERT_HOT_RANKING",)
ENGINE_ENV_PATH_ROOT = JOBS_ROOT + "/payloads/"


def engine_env_path_ok(v):
    return (isinstance(v, str) and v.startswith(ENGINE_ENV_PATH_ROOT) and ".." not in v
            and re.fullmatch(r"[A-Za-z0-9._/-]+", v) is not None)
ENGINE_LOG_LINES = 200


def log_tail_lines(path, n):
    """The last n non-empty lines of a log, raw (instrument output is a table - a keyword filter would drop its rows).
    Never raises: it runs in the order's cleanup."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [l.rstrip() for l in f.read().splitlines() if l.strip()][-n:]
    except Exception as e:                                                   # noqa: BLE001
        return ["(could not read %s: %s: %s)" % (path, type(e).__name__, e)]
# The draft length flag (size-m or n-max) caps the speculation round. Keep these values >= the lookup size (12) to avoid drafting nothing (fails silently if below), and low enough to avoid crossing llama.cpp's op-offload threshold (32) which triggers expensive CPU expert streaming on every check.
DRAFT_MAX_FLAG = {"ngram-simple": ("--spec-ngram-simple-size-m", "--spec-ngram-simple-size-n", 12),
                  "ngram-map-k": ("--spec-ngram-map-k-size-m", "--spec-ngram-map-k-size-n", 12),
                  "ngram-mod": ("--spec-ngram-mod-n-max", "--spec-ngram-mod-n-min", 48)}


def draft_max_args(spec_type, n):
    """The flags that cap one speculation round at n drafted tokens for this spec type."""
    if spec_type in DRAFT_MAX_FLAG:
        length, floor, floor_default = DRAFT_MAX_FLAG[spec_type]
        return [length, str(n), floor, str(min(n, floor_default))]
    return ["--spec-draft-n-max", str(n)]


def rigby_capabilities():
    """What rigby's own lane can build, by DETECTION (declared-but-absent is the failure mode refused): `rigby.llm` =
    the CPU llama-server and the models mount are there; `rigby.gt730` = the Vulkan build, the host loader it runs
    under (HOSTLIB) and the card's device node are there too. The desk places a piece by these (A1-team, a lead on a
    GPU box delegating to rigby - 34-A1 spec decision 3a)."""
    caps = []
    if os.path.exists(os.path.join(LLAMA_BIN, "llama-server")) and os.path.isdir(MODELS):
        caps.append("rigby.llm")
        if (os.path.exists(os.path.join(LLAMA_VULKAN, "llama-server"))
                and os.path.exists(os.path.join(HOSTLIB, "ld-linux-x86-64.so.2")) and os.path.exists("/dev/nvidia0")):
            caps.append("rigby.gt730")
    import rigby_profiles
    caps += ["sees.%s" % m for m, f in rigby_profiles.VISION.items()      # declared only when both files are really there: the same fact the window launches from
             if os.path.exists(os.path.join(MODELS, m)) and os.path.exists(os.path.join(MODELS, f))]
    import tolkien_page
    caps += tolkien_page.capabilities()                  # a page's browser on the 1060, once its proof order passed
    return caps


# Spock sends an `llm` order with a placement key and no worker. TOLKIEN resolves the worker using the first present row for that key in `POLICY_TABLE`.
# The `order` fields from that row (e.g. `menu_policy`) define the spec. A busy worker is a WAIT, not a skip; do not bypass to the next row.
POLICY_TABLE = os.environ.get("TOLKIEN_POLICY_TABLE",
                              os.path.join(tf.DOCS_ROOT, "Datasets", "Project34-Menu", "policy.json"))
FELLOWSHIP_BOXES = ("rigby", "fellowship")      # the table's names for rigby and the tablet pool
_POLICY = {"key": None, "data": None}


def _policy(path=None):
    path = path or POLICY_TABLE
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    if _POLICY["key"] != key:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        _POLICY.update(key=key, data=data)
    return _POLICY["data"]


def placed_capabilities(path=None):
    """`fellowship.placed` when the placement table is readable, i.e. Tolkien can resolve a placed order. Detected,
    never configured: the desk only sends a placed order to a Fellowship that declares it."""
    try:
        return ["fellowship.placed"] if isinstance(_policy(path).get("families"), dict) else []
    except Exception:                                                # noqa: BLE001 - a table that cannot be read claims nothing
        return []


def policy_rows(family, tier, lens, path=None):
    """Returns the contiguous run of Fellowship rows starting at the first one found.

    Rows ranked after a non-Fellowship row belong to that destination and must not be
    included in this result. Returns [] if the policy table, key, or rows are missing."""
    try:
        rows = _policy(path)["families"][family]["tiers"][tier][lens]
    except Exception:                                                # noqa: BLE001
        return []
    run = []
    for r in rows:
        mine = isinstance(r, dict) and r.get("box") in FELLOWSHIP_BOXES
        if mine:
            run.append(r)
        elif run:
            break
    return run


def card_usable(gpu, build=None):
    """Check if the GPU is visible to the Vulkan driver on the build's device path. This matches the launch-time lookup to prevent disagreement, as /proc entries may list devices that are no longer bus-present."""
    try:
        vulkan_device(card_dir(build), gpu)
        return True, ""
    except Exception as e:                                           # noqa: BLE001 - could not see it = not usable
        return False, "%s is not visible to the %s build (%s)" % (gpu, build or "vulkan", str(e)[:160])


def card_checks(spec, check=None):
    """{(gpu, build): None if its build can see it, else why} for every card an `llm` / `bench` order places work on
    (each card + build checked once); {} when the order uses no card."""
    check = check or card_usable
    runs = spec.get("runs") if (spec.get("kind") or "").strip().lower() == "bench" else [spec]
    out = {}
    for r in runs or []:
        if not isinstance(r, dict) or not uses_card(r):
            continue
        for g in gpu_list(r.get("gpu") or "gt730"):
            key = (g, r.get("build"))
            if key not in out:
                ok, why = check(g, r.get("build"))
                out[key] = None if ok else why
    return out


def card_problems(spec, check=None):
    """[why, ...] for the order's cards its build cannot see now; [] when none, or when the order uses no card. The
    rigby lane WAITS on a non-empty answer instead of launching into a VOID."""
    return [w for w in card_checks(spec, check).values() if w]


def worker_ready(row, caps, card_check=None):
    """(True, "") when the row's worker can build its order NOW - presence, not busyness - else (False, why)."""
    o = row.get("order")
    if not isinstance(o, dict) or not o.get("arms") or not o.get("model"):
        return False, "the row carries no order fields (regenerate policy.json)"
    if o["arms"] == ["rigby"]:
        if "rigby.llm" not in caps:
            return False, "rigby.llm is not declared"
        if not os.path.exists(os.path.join(MODELS, o["model"])):
            return False, "%s is not in rigby's models mount" % o["model"]
        if o.get("build") in CUDA_DIRS and not os.path.exists(os.path.join(CUDA_DIRS[o["build"]], "llama-server")):
            return False, "the %s build is not mounted" % o["build"]
        bad = card_problems(dict(o, kind="llm"), card_check)
        if bad:
            return False, "; ".join(bad)
        return True, ""
    if o["arms"] == ["warm"]:
        return (True, "") if "tablet_cpu" in caps else (False, "no healthy tablet")
    return False, "arms %r is not a Fellowship worker shape" % (o["arms"],)


class NoFellowshipRow(ValueError):
    """The table lists no Fellowship worker for the order's key: a property of the ORDER against the table (no wait
    can change it), so the order fails with the reason instead of waiting forever."""


def _row_info(r):
    return {k: r.get(k) for k in ("box", "model", "think", "temperature", "quality", "s_per_solve", "j_per_solve",
                                  "role", "status")}


def rebound(spec, option):
    """`spec` moved to another worker of its placement run: the chosen row's order fields replaced by `option`'s, the
    placement record naming the move. The options left behind it stay, for the next move."""
    out = {k: v for k, v in spec.items() if k not in set(spec.get("placement_fields") or ())}
    out.update(json.loads(json.dumps(option["order"])))
    opts = list(spec.get("placement_options") or [])
    rest = opts[opts.index(option) + 1:] if option in opts else []
    row = dict(option["row"], skipped=list((spec.get("placement_row") or {}).get("skipped") or []),
               moved_from={"box": (spec.get("placement_row") or {}).get("box"),
                           "model": (spec.get("placement_row") or {}).get("model"),
                           "why": "busy at dispatch - the next worker of the run had a free spot"})
    out.update(placement_row=row, placement_fields=sorted(option["order"]), placement_options=rest)
    return out


def resolve_placed(spec, caps, path=None, card_check=None):
    """(spec, None): a placed `llm` order filled from the first ready row of policy_rows' run; an order that is not
    placed, or already names its worker, comes back untouched. (None, reason) when no row of the run can build it
    now - the caller HANDS IT BACK to the desk, which re-places it on the table's next destination (a transient
    absence never consumes a piece, and a dropped card must not park one for hours). `placement_row` records the row
    chosen and every row skipped. Raises NoFellowshipRow when the table has no Fellowship row for the key at all."""
    p = spec.get("placement")
    if not isinstance(p, dict) or spec.get("arms") or spec.get("model"):
        return spec, None
    rows = policy_rows(p.get("family"), p.get("tier"), p.get("lens"), path)
    if not rows:
        raise NoFellowshipRow("the placement table has no Fellowship row for %s/%s/%s"
                              % (p.get("family"), p.get("tier"), p.get("lens")))
    skipped = []
    for i, r in enumerate(rows):
        ok, why = worker_ready(r, set(caps or ()), card_check)
        if ok:
            out = dict(spec)
            out.update(json.loads(json.dumps(r["order"])))
            out["placement_row"] = _row_info(r)
            out["placement_row"]["skipped"] = skipped
            # the other READY workers of the run, in the table's order: when this one is busy at dispatch, Tolkien
            # moves the piece to the next one with a free spot (rebind_to_free_worker) - presence here, busyness then
            out["placement_fields"] = sorted(r["order"])
            out["placement_options"] = [{"order": json.loads(json.dumps(o["order"])), "row": _row_info(o)}
                                        for o in rows[i + 1:] if worker_ready(o, set(caps or ()), card_check)[0]]
            return out, None
        skipped.append({"box": r.get("box"), "model": r.get("model"), "why": why})
    return None, "no Fellowship worker can build it now: " + "; ".join(
        "%s %s (%s)" % (s["box"], s["model"], s["why"]) for s in skipped)


def warm_nodes(spec):
    """How many warm nodes a placed piece asks for (one answer needs one), or None: every free node (CHAT1's shape)."""
    n = spec.get("warm_nodes")
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else None


def pick_warm_nodes(pool, n, tablets=None):
    """The n FASTEST free nodes by their measured GFLOP/s (scheduler.TABLETS; the class rules already filtered the
    pool), ties by name - one answer should not load a model on every tablet."""
    if tablets is None:
        import scheduler
        tablets = scheduler.TABLETS
    return sorted(pool, key=lambda h: (-float((tablets.get(h) or {}).get("gflops") or 0.0), h))[:n]


def server_bin(gpu_layers=None, fit=None, vk_dir=None):
    """The llama.cpp build a placement needs: the Vulkan build (vk_dir: the next build's, or the live one) when the order
    places anything on a card - by hand (gpu_layers) or by the fitter (fit = (target MiB, ctx))."""
    return (vk_dir or LLAMA_VULKAN) if (gpu_layers is not None or fit is not None) else LLAMA_BIN


def gpu_list(gpu):
    """A placement's cards as a list: "gtx1060" -> ["gtx1060"]; a list stays a list; None -> []."""
    return [gpu] if isinstance(gpu, str) else list(gpu) if isinstance(gpu, list) else []


def _int_in(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def server_env(gpu_layers=None, fit=None, gpu="gt730", kepler=False, op_offload_min_batch=None):
    on_card = gpu_layers is not None or fit is not None
    env = dict(os.environ, LD_LIBRARY_PATH=server_bin(gpu_layers, fit))
    if op_offload_min_batch is not None:
        # batches at least this big stream the CPU-held weights to the card (llama.cpp default 32)
        env["GGML_OP_OFFLOAD_MIN_BATCH"] = str(op_offload_min_batch)
    if on_card:
        env.pop("LD_LIBRARY_PATH")            # the host loader's --library-path decides; the image's libs must not mix in
        if kepler or "gt730" in gpu_list(gpu):
            # the flags are process-wide: with the GT 730 in the placement they constrain the 1060's shaders too
            env.update(KEPLER_VK_ENV)
    return env


def server_argv(model_path, ctx, rpc=None, draft=None, reasoning=None, spec_type=None, gpu_layers=None,
                override_tensor=None, op_offload=None, draft_rpc=None, vk_dev=None, fit=None, vk_dir=None,
                parallel=None, ubatch=None, threads=None, load_mode=None, draft_max=None, expert_hot=None,
                slot_save_path=None, extra=None, moe_cache_mib=None):
    """draft: a small same-family model for speculative decoding. This build requires --spec-type to activate the draft (default is none, which loads but ignores it), so a draft path implies draft-simple.
    spec_type alone: draft-mtp (model's own MTP heads) or an n-gram type (no draft model).
    reasoning: on|off|auto for thinking models; unset uses the server default.
    gpu_layers / override_tensor: GPU placement (-ngl, then -ot '<regex>=<buffer>' in order).
    draft_rpc: (endpoint, device) for the draft on an RPC server. The target model MUST get --device none; otherwise -ngl defaults to auto, which would incorrectly map target layers onto the registered RPC device."""
    sbin = server_bin(gpu_layers, fit, vk_dir)
    head = [os.path.join(sbin, "llama-server")]
    if gpu_layers is not None or fit is not None:
        head = [os.path.join(HOSTLIB, "ld-linux-x86-64.so.2"), "--library-path", "%s:%s" % (sbin, HOSTLIB)] + head
    argv = head + ["-m", model_path, "-c", str(ctx), "-t", str(threads or THREADS), "--host", "127.0.0.1",
                   "--port", str(SERVER_PORT)]
    if load_mode is not None:
        # "none" loads the weights instead of mapping them, so the CPU backend can repack them (llama.cpp warns:
        # "tensor overrides to CPU are used with mmap enabled - consider using --load-mode none")
        argv += ["--load-mode", load_mode]
    if parallel is not None:
        # -np 1: one stream. The default is 4 slots, and a hybrid model keeps a recurrent state per slot (Mothership,
        # Flash-Next doc: 0.34 GB at -np 1 vs 1.35 GB at -np 4) - memory the fitter would otherwise fill with experts
        argv += ["-np", str(parallel)]
    if ubatch is not None:
        # the logical batch must hold the micro-batch (llama-server default -b 2048)
        argv += ["-ub", str(ubatch)] + (["-b", str(ubatch)] if ubatch > 2048 else [])
    if fit is not None:
        # llama.cpp's own fitter places layers and experts in the card's free memory (margin fit[0] MiB, sized for
        # context fit[1]) - no hand -ngl / -ot, which would switch it off
        # one card = split-mode none; several = layer split, the fitter filling each card to its own margin
        argv += ["-dev", vk_dev, "-sm", "layer" if "," in vk_dev else "none", "-fitt", str(fit[0]),
                 "-fitc", str(fit[1])]
    if gpu_layers is not None:
        # op_offload False keeps prompt batches on the CPU: by default llama.cpp streams host weights to the device
        # for every batch (measured: Kepler read 5x slower for it)
        argv += card_args({"gpu_layers": gpu_layers, "override_tensor": override_tensor, "op_offload": op_offload},
                          vk_dev)
    if expert_hot:
        # the hotstore build: slots per layer (-1 = sized from free VRAM; any N > 0 also forces every expert to the
        # CPU, the slots being copies), dwell = re-syncs a resident keeps its slot, hyst = how much hotter a
        # challenger must be to evict it
        argv += ["-ehs", str(expert_hot["slots"])]
        if "dwell" in expert_hot:
            argv += ["--expert-dwell", str(expert_hot["dwell"])]
        if "hyst" in expert_hot:
            argv += ["--expert-hyst", str(expert_hot["hyst"])]
    if moe_cache_mib:
        # the merged expert cache: an LRU of hot experts on the card (BigHonker's A3B: 1024 MiB the winner, 10-17)
        argv += ["--moe-cache-mib", str(moe_cache_mib)]
    if reasoning:
        argv += ["--reasoning", reasoning]
    if slot_save_path:
        # the slot's KV + recurrent state can be saved to / restored from this directory (/slots/0?action=save|restore)
        # - #34 B11: the live state a deploy would otherwise throw away (Xbox-style quick resume)
        argv += ["--slot-save-path", slot_save_path]
    if rpc:
        argv += ["--rpc", ",".join(rpc), "-ngl", "99"]
    if draft_rpc:
        endpoint, device = draft_rpc
        argv += ["--rpc", endpoint, "--device", "none", "-ngl", "0", "--device-draft", device, "-ngld", "99"]
    if draft:
        argv += ["--model-draft", draft]
    spec_type = spec_type or ("draft-simple" if draft else None)
    if spec_type:
        argv += ["--spec-type", spec_type]
        if draft_max is not None:
            argv += draft_max_args(spec_type, draft_max)
    argv += list(extra or [])          # a kind's own server flags (the embed form: --embeddings), last so they read plainly
    return argv


_SERVER_ONLY = {"--host", "--port", "--reasoning", "--spec-type", "--model-draft", "--slot-save-path"}
FIT_REPORT_LINES = 80


_ENGINE_LINE = re.compile(r"moe|buffer size", re.I)


def engine_report(log_path, limit=24):
    """Returns server load-time lines for the expert cache and buffer sizes.

    Distinguishes what is PRESENT in the log from what is DECLARED in the config.
    Returns [] if nothing matches, the file is missing, or the CUDA build does not print these details."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            hits = [l.strip() for l in f if _ENGINE_LINE.search(l)]
    except OSError:
        return []
    return hits[:limit]


def fit_placement(server_argv_, env, timeout=300):
    """What llama.cpp's fitter will place where for this exact server launch: llama-fit-params (same build) with the
    same arguments, --fit-print on. {"rc", "lines"} - the fitter's own report, so a speed can be read against the
    placement that produced it: the raw tail, unfiltered (a keyword filter kept 1 header line of the memory table
    and the fitted arguments on its first live run). Never raises: {"error": ...} is "could not tell"."""
    argv, skip = [], False
    for a in server_argv_:
        if skip:
            skip = False
            continue
        if a in _SERVER_ONLY or a.startswith("--spec-"):
            skip = True
            continue
        argv.append(a)
    i = next((k for k, a in enumerate(argv) if a.endswith("/llama-server")), None)
    if i is None:
        return {"error": "no llama-server in the launch argv"}
    argv[i] = argv[i][:-len("llama-server")] + "llama-fit-params"
    try:
        p = subprocess.run(argv + ["-fitp", "on"], env=env, capture_output=True, text=True, timeout=timeout)
    except Exception as e:                                                   # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}
    text = (p.stdout or "") + (p.stderr or "")
    return {"rc": p.returncode, "lines": [l.rstrip() for l in text.splitlines() if l.strip()][-FIT_REPORT_LINES:]}


_RPC_DEV = re.compile(r"^\s+(RPC\d*(?:\[[^\]]*\])?):\s", re.M)


def rpc_devices(text):
    """The RPC device names in `llama-server --list-devices` output (`RPC0[ip:port]: ...` or `RPC0: ...`)."""
    return _RPC_DEV.findall(text or "")


def list_rpc_devices(endpoint):
    """Ask this build what it registers for one rpc-server; --device-draft takes exactly that name."""
    out = subprocess.run([os.path.join(LLAMA_BIN, "llama-server"), "--rpc", endpoint, "--list-devices"],
                         env=server_env(), capture_output=True, text=True, timeout=60)
    return rpc_devices(out.stdout + out.stderr), (out.stdout + out.stderr)[-400:]


def _get(path, timeout=10):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (SERVER_PORT, path), timeout=timeout) as r:
        return r.status, r.read()


def start_server(model_path, ctx, rpc, log_path, stop_event=None, tick=None, draft=None, reasoning=None,
                 spec_type=None, gpu_layers=None, override_tensor=None, op_offload=None, draft_rpc=None,
                 gpu="gt730", fit_target=None, build=None, parallel=None, ubatch=None, kepler_flags=False,
                 report=None, threads=None, load_mode=None, draft_max=None, op_offload_min_batch=None,
                 engine_env=None, expert_hot=None, slot_save_path=None, extra=None, moe_cache_mib=None):
    """Popen llama-server and wait for /health = 200. Returns (proc, load_s) or raises with the log tail.
    `tick` is called on every poll while it loads - the order's heartbeat through a long load.
    gpu names the card(s) (GPU_NAMES; a list = the fitter spreads over them); fit_target = llama.cpp's fitter, one
    margin or one per card; build "next" = the async-RPC build. report(dict) receives the fitter's placement."""
    meter_forget_ctx(None)          # a new server on the order's port: the tile asks its context size again
    gpus = gpu_list(gpu) or ["gt730"]
    fit = None
    if fit_target is not None:
        fts = fit_target if isinstance(fit_target, list) else [fit_target] * len(gpus)
        fit = (",".join(str(x) for x in fts), ctx)
    vk_dir = card_dir(build)
    env = server_env(gpu_layers, fit, gpus, kepler_flags, op_offload_min_batch)
    if build in CUDA_DIRS:
        env["CUDA_VISIBLE_DEVICES"] = cuda_visible(gpus[0])
    env.update({k: str(v) for k, v in (engine_env or {}).items()})
    dev = ",".join(vulkan_device(vk_dir, g) for g in gpus) if (gpu_layers is not None or fit is not None) else None
    argv = server_argv(model_path, ctx, rpc, draft=draft, reasoning=reasoning, spec_type=spec_type,
                       gpu_layers=gpu_layers, override_tensor=override_tensor, op_offload=op_offload,
                       draft_rpc=draft_rpc, vk_dev=dev, fit=fit, vk_dir=vk_dir, parallel=parallel, ubatch=ubatch,
                       threads=threads, load_mode=load_mode, draft_max=draft_max, expert_hot=expert_hot,
                       slot_save_path=slot_save_path, extra=extra, moe_cache_mib=moe_cache_mib)
    if fit is not None and report is not None:
        report(fit_placement(argv, env))
    logf = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
    t0 = time.time()
    while time.time() - t0 < LOAD_TIMEOUT_S:
        if tick is not None:
            tick("loading %ds" % (time.time() - t0))
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited rc=%s while loading: %s" % (proc.returncode, _tail(log_path)))
        if stop_event is not None and stop_event.is_set():
            stop_server(proc)
            raise RuntimeError("stopped by slip while loading")
        try:
            if _get("/health", timeout=5)[0] == 200:
                return proc, round(time.time() - t0, 1)
        except Exception:
            pass
        time.sleep(3)
    stop_server(proc)
    raise RuntimeError("llama-server not healthy after %ds: %s" % (LOAD_TIMEOUT_S, _tail(log_path)))


def stop_server(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)


def _tail(path, n=400):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()[-n:].replace("\n", " | ")
    except OSError:
        return "(no log)"


# A loop is no progress (work ends on no progress, never on a clock). Rule, measured on 571
# genuine finishes and 253 answers that ran to the context: BOTH of the last two 4000-char windows, digits collapsed to
# "0", compress below 0.10 -> 0 correct answers flagged. Collapsing digits catches a COUNTING loop ("1.0.2402",
# "1.0.2403", ... 0.125 raw, 0.013 collapsed). tests/loop_fixtures.json holds the real texts;
# infer_ollama.looping is the same rule on the GPU factories, the tablet payloads' idle runner the same on llama-cli.
# --- LOOP RULE: one source. This block is byte-identical in infer_ollama.py and tolkien_llm.py (test_fleet_deploy pins it).
LOOP_WINDOW, LOOP_RATIO, LOOP_EVERY = 4000, 0.10, 2000
_DIGITS = re.compile(r"\d+")
# Collapsing digits makes a table of numbers read as one passage over and over, so a turn that works through a numeric
# table (in its thinking, then in its code) would be cut. So when the digits are what makes it low, the same windows are
# read with the digits kept. Real loops
# read at most 0.126 that way (counting_loop), the tables at least 0.198 (number_table_in_code), and no other turn of 433 saved
# turns of 8,000+ chars reaches this branch. Numbers that differ are data until they have run far longer than any table seen
# (the longest stretch measured is 9,000 chars): LOOP_DATA_WINDOWS windows of them is a runaway, whatever the digits say.
LOOP_RAW_RATIO, LOOP_DATA_WINDOWS = 0.15, 6
# A loop LONGER than those windows compresses like prose, so the ratio cannot see it (measured: one 23,401-char block
# written back to back, byte for byte, 14 copies, 333,843 chars). `repeating` is the
# second half of the rule: the output ENDS in one block written back to back, exactly, at least three copies and at
# least the same 2 * LOOP_WINDOW chars of it. Blocks under LOOP_MIN_PERIOD (half of LOOP_WINDOW * LOOP_RATIO, where the
# ratio takes over) are the ratio's to judge. Digits are NOT collapsed here: rows that differ only in their numbers are
# data. Measured on the saved long runs: it ends that turn at 168,000 chars (half), and fires on none of the other turns
# of 8,000+ chars (the longest 206,716).
LOOP_PROBE = int(LOOP_WINDOW * LOOP_RATIO)
LOOP_MIN_PERIOD = LOOP_PROBE // 2


def repeating(text):
    """The length of the block `text` ends in back to back (>= 3 exact copies, >= 2 * LOOP_WINDOW chars of them), else 0."""
    n = len(text)
    if n < 2 * LOOP_WINDOW:
        return 0
    q = text.rfind(text[-LOOP_PROBE:], 0, n - 1)
    if q < 0:
        return 0
    p = n - LOOP_PROBE - q
    if p < LOOP_MIN_PERIOD:
        return 0
    copies = max(3, -(-2 * LOOP_WINDOW // p))
    if n < copies * p:
        return 0
    last = text[n - p:]
    return p if all(text[n - (i + 1) * p:n - i * p] == last for i in range(1, copies)) else 0


def looping(text):
    """True when the output stopped making progress. `text` is everything the model has written this turn: its last
    two LOOP_WINDOW-char windows, digits collapsed, both compress below LOOP_RATIO and with the digits kept both below
    LOOP_RAW_RATIO; or LOOP_DATA_WINDOWS such windows of numbers that differ; or it ends in one long block written over
    and over (`repeating`)."""
    if len(text) < 2 * LOOP_WINDOW:
        return False

    def r(s, collapse=True):
        b = (_DIGITS.sub("0", s) if collapse else s).encode("utf-8", "replace")
        return len(zlib.compress(b, 9)) / len(b)
    last, prev = text[-LOOP_WINDOW:], text[-2 * LOOP_WINDOW:-LOOP_WINDOW]
    if r(last) < LOOP_RATIO and r(prev) < LOOP_RATIO:
        if r(last, False) < LOOP_RAW_RATIO and r(prev, False) < LOOP_RAW_RATIO:
            return True
        if len(text) >= LOOP_DATA_WINDOWS * LOOP_WINDOW and all(
                r(text[-(i + 1) * LOOP_WINDOW:-i * LOOP_WINDOW]) < LOOP_RATIO for i in range(2, LOOP_DATA_WINDOWS)):
            return True
    return bool(repeating(text))
# --- END LOOP RULE


# Meters all model work via the shared complete() and chat() paths to ensure consistent accounting.
METER_MIN_TOKENS = 20      # a shorter prompt is mostly overhead, not a read rate (the GPU factories' ENGINE_MIN_TOKENS)
METER_WINDOW = 10
_METER_LK = threading.Lock()
_METER = {}


def meter_reset():
    """A new order: nothing counted yet."""
    with _METER_LK:
        _METER.clear()
        _METER.update(calls=0, running=0, since=None, reads=[], ctx=None, caps={})


def meter_forget_ctx(base=None):
    """A server (re)started on `base`: ask it its context size again."""
    with _METER_LK:
        (_METER.get("caps") or {}).pop(base, None)


def _slot_ctx(base):
    """The context one answer may hold on this server (llama-server /props n_ctx), asked once per server; None when
    it cannot say."""
    with _METER_LK:
        caps = _METER.setdefault("caps", {})
        if base in caps:
            return caps[base]
    n = None
    try:
        url = (base or "http://127.0.0.1:%d" % SERVER_PORT) + "/props"
        with urllib.request.urlopen(url, timeout=2) as r:
            props = json.loads(r.read().decode("utf-8", "replace")) or {}
        n = int((props.get("default_generation_settings") or {}).get("n_ctx") or 0) or None
    except Exception:                                                        # noqa: BLE001
        n = None
    with _METER_LK:
        _METER.setdefault("caps", {})[base] = n
    return n


def _meter_end(out, base):
    """One call ended: its read rate and the context it held. Never raises - it runs in complete()/chat()'s finally."""
    try:
        with _METER_LK:
            _METER["running"] = max(0, _METER.get("running", 0) - 1)
            _METER["since"] = time.time()
        prompt_n = gen_n = cache_n = read = None
        if isinstance(out, tuple) and len(out) == 3 and isinstance(out[2], dict):
            m = out[2]
            prompt_n, gen_n, cache_n, read = (m.get("prompt_tokens"), m.get("gen_tokens"), m.get("cache_n"),
                                              m.get("prompt_tps"))
        elif isinstance(out, dict):
            prompt_n, gen_n, cache_n = out.get("prompt_eval_count"), out.get("eval_count"), out.get("cache_n")
            d = out.get("prompt_eval_duration")
            read = (prompt_n / (d / 1e9)) if (prompt_n and d) else None
        if not (prompt_n or gen_n):
            return
        cap = _slot_ctx(base)
        with _METER_LK:
            if read and prompt_n >= METER_MIN_TOKENS:
                _METER["reads"] = (_METER.get("reads") or [])[-(METER_WINDOW - 1):] + [float(read)]
            if cap:
                _METER["ctx"] = [int((cache_n or 0) + (prompt_n or 0) + (gen_n or 0)), cap]
    except Exception:                                                        # noqa: BLE001
        pass


def _metered(fn):
    """Count each model call for the order's tile rows (meter_view)."""
    @functools.wraps(fn)
    def call(*a, **k):
        with _METER_LK:
            _METER["calls"] = _METER.get("calls", 0) + 1
            _METER["running"] = _METER.get("running", 0) + 1
            _METER["since"] = time.time()
        out = None
        try:
            out = fn(*a, **k)
            return out
        finally:
            _meter_end(out, k.get("base"))
    return call


def median(xs):
    """The middle value of xs, or None when empty."""
    s = sorted(xs or [])
    if not s:
        return None
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def meter_view(write=None):
    """The `gen` the deck's tile formats with Kirk's own formatter: write (the caller's rate - the one its phase
    carries) and read tok/s, the call the order is on and what it is doing, and the context the last answer held.
    The step time is left out while answers run in parallel (the warm arm): one clock cannot time several."""
    with _METER_LK:
        m = dict(_METER)
    calls, running, since = m.get("calls") or 0, m.get("running") or 0, m.get("since")
    return {"write": write, "read": median(m.get("reads")), "turn": calls or None, "turn_label": "call",
            "tool": ("generating" if running else "between calls") if calls else None,
            "tool_s": (time.time() - since) if (calls and since and running <= 1) else None,
            "tool_running": bool(running), "ctx": m.get("ctx")}


# Quick resume restores a cut answer by dumping the slot (KV/recurrent/checkpoints) and pending token to <state>/slots.
# Restored state only applies to the exact call; any mismatch (e.g. cut inside a tool call, different build, or different
# launch) must fall back to re-asking rather than continuing with stale state.
def slots_dir():
    """<state>/slots, read at CALL time (a test or gate that redirects tf.STATE_DIR after import must win)."""
    return os.path.join(tf.STATE_DIR, "slots")


RESUME = {"name": None, "launch": None, "cut": None, "restored": None, "log": None}
_RESUME_KEYS = ("messages", "tools", "temperature", "seed", "top_p", "top_k", "grammar")
TOOL_CALL_OPEN = "<tool_call>"     # rigby's Qwen tool-call marker: a half answer holding one is not continued


def _resume_files(name):
    base = os.path.join(slots_dir(), name + ".resume")
    return base + ".bin", base + ".json"


def request_key(body):
    """What decides one request's answer - its messages, tools and sampling, never the stream or limit knobs. A
    resumed order re-asks the cut call with the same key (A1 replays its checkpoint; a row is rebuilt from its task)."""
    keep = {k: body.get(k) for k in _RESUME_KEYS if body.get(k) is not None}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def resume_launch(spec, ctx):
    """The launch a dump is valid for: the model, the context, rigby's launch knobs and the engine's own files (a
    rebuilt engine need not read an older slot file)."""
    d = card_dir(spec.get("build"))
    eng = {}
    for f in ("libllama-server-impl.so", "libllama.so", "llama-server"):
        try:
            st = os.stat(os.path.join(d, f))
            eng[f] = [st.st_size, int(st.st_mtime)]
        except OSError:
            eng[f] = None
    return json.loads(json.dumps({"model": spec.get("model"), "ctx": int(ctx), "server": rigby_server_kwargs(spec),
                                  "engine": eng}))


def retire_resume(name, why=None, log=None):
    """Delete one order's dump by its exact names (a derived cache: worthless once spent or stale). Never raises."""
    for p in _resume_files(name):
        try:
            os.remove(p)
        except OSError:
            pass
    if why and log:
        log("quick resume: %s's dump retired (%s)" % (name, why))


def queued_names(queue_dir=None):
    """The spec names of every order still in Tolkien's queue (held ones included - they come back)."""
    out = set()
    for p in glob.glob(os.path.join(queue_dir or tf.QUEUE_DIR, "*.tjob.json*")):
        name = None
        try:
            with open(p, encoding="utf-8") as f:
                name = (json.load(f) or {}).get("name")
        except Exception:                                                    # noqa: BLE001 - the stem still names it
            pass
        out.add(name or os.path.basename(p).split(".tjob.json")[0])
    return out


def prune_resumes(keep, queued, log=None):
    """Retire every dump whose order is neither `keep` nor still queued - a cancelled order never comes back for it."""
    for j in glob.glob(os.path.join(slots_dir(), "*.resume.json")):
        name = os.path.basename(j)[:-len(".resume.json")]
        if name != keep and name not in queued:
            retire_resume(name, "its order is no longer queued", log)


def dump_on_stop(body, reasoning, content, tool_called, pre_wall=None, pre_tokens=None, log=None):
    """A stop slip cut this request on the resume-armed order: save the slot (the server defers a busy slot's save
    until the disconnected generation stops by itself) with its pending token, and record the cut call. Called AFTER
    the stream is closed. Returns the record or None; never raises - no dump leaves today's behaviour."""
    name = RESUME.get("name")
    if not name:
        return None
    bin_f, json_f = _resume_files(name)
    try:
        sv = _http_json(SERVER_PORT, "/slots/0?action=save", {"filename": os.path.basename(bin_f)})
        rec = {"launch": RESUME.get("launch"), "saved_tokens": sv.get("n_saved"), "bytes": sv.get("n_written"),
               "save_ms": (sv.get("timings") or {}).get("save_ms"), "pending_token": sv.get("pending_token"),
               "lab_replay": sv.get("lab_replay"), "lab_drafter_bytes": sv.get("lab_drafter_bytes"),
               "cut": {"key": request_key(body), "continuable": not tool_called, "reasoning": reasoning,
                       "content": content, "pre_wall_s": pre_wall, "pre_tokens": pre_tokens},
               "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = json_f + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        os.replace(tmp, json_f)
        RESUME["dumped"] = True
        if log:
            log("quick resume: dumped %s (%s tokens, %s bytes, %s ms; pending token %s; %s)"
                % (name, rec["saved_tokens"], rec["bytes"], rec["save_ms"], rec["pending_token"],
                   "the cut call can continue" if not tool_called else "cut inside a tool call: prefix only"))
        return rec
    except Exception as e:                                                   # noqa: BLE001 - never worse than today
        if log:
            log("quick resume: no dump for %s (%s: %s) - the requeue re-asks" % (name, type(e).__name__, str(e)[:200]))
        return None


def prepare_resume(log=None):
    """Restore the dump for this specific launch only; retire dumps from other launches or engine builds.

    The RESTORE happens right before the cut call (restore_for_cut), never at start: a resumed order runs its control first, and any other request overwrites slot 0. Restoring at start would lose the state to the control.

    Returns the record or None; never raises."""
    name = RESUME.get("name")
    if not name:
        return None
    bin_f, json_f = _resume_files(name)
    if not (os.path.exists(bin_f) and os.path.exists(json_f)):
        return None
    try:
        with open(json_f, encoding="utf-8") as f:
            rec = json.load(f)
    except Exception:                                                        # noqa: BLE001
        retire_resume(name, "its record does not read", log)
        return None
    if rec.get("launch") != RESUME.get("launch") or not isinstance(rec.get("cut"), dict):
        retire_resume(name, "made for another launch or engine build", log)
        return None
    RESUME["cut"] = dict(rec["cut"], pending_token=rec.get("pending_token"))
    RESUME["accepted"] = rec
    if log:
        log("quick resume: %s's dump (%s tokens, %s) waits for its cut call" % (
            name, rec.get("saved_tokens"), "continues it" if rec["cut"].get("continuable") else "prefix only"))
    return rec


def restore_for_cut(post=None):
    """Restore the accepted dump into slot 0 - called right before the cut call is asked. -> True when restored."""
    post = post or (lambda path, b: _http_json(SERVER_PORT, path, b))
    name, log = RESUME.get("name"), RESUME.get("log")
    try:
        rs = post("/slots/0?action=restore", {"filename": os.path.basename(_resume_files(name)[0])})
    except Exception as e:                                                   # noqa: BLE001 - never worse than today
        if log:
            log("quick resume: restore refused (%s) - asked as usual" % str(e)[:200])
        return False
    RESUME["restored"] = {"restored_tokens": rs.get("n_restored"),
                          "restore_ms": (rs.get("timings") or {}).get("restore_ms"),
                          "armed": rs.get("lab_resume_armed")}       # engine patch #3: None on an engine without it
    if log:
        log("quick resume: restored %s (%s tokens in %s ms) for its cut call" % (
            name, RESUME["restored"]["restored_tokens"], RESUME["restored"]["restore_ms"]))
    return True


def _take_cut(body):
    """The accepted cut call, if `body` IS that call (consumed once)."""
    cut = RESUME.get("cut")
    if cut and cut.get("key") == request_key(body):
        RESUME["cut"] = None
        return cut
    return None


def continue_for(body, cut, post=None, read_slot=None):
    """The cut call as a chat CONTINUE from the restored slot's own ids (+ the pending token), in the prefill form
    whose render equals them. -> (continue body, the half answer's reasoning, its content, info) or None when anything
    does not line up (the slot is not this call's, the half answer opened a tool call) - the caller then asks as usual
    and the restored prefix still saves the read."""
    post = post or (lambda path, b: _http_json(SERVER_PORT, path, b))
    read_slot = read_slot or (lambda: slot_file_tokens(_resume_files(RESUME["name"])[0]))
    base = {k: body[k] for k in ("messages", "tools") if body.get(k) is not None}
    ptext = post("/apply-template", base)["prompt"]

    def tok(text):
        return post("/tokenize", {"content": text, "add_special": True, "parse_special": True})["tokens"]
    ids, P = read_slot(), tok(ptext)
    if ids[:len(P)] != P or len(ids) <= len(P):
        return None
    pending = cut.get("pending_token")
    pending = pending if isinstance(pending, int) and not isinstance(pending, bool) and pending >= 0 else None
    gen = ids[len(P):] + ([pending] if pending is not None else [])
    gen_text = post("/detokenize", {"tokens": gen})["content"]
    if TOOL_CALL_OPEN in gen_text:
        return None

    def cbody(msg, cont):
        return dict(base, messages=list(body["messages"]) + [msg], continue_final_message=cont,
                    add_generation_prompt=False)
    target = ids + ([pending] if pending is not None else [])
    form, msg, cont, R = pick_prefill(prefill_candidates(ptext, gen_text), lambda mg, ct: tok(post(
        "/apply-template", cbody(mg, ct))["prompt"]), target)
    out = dict(body, messages=list(body["messages"]) + [msg], continue_final_message=cont, add_generation_prompt=False)
    if isinstance(body.get("max_tokens"), int) and body["max_tokens"] > 0:
        out["max_tokens"] = max(1, body["max_tokens"] - len(gen))
    # engine patch #3 (slot_resume_patch.py): when the restore says it armed the slot, the engine takes the
    # interrupted run's NEXT STEP from the pending token (drafts and all) instead of reading it alone - the MTP order's
    # answer then stays the uninterrupted one. Only on an exact render: the engine checks the prompt is the saved
    # state + that token, and anything else simply runs as before.
    lab = pending is not None and R == target and bool((RESUME.get("restored") or {}).get("armed"))
    if lab:
        out["lab_resume"] = True
    info = {"continued_from": len(gen), "pending_token": pending is not None, "render_exact": R == target,
            "lab_resume": lab, "pre_wall_s": cut.get("pre_wall_s"),
            "restore_ms": (RESUME.get("restored") or {}).get("restore_ms")}
    return out, msg.get("reasoning_content") or "", msg.get("content") or "", info


def _resume_continue(body):
    """complete()/chat(): if `body` IS the cut call, restore the dump into slot 0 now and return its continue (None
    for a prefix-only dump: the plain ask then reuses the restored prefix). Never raises."""
    cut = _take_cut(body)
    if not cut or not restore_for_cut():
        return None
    log = RESUME.get("log")
    if not cut.get("continuable"):
        if log:
            log("quick resume: the cut was inside a tool call - re-asked on the restored prefix")
        return None
    try:
        out = continue_for(body, cut)
    except Exception as e:                                                   # noqa: BLE001 - never worse than today
        out = None
        if log:
            log("quick resume: continue not built (%s: %s) - asked as usual" % (type(e).__name__, str(e)[:200]))
    if out is None and log:
        log("quick resume: the cut call does not line up with the restored slot - asked as usual")
    elif out and log:
        log("quick resume: continuing the cut call from token %d (pending token %s, render exact %s, lab resume %s)"
            % (out[3]["continued_from"], out[3]["pending_token"], out[3]["render_exact"], out[3].get("lab_resume")))
    return out


@_metered
def complete(prompt, seed, temperature, stop_event=None, live=None, ceiling_s=None, grammar=None, base=None):
    """One chat completion (the model's own chat template, as llama-cli -st applies it on the tablets), streamed:
    the socket timeout is per read, so only IDLE_S of silence fails it.

    Three things end an answer early, each with its own finish_reason and the partial text kept:
      "stopped"    a stop slip (stop_event) - checked on every streamed chunk
      "priced_out" the order's measured price ceiling (ceiling_s) was passed
      "loop"       the output stopped making progress (`looping`) - the model's answer, graded and void like "length"
    Leaving the stream closes the connection, and llama-server abandons a generation whose client is gone.
    `live(answer, thinking, n_chunks, elapsed_s)` is called every LIVE_EVERY_S while tokens arrive.
    `grammar`: a GBNF the answer must follow (a tool payload's own, as the tablets constrain llama-cli)."""
    req_body = {"messages": [{"role": "user", "content": prompt}], "temperature": temperature,
                "seed": seed, "max_tokens": -1, "stream": True, "timings_per_token": True,
                "stream_options": {"include_usage": True}}
    if grammar:
        req_body["grammar"] = grammar
    resumed = _resume_continue(req_body) if base is None else None      # the cut call after a quick resume
    body = json.dumps(resumed[0] if resumed else req_body).encode("utf-8")
    # `base`: another llama-server (a warm tablet, http://<wire ip>:<port>); default this order's own on rigby
    req = urllib.request.Request((base or "http://127.0.0.1:%d" % SERVER_PORT) + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = last_live = time.time()
    parts, think, tm, usage, finish, n = [], [], {}, {}, None, 0
    ttft = None                     # time to the first streamed token - the interactive price of a chat turn
    tail, unchecked = "", 0
    with urllib.request.urlopen(req, timeout=IDLE_S) as r:
        for raw in r:
            if stop_event is not None and stop_event.is_set():
                finish = "stopped"
                break
            if ceiling_s and time.time() - t0 > ceiling_s:
                finish = "priced_out"
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            obj = json.loads(data)
            for ch in obj.get("choices") or []:
                delta = ch.get("delta") or {}
                if delta.get("content") or delta.get("reasoning_content"):
                    n += 1
                    if ttft is None:
                        ttft = round(time.time() - t0, 3)
                parts.append(delta.get("content") or "")
                think.append(delta.get("reasoning_content") or "")
                finish = ch.get("finish_reason") or finish
                piece = (delta.get("reasoning_content") or "") + (delta.get("content") or "")
                tail += piece
                unchecked += len(piece)
            if unchecked >= LOOP_EVERY:
                unchecked = 0
                if looping(tail):
                    finish = "loop"
                    break
            tm = obj.get("timings") or tm
            usage = obj.get("usage") or usage
            if live is not None and time.time() - last_live >= LIVE_EVERY_S:
                last_live = time.time()
                live("".join(parts), "".join(think), n, last_live - t0)
    wall = round(time.time() - t0, 1)
    answer, thought = "".join(parts), "".join(think)
    gen = tm.get("predicted_n", usage.get("completion_tokens")) or (n if finish in CUT + ("loop",) else None)
    extra = {}
    if resumed:
        # the answer = the half written before the stop + what the continue wrote; its price excludes the restart
        _, pre_r, pre_c, info = resumed
        answer, thought = pre_c + answer, pre_r + thought
        gen = (gen or 0) + info["continued_from"]
        wall = round(wall + (info.get("pre_wall_s") or 0), 1)
        extra["resumed_from_dump"] = info
    if finish == "stopped" and base is None:
        dump_on_stop(req_body, thought, answer, False, pre_wall=wall, pre_tokens=gen, log=RESUME.get("log"))
    return answer, wall, dict({
        "prompt_tokens": tm.get("prompt_n", usage.get("prompt_tokens")),
        "gen_tokens": gen,
        "prompt_tps": tm.get("prompt_per_second"), "gen_tps": tm.get("predicted_per_second"),
        "prompt_ms": tm.get("prompt_ms"), "gen_ms": tm.get("predicted_ms"), "cache_n": tm.get("cache_n"),
        "draft_n": tm.get("draft_n"), "draft_accepted": tm.get("draft_n_accepted"), "finish_reason": finish,
        "thinking": thought, "ttft_s": ttft}, **extra)


CUT = ("stopped", "priced_out")


@_metered
def chat(messages, tools=None, options=None, stop_event=None, live=None):
    """A multi-turn chat with TOOLS on this order's llama-server, streamed and ended only by IDLE_S of silence (the
    socket timeout is per read). Returns the SAME shape as the GPU factories' infer_ollama._chat, so one mapping
    serves both (the A1 `lab` Inspect provider): {"message": {"content", "thinking", "tool_calls": [{"function":
    {"name", "arguments"}}]}, "prompt_eval_count", "eval_count", "done_reason"}. Context and reasoning are the
    server's launch settings (server_argv); `options` carries per-request sampling (temperature, seed, top_p, top_k) and an optional
    `num_predict` output cap (sent as max_tokens; absent = -1, the whole context).
    Tool-call arguments stream as JSON text fragments per index; an unparseable result is passed through as text,
    so the caller can hand the model its own malformed call back. `live(answer, thinking, n_chunks, elapsed_s)` is
    called every LIVE_EVERY_S while chunks arrive, as complete() does: the order's beat during a long turn."""
    body = {"messages": messages, "stream": True, "max_tokens": -1, "timings_per_token": True,
            "stream_options": {"include_usage": True}}
    for k in ("temperature", "seed", "top_p", "top_k"):
        if (options or {}).get(k) is not None:
            body[k] = options[k]
    if (options or {}).get("num_predict"):          # the A1 runaway guard's per-call cap (ollama's name for it)
        body["max_tokens"] = int(options["num_predict"])
    if tools:
        body["tools"] = tools
    resumed = _resume_continue(body)                 # the cut call after a quick resume continues from its token
    pre_r, pre_c = (resumed[1], resumed[2]) if resumed else ("", "")
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % SERVER_PORT,
                                 data=json.dumps(resumed[0] if resumed else body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    parts, think, calls, tm, usage, finish, n = [], [], {}, {}, {}, None, 0
    tail, unchecked = "", 0
    t0 = last_live = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=IDLE_S)
    except urllib.error.HTTPError as e:      # the server's own reason (context overflow, a bad message) - keep it
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:                                                    # noqa: BLE001
            pass
        raise RuntimeError("llama-server HTTP %s: %s" % (e.code, body or e.reason)) from None
    with resp as r:
        for raw in r:
            if stop_event is not None and stop_event.is_set():
                finish = "stopped"
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            obj = json.loads(data)
            for ch in obj.get("choices") or []:
                delta = ch.get("delta") or {}
                n += 1
                parts.append(delta.get("content") or "")
                think.append(delta.get("reasoning_content") or "")
                piece = (delta.get("reasoning_content") or "") + (delta.get("content") or "")
                for tc in delta.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", len(calls)), {"name": "", "arguments": ""})
                    fn = tc.get("function") or {}
                    slot["name"] += fn.get("name") or ""
                    slot["arguments"] += fn.get("arguments") or ""
                    piece += (fn.get("name") or "") + (fn.get("arguments") or "")
                finish = ch.get("finish_reason") or finish
                tail += piece
                unchecked += len(piece)
            if unchecked >= LOOP_EVERY:
                unchecked = 0
                if looping(tail):                    # the same rule complete() applies, over all it streamed
                    finish = "loop"
                    break
            tm = obj.get("timings") or tm
            usage = obj.get("usage") or usage
            if live is not None and time.time() - last_live >= LIVE_EVERY_S:
                last_live = time.time()
                args = "".join("\n[tool call %s] %s" % (c["name"], c["arguments"]) for c in calls.values())
                live(pre_c + "".join(parts) + args, pre_r + "".join(think), n, last_live - t0)
    tool_calls = []
    for i in (sorted(calls) if finish != "loop" else []):        # a looped call is cut mid-arguments: not a call
        a = calls[i]["arguments"]
        try:
            a = json.loads(a) if a.strip() else {}
        except ValueError:
            pass                                      # malformed: the caller hands the model's own text back
        tool_calls.append({"function": {"name": calls[i]["name"], "arguments": a}})
    answer, thought = pre_c + "".join(parts), pre_r + "".join(think)
    ev = tm.get("predicted_n", usage.get("completion_tokens"))
    extra = {}
    if resumed:
        ev = (ev or 0) + resumed[3]["continued_from"]
        extra["resumed_from_dump"] = resumed[3]
    if finish == "stopped":
        # after the stream closed: the server stops the disconnected generation, then saves (it defers a busy slot)
        dump_on_stop(body, thought, answer, bool(calls),
                     pre_wall=round(time.time() - t0 + ((resumed[3].get("pre_wall_s") or 0) if resumed else 0), 1),
                     pre_tokens=ev, log=RESUME.get("log"))
    return dict({"message": {"role": "assistant", "content": answer, "thinking": thought, "tool_calls": tool_calls},
                 "prompt_eval_count": tm.get("prompt_n", usage.get("prompt_tokens")),
                 "eval_count": ev,
                 "prompt_eval_duration": _ms_to_ns(tm.get("prompt_ms")),
                 "eval_duration": _ms_to_ns(tm.get("predicted_ms")),
                 "cache_n": tm.get("cache_n"),
                 "done_reason": finish or "stop"}, **extra)


def _ms_to_ns(ms):
    """The server's milliseconds as the nanoseconds the ollama-shaped result carries; None when the server sent none."""
    return None if ms is None else int(float(ms) * 1e6)


def _silence(e):
    """True when an answer ended because the server sent nothing for IDLE_S (read or connect), not because it said no."""
    if isinstance(e, urllib.error.HTTPError):
        return False
    return isinstance(e, (TimeoutError, socket.timeout)) or (
        isinstance(e, urllib.error.URLError) and isinstance(getattr(e, "reason", None), (TimeoutError, socket.timeout)))


def engine_alive():
    """After an answer went silent: can this order's llama-server produce ANY token? A silent answer alone cannot tell
    a hung server from a slow one, and an error row per silent answer reads as progress; one 4-token probe can tell."""
    body = json.dumps({"messages": [{"role": "user", "content": "ok"}], "max_tokens": 4, "stream": False}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % SERVER_PORT, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_S) as r:
            r.read()
        return True
    except Exception:                                                        # noqa: BLE001
        return False


def write_live(path, head, answer, thinking):
    """The answer being written, replaced atomically so a reader never sees half a file. Never raises."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(head + "\n\n--- thinking ---\n" + thinking + "\n\n--- answer ---\n" + answer + "\n")
        os.replace(tmp, path)
    except Exception:                                                        # noqa: BLE001
        pass


def checkpoint(path, row):
    """Append one finished row; resume_rows reads them back when the order runs again. Never raises."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:                                                        # noqa: BLE001
        pass


def retire_checkpoint(name):
    """Move a COMPLETE order's checkpoint to the archive, so a later order under the same name starts fresh.
    An incomplete or stopped order keeps it: that is what a requeue resumes from. Returns the archive path or None."""
    src = os.path.join(tf.STATE_DIR, "%s.checkpoint.jsonl" % name)
    if not os.path.exists(src):
        return None
    try:
        os.makedirs(tf.ARCHIVE_DIR, exist_ok=True)
        dst = os.path.join(tf.ARCHIVE_DIR, "%s.checkpoint.jsonl.%s" % (name, time.strftime("%Y%m%d-%H%M%S")))
        shutil.move(src, dst)
        return dst
    except Exception:                                                        # noqa: BLE001
        return None


def answered(row):
    """A finished answer: graded (`ran`) or reported at its price (`priced_out`). Stopped and errored rows are not -
    they run again. The one definition: the order's coverage count and the resume both use it."""
    return bool(row.get("ran") or row.get("priced_out"))


def at_cap(row):
    """The answer ran to the end of the order's context: complete() asks for max_tokens -1, so "length" can only be
    the context filling. Token sums cannot tell - a cached prompt reports prompt_tokens 1, and the server stops a few
    tokens short of ctx (184 + 32,559 at 32,768). A loop ended early (`looping`) counts the same: it would have run
    there. menu_fill.rigby_at_cap is the same test on delivered rows."""
    return row.get("finish_reason") in ("length", "loop")


def wilson_low(k, n, z=1.96):
    """Lower 95% bound of a k-of-n share: how small the true share can plausibly be."""
    if not n:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5) / (1 + z * z / n)


# Default price ceiling (10,032s is ~3h). Derived from historical data where items exceeding ~2.9h consistently failed.
# Re-derive if new runs show passes approaching this limit.
RIGBY_A1_CEILING_S = 10800


def baseline_ok(names):
    """{task: solved} of the named Tolkien order(s) from their delivered results, every generation merged (newest
    wins), or None when any has not delivered (the rule is then off)."""
    import glob
    got = {}
    for n in names:
        files = sorted(glob.glob(os.path.join(tf.OUTBOX, n + ".*.results.json")), key=os.path.getmtime)
        if not files:
            return None
        for p in files:
            with open(p, encoding="utf-8") as f:
                for r in json.load(f).get("rows") or []:
                    if r.get("ran") and not str(r.get("task", "")).startswith("control"):
                        got[r["task"]] = bool(r.get("ok"))
    return got


def early_stop_verdict(rows, spec, mod):
    """The GPU factories' A1 early stop (`a1_stop.decide`, shipped in the payload: one derivation) on this arm: None to
    continue, else the stop record. Off without a `stop_rule`, or when the baseline lacks a scored exercise."""
    rule = spec.get("stop_rule")
    if not rule or not hasattr(mod, "early_stop"):
        return None
    names = rule["baseline"] if isinstance(rule["baseline"], list) else str(rule["baseline"]).split(",")
    base_ok = baseline_ok([n.strip() for n in names if n.strip()])
    exclude = set(rule.get("exclude") or ())
    scored = [t for t in spec["tasks"] if t not in exclude]
    if base_ok is None or not set(scored) <= set(base_ok):
        return None
    answered = {r["task"]: {"passed": bool(r.get("ok"))} for r in rows
                if r.get("ran") and not str(r.get("task", "")).startswith("control")}
    return mod.early_stop(answered, spec["tasks"], rule, sum(1 for t in scored if base_ok[t]))


def brake_verdict(rows, ctx, share):
    """None, or why the arm stops: the order states the at-cap share its family's stop rule uses (menu_fill
    FIT_VOID_SHARE), and the arm stops mid-order once the evidence says its share is at least that - the
    between-order rule would stop it on the same rows."""
    # every answer the model finished writing counts, graded or not: an answer the grader could not run is still
    # evidence of where the model stopped
    graded = [r for r in rows if r.get("finish_reason") and r.get("finish_reason") not in CUT
              and not str(r.get("task", "")).startswith("control")]
    capped = sum(1 for r in graded if at_cap(r))
    low = wilson_low(capped, len(graded))
    if share is None or low < float(share):
        return None
    return {"at_cap": capped, "answers": len(graded), "share_low": round(low, 3), "rule_share": float(share),
            "why": "%d of %d answers ran to the %d-token context (share at least %.2f >= the stop rule's %.2f)"
                   % (capped, len(graded), ctx, low, float(share))}


def resume_rows(path, arm, spec, temperature):
    """{(task, replicate): row} - this arm's finished answers already in the order's checkpoint, for the pairs this
    spec asks for, at the same model and temperature (the GPU fleet's infer_ollama convention: an order that runs
    again under its own name re-runs only what is missing or errored). The control is never kept: it validates the
    server of THIS run. Unreadable lines are skipped; a missing file is a fresh order."""
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return out
    wanted = set(items_for(spec))
    for line in lines:
        try:
            row = json.loads(line)
            key = (row.get("task"), int(row.get("replicate")))
            same = (row.get("arm") == arm and row.get("model") == spec["model"]
                    and float(row.get("temperature")) == float(temperature))
        except (ValueError, TypeError, AttributeError):
            continue
        if same and key in wanted and answered(row):
            out[key] = row
    return out


# --- Voltron: tablet RPC backends -----------------------------------------------------------------------------

def _wire_ip(host):
    ip = tf.wire_ip(host)
    if not ip:
        raise KeyError("no wire address for %s (no octet, or TABLET_WIRE_PREFIX unset)" % host)
    return ip


def port_open(ip, port=RPC_PORT, timeout=3):
    try:
        socket.create_connection((ip, port), timeout).close()
        return True
    except OSError:
        return False


def kill_backends(hosts, adb_sh, port=RPC_PORT):
    """Kill every rpc-server over adb root (`rpc-server` matches the old `ggml-rpc-server` name too), then prove
    each port closed. Returns hosts still listening."""
    for h in hosts:
        adb_sh(tf.node_ip(h, 5555, refresh=True)[0], "pkill -f rpc-server", timeout=20)   # wire first
    time.sleep(2)
    return [h for h in hosts if port_open(_wire_ip(h), port)]


def rpc_server_name(d):
    """The rpc-server binary in a build dir: `ggml-rpc-server` at 70adb1b, possibly `rpc-server` later."""
    for n in ("ggml-rpc-server", "rpc-server"):
        if os.path.exists(os.path.join(d, n)):
            return n
    raise RuntimeError("no rpc-server binary in %s" % d)


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# Floor to preserve beyond incoming weights, keeping room for the OS and logs. Prevents cache growth from filling the disk and breaking other writes (e.g., deploys).
CACHE_DISK_FLOOR_MIB = 1024
RPC_CACHE_DIR = "/data/data/com.termux/files/home/.cache/llama.cpp/rpc"


def cache_room(host, need_mib):
    """Make room for `need_mib` of cached weights on `host`: delete the OLDEST rpc-cache files until free disk
    >= need + CACHE_DISK_FLOOR_MIB. Returns (fits, free MiB after); unreadable = (False, None) - never cache blind."""
    want = int(need_mib) + CACHE_DISK_FLOOR_MIB
    cmd = ("D=%s; free() { df -k ~ | awk 'NR==2{print int($4/1024)}'; }; "
           "for f in $(ls -1tr $D 2>/dev/null); do [ $(free) -ge %d ] && break; rm -f \"$D/$f\"; done; "
           "echo FREE $(free)" % (RPC_CACHE_DIR, want))
    rc, out = tf._ssh(host, cmd, timeout=60)
    m = re.search(r"FREE (\d+)", out or "")
    if rc != 0 or not m:
        return False, None
    free = int(m.group(1))
    return free >= want, free


def start_backends(hosts, log, all_cores=False, src=None, tdir=TABLET_DIR, port=RPC_PORT, cache=False,
                   need_mib=None):
    """Stage the arm64 rpc-server and start one per host over ssh. Returns the hosts whose port answers.
    all_cores: -t every core (max 8, as warm); rpc-server's own default is half of them. src / tdir / port select
    a different build (#36's async-RPC build lives in its own tablet dir: the RPC protocol differs). cache: -c, the
    server keeps each weight tensor it receives under ~/.cache/llama.cpp/rpc, so the next load of the same weights
    sends a hash instead of the bytes."""
    up = []
    src = src or LLAMA_ARM64
    exe = rpc_server_name(src)
    threads = ' -t $(t=$(nproc); [ "$t" -gt 8 ] && t=8; echo $t)' if all_cores else ""
    files = (exe, "libomp.so")
    want = {f: _sha256(os.path.join(src, f)) for f in files}
    for h in hosts:
        # skip copy if checksum matches to avoid redundant 13 MB transfers per run
        rc, out = tf._ssh(h, "mkdir -p ~/%s && cd ~/%s && sha256sum %s 2>/dev/null" % (tdir, tdir, " ".join(files)),
                          timeout=30)
        have = {}
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) == 2:
                have[parts[1].lstrip("*")] = parts[0]
        for f in files:
            if have.get(f) == want[f]:
                continue
            rc, out = tf._scp(h, os.path.join(src, f), "%s/%s" % (tdir, f), timeout=300)
            if rc != 0:
                log("voltron: %s stage %s failed: %s" % (h, f, out[-160:]))
        flags = threads
        if cache:
            fits, free = cache_room(h, (need_mib or {}).get(h, 0))
            if fits:
                flags += " -c"
            else:
                log("bench: %s rpc-server WITHOUT the weight cache - %s MiB free disk cannot hold this stage's "
                    "weights + %d MiB floor" % (h, free, CACHE_DISK_FLOOR_MIB))
        tf._ssh(h, "cd ~/%s && chmod +x %s && setsid env LD_LIBRARY_PATH=. ./%s "
                   "-H 0.0.0.0 -p %d%s >rpc.log 2>&1 </dev/null &" % (tdir, exe, exe, port, flags), timeout=30)
    deadline = time.time() + 30
    while time.time() < deadline:
        up = [h for h in hosts if port_open(_wire_ip(h), port)]
        if len(up) == len(hosts):
            break
        time.sleep(2)
    return up


# --- warm: a resident llama-server on each tablet, the Fellowship answering in parallel -----------------------

def warm_base(host):
    if host == WARM_730:
        return "http://127.0.0.1:%d" % WARM_730_PORT
    return "http://%s:%d" % (_wire_ip(host), WARM_PORT)


def gt730_try_acquire(name):
    """Take the GT 730 for one order or warm node, never waiting. False = someone holds it (GT730_HOLDER names who)."""
    if not GT730_LOCK.acquire(blocking=False):
        return False
    GT730_HOLDER[0] = name
    return True


def gt730_release():
    GT730_HOLDER[0] = ""
    GT730_LOCK.release()


def _places_on_730(place, gpu_default="gt730"):
    """A placement dict (an llm spec, a bench run, a handoff writer) that puts work on a card puts it on the 730 unless
    it names only the 1060 or runs a CUDA build (sm_61 only - the 730 has no kernels there)."""
    if place.get("build") in CUDA_DIRS:
        return False
    return "gt730" in gpu_list(place.get("gpu", gpu_default))


def spec_uses_gt730(spec):
    """Does this order put work on rigby's GT 730? Conservative: Voltron and unknown rigby shapes count as yes."""
    kind = (spec.get("kind") or "").strip().lower()
    if kind == "bench":
        for r in spec.get("runs") or []:
            if not isinstance(r, dict):
                continue
            if uses_card(r) and _places_on_730(r):
                return True
            if isinstance(r.get("writer"), dict) and _places_on_730(dict(r["writer"], build=r.get("build"))):
                return True
        return False
    if kind == EMBED_KIND:
        return spec.get("fit_target") is not None and _places_on_730(spec)
    if kind in KINDS:
        s = normalize(spec)
        if "voltron" in s["arms"]:
            return True
        if s["arms"] == ["warm"]:
            return warm_wants_730(spec)
        on_card = spec.get("gpu_layers") is not None or spec.get("fit_target") is not None
        return on_card and _places_on_730(spec)
    return False


def warm_730_argv(model_path, ctx, reasoning, dev):
    head, env = card_launch(LLAMA_VULKAN, "llama-server", "gt730")      # Kepler shader flags ride in env
    argv = head + ["-m", model_path, "-c", str(int(ctx)), "-t", str(WARM_730_THREADS), "--host", "127.0.0.1",
                   "--port", str(WARM_730_PORT), "-ngl", "99", "-dev", dev, "-sm", "none"]
    if reasoning:
        argv += ["--reasoning", reasoning]
    return argv, env


def start_warm_730(model, ctx, reasoning, log):
    """Launch the 730's warm server (the tablet model's GGUF must also sit in rigby's models mount). Returns the Popen,
    or None with the reason logged."""
    path = os.path.join(MODELS, model + ".gguf")
    if not os.path.exists(path):
        log("warm: %s skipped - %s.gguf is not in rigby's models" % (WARM_730, model))
        return None
    argv, env = warm_730_argv(path, ctx, reasoning, vulkan_device(LLAMA_VULKAN, "gt730"))
    logf = open(os.path.join(tf.STATE_DIR, "warm_730.log"), "w", encoding="utf-8")
    return subprocess.Popen(argv, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)


def stop_warm_730(proc):
    """Stop the 730's warm server and prove its port closed. Never raises (cleanup path). Returns True if closed."""
    try:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:                                                # noqa: BLE001
                proc.kill()
                proc.wait(timeout=15)
    except Exception:                                                        # noqa: BLE001
        pass
    return not port_open("127.0.0.1", WARM_730_PORT, timeout=2)


def warm_healthy(host, timeout=5):
    try:
        with urllib.request.urlopen(warm_base(host) + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def warm_server_cmd(model, ctx, reasoning=None):
    """The tablet's own resident server: its tablet model (~/llm/<model>.gguf, staged by the payloads), every core up to
    8 (the payloads' THREADS rule), listening on the wire for Tolkien."""
    rea = (" --reasoning %s" % reasoning) if reasoning else ""
    return ("cd ~/%s && chmod +x llama-server && t=$(nproc) && [ \"$t\" -gt 8 ] && t=8; "
            "setsid env LD_LIBRARY_PATH=. ./llama-server -m %s.gguf -c %d -t $t --host 0.0.0.0 --port %d%s "
            ">warm.log 2>&1 </dev/null &" % (TABLET_DIR, model, int(ctx), WARM_PORT, rea))


def kill_warm(hosts, adb_sh):
    """Kill every resident llama-server over adb root (a setsid'd process is invisible to its own uid over ssh), then
    prove each port closed. Returns hosts still listening."""
    for h in hosts:
        adb_sh(tf.node_ip(h, 5555, refresh=True)[0], "pkill -f llama-server", timeout=20)   # wire first
    time.sleep(2)
    return [h for h in hosts if port_open(_wire_ip(h), WARM_PORT)]


def start_warm(hosts, model, ctx, reasoning, log, stop_event=None, tick=None, on_up=None, extra_launched=None):
    """Stage llama-server, start one resident server per host that HOLDS the model, wait for /health. Returns
    ({host: load_s} of the servers that answered, [hosts without the model]). load_s runs from that host's own launch to
    its first healthy poll: an upper bound on the load. `on_up(host)` fires the moment a server first answers - during
    staging too - so a node can serve while later nodes stage and one dead node never idles the rest (r75: two tablets
    whose USB links were down held nine healthy nodes idle for the full WARM_LOAD_S). `extra_launched` = {node:
    launch time} of servers started elsewhere (the GT 730's), polled the same way."""
    holds, missing, launched = [], [], dict(extra_launched or {})
    loads = {}

    def poll(timeout):
        for h in list(launched):
            if h not in loads and warm_healthy(h, timeout=timeout):
                loads[h] = round(time.time() - launched[h], 1)
                if on_up is not None:
                    on_up(h)

    for h in hosts:
        tf._ssh(h, "mkdir -p ~/%s" % TABLET_DIR, timeout=30)
        rc, out = tf._ssh(h, "test -f ~/%s/%s.gguf && echo HOLDS" % (TABLET_DIR, model), timeout=30)
        if "HOLDS" not in (out or ""):
            missing.append(h)
            continue
        for f in ("llama-server", "libomp.so"):
            rc, out = tf._scp(h, os.path.join(LLAMA_ARM64, f), "%s/%s" % (TABLET_DIR, f), timeout=300)
            if rc != 0:
                log("warm: %s stage %s failed: %s" % (h, f, out[-160:]))
        tf._ssh(h, warm_server_cmd(model, ctx, reasoning), timeout=30)
        launched[h] = time.time()        # staging is sequential: each server's load is timed from its OWN launch
        holds.append(h)
        poll(1)                          # short: an unreachable node must not slow the staging of the rest
    t0 = time.time()
    while time.time() - t0 < WARM_LOAD_S and len(loads) < len(launched):
        if stop_event is not None and stop_event.is_set():
            break
        poll(5)
        if tick is not None:
            tick("warm: %d/%d servers up" % (len(loads), len(launched)))
        if len(loads) < len(launched):
            time.sleep(2)
    return loads, missing


def run_warm(spec, mod, work, hosts, log, progress, stop_event=None, adb_sh=None, tick=None, streaming=None):
    """The warm arm: every tablet that holds the model serves it from a resident server; turns are dealt to whichever
    node is free, so N nodes answer N chats at once. Each node answers the CONTROL first (a node whose own server
    fails it takes no work). Rows carry the node and the time to first token. Servers are killed on EVERY route."""
    ctx = int(spec.get("ctx", getattr(mod, "CONTEXT", 2048)))
    temperature = float(spec.get("temperature", getattr(mod, "TEMPERATURE", 0.6)))
    meta = {"arm": "warm", "model": spec["model"], "ctx": ctx, "temperature": temperature, "nodes": [],
            "reasoning": spec.get("reasoning")}
    rows, lock = [], threading.Lock()
    ckpt = os.path.join(tf.STATE_DIR, "%s.checkpoint.jsonl" % spec["name"])
    started = []
    held_730, proc_730 = False, None
    try:
        if adb_sh is not None:
            stale = kill_warm(hosts, adb_sh)
            if stale:
                raise RuntimeError("warm port still held after adb kill on %s" % ", ".join(stale))
        if warm_wants_730(spec):                # launched first so it loads while the tablets stage
            held_730 = gt730_try_acquire(spec["name"])
            if not held_730:
                meta["gt730_busy"] = GT730_HOLDER[0] or "another order"
                log("warm: %s skipped - the GT 730 is busy with %s" % (WARM_730, meta["gt730_busy"]))
            else:
                proc_730 = start_warm_730(spec["model"], ctx, spec.get("reasoning"), log)
        ctl_name, control = control_of(mod)
        prior = resume_rows(ckpt, "warm", spec, temperature)
        kept = [dict(prior[k], resumed=True) for k in items_for(spec) if k in prior]
        rows.extend(kept)
        q = queue.Queue()
        for t, r in items_for(spec):
            if (t, r) not in prior:
                q.put((t, r))
        work_total = len(kept) + q.qsize()
        done = [len(kept)]
        inflight = [0]
        joined = {}                             # node -> its thread; nodes join as their servers come up

        def total():
            return work_total + len(joined)     # each joined node adds its control turn

        def answer(host, task, rep, tdef):
            row = {"arm": "warm", "task": task, "replicate": rep, "seed": rep, "model": spec["model"],
                   "temperature": temperature, "shape": SHAPE["warm"], "node": host, "nodes": 1,
                   "reasoning": meta["reasoning"]}

            def live(answer_text, thinking, n, elapsed):
                if streaming is not None:      # a class C turn can generate for 30+ min: the order's beat is its tokens
                    with lock:
                        streaming("warm", task, rep, n)
            try:
                text, wall, tm = complete(prompt_of(mod, tdef), rep, temperature, stop_event=stop_event, live=live,
                                          base=warm_base(host))
                row.update(tm, wall_s=wall, answer=text, ran=True)
                if tm.get("finish_reason") in CUT:
                    row.update(ran=False, ok=False, checks_passed=0, checks_total=n_checks(tdef),
                               error="%s after %ss" % (tm["finish_reason"], wall), **{tm["finish_reason"]: True})
                else:
                    ans, field = ((text, "content") if (text or "").strip() else (tm.get("thinking") or "", "thinking"))
                    row.update(grade(mod, work, tdef, ans), graded_field=field)
            except Exception as e:                                           # noqa: BLE001
                row.update(ran=False, ok=False, checks_passed=0, checks_total=n_checks(tdef),
                           error="%s: %s" % (type(e).__name__, str(e)[:200]))
            handed_back = task != ctl_name and not row.get("ran") and not row.get("stopped")
            with lock:
                rows.append(row)
                checkpoint(ckpt, row)
                if not handed_back:            # a handed-back turn is still owed: it must not count as done
                    done[0] += 1
                progress("warm", done[0], total(), row)
            return row, handed_back

        def node(host):
            if not answer(host, ctl_name, 1, control)[0].get("ok"):
                log("warm: %s failed its control - it takes no work" % host)
                return
            while not (stop_event is not None and stop_event.is_set()):
                try:
                    t, r = q.get_nowait()
                except queue.Empty:
                    with lock:
                        busy = inflight[0]
                    if not busy:               # leave only when nothing in flight could still be handed back
                        return
                    time.sleep(1)
                    continue
                with lock:
                    inflight[0] += 1
                try:
                    row, handed_back = answer(host, t, r, mod.TASKS[t])
                    if handed_back:
                        q.put((t, r))
                finally:
                    with lock:
                        inflight[0] -= 1
                if handed_back:             # a node that cannot answer hands the turn back (above) and leaves
                    log("warm: %s could not answer (%s) - turn requeued, node leaves" % (host, row.get("error")))
                    return
        def join(h):
            if h in joined:
                return
            th = threading.Thread(target=node, args=(h,), daemon=True)
            with lock:
                joined[h] = th
            th.start()

        extra = {WARM_730: time.time()} if proc_730 is not None else None
        loads, missing = start_warm(hosts, spec["model"], ctx, spec.get("reasoning"), log, stop_event, tick,
                                    on_up=join, extra_launched=extra)
        started = sorted((set(loads) - {WARM_730}) | (set(hosts) - set(missing)))
        for h in sorted(loads):                 # anything up by now that has not joined yet joins now
            join(h)
        if proc_730 is not None and WARM_730 not in loads:
            log("warm: %s did not come up (rc %s) - see state/warm_730.log" % (WARM_730, proc_730.poll()))
        meta.update(nodes=sorted(loads), load_s=loads, without_model=missing,
                    not_healthy=sorted(set(hosts) - set(missing) - set(loads)))
        log("warm: %s resident on %d node(s) %s; without the model: %s; not healthy: %s"
            % (spec["model"], len(loads), sorted(loads), missing or "-", meta["not_healthy"] or "-"))
        if not loads:
            raise RuntimeError("no warm server came up")
        for th in list(joined.values()):
            th.join()
        if stop_event is not None and stop_event.is_set():
            meta["stopped"] = True
        meta["unanswered"] = q.qsize()
    except Exception as e:                                                   # noqa: BLE001
        meta["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        if adb_sh is not None and started:
            try:
                meta["warm_left_listening"] = kill_warm(started, adb_sh)
            except Exception as e:                                           # noqa: BLE001
                meta["cleanup_error"] = "%s: %s" % (type(e).__name__, e)
        if held_730:
            if proc_730 is not None and not stop_warm_730(proc_730):
                meta["warm_730_left_listening"] = True
            gt730_release()
    return rows, meta


# --- bench: llama-bench on rigby (CPU, or CPU + GT 730) or on one tablet, and a tablet's USB link ---------------
#
#   {"kind": "bench", "name": "...", "runs": [
#       {"id": "rigby-7b", "where": "rigby", "model": "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "p": [1, 2, 4, 8, 512],
#        "n": [64], "d": [0, 2048], "r": 3},
#       {"id": "rigby-7b-gt730", "where": "rigby", "model": "...", "p": [512], "gpu_layers": 8, "op_offload": false},
#       {"id": "gandalf-1.5b", "where": "gandalf", "model": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", "p": [512]},
#       {"id": "gandalf-link", "where": "gandalf", "link_mb": 256}]}
#
# p / n / d are llama-bench's prompt sizes, tokens written and context depths; a prompt of k tokens at depth d is
# the cost of checking k drafted tokens there. A run is stopped between runs, never mid-measurement.

BENCH_LINK_MAX_MB = 512
BENCH_REPS = 3
# #36's async-RPC llama.cpp (upstream #18626: RPC devices report async + events, so llama.cpp overlaps pipeline
# stages). Its own mounts (bin/, vulkan/, arm64/), its own tablet dir and ports: its RPC protocol differs from the
# live build's, which Voltron and draft_host keep using.
LLAMA_NEXT = os.environ.get("TOLKIEN_LLAMA_NEXT", "/llama/next")
NEXT_TABLET_DIR = "llm-next"
NEXT_RPC_PORT = int(os.environ.get("TOLKIEN_NEXT_RPC_PORT", "50062"))
RIGBY_RPC_PORT = int(os.environ.get("TOLKIEN_RIGBY_RPC_PORT", "50061"))
STAGE_DEVS = ("card", "rigby")      # plus any wire tablet, and rigby2..rigby16 (see is_rigby)
RIGBY_MAX_STAGES = 16
# 15 because the host CPU consumes 1 of the 16 GGML_SCHED_MAX_BACKENDS slots.
MAX_STAGES = 15


def is_rigby(dev):
    """rigby or one of its extra stages (rigby2..rigby16): several local rpc-servers, allowing rigby to hold layers on both sides of a tablet's run."""
    if dev == "rigby":
        return True
    if not isinstance(dev, str) or not dev.startswith("rigby") or not dev[5:].isdigit():
        return False
    return 2 <= int(dev[5:]) <= RIGBY_MAX_STAGES


def is_local_stage(dev):
    """A stage on rigby itself (its CPU servers or the card), not on a tablet."""
    return dev == "card" or is_rigby(dev)


def rigby_port(dev):
    return RIGBY_RPC_PORT if dev == "rigby" else RIGBY_RPC_PORT + 1000 + int(dev[5:])


def rigby_threads(stages, total):
    """{rigby stage: threads}: a stage's own `threads`, else rigby's cores shared in proportion to the stages' layer
    shares - the stages run at once on one CPU, so each taking every core would oversubscribe it."""
    rs = [s for s in stages if is_rigby(s["dev"])]
    share = float(sum(s["ts"] for s in rs)) or 1.0
    return {s["dev"]: int(s.get("threads") or max(1, round(total * s["ts"] / share))) for s in rs}


def bench_tablets(spec):
    """The tablets a bench order touches (a run's `where`, or a stage of a pipelined run), in order of first use."""
    out = []
    for r in spec.get("runs") or []:
        if not isinstance(r, dict):
            continue
        names = [r.get("where")] + [s.get("dev") for s in r.get("stages") or [] if isinstance(s, dict)]
        for w in names:
            if w and not is_local_stage(w) and w not in out:
                out.append(w)
    return out


def _stage_defects(tag, r):
    d = []
    st = r["stages"]
    if r.get("where") != "rigby":
        d.append("%s: a pipelined run (stages) runs from rigby - where must be rigby" % tag)
    if r.get("build") != "next":
        d.append("%s: stages need build \"next\" (the async-RPC llama.cpp; the live build cannot overlap stages)" % tag)
    if not isinstance(st, list) or len(st) < 2:
        return d + ["%s: stages must list at least two devices" % tag]
    if len(st) > MAX_STAGES:
        d.append("%s: %d stages - ggml schedules at most %d backends and the host CPU is one, so at most %d"
                 % (tag, len(st), MAX_STAGES + 1, MAX_STAGES))
    seen = set()
    for j, s in enumerate(st):
        dev = s.get("dev") if isinstance(s, dict) else None
        if not is_local_stage(dev) and dev not in tf.WIRE_OCTET:
            d.append("%s: stages[%d].dev must be card, rigby, rigby2..rigby%d or a wire tablet"
                     % (tag, j, RIGBY_MAX_STAGES))
        th = s.get("threads") if isinstance(s, dict) else None
        if th is not None and (not is_rigby(dev) or isinstance(th, bool) or not isinstance(th, int) or th < 1):
            d.append("%s: stages[%d].threads is a positive integer, on a rigby stage only" % (tag, j))
        if dev in seen:
            d.append("%s: stages[%d].dev %s is listed twice" % (tag, j, dev))
        seen.add(dev)
        ts = s.get("ts") if isinstance(s, dict) else None
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or ts <= 0:
            d.append("%s: stages[%d].ts must be a positive number (that device's share of the layers)" % (tag, j))
    if "handoff" in r:
        one = lambda k: isinstance(r.get(k), list) and len(r[k]) == 1 and isinstance(r[k][0], int) \
            and not isinstance(r[k][0], bool) and r[k][0] > 0
        if r["handoff"] is not True or not one("p") or not one("n") or r.get("d"):
            d.append("%s: handoff is true with one prompt size p and one write length n (no d) - read on the "
                     "stages, write on rigby alone" % tag)
    if "writer" in r:
        w = r["writer"]
        if r.get("handoff") is not True or not isinstance(w, dict) or set(w) - {"gpu_layers", "override_tensor",
                                                                               "op_offload", "gpu"}:
            d.append("%s: writer is a handoff run's GPU placement {gpu_layers, override_tensor, op_offload, gpu}"
                     % tag)
        elif w.get("gpu", "gt730") not in GPU_NAMES:
            d.append("%s: writer.gpu must be one of %s" % (tag, ", ".join(GPU_NAMES)))
        else:
            d += ["%s: writer.%s" % (tag, x) for x in _card_defects(w.get("gpu_layers"), w.get("override_tensor"),
                                                                     w.get("op_offload"))]
            if w.get("gpu_layers") is None:
                d.append("%s: writer needs gpu_layers" % tag)
    if any(k in r for k in ("gpu_layers", "override_tensor", "op_offload")):
        d.append("%s: a pipelined run puts every layer on a stage; gpu_layers / override_tensor / op_offload do not "
                 "combine (any -ot turns llama.cpp's pipeline parallelism off)" % tag)
    return d


_GGUF_SCALAR = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}   # value type -> bytes


GGML_TYPE_NAME = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0", 7: "Q5_1", 8: "Q8_0", 9: "Q8_1", 10: "Q2_K",
                  11: "Q3_K", 12: "Q4_K", 13: "Q5_K", 14: "Q6_K", 15: "Q8_K", 16: "IQ2_XXS", 17: "IQ2_XS",
                  18: "IQ3_XXS", 19: "IQ1_S", 20: "IQ4_NL", 21: "IQ3_S", 22: "IQ2_S", 23: "IQ4_XS", 24: "I8",
                  25: "I16", 26: "I32", 27: "I64", 28: "F64", 29: "IQ1_M", 30: "BF16"}


def gguf_tensor_bytes(path):
    """(block_count, {tensor name: bytes}) - see gguf_tensor_table."""
    n, tt = gguf_tensor_table(path)
    return n, {k: v[1] for k, v in tt.items()}


def gguf_tensor_table(path):
    """(block_count, {tensor name: (ggml type name, bytes)}) from a GGUF header alone. A tensor's bytes are the
    distance to the next tensor's data offset (the last runs to the end of the file), so no quant-type size table is
    needed and padding is counted the way the loader allocates it."""
    blocks, data, infos = _gguf_read(path)
    out = {}
    for i, (off, name, typ, _dims) in enumerate(infos):
        end = infos[i + 1][0] if i + 1 < len(infos) else data
        out[name] = (GGML_TYPE_NAME.get(typ, "T%d" % typ), end - off)
    return blocks, out


def _gguf_read(path):
    """(block_count, data section bytes, [(offset, name, ggml type, dims)] sorted by offset) - the one GGUF header
    reader behind gguf_tensor_table and kv_bytes_per_token."""
    import struct

    def rd(f, fmt):
        fmt = "<" + fmt                             # packed little-endian: a native calcsize pads "IQQ" to 24
        return struct.unpack(fmt, f.read(struct.calcsize(fmt)))

    def rstr(f):
        (n,) = rd(f, "Q")
        return f.read(n).decode("utf-8", "replace")

    def skip(f, t):
        if t == 8:
            rstr(f)
        elif t == 9:
            et, n = rd(f, "IQ")
            for _ in range(n):
                skip(f, et)
        else:
            f.read(_GGUF_SCALAR[t])

    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("%s is not a GGUF file" % path)
        _ver, n_t, n_kv = rd(f, "IQQ")
        align, blocks, arch = 32, None, None
        for _ in range(n_kv):
            key = rstr(f)
            (t,) = rd(f, "I")
            if key == "general.architecture" and t == 8:
                arch = rstr(f)
            elif key == "general.alignment" and t == 4:
                (align,) = rd(f, "I")
            elif key.endswith(".block_count") and t in (4, 10):
                (blocks,) = rd(f, "I" if t == 4 else "Q")
                if arch and not key.startswith(arch + "."):
                    blocks = None
            else:
                skip(f, t)
        infos = []
        for _ in range(n_t):
            name = rstr(f)
            (nd,) = rd(f, "I")
            dims = rd(f, "Q" * nd)
            typ, off = rd(f, "IQ")
            infos.append((off, name, typ, dims))
        start = f.tell()
        start += (-start) % align
        size = os.fstat(f.fileno()).st_size
    if blocks is None:
        raise ValueError("%s states no <arch>.block_count" % path)
    infos.sort()
    return blocks, size - start, infos


KV_CACHE_BYTES = 2           # llama-bench's default K and V cache type, f16


def kv_bytes_per_token(model_path):
    """(block_count, {layer: K+V cache bytes per token}) for the layers that keep a KV cache - those with attn_k; a
    hybrid's linear layers keep a fixed recurrent state instead. Width = the K and V projections' output dims.
    Raises when no layer has attn_k: a cache that cannot be sized must not be read as an empty one."""
    blocks, _data, infos = _gguf_read(model_path)
    dims = {name: d for _off, name, _typ, d in infos}
    out = {}
    for il in range(blocks):
        k = dims.get("blk.%d.attn_k.weight" % il)
        if k:
            v = dims.get("blk.%d.attn_v.weight" % il, k)
            out[il] = (k[-1] + v[-1]) * KV_CACHE_BYTES
    if not out:
        raise ValueError("%s: no layer has attn_k - cannot size its KV cache" % model_path)
    return blocks, out


def unit_kv_mib(model_path, n_ctx):
    """[MiB of KV cache per unit] at n_ctx, in stage_units' unit order (the output unit holds none)."""
    blocks, per = kv_bytes_per_token(model_path)
    return [per.get(u, 0) * n_ctx / 1048576.0 for u in range(blocks)] + [0.0]


def bench_n_ctx(run):
    """The context llama-bench makes for a run's largest test: prompt + generated + depth, padded to 256."""
    n = max(run.get("p") or [0]) + max(run.get("n") or [0]) + max(run.get("d") or [0])
    return n + (-n) % 256


def unit_mix(model_path):
    """(n_layer, [{ggml type: MiB} per unit]) - the n_layer blocks then the output unit (output.weight, or the token
    table when the model ties them, plus output_norm), the same units stage_units splits."""
    n_layer, tt = gguf_tensor_table(model_path)
    units = [dict() for _ in range(n_layer + 1)]

    def add(u, name):
        if name in tt:
            t, b = tt[name]
            units[u][t] = units[u].get(t, 0.0) + b / 1048576.0
    for name in tt:
        if name.startswith("blk."):
            il = int(name.split(".")[1])
            if il < n_layer:
                add(il, name)
    add(n_layer, "output.weight" if "output.weight" in tt else "token_embd.weight")
    add(n_layer, "output_norm.weight")
    return n_layer, units


def stage_units(ts, n_layer):
    """[[unit, ...] per stage]: llama.cpp's own layer split (llama-model.cpp, 23b0202) with every layer offloaded -
    n_layer + 1 units (the last is the output layer), unit il goes to the first stage whose cumulative normalized
    share exceeds il / (n_layer + 1). Checked against the assignments llama.cpp logged in rounds 3 and 7."""
    total = float(sum(ts))
    cum, acc = [], 0.0
    for t in ts:
        acc += t
        cum.append(acc / total)
    out = [[] for _ in ts]
    units = n_layer + 1
    for il in range(units):
        x = float(il) / units
        dev = next((i for i, c in enumerate(cum) if c > x), len(ts) - 1)
        out[dev].append(il)
    return out


def stage_weight_mib(model_path, stages):
    """{stage dev: MiB of weights llama.cpp will place on it}. The output unit carries output.weight (or the token
    table when the model ties them) and output_norm; the token table itself stays on the host."""
    n_layer, mix = unit_mix(model_path)
    units = stage_units([s["ts"] for s in stages], n_layer)
    return {s["dev"]: sum(sum(mix[u].values()) for u in us) for s, us in zip(stages, units)}


# A last stage holding ONLY the output unit aborts llama-bench the first time an output is computed ("Remote RPC
# server crashed", no layer on that stage ever computes): round 13 (output alone on a tablet) and rounds 19b-21 (alone
# on rigby, 15 stages; 32 input copies and a one-batch prompt changed nothing).
OUTPUT_ALONE = "the last stage holds only the output layer - llama.cpp aborts on the first output (rounds 13, 19b-21)"


def stage_layout_defects(model_path, stages):
    units = stage_units([s["ts"] for s in stages], unit_mix(model_path)[0])
    return [OUTPUT_ALONE] if len(units[-1]) < 2 else []


def stage_kv_mib(model_path, stages, n_ctx):
    """{stage dev: MiB of KV cache llama.cpp will place on it at n_ctx} - grows with the prompt, unlike the weights."""
    kv = unit_kv_mib(model_path, n_ctx)
    units = stage_units([s["ts"] for s in stages], len(kv) - 1)
    return {s["dev"]: sum(kv[u] for u in us) for s, us in zip(stages, units)}


# Extra memory reserved per stage beyond weights and KV cache.
# Covers small runtime buffers and Android LMKD margins; 300 MiB is a safe
# minimum to prevent the OS from killing the process during inference.
STAGE_RESERVE_MIB = 300
# Scaled to 16K context: 8% of weights + 80 MiB covers observed read-time growth (up to +173 MiB).
# At 4K context, this allowance still admits all stable layouts.
STAGE_GROWTH_FRAC = 0.08
STAGE_GROWTH_MIB = 80.0
STAGE_GROWTH_CTX = 16384


def stage_need_mib(weights_mib, kv_mib, n_ctx):
    """MiB a tablet stage will take at n_ctx: its weights + KV cache + the measured growth allowance. The stage
    preflight and the planner both size stages with this one function."""
    scale = n_ctx / float(STAGE_GROWTH_CTX)
    return weights_mib + kv_mib + scale * (STAGE_GROWTH_FRAC * weights_mib + STAGE_GROWTH_MIB)


STAGE_MEM_FRESH_S = 120
STAGE_MEM_WAIT_S = 20        # the telemetry logger samples a tablet every 2 s; ten periods, then "could not tell"


def tablet_mem_avail(nodes, since=None):
    """{node: MemAvailable MiB} from the telemetry store's newest sample per node within STAGE_MEM_FRESH_S. A node
    with no fresh sample is absent: could not tell. (rpc-server reports a tablet's TOTAL RAM as free - every class B
    tablet says 1801 MiB with ~1.0 GB actually available - so its number is not used.)"""
    db = _telemetry_db()
    if db is None:
        return {}
    try:
        floor = max(time.time() - STAGE_MEM_FRESH_S, since or 0)
        rows = db.execute("SELECT node, mem_avail_mb FROM samples WHERE ts >= ? AND mem_avail_mb IS NOT NULL "
                          "ORDER BY ts", (floor,)).fetchall()
    except Exception:                                                        # noqa: BLE001
        return {}
    finally:
        db.close()
    out = {}
    for n, v in rows:
        if n in nodes:
            out[n] = v
    return out


# A tablet under this charge is not given a stage. The floor is the battery governor's own band low (Tablet-
# Instrumentation-Maintenance § Battery charge governance); it stands in until the telemetry store holds a loaded
# discharge curve, when it becomes drain rate x run length + reserve. A 500 mA port cannot keep up with a busy tablet.
BATTERY_FLOOR_PCT = 50


def tablet_battery(nodes):
    """{node: (percent, status)} from the telemetry store's newest sample per node within STAGE_MEM_FRESH_S that
    carries a battery reading. A node with none (no fresh sample, a store from before the column) is absent: could
    not tell."""
    db = _telemetry_db()
    if db is None:
        return {}
    try:
        rows = db.execute("SELECT node, battery_pct, charging FROM samples WHERE ts >= ? AND battery_pct IS NOT NULL "
                          "ORDER BY ts", (time.time() - STAGE_MEM_FRESH_S,)).fetchall()
    except Exception:                                                        # noqa: BLE001
        return {}
    finally:
        db.close()
    return {n: (p, c) for n, p, c in rows if n in nodes}


def stage_battery_defects(tablets, have):
    """Refusals (empty = every tablet stage can start): each tablet needs a fresh reading at or above
    BATTERY_FLOOR_PCT. rigby and the card have no battery and are skipped."""
    out = []
    for dev in sorted(tablets):
        if is_local_stage(dev):
            continue
        got = have.get(dev)
        if got is None:
            out.append("%s: could not read its battery (no fresh telemetry) - not starting a run on it blind" % dev)
        elif got[0] < BATTERY_FLOOR_PCT:
            out.append("%s: battery %d%% (%s) < the %d%% floor - a busy tablet on a 500 mA port drains faster than it "
                       "charges" % (dev, got[0], got[1] or "status unknown", BATTERY_FLOOR_PCT))
    return out


def stage_memory_defects(need_mib, avail_mib):
    """Refusals (empty = every tablet stage fits): need (stage_need_mib) + STAGE_RESERVE_MIB must fit in real
    MemAvailable. rigby and the card are not checked here."""
    out = []
    for dev, need in sorted(need_mib.items()):
        if is_local_stage(dev):
            continue
        have = avail_mib.get(dev)
        if have is None:
            out.append("%s: could not read its free memory (no fresh telemetry) - not loading %.0f MiB onto it blind"
                       % (dev, need))
        elif need + STAGE_RESERVE_MIB > have:
            out.append("%s: %.0f MiB of weights, KV cache and growth + %d MiB reserve > %.0f MiB available" % (
                dev, need, STAGE_RESERVE_MIB, have))
    return out


def _int_list(v):
    return (isinstance(v, list) and bool(v)
            and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v))


def bench_defects(spec):
    d = []
    if not spec.get("name"):
        d.append("missing required field: name")
    runs = spec.get("runs")
    if not isinstance(runs, list) or not runs:
        return d + ["bench needs runs: a non-empty list"]
    ids = set()
    for i, r in enumerate(runs):
        tag = "runs[%d]" % i
        if not isinstance(r, dict):
            d.append("%s must be an object" % tag)
            continue
        rid = r.get("id")
        if not isinstance(rid, str) or not rid or rid in ids:
            d.append("%s: id must be a unique non-empty string" % tag)
        ids.add(rid)
        where = r.get("where")
        if where != "rigby" and where not in tf.WIRE_OCTET:
            d.append("%s: where must be rigby or a wire tablet (%s)" % (tag, ", ".join(sorted(tf.WIRE_OCTET))))
        if "link_mb" in r:
            lm = r["link_mb"]
            if where == "rigby":
                d.append("%s: link_mb measures a tablet's USB link - where must be a tablet" % tag)
            if isinstance(lm, bool) or not isinstance(lm, int) or not 1 <= lm <= BENCH_LINK_MAX_MB:
                d.append("%s: link_mb must be an integer 1..%d" % (tag, BENCH_LINK_MAX_MB))
            if any(k in r for k in ("model", "p", "n", "d", "gpu_layers", "override_tensor", "op_offload")):
                d.append("%s: a link run takes no model or bench arguments" % tag)
            continue
        m = r.get("model")
        if not isinstance(m, str) or not m.endswith(".gguf") or "/" in m or "\\" in m:
            d.append("%s: model must be a .gguf file name in the models mount (no path)" % tag)
        if r.get("build") not in BUILDS:
            d.append("%s: build must be \"next\", \"cuda\", \"hotstore\" or unset (the live build)" % tag)
        if r.get("build") in CUDA_DIRS and (where != "rigby" or r.get("gpu") != "gtx1060" or not uses_card(r)
                                            or "stages" in r):
            d.append("%s: build \"%s\" is the GTX 1060's (sm_61 only): a rigby card placement with gpu gtx1060"
                     % (tag, r["build"]))
        if r.get("build") == "next" and where != "rigby" and "stages" not in r:
            d.append("%s: a tablet's own llama-bench runs the live build only - build \"next\" would be ignored" % tag)
        ub = r.get("ub")
        if ub is not None and (isinstance(ub, bool) or not isinstance(ub, int) or ub < 1):
            d.append("%s: ub must be a positive integer (llama-bench -ub, the chunk size)" % tag)
        if "stages" in r:
            d += _stage_defects(tag, r)
        if "writer" in r and "stages" not in r:
            d.append("%s: writer is a handoff run's GT 730 placement - it needs stages and handoff" % tag)
        if "trace" in r and (not isinstance(r["trace"], bool) or "stages" not in r):
            d.append("%s: trace must be true or false and needs stages (it traces the RPC hand-offs)" % tag)
        if "graph_reuse" in r and (not isinstance(r["graph_reuse"], bool) or "stages" not in r):
            d.append("%s: graph_reuse must be true or false and needs stages" % tag)
        if "kld" in r or "kld_base" in r:
            kt, ch = r.get("kld_text"), r.get("chunks")
            if (r.get("kld") is True) == (r.get("kld_base") is True) or where != "rigby" \
                    or (r.get("kld_base") and "stages" in r):
                d.append("%s: exactly one of kld / kld_base true, on rigby (kld_base on rigby alone)" % tag)
            if not isinstance(kt, str) or not kt or "/" in kt or "\\" in kt \
                    or isinstance(ch, bool) or not isinstance(ch, int) or ch < 1:
                d.append("%s: kld needs kld_text (a file name in payloads/kld) and chunks (a positive integer)" % tag)
        if "imatrix" in r:
            it, ch = r.get("imatrix_text"), r.get("chunks")
            if r.get("imatrix") is not True or where != "rigby" or "stages" in r or "kld" in r or "kld_base" in r:
                d.append("%s: imatrix must be true, on rigby alone (no stages, no kld)" % tag)
            if not isinstance(it, str) or not it or "/" in it or "\\" in it \
                    or isinstance(ch, bool) or not isinstance(ch, int) or ch < 1:
                d.append("%s: imatrix needs imatrix_text (a file name in payloads/imatrix) and chunks (a positive "
                         "integer)" % tag)
        for k in ("p", "n", "d"):
            if k in r and not _int_list(r[k]):
                d.append("%s: %s must be a non-empty list of non-negative integers" % (tag, k))
        tests = [x for k in ("p", "n") for x in (r.get(k) if _int_list(r.get(k)) else [])]
        if not any(x > 0 for x in tests) and not (r.get("kld") or r.get("kld_base") or r.get("imatrix")):
            d.append("%s: needs a test - p (prompt sizes) or n (tokens written) with a value > 0" % tag)
        for k, default in (("r", BENCH_REPS), ("threads", 1)):
            v = r.get(k, default)
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                d.append("%s: %s must be a positive integer" % (tag, k))
        gl, ot, oo = r.get("gpu_layers"), r.get("override_tensor"), r.get("op_offload")
        if (gl is not None or ot is not None or oo is not None) and where != "rigby":
            d.append("%s: gpu_layers / override_tensor / op_offload place layers on rigby's GT 730 - rigby runs only"
                     % tag)
        d += ["%s: %s" % (tag, x) for x in _card_defects(gl, ot, oo)]
        ft = r.get("fit_target")
        if "gpu" in r and (r["gpu"] not in GPU_NAMES or (gl is None and ft is None)):
            d.append("%s: gpu names the card a gpu_layers or fit_target placement goes to: one of %s"
                     % (tag, ", ".join(GPU_NAMES)))
        if ft is not None and (isinstance(ft, bool) or not isinstance(ft, int) or ft < 0 or gl is not None
                               or ot is not None or "stages" in r or where != "rigby"):
            d.append("%s: fit_target is llama.cpp's fit margin in MiB (an integer >= 0) on a rigby run, instead of "
                     "gpu_layers / override_tensor - a hand placement switches the fitter off" % tag)
        for k in ("cache_type_k", "cache_type_v"):
            if k in r:
                if r[k] not in KV_TYPES:
                    d.append("%s: %s must be one of %s (llama-bench -ctk / -ctv)" % (tag, k, ", ".join(KV_TYPES)))
                if "stages" in r or r.get("kld") or r.get("kld_base") or where != "rigby":
                    d.append("%s: %s is a plain rigby llama-bench knob (a handoff writer must restore the reader's "
                             "cache type, so stages / handoff / kld runs keep f16)" % (tag, k))
    return d


KV_TYPES = ("f16", "q8_0", "q4_0")


def _card_defects(gl, ot, oo):
    """Defects of a GT 730 placement (gpu_layers, override_tensor, op_offload); [] when absent or sound."""
    d = []
    if gl is not None and (isinstance(gl, bool) or not isinstance(gl, int) or gl < 0):
        d.append("gpu_layers must be a non-negative integer")
    if ot is not None and (not isinstance(ot, list) or not ot or not all(
            isinstance(x, str) and x.count("=") == 1 and all(x.split("=")) for x in ot)):
        d.append("override_tensor must be a non-empty list of '<tensor regex>=<buffer>' strings")
    if ot is not None and gl is None:
        d.append("override_tensor needs gpu_layers")
    if oo is not None and (not isinstance(oo, bool) or gl is None):
        d.append("op_offload must be true or false and needs gpu_layers")
    return d


def bench_expected(run):
    """How many results llama-bench returns for a run: one per test (p > 0 or n > 0) at each depth."""
    if run.get("handoff") or run.get("kld") or run.get("kld_base") or run.get("imatrix"):
        return 1
    tests = sum(1 for k in ("p", "n") for x in run.get(k) or [] if x > 0)
    return tests * len(run.get("d") or [0])


def _csv(v):
    return ",".join(str(x) for x in v)


def next_dirs():
    return {k: os.path.join(LLAMA_NEXT, k) for k in ("bin", "vulkan", "arm64")}


def uses_card(run):
    return (run.get("gpu_layers") is not None or run.get("fit_target") is not None
            or any(s.get("dev") == "card" for s in run.get("stages") or []))


LLAMA_CUDA = os.environ.get("TOLKIEN_LLAMA_CUDA", "/llama/cuda")
# llama.cpp PR #26563 (closed upstream, unmerged): the hottest experts copied to GPU slots and run there, the cold ones
# on the CPU - no expert crosses the x1 riser per token. Its own CUDA 11.4 build; order field expert_hot
LLAMA_HOTSTORE = os.environ.get("TOLKIEN_LLAMA_HOTSTORE", "/llama/hotstore")
CUDA_DIRS = {"cuda": LLAMA_CUDA, "hotstore": LLAMA_HOTSTORE}
BUILDS = (None, "next") + tuple(CUDA_DIRS)


def card_dir(build):
    """The GPU build a card placement runs: "cuda" = the CUDA 11.4 build for the GTX 1060 (sm_61 only; driver 470 caps
    CUDA at 11.4), "next" = the async-RPC Vulkan build, unset = the live Vulkan build."""
    return CUDA_DIRS.get(build) or (next_dirs()["vulkan"] if build == "next" else LLAMA_VULKAN)


def bench_head(run):
    """(argv head, env) for rigby's llama-bench: the CPU build, or a GPU build under the host loader when anything
    goes to a card; build "next" = the async-RPC build's mounts, "cuda" = the CUDA build."""
    nxt = run.get("build") == "next"
    cpu_dir = next_dirs()["bin"] if nxt else LLAMA_BIN
    if not uses_card(run):
        return [os.path.join(cpu_dir, "llama-bench")], dict(os.environ, LD_LIBRARY_PATH=cpu_dir)
    return card_launch(card_dir(run.get("build")), "llama-bench", run.get("gpu", "gt730"))


def bench_vk_dir(run):
    return card_dir(run.get("build"))


NVIDIA_GPU_INFO = "/proc/driver/nvidia/gpus/*/information"
_GPU_INFO = re.compile(r"^Model:\s+(.+?)\s*\n(?:.*\n)*?GPU UUID:\s+(GPU-[0-9a-f-]+)", re.M)


def gpu_inventory(text=None):
    """[(model, uuid)] from the driver's own per-GPU files (no nvidia-smi in tablet-foundry): `Model:` then
    `GPU UUID:` in each /proc/driver/nvidia/gpus/<bus>/information."""
    if text is None:
        text = "".join(open(f, encoding="utf-8", errors="replace").read() for f in sorted(glob.glob(NVIDIA_GPU_INFO)))
    return _GPU_INFO.findall(text)


def cuda_visible(gpu, info_text=None):
    """CUDA_VISIBLE_DEVICES for one card, by NAME: its UUID from the driver's GPU files. The CUDA build carries no
    kernels for the GT 730 (Kepler), so the runtime is never shown it. Raises unless exactly one card has the name."""
    inv = gpu_inventory(info_text)
    want = GPU_NAMES[gpu]
    hit = [uuid for model, uuid in inv if want in model]
    if len(hit) != 1:
        raise RuntimeError("expected one GPU named %r, found %d in %s" % (want, len(hit), inv))
    return hit[0]


# Maps logical names to exact strings in `--list-devices`; required to pin a single device and avoid splitting across GPUs.
GPU_NAMES = {"gt730": "GT 730", "gtx1060": "GTX 1060"}


def pick_vulkan(names, gpu):
    """The Vulkan device (`Vulkan1`) whose --list-devices line names `gpu`. [(name, rest of line)]. Raises unless
    exactly one matches."""
    want = GPU_NAMES[gpu]
    hit = [n for n, rest in names if n.startswith(("Vulkan", "CUDA")) and want in rest]
    if len(hit) != 1:
        raise RuntimeError("expected one GPU device named %r (%s), found: %s" % (want, gpu, names))
    return hit[0]


def vulkan_device(vk_dir, gpu):
    head, env = card_launch(vk_dir, "llama-server", gpu)
    return pick_vulkan(bench_devices(head, env, [])[0], gpu)


def card_launch(vk_dir, binary, gpu="gt730"):
    """(argv head, env) for a Vulkan-build binary, run under the host loader. The Kepler shader flags only for the
    GT 730 (they would also constrain the 1060's shaders)."""
    env = dict(os.environ)
    env.pop("LD_LIBRARY_PATH", None)       # the host loader's --library-path decides; the image's libs must not mix in
    if gpu == "gt730":
        env.update(KEPLER_VK_ENV)
    if vk_dir in CUDA_DIRS.values():
        env["CUDA_VISIBLE_DEVICES"] = cuda_visible(gpu)
    return [os.path.join(HOSTLIB, "ld-linux-x86-64.so.2"), "--library-path", "%s:%s" % (vk_dir, HOSTLIB),
            os.path.join(vk_dir, binary)], env


def card_args(place, dev=None):
    """-dev / -ngl / -ot / --no-op-offload for a GPU placement {gpu_layers, override_tensor?, op_offload?}."""
    argv = ["-dev", dev, "-sm", "none"] if dev else []      # llama.cpp docs/multi-gpu.md: one card = split-mode none
    argv += ["-ngl", str(place["gpu_layers"])]
    for ot in place.get("override_tensor") or ():
        argv += ["-ot", ot]
    if place.get("op_offload") is False:
        argv += ["--no-op-offload"]
    return argv


def bench_live(live, rid, want):
    """(beat, on_line) for a rigby bench run: the deck line carries the elapsed time AND the last finished test's
    speed with how many of the run's `want` tests are done (llama-bench -o jsonl prints each result as it ends)."""
    seen = {"n": 0, "last": ""}

    def on_line(line):
        try:
            x = json.loads(line)
        except ValueError:
            return
        if not isinstance(x, dict) or not (x.get("avg_ts") or 0) > 0:
            return
        seen["n"] += 1
        test = ("pp%d" % x["n_prompt"]) if x.get("n_prompt") else ("tg%d" % (x.get("n_gen") or 0))
        depth = (" @d%d" % x["n_depth"]) if x.get("n_depth") else ""
        seen["last"] = "%s%s %.1f tok/s (%d/%d done)" % (test, depth, x["avg_ts"], seen["n"], want)
        live(rid, seen["last"])

    def beat(s):
        t = "%dm%02ds" % (s // 60, s % 60)
        live(rid, "%s | %s" % (t, seen["last"]) if seen["last"] else t)
    return beat, on_line


def bench_argv(run, model_path, vk_dev=None):
    """rigby's llama-bench: the CPU build, or the Vulkan build under the host loader when layers go to a GPU;
    vk_dev = that GPU's Vulkan device (vulkan_device), pinned with -dev."""
    gl = run.get("gpu_layers")
    head, _env = bench_head(run)
    argv = head + ["-m", model_path, "-o", "jsonl", "-r", str(run.get("r", BENCH_REPS)),
                   "-t", str(run.get("threads", THREADS)),
                   "-p", _csv(run.get("p") or [0]), "-n", _csv(run.get("n") or [0])]
    if run.get("d"):
        argv += ["-d", _csv(run["d"])]
    if run.get("ub"):
        argv += ["-ub", str(run["ub"])]
    if run.get("fit_target") is not None:
        # llama.cpp's own fitter places layers and MoE experts in the card's free memory with this margin, sized for
        # the run's largest context (compute buffers included) - no hand -ngl / -ot, which would switch it off
        argv += ["-dev", vk_dev, "-sm", "none", "-fitt", str(run["fit_target"]), "-fitc", str(bench_n_ctx(run))]
    if gl is not None:
        if vk_dev:
            argv += ["-dev", vk_dev, "-sm", "none"]
        argv += ["-ngl", str(gl)]
        if run.get("override_tensor"):
            argv += ["-ot", ";".join(run["override_tensor"])]
        if run.get("op_offload") is False:
            argv += ["-nopo", "1"]
    if run.get("cache_type_k"):
        argv += ["-ctk", run["cache_type_k"]]
    if run.get("cache_type_v"):
        argv += ["-ctv", run["cache_type_v"]]
    return argv


class BenchStopped(Exception):
    """A stop slip cut a bench run mid-measurement."""


BENCH_BEAT_S = 20.0


def _read_lines(stream, on_line):
    """The stream's whole text, handing each line to on_line as it arrives (a failing callback never costs the text)."""
    lines = []
    for line in stream:
        lines.append(line)
        try:
            on_line(line)
        except Exception:                                                    # noqa: BLE001
            pass
    return "".join(lines)


def run_stoppable(argv, env, stop_event=None, beat=None, on_line=None):
    """(rc, stdout, stderr) of a llama-bench run that a stop slip can cut: the order's stop_event, polled every second,
    terminates it (then kills it). A bench run is minutes long; waiting for it to end ignores the customer's stop.
    beat(elapsed_s) is called every BENCH_BEAT_S: llama-bench prints nothing until it ends, so without it the deck
    reads a working run as "at 0 for N min". on_line(text) sees each stdout line as it arrives - with -o jsonl that
    is one finished test's result."""
    p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    got = {}
    reads = {"out": (lambda s: _read_lines(s, on_line)) if on_line else (lambda s: s.read()), "err": lambda s: s.read()}
    pumps = [threading.Thread(target=lambda k=k, s=s: got.__setitem__(k, reads[k](s)), daemon=True)
             for k, s in (("out", p.stdout), ("err", p.stderr))]
    for t in pumps:
        t.start()
    t0 = last = time.time()
    while p.poll() is None:
        if beat is not None and time.time() - last >= BENCH_BEAT_S:
            last = time.time()
            try:
                beat(time.time() - t0)
            except Exception:                                                # noqa: BLE001
                pass
        if stop_event is not None and stop_event.is_set():
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            raise BenchStopped("stopped by a slip mid-run")
        time.sleep(1)
    for t in pumps:
        t.join(5)
    return p.returncode, got.get("out", ""), got.get("err", "")


_DEV_LINE = re.compile(r"^\s+((?:Vulkan|CUDA|RPC)\d*(?:\[[^\]]*\])?):\s*(.*)$", re.M)


def bench_devices(head, env, endpoints):
    """Get device names and details from llama-bench for the specified RPC endpoints.

    Note: The -rpc flag must be passed before --list-devices. llama-bench parses flags
    in order, and if -rpc comes later, the RPC devices will not be included in the output."""
    p = subprocess.run(head + (["-rpc", ",".join(endpoints)] if endpoints else []) + ["--list-devices"],
                       env=env, capture_output=True, text=True, timeout=120)
    text = (p.stdout or "") + (p.stderr or "")
    return _DEV_LINE.findall(text), text[-400:]


def map_stage_devices(stages, names, endpoints):
    """stage dev -> llama.cpp device name. `names` = [(name, rest of its --list-devices line)]. The card is the
    Vulkan device named GT 730 (pick_vulkan); each RPC stage is the RPC device whose line carries its endpoint (`RPC0[ip:port]` or
    `RPC0: ip:port (...)`), else the i-th RPC device in -rpc order. Raises unless every stage maps to one device."""
    names = [(n, "") if isinstance(n, str) else tuple(n) for n in names]
    rpc = [(n, rest) for n, rest in names if n.startswith("RPC")]
    order = [s["dev"] for s in stages if s["dev"] != "card"]
    if len(rpc) != len(order):
        raise RuntimeError("expected %d RPC devices, found %d: %s" % (len(order), len(rpc), names))
    out = {}
    for i, dev in enumerate(order):
        ep = endpoints[dev]
        hit = [n for n, rest in rpc if ("[%s]" % ep) in n or rest.split(" ")[0] == ep]
        out[dev] = hit[0] if len(hit) == 1 else rpc[i][0]
    if len(set(out.values())) != len(out):
        raise RuntimeError("two stages mapped to one RPC device: %s" % out)
    if any(s["dev"] == "card" for s in stages):
        out["card"] = pick_vulkan(names, "gt730")
    return out


def stage_argv(run, head, model_path, endpoints, devmap):
    """Every layer on a stage (-ngl 99, layer split, no -ot): the conditions llama.cpp needs to overlap stages."""
    st = run["stages"]
    # -v: without it llama-bench installs a null log callback (llama-bench.cpp ~L2285 at 23b0202) and the
    # "pipeline parallelism enabled" line never reaches stderr - the overlap flag would read False whatever happened
    argv = head + ["-v", "-m", model_path, "-o", "json", "-r", str(run.get("r", BENCH_REPS)),
                   "-t", str(run.get("threads", THREADS)),
                   "-p", _csv(run.get("p") or [0]), "-n", _csv(run.get("n") or [0]),
                   "-ngl", "99", "-sm", "layer", "-rpc", ",".join(endpoints[s["dev"]] for s in st if s["dev"] != "card"),
                   "-dev", "/".join(devmap[s["dev"]] for s in st), "-ts", "/".join(str(s["ts"]) for s in st)]
    if run.get("d"):
        argv += ["-d", _csv(run["d"])]
    if run.get("ub"):
        argv += ["-ub", str(run["ub"])]
    return argv


STAGE_LIVE_S = 20.0


def _telemetry_db():
    """Tolkien's telemetry store, read-only (tolkien_telemetry writes it); None when it cannot be opened."""
    import sqlite3
    import tolkien_telemetry as tt
    try:
        return sqlite3.connect("file:%s?mode=ro" % tt.DB, uri=True, timeout=5)
    except sqlite3.Error:
        return None


def stage_busy_now(stage_nodes):
    """The deck's line for a pipelined run, from the telemetry store's newest moment: which stages are computing now.
    None when the store has nothing recent (the logger is down) - never a made-up idle."""
    import tolkien_telemetry as tt
    db = _telemetry_db()
    if db is None:
        return None
    try:
        rows = db.execute("SELECT node, MAX(proc_cpu_pct) FROM samples WHERE ts >= ? AND proc != '' GROUP BY node",
                          (time.time() - 2 * tt.BUCKET_S,)).fetchall()
    except Exception:                                                        # noqa: BLE001
        return None
    finally:
        db.close()
    seen = {n: v for n, v in rows if n in stage_nodes and v is not None}
    if not seen:
        return None
    busy = sum(1 for v in seen.values() if v >= tt.BUSY_PCT)
    return "busy now %d/%d: %s" % (busy, len(stage_nodes), " ".join(
        "%s %.0f%%" % (n, v) for n, v in sorted(seen.items(), key=lambda kv: -kv[1])))


def stage_overlap(t0, t1):
    """(max stages busy at the same moment, busy moments) over a run's window, from the telemetry store - the same
    derivation the summary CLI uses. None when the store cannot be read (could not tell, never 0)."""
    import tolkien_telemetry as tt
    db = _telemetry_db()
    if db is None:
        return None
    try:
        return tt.overlap(db, since=t0, until=t1)
    except Exception:                                                        # noqa: BLE001
        return None
    finally:
        db.close()


class StageLive(threading.Thread):
    """Every STAGE_LIVE_S, post the telemetry store's 'busy now' line for this run's stages to the deck."""

    def __init__(self, stage_nodes, live, every=STAGE_LIVE_S):
        threading.Thread.__init__(self, daemon=True)
        self.nodes, self.live, self.every, self.halt = set(stage_nodes), live, every, threading.Event()

    def run(self):
        while not self.halt.wait(self.every):
            try:
                line = stage_busy_now(self.nodes)
                if line and self.live is not None:
                    self.live(line)
            except Exception:                                                # noqa: BLE001
                pass


RPC_SET, RPC_GET, RPC_COMPUTE, RPC_RECOMPUTE = 6, 8, 10, 16       # Not anchored: llama.cpp load-progress dots may share a line with a trace line
_TRACE_LINE = re.compile(r"RPCTRACE (\d+) (\S+) (begin|end) (\d+)(?: (\S+))?")
# lab patch 4 (rpc_hostwait_trace_patch.py): the host thread waited >= 1 ms on that server
_HOSTWAIT_LINE = re.compile(r"RPCTRACE (\d+) (\S+) hostwait (\w+) (\d+)")


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def trace_summary(text):
    """Parses a GGML_RPC_TRACE log into per-stage timing figures. Assumes one begin/end line per RPC message.

    - window: compute start to response; median_ms normalizes by compute count (last stage holds full pass).
    - gap: input wait between windows.
    - copy_in: data transfer time excluding upstream waits (posted copies are measured from snapshot completion).
    - host_waits: host blocks during compute (pipelined passes should show only end-of-pass sync).

    Raises RuntimeError if no trace lines are found (indicates tracing was disabled)."""
    ev = [(int(t) / 1e6, ep, kind, int(cmd), tag) for t, ep, kind, cmd, tag in _TRACE_LINE.findall(text or "")]
    if not ev:
        raise RuntimeError("zero RPCTRACE lines - the trace was not on, or this build lacks the trace patch")
    ev.sort(key=lambda e: e[0])
    eps = sorted({e[1] for e in ev})
    st = {ep: {"begin": None, "sent": None, "windows": [], "copies": [], "first": None, "last": None} for ep in eps}
    last_get_end, computing = {}, False
    for t, ep, kind, cmd, tag in ev:
        s = st[ep]
        if kind == "begin":
            s["begin"] = t
            if cmd in (RPC_COMPUTE, RPC_RECOMPUTE):
                computing = True
                s["first"] = s["first"] if s["first"] is not None else t
                if s["sent"] is None:
                    s["sent"], s["n"] = t, 0
                s["n"] += 1
            continue
        b, s["begin"] = s["begin"], None
        if computing and b is not None:
            if tag == "fn":
                src = max([v for k, v in last_get_end.items() if k != ep] or [b])
                s["copies"].append(t - max(b, src))
            elif cmd == RPC_SET:
                s["copies"].append(t - b)
        if cmd == RPC_GET:
            last_get_end[ep] = t
            if s["sent"] is not None:
                s["windows"].append((s["sent"], t, s["n"]))
                s["sent"] = None
                s["last"] = t
    first_compute = min((s["first"] for s in st.values() if s["first"] is not None), default=None)
    waits = {}
    for t, ep, _kind, us in _HOSTWAIT_LINE.findall(text or ""):
        if first_compute is not None and int(t) / 1e6 >= first_compute:
            n, ms = waits.get(ep, (0, 0.0))
            waits[ep] = (n + 1, ms + int(us) / 1000.0)
    out = {}
    for ep in eps:
        s = st[ep]
        w = s["windows"]
        busy = [1000.0 * (b - a) for a, b, _n in w]
        ms = [1000.0 * (b - a) / n for a, b, n in w]
        gaps = [1000.0 * (w[i + 1][0] - w[i][1]) for i in range(len(w) - 1)]
        span = (s["last"] - s["first"]) if w else 0
        cp = [1000.0 * c for c in s["copies"]]
        out[ep] = {"windows": len(w), "computes": sum(n for _a, _b, n in w),
                   "median_ms": round(_pct(ms, 0.5), 1) if ms else None,
                   "p90_ms": round(_pct(ms, 0.9), 1) if ms else None,
                   "busy_s": round(sum(busy) / 1000.0, 2),
                   "busy_share": round(sum(busy) / 1000.0 / span, 3) if span > 0 else None,
                   "gap_median_ms": round(_pct(gaps, 0.5), 1) if gaps else None,
                   "copies_in": len(cp), "copy_in_median_ms": round(_pct(cp, 0.5), 1) if cp else None,
                   "copy_in_s": round(sum(cp) / 1000.0, 2),
                   "host_waits": waits.get(ep, (0, 0.0))[0], "host_wait_s": round(waits.get(ep, (0, 0.0))[1] / 1000.0, 2)}
    return out


def _server_log(order_name, rid, dev):
    """Opens the stage server trace log: OUTBOX/trace/<order>/<run>.<dev>.rpc.log. Returns None on failure (fail-soft)."""
    try:
        dst = os.path.join(tf.OUTBOX, "trace", order_name or "unnamed")
        os.makedirs(dst, exist_ok=True)
        return open(os.path.join(dst, "%s.%s.rpc.log" % (rid or "run", dev)), "wb")
    except OSError:
        return None


def ship_trace(stderr, order_name, rid):
    """A traced run's stderr is a deliverable: OUTBOX/trace/<order>/<run>.log. Fail-soft: returns the path or None."""
    try:
        dst = os.path.join(tf.OUTBOX, "trace", order_name)
        os.makedirs(dst, exist_ok=True)
        path = os.path.join(dst, "%s.log" % rid)
        with open(path, "w", encoding="utf-8") as f:
            f.write(stderr or "")
        return path
    except Exception:                                                        # noqa: BLE001
        return None


def overlap_verdict(stderr):
    """True = llama.cpp logged that it turned pipeline parallelism on; False = its context log is visible and says
    nothing of the kind; None = no llama.cpp log reached stderr at all, so the run COULD NOT TELL."""
    text = stderr or ""
    if "pipeline parallelism enabled" in text:
        return True
    return False if "llama_context" in text else None


def run_stages(run, log, adb_sh, stop_event=None, live=None, order_name=None):
    """One pipelined bench run: rigby's CPU as a localhost rpc-server, the tablets' async-RPC servers, the card as
    Vulkan. Returns (results, meta); every server it started is stopped and every tablet port proved closed on every
    route. meta.pipeline_parallel records whether llama.cpp actually turned overlap on - a run that ran the stages in
    turn must never read as a pipeline result. trace: true ships the RPC trace and puts trace_summary's figures in
    meta.trace, keyed by stage (the card is not an RPC device, so it has no row)."""
    d = next_dirs()
    st = run["stages"]
    tabs = [s["dev"] for s in st if not is_local_stage(s["dev"])]
    head, env = bench_head(run)
    if run.get("trace"):
        env = dict(env, GGML_RPC_TRACE="1")
    if run.get("graph_reuse") is False:
        # llama-context.cpp ~1433 (23b0202): a REUSED graph drains every stage before the next chunk's inputs, which
        # holds the host until the chunk reaches the last stage; a fresh graph per chunk takes the next input copy
        env = dict(env, LLAMA_GRAPH_REUSE_DISABLE="1")
    local, meta, sampler = [], {"stages": st}, None
    model = os.path.join(MODELS, run["model"])
    bad = stage_layout_defects(model, st)
    if bad:
        raise RuntimeError("stage layout refused, nothing started: " + "; ".join(bad))
    need = stage_weight_mib(model, st)
    kv = stage_kv_mib(model, st, bench_n_ctx(run))
    try:
        endpoints = {}
        if tabs:
            stale = kill_backends(tabs, adb_sh, NEXT_RPC_PORT)
            if stale:
                raise RuntimeError("rpc port still held after adb kill on %s" % ", ".join(stale))
            # free memory measured AFTER the previous run's servers are gone (round 13's second run was refused on
            # samples taken while the first run's servers still held their weights)
            killed_at, have = time.time(), {}
            deadline = killed_at + STAGE_MEM_WAIT_S
            while True:
                have = tablet_mem_avail(set(tabs), since=killed_at)
                if set(tabs) <= set(have) or time.time() > deadline:
                    break
                time.sleep(1)
            meta["stage_mem_mib"] = {dv: [round(need[dv]), round(kv[dv]), have.get(dv)] for dv in need}
            bad = stage_memory_defects({dv: stage_need_mib(need[dv], kv[dv], bench_n_ctx(run)) for dv in need}, have)
            if bad:
                raise RuntimeError("stages will not fit, nothing loaded: " + "; ".join(bad))
            charge = tablet_battery(set(tabs))
            meta["stage_battery"] = {dv: list(charge[dv]) if dv in charge else None for dv in tabs}
            low = stage_battery_defects(tabs, charge)
            if low:
                raise RuntimeError("stages will not run, nothing loaded: " + "; ".join(low))
        threads = rigby_threads(st, run.get("threads", THREADS))
        for dv in threads:
            out = _server_log(order_name, run.get("id"), dv) if len(threads) > 1 else None
            p = subprocess.Popen([os.path.join(d["bin"], rpc_server_name(d["bin"])), "-H", "127.0.0.1",
                                  "-p", str(rigby_port(dv)), "-t", str(threads[dv])],
                                 env=dict(os.environ, LD_LIBRARY_PATH=d["bin"]),
                                 stdout=out or subprocess.DEVNULL, stderr=subprocess.STDOUT if out else subprocess.DEVNULL)
            if out:
                out.close()                                  # the child holds its own descriptor
            local.append(p)
            deadline = time.time() + 30
            while not port_open("127.0.0.1", rigby_port(dv), timeout=1):
                if p.poll() is not None or time.time() > deadline:
                    raise RuntimeError("rigby's local rpc-server %s did not come up (rc=%s)" % (dv, p.poll()))
                time.sleep(0.5)
        if len(threads) > 1:
            meta["rigby_threads"] = threads
        if tabs:
            up = start_backends(tabs, log, all_cores=True, src=d["arm64"], tdir=NEXT_TABLET_DIR, port=NEXT_RPC_PORT,
                                cache=True, need_mib=need)
            if len(up) != len(tabs):
                raise RuntimeError("stage rpc-servers up on %s of %s" % (up, tabs))
        for s in st:
            if is_rigby(s["dev"]):
                endpoints[s["dev"]] = "127.0.0.1:%d" % rigby_port(s["dev"])
            elif s["dev"] != "card":
                endpoints[s["dev"]] = "%s:%d" % (_wire_ip(s["dev"]), NEXT_RPC_PORT)
        names, tail = bench_devices(head, env, [endpoints[s["dev"]] for s in st if s["dev"] != "card"])
        devmap = map_stage_devices(st, names, endpoints)
        meta.update(devices=[devmap[s["dev"]] for s in st], endpoints=endpoints)
        sampler = StageLive(list(dict.fromkeys("rigby" if is_local_stage(s["dev"]) else s["dev"] for s in st)), live)
        sampler.start()
        meta["window_start"] = time.time()
        if run.get("handoff"):
            res, meta["handoff"] = handoff_body(run, d["bin"], env, endpoints, devmap, local, stop_event, order_name)
            return res, meta
        if run.get("kld"):
            argv = kld_argv(run, head, model, kld_base_path(order_name, run)) + [
                "-ngl", "99", "-sm", "layer", "--rpc", ",".join(endpoints[s["dev"]] for s in st if s["dev"] != "card"),
                "-dev", ",".join(devmap[s["dev"]] for s in st), "-ts", ",".join(str(s["ts"]) for s in st)]
            rc, out, err = run_stoppable(argv, env, stop_event)
            res, meta["kld"] = parse_kld((out or "") + (err or ""), rc, True)
            return res, meta
        rc, out, err = run_stoppable(stage_argv(run, head, os.path.join(MODELS, run["model"]), endpoints, devmap),
                                     env, stop_event,
                                     beat=(lambda s: live("%dm%02ds" % (s // 60, s % 60))) if live else None)
        meta["pipeline_parallel"] = overlap_verdict(err)
        if run.get("trace"):
            meta["trace_log"] = ship_trace(err, order_name or "unnamed", run["id"])
            by_ep = trace_summary(err)
            dev_of = {ep: dev for dev, ep in endpoints.items()}
            meta["trace"] = {dev_of.get(ep, ep): v for ep, v in by_ep.items()}
            log("bench: %s trace %s" % (run["id"], "; ".join(
                "%s %s ms/chunk (p90 %s, gap %s, busy %s, host waits %d)" % (
                    k, v["median_ms"], v["p90_ms"], v["gap_median_ms"], v["busy_share"], v["host_waits"])
                for k, v in meta["trace"].items())))
        return parse_bench(out, rc), meta
    finally:
        if sampler is not None:
            sampler.halt.set()
            ov = stage_overlap(meta["window_start"], time.time())
            meta.update(max_busy_stages=ov[0] if ov else None, busy_moments=ov[1] if ov else None)
        for p in local:
            try:
                p.terminate()
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
            except Exception:                                                # noqa: BLE001 - cleanup never raises
                pass
        if tabs and adb_sh is not None:
            meta["backends_left_listening"] = kill_backends(tabs, adb_sh, NEXT_RPC_PORT)


KLD_DIR = "/tmp/tolkien-kld"            # rigby-local: a base holds every token's full log-probs (~1 GB)
KLD_CTX = 512


def kld_text_path(name):
    return os.path.join(os.path.dirname(tf.INBOX), "payloads", "kld", name)


def kld_base_path(order_name, run):
    return os.path.join(KLD_DIR, "%s.%s.kld" % (order_name or "unnamed", run["kld_text"]))


def kld_argv(run, head, model_path, base):
    """llama-perplexity on the order's text: kld_base saves rigby's own log-probs, kld scores against them -
    the quant-comparison instrument (mean KLD, top-token agreement), here pointed at the split's arithmetic."""
    argv = head[:-1] + [os.path.join(os.path.dirname(head[-1]), "llama-perplexity"), "-m", model_path, "-f", kld_text_path(run["kld_text"]), "-c", str(KLD_CTX),
                               "--chunks", str(run["chunks"]), "-t", str(run.get("threads", THREADS)),
                               "--kl-divergence-base", base]
    if run.get("ub"):
        argv += ["-ub", str(run["ub"])]
    return argv + (["--kl-divergence"] if run.get("kld") else [])


_KLD_FIELDS = (("ppl", r"Final estimate: PPL = ([\d.]+)"), ("ppl_q", r"Mean\s+PPL\(Q\)\s*:\s*([\d.]+)"),
               ("ppl_base", r"Mean\s+PPL\(base\)\s*:\s*([\d.]+)"), ("kld_mean", r"Mean\s+KLD:\s*([\d.]+)"),
               ("kld_p99", r"99\.0%\s+KLD:\s*([\d.]+)"), ("kld_max", r"Maximum KLD:\s*([\d.]+)"),
               ("same_top_pct", r"Same top p:\s*([\d.]+)"))


def parse_kld(text, rc, scored):
    """Metrics from llama-perplexity's report; raises unless it ran and (when scoring) printed a mean KLD."""
    if rc != 0:
        raise RuntimeError("llama-perplexity rc=%s: %s" % (rc, (text or "")[-300:]))
    m = {}
    for k, pat in _KLD_FIELDS:
        hit = re.search(pat, text or "")
        if hit:
            m[k] = float(hit.group(1))
    if scored and "kld_mean" not in m:
        raise RuntimeError("llama-perplexity printed no mean KLD: %s" % (text or "")[-300:])
    return [{"n_prompt": KLD_CTX, "n_gen": 0, "n_depth": 0, "avg_ts": 1.0, "stddev_ts": 0.0}], m


IMATRIX_CTX = 512


def imatrix_text_path(name):
    return os.path.join(os.path.dirname(tf.INBOX), "payloads", "imatrix", name)


def imatrix_out_path(order_name, run):
    """The run's product: llama-imatrix's GGUF (per tensor in_sum2 and, for expert tensors, a per-EXPERT count of the
    tokens routed to it), shipped beside the order's results."""
    return os.path.join(os.path.dirname(tf.INBOX), "outbox",
                        "%s.%s.imatrix.gguf" % (order_name or "unnamed", run.get("id", "run")))


def imatrix_argv(run, head, model_path, out, dev=None):
    """llama-imatrix over the order's text: how often each expert of each layer is routed on that text (the routing
    count behind pinning hot experts on the card). --no-ppl (the count is the product), --parse-special (the text is
    in the model's chat format), one save at the end. A fitted / hand placement as in bench_argv."""
    argv = head[:-1] + [os.path.join(os.path.dirname(head[-1]), "llama-imatrix"), "-m", model_path,
                        "-f", imatrix_text_path(run["imatrix_text"]), "-o", out, "-c", str(IMATRIX_CTX),
                        "--chunks", str(run["chunks"]), "-t", str(run.get("threads", THREADS)),
                        "--no-ppl", "--parse-special", "--output-frequency", "1000000"]
    if run.get("fit_target") is not None:
        argv += ["-dev", dev, "-sm", "none", "-fitt", str(run["fit_target"]), "-fitc", str(IMATRIX_CTX)]
    elif run.get("gpu_layers") is not None:
        argv += card_args({"gpu_layers": run["gpu_layers"], "override_tensor": run.get("override_tensor"),
                           "op_offload": run.get("op_offload")}, dev)
    if run.get("ub"):
        argv += ["-ub", str(run["ub"])]
    return argv


def parse_imatrix(text, rc, out):
    """The product's facts; raises unless llama-imatrix exited 0 AND wrote a non-empty file (a run that collected
    nothing must not read as a count)."""
    if rc != 0:
        raise RuntimeError("llama-imatrix rc=%s: %s" % (rc, (text or "")[-300:]))
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise RuntimeError("llama-imatrix wrote no file at %s: %s" % (out, (text or "")[-300:]))
    m = {"file": out, "bytes": os.path.getsize(out)}
    hit = re.search(r"computing over (\d+) chunks", text or "")
    if hit:
        m["chunks_computed"] = int(hit.group(1))
    return [{"n_prompt": IMATRIX_CTX, "n_gen": 0, "n_depth": 0, "avg_ts": 1.0, "stddev_ts": 0.0}], m


HANDOFF_PORTS = (18091, 18092)          # reader (the split), writer (rigby alone)
HANDOFF_TEXT = ("The committee reviewed the quarterly maintenance logs for the river locks, noting that the "
                "upstream gates had been resealed in spring while the downstream pumps still awaited new bearings. ")
HANDOFF_STATE = "handoff.bin"


def _server_refusal(e, path):
    """Construct an exception carrying the server's response body to preserve error details."""
    text = ""
    try:
        text = e.read().decode("utf-8", "replace")[:400]
    except Exception:                                                        # noqa: BLE001 - the reason is optional
        pass
    return RuntimeError("llama-server HTTP %s on %s: %s" % (e.code, path, text or e.reason))


def _http_json(port, path, body=None, timeout=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise _server_refusal(e, path) from None


def _serve(argv, env, port, log_file, stop_event):
    """Start a llama-server and return it once /health answers 200 - status, not a clock: it fails when the
    process exits, and a stop slip ends the wait."""
    p = subprocess.Popen(argv, env=env, stdout=log_file or subprocess.DEVNULL,
                         stderr=subprocess.STDOUT if log_file else subprocess.DEVNULL)
    if log_file:
        log_file.close()
    while True:
        if p.poll() is not None:
            raise RuntimeError("llama-server on :%d exited rc=%s while loading" % (port, p.returncode))
        if stop_event is not None and stop_event.is_set():
            p.terminate()
            raise BenchStopped()
        try:
            _http_json(port, "/health", timeout=5)
            return p
        except Exception:                                                    # noqa: BLE001 - not up yet
            time.sleep(1)


def _stop(procs):
    for p in procs:
        try:
            p.terminate()
            p.wait(30)
        except subprocess.TimeoutExpired:
            p.kill()
        except Exception:                                                    # noqa: BLE001 - cleanup never raises
            pass


def handoff_body(run, bindir, env, endpoints, devmap, local, stop_event, order_name):
    """READ on the split, WRITE on rigby alone (#36 working rule 4; round 18: writing through the split is 3.6x
    slower). Reader = llama-server over the stages; it reads the prompt, saves its slot (KV + recurrent state);
    rigby's stage servers stop; writer = llama-server on rigby's CPU restores the slot and writes. The writer's
    prompt EXTENDS the read one by one token, because the hybrid's recurrent state cannot be rewound (a prompt equal
    to the cache would force a full re-read). Control on the same writer: erase the slot, read + write from scratch.
    Returns ([one llama-bench-shaped result], metrics) - metrics.writer_reread_tokens says whether the cache was
    actually reused, and identical whether the handoff wrote the same text as the control."""
    model = os.path.join(MODELS, run["model"])
    p_tok, n_gen = run["p"][0], run["n"][0]
    ctx = bench_n_ctx(run) + 256
    slots = tempfile.mkdtemp(prefix="handoff_")
    srv = os.path.join(bindir, "llama-server")
    st = run["stages"]
    rd, wr = HANDOFF_PORTS
    m, procs = {}, []
    try:
        reader = _serve([srv, "-m", model, "-c", str(ctx), "-ub", str(run.get("ub", 512)), "-ngl", "99", "-sm", "layer",
                         "--rpc", ",".join(endpoints[s["dev"]] for s in st if s["dev"] != "card"),
                         "-dev", ",".join(devmap[s["dev"]] for s in st), "-ts", ",".join(str(s["ts"]) for s in st),
                         "-np", "1", "-t", str(run.get("threads", THREADS)), "--slot-save-path", slots,
                         "--host", "127.0.0.1", "--port", str(rd)],
                        env, rd, _server_log(order_name, run.get("id"), "reader"), stop_event)
        procs.append(reader)
        unit = _http_json(rd, "/tokenize", {"content": HANDOFF_TEXT})["tokens"]
        ids = (unit * (p_tok // len(unit) + 1))[:p_tok]
        tail = _http_json(rd, "/tokenize", {"content": " Summary:"})["tokens"][:1]
        r = _http_json(rd, "/completion", {"prompt": ids, "n_predict": 1, "cache_prompt": True, "id_slot": 0,
                                           "temperature": 0})
        m.update(read_ms=r["timings"]["prompt_ms"], read_tokens=r["timings"]["prompt_n"],
                 read_tps=r["timings"]["prompt_per_second"])
        s = _http_json(rd, "/slots/0?action=save", {"filename": HANDOFF_STATE})
        m.update(state_bytes=s.get("n_written"), save_ms=(s.get("timings") or {}).get("save_ms"))
        _stop([reader] + list(local))                                        # free rigby before the writer loads
        procs.remove(reader)
        t0 = time.time()
        wargs = ["-m", model, "-c", str(ctx), "-np", "1", "-t", str(run.get("threads", THREADS)),
                 "--slot-save-path", slots, "--host", "127.0.0.1", "--port", str(wr)]
        if run.get("writer"):                    # rigby + a GPU (the GT 730's attention = the writer A1 runs on)
            gpu = run["writer"].get("gpu", "gt730")
            head, wenv = card_launch(next_dirs()["vulkan"], "llama-server", gpu)
            wargv = head + wargs + card_args(run["writer"], vulkan_device(next_dirs()["vulkan"], gpu))
        else:
            wargv, wenv = [srv] + wargs, dict(env, LD_LIBRARY_PATH=bindir)
        writer = _serve(wargv, wenv, wr, _server_log(order_name, run.get("id"), "writer"), stop_event)
        procs.append(writer)
        m["writer_load_s"] = round(time.time() - t0, 1)
        s = _http_json(wr, "/slots/0?action=restore", {"filename": HANDOFF_STATE})
        m.update(restored_tokens=s.get("n_restored"), restore_ms=(s.get("timings") or {}).get("restore_ms"))
        body = {"prompt": ids + tail, "n_predict": n_gen, "cache_prompt": True, "id_slot": 0, "temperature": 0,
                "seed": 1, "ignore_eos": True}
        w = _http_json(wr, "/completion", body)
        m.update(writer_reread_tokens=w["timings"]["prompt_n"], writer_reread_ms=w["timings"]["prompt_ms"],
                 write_tps=w["timings"]["predicted_per_second"], written=w["timings"]["predicted_n"])
        _http_json(wr, "/slots/0?action=erase", {})
        c = _http_json(wr, "/completion", body)
        m.update(control_read_ms=c["timings"]["prompt_ms"], control_read_tokens=c["timings"]["prompt_n"],
                 control_write_tps=c["timings"]["predicted_per_second"])
        same = [i for i, (a, b) in enumerate(zip(w.get("content", ""), c.get("content", ""))) if a != b]
        m.update(identical=w.get("content") == c.get("content"), first_diff_char=same[0] if same else None)
        res = [{"n_prompt": p_tok, "n_gen": n_gen, "n_depth": 0, "avg_ts": m["write_tps"], "stddev_ts": 0.0}]
        return res, m
    finally:
        _stop(procs)
        shutil.rmtree(slots, ignore_errors=True)


# ── The live state dump a deploy would otherwise throw away ──
DUMP_PROBE_STATE = "dump_probe.bin"
DUMP_PROBE_KEYS = ("p", "cut", "n")
DUMP_PROBE_OPTIONAL = ("mode", "temperature", "seed", "doc")
DUMP_MODES = ("question", "continue", "chat")
DUMP_PROBE_FILES = ("dump_probe_ref.bin", "dump_probe_res.bin", "dump_probe_ctl.bin")   # the chat mode's id reads
# A slot file's head (llama_context::state_seq_save_file at 23b0202): u32 magic 'ggsq', u32 version, u32 n_tokens,
# int32 tokens[n], then the KV/recurrent state. The token list IS the live state's text - exact ids, no round trip.
SLOT_MAGIC, SLOT_VERSION = 0x67677371, 4
SLOT_TOKENS_STATE_VERSION = 1          # server_tokens::serialize's packed-list version (server-common.cpp)
# rigby serves Qwen-family models; these are their templates' reasoning markers. The chat probe's render check says
# whether a prefill built from them reproduces the saved state token for token.
THINK_OPEN, THINK_CLOSE = "<think>", "</think>"
DOCS_ROOT = os.environ.get("TOLKIEN_DOCS", "/docs")
DUMP_QUESTION = ("\n\nQuestion: In your own words, explain what the document above is for, then name three risks it "
                 "guards against and why each one matters.\nAnswer:")
NEAR_TIE = 0.1          # top-2 probability gap under which rounding can flip the choice


def dump_probe_defects(dp, ctx):
    """{p: question tokens, cut: answer tokens written before the stop, n: tokens written after the resume (mode "question") or in all (modes "continue" / "chat"); mode?: "question" (re-ask the question - the turn-boundary dump) | "continue" (dump the half-written answer and continue it from raw token ids) | "chat" (the same through the chat API, stopped by leaving the stream); temperature?, seed?}."""
    if not isinstance(dp, dict) or not set(DUMP_PROBE_KEYS) <= set(dp) \
            or set(dp) - set(DUMP_PROBE_KEYS) - set(DUMP_PROBE_OPTIONAL):
        return ["dump_probe must be {p, cut, n} (+ mode, temperature, seed): question tokens, answer tokens written "
                "before the stop, tokens written after the resume (question) or in all (continue)"]
    if any(isinstance(dp[k], bool) or not isinstance(dp[k], int) or dp[k] < 1 for k in DUMP_PROBE_KEYS):
        return ["dump_probe p, cut and n must be positive integers"]
    mode = dp.get("mode", "question")
    if mode not in DUMP_MODES:
        return ["dump_probe mode must be one of %s" % (DUMP_MODES,)]
    if mode in ("continue", "chat") and dp["cut"] >= dp["n"]:
        return ["dump_probe %s: cut must be below n (the stop falls inside the answer)" % mode]
    t = dp.get("temperature", 0)
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not 0 <= t <= 2:
        return ["dump_probe temperature must be a number 0..2"]
    if "seed" in dp and (isinstance(dp["seed"], bool) or not isinstance(dp["seed"], int)):
        return ["dump_probe seed must be an integer"]
    doc = dp.get("doc")
    if doc is not None and (not isinstance(doc, str) or not doc.endswith(".md") or doc.startswith(("/", "\\"))
                            or ".." in doc.replace("\\", "/").split("/")):
        return ["dump_probe doc is a library .md path relative to the docs mount (e.g. Runbook/RUNBOOK.md)"]
    if dp["p"] + max(dp["cut"], dp["n"]) + 8 > ctx:
        return ["dump_probe p + max(cut, n) must fit the order's ctx (%d)" % ctx]
    return []


def launch_spec(spec):
    """The spec rigby launches: the model's PRODUCTION profile (rigby_profiles.LAUNCH, the measured best) with the
    order laid over it key by key - the order wins, an explicit null turns a setting off. An order that places the model
    on another card or by hand (gpu_layers / override_tensor) is a different placement and takes no profile. Every rigby
    launch passes through here (an llm order's rigby arm, a chat window, the dump probe), so production runs the best
    settings without each caller carrying them."""
    import rigby_profiles
    prof = rigby_profiles.LAUNCH.get(spec.get("model"))
    if not prof:
        return spec
    if spec.get("gpu") not in (None, prof.get("gpu")) or spec.get("gpu_layers") is not None \
            or spec.get("override_tensor") is not None:
        return spec
    return dict(json.loads(json.dumps(prof)), **spec)


def rigby_server_kwargs(spec):
    """rigby's production launch knobs for start_server, from an llm spec - exactly what run_arm passes for the rigby
    arm (test_dump_probe pins the two equal: one derivation of how rigby's model is served)."""
    return {"reasoning": spec.get("reasoning"),
            "spec_type": spec.get("spec_type") or ("draft-simple" if spec.get("draft_model") else None),
            "gpu_layers": spec.get("gpu_layers"), "override_tensor": spec.get("override_tensor"),
            "op_offload": spec.get("op_offload"), "gpu": spec.get("gpu", "gt730") or "gt730",
            "fit_target": spec.get("fit_target"), "build": spec.get("build"), "parallel": spec.get("parallel"),
            "ubatch": spec.get("ubatch"), "kepler_flags": bool(spec.get("kepler_flags")),
            "threads": spec.get("threads"), "load_mode": spec.get("load_mode"), "draft_max": spec.get("draft_max"),
            "op_offload_min_batch": spec.get("op_offload_min_batch"), "engine_env": spec.get("engine_env"),
            "expert_hot": spec.get("expert_hot"), "moe_cache_mib": spec.get("moe_cache_mib")}


class ProbeStopped(Exception):
    pass


def _beating(call, tick, what, every_s=30):
    """Run one long blocking server call while the order keeps beating (a 30k-token read is minutes on rigby; silence
    would read as a hung order). Returns the call's result or raises its exception."""
    box = {}

    def body():
        try:
            box["r"] = call()
        except BaseException as e:                                           # noqa: BLE001 - re-raised below
            box["e"] = e
    t = threading.Thread(target=body, daemon=True)
    t0 = time.time()
    t.start()
    while t.is_alive():
        if tick is not None:
            tick("dump probe: %s %ds" % (what, time.time() - t0))
        t.join(every_s)
    if "e" in box:
        raise box["e"]
    return box.get("r")


def run_dump_probe(spec, log, stop_event=None, tick=None, post=None, launch=None, halt=None, chat_stream=None,
                   read_slot=None):
    """Five-step dump probe simulating a production restart with slot restoration.

    1. Generate a partial answer (simulates a stop mid-stream).
    2. Dump the slot state: input prompt minus the last token, no answer tokens.
    3. Restart the server and restore the slot from the saved state.
    4. Resume generation: the saved state must be a strict prefix of the prompt,
       as hybrid recurrent states cannot be rewound.
    5. Control run: repeat with the slot cleared to measure full re-read cost.

    Modes "continue" and "chat" bypass steps 2-3 using direct resume probes.
    Returns metrics; `stopped` is true if the run was interrupted between steps.
    ``post``, ``launch``, ``halt``, ``chat_stream``, and ``read_slot`` are injectable for tests."""
    spec = launch_spec(spec)           # the PRODUCTION launch: the model's profile under the order
    dp = spec["dump_probe"]
    ctx = int(spec.get("ctx", 2048))
    slots = os.path.join(tf.STATE_DIR, "slots")
    os.makedirs(slots, exist_ok=True)
    log_path = os.path.join(tf.STATE_DIR, "llm_rigby.log")
    kw = rigby_server_kwargs(spec)
    post = post or (lambda path, body: _http_json(SERVER_PORT, path, body))
    launch = launch or (lambda: start_server(os.path.join(MODELS, spec["model"]), ctx, None, log_path, stop_event,
                                             tick=tick, slot_save_path=slots, **kw))
    halt = halt or stop_server
    chat_stream = chat_stream or _probe_chat
    read_slot = read_slot or (lambda filename: slot_file_tokens(os.path.join(slots, filename)))
    m = {"p": dp["p"], "cut": dp["cut"], "n": dp["n"], "model": spec["model"], "ctx": ctx, "steps_done": 0}
    gen = {"cache_prompt": True, "id_slot": 0, "temperature": 0, "seed": 1, "ignore_eos": True}

    def run(what, call):
        if stop_event is not None and stop_event.is_set():
            raise ProbeStopped(what)
        return _beating(call, tick, what)

    def step(what, path, body):
        return run(what, lambda: post(path, body))

    def t(r):
        return r.get("timings") or {}
    proc = None
    try:
        proc, m["load_s"] = launch()
        if dp.get("doc"):
            with open(os.path.join(DOCS_ROOT, dp["doc"]), encoding="utf-8", errors="replace") as f:
                text = f.read()
            q = post("/tokenize", {"content": DUMP_QUESTION})["tokens"]
            ids = post("/tokenize", {"content": text})["tokens"][:max(1, dp["p"] - len(q))] + q
            m["doc"] = dp["doc"]
        else:
            unit = post("/tokenize", {"content": HANDOFF_TEXT})["tokens"]
            ids = (unit * (dp["p"] // len(unit) + 1))[:dp["p"]]
        m["prompt_tokens"] = len(ids)
        if dp.get("mode", "question") == "continue":
            proc = _continue_probe(m, dp, ids, gen, step, t, post, launch, halt, proc)
            return m
        if dp.get("mode") == "chat":
            # the same p tokens as text in ONE user message - what an order's chat request carries
            messages = [{"role": "user", "content": post("/detokenize", {"tokens": ids})["content"]}]
            proc = _chat_probe(m, dp, messages, run, step, launch, halt, proc, chat_stream, read_slot)
            return m
        r = step("1 cold read + cut answer", "/completion", dict(gen, prompt=ids, n_predict=dp["cut"]))
        m.update(cold_read_tokens=t(r).get("prompt_n"), cold_read_ms=t(r).get("prompt_ms"),
                 cut_written=t(r).get("predicted_n"), steps_done=1)
        s = step("2 stop: question minus its last token", "/completion", dict(gen, prompt=ids[:-1], n_predict=0))
        m.update(stop_reread_tokens=t(s).get("prompt_n"), stop_reread_ms=t(s).get("prompt_ms"),
                 stop_written=t(s).get("predicted_n"))
        sv = step("2 save", "/slots/0?action=save", {"filename": DUMP_PROBE_STATE})
        m.update(state_bytes=sv.get("n_written"), saved_tokens=sv.get("n_saved"), save_ms=t(sv).get("save_ms"),
                 steps_done=2)
        halt(proc)
        proc = None
        proc, m["reload_s"] = launch()
        rs = step("3 restore", "/slots/0?action=restore", {"filename": DUMP_PROBE_STATE})
        m.update(restored_tokens=rs.get("n_restored"), restore_ms=t(rs).get("restore_ms"), steps_done=3)
        w = step("4 resumed turn", "/completion", dict(gen, prompt=ids, n_predict=dp["n"]))
        m.update(resume_reread_tokens=t(w).get("prompt_n"), resume_reread_ms=t(w).get("prompt_ms"),
                 resume_written=t(w).get("predicted_n"), steps_done=4)
        step("5 erase", "/slots/0?action=erase", {})
        c = step("5 control", "/completion", dict(gen, prompt=ids, n_predict=dp["n"]))
        m.update(control_read_tokens=t(c).get("prompt_n"), control_read_ms=t(c).get("prompt_ms"),
                 identical=w.get("content") == c.get("content"), steps_done=5)
        parts = [m.get(k) for k in ("stop_reread_ms", "save_ms", "restore_ms", "resume_reread_ms")]
        m["with_dump_ms"] = round(sum(parts), 1) if all(isinstance(x, (int, float)) for x in parts) else None
        m["without_dump_ms"] = m["control_read_ms"]
        m["dump_resumes"] = isinstance(m["resume_reread_tokens"], int) and m["resume_reread_tokens"] <= 2
        log("dump probe: stop re-read %s tok / %s ms, save %s ms (%s B), restore %s ms, resumed turn re-read %s tok "
            "- vs full re-read %s tok / %s ms" % (m["stop_reread_tokens"], m["stop_reread_ms"], m["save_ms"],
                                                   m["state_bytes"], m["restore_ms"], m["resume_reread_tokens"],
                                                   m["control_read_tokens"], m["control_read_ms"]))
    except ProbeStopped as e:
        m["stopped"] = str(e)
    except Exception as e:                                                   # noqa: BLE001
# Catch-all required: partial probe results must be preserved, not discarded by a later step failure
        m["error"] = "%s: %s" % (type(e).__name__, str(e)[:400])
    finally:
        halt(m.pop("_proc", None) or proc)
        for f in (DUMP_PROBE_STATE,) + DUMP_PROBE_FILES:
            try:
                os.remove(os.path.join(slots, f))
            except OSError:
                pass
    return m


def _gen_tokens(r, post):
    """(token ids the server generated, where they came from). `return_tokens` gives the exact ids; without it the
    text is re-tokenized, which is NOT exact (a detokenize/tokenize round trip can merge or split) - said, not hidden."""
    toks = r.get("tokens")
    if isinstance(toks, list) and toks and all(isinstance(x, int) for x in toks):
        return list(toks), "server"
    return list(post("/tokenize", {"content": r.get("content") or ""})["tokens"]), "retokenized"


def _near_ties(r):
    """How many written tokens had their top two candidates within NEAR_TIE probability - the positions where float
    rounding (or a different random draw) can change the answer. None when the server sent no probabilities: a test
    whose answer could not have moved must not read as a lossless one."""
    import math
    rows = r.get("completion_probabilities")
    if not isinstance(rows, list) or not rows:
        return None
    n = 0
    for e in rows:
        top = e.get("top_logprobs") or e.get("top_probs") or e.get("probs") or []
        ps = []
        for x in top[:2]:
            if "logprob" in x:
                ps.append(math.exp(x["logprob"]))
            elif "prob" in x:
                ps.append(x["prob"])
        if len(ps) == 2 and abs(ps[0] - ps[1]) < NEAR_TIE:
            n += 1
    return n


def _first_diff(a, b):
    """Index of the first differing token, len of the shorter on a pure length difference, None when identical."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if len(a) == len(b) else min(len(a), len(b))


def _continue_probe(m, dp, ids, gen, step, t, post, launch, halt, proc):
    """Test token-continuation fidelity via slot dump/restore. Compares uninterrupted generation against resumed generation to verify lossless continuity. Note: temperature > 0 breaks 1-to-1 match because llama.cpp does not persist RNG state in slots; divergence at the cut point is expected."""
    # n_probs on EVERY call (one variable: the reference and the resume are asked the same way)
    g = dict(gen, temperature=dp.get("temperature", 0), seed=dp.get("seed", 1), return_tokens=True, n_probs=2)
    m.update(mode="continue", temperature=g["temperature"], seed=g["seed"])
    a = step("1 reference", "/completion", dict(g, prompt=ids, n_predict=dp["n"]))
    A, src = _gen_tokens(a, post)
    m.update(ref_read_tokens=t(a).get("prompt_n"), ref_written=len(A), token_source=src, steps_done=1,
             near_ties=_near_ties(a), ref_text_head=(a.get("content") or "")[:300])
    step("2 erase", "/slots/0?action=erase", {})
    b = step("2 interrupted at cut", "/completion", dict(g, prompt=ids, n_predict=dp["cut"]))
    B, src_b = _gen_tokens(b, post)
    sv = step("2 save", "/slots/0?action=save", {"filename": DUMP_PROBE_STATE})
    m.update(cut_written=len(B), prefix_identical=B == A[:len(B)], prefix_first_diff=_first_diff(B, A[:len(B)]),
             state_bytes=sv.get("n_written"), saved_tokens=sv.get("n_saved"), save_ms=t(sv).get("save_ms"),
             steps_done=2)
    halt(proc)
    m["_proc"] = None
    proc, m["reload_s"] = launch()
    m["_proc"] = proc
    rs = step("3 restore", "/slots/0?action=restore", {"filename": DUMP_PROBE_STATE})
    m.update(restored_tokens=rs.get("n_restored"), restore_ms=t(rs).get("restore_ms"), steps_done=3)
    c = step("4 continue", "/completion", dict(g, prompt=ids + B, n_predict=dp["n"] - len(B)))
    C, src_c = _gen_tokens(c, post)
    resumed = B + C
    m.update(continue_reread_tokens=t(c).get("prompt_n"), continue_reread_ms=t(c).get("prompt_ms"),
             lossless=resumed == A, first_diff=_first_diff(resumed, A), steps_done=4)
    step("5 erase", "/slots/0?action=erase", {})
    d = step("5 control", "/completion", dict(g, prompt=ids + B, n_predict=dp["n"] - len(B)))
    D, src_d = _gen_tokens(d, post)
    m.update(control_read_tokens=t(d).get("prompt_n"), control_read_ms=t(d).get("prompt_ms"),
             control_lossless=(B + D) == A, control_first_diff=_first_diff(B + D, A), steps_done=5)
    if {src, src_b, src_c, src_d} != {"server"}:
        m["token_source"] = "retokenized"        # any text round trip makes the identity checks approximate
    return proc


def slot_file_tokens(path):
    """Reads the exact token ids from a llama-server slot file. Note: the file layout is PACKED ([-1 marker][state version 1][count][count ids][media count][media...]) rather than a plain list of ids. Fails fast if the magic/version doesn't match this engine."""
    import struct
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) != 12:
            raise ValueError("slot file %s: %d header bytes" % (path, len(head)))
        magic, version, n = struct.unpack("<III", head)
        if (magic, version) != (SLOT_MAGIC, SLOT_VERSION):
            raise ValueError("slot file %s: magic %#x version %d, want %#x version %d"
                             % (path, magic, version, SLOT_MAGIC, SLOT_VERSION))
        body = f.read(4 * n)
    if len(body) != 4 * n:
        raise ValueError("slot file %s: %d of %d token bytes" % (path, len(body), 4 * n))
    packed = list(struct.unpack("<%di" % n, body))
    if not packed or packed[0] != -1:
        return packed                                      # a plain id list (older servers)
    if len(packed) < 3 or packed[1] != SLOT_TOKENS_STATE_VERSION:
        raise ValueError("slot file %s: packed tokens state version %s, want %d"
                         % (path, packed[1] if len(packed) > 1 else None, SLOT_TOKENS_STATE_VERSION))
    count = packed[2]
    if count < 0 or 3 + count >= len(packed):
        raise ValueError("slot file %s: packed count %d does not fit %d slots" % (path, count, len(packed)))
    if packed[3 + count] != 0:
        raise ValueError("slot file %s: holds %d media chunks - not a text slot" % (path, packed[3 + count]))
    return packed[3:3 + count]


def prefill_message(prompt_text, gen_text, keep_tail=False):
    """Returns (assistant_message, field_name) to re-render prompt + partial answer for chat continuation.

    keep_tail=True preserves the text after THINK_OPEN in the prompt; keep_tail=False strips it.
    The template determines if the reasoning marker re-adds post-THINK_OPEN content, so both forms
    must be generated. The caller must select the variant where the rendered IDs match the saved IDs—
    do not guess."""
    k = prompt_text.rfind(THINK_OPEN, max(0, len(prompt_text) - 64))
    opened = k >= 0 and THINK_CLOSE not in prompt_text[k:]
    text = prompt_text[k + len(THINK_OPEN):] + gen_text if opened and keep_tail else gen_text
    if not opened and text.startswith(THINK_OPEN):
        text, opened = text[len(THINK_OPEN):], True
    if not opened:
        return {"role": "assistant", "content": text}, "content"
    if THINK_CLOSE in text:
        r, c = text.split(THINK_CLOSE, 1)
        return {"role": "assistant", "reasoning_content": r, "content": c}, "content"
    # the server refuses an assistant message with no `content` key at all (server-common.cpp: "Assistant message
    # must contain either 'content' or 'tool_calls'!") - a stop inside the thinking still sends an empty one
    return {"role": "assistant", "reasoning_content": text, "content": ""}, "reasoning_content"


def prefill_candidates(prompt_text, gen_text):
    """The distinct prefill forms for one half answer, the likelier first (prefill_message's keep_tail False, then
    True)."""
    out = []
    for keep in (False, True):
        f = prefill_message(prompt_text, gen_text, keep_tail=keep)
        if f not in out:
            out.append(f)
    return out


def _common_prefix(x, y):
    n = 0
    for a, b in zip(x, y):
        if a != b:
            break
        n += 1
    return n


def pick_prefill(forms, render, target):
    """(index, message, continue_as, rendered ids) of the first form whose render EQUALS target (the saved ids);
    none equal -> the one sharing the longest prefix with it. `render(message, continue_as)` -> ids."""
    best = None
    for i, (msg, cont) in enumerate(forms):
        ids = render(msg, cont)
        if ids == target:
            return i, msg, cont, ids
        if best is None or _common_prefix(ids, target) > _common_prefix(best[3], target):
            best = (i, msg, cont, ids)
    return best


def _probe_chat(body, stop_after=None):
    """One streamed /v1/chat/completions call on this order's server, read as chat() reads it. stop_after: leave the
    stream after that many chunks carrying text - exactly how chat() leaves it on a stop slip (the server notices and
    stops on its own). Returns the reasoning and content text, the chunk count, the last timings and every token's
    logprobs entry (near-ties)."""
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % SERVER_PORT,
                                 data=json.dumps(dict(body, stream=True, timings_per_token=True)).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    reasoning, content, probs, tm, n = [], [], [], {}, 0
    try:
        resp = urllib.request.urlopen(req, timeout=IDLE_S)
    except urllib.error.HTTPError as e:
        raise _server_refusal(e, "/v1/chat/completions") from None
    with resp as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            obj = json.loads(data)
            for ch in obj.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content") or d.get("reasoning_content"):
                    n += 1
                content.append(d.get("content") or "")
                reasoning.append(d.get("reasoning_content") or "")
                probs.extend(((ch.get("logprobs") or {}).get("content")) or [])
            tm = obj.get("timings") or tm
            if stop_after and n >= stop_after:
                break
    return {"reasoning": "".join(reasoning), "content": "".join(content), "chunks": n, "timings": tm, "probs": probs}


def _chat_probe(m, dp, messages, run, step, launch, halt, proc, chat_stream, read_slot):
    """The quick-resume probe through the PRODUCTION path - the chat API the orders use (complete() / chat()) - with
    a REAL stop: the client leaves the stream, as chat() does on a slip. Exact ids come from the slot files (the live
    state), never from text:
      1. reference: n tokens straight through; save -> its ids
      2. interrupted: leave the stream after `cut` text chunks; save (the server defers a busy slot's save until it
         stops on its own) -> the live state, incl. tokens written after the client left
      3. RENDER CHECK: the half answer as the assistant message a chat continue renders (prefill_message);
         /apply-template + /tokenize of that request vs the saved ids - equal = the resume can be exact. Also the
         same check for the text the CLIENT saw
      4. the deploy: restart, restore, continue through the chat API; save -> ids; lossless = the reference's ids
      5. control: erase, the same continue WITHOUT the dump (a full re-read) - what the dump bought
    Returns the live proc."""
    g = {"temperature": dp.get("temperature", 0), "seed": dp.get("seed", 1), "logprobs": True, "top_logprobs": 2}
    m.update(mode="chat", temperature=g["temperature"], seed=g["seed"])
    ref_f, res_f, ctl_f = DUMP_PROBE_FILES

    def render(body):
        text = step("render", "/apply-template", body)["prompt"]
        return text, step("tokenize", "/tokenize", {"content": text, "add_special": True,
                                                     "parse_special": True})["tokens"]

    def tm(r):
        return r.get("timings") or {}
    base = {"messages": messages}
    ptext, P = render(base)
    m.update(prompt_tokens=len(P), generation_prompt_tail=ptext[-40:])
    step("1 erase", "/slots/0?action=erase", {})
    a = run("1 reference", lambda: chat_stream(dict(base, max_tokens=dp["n"], **g), None))
    step("1 save", "/slots/0?action=save", {"filename": ref_f})
    A = read_slot(ref_f)
    m.update(prompt_match=A[:len(P)] == P, ref_written=len(A) - len(P), ref_chunks=a["chunks"],
             near_ties=_near_ties({"completion_probabilities": a.get("probs")}),
             ref_text_head=(a["reasoning"] + "|" + a["content"])[:300], steps_done=1)
    step("2 erase", "/slots/0?action=erase", {})
    b = run("2 interrupted", lambda: chat_stream(dict(base, max_tokens=dp["n"], **g), dp["cut"]))
    sv = step("2 save", "/slots/0?action=save", {"filename": DUMP_PROBE_STATE})
    B = read_slot(DUMP_PROBE_STATE)
    G = B[len(P):]
    m.update(client_chunks=b["chunks"], server_written=len(G), ran_past_client=len(G) - b["chunks"],
             prefix_identical=B == A[:len(B)], prefix_first_diff=_first_diff(B, A[:len(B)]),
             state_bytes=sv.get("n_written"), saved_tokens=sv.get("n_saved"), save_ms=tm(sv).get("save_ms"),
             lab_drafter_bytes=sv.get("lab_drafter_bytes"), lab_replay=sv.get("lab_replay"),
             lab_rng_bytes=sv.get("lab_rng_bytes"), steps_done=2)
    if len(G) >= dp["n"] - 1:
        m["cut_void"] = "the server wrote the whole answer before it saw the client leave - nothing was cut"
    def continue_body(msg, cont):
        return dict(base, messages=messages + [msg], continue_final_message=cont, add_generation_prompt=False)
    # the engine's PENDING token (lab patch #0, slot_pending_token_patch.py): the sampled token no cache holds yet.
    # With it the continue EXTENDS the saved state by one token (1 read, no rollback); an unpatched engine sends none
    # and the continue equals the cache (rollback + re-read of the half answer) - the oracle arm below covers that.
    pending = sv.get("pending_token")
    use_pending = isinstance(pending, int) and not isinstance(pending, bool) and pending >= 0
    m.update(pending_token=pending, continue_with_pending=use_pending,
             pending_matches_reference=(pending == A[len(B)]) if use_pending and len(A) > len(B) else None)
    gen_ids, target = (G + [pending], B + [pending]) if use_pending else (G, B)
    forms = prefill_candidates(ptext, step("3 detokenize", "/detokenize", {"tokens": gen_ids})["content"])
    form, msg, cont, R = pick_prefill(forms, lambda mg, ct: render(continue_body(mg, ct))[1], target)
    body = continue_body(msg, cont)
    m.update(prefill_form=form, prefill_forms=len(forms))
    seen = {"role": "assistant", "content": b["content"]}
    if b["reasoning"]:
        seen["reasoning_content"] = b["reasoning"]
    _, Rc = render(dict(base, messages=messages + [seen], add_generation_prompt=False,
                        continue_final_message="content" if b["content"] else "reasoning_content"))
    m.update(continue_as=cont, render_match=R == target, render_first_diff=_first_diff(R, target), render_tokens=len(R),
             client_render_match=Rc == B[:len(Rc)], client_render_first_diff=_first_diff(Rc, B[:len(Rc)]),
             steps_done=3)
    halt(proc)
    m["_proc"] = None
    proc, m["reload_s"] = launch()
    m["_proc"] = proc
    rs = step("3 restore", "/slots/0?action=restore", {"filename": DUMP_PROBE_STATE})
    m.update(restored_tokens=rs.get("n_restored"), restore_ms=tm(rs).get("restore_ms"),
             resume_armed=rs.get("lab_resume_armed"))
    rest = max(1, dp["n"] - len(gen_ids))           # the pending token, when sent, is already written
    c = run("4 continue", lambda: chat_stream(dict(body, max_tokens=rest, **g), None))
    step("4 save", "/slots/0?action=save", {"filename": res_f})
    C = read_slot(res_f)
    L = min(len(A), len(C))
    m.update(continue_reread_tokens=tm(c).get("prompt_n"), continue_reread_ms=tm(c).get("prompt_ms"),
             continue_cache_n=tm(c).get("cache_n"), compared=L - len(P),
             lossless=C[:L] == A[:L] and L - len(P) >= dp["n"] - 2,
             first_diff=None if C[:L] == A[:L] else _first_diff(C[:L], A[:L]) - len(P), steps_done=4)
    # 4b ORACLE: the cache never holds the LAST token the server sampled (it is decoded only on the next step), so a
    # continue that equals the cache makes a hybrid roll back to its prompt-end checkpoint and re-read the half answer
    # (server-context.cpp, [TAG_PROMPT_LOGITS]). The same continue + that one pending token - taken from the reference,
    # valid only when the fresh runs agreed - says whether saving it with the slot (an engine patch) is what is missing.
    if not use_pending and m["prefix_identical"] and len(A) > len(B):
        step("4b restore", "/slots/0?action=restore", {"filename": DUMP_PROBE_STATE})
        forms2 = prefill_candidates(ptext, step("4b detokenize", "/detokenize", {"tokens": G + [A[len(B)]]})["content"])
        _, msg2, cont2, R2 = pick_prefill(forms2, lambda mg, ct: render(continue_body(mg, ct))[1], B + [A[len(B)]])
        body2 = continue_body(msg2, cont2)
        c2 = run("4b continue + the pending token",
                 lambda: chat_stream(dict(body2, max_tokens=max(1, rest - 1), **g), None))
        step("4b save", "/slots/0?action=save", {"filename": res_f})
        C2 = read_slot(res_f)
        L2 = min(len(A), len(C2))
        m.update(oracle_render_extends=R2[:len(B)] == B and len(R2) == len(B) + 1,
                 oracle_reread_tokens=tm(c2).get("prompt_n"),
                 oracle_lossless=C2[:L2] == A[:L2] and L2 - len(P) >= dp["n"] - 2,
                 oracle_first_diff=None if C2[:L2] == A[:L2] else _first_diff(C2[:L2], A[:L2]) - len(P))
    # 4c LAB RESUME (engine patch #3, slot_resume_patch.py): step 4 restored the drafter's state with the slot (patch
    # #3's part a) but still read the pending token alone; here the same continue asks the engine to take the
    # interrupted run's NEXT STEP - the pending token as the sampled one, decoded with its draft as one batch - and
    # should read 0 tokens. Runs only when the engine says the restore armed it (an engine without #3 says nothing).
    if use_pending and m.get("resume_armed") is not None:
        ra = step("4c restore", "/slots/0?action=restore", {"filename": DUMP_PROBE_STATE})
        m["resume_armed"] = ra.get("lab_resume_armed")
        if m["resume_armed"]:
            c3 = run("4c lab resume", lambda: chat_stream(dict(body, max_tokens=rest, lab_resume=True, **g), None))
            step("4c save", "/slots/0?action=save", {"filename": res_f})
            C3 = read_slot(res_f)
            L3 = min(len(A), len(C3))
            m.update(resume_reread_tokens=tm(c3).get("prompt_n"), resume_cache_n=tm(c3).get("cache_n"),
                     resume_lossless=C3[:L3] == A[:L3] and L3 - len(P) >= dp["n"] - 2,
                     resume_first_diff=None if C3[:L3] == A[:L3] else _first_diff(C3[:L3], A[:L3]) - len(P))
    step("5 erase", "/slots/0?action=erase", {})
    d = run("5 control", lambda: chat_stream(dict(body, max_tokens=rest, **g), None))
    step("5 save", "/slots/0?action=save", {"filename": ctl_f})
    D = read_slot(ctl_f)
    Ld = min(len(A), len(D))
    m.update(control_read_tokens=tm(d).get("prompt_n"), control_read_ms=tm(d).get("prompt_ms"),
             control_lossless=D[:Ld] == A[:Ld] and Ld - len(P) >= dp["n"] - 2,
             control_first_diff=None if D[:Ld] == A[:Ld] else _first_diff(D[:Ld], A[:Ld]) - len(P), steps_done=5)
    # the instrument names its own mismatches: where each pair first differs, as decoded token pieces
    m["lens"] = {"P": len(P), "A": len(A), "B": len(B), "R": len(R), "C": len(C), "D": len(D)}
    m["ref_prompt_n"], m["ref_cache_n"] = tm(a).get("prompt_n"), tm(a).get("cache_n")
    m["diff_windows"] = {tag: _diff_window(step, x, y) for tag, x, y in
                         (("P_vs_A", P, A[:len(P)]), ("B_vs_A", B, A[:len(B)]), ("R_vs_target", R, target),
                          ("C_vs_A", C[:L], A[:L])) if _first_diff(x, y) is not None}
    return proc


def _diff_window(step, x, y, k=4):
    """{at, x: [pieces], y: [pieces]} around the first difference of two id lists - each id decoded on its own, so a
    tokenization boundary shows as different PIECES spelling the same text."""
    i = _first_diff(x, y)

    def pieces(ids):
        return [step("diag", "/detokenize", {"tokens": [t]})["content"] for t in ids[max(0, i - k):i + k]]
    return {"at": i, "x": pieces(x), "y": pieces(y), "x_ids": x[max(0, i - k):i + k], "y_ids": y[max(0, i - k):i + k]}


def tablet_bench_cmd(run):
    th = str(run["threads"]) if run.get("threads") else '$(t=$(nproc); [ "$t" -gt 8 ] && t=8; echo $t)'
    args = "-m %s -o json -r %d -t %s -p %s -n %s" % (run["model"], run.get("r", BENCH_REPS), th,
                                                     _csv(run.get("p") or [0]), _csv(run.get("n") or [0]))
    if run.get("d"):
        args += " -d %s" % _csv(run["d"])
    if run.get("ub"):
        args += " -ub %d" % run["ub"]
    return "cd ~/%s && chmod +x llama-bench && LD_LIBRARY_PATH=. ./llama-bench %s 2>/dev/null" % (TABLET_DIR, args)


BENCH_KEEP = ("n_prompt", "n_gen", "n_depth", "avg_ts", "stddev_ts", "n_threads", "n_gpu_layers", "backends",
              "gpu_info", "cpu_info", "model_type", "model_size", "n_batch", "n_ubatch", "type_k", "type_v")


def parse_bench(out, rc, err=""):
    """llama-bench -o json's result list. Raises unless it ran, parsed, and every result reports a speed. A failure
    carries stderr's tail: with -o json, stdout holds only the opened list, and the reason is on stderr."""
    if rc != 0:
        raise RuntimeError("llama-bench rc=%s: %s | stderr: %s" % (rc, (out or "")[-200:], (err or "")[-600:]))
    if (out or "").lstrip().startswith("{"):                                 # -o jsonl: one result object per line
        res = [json.loads(l) for l in out.splitlines() if l.strip().startswith("{")]
    else:
        a, b = (out or "").find("["), (out or "").rfind("]")
        if a < 0 or b < a:
            raise RuntimeError("no JSON result list in llama-bench output: %s" % (out or "")[-300:])
        res = json.loads(out[a:b + 1])
    if not isinstance(res, list) or not res:
        raise RuntimeError("llama-bench returned no results")
    if not all(isinstance(x, dict) and (x.get("avg_ts") or 0) > 0 for x in res):
        raise RuntimeError("llama-bench returned a result without a speed")
    return res


def stage_tablet_bench(host, model, log):
    """llama-bench + libomp (the same 70adb1b arm64 build as the rpc-server) and the model, copied only when the
    tablet's copy differs in size."""
    for f, src in (("llama-bench", os.path.join(LLAMA_ARM64, "llama-bench")),
                   ("libomp.so", os.path.join(LLAMA_ARM64, "libomp.so")),
                   (model, os.path.join(MODELS, model))):
        if not os.path.exists(src):
            raise RuntimeError("%s is not on rigby (%s)" % (f, src))
        rc, out = tf._ssh(host, "mkdir -p ~/%s && stat -c %%s ~/%s/%s 2>/dev/null || echo 0" % (TABLET_DIR, TABLET_DIR, f),
                          timeout=30)
        nums = [l.strip() for l in (out or "").splitlines() if l.strip().isdigit()]
        if not nums or int(nums[-1]) != os.path.getsize(src):
            log("bench: staging %s to %s" % (f, host))
            rc, out = tf._scp(host, src, "%s/%s" % (TABLET_DIR, f))
            if rc != 0:
                raise RuntimeError("staging %s on %s failed: %s" % (f, host, (out or "")[-200:]))


def link_throughput(host, mb):
    """ssh throughput rigby <-> a tablet over the route the work takes (wire first), each way, plus a bare
    connect's time so the handshake can be told apart from the link."""
    opts, ip = tf._ssh_base(host)
    base = ["ssh"] + opts + [ip]
    t0 = time.time()
    rc = subprocess.run(base + ["true"], capture_output=True).returncode
    connect_s = time.time() - t0
    if rc != 0:
        raise RuntimeError("ssh to %s (%s) failed rc=%s" % (host, ip, rc))
    n, chunk = mb * 1048576, b"\0" * 1048576
    t0 = time.time()
    p = subprocess.Popen(base + ["cat > /dev/null"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    for _ in range(mb):
        p.stdin.write(chunk)
    p.stdin.close()
    rc_up, up_s = p.wait(), time.time() - t0
    t0 = time.time()
    p = subprocess.Popen(base + ["head -c %d /dev/zero" % n], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    got = 0
    while True:
        blk = p.stdout.read(1048576)
        if not blk:
            break
        got += len(blk)
    rc_down, down_s = p.wait(), time.time() - t0
    if rc_up or rc_down or got != n:
        raise RuntimeError("link test incomplete: up rc=%s, down rc=%s, %d of %d bytes" % (rc_up, rc_down, got, n))
    return {"route": "wire" if tf.on_wire(ip) else "wifi", "ip": ip, "mb": mb,
            "connect_s": round(connect_s, 3), "to_tablet_s": round(up_s, 3), "from_tablet_s": round(down_s, 3),
            "to_tablet_mb_s": round(mb / up_s, 2), "from_tablet_mb_s": round(mb / down_s, 2)}


def _dram_ok(v):
    return v is not None and v >= tf.DRAM_MIN_MGATHER


def stage_dram_check(spec, kept, log):
    """{tablet: M gathers/s or None} - the lab's DRAM probe (tablet_foundry.dram_health) once per order on every
    tablet a not-yet-finished stage run uses. A degraded memory state reads full clock and runs up to 3.4x slow
    (Fellowship-Operations § "DRAM State Degradation"); it is invisible to every other check."""
    tabs = []
    for r in spec["runs"]:
        if "stages" in r and r["id"] not in kept:
            tabs += [s["dev"] for s in r["stages"] if not is_local_stage(s["dev"]) and s["dev"] not in tabs]
    out = {h: tf.dram_health(h) for h in tabs}
    if out:
        log("bench: DRAM preflight %s" % ", ".join("%s %s" % (h, v) for h, v in out.items()))
    return out


def bench_resume(path, spec):
    """{run id: rows} for runs the order's checkpoint already holds COMPLETE (a link run: 1 row; a bench run: its
    expected count). Anything short is re-run. A missing or unreadable file is a fresh order."""
    by = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                by.setdefault(r.get("run"), []).append(r)
    except OSError:
        return {}
    keep = {}
    for run in spec["runs"]:
        got = by.get(run["id"], [])
        if got and len(got) == (1 if "link_mb" in run else bench_expected(run)):
            keep[run["id"]] = got
    return keep


def run_bench(spec, log, progress, stop_event=None, tick=None, adb_sh=None, live=None):
    """Every run in order. Returns (rows, errors by run id). A run whose result count differs from what its
    arguments ask for is an ERROR with no rows: "measured less" must never read as "measured". Each finished run's
    rows go to the order's checkpoint as they land (live numbers; a requeue keeps them)."""
    rows, errors = [], {}
    runs = spec["runs"]
    ckpt = os.path.join(tf.STATE_DIR, "%s.checkpoint.jsonl" % spec["name"])
    kept = bench_resume(ckpt, spec)
    if kept:
        log("bench: resuming %s - %d finished run(s) kept from its checkpoint" % (spec["name"], len(kept)))
    dram = stage_dram_check(spec, kept, log)
    for i, run in enumerate(runs):
        if stop_event is not None and stop_event.is_set():
            break
        rid, where = run["id"], run["where"]
        if rid in kept:
            rows += [dict(r, resumed=True) for r in kept[rid]]
            progress(i + 1, len(runs), rid)
            continue
        first = len(rows)
        if tick is not None:
            tick("%s on %s" % (rid, where))
        try:
            if "link_mb" in run:
                rows.append(dict(link_throughput(where, run["link_mb"]), run=rid, where=where, test="link"))
            else:
                extra = {}
                if "stages" in run:
                    sick = {s["dev"]: dram.get(s["dev"]) for s in run["stages"]
                            if not is_local_stage(s["dev"]) and not _dram_ok(dram.get(s["dev"]))}
                    if sick:
                        raise RuntimeError("DRAM preflight: %s - degraded or unmeasured (the lab's 48 MB gather probe, "
                                           "healthy >= %.1f M/s); cure is a reboot, Fellowship-Operations § DRAM State"
                                           % (sick, tf.DRAM_MIN_MGATHER))
                    extra["dram_mgather"] = {s["dev"]: dram.get(s["dev"]) for s in run["stages"]
                                             if not is_local_stage(s["dev"])}
                    if adb_sh is None:
                        from tablet_control import _adb_root_sh as adb_sh     # the kills need ROOT, as Voltron's
                    res, staged = run_stages(run, log, adb_sh, stop_event,
                                             live=(lambda text, r=rid: live(r, text)) if live else None,
                                             order_name=spec["name"])
                    extra.update(staged)
                elif where == "rigby" and run.get("imatrix"):
                    head, env = bench_head(run)
                    dev = (vulkan_device(bench_vk_dir(run), run.get("gpu", "gt730")) if uses_card(run) else None)
                    out = imatrix_out_path(spec["name"], run)
                    rc, out_text, err = run_stoppable(
                        imatrix_argv(run, head, os.path.join(MODELS, run["model"]), out, dev), env, stop_event,
                        beat=(lambda s, r=rid: live(r, "%dm%02ds" % (s // 60, s % 60))) if live else None)
                    res, extra["imatrix"] = parse_imatrix((out_text or "") + (err or ""), rc, out)
                elif where == "rigby" and (run.get("kld") or run.get("kld_base")):
                    head, env = bench_head(run)
                    os.makedirs(KLD_DIR, exist_ok=True)
                    rc, out, err = run_stoppable(
                        kld_argv(run, head, os.path.join(MODELS, run["model"]), kld_base_path(spec["name"], run)),
                        env, stop_event, beat=(lambda s, r=rid: live(r, "%dm%02ds" % (s // 60, s % 60))) if live else None)
                    res, extra["kld"] = parse_kld((out or "") + (err or ""), rc, bool(run.get("kld")))
                elif where == "rigby":
                    head, env = bench_head(run)
                    dev = (vulkan_device(bench_vk_dir(run), run.get("gpu", "gt730")) if uses_card(run) else None)
                    beat, on_line = bench_live(live, rid, bench_expected(run)) if live else (None, None)
                    rc, out, err = run_stoppable(
                        bench_argv(run, os.path.join(MODELS, run["model"]), dev), env, stop_event,
                        beat=beat, on_line=on_line)
                    res = parse_bench(out, rc, err)
                else:
                    stage_tablet_bench(where, run["model"], log)
                    rc, out = tf._ssh(where, tablet_bench_cmd(run), timeout=None)
                    res = parse_bench(out, rc)
                want = bench_expected(run)
                if len(res) != want:
                    raise RuntimeError("expected %d llama-bench results, got %d" % (want, len(res)))
                for x in res:
                    rows.append(dict({k: x.get(k) for k in BENCH_KEEP}, run=rid, where=where, test="bench",
                                     model=run["model"], gpu_layers=run.get("gpu_layers"),
                                     op_offload=run.get("op_offload"), build=run.get("build") or "live",
                                     ub=run.get("ub"), **extra))
        except BenchStopped:
            log("bench: %s STOPPED mid-run by a slip - not recorded" % rid)
            break
        except Exception as e:                                               # noqa: BLE001
            errors[rid] = "%s: %s" % (type(e).__name__, str(e)[:300])
            log("bench: %s FAILED - %s" % (rid, errors[rid]))
        for r in rows[first:]:
            checkpoint(ckpt, r)
        progress(i + 1, len(runs), rid)
    for f in glob.glob(os.path.join(KLD_DIR, "%s.*.kld" % spec["name"])):    # a byproduct: ~1 GB per base
        try:
            os.remove(f)
        except OSError:
            pass
    return rows, errors


# --- the order ------------------------------------------------------------------------------------------------

def publish_models(path=None, models_dir=None):
    """rigby's GGUFs (projectors excluded) for desks that cannot see rigby's disk, e.g. the menu filler.
    Rewritten only when the set changes. Returns the list, or None when it could not publish - never raises."""
    path, models_dir = path or MODELS_MANIFEST, models_dir or MODELS
    try:
        models = sorted(f for f in os.listdir(models_dir) if f.endswith(".gguf") and not f.startswith("mmproj"))
        try:
            with open(path, encoding="utf-8") as fh:
                if json.load(fh).get("models") == models:
                    return models
        except (OSError, ValueError):
            pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"host": "rigby", "models": models, "at": time.strftime("%Y-%m-%d %H:%M:%S")}, fh, indent=1)
        os.replace(tmp, path)
        return models
    except Exception:                                                   # noqa: BLE001
        return None


def items_for(spec):
    """Every task at every replicate, or exactly the [task, replicate] pairs in `items` (a re-run of the pairs a
    stopped order never finished: same seed, since the seed IS the replicate)."""
    if spec.get("items"):
        return [(t, int(r)) for t, r in spec["items"]]
    return [(t, r + 1) for r in range(int(spec.get("replicates", 1))) for t in spec["tasks"]]


def run_arm(arm, spec, mod, work, hosts, log, progress, stop_event=None, adb_sh=None, tick=None, streaming=None):
    """One arm end to end. Returns (rows, meta). Backends and the server are cleaned up on EVERY route.
    Every row is checkpointed as it lands; `streaming(arm, task, rep, n_chunks)` reports an answer in flight;
    state/llm_<arm>.live.txt holds the answer being written. A stop slip cuts the answer in flight."""
    if arm == "warm":
        return run_warm(spec, mod, work, hosts, log, progress, stop_event=stop_event, adb_sh=adb_sh, tick=tick,
                        streaming=streaming)
    if arm == "rigby":
        spec = launch_spec(spec)       # the model's production profile under the order (rigby_profiles)
    model_path = os.path.join(MODELS, spec["model"])
    ctx = int(spec.get("ctx", getattr(mod, "CONTEXT", 2048)))
    temperature = float(spec.get("temperature", getattr(mod, "TEMPERATURE", 0.6)))
    # speculative decoding is a rigby-arm knob: Voltron's bottleneck is the USB hops, not memory bandwidth
    draft = os.path.join(MODELS, spec["draft_model"]) if (spec.get("draft_model") and arm == "rigby") else None
    draft_host = spec.get("draft_host") if (draft and arm == "rigby") else None
    meta = {"arm": arm, "model": spec["model"], "ctx": ctx, "temperature": temperature, "nodes": [],
            "draft_model": spec.get("draft_model") if draft else None, "reasoning": spec.get("reasoning"),
            "draft_host": draft_host, "draft_device": None,
            "spec_type": (spec.get("spec_type") or ("draft-simple" if draft else None)) if arm == "rigby" else None,
            "gpu_layers": spec.get("gpu_layers") if arm == "rigby" else None,
            "override_tensor": spec.get("override_tensor") if arm == "rigby" else None,
            "op_offload": spec.get("op_offload") if arm == "rigby" else None,
            "gpu": spec.get("gpu", "gt730") if arm == "rigby" else None,
            "fit_target": spec.get("fit_target") if arm == "rigby" else None,
            "build": spec.get("build") if arm == "rigby" else None,
            "parallel": spec.get("parallel") if arm == "rigby" else None,
            "ubatch": spec.get("ubatch") if arm == "rigby" else None,
            "kepler_flags": bool(spec.get("kepler_flags")) if arm == "rigby" else None,
            "threads": spec.get("threads") if arm == "rigby" else None,
            "load_mode": spec.get("load_mode") if arm == "rigby" else None,
            "draft_max": spec.get("draft_max") if arm == "rigby" else None,
            "op_offload_min_batch": spec.get("op_offload_min_batch") if arm == "rigby" else None,
            "engine_env": spec.get("engine_env") if arm == "rigby" else None,
            "expert_hot": spec.get("expert_hot") if arm == "rigby" else None,
            "moe_cache_mib": spec.get("moe_cache_mib") if arm == "rigby" else None}
    rows, proc, started = [], None, []
    log_path = os.path.join(tf.STATE_DIR, "llm_%s.log" % arm)
    live_path = os.path.join(tf.STATE_DIR, "llm_%s.live.txt" % arm)
    ckpt = os.path.join(tf.STATE_DIR, "%s.checkpoint.jsonl" % spec["name"])
    lane = tf.lane_name()           # an agentic payload calls live() from Inspect's threads: keep this lane's claim
    ceiling = spec.get("price_ceiling_s")
    if ceiling is None and arm == "rigby" and is_agentic(mod):
        ceiling = RIGBY_A1_CEILING_S
    quick = arm == "rigby"             # #34 B11 quick resume: rigby's own server dumps on a stop and restores on start
    try:
        if quick:
            os.makedirs(slots_dir(), exist_ok=True)
            RESUME.update(name=spec["name"], launch=resume_launch(spec, ctx), cut=None, restored=None, log=log,
                          dumped=False)
            prune_resumes(spec["name"], queued_names(), log)
        if is_agentic(mod) and spec.get("reasoning") not in ("on", "off"):
            raise RuntimeError("an agentic order must state reasoning on|off: the lab provider refuses a silent "
                               "default and the server launches with the same value")
        rpc = None
        if arm == "voltron":
            stale = kill_backends(hosts, adb_sh)
            if stale:
                raise RuntimeError("rpc port still held after adb kill on %s" % ", ".join(stale))
            started = start_backends(hosts, log)
            meta["nodes"] = started
            if len(started) < len(hosts):
                log("voltron: backends up on %d/%d (%s missing)" % (len(started), len(hosts),
                                                                 ", ".join(sorted(set(hosts) - set(started)))))
            if not started:
                raise RuntimeError("no rpc backend came up")
            rpc = ["%s:%d" % (_wire_ip(h), RPC_PORT) for h in started]
        draft_rpc = None
        if draft_host:
            stale = kill_backends([draft_host], adb_sh)
            if stale:
                raise RuntimeError("rpc port still held after adb kill on %s" % draft_host)
            if not start_backends([draft_host], log, all_cores=True):
                raise RuntimeError("draft rpc-server did not come up on %s" % draft_host)
            endpoint = "%s:%d" % (_wire_ip(draft_host), RPC_PORT)
            devs, tail = list_rpc_devices(endpoint)
            if len(devs) != 1:
                raise RuntimeError("expected exactly 1 RPC device for %s, found %d: %s" % (endpoint, len(devs), tail))
            draft_rpc = (endpoint, devs[0])
            meta["draft_device"] = devs[0]
        proc, meta["load_s"] = start_server(model_path, ctx, rpc, log_path, stop_event, tick=tick, draft=draft,
                                            reasoning=spec.get("reasoning"), spec_type=meta["spec_type"],
                                            gpu_layers=meta["gpu_layers"], override_tensor=meta["override_tensor"],
                                            op_offload=meta["op_offload"], draft_rpc=draft_rpc,
                                            gpu=meta["gpu"] or "gt730", fit_target=meta["fit_target"],
                                            build=meta["build"], parallel=meta["parallel"],
                                            ubatch=meta["ubatch"], kepler_flags=bool(meta["kepler_flags"]),
                                            report=lambda fp: meta.__setitem__("fit_placement", fp),
                                            threads=meta["threads"], load_mode=meta["load_mode"],
                                            draft_max=meta["draft_max"],
                                            op_offload_min_batch=meta["op_offload_min_batch"],
                                            engine_env=meta["engine_env"], expert_hot=meta["expert_hot"],
                                            moe_cache_mib=meta["moe_cache_mib"],
                                            slot_save_path=slots_dir() if quick else None)
        if quick:
            qr = prepare_resume(log)
            if qr:
                meta["quick_resume"] = {"pending_token": qr.get("pending_token"), "saved_tokens": qr.get("saved_tokens"),
                                        "continuable": bool((qr.get("cut") or {}).get("continuable")),
                                        "dumped_at": qr.get("at")}
        if meta.get("fit_placement"):
            fp = meta["fit_placement"]
            log("%s: fitter placement %s" % (arm, fp.get("error") or " | ".join(fp.get("lines", [])[-6:])))
        meta["engine_report"] = engine_report(log_path)
        if meta["moe_cache_mib"]:
            log("%s: moe_cache_mib %s asked; the server reported: %s" % (
                arm, meta["moe_cache_mib"], " | ".join(meta["engine_report"]) or "NOTHING about an MoE cache"))
        log("%s: %s loaded in %ss%s" % (arm, spec["model"], meta["load_s"],
                                         (" across rigby + %d tablets" % len(started)) if rpc else
                                         (" on rigby, draft on %s (%s)" % (draft_host, meta["draft_device"]))
                                         if draft_rpc else " on rigby alone"))
        ctl_name, control = control_of(mod)
        grammar = getattr(mod, "GBNF", None) if is_tool(mod) else None
        prior = resume_rows(ckpt, arm, spec, temperature)
        kept = [dict(prior[k], resumed=True) for k in items_for(spec) if k in prior]
        work_items = [(ctl_name, 1, control)] + [(t, r, mod.TASKS[t]) for t, r in items_for(spec)
                                                 if (t, r) not in prior]
        if kept:
            meta["resumed"] = len(kept)
            log("%s: resuming %s from its checkpoint - %d finished answer(s) kept, %d to run (control included)"
                % (arm, spec["name"], len(kept), len(work_items)))
        rows.extend(kept)
        revoid, flagged = set(), set()

        def alert(text):
            """One DM per verdict, the GPU factories' `ALERT:` vocabulary (a1_order prints the same lines)."""
            log("%s: ALERT: A1 %s: %s" % (arm, spec["name"], text))
            try:
                tf.notify_desk("ALERT: A1 %s: %s" % (spec["name"], text))
            except Exception:                                                # noqa: BLE001
                pass
        for i, (task, rep, tdef) in enumerate(work_items):
            if stop_event is not None and stop_event.is_set():
                meta["stopped"] = True
                break
            row = {"arm": arm, "task": task, "replicate": rep, "seed": rep, "model": spec["model"],
                   "temperature": temperature, "shape": SHAPE[arm], "nodes": len(started) if rpc else 0,
                   "draft_model": meta["draft_model"], "draft_host": meta["draft_host"],
                   "spec_type": meta["spec_type"], "reasoning": meta["reasoning"],
                   "gpu_layers": meta["gpu_layers"], "override_tensor": meta["override_tensor"],
                   "op_offload": meta["op_offload"], "moe_cache_mib": meta["moe_cache_mib"],
                   "engine_report": meta["engine_report"]}
            def live(answer, thinking, n, elapsed, task=task, rep=rep):
                with tf.as_lane(lane):
                    write_live(live_path, "%s  %s r%d  %d tokens  %ds  (%s)" % (
                        spec["name"], task, rep, n, elapsed, time.strftime("%H:%M:%S")), answer, thinking)
                    if streaming is not None:
                        streaming(arm, task, rep, n)
            try:
                if is_agentic(mod):
                    res = mod.run_agentic(task, tdef, spec=spec, ctx=ctx, temperature=temperature, seed=rep,
                                          stop_event=stop_event, live=live,
                                          ceiling_s=None if task.startswith("control") else ceiling,
                                          log_dir=os.path.join(tf.RESULTS_DIR, "inspect", spec["name"]))
                    if res.pop("silent", False) and not engine_alive():
                        raise TimeoutError("agentic run went silent (%s)" % res.get("error"))
                    if res.get("inspect_log"):
                        res["inspect_shipped"] = ship_inspect_log(res["inspect_log"], spec["name"])
                    row.update(res, ran=True)
                    if res.get("finish_reason") in CUT:
                        row.update(ran=False, ok=False, **{res["finish_reason"]: True})
                    elif res.get("void"):
                        # measured the instrument, not the model (a1_validity): never answered, never scored
                        row.update(ran=False, ok=False)
                        if (task, rep) not in revoid:
                            revoid.add((task, rep))
                            work_items.append((task, rep, tdef))
                            alert("%s VOID (%s) - running it once more" % (task, res["void"]))
                        else:
                            alert("%s VOID twice (%s) - recorded as void, not scored" % (task, res["void"]))
                    for flag in (mod.run_flags([r for r in rows if r.get("ran")] + [row], spec)
                                 if hasattr(mod, "run_flags") else []):
                        if flag not in flagged:
                            flagged.add(flag)
                            alert(flag)
                else:
                    # a priced-out control would read as a FAILED control and void the order it exists to validate
                    text, wall, tm = complete(prompt_of(mod, tdef), rep, temperature, stop_event=stop_event,
                                              live=live, ceiling_s=None if task.startswith("control") else ceiling,
                                              grammar=grammar)
                    row.update(tm, wall_s=wall, answer=text, ran=True)
                    if tm.get("finish_reason") in CUT:
                        # cut short: not a graded answer - re-run (stopped) or reported at its price (priced_out)
                        row.update(ran=False, ok=False, checks_passed=0, checks_total=n_checks(tdef),
                                   error="%s after %ss" % (tm["finish_reason"], wall),
                                   **{tm["finish_reason"]: True})
                    else:
                        # a thinking model can file its whole answer under thinking and leave content empty
                        ans, field = ((text, "content") if (text or "").strip()
                                      else (tm.get("thinking") or "", "thinking"))
                        row.update(grade(mod, work, tdef, ans), graded_field=field)
            except Exception as e:                                           # noqa: BLE001
                if _silence(e) and not engine_alive():
                    # held, not failed: stopped with its checkpoint, the silent answer NOT recorded, so a requeue
                    # (fresh server) re-asks it and keeps every finished answer
                    meta["stopped"] = True
                    meta["engine_hung"] = ("%s r%d got no token for %ds and a 4-token probe got nothing in %ds - "
                                           "llama-server hung; requeue to resume on a fresh server"
                                           % (task, rep, IDLE_S, PROBE_S))
                    log("%s: ENGINE HUNG - %s" % (arm, meta["engine_hung"]))
                    break
                row.update(ran=False, ok=False, checks_passed=0, checks_total=n_checks(tdef),
                           error="%s: %s" % (type(e).__name__, str(e)[:200]))
            rows.append(row)
            checkpoint(ckpt, row)
            progress(arm, len(kept) + i + 1, len(kept) + len(work_items), row)
            if row.get("stopped"):
                meta["stopped"] = True
                break
            braked = brake_verdict(rows, ctx, spec.get("at_cap_brake"))
            if braked:
                meta["braked"] = braked
                log("%s: AT-CAP BRAKE - %s; the rest of this arm is not run" % (arm, braked["why"]))
                break
            early = early_stop_verdict(rows, spec, mod) if is_agentic(mod) else None
            if early:
                meta["stopped_early"] = early
                log("%s: STOPPED EARLY - %s" % (arm, early["why"]))
                break
            if (is_agentic(mod) and hasattr(mod, "lead_alone")
                    and mod.lead_alone([r for r in rows if r.get("ran")], spec.get("team"))):
                # a team arm told to delegate that hands nothing measures the lead alone, not its question (the run
                # flag already ALERTed; the GPU arms stop on the same a1_validity.lead_alone)
                meta["stopped_lead_alone"] = {"why": "the team arm delegated nothing on its last 3 exercises"}
                log("%s: STOPPED - %s" % (arm, meta["stopped_lead_alone"]["why"]))
                break
    except Exception as e:                                                   # noqa: BLE001
        meta["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        try:
            stop_server(proc)
        except Exception as e:                                               # noqa: BLE001
            meta.setdefault("error", "stop_server: %s" % e)
        if quick:
            # keep a dump only for a stop that MADE one (the requeue continues from it); a finished order, or a stop
            # between answers after the restored dump was spent, retires it
            if RESUME.get("restored") and "quick_resume" in meta:
                meta["quick_resume"].update(RESUME["restored"])        # restored right before the cut call
            if not (meta.get("stopped") and RESUME.get("dumped")):
                retire_resume(spec["name"], "spent or not needed" if RESUME.get("accepted") else None, log)
            RESUME.update(name=None, launch=None, cut=None, restored=None, accepted=None, log=None, dumped=False)
        if meta.get("engine_env"):
            meta["engine_log_tail"] = log_tail_lines(log_path, ENGINE_LOG_LINES)
        backends = hosts if arm == "voltron" else [draft_host] if draft_host else []
        if backends and adb_sh is not None:
            try:
                meta["backends_left_listening"] = kill_backends(backends, adb_sh)
            except Exception as e:                                           # noqa: BLE001
                meta["cleanup_error"] = "%s: %s" % (type(e).__name__, e)
    return rows, meta
