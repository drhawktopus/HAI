#!/bin/bash
# Build hwdec for Android arm64 in the ndkbuild container.
# Links libmediandk (the NDK MediaCodec C API) -- no JVM, no JNI, no app.
set -u
rm -f /root/HWDEC_DONE /root/HWDEC_FAILED
fail() { echo "$1" > /root/HWDEC_FAILED; echo "FAILED: $1"; exit 1; }

NDK="/root/android-ndk-r27c"
TC="$NDK/toolchains/llvm/prebuilt/linux-x86_64"
# API 28 is not required by any symbol: hwdec.c makes no `AMediaCodecList_findDecoderForFormat`
# call, and no enumeration exists in the NDK C API (there is no NdkMediaCodecList.h in the sysroot).
# Selection is `AMediaCodec_createCodecByName` walking the HEVC_TRY[]/AVC_TRY[] tables, and a
# candidate must also DELIVER A FRAME to be kept. API 28 is kept because it is harmless and every
# node is well past it (Android 11/12 = API 30/31).
CC="$TC/bin/aarch64-linux-android28-clang"

[ -x "$CC" ]              || fail "no-clang"
[ -f /root/hwdec.c ]      || fail "no-source"
"$CC" -O2 -std=c11 /root/hwdec.c -o /root/hwdec -lmediandk -llog \
  > /root/hwdec_build.log 2>&1 || { tail -25 /root/hwdec_build.log; fail "compile"; }

# Same Termux trap as the ncnn work: a missing shared lib is invisible until exec.
READELF="$TC/bin/llvm-readelf"
NEEDED=$("$READELF" -d /root/hwdec | grep NEEDED | sed 's/.*\[\(.*\)\]/\1/')
echo "--- NEEDED: $(echo "$NEEDED" | tr '\n' ' ')"
echo "$NEEDED" | grep -qx "libc++_shared.so" && fail "links libc++_shared - Termux lacks it"

ls -la /root/hwdec
file /root/hwdec
touch /root/HWDEC_DONE
echo HWDEC_DONE
