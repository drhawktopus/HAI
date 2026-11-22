#!/bin/bash
# Build yolo11_bench for Android arm64 inside the existing `ndkbuild` container.
# Same flags and same assertions as build_detect.sh -- see that file for why each one exists;
# the two that bite hardest are -static-libstdc++ (Termux has no libc++_shared.so, so the
# binary builds fine and dies at exec) and the whole-group link (libncnn.a carries unresolved
# Vulkan symbols even with Vulkan off at runtime).
set -u
rm -f /root/BENCH_DONE /root/BENCH_FAILED
fail() { echo "$1" > /root/BENCH_FAILED; echo "FAILED: $1"; exit 1; }

NDK="/root/android-ndk-r27c"
NCNN="/root/ncnn"
BUILD="$NCNN/build-android"
CXX="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android24-clang++"

[ -x "$CXX" ]                     || fail "no-clang++"
[ -f "$BUILD/src/libncnn.a" ]     || fail "no-libncnn"
[ -f /root/yolo11_bench.cpp ]     || fail "no-source"

LIBS=$(find "$BUILD" -name '*.a' | sort | tr '\n' ' ')
[ -n "$LIBS" ] || fail "no-static-libs"

"$CXX" -O3 -std=c++17 -fopenmp -static-openmp -static-libstdc++ \
  -I"$NCNN/src" -I"$BUILD/src" \
  /root/yolo11_bench.cpp -o /root/yolo11_bench \
  -Wl,--start-group $LIBS -Wl,--end-group \
  -lvulkan -lz -lm -llog -landroid \
  > /root/bench_build.log 2>&1 || { tail -30 /root/bench_build.log; fail "compile"; }

SZ=$(stat -c%s /root/yolo11_bench)
[ "$SZ" -gt 200000 ] || fail "binary-too-small-$SZ"

READELF="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
NEEDED=$("$READELF" -d /root/yolo11_bench | grep NEEDED | sed 's/.*\[\(.*\)\]/\1/')
echo "--- NEEDED: $(echo "$NEEDED" | tr '\n' ' ')"
for bad in libc++_shared.so libomp.so; do
  echo "$NEEDED" | grep -qx "$bad" && fail "links $bad -- Termux does not ship it"
done

# Stage the two model families under the names the binary expects (<prefix>.ncnn.param/bin).
for m in yolo11n yolo11s; do
  [ -f "/root/${m}_ncnn_model/model.ncnn.param" ] || fail "no-export-$m"
  cp "/root/${m}_ncnn_model/model.ncnn.param" "/root/${m}.ncnn.param"
  cp "/root/${m}_ncnn_model/model.ncnn.bin"   "/root/${m}.ncnn.bin"
done

echo "--- built:"
ls -la /root/yolo11_bench /root/yolo11n.ncnn.* /root/yolo11s.ncnn.*
file /root/yolo11_bench
touch /root/BENCH_DONE
echo "BENCH_DONE"
