# tiktoken_cache

`fb374d419588a4632f3f557e76b4b70aebbca790` is tiktoken's `o200k_base` encoding, unchanged. tiktoken names a cached
encoding by the SHA-1 of its download URL; this one is
`https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken` (3,613,922 bytes).

Why it ships: Inspect AI's context compaction estimates tokens with `o200k_base` until the model's first reply
calibrates the count (`inspect_ai/model/_tokens.py`). The A1 payload's tests run in a container with no network
(`foundry/fleet_deploy_tolkien.py`'s A1 gate: `docker run --network none ... -e TIKTOKEN_CACHE_DIR=` this folder), so the
encoding has to be here already, or tiktoken would try to download it and the tests would fail. On a factory the A1
venv keeps its own copy beside itself (`a1/a1_chat.py`, `tiktoken-cache` under the venv).

To refresh it: download that URL and save it here under the same name.
