#!/bin/bash
# Rebuild llama.cpp's multimodal CLI for Android arm64 WITH THE VULKAN BACKEND.
#
# WHY: a CPU-ONLY build (no Vulkan flag) measured SmolVLM-500M on gandalf at 51 s wall, ~377
# CPU-seconds for a yes/no, which says nothing about the platform's GPU. Both SoC families in the
# Fellowship expose Vulkan 1.1 with the compute feature (vulkan.mt6765.so, vulkan.msm8937.so) and
# libOpenCL, so the CPU figure is not a verdict on the platform.
#
# Built alongside the CPU binary rather than replacing it: the comparison must be same model, same
# frame, same tablet, only the backend differing -- one variable.
#
# x86 container required (see ndk_whisper.sh). Take the LARGEST matching binary, not the first:
# whisper.cpp ships a 416 KB deprecation stub beside its 19.8 MB real binary.
set -u
rm -f /root/LLAMAVK_DONE /root/LLAMAVK_FAILED
fail() { echo "$1" > /root/LLAMAVK_FAILED; echo "FAILED: $1"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq > /root/vk_apt.log 2>&1
apt-get install -y -qq wget unzip cmake git build-essential glslang-tools >> /root/vk_apt.log 2>&1 \
  || fail apt

cd /root || fail cd
NDK="/root/android-ndk-r27c"
[ -f "$NDK/build/cmake/android.toolchain.cmake" ] || fail ndk-missing
[ -d llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp > /root/vk_clone.log 2>&1 \
  || fail clone
cd llama.cpp || fail cd2

rm -rf build-vulkan
cmake -B build-vulkan \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-24 \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_VULKAN=ON \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=ON \
  -DLLAMA_BUILD_TOOLS=ON \
  -DBUILD_SHARED_LIBS=OFF \
  > /root/vk_cmake.log 2>&1 || fail cmake

# assert Vulkan really is on -- a silent fallback to CPU would produce a working binary that
# quietly answers the wrong question, which is the whole failure mode this rebuild exists to fix
grep -qiE "GGML_VULKAN:BOOL=(ON|1)" build-vulkan/CMakeCache.txt || fail vulkan-not-enabled

cmake --build build-vulkan -j"$(nproc)" --target llama-mtmd-cli > /root/vk_make.log 2>&1 \
  || cmake --build build-vulkan -j"$(nproc)" >> /root/vk_make.log 2>&1 || fail make

BIN=$(find build-vulkan -type f -name 'llama-mtmd-cli' -printf '%s %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
[ -n "$BIN" ] || fail nobinary
SZ=$(stat -c%s "$BIN"); [ "$SZ" -gt 1000000 ] || fail "binary-too-small-$SZ"
STRIP=$(find "$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin" -name llvm-strip | head -1)
"$STRIP" -o /root/llama-mtmd-cli-vulkan "$BIN" || cp "$BIN" /root/llama-mtmd-cli-vulkan

echo "--- built:"
ls -la /root/llama-mtmd-cli-vulkan
file /root/llama-mtmd-cli-vulkan 2>/dev/null || true
touch /root/LLAMAVK_DONE
echo "LLAMAVK_DONE"
