"""[P20-TECACHE] The lab's FLUX trick for the Krea 2 trainer (FOUNDRY_AITK_KREA2): do not load the 8.9 GB Qwen3-VL
text encoder at start-up when nothing needs it.

Measured on a start-up: loading the text encoder + VAE took 47 s of a 3 min 55 s start, and with every
caption already in the trainer's own per-item cache the encoder was used only for the trainer's STATIC prompts (blank,
unconditional, trigger) and then unloaded.

What it does, ONLY when the launcher sets P20_TE_LAZY=1 (opt-in; default behaviour is untouched):
 * load_model puts the toolkit's own FakeTextEncoder where the encoder would be (its .to/.free/unload paths already
   handle that class) and loads only the tokenizers;
 * static prompts (SDTrainer.encode_static_prompt) are served from a content-addressed cache (P20_TE_CACHE_DIR, else
   beside the encoder folder): one file per (the encoder's weight/config files' names, sizes and mtimes, max length,
   selected layers, dtype, prompt text), and the full key is
   stored inside the file and compared on load - a cache entry can never answer for a different encoder or prompt
   (a cache keyed by trigger word alone answers for captions that have since changed);
 * anything not cached - a static prompt seen for the first time, a caption whose per-item cache is missing, a sample
   prompt - loads the real encoder on demand, exactly as before, and says so in the log.
Edit models (reference images) are never deferred.

Usage on the box:     python p20_apply_te_cache.py [--target-root <tree>] [--revert]"""
import os
import shutil
import sys


def _need(name):
    """A setting this setup script cannot run without (an environment variable): its value, or a refusal naming it."""
    v = os.environ.get(name, "")
    if not v:
        sys.exit("set %s (see this script's docstring)" % name)
    return v

ROOT = _need("FOUNDRY_AITK_KREA2")
MARK = b"[P20-TECACHE]"


def crlf(s):
    return s.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


KREA_IMPORT_OLD = "from .src.pipeline import Krea2Pipeline, pad_text_features, predict_velocity\n"
KREA_IMPORT_NEW = KREA_IMPORT_OLD + '''
# [P20-TECACHE] deferred text encoder + content-addressed static-prompt cache (setup/p20_apply_te_cache.py)
import hashlib as _p20_hashlib
import json as _p20_json
import time as _p20_time
from toolkit.unloader import FakeTextEncoder


def _p20_te_lazy(model):
    return os.environ.get("P20_TE_LAZY") == "1" and not model.is_edit


def _p20_te_path(model):
    return model.model_config.model_kwargs.get("text_encoder_path", QWEN3_VL_PATH)


def _p20_static_key(model, prompts):
    te = _p20_te_path(model)
    files = []
    if os.path.isdir(te):
        for n in sorted(os.listdir(te)):
            p = os.path.join(te, n)
            if os.path.isfile(p) and n.endswith((".safetensors", ".bin", ".json")):
                st = os.stat(p)
                files.append([n, st.st_size, int(st.st_mtime)])
    # identity = the encoder's files (name, size, mtime), not its folder: a verified copy on another drive is the
    # same encoder (robocopy keeps mtimes), so it shares one cache
    return _p20_json.dumps({"v": 1, "te_files": files,
                            "max_len": model.max_text_length, "layers": list(SELECT_LAYERS),
                            "dtype": str(model.torch_dtype), "prompts": list(prompts)}, sort_keys=True)


def _p20_static_path(model, key):
    te = _p20_te_path(model).rstrip("\\\\/")
    d = os.environ.get("P20_TE_CACHE_DIR") or os.path.join(os.path.dirname(te), "p20_te_static_cache")
    return os.path.join(d, _p20_hashlib.sha256(key.encode("utf-8")).hexdigest() + ".pt")


def _p20_static_get(model, prompts):
    key = _p20_static_key(model, prompts)
    p = _p20_static_path(model, key)
    if not os.path.exists(p):
        return None
    d = torch.load(p, map_location="cpu", weights_only=False)
    if d.get("key") != key:
        return None
    return AdvancedPromptEmbeds(text_embeds=[t.to(model.device_torch) for t in d["text_embeds"]])


def _p20_static_put(model, prompts, embeds):
    key = _p20_static_key(model, prompts)
    p = _p20_static_path(model, key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    torch.save({"key": key, "text_embeds": [t.detach().to("cpu") for t in embeds.text_embeds]}, tmp)
    os.replace(tmp, p)
'''

KREA_LOAD_OLD = '''        tokenizer, processor, vl_processor, text_encoder = self._load_text_encoder()
        text_encoder.aitk_post_load(**self.component_load_kwargs("te"))
        flush()
'''
KREA_LOAD_NEW = '''        if _p20_te_lazy(self):
            # [P20-TECACHE] the text encoder loads only when a prompt is not cached (see _p20_te_real)
            tokenizer, processor, vl_processor = self._p20_load_tokenizers()
            text_encoder = FakeTextEncoder(device=self.device_torch, dtype=self.torch_dtype)
            self.print_and_status_update("[P20-TECACHE] text encoder deferred - it loads on the first prompt that is "
                                         "not cached")
        else:
            tokenizer, processor, vl_processor, text_encoder = self._load_text_encoder()
            text_encoder.aitk_post_load(**self.component_load_kwargs("te"))
        flush()
'''

