"""One-time install of the Krea 2 trainer on the AMD box, BESIDE the FLUX.2 one (FOUNDRY_AI_TOOLKIT is never touched).

Runs on the box as a scheduled task (long work over ssh dies with the session). Steps, each logged and resumable:
  1. clone upstream ai-toolkit at a PINNED commit into FOUNDRY_AITK_KREA2
  2. copy the lab's ROCm interpreter (<FOUNDRY_AI_TOOLKIT>\\venv, itself the comfyui-rocm python_env) to its own venv
  3. pip install upstream's requirements with the ROCm torch stack CONSTRAINED to the versions already installed,
     minus the audio/video-only packages (torchcodec, librosa, mutagen, av) Krea 2 never imports
  4. prove it: torch sees the card, and ai-toolkit's Krea2Model imports
  5. stage the text encoder (Qwen/Qwen3-VL-4B-Instruct) and the VAE (Qwen/Qwen-Image vae/) locally, sha256-checked
     against Hugging Face, so a training run never downloads mid-launch
Writes <P20_KREA2_TRAIN_DIR>\\setup\\RESULT.json with a verdict per step; exits non-zero unless every step passed.
"""
import hashlib, json, os, re, shutil, subprocess, sys, time, urllib.request


def _need(name):
    """A setting this setup script cannot run without (an environment variable): its value, or a refusal naming it."""
    v = os.environ.get(name, "")
    if not v:
        sys.exit("set %s (see this script's docstring)" % name)
    return v

ROOT = _need("FOUNDRY_AITK_KREA2")
TRAIN = _need("P20_KREA2_TRAIN_DIR")
SETUP = os.path.join(TRAIN, "setup")
LOG = os.path.join(SETUP, "setup.log")
REPO = "https://github.com/ostris/ai-toolkit"
PIN = "73ebae13a468942ded8b47991b5d56eeeea25c36"      # upstream main with Krea 2 support
SRC_VENV = os.path.join(_need("FOUNDRY_AI_TOOLKIT"), "venv")
VENV_PY = os.path.join(ROOT, "venv", "python.exe")
SKIP = ("torchcodec", "librosa", "mutagen", "av")
KEEP = re.compile(r"^(torch|torchvision|torchaudio|rocm[a-z_-]*|triton[a-z_-]*)-(.+)\.dist-info$", re.I)
HF = [("Qwen/Qwen3-VL-4B-Instruct", None, os.path.join(TRAIN, "hf", "Qwen3-VL-4B-Instruct")),
      ("Qwen/Qwen-Image", "vae/", os.path.join(TRAIN, "hf", "Qwen-Image"))]
result = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "pin": PIN, "steps": {}}


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))


