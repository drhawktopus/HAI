"""Quality ladder: bits per byte (BPB) of several models on ONE fixed corpus, so models with different tokenizers compare.

    python ppl.py <ORDER_OUT> <plan.json>

plan: {"corpus": <path>, "corpus_sha256": <hex>, "ctx": 2048, "arms": [{"name", "bin", "gguf", "args": [...]}, ...]}
Per arm: llama-tokenize counts the corpus's tokens for that model (vocab-only load), llama-perplexity scores the corpus
(second half of every ctx window, the tool's standard), and
    BPB = log2(PPL) * tokens / bytes
(the scored windows are a uniform sample of the corpus, so its token density stands in for theirs). Lower is better.
The corpus is copied into the order folder and its sha256 checked first: a different hash is a different experiment.
No timeouts: an arm ends when the tool ends; its log is in the order folder."""
import hashlib, json, math, os, re, shutil, subprocess, sys, time

OUT, PLAN = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
plan = json.load(open(PLAN, encoding="utf-8"))
data = open(plan["corpus"], "rb").read()
sha = hashlib.sha256(data).hexdigest()
assert sha == plan["corpus_sha256"], "corpus sha256 %s != plan %s" % (sha, plan["corpus_sha256"])
corpus = os.path.join(OUT, "corpus.txt")
shutil.copyfile(plan["corpus"], corpus)
nbytes = len(data)
ctx = int(plan.get("ctx", 2048))
print("corpus %d bytes, sha256 %s..., ctx %d" % (nbytes, sha[:16], ctx), flush=True)
results = []
for a in plan["arms"]:
    name, b = a["name"], a["bin"]
    t0 = time.time()
    tok = subprocess.run([os.path.join(b, "llama-tokenize.exe"), "-m", a["gguf"], "-f", corpus, "--ids", "--log-disable"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=b)
    m = re.search(r"\[([\d,\s]+)\]", tok.stdout)
    ntok = len(m.group(1).split(",")) if m else 0
    if ntok == 0:
        print("%s: TOKENIZE FAILED rc=%s: %s" % (name, tok.returncode, (tok.stderr or tok.stdout)[-400:]), flush=True)
        results.append({"name": name, "result": "tokenize failed"})
        continue
    log = os.path.join(OUT, "ppl_%s.log" % name)
    argv = [os.path.join(b, "llama-perplexity.exe"), "-m", a["gguf"], "-f", corpus, "-c", str(ctx)] + a.get("args", [])
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        rc = subprocess.run(argv, stdout=lf, stderr=subprocess.STDOUT, cwd=b).returncode
    txt = open(log, encoding="utf-8", errors="replace").read()
    fm = re.search(r"Final estimate: PPL = ([\d.]+) \+/- ([\d.]+)", txt)
    if not fm:
        print("%s: NO FINAL ESTIMATE (rc %s) - see %s; tail: %s" % (name, rc, log, txt[-300:].replace("\n", " | ")), flush=True)
        results.append({"name": name, "result": "no estimate", "rc": rc})
        continue
    ppl, err = float(fm.group(1)), float(fm.group(2))
    bpb = math.log2(ppl) * ntok / nbytes
    # the +/- band of PPL carried into BPB, so a difference smaller than the bands is called as such
    bpb_lo = math.log2(max(ppl - err, 1e-9)) * ntok / nbytes
    bpb_hi = math.log2(ppl + err) * ntok / nbytes
    r = {"name": name, "gguf": a["gguf"], "tokens": ntok, "bytes_per_token": round(nbytes / ntok, 3), "ppl": ppl,
         "ppl_err": err, "bpb": round(bpb, 4), "bpb_band": [round(bpb_lo, 4), round(bpb_hi, 4)],
         "minutes": round((time.time() - t0) / 60, 1), "result": "ok"}
    results.append(r)
    print("%s: BPB %.4f (band %.4f-%.4f) | PPL %.3f +/- %.3f | %d tokens (%.2f bytes/token) | %.1f min"
          % (name, bpb, bpb_lo, bpb_hi, ppl, err, ntok, nbytes / ntok, r["minutes"]), flush=True)
    json.dump({"corpus_sha256": sha, "bytes": nbytes, "ctx": ctx, "results": results},
              open(os.path.join(OUT, "quality.json"), "w"), indent=1)
ok = [r for r in results if r.get("result") == "ok"]
print("QUALITY LADDER (lower BPB = better): " + " < ".join("%s %.4f" % (r["name"], r["bpb"]) for r in sorted(ok, key=lambda r: r["bpb"])), flush=True)
sys.exit(0 if ok else 1)
