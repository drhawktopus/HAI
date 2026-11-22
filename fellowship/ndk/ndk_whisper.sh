#!/bin/bash
# Cross-compile whisper.cpp for Android/bionic arm64 using the NDK, on x86 hardware.
# Produces a binary that runs NATIVELY in Termux on the tablets (no proot).
set -u
rm -f /root/NDK_DONE /root/NDK_FAILED
fail() { echo "$1" > /root/NDK_FAILED; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq > /root/ndk_apt.log 2>&1
apt-get install -y -qq wget unzip cmake git build-essential >> /root/ndk_apt.log 2>&1 || fail apt
cd /root || fail cd
NDK_VER=r27c
if [ ! -d "android-ndk-$NDK_VER" ]; then
  wget -q "https://dl.google.com/android/repository/android-ndk-$NDK_VER-linux.zip" -O ndk.zip || fail ndk-download
  unzip -q ndk.zip || fail ndk-unzip
fi
NDK="/root/android-ndk-$NDK_VER"
[ -f "$NDK/build/cmake/android.toolchain.cmake" ] || fail ndk-missing-toolchain
[ -d whisper.cpp ] || git clone --depth 1 https://github.com/ggerganov/whisper.cpp > /root/ndk_clone.log 2>&1 || fail clone
cd whisper.cpp || fail cd2
rm -rf build-android
cmake -B build-android \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-24 \
  -DCMAKE_BUILD_TYPE=Release \
  -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON \
  -DBUILD_SHARED_LIBS=OFF \
  > /root/ndk_cmake.log 2>&1 || fail cmake
cmake --build build-android -j"$(nproc)" > /root/ndk_make.log 2>&1 || fail make
BIN=$(find build-android -type f \( -name whisper-cli -o -name main \) | head -1)
[ -n "$BIN" ] || fail nobinary
cp "$BIN" /root/whisper-cli-android || fail copy
mkdir -p /root/models
for m in tiny.en base.en; do
  f="/root/models/ggml-$m.bin"
  [ -s "$f" ] || wget -q -O "$f" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$m.bin" || fail "model-$m"
done
file /root/whisper-cli-android 2>/dev/null || true
ls -la /root/whisper-cli-android /root/models/
touch /root/NDK_DONE
