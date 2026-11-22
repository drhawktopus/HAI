#!/bin/bash
# Cross-compile ncnn (Tencent) WITH VULKAN for Android arm64, plus benchncnn.
#
# WHY ncnn: it is purpose-built for Android inference, uses Vulkan compute, and cross-compiles with
# the same NDK toolchain already proven twice here (whisper.cpp, llama.cpp). onnxruntime and
# tflite-runtime have NO Termux/py3.14 wheels, so this is the realistic detector path.
#
# WHY benchncnn: it runs the SAME standard models on CPU and GPU and reports per-model milliseconds,
# a fair comparison from a tool the lab did not write rather than a hand-rolled harness that could
# flatter whichever side was built first.
#   usage: benchncnn [loops] [threads] [powersave] [gpu_device] [cooldown]
#          gpu_device: -1 = CPU, 0 = first Vulkan device
#
# MUST run in an explicitly x86 container (docker run --platform linux/amd64) -- a plain
# `docker run debian` on this host comes up aarch64 and the x86 NDK toolchain dies with
# "cannot execute: required file not found".
# Vulkan needs the glslang submodule; a plain clone builds without it and silently loses GPU.
set -u
rm -f /root/NCNN_DONE /root/NCNN_FAILED
fail() { echo "$1" > /root/NCNN_FAILED; echo "FAILED: $1"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq > /root/ncnn_apt.log 2>&1
apt-get install -y -qq wget unzip cmake git build-essential >> /root/ncnn_apt.log 2>&1 || fail apt

cd /root || fail cd
NDK="/root/android-ndk-r27c"
[ -f "$NDK/build/cmake/android.toolchain.cmake" ] || fail ndk-missing

if [ ! -d ncnn ]; then
  # --recursive: glslang is a submodule and Vulkan support needs it
  git clone --depth 1 --recursive https://github.com/Tencent/ncnn > /root/ncnn_clone.log 2>&1 \
    || fail clone
fi
cd ncnn || fail cd2
[ -d glslang/glslang ] || git submodule update --init --recursive >> /root/ncnn_clone.log 2>&1

rm -rf build-android
cmake -B build-android \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-24 \
  -DCMAKE_BUILD_TYPE=Release \
  -DNCNN_VULKAN=ON \
  -DNCNN_BUILD_BENCHMARK=ON \
  -DNCNN_BUILD_EXAMPLES=OFF \
  -DNCNN_BUILD_TOOLS=OFF \
  -DNCNN_SHARED_LIB=OFF \
  > /root/ncnn_cmake.log 2>&1 || fail cmake

# assert Vulkan actually got enabled -- a silent fallback to CPU-only would make the whole
# GPU-vs-CPU comparison meaningless while still producing a working binary
grep -qiE "NCNN_VULKAN.*(ON|1)" build-android/CMakeCache.txt || fail vulkan-not-enabled

cmake --build build-android -j"$(nproc)" > /root/ncnn_make.log 2>&1 || fail make

BIN=$(find build-android -type f -name benchncnn -printf '%s %p\n' | sort -rn | head -1 | cut -d' ' -f2-)
[ -n "$BIN" ] || fail nobinary
SZ=$(stat -c%s "$BIN"); [ "$SZ" -gt 500000 ] || fail "binary-too-small-$SZ"
cp "$BIN" /root/benchncnn-android || fail copy

# the .param files benchncnn reads (model graphs, no weights needed -- it uses random data)
tar -czf /root/ncnn-benchparams.tgz -C benchmark $(cd benchmark && ls *.param | tr '\n' ' ') \
  2>/dev/null || fail params

echo "--- built:"
ls -la /root/benchncnn-android /root/ncnn-benchparams.tgz
file /root/benchncnn-android 2>/dev/null || true
grep -i "vulkan" build-android/CMakeCache.txt | head -3
touch /root/NCNN_DONE
echo "NCNN_DONE"
