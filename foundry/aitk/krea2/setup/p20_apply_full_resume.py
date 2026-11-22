"""[P20] Byte-identical resume for ai-toolkit's BaseSDTrainProcess (the Krea 2 tree first): a resumed run is held to the
same standard as a resumed text model, its output byte-identical to the uninterrupted run.

Upstream (73ebae13) saves only the adapter (at the SAVE dtype, float16 here, while it trains in float32) and the
optimizer. A resume therefore starts from rounded weights, with fresh random streams, a restarted data order, and
re-trains the step it saved (it resumes AT step N, whose update the checkpoint already holds). This adds:
  1. at every save, `p20_resume_<step>.pt` beside the checkpoint: the adapter state_dict at its TRAINING precision,
     every RNG (torch CPU, every CUDA device, numpy, python), and the data position (the global RNG state captured
     immediately before the current epoch's iter() plus how many batches that epoch has handed out);
  2. on resume, if that file exists for the checkpoint's step: load those weights over the rounded ones, replay the
     data position (restore the epoch-start RNG, rebuild the iterator, draw the consumed batches), then restore every
     RNG - so step N+1 sees exactly what an uninterrupted run would have;
  3. resume at the step after the checkpoint's last update: the sidecar records it (`next_step`) when it is written,
     because the label means two things - a save inside the loop (and STOP_NOW's) is labelled with the step index it
     just trained (next = N+1), the save after a run that ran to the end with the step COUNT (next = N). Without a
     sidecar it is N+1 (a fixed N+1 skips one step after a natural finish: 39 updates against 40, every tensor off
     by up to 1.9e-4);
  4. P20_TRAIN_SEED: an optional training seed, applied before the adapter is initialised (upstream has none - its
     `seed` settings are for sample images), so two fresh runs are comparable at all.
Requires the dataset's `num_workers: 0`: with persistent workers the FIRST iter() of a process draws a worker seed
that a resumed process draws at a different epoch, shifting the shuffle by one draw.

Safe: backs up first, idempotent (marker), every anchor must match exactly once, py_compile or revert.
Usage:  python p20_apply_full_resume.py [--revert]
"""
import os, py_compile, shutil, sys, datetime


def _need(name):
    """A setting this setup script cannot run without (an environment variable): its value, or a refusal naming it."""
    v = os.environ.get(name, "")
    if not v:
        sys.exit("set %s (see this script's docstring)" % name)
    return v

TARGET = (sys.argv[sys.argv.index("--target") + 1] if "--target" in sys.argv   # a copy (for testing): no setting needed
          else os.path.join(_need("FOUNDRY_AITK_KREA2"), "jobs", "process", "BaseSDTrainProcess.py"))
BACKUP = TARGET + '.p20bak-resume'
MARK = '[P20-RESUME]'
UPGRADE_MARK = 'def _p20_next_step('

