"""LOSSLESS mid-answer resume on a GPU factory's ollama engine (#34 B11b stage 2 proof; rigby's B11 test, raw-completion form).

    engine_resume_probe.py <model> <num_ctx> {ORDER_OUT} [--n 160] [--cut 60] [--temperature 0.6] [--seed 7]

ollama serves the model through its bundled llama-server; this probe talks to that runner's own 127.0.0.1 port (found
from its command line, as helper_window.runner_port), so it tests the ENGINE ollama runs, with ollama's flags (MTP where
the box's model has it):
  A  reference: n tokens straight through (fresh slot, fixed seed and temperature)
  B  the same request cut after `cut` tokens; the slot SAVED (lab #0 reports the pending token, #3 the replay flag and
     MTP carry, #2 the sampler's random state)
  -  the model EVICTED through ollama (keep_alive 0) and reloaded - a new runner process, an empty card
  C  restore, then continue with prompt = the saved state + the pending token and `lab_resume` (lab #3's next step)
  D  control: restore again, the same continue WITHOUT lab_resume
Lossless = B + C equals A, token for token, past the cut. Writes {ORDER_OUT}\\resume.json; installs and changes nothing."""
import argparse
import glob
import hashlib
import json
import os
import re
import socket
import shutil
import subprocess
import time
import urllib.request
import uuid

OLLAMA = "http://127.0.0.1:11434"
SLOT = "lab-resume-probe.bin"


def post(url, body, timeout=900):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def runner_port():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | ForEach-Object { "
                          "$_.ExecutablePath + ' ' + $_.CommandLine }"], capture_output=True, text=True,
                         errors="replace", timeout=60).stdout
    lines = [l for l in out.splitlines() if "\\lib\\ollama\\" in l.lower().replace("/", "\\") or "ollama" in l.lower()]
    ports = sorted({int(p) for l in lines for p in re.findall(r"--port (\d+)", l)})
    return (ports[0] if len(ports) == 1 else None), lines


def load(model, num_ctx, draft_num_predict=None):
    """The model resident through ollama (a 1-token generate) -> its runner's port."""
    options = {"num_ctx": int(num_ctx), "num_predict": 1}
    if draft_num_predict is not None:
        options["draft_num_predict"] = draft_num_predict
    post(OLLAMA + "/api/generate", {"model": model, "prompt": "ok", "stream": False, "keep_alive": "10m", "options": options})
    for _ in range(30):
        port, lines = runner_port()
        if port:
            return port, lines
        time.sleep(1)
    raise SystemExit("no single ollama runner port after the load: %s" % lines)


def evict(model):
    post(OLLAMA + "/api/generate", {"model": model, "keep_alive": 0, "prompt": "", "stream": False})
    for _ in range(60):
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=10) as r:
            if not json.loads(r.read().decode()).get("models"):
                return
        time.sleep(1)


def gen(port, prompt_ids, n, a):
    r = post("http://127.0.0.1:%d/completion" % port,
             dict({"prompt": prompt_ids, "n_predict": n, "temperature": a.temperature, "seed": a.seed,
                   "id_slot": 0, "cache_prompt": True, "return_tokens": True, "stream": False, "n_probs": a.probs,
                   "post_sampling_probs": a.post}, **a.extra))
    return r.get("tokens") or [], r.get("timings") or {}, r


