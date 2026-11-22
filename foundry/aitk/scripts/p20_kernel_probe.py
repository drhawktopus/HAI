"""[P20] Fused low-bit kernel investigation for gfx1100.

CONTEXT: measured that the GPU is BUSY (device_ms ~= wall_ms) but the step rate swings 30<->75 s/it with
clocks/temp/power/spill flat => likely memory-STALLED. The dominant cost is quanto's uint4 -> bf16 dequant on every
linear, doubled by gradient-checkpoint recompute. RDNA3 (gfx1100) WMMA natively supports INT8 and INT4, so that
dequant is a SOFTWARE limitation, not a hardware ceiling.

KEY POINT: this question does NOT need a training run. We can microbenchmark the exact GEMM shapes Flux.2's linears
use and compare dequant+matmul vs a fused int4 path in seconds.

MODES:
  --inventory  : CPU-safe. Report torch/ROCm, which quant libs exist, and whether the int4 ops are registered.
                 Safe to run while training occupies the GPU.
  --bench      : GPU. Times bf16 baseline vs quanto uint4 vs torchao int4 on real shapes. Small (a few hundred MB),
                 but DO NOT run it beside a training job you care about - it perturbs timings both ways.
"""
import sys, importlib, traceback

def inventory():
    import torch
    print("torch      :", torch.__version__)
    print("hip        :", getattr(torch.version, "hip", None))
    print("cuda avail :", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("device     :", torch.cuda.get_device_name(0))
        try:
            print("gcnArch    :", torch.cuda.get_device_properties(0).gcnArchName)
        except Exception:
            pass
    print("\n--- quantization libraries ---")
    for m in ["optimum.quanto", "torchao", "gemlite", "bitsandbytes"]:
        try:
            mod = importlib.import_module(m)
            print("  OK   %-16s %s" % (m, getattr(mod, "__version__", "?")))
        except Exception as e:
            print("  MISS %-16s (%s)" % (m, type(e).__name__))

    print("\n--- int4 ops registered in this torch build ---")
    for op in ["_weight_int4pack_mm", "_convert_weight_to_int4pack",
               "_weight_int8pack_mm", "_int_mm"]:
        print("  %-30s %s" % (op, hasattr(torch.ops.aten, op)))

    print("\n--- how quanto actually does the matmul (the thing we'd replace) ---")
    try:
        import inspect
        from optimum.quanto.tensor.qtype import qtypes  # noqa
        import optimum.quanto as q
        # QLinear forward is the hot path
        cand = [n for n in dir(q) if "Linear" in n]
        print("  quanto Linear classes:", cand)
        for n in cand:
            cls = getattr(q, n)
            if hasattr(cls, "forward"):
                src = inspect.getsource(cls.forward)
                print("  --- %s.forward ---" % n)
                for line in src.splitlines()[:18]:
                    print("     ", line.rstrip())
                break
    except Exception:
        print("  (could not introspect quanto:)")
        traceback.print_exc(limit=2)


def bench():
    import torch, time
    dev = "cuda"
    # Representative Flux.2 linear shapes (hidden ~3072, mlp x4). M = tokens at 448 res.
    shapes = [(1024, 3072, 3072), (1024, 3072, 12288), (1024, 12288, 3072)]
    print("shape (M,K,N)            bf16 ms   notes")
    for (M, K, N) in shapes:
        a = torch.randn(M, K, device=dev, dtype=torch.bfloat16)
        w = torch.randn(N, K, device=dev, dtype=torch.bfloat16)
        for _ in range(3):
            _ = a @ w.t()
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for _ in range(20):
            _ = a @ w.t()
        torch.cuda.synchronize()
        ms = (time.perf_counter() - t0) / 20 * 1000
        print("(%5d,%6d,%6d)  %8.3f" % (M, K, N, ms))
    print("\n(baseline only for now - add quanto/torchao paths once the inventory says what exists)")


if __name__ == "__main__":
    if "--bench" in sys.argv:
        bench()
    else:
        inventory()
