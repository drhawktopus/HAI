r"""Strata arm for the Flash-Next ladder. Measures the same workload as ladder.py (one warm-up, N x 500-token generations, 2 x prefill of ladder.LONG, telemetry window, bottleneck profile) against Strata's engine instead of llama-server. One variable: the engine. Prompts, telemetry, profile and frontier mode are imported from ladder.py.

    python strata_arm.py <ORDER_OUT> <plan.json> [order name, appended by the factory]
plan: {"strata": "<the Strata checkout>", "gguf_dir": "<its gguf folder>", "data_dir": "<its data folder>", "model": "Q2_0",
       "shards": {"<file name>": "<sha256>", ...}, "gens": 3, "port": 8080, "setup_args": ["--low-ram", "auto"],
       "prompts": [<varied prompts: one 500-token generation each, as ladder.py's varied arms>]}
Steps: verify shards (SHA-256) -> unzip source and HIP engine if absent -> Strata setup (--yes --no-start; fetches pinned llama.cpp, pip deps, ~5 GB MTP tensors, writes low-RAM experts file) -> unload ollama, frontier mode -> start Strata -> warm-up, generations, prefills -> stop it -> restore.
Strata has no ignore_eos, so output length cannot be forced: every generation's completion tokens are recorded and one under SHORT tokens is flagged (rate is still Strata's own timings.predicted_per_second, llama.cpp's field).
No clock on setup, load or answers: each waits while its process is alive."""
import glob, hashlib, json, os, socket, subprocess, sys, time, urllib.error, urllib.request, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ladder as L  # noqa: E402