def gen_hang_up(port, prompt_ids, n, stop_after, a):
    """The cut the way a real interruption happens: ask for the whole answer, stream it, hang up after `stop_after` tokens. Asking for exactly `cut` tokens instead limits the last
    drafted batch at the cut, which an uninterrupted run never does, and the engine's rounding follows batch shape."""
    body = {"prompt": prompt_ids, "n_predict": n, "temperature": a.temperature, "seed": a.seed, "id_slot": 0, "cache_prompt": True,
            "return_tokens": True, "stream": True, "n_probs": a.probs, "post_sampling_probs": a.post}
    req = urllib.request.Request("http://127.0.0.1:%d/completion" % port, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    tokens = []
    with urllib.request.urlopen(req, timeout=900) as r:
        for line in r:
            if line.startswith(b"data: "):
                tokens += json.loads(line[6:]).get("tokens") or []
                if len(tokens) >= stop_after:
                    break
    return tokens


def numbers(r):
    """Each generated token's numbers: its id and the engine's top candidates. Log probabilities straight from the model, or with --post the
    probabilities the sampler chain left: same logits with different post-chain numbers mean a sampler step holds state a restore did not bring back."""
    return [{"id": x.get("id"), "top": {t.get("id"): (t["prob"] if "prob" in t else t.get("logprob")) for t in (x.get("top_probs") or x.get("top_logprobs") or [])}}
            for x in r.get("completion_probabilities") or []]


def compare_numbers(x, y):
    """Two runs of the same tokens, number by number. Token ids can match for a long way while the numbers beneath them have already moved, and
    a token only changes when the movement crosses a sampling boundary: this is the instrument for WHERE they moved. Stops at the first different token, past which the
    two runs are reading different text and their numbers mean nothing against each other."""
    first_moved, worst, compared, ids_differ, measured = None, 0.0, 0, None, 0
    for i, (p, q) in enumerate(zip(x, y)):
        if p["id"] != q["id"]:
            ids_differ = i
            break
        compared += 1
        if not (p["top"] and q["top"]):
            continue                 # no numbers on this row: with MTP drafting on the server records them for almost no token. Blank is not "unchanged".
        measured += 1
        shared = set(p["top"]) & set(q["top"])
        move = max((abs(p["top"][k] - q["top"][k]) for k in shared), default=0.0)
        if move > 0 or set(p["top"]) != set(q["top"]):
            if first_moved is None:
                first_moved = i
            worst = max(worst, move)
    return {"compared": compared, "rows_with_numbers": measured, "first_moved": first_moved, "largest_move": worst, "first_different_token": ids_differ}


def slot(port, action, **extra):
    return post("http://127.0.0.1:%d/slots/0?action=%s" % (port, action), dict({"filename": SLOT}, **extra))


def plain_history_control(a, prompt_ids):
    """Independent control: after a fresh load, no slot save/restore at all.

    Same serving settings, prompt, seed, and full prompt evaluation in every
    comparison. The older control ran after restored work and cannot isolate
    ordinary engine history from effects introduced by restore.
    """
    evict(a.model)
    port, lines = load(a.model, a.num_ctx, a.draft_num_predict)
    replies = {}
    for label, seed in (("reference", a.seed), ("immediate_repeat", a.seed),
                        ("different_answer", a.seed + 1000), ("after_different", a.seed)):
        slot(port, "erase")
        request = argparse.Namespace(**dict(vars(a), seed=seed, extra={"cache_prompt": False}))
        tokens, timing, response = gen(port, prompt_ids, a.n, request)
        replies[label] = {"tokens": tokens, "timings": timing, "numbers": numbers(response)}
    reference = replies["reference"]
    comparisons = {}
    for label in ("immediate_repeat", "different_answer", "after_different"):
        current = replies[label]
        comparisons[label] = {"identical": current["tokens"] == reference["tokens"],
            "first_diff": next((i for i, (x, y) in enumerate(zip(current["tokens"], reference["tokens"])) if x != y), None),
            "numbers": compare_numbers(reference["numbers"], current["numbers"])}
    return {"fresh_engine": True, "save_restore_calls": 0, "cache_prompt": False,
            "runner": lines, "replies": replies, "comparisons": comparisons}


def capture_prefill_state(a, port, runner, prompt_ids, slot_dir=None):
    """Use the existing slot export to locate history variation before decoding.

    This is a state diagnostic, not another resume verdict. The completed
    no-save/no-restore control remains the independent evidence for variation.
    Exporting state synchronizes the device; a null here cannot clear that issue.
    """
    command = ' '.join(runner)
    required = ('--spec-type draft-mtp', '--spec-draft-n-max 4', '--spec-draft-backend-sampling',
                '--cache-type-k q8_0', '--cache-type-v q8_0', '--flash-attn on', '-c 125952')
    if a.model != 'qwen3.8:27b' or a.num_ctx != 125952 or not all(x in command for x in required):
        raise RuntimeError('State capture requires the recorded 27B serving flags; nothing is changed to obtain them')
    if slot_dir is None:
        # The same launcher-derived location used by the existing slot-dump
        # probe and every live window; never guess C: versus F:.
        import sys
        root = os.environ.get('FOUNDRY_ROOT') or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sys.path.insert(0,os.path.join(root,'bin'))
        from helper_window import runner_slot_dir
        slot_dir = runner_slot_dir()
    report = {'box':socket.gethostname(), 'model':a.model, 'num_ctx':a.num_ctx, 'runner':runner,
              'temperature':a.temperature, 'seed':a.seed, 'prompt_ids':prompt_ids, 'probs':a.probs,
              'restore_calls':0, 'diagnostic_only':True, 'captures':[],
              'limitation':'Slot export synchronizes the device; no token-equivalence or resume verdict is inferred.'}
    nonce = uuid.uuid4().hex
    def digest(path):
        h = hashlib.sha256()
        with open(path,'rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
        return h.hexdigest()
    def persist():
        with open(os.path.join(a.out,'state_capture.json'),'w',encoding='utf-8') as stream:
            json.dump(report,stream,indent=2)
    for label in ('fresh_prefill','same_engine_prefill','after_other_answer'):
        if label == 'after_other_answer':
            slot(port,'erase')
            other = argparse.Namespace(**dict(vars(a),seed=a.seed+1000,extra={'cache_prompt':False}))
            _,timing,_ = gen(port,prompt_ids,a.n,other)
            report['intervening_answer'] = timing
        slot(port,'erase')
        request = argparse.Namespace(**dict(vars(a),extra={'cache_prompt':False}))
        tokens,timing,response = gen(port,prompt_ids,1,request)
        if timing.get('prompt_n') != len(prompt_ids) or timing.get('cache_n') != 0:
            raise RuntimeError('Capture did not read the complete same prompt; no state comparison is valid')
        filename = 'lab-prefill-'+nonce+'-'+label+'.bin'
        saved = slot(port,'save',filename=filename)
        source = os.path.join(slot_dir,filename)
        if not os.path.isfile(source):
            raise RuntimeError('Cannot identify the newly exported slot file; no original state is touched')
        files = sorted(glob.glob(source+'*'))
        needed = sum(os.path.getsize(p) for p in files)
        # Actual export size, with room for the remaining captures and their copies.
        if shutil.disk_usage(os.path.dirname(source)).free < 6*needed or shutil.disk_usage(a.out).free < 3*needed:
            raise RuntimeError('Insufficient capacity to preserve all state exports and copies')
        copied = {}
        for path in files:
            target = os.path.join(a.out,os.path.basename(path))
            if os.path.exists(target):raise RuntimeError('Refusing to overwrite a diagnostic artifact')
            shutil.copyfile(path,target)
            sha = digest(path)
            if digest(target) != sha:raise RuntimeError('State export copy failed hash verification')
            copied[os.path.basename(path)] = {'bytes':os.path.getsize(path),'sha256':sha,'source':path}
        report['captures'].append({'label':label,'tokens':tokens,'timings':timing,'numbers':numbers(response),
                                   'save':saved,'primary':filename,'files':copied})
        persist()
        # Follow the existing slot caller's cleanup contract. These are only
        # this mode's UUID exports, preserved verbatim in ORDER_OUT above.
        for path in files:
            if os.path.dirname(os.path.realpath(path)) != os.path.realpath(slot_dir) or not os.path.basename(path).startswith(filename):
                raise RuntimeError('Refusing cleanup outside the newly created export group')
            os.remove(path)
    slot(port,'erase')
    print('[state] Three prefill exports preserved; no restore or lossless verdict',flush=True)
    return report


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("num_ctx", type=int)
    ap.add_argument("out")
    ap.add_argument("--n", type=int, default=160)
    ap.add_argument("--cut", type=int, default=60)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fresh-load", action="store_true",
                    help="A and B each from a fresh model load, not one after the other in one engine. 2026-11-05: Mothership's 27B matched "
                         "itself in 16 arms from fresh loads, cold and hot, temperature 0 and 0.6, while this probe's same-engine arms "
                         "differ at token 15 (10-22, 11-05): the difference may be the arrangement and not the engine")
    ap.add_argument("--probs", type=int, default=8, help="how many top candidates' log probabilities to record per token: the numbers under the tokens")
    ap.add_argument("--post", action="store_true", help="record the probabilities after the sampler chain instead of the model's raw ones")
    ap.add_argument("--hang-up", action="store_true", help="cut by hanging up on a streamed whole answer, as a real interruption does, not by asking for exactly `cut` tokens")
    ap.add_argument("--repeat-control", action="store_true", help="run the whole answer a second time from a fresh load: does the engine repeat itself at all?")
    ap.add_argument("--continue", dest="continue_arm", action="store_true",
                    help="lab patch #4 (the mid-turn dump on the GPU boxes): restore with lab_continue, ask the SAME request again, "
                         "and the whole answer must come back identical - with the request's own seed and with another one - while "
                         "the same re-ask without lab_continue differs")
    ap.add_argument("--draft-num-predict", type=int, default=None, help="Diagnostic only: existing Ollama runner option, never a persistent model change")
    ap.add_argument("--history-control", action="store_true", help="Independent ordinary-history control from a fresh load, without any save/restore")
    ap.add_argument("--state-capture", action="store_true", help="Export first-prefill KV/recurrent state using the existing slot API; no resume verdict")
    a, _rest = ap.parse_known_args(argv)                        # the runner appends the order name: ignore it
    a.extra = {}
    if a.state_capture and (not a.fresh_load or a.draft_num_predict is not None or a.post):
        ap.error('State capture requires --fresh-load, original MTP settings and pre-sampling probabilities')
    os.makedirs(a.out, exist_ok=True)
    rep = {"box": socket.gethostname(), "model": a.model, "num_ctx": a.num_ctx, "n": a.n, "cut": a.cut,
           "temperature": a.temperature, "seed": a.seed}
    if a.fresh_load:
        # Evict stale runner state to ensure the fresh load uses the correct context configuration
        evict(a.model)
    port, lines = load(a.model, a.num_ctx, a.draft_num_predict)
    rep["runner"] = lines[0][:1800] if lines else None
    rep["diagnostic_draft_num_predict"] = a.draft_num_predict
    if a.draft_num_predict == 0 and any("--spec-type" in line for line in lines):
        raise SystemExit("Draft-off diagnostic was not applied; no valid measurement")
    rep["post_sampling"] = a.post
    prompt = ("Write a short story, about 300 words, about a lighthouse keeper who finds a message in a bottle. "
              "Use vivid detail.")
    P = post("http://127.0.0.1:%d/tokenize" % port, {"content": prompt, "add_special": True})["tokens"]
    rep["prompt_tokens"] = len(P)
    if a.state_capture:
        return capture_prefill_state(a,port,lines,P)
    slot(port, "erase")
    A, ta, ra = gen(port, P, a.n, a)
    rep["fresh_load"] = a.fresh_load
    rep["drafts"] = {"A": {k: ta.get(k) for k in ("draft_n", "draft_n_accepted", "predicted_n")}}
    if a.repeat_control:
        evict(a.model)
        port, _lines = load(a.model, a.num_ctx, a.draft_num_predict)
        slot(port, "erase")
        A2, ta2, _r2 = gen(port, P, a.n, a)
        rep["repeat_control"] = {"same": A2 == A, "first_diff": next((i for i, (x, y) in enumerate(zip(A2, A)) if x != y), None),
                                 "draft_n": ta2.get("draft_n"), "draft_n_accepted": ta2.get("draft_n_accepted")}
    if a.fresh_load:
        evict(a.model)
        port, _lines = load(a.model, a.num_ctx, a.draft_num_predict)
    slot(port, "erase")
    rep["hang_up"] = a.hang_up
    if a.hang_up:
        B, rb = gen_hang_up(port, P, a.n, a.cut, a), {}
        time.sleep(1)                # the server notices the hang-up between batches; the save below waits for the slot either way
    else:
        B, _t, rb = gen(port, P, a.cut, a)
    sv = slot(port, "save")
    if a.hang_up:
        # tokens the server finished after the hang-up never reached this client: the save says how far it got
        got = sv.get("n_saved", 0) - len(P) + 1
        rep["hang_up_received"], rep["hang_up_engine_reached"] = len(B), got
        if got > len(B) and B == A[:len(B)]:
            B = A[:got]
        elif got < len(B):
            B = B[:got]
        rep["cut"] = len(B)
    rep["save"] = {k: sv.get(k) for k in ("n_saved", "n_written", "pending_token", "lab_replay", "lab_rng_bytes",
                                          "lab_drafter_bytes", "timings")}
    pending = sv.get("pending_token")
    rep["reference_prefix_identical"] = B == A[:len(B)]
    # the pending token is B's LAST token - sampled and returned, not yet decoded into the cache (n_saved = P + B - 1)
    rep["pending_is_last_returned"] = (pending == B[-1]) if B else None
    right_after = None
    if a.continue_arm:
        # the commonest real park: the SAME server, straight after the cut (a turn parked and asked again in one window).
        # The saved file is not touched by it, and the reload below gives every later arm a fresh server.
        rs_ = slot(port, "restore", lab_continue=True)
        toks_, tim_, _r = gen(port, P, a.n, argparse.Namespace(**dict(vars(a), seed=a.seed + 1000, extra={})))
        right_after = (rs_, toks_, tim_)
    t = time.time()
    evict(a.model)
    port, _lines = load(a.model, a.num_ctx, a.draft_num_predict)
    rep["reload_s"] = round(time.time() - t, 2)
    # the cache holds P + B minus its last token (sampled, not yet decoded): that last token IS the pending one
    cont = P + B
    rest = a.n - len(B)
    rs = slot(port, "restore")
    rep["restore"] = {k: rs.get(k) for k in ("n_restored", "n_read", "lab_resume_armed", "timings")}
    a.extra = {"lab_resume": True}
    C, tc, rc = gen(port, cont, rest, a)
    rep["drafts"]["C"] = {k: tc.get(k) for k in ("draft_n", "draft_n_accepted", "predicted_n")}
    rep["lab_resume"] = {"read_tokens": tc.get("prompt_n"), "cache_n": tc.get("cache_n"),
                         "lossless": (B + C)[:a.n] == A[:a.n] and len(B + C) >= a.n - 1,
                         "first_diff": next((i for i, (x, y) in enumerate(zip(B + C, A)) if x != y), None)}
    slot(port, "restore")
    a.extra = {}
    D, td, rd = gen(port, cont, rest, a)
    rep["control_no_lab_resume"] = {"read_tokens": td.get("prompt_n"),
                                    "lossless": (B + D)[:a.n] == A[:a.n] and len(B + D) >= a.n - 1,
                                    "first_diff": next((i for i, (x, y) in enumerate(zip(B + D, A)) if x != y), None)}
    if a.continue_arm:
        # lab patch #4: the caller does not build a continuation. It restores with lab_continue and asks the SAME request
        # again (prompt P, the whole answer); the engine replays the cut part to the caller and goes on from the cut.
        def re_ask(armed, seed):
            rs_ = slot(port, "restore", **({"lab_continue": True} if armed else {}))
            a2 = argparse.Namespace(**dict(vars(a), seed=seed, extra={}))
            toks, tim, _r = gen(port, P, a.n, a2)
            return rs_, toks, tim

        def row(rs_, toks, tim):
            return {"armed": rs_.get("lab_continue_armed"), "identical": toks[:a.n] == A[:a.n] and len(toks) >= a.n - 1,
                    "first_diff": next((i for i, (x, y) in enumerate(zip(toks, A)) if x != y), None),
                    "tokens": len(toks), "read_tokens": tim.get("prompt_n"), "cache_n": tim.get("cache_n"),
                    "predicted_n": tim.get("predicted_n"), "predicted_ms": tim.get("predicted_ms"),
                    "draft_n": tim.get("draft_n"), "draft_n_accepted": tim.get("draft_n_accepted")}
        other = a.seed + 1000
        rep["lab_continue"] = {
            "reference_predicted_ms": ta.get("predicted_ms"),
            "same_seed": row(*re_ask(True, a.seed)),
            # ollama sends no fixed seed, so a real re-ask arrives with another one: the restored random state must carry it
            "other_seed": row(*re_ask(True, other)),
            # the control: NOT armed, another seed - an ordinary re-ask that writes a new answer. It must differ, or the
            # two rows above prove nothing (temperature 0 cannot fail it)
            "control_not_armed_other_seed": row(*re_ask(False, other))}
        # Server state (warm vs. freshly loaded) affects drift: "right_after_the_cut" is the warm state, "after_a_fresh_load" is the cold state.
        rep["lab_continue"]["right_after_the_cut"] = row(*right_after)
        evict(a.model)
        port, _lines = load(a.model, a.num_ctx, a.draft_num_predict)
        rep["lab_continue"]["after_a_fresh_load"] = row(*re_ask(True, other))
        # `same_seed` above differs from every other armed row in TWO things: its seed, and the DIFFERENT answer (the
        # not-resumed control) the server wrote just before it. These two rows hold one of them each.
        evict(a.model)
        port, _lines = load(a.model, a.num_ctx, a.draft_num_predict)
        rep["lab_continue"]["same_seed_after_a_fresh_load"] = row(*re_ask(True, a.seed))
        re_ask(False, other)                 # a different answer in the slot (the control's arrangement)
        rep["lab_continue"]["other_seed_after_a_different_answer"] = row(*re_ask(True, other))
        # The control for THAT row, with no park and no restore anywhere in it: the reference request itself (same
        # prompt, same seed, an erased slot) asked of a server that has just written a different answer. If this
        # differs from the reference, the engine does not repeat itself across its own history, and a resume that
        # follows other work cannot be held to a standard an uninterrupted run does not meet.
        re_ask(False, other)
        slot(port, "erase")
        H, _th, _rh = gen(port, P, a.n, argparse.Namespace(**dict(vars(a), extra={"cache_prompt": False})))
        rep["lab_continue"]["plain_ask_after_a_different_answer"] = {
            "identical": H[:a.n] == A[:a.n], "tokens": len(H),
            "read_tokens": _th.get("prompt_n"), "cache_n": _th.get("cache_n"),
            "full_prompt_read": _th.get("prompt_n") == len(P),
            "first_diff": next((i for i, (x, y) in enumerate(zip(H, A)) if x != y), None)}
    na, nb, nc, nd = numbers(ra), numbers(rb), numbers(rc), numbers(rd)
    if a.hang_up:
        nb = [{"id": t, "top": {}} for t in B]       # a streamed cut keeps tokens only; the rows below stay aligned to the cut
    rep["tokens"] = {"A": A, "B": B, "C": C, "D": D}
    # positions in the two "resumed" rows count from the cut: position 0 is the first token after it
    rep["numbers_raw"] = {"A": na, "C": nc}
    rep["numbers"] = {"recorded": [len(na), len(nb), len(nc), len(nd)],
                      "fresh_vs_fresh_before_the_cut": compare_numbers(na[:len(nb)], nb),
                      "resumed_vs_reference_after_the_cut": compare_numbers(na[len(nb):], nc),
                      "control_vs_reference_after_the_cut": compare_numbers(na[len(nb):], nd)}
    slot(port, "erase")
    if a.draft_num_predict is not None:
        evict(a.model)  # no diagnostic runner settings linger for subsequent normal work
    for folder in (r"F:\HAI\slots", r"C:\ProgramData\HAI\slots"):      # wherever this box's launcher sends slot saves
        try:
            os.remove(os.path.join(folder, SLOT))
        except OSError:
            pass
    # Verdict is void if the cut run diverges from the reference before the cut, or if the save lacks lab fields
    if not rep["reference_prefix_identical"]:
        rep["verdict"] = "VOID - the cut run differs from the reference before the cut (not reproducible)"
    elif pending is None:
        rep["verdict"] = "STOCK ENGINE - the slot save carries no lab fields"
    else:
        rep["verdict"] = "LOSSLESS" if rep["lab_resume"]["lossless"] else "NOT LOSSLESS"
    rep["raw_resume_verdict"] = rep["verdict"]
    if a.continue_arm and "lab_continue" in rep:
        lc = rep["lab_continue"]
        if not lc["same_seed"]["armed"]:
            lc["verdict"] = "NOT ARMED - this engine has no lab patch #4, or the save was not mid-answer"
        elif lc["control_not_armed_other_seed"]["identical"]:
            lc["verdict"] = "VOID - the control (not armed, another seed) also matched: this arrangement cannot fail"
        else:
            arms = ("right_after_the_cut", "after_a_fresh_load", "same_seed", "other_seed",
                    "same_seed_after_a_fresh_load", "other_seed_after_a_different_answer")
            bad = [k for k in arms if not lc[k]["identical"]]
            lc["verdict"] = "CONTINUES LOSSLESS" if not bad else "NOT LOSSLESS in: " + ", ".join(
                "%s (differs at %s)" % (k, lc[k]["first_diff"]) for k in bad)
        print("[probe] %s %s: re-asking the same request after a restore: %s (control differs at %s)" % (
            rep["box"], a.model, lc["verdict"], lc["control_not_armed_other_seed"]["first_diff"]), flush=True)
    if a.continue_arm:
        rep["verdict"] = rep["lab_continue"]["verdict"]
    if a.history_control:
        rep["independent_plain_history_control"] = plain_history_control(a, P)
    with open(os.path.join(a.out, "resume.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    print("[probe] %s %s: %s (lab_resume read %s tokens; control lossless %s)" % (
        rep["box"], a.model, rep["verdict"], rep["lab_resume"]["read_tokens"], rep["control_no_lab_resume"]["lossless"]),
        flush=True)


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
