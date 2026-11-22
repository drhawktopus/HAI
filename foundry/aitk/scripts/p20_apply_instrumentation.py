"""[P20] Apply instrumentation + manual save/stop triggers to ai-toolkit's BaseSDTrainProcess.py.

WHY: when the step rate swings (30 -> 75 s/it) with GPU clock, temperature, power and spill all flat, the cause is
inside the process, but ai-toolkit's own timers are wall-clock only, so they cannot distinguish "GPU busy" from "GPU
starving". This adds the two things it lacks:
  1. DEVICE time (CUDA events) vs WALL time per step  <- splits "kernels slower" from "GPU waiting"
  2. allocator stats (num_alloc_retries / reserved / allocated) <- tests the allocator-thrash hypothesis
Phase breakdown is NOT patched: ai-toolkit already has it via config `performance_log_every` (BaseProcess.py L24).

ALSO adds SAVE_NOW / STOP_NOW flag files, because ai-toolkit has NO save-on-interrupt: saves only fire on
`step_num % save_every == 0` (L2105) and the only other save is after the loop completes, so killing mid-run loses
everything since the last save.

Safe: backs up first, idempotent (re-runs are no-ops), byte-verifies, and py_compiles the result.
Usage:  python p20_apply_instrumentation.py [--revert]
"""
import os, py_compile, shutil, sys, datetime


def _need(name):
    """A setting this setup script cannot run without (an environment variable): its value, or a refusal naming it."""
    v = os.environ.get(name, "")
    if not v:
        sys.exit("set %s (see this script's docstring)" % name)
    return v

TARGET = os.path.join(_need("FOUNDRY_AI_TOOLKIT"), "jobs", "process", "BaseSDTrainProcess.py")
BACKUP = TARGET + '.p20bak-instr'
MARK = '[P20-INSTR]'

INSTR_START = """
            # [P20-INSTR] device-vs-wall timing + allocator stats. Fail-safe: must never break training.
            _p20_t0 = None; _p20_ev0 = None; _p20_ev1 = None
            try:
                import time as _p20_time, torch as _p20_torch
                _p20_t0 = _p20_time.perf_counter()
                if _p20_torch.cuda.is_available():
                    _p20_ev0 = _p20_torch.cuda.Event(enable_timing=True)
                    _p20_ev1 = _p20_torch.cuda.Event(enable_timing=True)
                    _p20_ev0.record()
            except Exception:
                pass
"""

INSTR_END = """
            # [P20-INSTR] close the step measurement and append a row.
            # NB: the CUDA sync below is itself a (small) perturbation - intended for benchmark runs.
            try:
                _p20_dev_ms = -1.0
                if _p20_ev0 is not None:
                    _p20_ev1.record(); _p20_torch.cuda.synchronize()
                    _p20_dev_ms = _p20_ev0.elapsed_time(_p20_ev1)
                _p20_wall_ms = (_p20_time.perf_counter() - _p20_t0) * 1000.0
                _p20_ms = _p20_torch.cuda.memory_stats()
                _p20_path = os.path.join(os.environ.get("FOUNDRY_ROOT") or r"@FOUNDRY_ROOT@", "var", "state", "telemetry", "step_instr.csv")
                if not os.path.exists(_p20_path):
                    with open(_p20_path, 'w') as _pf:
                        _pf.write('step,wall_ms,device_ms,alloc_retries,reserved_mb,allocated_mb\\n')
                with open(_p20_path, 'a') as _pf:
                    _pf.write('%d,%.1f,%.1f,%d,%.1f,%.1f\\n' % (
                        self.step_num, _p20_wall_ms, _p20_dev_ms,
                        _p20_ms.get('num_alloc_retries', -1),
                        _p20_ms.get('reserved_bytes.all.current', 0) / 1048576.0,
                        _p20_ms.get('allocated_bytes.all.current', 0) / 1048576.0))
            except Exception:
                pass
"""

TRIGGER = """
                # [P20-INSTR] manual save/stop triggers (ai-toolkit has no save-on-interrupt).
                #   SAVE_NOW -> checkpoint at the current step and keep training
                #   STOP_NOW -> checkpoint at the current step, then exit the loop cleanly (0 steps lost)
                try:
                    _p20_flagdir = os.path.join(os.environ.get("FOUNDRY_ROOT") or r"@FOUNDRY_ROOT@", "var", "state")
                    _p20_save = os.path.join(_p20_flagdir, 'SAVE_NOW')
                    _p20_stop = os.path.join(_p20_flagdir, 'STOP_NOW')
                    if os.path.exists(_p20_save):
                        is_save_step = True
                        os.remove(_p20_save)
                    if os.path.exists(_p20_stop):
                        is_save_step = True
                        self._p20_stop_after_save = True
                        os.remove(_p20_stop)
                except Exception:
                    pass
"""

STOP_BREAK = """
                        # [P20-INSTR] honor STOP_NOW: we just saved, so exit with zero lost steps.
                        if getattr(self, '_p20_stop_after_save', False):
                            print_acc('\\n[P20] STOP_NOW honored - saved at step %d, exiting cleanly.' % self.step_num)
                            break
"""

# (anchor_line_exact, text_to_insert_after)
EDITS = [
    ("            self.timer.start('train_loop')", INSTR_START),
    ("                is_save_step = self.save_config.save_every and self.step_num % self.save_config.save_every == 0", TRIGGER),
    ("                        flush_next = True", STOP_BREAK),
    ("            self.timer.stop('train_loop')", INSTR_END),
]


def revert():
    if not os.path.exists(BACKUP):
        print('no backup found; nothing to revert'); return 1
    shutil.copy2(BACKUP, TARGET)
    print('reverted %s from %s' % (TARGET, BACKUP)); return 0


def main():
    if '--revert' in sys.argv:
        return revert()
    src = open(TARGET, 'r', encoding='utf-8').read()
    if MARK in src:
        print('already patched (marker %s present) - no-op' % MARK); return 0
    if not os.path.exists(BACKUP):
        shutil.copy2(TARGET, BACKUP)
        print('backed up -> %s' % BACKUP)
    out = src
    for anchor, block in EDITS:
        n = out.count(anchor)
        if n != 1:
            print('ABORT: anchor found %d times (need exactly 1): %r' % (n, anchor[:60])); return 2
        out = out.replace(anchor, anchor + block, 1)
    out = out.replace("@FOUNDRY_ROOT@", _need("FOUNDRY_ROOT"))   # the root the trainer writes under
    open(TARGET, 'w', encoding='utf-8').write(out)
    try:
        py_compile.compile(TARGET, doraise=True)
    except Exception as e:
        shutil.copy2(BACKUP, TARGET)
        print('COMPILE FAILED - reverted. %s' % e); return 3
    os.makedirs(os.path.join(_need("FOUNDRY_ROOT"), "var", "state", "telemetry"), exist_ok=True)
    print('patched OK at %s; %d blocks inserted; py_compile clean' % (datetime.datetime.now(), len(EDITS)))
    print('revert with: python p20_apply_instrumentation.py --revert')
    return 0


if __name__ == '__main__':
    sys.exit(main())