SHORT = 400


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(16 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sidecar_path(gguf_dir):
    """One file per shard directory: the hashes already proven for the shards in it."""
    return os.path.join(gguf_dir, ".verified.json")


def _read_sidecar(gguf_dir):
    """The recorded hashes, or {} when there is none or it cannot be read. No record means verify in full: a
    sidecar is never trusted for what it does not say."""
    try:
        with open(_sidecar_path(gguf_dir), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def _write_sidecar(gguf_dir, rec):
    tmp = _sidecar_path(gguf_dir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1)
    os.replace(tmp, _sidecar_path(gguf_dir))


def verify_shards(plan, allow_cached=False, log=print):
    """Check every shard against the hash the plan names. A shard is accepted without re-hashing only when the
    sidecar records its size, its mtime_ns and a sha256 equal to the hash the plan wants, and the shard still has
    that size and mtime. Any other case - no record, a different size or mtime, no matching hash in the record, an
    unreadable sidecar - hashes the file in full. A mismatch raises either way. allow_cached is set only by the
    daily serving path; a measurement start passes nothing and hashes everything. Raises OSError if a named shard
    is missing: a plan never runs on a model the plan did not find."""
    gguf_dir = plan["gguf_dir"]
    rec = _read_sidecar(gguf_dir) if allow_cached else {}
    changed = False
    for name, want in plan["shards"].items():
        p = os.path.join(gguf_dir, name)
        st = os.stat(p)
        held = rec.get(name)
        trusted = isinstance(held, dict) and held.get("size") == st.st_size and \
            held.get("mtime_ns") == st.st_mtime_ns and held.get("sha256") == want
        if trusted:
            log("shard %s sha256 OK (verified earlier, unchanged)" % name, flush=True)
            continue
        got = sha256(p)
        log("shard %s sha256 %s" % (name, "OK" if got == want else "MISMATCH " + got), flush=True)
        assert got == want, "shard %s does not match its archive hash - refusing to measure a corrupt model" % name
        if not isinstance(held, dict) or (held.get("size"), held.get("mtime_ns"), held.get("sha256")) != \
                (st.st_size, st.st_mtime_ns, got):
            rec[name] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": got}
            changed = True
    if allow_cached and changed:
        _write_sidecar(gguf_dir, rec)


def extract(zpath, dest):
    """Unzip into dest, dropping a single shared top-level folder if the zip has one."""
    with zipfile.ZipFile(zpath) as zf:
        names = [n for n in zf.namelist() if n and not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        strip = (tops.pop() + "/") if len(tops) == 1 and all("/" in n for n in names) else ""
        for n in names:
            rel = n[len(strip):] if strip and n.startswith(strip) else n
            target = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(n) as src, open(target, "wb") as out:
                while True:
                    b = src.read(16 << 20)
                    if not b:
                        break
                    out.write(b)


def port_free(port):
    s = socket.socket()
    try:
        return s.connect_ex(("127.0.0.1", port)) != 0
    finally:
        s.close()


def chat(port, prompt, n, temperature=0.0):
    body = {"model": "strata", "messages": [{"role": "user", "content": prompt}], "max_tokens": n,
            "temperature": temperature, "stream": False}
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


NEEDLE = "The vault code for the east wing is 7391-QX."


def deep_text(lines, needle_at=0.5):
    """A long, non-repeating maintenance log of `lines` lines (~26 tokens each) with ONE needle line at `needle_at` of
    the depth. Every line differs (numbers), so no prefix cache or repetition shortcut can stand in for reading it."""
    out = []
    k = int(lines * needle_at)
    for i in range(lines):
        if i == k:
            out.append(NEEDLE)
        out.append("Record %05d: machine M-%05d serviced on day %d by technician T-%02d; part P-%03d replaced, "
                   "meter at %d hours." % (i, (i * 7919) % 99991, 1 + i % 365, i % 37, (i * 13) % 997, 1000 + i * 3))
    return "\n".join(out)


def deep_read(port, lines, salt=""):
    """Read a deep prompt and answer from its middle, thinking off (a retrieval, not a reasoning test): -> the read's
    prompt_n / prompt tok/s, the answer's write tok/s at that depth, and whether the needle came back. No request
    timeout (13a): Strata answers or refuses (over-context is a 400 with its own words). `salt` leads the prompt, so no
    cached prefix of an earlier read can stand in for this one."""
    body = {"model": "strata", "max_tokens": 64, "temperature": 0.0, "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": (salt + "\n" if salt else "") + deep_text(lines) +
                          "\n\nWhat is the vault code for the east wing? Answer with the code only."}]}
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req) as r:
        d = json.loads(r.read())
    t = d.get("timings") or {}
    ans = (((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    return {"lines": lines, "prompt_n": t.get("prompt_n"), "read_tok_s": t.get("prompt_per_second"),
            "write_tok_s": t.get("predicted_per_second"), "wall_s": round(time.time() - t0, 1),
            "answer": ans[:120], "needle_found": "7391-QX" in ans}


def ready(proc, port):
    while proc.poll() is None:
        try:
            chat(port, "ok", 4)
            return True
        except (OSError, ValueError, urllib.error.HTTPError):
            time.sleep(5)
    return False


def prepare(plan, out, res, save=lambda: None, trust_verified=False):
    """Verify the shards, unzip the source + HIP engine if absent, run Strata's own setup (--no-start). Writes
    setup.log under `out`; records engine_build / setup_s / setup_rc in `res`. Returns (bat, src). Raises on a bad
    shard or a failed setup. trust_verified is passed only by the daily serving path; a measurement start leaves it
    False and re-hashes every shard."""
    verify_shards(plan, allow_cached=trust_verified)
    root = plan["strata"]
    srczip = glob.glob(os.path.join(root, "Strata-v*-source.zip"))[0]
    with zipfile.ZipFile(srczip) as zf:
        src = os.path.join(root, zf.namelist()[0].split("/", 1)[0])
    if not os.path.exists(os.path.join(src, "START-HERE.bat")):
        print("unzipping the source into", src, flush=True)
        extract(srczip, src)
    eng = os.path.join(src, "engine")
    if not os.path.exists(os.path.join(eng, "BUILD.json")):
        print("unzipping the HIP engine into", eng, flush=True)
        extract(os.path.join(root, "strata-windows-x64-hip.zip"), eng)
    res["engine_build"] = json.load(open(os.path.join(eng, "BUILD.json"), encoding="utf-8"))
    res["server_py"] = install_server(plan, src)
    bat = os.path.join(src, "START-HERE.bat")
    setup = ["cmd", "/c", bat, "--yes", "--family", "qwen", "--model", plan["model"], "--backend", "hip",
             "--gguf-dir", plan["gguf_dir"], "--data-dir", plan["data_dir"], "--no-start", "--no-browser"] + \
        list(plan.get("setup_args", []))
    print("setup:", " ".join(setup[2:]), flush=True)
    t0 = time.time()
    with open(os.path.join(out, "setup.log"), "w", encoding="utf-8") as lf:
        rc = subprocess.call(setup, cwd=src, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    res["setup_s"], res["setup_rc"] = round(time.time() - t0, 1), rc
    save()
    tail = open(os.path.join(out, "setup.log"), encoding="utf-8", errors="replace").read().splitlines()[-6:]
    print("setup rc %s in %.0f s | %s" % (rc, res["setup_s"], " | ".join(tail)[-400:]), flush=True)
    assert rc == 0, "Strata setup failed (rc %s) - see setup.log" % rc
    return bat, src


def install_server(plan, src):
    """The plan's own server, when it names one: `server_py`, a file beside this one, over <src>\\serve\\server.py before the
    engine starts (the lab's mid-token resume, lab-engine/patches/strata_continue_patch.py). One plan says which server
    it was measured with, every launch checks the bytes, and a plan without the key leaves the install alone. -> the
    sha256 in force, or None when the plan names none. A named file that is missing raises: a plan never runs on a server it did not ask for."""
    own = plan.get("server_py")
    if not own:
        return None
    want = os.path.join(os.path.dirname(os.path.abspath(__file__)), own)
    live = os.path.join(src, "serve", "server.py")
    sha = sha256(want)
    if not os.path.exists(live) or sha256(live) != sha:
        with open(want, "rb") as fi, open(live + ".tmp", "wb") as fo:
            fo.write(fi.read())
        os.replace(live + ".tmp", live)
        print("the plan's server is in place: %s %s" % (own, sha[:16]), flush=True)
    return sha


def enter(frontier=True):
    """Frontier mode is exclusive to Strata and blocks live serving; pass frontier=False for daily use."""
    for m in json.loads(urllib.request.urlopen(L.OLLAMA + "/api/ps", timeout=30).read()).get("models", []):
        L.post(L.OLLAMA + "/api/generate", {"model": m["name"], "keep_alive": 0}, 120)
        print("unloaded ollama model", m["name"], flush=True)
    if not frontier:
        return
    print("frontier enter:", subprocess.run([sys.executable, L.FRONTIER, "enter"], capture_output=True,
                                            text=True).stdout[-120:], flush=True)


def launch(bat, src, port, out):
    """Start Strata's server (server.log under `out`) and wait while its process lives (no clock, 13a).
    Returns (proc, ok, load_s); ok False = it died before answering."""
    t0 = time.time()
    with open(os.path.join(out, "server.log"), "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(["cmd", "/c", bat, "--no-browser"], cwd=src, stdout=lf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
    ok = ready(proc, port)
    return proc, ok, round(time.time() - t0, 1)


def stop(proc, port):
    """Stop the server and restore frontier mode; must not raise."""
    try:
        if proc is not None and proc.poll() is None:
            stop_pid(proc.pid, port, restore=False)
            proc.wait()
    except Exception as e:                                                   # noqa: BLE001
        print("strata stop: %r" % (e,), flush=True)
    _restore()


def stop_pid(pid, port, restore=True):
    """stop() for a server this process did not start (strata_serve.py stop = llama-swap's cmdStop): Strata's unload,
    end the tree under `pid`, frontier restore. Never raises."""
    try:
        try:
            L.post("http://127.0.0.1:%d/unload" % port, {}, 120)
        except OSError:
            pass
        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True)
    except Exception as e:                                                   # noqa: BLE001
        print("strata stop_pid: %r" % (e,), flush=True)
    if restore:
        _restore()


def _restore():
    try:
        print("frontier restore:", subprocess.run([sys.executable, L.FRONTIER, "restore"], capture_output=True,
                                                  text=True).stdout[-120:], flush=True)
    except Exception as e:                                                   # noqa: BLE001
        print("frontier restore failed: %r" % (e,), flush=True)


def answer_mode(plan, port, out, res, effort_arm=None):
    """Runs the standard grading measurement for the given arm: executes all prompts in `plan["answer_prompts"]` once using the plan's sampler with thinking enabled. To ensure valid data collection for grading, requests must omit `max_tokens` and request timeouts. Any response ending with "length" or empty is considered void and excluded from grading. The output is written to `answers_<arm>.json` in the exact schema expected by the external grading script."""
    prompts = json.load(open(plan["answer_prompts"], encoding="utf-8"))["prompts"]
    if plan.get("only_prompts"):                 # a make-up run for prompts an earlier order did not reach (answers.py's)
        prompts = [p for p in prompts if p["id"] in plan["only_prompts"]]
        assert len(prompts) == len(plan["only_prompts"]), "only_prompts names an id the prompt file lacks"
    arm = plan.get("arm_name", "strata-" + plan["model"].lower())
    # Flash-Next GGUF defaults: enable_thinking True, reasoning_effort 'xhigh'.
    kwargs = {"enable_thinking": True}
    if effort_arm:
        arm = "%s-%s" % (arm, effort_arm["name"])
        kwargs = {"enable_thinking": bool(effort_arm.get("enable_thinking", True))}
        if effort_arm.get("reasoning_effort"):
            kwargs["reasoning_effort"] = effort_arm["reasoning_effort"]
    rec = {"arm": arm, "engine": "strata", "model": plan["model"], "setup_args": plan.get("setup_args", []),
           "sampler": plan.get("sampler", {}), "chat_template_kwargs": kwargs, "answers": []}
    path = os.path.join(out, "answers_%s.json" % arm)
    for p in prompts:
        body = dict(plan.get("sampler", {}), model="strata", stream=False,
                    messages=[{"role": "user", "content": p["prompt"]}], chat_template_kwargs=kwargs)
        t1 = time.time()
        req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            d = json.loads(r.read())
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        ans = {"id": p["id"], "kind": p.get("kind"), "content": msg.get("content") or "",
               "reasoning": msg.get("reasoning_content") or "", "finish_reason": ch.get("finish_reason"),
               "completion_tokens": (d.get("usage") or {}).get("completion_tokens"),
               "seconds": round(time.time() - t1, 1), "timings": d.get("timings")}
        ans["void"] = ans["finish_reason"] == "length" or not ans["content"].strip()
        rec["answers"].append(ans)
        tm = d.get("timings") or {}
        print("%s: %s %s | %s tokens | %.0f s | %s tok/s | answer %d chars%s"
              % (arm, p["id"], ans["finish_reason"], ans["completion_tokens"], ans["seconds"],
                 tm.get("predicted_per_second"), len(ans["content"]), " | VOID" if ans["void"] else ""), flush=True)
        # the deck's own progress protocol (p20_foundry COMPUTE_PROG_RE / COMPUTE_TOKS_RE): `[tag] N/M ... X tok/s`
        print("[strata] %d/%d %s %.1f tok/s void %d" % (len(rec["answers"]), len(prompts), p["id"],
              tm.get("predicted_per_second") or 0.0, sum(1 for a in rec["answers"] if a["void"])), flush=True)
        json.dump(rec, open(path, "w", encoding="utf-8"), indent=1)
    res["answers_file"] = os.path.basename(path)


def deep_write(port, lines, salt=""):
    """WRITE speed at depth: read the deep log (salted), then answer with the needle AND a ~250-word summary, thinking
    off, so the write rate is measured over hundreds of tokens at that depth (a 7-token answer's rate is not a rate -
    the first rounds have no draft yet). Adds the engine's own per-request stats from GET /metrics (expert-cache hit
    rate, PCIe share, RAM/file expert reads, drafts) - where the time at depth goes."""
    body = {"model": "strata", "max_tokens": 500, "temperature": 0.0, "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": (salt + "\n" if salt else "") + deep_text(lines) +
                          "\n\nFirst give the vault code for the east wing. Then write a 250-word plain-English summary "
                          "of what this maintenance log shows."}]}
    req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req) as r:
        d = json.loads(r.read())
    t, u = d.get("timings") or {}, d.get("usage") or {}
    ans = (((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
    out = {"lines": lines, "prompt_n": t.get("prompt_n"), "read_tok_s": t.get("prompt_per_second"),
           "write_tok_s": t.get("predicted_per_second"), "written": u.get("completion_tokens"),
           "wall_s": round(time.time() - t0, 1), "needle_found": "7391-QX" in ans}
    try:
        m = json.load(urllib.request.urlopen("http://127.0.0.1:%d/metrics" % port, timeout=10))
        last = (m.get("requests") or [{}])[0]
        out["engine"] = {k: last.get(k) for k in ("hit_rate", "pcie_share", "ram_blobs", "file_blobs", "file_mb",
                                                   "drafts_offered", "drafts_accepted", "decode_tok_s", "prompt_ms")}
    except (OSError, ValueError):
        out["engine"] = None
    return out


def band_png(colours, w=360, h=240):
    """A PNG of equal vertical colour bands, left to right, written with zlib alone (no image library on the box)."""
    import struct, zlib
    band = max(1, w // len(colours))
    row = b"\x00" + b"".join(bytes(colours[min(x // band, len(colours) - 1)]) for x in range(w))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * h, 9)) + chunk(b"IEND", b""))


# Two pictures whose answers are each other's reverse: a server that ignores the picture cannot get both right.
VISION_ARMS = [("red green blue", [(220, 30, 30), (30, 170, 60), (40, 70, 220)]),
               ("blue green red", [(40, 70, 220), (30, 170, 60), (220, 30, 30)])]


def vision_check(port):
    """Verifies the server parses and reads vision inputs (e.g., OpenAI `image_url` parts).

    Returns one row per test image: the requested colors, the returned text,
    whether the colors were named in order, response time, and prompt tokens."""
    import base64, re
    rows = []
    for want, colours in VISION_ARMS:
        img = base64.b64encode(band_png(colours)).decode()
        body = {"model": "strata", "max_tokens": 2000, "temperature": 0.0, "stream": False, "messages": [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img}},
                {"type": "text", "text": "This picture is three vertical colour bands. Name the three colours from "
                                         "left to right, in three words and nothing else."}]}]}
        req = urllib.request.Request("http://127.0.0.1:%d/v1/chat/completions" % port, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0, row = time.time(), {"want": want}
        try:
            with urllib.request.urlopen(req) as r:
                d = json.loads(r.read())
            text = (((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            said = re.findall(r"red|green|blue", text.lower())
            row.update(answer=text.strip()[:300], ok=said[:3] == want.split(),
                       prompt_n=(d.get("timings") or {}).get("prompt_n"),
                       prompt_tokens=(d.get("usage") or {}).get("prompt_tokens"))
        except urllib.error.HTTPError as e:                                      # the server's own reason, kept
            row.update(answer="", ok=False, error="HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:300]))
        except Exception as e:                                                   # noqa: BLE001
            row.update(answer="", ok=False, error="%s: %s" % (type(e).__name__, e))
        row["wall_s"] = round(time.time() - t0, 1)
        rows.append(row)
        print("vision: wanted %r, got %r -> %s in %s s" % (want, row.get("answer") or row.get("error"),
                                                            "OK" if row["ok"] else "NOT READ", row["wall_s"]), flush=True)
    return rows


def set_engine_args(cfg_path, overrides):
    """Set engine flags in Strata's run config (`args`) for one arm: {"--kv-resident": "131072"} replaces that flag's
    value (or appends the pair). The next setup run regenerates the config, so nothing outlives the order."""
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    args = cfg["args"]
    for flag, val in overrides.items():
        if flag in args:
            args[args.index(flag) + 1] = str(val)
        else:
            args += [flag, str(val)]
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), indent=1)
    return [args[i:i + 2] for i in range(len(args)) if args[i] in overrides]


def set_config_key(cfg_path, key, value):
    """One top-level key of Strata's run config (the server reads it; `args` are the engine's). -> the value written."""
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    cfg[key] = value
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"), indent=1)
    return value


def apply_serving(plan, src):
    """A SERVING plan's own launch settings, for every caller that serves from it (infer_ollama --strata, a1_order's
    Strata engine): plan["env"] into this process's environment (the server inherits it) and plan["engine_args"] into
    Strata's run config, after setup has (re)written it. Returns what was applied. Absent keys change nothing."""
    applied = {}
    env = {k: str(v) for k, v in (plan.get("env") or {}).items()}
    if env:
        os.environ.update(env)
        applied["env"] = env
    if plan.get("engine_args"):
        cfg = os.path.join(src, "strata-%s.json" % str(plan["model"]).lower())
        applied["engine_args"] = set_engine_args(cfg, plan["engine_args"])
    if plan.get("slot_save_path"):
        # the session files: POST /slots/0?action=save|restore (Strata docs\DETAILS.md "Session files"). The folder chat
        # windows already use on this box's fastest drive. Setup rewrites the config, so it goes in on every start.
        cfg = os.path.join(src, "strata-%s.json" % str(plan["model"]).lower())
        applied["slot_save_path"] = set_config_key(cfg, "slot_save_path", plan["slot_save_path"])
    if plan.get("vision"):
        # Windows/AMD: Strata setup leaves vision off, so this block must persist the encoder config on every start. The server auto-adds --vision flag when it detects this section.
        cfg = os.path.join(src, "strata-%s.json" % str(plan["model"]).lower())
        applied["vision"] = set_config_key(cfg, "vision", dict(plan["vision"]))
    return applied


def env_ab(plan, bat, src, port, out, res, save):
    """A/B of Strata launch ENVIRONMENTS, one variable: each arm of plan["env_arms"] ([{"name", "env": {...}}]) is its
    own launch (server log under <out>\\<arm>), then the same measurements on fresh, salted prompts: the deep reads of
    plan["ab_read_lines"] (e.g. [700, 90] ~ 32K and 4K tokens) and a write rate over plan["ab_write_prompts"] varied
    prompts. A file the env names (e.g. STRATA_HIPBLASLT_TUNING) is copied local first. Arms run back to back; the
    server is stopped between them by any route."""
    res["arms"] = {}
    cfg_path = os.path.join(src, "strata-%s.json" % str(plan["model"]).lower())
    cfg_orig = open(cfg_path, encoding="utf-8").read() if os.path.exists(cfg_path) else None
    for arm in plan["env_arms"]:
        name, env = arm["name"], dict(arm.get("env") or {})
        adir = os.path.join(out, name)
        os.makedirs(adir, exist_ok=True)
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update({k: str(v) for k, v in env.items()})
        proc, r = None, {"env": env}
        res["arms"][name] = r
        try:
            if cfg_orig is not None:            # every arm starts from setup's own config, plus its overrides
                open(cfg_path, "w", encoding="utf-8").write(cfg_orig)
            if arm.get("engine_args"):
                r["engine_args"] = set_engine_args(cfg_path, arm["engine_args"])
            proc, ok, r["load_s"] = launch(bat, src, port, adir)
            if not ok:
                r["result"] = "FAILED TO LOAD (rc %s)" % proc.returncode
                continue
            # the deck's LIVE rows (p20_foundry.EngineRates polls what an order declares - the same file
            # infer_ollama --strata and a1_order write), in the ORDER's folder, which the factory watches
            sa = [str(x) for x in plan.get("setup_args") or []]
            ctx = int(sa[sa.index("--context") + 1]) if "--context" in sa[:-1] else None
            with open(os.path.join(out, "engine_live.json"), "w", encoding="utf-8") as f:
                json.dump({"engine": "strata", "metrics": "http://127.0.0.1:%d/metrics" % port, "ctx": ctx}, f)
            time.sleep(12)
            chat(port, L.GEN, 64)                                              # warm-up, discarded
            r["reads"] = [deep_read(port, int(n), salt="arm %s read %d %s" % (name, i, time.time()))
                          for i, n in enumerate(plan.get("ab_read_lines") or [])]
            if plan.get("ab_deep_write_lines"):
                r["deep_write"] = deep_write(port, int(plan["ab_deep_write_lines"]),
                                             salt="arm %s deep write %s" % (name, time.time()))
                print("[ab] %s deep write: %s" % (name, r["deep_write"]), flush=True)
                save()
            writes = []
            for p in (plan.get("prompts") or [])[:int(plan.get("ab_write_prompts", 5))]:
                d = chat(port, "(%s) %s\n\n" % (name, p), 300)
                writes.append((d.get("timings") or {}).get("predicted_per_second"))
            ws = sorted(w for w in writes if w)
            r["writes"], r["write_median"] = writes, (ws[len(ws) // 2] if ws else None)
            r["result"] = "ok"
            print("[ab] %s: reads %s, write median %s" % (name, [(x["prompt_n"], x["read_tok_s"], x["needle_found"])
                                                                  for x in r["reads"]], r["write_median"]), flush=True)
        except Exception as e:                                                 # noqa: BLE001
            r["result"] = "FAILED: %r" % (e,)
            print("[ab] %s %s" % (name, r["result"]), flush=True)
        finally:
            stop(proc, port)
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            log = os.path.join(adir, "server.log")
            if os.path.exists(log):            # the engine's own words about the table (STRATA_HIPBLASLT_VERBOSE=1)
                r["hipblaslt_lines"] = [ln.strip()[:300] for ln in open(log, encoding="utf-8", errors="replace")
                                        if "hipblaslt" in ln.lower() or "fallbacks" in ln.lower()][:20]
            save()
    if cfg_orig is not None:                    # setup's own config back (the next setup run rewrites it anyway)
        open(cfg_path, "w", encoding="utf-8").write(cfg_orig)
    res["result"] = "ok" if all(a.get("result") == "ok" for a in res["arms"].values()) else "partial"


def main():
    out, plan = sys.argv[1], json.load(open(sys.argv[2], encoding="utf-8"))
    os.makedirs(out, exist_ok=True)
    port, gens_n = int(plan.get("port", 8080)), int(plan.get("gens", 3))
    res = {"engine": "strata", "plan": plan, "at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def save():
        json.dump(res, open(os.path.join(out, "strata.json"), "w", encoding="utf-8"), indent=1)

    bat, src = prepare(plan, out, res, save)
    assert port_free(port), "port %d is already in use - not starting a second server beside it" % port
    if plan.get("apply_serving"):               # measure the plan AS SERVED: its env and engine flags, as strata_serve applies them
        res["serving"] = apply_serving(plan, src)
        print("serving:", json.dumps(res["serving"]), flush=True)
    enter(frontier=bool(plan.get("frontier", True)))
    if plan.get("env_arms"):
        try:
            env_ab(plan, bat, src, port, out, res, save)
        finally:
            stop(None, port)                   # restores frontier mode (each arm already stopped its own server)
            save()
        return
    proc = None
    try:
        proc, ok, res["load_s"] = launch(bat, src, port, out)
        if not ok:
            tail = open(os.path.join(out, "server.log"), encoding="utf-8", errors="replace").read().splitlines()[-6:]
            res["result"] = "FAILED TO LOAD (rc %s): %s" % (proc.returncode, " | ".join(tail)[-400:])
            print(res["result"], flush=True)
            return
        time.sleep(12)
        res["vram_used_gb"], res["spill_gb"] = L.vram_used_gb(), L.spill_gb()   # recorded: Strata pins host memory by design
        chat(port, L.GEN, 64)                                                    # warm-up, discarded
        if plan.get("answer_prompts"):
            # one load, several thinking/effort arms back to back (load once, run many); none = the template default
            for arm in plan.get("effort_arms") or [None]:
                answer_mode(plan, port, out, res, arm)
            res["result"] = "ok"
            return
        sampler = L.start_sampler(os.path.join(out, "sys_strata.csv"))
        t_gen0 = time.strftime("%Y-%m-%d %H:%M:%S")
        gens, varied = [], []
        try:
            for _ in range(gens_n):
                d = chat(port, L.GEN, 500)
                t, u = d.get("timings") or {}, d.get("usage") or {}
                gens.append({"tok_s": t.get("predicted_per_second"), "tokens": u.get("completion_tokens"),
                             "short": (u.get("completion_tokens") or 0) < SHORT, "timings": t,
                             # content is null when the cap lands while the model is still thinking
                             "text": (((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "")[:2000]})
                print("gen: %s tok/s, %s tokens%s" % (gens[-1]["tok_s"], gens[-1]["tokens"],
                                                       "  SHORT" if gens[-1]["short"] else ""), flush=True)
            for p in plan.get("prompts", []):    # one 500-token generation per prompt, as ladder.py's varied arms
                d = chat(port, p + "\n\n", 500)
                t, u = d.get("timings") or {}, d.get("usage") or {}
                varied.append({"tok_s": t.get("predicted_per_second"), "tokens": u.get("completion_tokens"),
                               "short": (u.get("completion_tokens") or 0) < SHORT})
                print("varied: %s tok/s, %s tokens%s" % (varied[-1]["tok_s"], varied[-1]["tokens"],
                                                          "  SHORT" if varied[-1]["short"] else ""), flush=True)
                print("[strata] %d/%d varied %.1f tok/s" % (len(varied), len(plan["prompts"]),
                                                             varied[-1]["tok_s"] or 0.0), flush=True)
        finally:
            # the profile covers every measured generation; it may never replace the measurements -- keep the profile read-only and separate from the results
            try:
                t_gen1 = time.strftime("%Y-%m-%d %H:%M:%S")
                res["profile"] = {"sys": L.stop_sampler(sampler, os.path.join(out, "sys_strata.csv")),
                                  "gpu": L.gpu_window(t_gen0, t_gen1)}
                print(L.profile_line("strata", res["profile"]["sys"], res["profile"]["gpu"]), flush=True)
                for line in L.tree_lines("strata", res["profile"]["sys"], res["profile"]["gpu"]):
                    print(line, flush=True)
            except Exception as e:                                              # noqa: BLE001
                res["profile"] = "unavailable (%s: %s)" % (type(e).__name__, e)
                print("profile", res["profile"], flush=True)
        if varied:
            vs = sorted(v["tok_s"] for v in varied if v["tok_s"])
            res["varied"], res["varied_median_tok_s"] = varied, (vs[len(vs) // 2] if vs else None)
        if plan.get("vision_check"):
            res["vision"] = vision_check(port)
            res["vision_reads"] = all(r["ok"] for r in res["vision"])
            save()
        if plan.get("deep_read_lines"):        # the context really served: read to depth, answer from the middle
            res["deep_read"] = deep_read(port, int(plan["deep_read_lines"]))
            save()
            print("deep read: %(prompt_n)s tokens at %(read_tok_s)s tok/s, wrote at %(write_tok_s)s tok/s, needle "
                  "%(needle_found)s (%(answer)r), %(wall_s)s s" % res["deep_read"], flush=True)
        pres = []
        for i in range(2):                     # the varying part FIRST, so a prefix cache cannot reuse the prompt
            t = chat(port, "(%d) " % i + L.LONG, 1).get("timings") or {}
            pres.append({"tok_s": t.get("prompt_per_second"), "prompt_n": t.get("prompt_n"),
                         "cache_n": t.get("cache_n")})
        res.update({"gens": gens, "prefills": pres, "result": "ok",
                    "gen_tok_s": [g["tok_s"] for g in gens], "prefill_tok_s": [p["tok_s"] for p in pres]})
        warm = [g["tok_s"] for g in gens[1:] if g["tok_s"] and not g["short"]]
        res["gen_best_warm"] = max(warm) if warm else None
        print("strata %s | load %.0fs | VRAM %s GB | spill %s GB | gen %s | prefill %s | best warm %s"
              % (plan["model"], res["load_s"], res["vram_used_gb"], res["spill_gb"], res["gen_tok_s"],
                 res["prefill_tok_s"], res["gen_best_warm"]), flush=True)
    except Exception as e:                                                     # noqa: BLE001
        res["result"] = "ERROR %s: %s" % (type(e).__name__, e)
        print(res["result"], flush=True)
    finally:
        stop(proc, port)
        save()
        print("DONE", flush=True)
    return res.get("result")


if __name__ == "__main__":
    # an error or a failed load must reach the desk as a failure, never as a delivered measurement
    sys.exit(0 if main() == "ok" else 1)
