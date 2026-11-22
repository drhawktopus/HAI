#!/usr/bin/env bash
# Split inference: Unsloth's MTP build of the tablet head's A3B quant. Same upstream filename as the live model, so it
# lands under its OWN name. Resumable (.part), size + sha256 checked against Hugging Face before the rename.
set -uo pipefail
BUILD_HOME="${BUILD_HOME:-$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)}"   # the home of whoever ran it (run_remote runs it through sudo, so $HOME is root's)
D=$BUILD_HOME/models
URL="https://huggingface.co/unsloth/Qwen3.6-35B-A3B-MTP-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf"
OUT="$D/Qwen3.6-35B-A3B-MTP-UD-Q3_K_XL.gguf"
SIZE=17227569440
SHA=3fba9ab57290b34726837521335bf9268397174f3dd662880e2d0c5264d17b81
LOG=$BUILD_HOME/llama-build-rpcasync/fetch-mtp.log
exec >> "$LOG" 2>&1
echo "== start $(date -Is)"
test -e "$OUT" && { echo "already present: $OUT"; echo "FETCH-MTP DONE"; exit 0; }
for i in 1 2 3 4 5; do
  curl -L --fail -C - -o "$OUT.part" "$URL" && break
  echo "curl attempt $i rc=$? at $(stat -c %s "$OUT.part" 2>/dev/null) bytes"; sleep 10
done
got=$(stat -c %s "$OUT.part")
[ "$got" = "$SIZE" ] || { echo "SIZE MISMATCH $got != $SIZE"; echo "FETCH-MTP FAILED"; exit 1; }
h=$(sha256sum "$OUT.part" | cut -c1-64)
[ "$h" = "$SHA" ] || { echo "SHA MISMATCH $h"; echo "FETCH-MTP FAILED"; exit 1; }
mv "$OUT.part" "$OUT"
ls -la "$OUT"
echo "FETCH-MTP DONE $(date -Is)"