KREA_TOKS_OLD = "    def _load_vae(self):\n"
KREA_TOKS_NEW = '''    def _p20_load_tokenizers(self):
        # [P20-TECACHE] the light half of _load_text_encoder (same calls, same arguments)
        te_path = _p20_te_path(self)
        tokenizer = AutoTokenizer.from_pretrained(te_path, max_length=self.max_text_length, token=HF_TOKEN)
        processor = Qwen2TokenizerFast.from_pretrained(te_path, max_length=self.max_text_length, token=HF_TOKEN)
        return tokenizer, processor, None

    def _p20_te_real(self, why):
        # [P20-TECACHE] load the real encoder on demand, exactly as load_model would have
        t0 = _p20_time.time()
        tokenizer, processor, vl_processor, text_encoder = self._load_text_encoder()
        text_encoder.aitk_post_load(**self.component_load_kwargs("te"))
        self.tokenizer, self.processor, self.vl_processor = tokenizer, processor, vl_processor
        self.text_encoder = text_encoder
        flush()
        self.print_and_status_update("[P20-TECACHE] text encoder loaded on demand (%s) in %.0f s"
                                     % (why, _p20_time.time() - t0))

''' + KREA_TOKS_OLD

KREA_EMB_OLD = '''        if isinstance(prompt, str):
            prompt = [prompt]

        if self.text_encoder.device == torch.device("cpu"):
'''
KREA_EMB_NEW = '''        if isinstance(prompt, str):
            prompt = [prompt]

        # [P20-TECACHE] static prompts from the content-addressed cache; anything else needs the real encoder
        _p20_static = _p20_te_lazy(self) and getattr(self, "_p20_static", False) and control_images is None
        if _p20_static:
            _hit = _p20_static_get(self, prompt)
            if _hit is not None:
                return _hit
        if isinstance(self.text_encoder, FakeTextEncoder):
            self._p20_te_real("static prompt not cached yet" if _p20_static else "a prompt with no cache entry")

        if self.text_encoder.device == torch.device("cpu"):
'''

KREA_RET_OLD = "        return AdvancedPromptEmbeds(text_embeds=features_list)\n"
KREA_RET_NEW = '''        _p20_out = AdvancedPromptEmbeds(text_embeds=features_list)
        if _p20_static:
            _p20_static_put(self, prompt, _p20_out)   # [P20-TECACHE]
        return _p20_out
'''

SDT_OLD = "    def encode_static_prompt(self, prompt, **kwargs):\n"
SDT_NEW = '''    def encode_static_prompt(self, prompt, **kwargs):
        # [P20-TECACHE] mark static prompts so the model may serve them from its content-addressed cache
        self.sd._p20_static = True
        try:
            return self._p20_encode_static_prompt(prompt, **kwargs)
        finally:
            self.sd._p20_static = False

    def _p20_encode_static_prompt(self, prompt, **kwargs):
'''

FILES = {
    os.path.join("extensions_built_in", "diffusion_models", "krea2", "krea2.py"):
        [(KREA_IMPORT_OLD, KREA_IMPORT_NEW), (KREA_LOAD_OLD, KREA_LOAD_NEW), (KREA_TOKS_OLD, KREA_TOKS_NEW),
         (KREA_EMB_OLD, KREA_EMB_NEW), (KREA_RET_OLD, KREA_RET_NEW)],
    os.path.join("extensions_built_in", "sd_trainer", "SDTrainer.py"): [(SDT_OLD, SDT_NEW)],
}


def main():
    args = sys.argv[1:]
    root = args[args.index("--target-root") + 1] if "--target-root" in args else ROOT
    paths = {rel: os.path.join(root, rel) for rel in FILES}
    if "--revert" in args:
        for rel, p in paths.items():
            b = p + ".p20bak-tecache"
            if os.path.exists(b):
                shutil.copy2(b, p)
                print("reverted", p)
        return 0
    planned = {}
    for rel, edits in FILES.items():
        src = open(paths[rel], "rb").read()
        if MARK in src:
            print("already applied:", paths[rel])
            continue
        out = src
        for old, new in edits:
            n = out.count(crlf(old))
            if n != 1:
                print("REFUSED: %s - an anchor matched %d times (need 1):\n%s" % (paths[rel], n, old[:120]))
                return 2
            out = out.replace(crlf(old), crlf(new), 1)
        compile(out.decode("utf-8"), paths[rel], "exec")
        planned[rel] = (src, out)
    for rel, (src, out) in planned.items():          # all anchors checked first: nothing half-applied
        shutil.copy2(paths[rel], paths[rel] + ".p20bak-tecache")
        open(paths[rel], "wb").write(out)
        print("applied: %s (%d -> %d bytes), backup .p20bak-tecache" % (paths[rel], len(src), len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
