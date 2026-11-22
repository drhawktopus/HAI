# Tablet fleet job #1 — Whisper subtitle generation (PROVEN 2026-08-05)

**The fellowship's first real workload.**

This process generates subtitles for the tablet fleet using the Whisper model without requiring GPU allocation. For current state details, see PROJECT-BACKLOG §E Media stack.

Fleet context: [[Fellowship-Crunch-Integration]]

## Whisper.cpp throughput benchmarks (gandalf, 8× A53, `ggml-tiny.en`, `-t 8`)

Benchmark measurements for whisper.cpp transcription performance on gandalf hardware configuration.

| test | audio duration | wall time | real-time ratio |
|---|--:|--:|--:|
| smoke (`-d 60000`) | 60 s | 30.8 s | 1.95× |
| full episode | 1794 s (29m54s) | 852 s (14m13s) | 2.10× |

Output characteristics: 2884-line `.srt` file with correct timings, music cues (`♪♪`), and speaker dashes preserved. Quality is model-grade; example error: "old people and magazines" for "old *People* magazines".

`base.en` (148 MB) provides higher quality at ~2-3× the processing time. Measure before selecting model.

Fleet throughput estimate: ~2 episodes/hour/node × 5 nodes ≈ 10 episodes/hour (~240/day). Power consumption: ~35 W. Zero GPU load required. Audio extract runs only on Plex host.

## WHY IT FITS (the compute-per-byte rule)
The data payload is ~57 MB uncompressed or ~15 MB compressed. Processing time averages ~14 minutes; results return as a ~100 KB SRT file.
One instance constitutes an independent work unit.
Node disconnection from WiFi incurs the cost of one retried episode, avoiding whole-job failure.

## THE PIPELINE (proven end to end)

1.  Extract audio on SUNDAE. Sundae's HOST runs in a container with `-v /mnt/media:/media:ro` because the host lacks `ffmpeg`. Tablets must never receive video.
2.  Ship the extracted WAV file to a tablet (~57 MB per 30-min episode).
3.  Run Whisper on the tablet:
    
    ```bash
    LD_LIBRARY_PATH=$HOME/lib ./whisper-cli -m models/ggml-tiny.en.bin -f in.wav -t 8 -osrt -of OUT
    ```
4.  Return and file the resulting `.srt` beside the media.

## ARTIFACTS ON THE TABLETS (gandalf has all of these; clone to the rest)
| path | what | size |
|---|---|--:|
| `~/whisper-cli` | whisper.cpp CLI, arm64 bionic | 19.8 MB |
| `~/lib/libomp.so` | **required** — NDK links OpenMP dynamically | 1.2 MB |
| `~/models/ggml-tiny.en.bin` | tiny English model | 78 MB |

The executable requires `LD_LIBRARY_PATH=$HOME/lib`. Without this, execution fails with `CANNOT LINK EXECUTABLE ... library "libomp.so" not found`.

## BUILDING IT (do this on a VM, never on a tablet)

Android NDK **r27c** cross-compile, on Sundae, in an **explicitly x86** container:
```bash
docker run -d --name ndkbuild --platform linux/amd64 debian:trixie sleep infinity
cmake -B build-android -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
      -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-24 -DCMAKE_BUILD_TYPE=Release
```
Script: session scratchpad `ndk_whisper.sh`. Produces binaries that run **natively in Termux — no
proot**, which is what you want for production work units.

## ⚠ THREE TRAPS THAT COST TIME
### ⚠ MANDATORY LINUX PLATFORM FLAG ON SUNDAY
The build mandates the `--platform linux/amd64` flag. Without this, Docker defaults to ARM architectures on Sundae.
* **Reason**: The arm64 binfmt installer establishes aarch64 as the default platform; omitting the flag causes images like Debian to spawn on ARM hardware, leading x86 NDK toolchains to fail with `cannot execute: required file not found` due to ELF/arch mismatches.
