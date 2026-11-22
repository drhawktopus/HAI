"""A/B collision test (on Mothership): Leg B recipe against the :8190 clone (June-11 public wheel).
Render on 8190 + SDXL gen fired +15s ALSO on 8190's queue? NO — collision requires a second
CONTEXT: partner SDXL runs on :8189 (production alpha server, launched for the test). The RENDER
context (the one that gets poisoned) is the subject under test. Up to N cycles; each cycle
reboots nothing (context-kill between cycles keeps kernels comparable to Leg B, which wedged on a
fresh kernel anyway). Daemon (watching 8189 only for renders) may not judge 8190 — this script
self-judges via step timeout."""
import json
import subprocess
import sys
import time
import urllib.request

AB = "http://127.0.0.1:8190"
PARTNER = "http://127.0.0.1:8189"
CYCLES = int(sys.argv[1]) if len(sys.argv) > 1 else 3
RENDER_TIMEOUT = 600


def post_json(base, g, cid):
    req = urllib.request.Request(base + "/prompt",
                                 data=json.dumps({"client_id": cid, "prompt": g}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["prompt_id"]


def up(base):
    try:
        with urllib.request.urlopen(base + "/system_stats", timeout=5):
            return True
    except Exception:
        return False


def hist_done(base, pid):
    try:
        with urllib.request.urlopen(base + "/history/" + pid, timeout=15) as r:
            h = json.load(r)
        return pid in h and h[pid].get("status", {}).get("completed")
    except Exception:
        return False


def kill_port(port):
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "$p = (Get-NetTCPConnection -LocalPort %d -ea 0 | Where-Object { $_.State -eq 'Listen' -or $_.RemoteAddress -in '0.0.0.0','::' } | "
                    "Select -First 1).OwningProcess; if ($p) { Stop-Process -Id $p -Force }" % port],
                   capture_output=True, timeout=30)


def launch(task):
    subprocess.run(["schtasks", "/run", "/tn", task], capture_output=True, timeout=30)


def flux_graph(seed):
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux2-dev-Q3_K_M.gguf"}},
        "4": {"class_type": "P20LoadConditioning", "inputs": {"name": "harness"}},
        "5": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": 2.5}},
        "6": {"class_type": "VAELoader", "inputs": {"vae_name": "flux2-vae.safetensors"}},
        "7": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "8": {"class_type": "Flux2Scheduler", "inputs": {"steps": 20, "width": 1024, "height": 1024}},
        "9": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["5", 0]}},
        "12": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["10", 0], "guider": ["11", 0], "sampler": ["9", 0],
                          "sigmas": ["8", 0], "latent_image": ["7", 0]}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["6", 0]}},
        "14": {"class_type": "SaveImage", "inputs": {"images": ["13", 0], "filename_prefix": "ab714"}},
    }


def sdxl_graph(seed):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "RealVisXL_V5.0_fp16.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "a lighthouse, angle %d" % seed}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "blurry"}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                         "latent_image": ["4", 0], "seed": seed, "steps": 6, "cfg": 5.0,
                         "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": "ab714_partner"}},
    }


# ensure both servers
if not up(AB):
    launch("AB ComfyUI 8190")
    t0 = time.time()
    while not up(AB):
        if time.time() - t0 > 360:
            print("8190 clone never came up"); sys.exit(1)
        time.sleep(5)
    print("8190 (June-11 wheel) up (%.0fs)" % (time.time() - t0), flush=True)
if not up(PARTNER):
    launch("P20 Harness ComfyUI")
    t0 = time.time()
    while not up(PARTNER):
        if time.time() - t0 > 360:
            print("8189 partner never came up"); sys.exit(1)
        time.sleep(5)
    print("8189 partner up (%.0fs)" % (time.time() - t0), flush=True)

for cyc in range(1, CYCLES + 1):
    t0 = time.time()
    rp = post_json(AB, flux_graph(1000 + cyc), "ab714")
    print("cycle %d: render queued on 8190" % cyc, flush=True)
    fired = False
    done = False
    while time.time() - t0 < RENDER_TIMEOUT:
        time.sleep(10)
        if not fired and time.time() - t0 > 15:
            fired = True
            try:
                post_json(PARTNER, sdxl_graph(cyc), "ab714_partner")
                print("cycle %d: partner SDXL fired on 8189 (+%.0fs)" % (cyc, time.time() - t0), flush=True)
            except Exception as e:
                print("cycle %d: partner fire FAILED %r" % (cyc, e), flush=True)
        if hist_done(AB, rp):
            done = True
            break
    secs = time.time() - t0
    if done:
        print("cycle %d: RENDER COMPLETED in %.0fs (alpha wedged here in ~90s; healthy ~165-250s w/ contention)" % (cyc, secs), flush=True)
    else:
        print("cycle %d: RENDER TIMEOUT %.0fs = WEDGED ON JUNE-11 WHEEL TOO" % (cyc, secs), flush=True)
        print("RESULT: BUG PRESENT in 2.10.0+rocm7.14.0a20260611 -> long-standing, not a July regression")
        kill_port(8190)
        sys.exit(0)
    # fresh context between cycles, like Leg B's churn
    kill_port(8190)
    time.sleep(5)
    launch("AB ComfyUI 8190")
    t0 = time.time()
    while not up(AB):
        if time.time() - t0 > 360:
            print("8190 relaunch failed"); sys.exit(1)
        time.sleep(5)

print("RESULT: %d collision cycles CLEAN on the June-11 public wheel -> bug likely introduced in the "
      "June 11 -> July 14 window (33-day bisect range for AMD)" % CYCLES)
kill_port(8190)
