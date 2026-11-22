"""[P20] FIX the Mistral uint4 cache.

PROBLEM: `load_te` saves `text_encoder.state_dict()`, which by definition OMITS non-persistent buffers. The model
has exactly 2 of them - `model.language_model.rotary_emb.inv_freq` and
`model.vision_tower.patch_positional_embedding.inv_freq` - so after `load_state_dict(cache, assign=True)` those two
stay on the META device and the following `.to(device)` raises
`NotImplementedError: Cannot copy out of meta tensor`. The fast path then falls back to a ~4-5 min CPU re-quant on
EVERY launch.

DESIGN (deliberately conservative):
  * We do NOT recompute inv_freq by hand. A wrong rotary frequency does not crash - it silently corrupts positional
    encoding and poisons a whole training run. Instead we CAPTURE the buffers from the REAL built model on the slow
    build path, so the values are correct by construction.
  * Consequence: the next launch still does one slow build and writes the sidecar; every launch after that is fast.
  * Fail-safe preserved: if the sidecar is missing/incomplete the fast path RAISES, which drops through to the
    proven build path exactly as before. It can only ever be faster, never wrong.

Byte-level patching (the file has mixed-encoding comments; binary edits preserve them exactly).
Usage: python p20_apply_mistral_bufferfix.py [--revert]
"""
import os, py_compile, shutil, sys


def _need(name):
    """A setting this setup script cannot run without (an environment variable): its value, or a refusal naming it."""
    v = os.environ.get(name, "")
    if not v:
        sys.exit("set %s (see this script's docstring)" % name)
    return v

TARGET = os.path.join(_need("FOUNDRY_AI_TOOLKIT"), "extensions_built_in", "diffusion_models", "flux2", "flux2_model.py")
BACKUP = TARGET + '.p20bak-buffix'
MARK = b'[P20-BUF]'

ANCHOR_CONST = b'MISTRAL_UINT4_CACHE = (MISTRAL_PATH.rstrip("\\\\/") + "-uint4.pt") if os.path.isdir(MISTRAL_PATH) else os.path.join(os.environ.get("P20_TRAIN_DIR") or r"F:\\flux2-train", "mistral-uint4.pt")'
ADD_CONST = b'''
# [P20-BUF] sidecar holding the NON-PERSISTENT buffers that state_dict() omits (rotary inv_freq x2).
MISTRAL_UINT4_BUFFERS = MISTRAL_UINT4_CACHE[:-3] + "-buffers.pt" if MISTRAL_UINT4_CACHE.endswith(".pt") else MISTRAL_UINT4_CACHE + "-buffers.pt"'''

ANCHOR_LOAD = b'                text_encoder.load_state_dict(_sd, assign=True)'
ADD_LOAD = b'''
                # [P20-BUF] restore non-persistent buffers (rotary inv_freq) that state_dict() omits. Without
                # them those tensors remain on META and the .to(device) below raises. If the sidecar is missing
                # or incomplete we RAISE on purpose -> caller falls back to the proven build path (which writes it).
                _sdk2 = set(text_encoder.state_dict().keys())
                _still_meta = [_n for _n, _b in text_encoder.named_buffers()
                               if _b is not None and _n not in _sdk2 and _b.is_meta]
                if _still_meta:
                    if not os.path.exists(MISTRAL_UINT4_BUFFERS):
                        raise RuntimeError(
                            "buffer sidecar missing (%s); %d meta buffers: %s"
                            % (MISTRAL_UINT4_BUFFERS, len(_still_meta), _still_meta))
                    _bsd = torch.load(MISTRAL_UINT4_BUFFERS, map_location="cpu", weights_only=False)
                    for _n in _still_meta:
                        if _n not in _bsd:
                            raise RuntimeError("buffer %s absent from sidecar" % _n)
                        _pname, _leaf = (_n.rsplit(".", 1) if "." in _n else ("", _n))
                        _parent = text_encoder.get_submodule(_pname) if _pname else text_encoder
                        _parent._buffers[_leaf] = _bsd[_n].clone()
                    self.print_and_status_update(
                        "Restored %d non-persistent buffer(s) from sidecar" % len(_still_meta))'''

ANCHOR_SAVE = b'                        self.print_and_status_update(f"Mistral cache save skipped ({_e!r})")'
ADD_SAVE = b'''
                # [P20-BUF] persist the non-persistent buffers alongside the cache. Captured from the REAL built
                # model here, so the values are correct by construction (never hand-recomputed). Gated on the
                # sidecar's own existence so it is written even when the main cache already exists.
                if _use_cache and not os.path.exists(MISTRAL_UINT4_BUFFERS):
                    try:
                        _sdk = set(text_encoder.state_dict().keys())
                        _bufs = {_n: _b.detach().cpu().clone()
                                 for _n, _b in text_encoder.named_buffers()
                                 if _b is not None and _n not in _sdk}
                        if _bufs:
                            torch.save(_bufs, MISTRAL_UINT4_BUFFERS)
                            self.print_and_status_update(
                                "Saved %d non-persistent buffer(s) -> %s" % (len(_bufs), MISTRAL_UINT4_BUFFERS))
                    except Exception as _e:
                        self.print_and_status_update("Mistral buffer sidecar save skipped (%r)" % (_e,))'''

EDITS = [(ANCHOR_CONST, ADD_CONST), (ANCHOR_LOAD, ADD_LOAD), (ANCHOR_SAVE, ADD_SAVE)]


def main():
    if '--revert' in sys.argv:
        if not os.path.exists(BACKUP):
            print('no backup; nothing to revert'); return 1
        shutil.copy2(BACKUP, TARGET); print('reverted'); return 0
    src = open(TARGET, 'rb').read()
    if MARK in src:
        print('already patched - no-op'); return 0
    if not os.path.exists(BACKUP):
        shutil.copy2(TARGET, BACKUP); print('backed up -> %s' % BACKUP)
    out = src
    for anchor, block in EDITS:
        n = out.count(anchor)
        if n != 1:
            print('ABORT: anchor found %d times (need 1): %r' % (n, anchor[:70])); return 2
        out = out.replace(anchor, anchor + block, 1)
    open(TARGET, 'wb').write(out)
    try:
        py_compile.compile(TARGET, doraise=True)
    except Exception as e:
        shutil.copy2(BACKUP, TARGET); print('COMPILE FAILED - reverted: %s' % e); return 3
    print('patched OK; 3 blocks inserted; py_compile clean')
    print('NOTE: next launch does ONE more slow build (writes the sidecar); launches after that use the fast path.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
