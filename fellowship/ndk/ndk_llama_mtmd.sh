#!/bin/bash
# Cross-compile llama.cpp's MULTIMODAL cli (llama-mtmd-cli) for Android/bionic arm64,
# so the Fellowship can run a small vision-language model natively in Termux (no proot).
#
# This is the SIBLING of ndk_whisper.sh, deliberately: whisper.cpp and llama.cpp share ggml and
# the same cmake/toolchain shape, and that script is the lab's PROVEN path to a binary that runs
# on these tablets. Reusing the known-good pipeline instead of inventing a second one.
#
# MUST run in an explicitly x86 container (`docker run --platform linux/amd64`). A plain
# `docker run debian` on this host comes up aarch64 and the x86 NDK toolchain dies with
# "cannot execute: required file not found" (ELF/arch mismatch).
set -u
rm -f /root/LLAMA_DONE /root/LLAMA_FAILED
fail() { echo "$1" > /root/LLAMA_FAILED; echo "FAILED: $1"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq > /root/llama_apt.log 2>&1
apt-get install -y -qq wget unzip cmake git build-essential >> /root/llama_apt.log 2>&1 || fail apt

cd /root || fail cd
NDK_VER=r27c
if [ ! -d "android-ndk-$NDK_VER" ]; then
  wget -q "https://dl.google.com/android/repository/android-ndk-$NDK_VER-linux.zip" -O ndk.zip || fail ndk-download
  unzip -q ndk.zip || fail ndk-unzip
fi
NDK="/root/android-ndk-$NDK_VER"
[ -f "$NDK/build/cmake/android.toolchain.cmake" ] || fail ndk-missing-toolchain

[ -d llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp > /root/llama_clone.log 2>&1 || fail clone
cd llama.cpp || fail cd2
rm -rf build-android

# LLAMA_CURL=OFF: the curl dependency does not cross-compile cleanly and mtmd does not need it
# (models are staged by hand). BUILD_SHARED_LIBS=OFF keeps the binary self-contained apart from
# libomp, which the NDK links dynamically and must be shipped alongside (whisper hit this too).
cmake -B build-android \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-24 \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=ON \
  -DLLAMA_BUILD_TOOLS=ON \
  -DBUILD_SHARED_LIBS=OFF \
  > /root/llama_cmake.log 2>&1 || fail cmake

cmake --build build-android -j"$(nproc)" --target llama-mtmd-cli > /root/llama_make.log 2>&1 \
  || cmake --build build-android -j"$(nproc)" >> /root/llama_make.log 2>&1 \
  || fail make

# Take the REAL binary, not a deprecation stub. whisper.cpp ships a 416 KB stub named
# identically to the 19.8 MB binary, so a naive `find | head -1` can grab the stub. Pick the LARGEST
# match and assert it is plausibly big.
BIN=$(find build-android -type f -name 'llama-mtmd-cli' -printf '%s %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
[ -n "$BIN" ] || fail nobinary
SZ=$(stat -c%s "$BIN")
[ "$SZ" -gt 1000000 ] || fail "binary-too-small-$SZ-probably-a-stub"
cp "$BIN" /root/llama-mtmd-cli-android || fail copy

# libomp.so — NDK links OpenMP dynamically; without it the binary dies at load time on the tablet.
OMP=$(find "$NDK/toolchains/llvm/prebuilt/linux-x86_64" -name libomp.so | head -1)
[ -n "$OMP" ] && cp "$OMP" /root/libomp.so

echo "--- built:"
ls -la /root/llama-mtmd-cli-android /root/libomp.so 2>/dev/null
file /root/llama-mtmd-cli-android 2>/dev/null || true
touch /root/LLAMA_DONE
echo "LLAMA_DONE"
