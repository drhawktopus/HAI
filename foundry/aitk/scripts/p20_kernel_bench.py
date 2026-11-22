"""[P20] Fused low-bit kernel MICROBENCHMARK on gfx1100 — the decisive test, without a training run.

WHY: the dominant per-step cost is quanto's uint4 -> bf16 dequant on every linear (x2 for checkpoint recompute).
Confirmed from source that quanto has NO fused path:
    QLinear.forward -> torch.nn.functional.linear(input, self.qweight, bias)
i.e. it materialises bf16 and does a normal matmul. RDNA3 (gfx1100) WMMA natively supports INT4, and this torch
build DOES register `_weight_int4pack_mm` / `_convert_weight_to_int4pack`. So the question is purely empirical:
is a fused int4 path actually FASTER than dequant+matmul on this card?

REAL Flux.2 shapes (from Flux2Params): hidden_size 6144, mlp_ratio 3.0 -> 18432, 8 double + 48 single = 56 blocks.
    attention proj : K=6144  N=6144
    mlp up         : K=6144  N=18432
    mlp down       : K=18432 N=6144

NEEDS THE GPU FREE. Peak ~1 GB across weights + quantized copies; we run at ~200 MB headroom during training, so
running this beside a live run risks OOM and perturbs both. Run it when the card is idle. Takes seconds.
"""
import gc, sys, time
import torch
import torch.nn as nn

DEV = "cuda"
DTYPE = torch.bfloat16
M = 1024  # ~tokens at 448 (56x56 latent, 2x2 patch = 784 img tokens + text context)
SHAPES = [("attn_proj", 6144, 6144), ("mlp_up", 6144, 18432), ("mlp_down", 18432, 6144)]
ITERS = 20


def timeit(fn, warmup=3, iters=ITERS):
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) / iters * 1000.0


def free():
    gc.collect()
    torch.cuda.empty_cache()


def run_shape(tag, K, N):
    print("\n=== %s : M=%d K=%d N=%d ===" % (tag, M, K, N))
    a = torch.randn(M, K, device=DEV, dtype=DTYPE)
    results = {}

    # 1) bf16 baseline
    try:
        lin = nn.Linear(K, N, bias=False).to(DEV, DTYPE)
        results["bf16"] = timeit(lambda: lin(a))
        del lin; free()
    except Exception as e:
        print("  bf16 FAILED:", repr(e))

    # 2) quanto uint4 (what we run today: dequant -> F.linear)
    try:
        from optimum.quanto import quantize, freeze
        try:
            from optimum.quanto import quint4 as QT
        except Exception:
            from optimum.quanto import qint4 as QT
        lin = nn.Linear(K, N, bias=False).to(DEV, DTYPE)
        quantize(lin, weights=QT)
        freeze(lin)
        results["quanto_u4"] = timeit(lambda: lin(a))
        del lin; free()
    except Exception as e:
        print("  quanto FAILED:", repr(e))

    # 3) torchao int4 weight-only (tinygemm / _weight_int4pack_mm fused path)
    try:
        from torchao.quantization import quantize_
        try:
            from torchao.quantization import int4_weight_only as cfg
            conf = cfg()
        except Exception:
            from torchao.quantization import Int4WeightOnlyConfig as cfg
            conf = cfg()
        lin = nn.Linear(K, N, bias=False).to(DEV, DTYPE)
        quantize_(lin, conf)
        results["torchao_i4"] = timeit(lambda: lin(a))
        del lin; free()
    except Exception as e:
        print("  torchao FAILED:", repr(e))

    base = results.get("bf16")
    for k, v in results.items():
        rel = ("  (%.2fx vs bf16)" % (base / v)) if base and v else ""
        print("  %-12s %8.3f ms%s" % (k, v, rel))
    del a; free()
    return results


if __name__ == "__main__":
    print("torch", torch.__version__, "| hip", getattr(torch.version, "hip", None))
    print("device", torch.cuda.get_device_name(0),
          "| free/total GB %.2f/%.2f" % tuple(x / 1024**3 for x in torch.cuda.mem_get_info()))
    if torch.cuda.mem_get_info()[0] < 2 * 1024**3:
        print("\n⚠ ABORT: <2 GB free — a training run is probably active. Run this when the card is idle.")
        sys.exit(1)
    all_res = {}
    for tag, K, N in SHAPES:
        all_res[tag] = run_shape(tag, K, N)
    print("\n=== SUMMARY (ms, lower is better) ===")
    print("%-12s %10s %12s %12s" % ("shape", "bf16", "quanto_u4", "torchao_i4"))
    for tag, r in all_res.items():
        print("%-12s %10s %12s %12s" % (
            tag,
            "%.3f" % r["bf16"] if "bf16" in r else "-",
            "%.3f" % r["quanto_u4"] if "quanto_u4" in r else "FAIL",
            "%.3f" % r["torchao_i4"] if "torchao_i4" in r else "FAIL"))
    print("\nRead: if torchao_i4 << quanto_u4, the fused INT4 path is a real lever and worth the fork surgery.")
    print("If torchao_i4 >= quanto_u4 (or FAILs), the gfx1100 int4 kernels aren't there yet — drop the lead.")