HELPERS = '''
# [P20-RESUME] byte-identical resume: full-precision adapter weights, every RNG, and the data position.
def _p20_rng_get():
    return {"torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
            "numpy": np.random.get_state(),
            "python": random.getstate()}


def _p20_rng_set(s):
    torch.set_rng_state(s["torch"])
    if s.get("cuda") is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(s["cuda"])
    np.random.set_state(s["numpy"])
    random.setstate(s["python"])


def _p20_seed_from_env():
    v = os.environ.get("P20_TRAIN_SEED", "").strip()
    if not v:
        return None
    s = int(v)
    random.seed(s)
    np.random.seed(s)
    torch.manual_seed(s)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(s)
    print_acc("[P20-RESUME] training seed %d (P20_TRAIN_SEED)" % s)
    return s


class _P20Counting:
    """Counts the batches an epoch's iterator hands out - the data position a resume replays."""

    def __init__(self, it, rec):
        self.it, self.rec = it, rec

    def __iter__(self):
        return self

    def __next__(self):
        b = next(self.it)
        self.rec["consumed"] += 1
        return b


def _p20_iter(proc, dataloader, reset):
    res = getattr(proc, "_p20_resume", None)
    if res is not None and res.get("data"):
        d = res["data"]
        _p20_rng_set(d["rng"])
        proc._p20_epoch = {"rng": d["rng"], "reset": d["reset"], "consumed": 0}
        it = _P20Counting(iter(dataloader), proc._p20_epoch)
        for _ in range(int(d["consumed"])):
            next(it)
        _p20_rng_set(res["rng"])
        proc._p20_resume = None
        print_acc("[P20-RESUME] data position replayed (%d batch(es) into the epoch), every RNG restored"
                  % int(d["consumed"]))
        return it
    proc._p20_epoch = {"rng": _p20_rng_get(), "reset": reset, "consumed": 0}
    return _P20Counting(iter(dataloader), proc._p20_epoch)


def _p20_next_step(proc):
    """The step a resume from this save must train first. Inside the loop (and on STOP_NOW's break) step_num is the
    index just trained; after a loop that ran to the end it is the step COUNT, every update already done."""
    return proc.step_num if proc.step_num >= proc.train_config.steps else proc.step_num + 1


def _p20_save_resume(proc):
    """Never raises: a failed sidecar costs the byte-identical resume, never the save itself."""
    try:
        if proc.network is None:
            return
        state = {"step": proc.step_num, "epoch": proc.epoch_num,
                 "next_step": _p20_next_step(proc),
                 "network": {k: v.detach().to("cpu").clone() for k, v in proc.network.state_dict().items()},
                 "rng": _p20_rng_get(),
                 "data": dict(getattr(proc, "_p20_epoch", None) or {}) or None}
        path = os.path.join(proc.save_root, "p20_resume_%09d.pt" % proc.step_num)
        torch.save(state, path + ".tmp")
        os.replace(path + ".tmp", path)
        print_acc("[P20-RESUME] full state saved: %s" % path)
        olds = sorted(glob.glob(os.path.join(proc.save_root, "p20_resume_*.pt")))
        for old in olds[:-3]:
            os.remove(old)
    except Exception as e:
        print_acc("[P20-RESUME] full state NOT saved (%r) - a resume from here would not be byte-identical" % (e,))


def _p20_load_resume(proc):
    """After the checkpoint is loaded (step N from its metadata): full-precision weights + RNG + data position from
    the sidecar when it exists, and resume at the sidecar's next_step (N+1 without one)."""
    if not proc.step_num:
        return
    n = proc.step_num
    path = os.path.join(proc.save_root, "p20_resume_%09d.pt" % n)
    if os.path.exists(path):
        state = torch.load(path, map_location="cpu", weights_only=False)
        missing, unexpected = proc.network.load_state_dict(state["network"], strict=False)
        if missing or unexpected:
            raise RuntimeError("[P20-RESUME] sidecar does not match the network: missing %d, unexpected %d"
                               % (len(missing), len(unexpected)))
        proc._p20_resume = state
        print_acc("[P20-RESUME] full state loaded for step %d (float32 adapter, RNG, data position)" % n)
    else:
        print_acc("[P20-RESUME] no full-state sidecar for step %d - resuming the classic way: the adapter is the "
                  "rounded checkpoint and the random streams and data order start fresh (NOT byte-identical)" % n)
    res = getattr(proc, "_p20_resume", None)
    proc.step_num = int(res["next_step"]) if res is not None and res.get("next_step") is not None else n + 1
    proc.start_step = proc.step_num
    print_acc("[P20-RESUME] resuming at step %d (checkpoint labelled %d holds every update before it)"
              % (proc.step_num, n))


def _p20_exit_watchdog(seconds=120):
    """Every file of the run is written (the final save just ran). If the process is still alive `seconds` later - a
    teardown stuck in the GPU driver, which holds the card and keeps the foundry's lane closed - end it so the foundry
    sees the trainer exit. A normal exit beats the timer and this does nothing."""
    import sys as _sys, threading as _threading, time as _time

    def _later():
        _time.sleep(seconds)
        print_acc("[P20-RESUME] exit watchdog: still alive %ds after the final save - forcing exit "
                  "(every file is already written)" % seconds)
        try:
            _sys.stdout.flush()
            _sys.stderr.flush()
        finally:
            os._exit(0)
    _threading.Thread(target=_later, daemon=True).start()

'''