def run(cmd, **kw):
    log("RUN " + " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write((p.stdout or "")[-6000:] + (p.stderr or "")[-6000:] + "\n")
    return p


def step(name, ok, detail=""):
    result["steps"][name] = {"ok": bool(ok), "detail": str(detail)[:500]}
    log("STEP %s %s %s" % (name, "OK" if ok else "FAILED", detail))
    with open(os.path.join(SETUP, "RESULT.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    return ok


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(16 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    os.makedirs(SETUP, exist_ok=True)
    log("=== Krea 2 trainer setup, pin %s ===" % PIN)
    # 1. code
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        run(["git", "clone", REPO, ROOT])
    run(["git", "-C", ROOT, "fetch", "origin", PIN])
    run(["git", "-C", ROOT, "checkout", "--force", PIN])
    head = run(["git", "-C", ROOT, "rev-parse", "HEAD"]).stdout.strip()
    krea = os.path.join(ROOT, "extensions_built_in", "diffusion_models", "krea2", "krea2.py")
    if not step("clone", head == PIN and os.path.isfile(krea), head):
        return 1
    # 2. interpreter
    if not os.path.isfile(VENV_PY):
        p = run(["robocopy", SRC_VENV, os.path.join(ROOT, "venv"), "/E", "/NFL", "/NDL", "/NJH", "/NP", "/R:2", "/W:5"])
        if p.returncode >= 8:                                   # robocopy: 0-7 success, 8+ failure
            step("venv", False, "robocopy rc=%d" % p.returncode)
            return 1
    sp = os.path.join(ROOT, "venv", "Lib", "site-packages")
    pins = ["%s==%s" % (m.group(1), m.group(2)) for m in (KEEP.match(d) for d in os.listdir(sp)) if m]
    if not step("venv", os.path.isfile(VENV_PY) and any(p.lower().startswith("torch==") for p in pins),
                "%d pinned: %s" % (len(pins), ", ".join(pins))):
        return 1
    # 3. requirements, ROCm stack held where it is
    with open(os.path.join(SETUP, "constraints.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(pins) + "\n")
    reqs = []
    for fn in ("requirements_base.txt",):
        for line in open(os.path.join(ROOT, fn), encoding="utf-8"):
            name = re.split(r"[=<>\[ ;@]", line.strip(), 1)[0].lower()
            if line.strip() and not line.startswith("#") and name not in SKIP and not line.startswith("-r"):
                reqs.append(line.strip())
    rq = os.path.join(SETUP, "requirements_krea2.txt")
    with open(rq, "w", encoding="utf-8") as f:
        f.write("\n".join(reqs) + "\n")
    p = run([VENV_PY, "-m", "pip", "install", "--no-input", "-r", rq, "-c", os.path.join(SETUP, "constraints.txt")],
            cwd=ROOT)
    if not step("pip", p.returncode == 0, "rc=%d, %d requirements" % (p.returncode, len(reqs))):
        return 1
    # 4. proof
    probe = ("import sys, torch; sys.path.insert(0, r'%s'); print('TORCH', torch.__version__, torch.cuda.is_available(),"
             " torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-');"
             " import transformers, diffusers; print('LIBS', transformers.__version__, diffusers.__version__);"
             " from extensions_built_in.diffusion_models.krea2.krea2 import Krea2Model; print('KREA2_IMPORT_OK', Krea2Model.arch)"
             % ROOT)
    p = run([VENV_PY, "-c", probe], cwd=ROOT)
    if not step("import", "KREA2_IMPORT_OK" in (p.stdout or "") and "TORCH" in (p.stdout or "") and " True " in p.stdout,
                (p.stdout or p.stderr or "")[-400:]):
        return 1
    # 5. encoder + VAE, verified against Hugging Face
    for repo, prefix, dest in HF:
        req = urllib.request.Request("https://huggingface.co/api/models/%s?blobs=true" % repo,
                                     headers={"User-Agent": "curl/8.9.1"})
        sib = json.load(urllib.request.urlopen(req, timeout=60))["siblings"]
        files = [s for s in sib if (prefix is None or s["rfilename"].startswith(prefix))
                 and not s["rfilename"].endswith((".md", ".gitattributes"))]
        bad = []
        for s in files:
            out = os.path.join(dest, *s["rfilename"].split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            want = (s.get("lfs") or {}).get("sha256")
            size = (s.get("lfs") or {}).get("size") or s.get("size")
            good = os.path.isfile(out) and (sha256(out) == want if want else os.path.getsize(out) == size)
            for _ in range(3):
                if good:
                    break
                run(["curl.exe", "-L", "--fail", "-sS", "-C", "-", "--retry", "5", "-o", out,
                     "https://huggingface.co/%s/resolve/main/%s" % (repo, s["rfilename"])])
                good = os.path.isfile(out) and (sha256(out) == want if want else os.path.getsize(out) == size)
                if not good and os.path.isfile(out):
                    os.replace(out, out + ".bad-%d" % int(time.time()))
            if not good:
                bad.append(s["rfilename"])
        if not step("hf:" + repo, files and not bad, "%d files, bad=%s" % (len(files), bad)):
            return 1
    result["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    step("all", True, "trainer ready at %s" % ROOT)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as e:                                               # noqa: BLE001
        step("crash", False, repr(e))
        rc = 1
    sys.exit(rc)
