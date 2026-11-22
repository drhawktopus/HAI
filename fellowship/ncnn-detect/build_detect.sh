#!/bin/bash
# Build the resident ncnn detector for Android arm64, INSIDE the existing `ndkbuild` container.
#
# This does NOT rebuild ncnn: ndk_ncnn.sh cross-compiles it, and its static libs stay in
# /root/ncnn/build-android in the running `ndkbuild` container (~20 minutes of build). Reusing them
# is the difference between a two-minute step and a fresh clone.
#
# MUST run in the x86 container. A plain container on Sundae comes up aarch64 and the x86 NDK
# toolchain dies with "cannot execute: required file not found".
set -u
rm -f /root/DETECT_DONE /root/DETECT_FAILED
fail() { echo "$1" > /root/DETECT_FAILED; echo "FAILED: $1"; exit 1; }

NDK="/root/android-ndk-r27c"
NCNN="/root/ncnn"
BUILD="$NCNN/build-android"
CXX="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android24-clang++"

[ -x "$CXX" ]                    || fail "no-clang++"
[ -f "$BUILD/src/libncnn.a" ]    || fail "no-libncnn -- the ndkbuild container lost its build tree"
[ -f /root/ncnn_detect.cpp ]     || fail "no-source"

# ncnn was built with NCNN_VULKAN=ON, so libncnn.a carries unresolved glslang/SPIRV symbols even
# though we never enable Vulkan at runtime. Link every static lib in the tree inside a group so the
# order between them does not matter -- getting that order wrong by hand is a 20-minute detour.
LIBS=$(find "$BUILD" -name '*.a' | sort | tr '\n' ' ')
[ -n "$LIBS" ] || fail "no-static-libs"
echo "--- linking against:"; echo "$LIBS" | tr ' ' '\n' | sed '/^$/d'

# -static-libstdc++ IS NOT OPTIONAL. The NDK links libc++_shared.so by default and Termux does
# not ship it, so the binary builds perfectly, passes `file`, and then dies at exec with
# "CANNOT LINK EXECUTABLE". Nothing before the tablet catches that, so the readelf assertion
# below checks it.
"$CXX" -O3 -std=c++17 -fopenmp -static-openmp -static-libstdc++ \
  -I"$NCNN/src" -I"$BUILD/src" \
  /root/ncnn_detect.cpp -o /root/ncnn_detect \
  -Wl,--start-group $LIBS -Wl,--end-group \
  -lvulkan -lz -lm -llog -landroid \
  > /root/detect_build.log 2>&1 || { tail -30 /root/detect_build.log; fail "compile"; }

SZ=$(stat -c%s /root/ncnn_detect)
[ "$SZ" -gt 200000 ] || fail "binary-too-small-$SZ"

# Assert the runtime deps are ones Termux ACTUALLY HAS. A missing shared library is invisible to
# every check that stops at "it compiled"; only the target would have told us, and it tells us by
# failing in service. libc++_shared and libomp are the two the NDK reaches for by default.
READELF="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
NEEDED=$("$READELF" -d /root/ncnn_detect | grep NEEDED | sed 's/.*\[\(.*\)\]/\1/')
echo "--- NEEDED: $(echo "$NEEDED" | tr '\n' ' ')"
for bad in libc++_shared.so libomp.so; do
  echo "$NEEDED" | grep -qx "$bad" && fail "links $bad -- Termux does not ship it; link it statically"
done

echo "--- built:"
ls -la /root/ncnn_detect
file /root/ncnn_detect
touch /root/DETECT_DONE
echo "DETECT_DONE"