SEED = '''
        # [P20-RESUME] optional training seed, before the adapter is initialised
        self._p20_train_seed = _p20_seed_from_env()
'''

EDITS = [
    # (anchor, replacement) - every anchor must match exactly once
    ("class BaseSDTrainProcess(BaseTrainProcess):", HELPERS + "\nclass BaseSDTrainProcess(BaseTrainProcess):"),
    ("    def run(self):\n", "    def run(self):\n" + SEED),
    ("            dataloader = self.data_loader\n            dataloader_iterator = iter(dataloader)\n",
     "            dataloader = self.data_loader\n"
     "            dataloader_iterator = _p20_iter(self, dataloader, reset=False)  # [P20-RESUME]\n"),
    ("                                dataloader_iterator = iter(dataloader)\n"
     "                                trigger_dataloader_setup_epoch(dataloader)\n",
     "                                dataloader_iterator = _p20_iter(self, dataloader, reset=True)  # [P20-RESUME]\n"
     "                                trigger_dataloader_setup_epoch(dataloader)\n"),
    ('                print_acc(f"Saved optimizer to {file_path}")\n',
     '                print_acc(f"Saved optimizer to {file_path}")\n'
     '                _p20_save_resume(self)  # [P20-RESUME]\n'),
    ("                    extra_weights = self.load_weights(latest_save_path)\n",
     "                    extra_weights = self.load_weights(latest_save_path)\n"
     "                    _p20_load_resume(self)  # [P20-RESUME]\n"),
    ("        if self.accelerator.is_main_process:\n            self.save()\n",
     "        if self.accelerator.is_main_process:\n            self.save()\n"
     "            _p20_exit_watchdog()  # [P20-RESUME]\n"),
]


def revert():
    if not os.path.exists(BACKUP):
        print('no backup found; nothing to revert'); return 1
    shutil.copy2(BACKUP, TARGET)
    print('reverted %s from %s' % (TARGET, BACKUP)); return 0


def main():
    if '--revert' in sys.argv:
        return revert()
    target = sys.argv[sys.argv.index('--target') + 1] if '--target' in sys.argv else TARGET
    backup = target + '.p20bak-resume'
    src = open(target, 'r', encoding='utf-8').read()
    if MARK in src and UPGRADE_MARK in src:
        print('already patched (markers %s, %s present) - no-op' % (MARK, UPGRADE_MARK)); return 0
    if MARK in src:
        # an earlier version of this patch: swap its helper block (everything the patch put before the class) for
        # the current one; the hooks in the class body are unchanged
        head = '# [P20-RESUME] byte-identical resume:'
        cls = 'class BaseSDTrainProcess(BaseTrainProcess):'
        if src.count(head) != 1 or src.count(cls) != 1 or src.index(head) > src.index(cls):
            print('ABORT: cannot find the earlier helper block exactly once'); return 2
        out = src[:src.index(head)] + HELPERS[HELPERS.index(head):] + '\n' + src[src.index(cls):]
        backup = target + '.p20bak-resume-v1'
        what = 'upgraded the helper block'
    else:
        out = src
        for anchor, repl in EDITS:
            n = out.count(anchor)
            if n != 1:
                print('ABORT: anchor found %d times (need exactly 1): %r' % (n, anchor[:70])); return 2
            out = out.replace(anchor, repl, 1)
        what = '%d edits' % len(EDITS)
    if not os.path.exists(backup):
        shutil.copy2(target, backup)
        print('backed up -> %s' % backup)
    with open(target, 'w', encoding='utf-8', newline='\n') as f:
        f.write(out)
    try:
        py_compile.compile(target, doraise=True)
    except Exception as e:
        shutil.copy2(backup, target)
        print('COMPILE FAILED - reverted. %s' % e); return 3
    print('patched OK at %s; %s; py_compile clean' % (datetime.datetime.now(), what))
    return 0


if __name__ == '__main__':
    sys.exit(main())
