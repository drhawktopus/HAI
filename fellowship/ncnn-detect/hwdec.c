/* hwdec -- decode H.265/H.264 on a tablet via the NDK MediaCodec C API, downscale on-device, and
 *          serve raw frames over TCP to any consumer that speaks ffmpeg's rawvideo demuxer.
 *
 * WHY THIS EXISTS: Sundae (Hyper-V guest) has NO video decode acceleration at all -- /dev/dri/card1
 * is hyperv_drm, a synthetic framebuffer, no VAAPI -- so decode costs it roughly one core per
 * camera in software. The tablets have idle video decoders. Pushing decode to them is the scaling
 * path to 8-10 cameras.
 *
 * THE FLEET IS THREE TABLET MODELS AND THEIR DECODERS DIFFER. ALL FIVE ARE CAPABLE ENOUGH;
 * only the decoder NAME that must be asked for changes. Verified on every node, each decoding
 * 2688x1520 @30 with no per-node configuration:
 *
 *      gandalf        c2.mtk.hevc.decoder          hardware, ALSO the only node that does 4K,
 *                                                  and sustains FOUR concurrent 2K30 streams
 *      frodo, sam     OMX.MTK.VIDEO.DECODER.HEVC   hardware
 *      merry, pippin  c2.android.hevc.decoder      SOFTWARE (Codec2) -- their qcom decoder will
 *                                                  not configure 2688x1520; this one delivers
 *
 * **The HEVC_TRY[] table below is the authoritative account** -- if this header and that table
 * ever disagree, the table is right and this header is stale. A file that contradicts itself
 * teaches whichever half the reader hits first.
 *
 * WHY NOT ffmpeg: Termux's ffmpeg HAS hevc_mediacodec compiled in, but its wrapper goes through
 * JNI and the CLI has no JVM -- it prints "No Java virtual machine has been registered" and
 * silently falls back to SOFTWARE (measured: 4K at 8 fps, 0.5x realtime, useless). The NDK's
 * AMediaCodec is a pure C API that needs no JVM at all. That is the whole trick.
 *
 * NO SURFACE is passed to AMediaCodec_configure(), so decoded frames land in ByteBuffers where we
 * can reach them. A surface would be faster but the pixels would go to a display we do not have.
 *
 * ⭐⭐ THE ONE THING THAT WILL BITE YOU AGAIN: DO NOT FEED MID-STREAM VPS/SPS/PPS.
 * The camera repeats its parameter sets before every IDR. Queueing those as ordinary input buffers
 * WEDGES this decoder -- it works for 6-8 seconds and then permanently stops returning input
 * buffers AND stops emitting frames, with no error from any API call. Measured on a
 * 2x2: baseline died at 96-126 frames every run; skipping them ran 896 frames at the full 15.4 fps
 * source rate. The parameter sets are redundant because the same bytes go in as csd-0 at
 * configure(). They are COMPARED, not blindly dropped -- see the feed loop -- because a camera that
 * reconnects can legitimately come back with different encoder settings.
 *
 * ── SERVE MODE (--serve PORT) ──────────────────────────────────────────────────────────────────
 * We listen; the CONSUMER's ffmpeg connects as a client and reads raw yuv420p. No patching of the
 * consumer required -- it is an ordinary ffmpeg input. (Frigate was the original consumer and was
 * retired; Tolkien inherits the role. The interface is unchanged,
 * which is the point of serving a standard format.)
 *
 *   inputs:
 *     - path: tcp://<tablet-address>:8803
 *       input_args: [-f, rawvideo, -pix_fmt, yuv420p, -video_size, 704x480, -framerate, "5"]
 *       roles: [detect]
 *
 * THE RTSP PULL IS OUR CHILD, STARTED ONLY ONCE A CLIENT IS CONNECTED (--input-cmd). That is
 * deliberate: a decoder that runs whether or not anyone is watching burns the tablet's battery to
 * produce frames nobody reads, and it makes "is this thing working?" unanswerable from the outside.
 * Client connects -> ffmpeg starts -> frames flow. Client leaves -> ffmpeg is killed -> back to
 * listening. Frigate's own reconnect logic therefore drives the whole lifecycle and there is no
 * second supervisor of mine to drift.
 *
 * RAW, NOT RE-ENCODED. 704x480 yuv420p at 5 fps is 20.3 Mbit/s, which the measured 5 GHz link
 * (317/164 Mbit/s) carries comfortably. Re-encoding would cost CPU on the tablet and quality on
 * the frame, to save bandwidth that is not scarce. Full-rate 4K would be 124 Mbit/s -- hence
 * --scale and --fps, which are what make this affordable rather than merely possible.
 *
 * BUILD: build_hwdec.sh, in the ndkbuild container. Links libmediandk.
 */

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/prctl.h>
#include <media/NdkMediaCodec.h>
#include <media/NdkMediaFormat.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define INBUF_CAP (4 * 1024 * 1024)

/* MediaCodec colour formats we can actually interpret. Anything else must FAIL LOUDLY: a wrong
 * guess here does not error, it emits plausible-looking garbage, and a sheared green frame that
 * still "detects" nothing is the worst possible failure mode. */
#define CF_I420 19 /* COLOR_FormatYUV420Planar    -- Y, then U, then V */
#define CF_NV12 21 /* COLOR_FormatYUV420SemiPlanar -- Y, then interleaved UV */

static volatile sig_atomic_t g_stop = 0;
static void on_sig(int s) { (void)s; g_stop = 1; }

/* ---- SELF-DEADLINE (--max-seconds) -------------------------------------------------------------
 * A decoder owns knowing its own budget, and it is the ONLY party that can always act on it.
 *
 * WHY THIS EXISTS, MEASURED. The agent's burst path already kills its children on timeout, and
 * that is not enough twice over:
 *   1. The agent cannot kill anything if the AGENT ITSELF dies mid-burst. A wedged 4K decoder can
 *      outlive its agent's death AND the boot supervisor's respawn, still burning ~90% of a core
 *      writing 0 bytes, until a manual `kill -9` by PID over ssh.
 *   2. PR_SET_PDEATHSIG does NOT cover it, though it holds for the live-serve path.
 *      So "the parent will clean up" is not a property this program may rely on.
 *
 * DELIBERATELY SIGALRM, NOT A CHECK IN THE LOOP. A loop test only fires if the loop is running,
 * and the failure being defended against is a decoder wedged INSIDE a MediaCodec call, where no
 * loop iteration ever completes. The kernel's timer does not need this process to be well.
 *
 * The handler is async-signal-safe: write(2) and _exit(), no fprintf, no malloc. Exit code 3 is
 * distinct so a caller can tell "I stopped myself" from a crash (>=128) or a clean end (0). */
static volatile sig_atomic_t g_deadline_s = 0;
static void on_deadline(int s) {
    (void)s;
    static const char msg[] = "hwdec: SELF-DEADLINE reached -- exiting rather than becoming an orphan\n";
    ssize_t ignored = write(2, msg, sizeof(msg) - 1);
    (void)ignored;
    _exit(3);
}

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* Find the next Annex-B start code (00 00 01 or 00 00 00 01) at or after `from`.
 * Returns offset of the 00, or -1. `sc_len` receives 3 or 4. */
static long find_sc(const uint8_t *b, long len, long from, int *sc_len) {
    for (long i = from; i + 3 <= len; i++) {
        if (b[i] == 0 && b[i + 1] == 0) {
            if (b[i + 2] == 1) { *sc_len = 3; return i; }
            if (i + 4 <= len && b[i + 2] == 0 && b[i + 3] == 1) { *sc_len = 4; return i; }
        }
    }
    return -1;
}

/* HEVC nal type = (byte0 >> 1) & 0x3f ; H.264 nal type = byte0 & 0x1f */
static int nal_type(const uint8_t *nal, int hevc) {
    return hevc ? ((nal[0] >> 1) & 0x3f) : (nal[0] & 0x1f);
}
static int is_vcl(int t, int hevc) {
    return hevc ? (t <= 31) : (t >= 1 && t <= 5);
}
/* This existed in the original file and was NEVER CALLED -- the author clearly intended to treat
 * parameter sets differently and never wired it up. I deleted it as dead code and had to put it
 * back an hour later. An unwired capability fails exactly like an absent one (doctrine class 17). */
static int is_param_set(int t, int hevc) {
    return hevc ? (t == 32 || t == 33 || t == 34)   /* VPS SPS PPS */
                : (t == 7 || t == 8);               /* SPS PPS */
}

/* Is `needle` present verbatim inside `hay`? Used to ask "is this mid-stream parameter set the same
 * one we already configured with?". memmem() is a GNU extension and bionic's availability varies by
 * API level, so this is spelled out rather than risking a link error on the tablet. */
static int mem_contains(const uint8_t *hay, size_t hlen, const uint8_t *needle, size_t nlen) {
    if (nlen == 0 || nlen > hlen) return 0;
    for (size_t i = 0; i + nlen <= hlen; i++)
        if (hay[i] == needle[0] && memcmp(hay + i, needle, nlen) == 0) return 1;
    return 0;
}

/* ── geometry of a decoded buffer ───────────────────────────────────────────────────────────────
 * THE DECODED BUFFER IS NOT width*height*1.5 AND ASSUMING SO IS THE CLASSIC MediaCodec BUG.
 * The hardware writes with a STRIDE (row pitch) and SLICE-HEIGHT (plane pitch) of its own choosing
 * -- 1080p commonly lands in a 1088-tall allocation -- and a CROP rectangle says which part is the
 * real picture. Reading it flat gives a sheared, green-fringed image that still looks like video,
 * which is precisely why this must be read from the format rather than assumed. */
typedef struct {
    int32_t stride, slice_h;
    int32_t crop_x, crop_y, crop_w, crop_h;
    int32_t colour;
} geom_t;

static void read_geom(AMediaFormat *of, int32_t w, int32_t h, geom_t *g) {
    /* String keys, not the AMEDIAFORMAT_KEY_* constants: STRIDE and SLICE_HEIGHT were only added
     * to the NDK headers at API 28 and this builds against 24. The underlying keys are stable. */
    if (!AMediaFormat_getInt32(of, "stride", &g->stride) || g->stride <= 0) g->stride = w;
    if (!AMediaFormat_getInt32(of, "slice-height", &g->slice_h) || g->slice_h <= 0) g->slice_h = h;

    int32_t l = 0, r = 0, t = 0, b = 0;
    if (AMediaFormat_getInt32(of, "crop-left", &l) && AMediaFormat_getInt32(of, "crop-right", &r) &&
        AMediaFormat_getInt32(of, "crop-top", &t) && AMediaFormat_getInt32(of, "crop-bottom", &b) &&
        r >= l && b >= t) {
        g->crop_x = l; g->crop_y = t; g->crop_w = r - l + 1; g->crop_h = b - t + 1;
    } else {
        g->crop_x = 0; g->crop_y = 0; g->crop_w = w; g->crop_h = h;
    }
    if (!AMediaFormat_getInt32(of, AMEDIAFORMAT_KEY_COLOR_FORMAT, &g->colour)) g->colour = 0;
}

/* ── downscale ──────────────────────────────────────────────────────────────────────────────────
 * Box average, integer. Nearest-neighbour was rejected: at 5.45x horizontal reduction it aliases
 * hard, and a detector fed aliased pixels is being handicapped in exactly the regime (small,
 * distant animals) this whole project cares about. Box averaging is a few adds per source pixel
 * and the decode is hardware anyway, so the CPU is free.
 *
 * `sub` is 1 for the luma plane and 2 for chroma, i.e. how much the plane is subsampled. */
static void box_scale_plane(const uint8_t *src, int src_stride, int sx0, int sy0, int sw, int sh,
                            uint8_t *dst, int dw, int dh) {
    for (int y = 0; y < dh; y++) {
        int y0 = sy0 + (int)((int64_t)y * sh / dh);
        int y1 = sy0 + (int)((int64_t)(y + 1) * sh / dh);
        if (y1 <= y0) y1 = y0 + 1;
        for (int x = 0; x < dw; x++) {
            int x0 = sx0 + (int)((int64_t)x * sw / dw);
            int x1 = sx0 + (int)((int64_t)(x + 1) * sw / dw);
            if (x1 <= x0) x1 = x0 + 1;
            uint32_t acc = 0, n = 0;
            for (int yy = y0; yy < y1; yy++) {
                const uint8_t *row = src + (size_t)yy * src_stride;
                for (int xx = x0; xx < x1; xx++) { acc += row[xx]; n++; }
            }
            dst[(size_t)y * dw + x] = (uint8_t)(acc / (n ? n : 1));
        }
    }
}

/* Same, for one interleaved chroma channel of NV12 (stride counts BYTES, pairs are UV). */
static void box_scale_nv12_c(const uint8_t *src, int src_stride, int sx0, int sy0, int sw, int sh,
                             int odd, uint8_t *dst, int dw, int dh) {
    for (int y = 0; y < dh; y++) {
        int y0 = sy0 + (int)((int64_t)y * sh / dh);
        int y1 = sy0 + (int)((int64_t)(y + 1) * sh / dh);
        if (y1 <= y0) y1 = y0 + 1;
        for (int x = 0; x < dw; x++) {
            int x0 = sx0 + (int)((int64_t)x * sw / dw);
            int x1 = sx0 + (int)((int64_t)(x + 1) * sw / dw);
            if (x1 <= x0) x1 = x0 + 1;
            uint32_t acc = 0, n = 0;
            for (int yy = y0; yy < y1; yy++) {
                const uint8_t *row = src + (size_t)yy * src_stride;
                for (int xx = x0; xx < x1; xx++) { acc += row[xx * 2 + odd]; n++; }
            }
            dst[(size_t)y * dw + x] = (uint8_t)(acc / (n ? n : 1));
        }
    }
}

/* Convert one decoded buffer into a tightly-packed I420 frame at dw x dh. Returns 0 on success. */
static int emit_i420(const uint8_t *src, const geom_t *g, uint8_t *dst, int dw, int dh) {
    const int cw = dw / 2, ch = dh / 2;
    uint8_t *dy = dst, *du = dst + (size_t)dw * dh, *dv = du + (size_t)cw * ch;

    box_scale_plane(src, g->stride, g->crop_x, g->crop_y, g->crop_w, g->crop_h, dy, dw, dh);

    const uint8_t *cplane = src + (size_t)g->stride * g->slice_h;
    const int ccx = g->crop_x / 2, ccy = g->crop_y / 2;
    const int ccw = g->crop_w / 2, cch = g->crop_h / 2;

    if (g->colour == CF_I420) {
        const int cstride = g->stride / 2;
        const uint8_t *u = cplane;
        const uint8_t *v = cplane + (size_t)cstride * (g->slice_h / 2);
        box_scale_plane(u, cstride, ccx, ccy, ccw, cch, du, cw, ch);
        box_scale_plane(v, cstride, ccx, ccy, ccw, cch, dv, cw, ch);
        return 0;
    }
    if (g->colour == CF_NV12) {
        box_scale_nv12_c(cplane, g->stride, ccx, ccy, ccw, cch, 0, du, cw, ch);
        box_scale_nv12_c(cplane, g->stride, ccx, ccy, ccw, cch, 1, dv, cw, ch);
        return 0;
    }
    return -1;
}

/* ── the RTSP child ─────────────────────────────────────────────────────────────────────────────
 * fork+exec rather than popen(), because we must be able to KILL it when the client goes away and
 * popen() gives us no pid. ffmpeg demuxes with -c:v copy: no decode, so this costs the tablet
 * almost nothing and the hardware does the only expensive part. */
static pid_t spawn_input(const char *cmd, int *out_fd) {
    int fds[2];
    if (pipe(fds) != 0) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(fds[0]); close(fds[1]); return -1; }
    if (pid == 0) {
        close(fds[0]);
        dup2(fds[1], STDOUT_FILENO);
        close(fds[1]);
        setpgid(0, 0);             /* own process group, so one kill takes the whole pipeline */
        execl("/system/bin/sh", "sh", "-c", cmd, (char *)NULL);
        execl("/bin/sh", "sh", "-c", cmd, (char *)NULL);
        _exit(127);
    }
    close(fds[1]);
    setpgid(pid, pid);
    *out_fd = fds[0];
    return pid;
}

static void reap_input(pid_t pid, int fd) {
    if (pid > 0) {
        kill(-pid, SIGTERM);
        for (int i = 0; i < 40; i++) {         /* 2 s, then insist */
            if (waitpid(pid, NULL, WNOHANG) == pid) { pid = 0; break; }
            usleep(50000);
        }
        if (pid > 0) { kill(-pid, SIGKILL); waitpid(pid, NULL, 0); }
    }
    if (fd >= 0) close(fd);
}

static int listen_on(int port) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return -1;
    int one = 1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    a.sin_port = htons((uint16_t)port);
    if (bind(s, (struct sockaddr *)&a, sizeof(a)) != 0) { close(s); return -1; }
    if (listen(s, 1) != 0) { close(s); return -1; }
    return s;
}

static int write_all(int fd, const uint8_t *b, size_t n) {
    while (n) {
        ssize_t k = write(fd, b, n);
        if (k <= 0) {
            if (k < 0 && (errno == EINTR)) continue;
            return -1;
        }
        b += k; n -= (size_t)k;
    }
    return 0;
}

/* Liveness comes from the DECODE LOOP ITSELF, never from a helper thread: a heartbeat written by
 * anything other than the worker can outlive the worker and vouch for a corpse. The frame
 * counter is in the beat so a FROZEN worker is visible
 * as a fresh file with a static count -- "recent" and "working" are different claims. */
static void beat(const char *path, int frames, double fps, int client, const char *state) {
    if (!path) return;
    char tmp[512];
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "{\"ts\": %.0f, \"frames\": %d, \"fps\": %.2f, \"client\": %d, \"state\": \"%s\"}\n",
            (double)time(NULL), frames, fps, client, state);
    fclose(f);
    rename(tmp, path);            /* atomic: a reader never sees a half-written beat */
}

int main(int argc, char **argv) {
    const char *mime = "video/hevc";
    int width = 3840, height = 2160, bench = 0, max_frames = 0;
    int out_w = 0, out_h = 0, serve_port = 0, target_fps = 0, once = 0;
    const char *out_path = NULL, *input_cmd = NULL, *beat_path = NULL;
    /* Force one specific decoder by name, bypassing the preference list. For diagnosis: it makes
     * "can THIS codec take these pixels" a single answerable question instead of a guess. */
    const char *force_codec = NULL;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--mime") && i + 1 < argc) mime = argv[++i];
        else if (!strcmp(argv[i], "--width") && i + 1 < argc) width = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--height") && i + 1 < argc) height = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--frames") && i + 1 < argc) max_frames = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) out_path = argv[++i];
        else if (!strcmp(argv[i], "--serve") && i + 1 < argc) serve_port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--input-cmd") && i + 1 < argc) input_cmd = argv[++i];
        else if (!strcmp(argv[i], "--heartbeat") && i + 1 < argc) beat_path = argv[++i];
        else if (!strcmp(argv[i], "--codec") && i + 1 < argc) force_codec = argv[++i];
        else if (!strcmp(argv[i], "--fps") && i + 1 < argc) target_fps = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--once")) once = 1;
        /* --skip-param-sets and --pts-slices-only were the two experiment arms. The first is now
         * unconditional (it WAS the bug); the second changed nothing and is not shipped. Accepted
         * and ignored so an old command line does not fail. */
        else if (!strcmp(argv[i], "--skip-param-sets")) { /* now always on */ }
        else if (!strcmp(argv[i], "--pts-slices-only")) { /* measured: no effect */ }
        else if (!strcmp(argv[i], "--max-seconds") && i + 1 < argc) g_deadline_s = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--bench")) bench = 1;
        else if (!strcmp(argv[i], "--scale") && i + 1 < argc) {
            if (sscanf(argv[++i], "%dx%d", &out_w, &out_h) != 2 || out_w <= 0 || out_h <= 0) {
                fprintf(stderr, "hwdec: --scale wants WxH, got %s\n", argv[i]); return 2;
            }
            /* yuv420p has a half-resolution chroma plane, so both axes must be even or the
             * chroma geometry silently disagrees with the luma and colours shear. */
            if ((out_w & 1) || (out_h & 1)) {
                fprintf(stderr, "hwdec: --scale dimensions must be even (yuv420p chroma)\n");
                return 2;
            }
        } else {
            fprintf(stderr,
                    "usage: %s [--mime video/hevc|video/avc] [--width W] [--height H]\n"
                    "          [--scale WxH] [--fps N] [--serve PORT] [--input-cmd 'ffmpeg ...']\n"
                    "          [--heartbeat FILE] [--frames N] [--out file.yuv] [--bench] [--once]\n",
                    argv[0]);
            return 2;
        }
    }
    const int hevc = strstr(mime, "hevc") != NULL;

    if (serve_port && !input_cmd) {
        fprintf(stderr, "hwdec: --serve needs --input-cmd (the decoder owns the RTSP pull so that "
                        "nothing decodes while no client is attached)\n");
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);        /* a client vanishing is normal; it must not kill us */
    signal(SIGTERM, on_sig);
    signal(SIGINT, on_sig);
    /* Armed BEFORE any decoder work so a wedge during setup is covered too -- the 4K stall that
     * produced the orphan happened after `decoder started:` and before a single frame, which a
     * deadline armed later in the run would have missed. 0 = no deadline (the serve path, which is
     * SUPPOSED to live forever and is covered by PDEATHSIG instead). */
    if (g_deadline_s > 0) {
        signal(SIGALRM, on_deadline);
        alarm((unsigned)g_deadline_s);
        fprintf(stderr, "hwdec: self-deadline armed at %ds\n", (int)g_deadline_s);
    }

    /* DIE WITH THE PARENT. This is the fix for the single most misleading failure on this fleet.
     * The fellow agent spawns this process; when the agent is killed, an ordinary child SURVIVES and
     * is reparented to init -- where it goes on holding the serve port. The agent's boot supervisor
     * then respawns the agent within 5 s, its fresh decoder cannot bind, and it logs
     * `cannot listen on 8803: Address already in use` on an escalating 20s->300s backoff. That reads
     * exactly like the supervisor being broken; it is the supervisor being correct about a port it
     * genuinely cannot have.
     * PR_SET_PDEATHSIG makes the KERNEL do the cleanup, so it holds however the parent dies --
     * including SIGKILL, where no handler of the parent's could ever run. Cleanup that depends on
     * the dying process remembering to clean up is not cleanup.
     * It is inherited-through-fork but CLEARED ON EXEC, so it must be set here in the child we
     * actually run, not once in the launcher. */
    prctl(PR_SET_PDEATHSIG, SIGTERM);
    if (getppid() == 1) {
        /* Parent already died in the window between fork and here -- the very race this guards. */
        fprintf(stderr, "hwdec: parent already gone at startup, exiting rather than orphaning\n");
        return 1;
    }

    int lsock = -1;
    if (serve_port) {
        lsock = listen_on(serve_port);
        if (lsock < 0) { fprintf(stderr, "hwdec: cannot listen on %d: %s\n",
                                 serve_port, strerror(errno)); return 1; }
        fprintf(stderr, "hwdec: listening on 0.0.0.0:%d (idle -- no decode until a client attaches)\n",
                serve_port);
    }

    uint8_t *buf = malloc(INBUF_CAP);
    if (!buf) { fprintf(stderr, "oom\n"); return 1; }

    int sessions = 0, last_rc = 0;

    /* ── one session = one attached client (or, without --serve, one run over stdin) ──────────── */
    do {
        int csock = -1, in_fd = 0;
        pid_t child = -1;

        if (serve_port) {
            beat(beat_path, 0, 0.0, 0, "idle");
            struct sockaddr_in ca;
            socklen_t cl = sizeof(ca);
            /* Poll the accept so a SIGTERM while idle is still honoured promptly. */
            fcntl(lsock, F_SETFL, O_NONBLOCK);
            /* KEEP BEATING WHILE IDLE. A beat written once on entering this loop and not again until a
             * client arrives goes stale within 30 s while a decoder correctly waits for a consumer,
             * and reads as DEAD to `hwdec_status()` (`assigned=True alive=True beat_fresh=False` on
             * a healthy node). A liveness signal that reports "dead" during a NORMAL state is worse
             * than none -- it is the permanently-red field the operator learns to ignore.
             * Freshness now means "this process is responsive"; the FRAME COUNTER is what
             * distinguishes idle from decoding, which is the distinction that actually matters. */
            double t_idle_beat = now_s();
            while (!g_stop) {
                csock = accept(lsock, (struct sockaddr *)&ca, &cl);
                if (csock >= 0) break;
                if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
                    fprintf(stderr, "hwdec: accept: %s\n", strerror(errno));
                    break;
                }
                if (now_s() - t_idle_beat >= 5.0) {
                    beat(beat_path, 0, 0.0, 0, "idle");
                    t_idle_beat = now_s();
                }
                usleep(100000);
            }
            if (g_stop || csock < 0) break;
            char ip[64] = "?";
            inet_ntop(AF_INET, &ca.sin_addr, ip, sizeof(ip));
            int one = 1;
            setsockopt(csock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            fprintf(stderr, "hwdec: client %s connected -- starting input\n", ip);

            child = spawn_input(input_cmd, &in_fd);
            if (child < 0) {
                fprintf(stderr, "hwdec: cannot spawn input\n");
                close(csock);
                break;
            }
        }
        sessions++;

        /* ---- collect parameter sets before configure() ----
         * MediaCodec needs csd-0 BEFORE configure(). Without it configure() SUCCEEDS and then every
         * frame fails to decode -- a failure that reads as "the hardware does not work" rather than
         * "you forgot the SPS". */
        long len = 0, csd_end = -1;
        while (len < INBUF_CAP && !g_stop) {
            ssize_t n = read(in_fd, buf + len, 65536);
            if (n <= 0) break;
            len += n;
            int scl;
            long p = find_sc(buf, len, 0, &scl);
            int seen_vcl = 0;
            while (p >= 0) {
                int scl2;
                long q = find_sc(buf, len, p + scl, &scl2);
                long nal_off = p + scl;
                if (nal_off < len) {
                    int t = nal_type(buf + nal_off, hevc);
                    if (is_vcl(t, hevc)) { seen_vcl = 1; csd_end = p; break; }
                }
                p = q; scl = scl2;
            }
            if (seen_vcl && csd_end > 0) break;
        }
        if (csd_end <= 0) {
            fprintf(stderr, "hwdec: no parameter sets found before first slice (stream too short?)\n");
            beat(beat_path, 0, 0.0, 0, "no-csd");
            if (csock >= 0) close(csock);
            reap_input(child, serve_port ? in_fd : -1);
            last_rc = 1;
            if (serve_port) { sleep(2); continue; }
            break;
        }
        fprintf(stderr, "hwdec: csd-0 = %ld bytes, mime=%s, configuring %dx%d\n",
                csd_end, mime, width, height);

        AMediaFormat *fmt = AMediaFormat_new();
        AMediaFormat_setString(fmt, AMEDIAFORMAT_KEY_MIME, mime);
        AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_WIDTH, width);
        AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_HEIGHT, height);
        /* max-input-size, and it may be the difference between a node that decodes 2K/4K and one
         * that returns `configure failed` or hands back a zero-capacity input buffer.
         * The MediaTek vcodec driver has a documented history of defaulting its maximum resolution
         * to 1080p even where the hardware supports 4K, corrected only by an explicit format set.
         * Measured on this fleet: gandalf decodes 4K, sam gives `cap 0` at 4K, merry fails
         * `configure` at 2K and 4K while decoding D1 fine -- three devices, three behaviours, same
         * binary. Sizing this explicitly costs nothing on a node that never needed it.
         * A compressed frame is far smaller than the raw picture; w*h*3/4 is generous headroom for
         * an IDR at these bitrates without asking the driver for an absurd allocation. */
        AMediaFormat_setInt32(fmt, "max-input-size", (int32_t)((int64_t)width * height * 3 / 4));
        AMediaFormat_setBuffer(fmt, "csd-0", buf, (size_t)csd_end);

        /* NAME THE DECODER. DO NOT LET THE FRAMEWORK PICK.
         *
         * `createDecoderByType("video/hevc")` returns *a* decoder for the mime with no regard for
         * RESOLUTION, and these tablets publish several decoders under one type with wildly
         * different limits (from their own /vendor/etc/media_codecs*.xml):
         *
         *      gandalf        c2.mtk.hevc.decoder          3840x2160
         *      sam, frodo     OMX.qcom.video.decoder.hevc  8192x4320   (+ an MTK one at 1920x1088)
         *      merry, pippin  OMX.qcom.video.decoder.hevc  3840x2160   (+ entries at 1920x1088)
         *
         * Type-selection handed back the 1080p-capped instance, so 4K produced `cap 0` on sam and
         * `configure failed` at 2K on merry -- which reads exactly like "these tablets are too
         * weak" and is nothing of the sort. EVERY node has a decoder good for well past 1080p.
         *
         * There is NO enumeration in the NDK: `AMediaCodecList_findDecoderForFormat` does not
         * exist in the C API and there is no NdkMediaCodecList.h (checked in the NDK sysroot after
         * assuming otherwise twice). `AMediaCodec_createCodecByName` is API 21 and is the whole
         * mechanism available -- so we carry the names and try them in order of capability.
         *
         * A candidate is accepted only if it CONFIGURES AND STARTS; that is the real capability
         * test, and it needs no per-device special-casing. The name that won is logged, because
         * "which codec am I actually running" was unanswerable for hours. */
        /* Hardware first, then SOFTWARE — because a software decoder that delivers beats a hardware
         * one that refuses. merry and pippin (a different tablet model) have a qcom decoder that
         * will not configure 2688x1520 and no MTK decoder at all, so they fell off the end of the
         * hardware list and got whatever createDecoderByType handed back: zero frames, which read
         * as "these two nodes cannot do 2K". They can — `c2.android.hevc.decoder` delivers 2K30 on
         * both (merry 81 frames, pippin 116/125 = 93%). */
        /* ---- DELIVERY GATE state ------------------------------------------------------------
         * A candidate that configures and STARTS is not a working decoder. sam's
         * `OMX.MTK.VIDEO.DECODER.HEVC` configures and starts at 4K and then emits nothing, and
         * because the loop below committed on start() it never reached `c2.android.hevc.decoder`
         * -- the software path that delivers 4K on merry and pippin, 3/3. That is the whole reason
         * frodo/sam/legolas measured "0 frames at 4K": the chain commits to the candidate that
         * LIES about starting and never falls through to the one that works. Every node
         * has a decode chip, and with the correct instruction set they can ALL do 4K.
         *
         * The lab's own rule, earned on this hardware: "started" is not "works"; only a delivered
         * frame is. This makes that mechanical -- a candidate stays PROVISIONAL until it hands
         * back a frame, and one that does not is walked past.
         *
         * THIS STATE MUST SURVIVE A SESSION RESTART (we end the session to re-init on the next
         * candidate; without memory we would pick the same liar forever) -- but the statics
         * warning about statics applies exactly here: a static that outlives its reason is a
         * reconnect-only bug that never shows up in a single test run. A decoder that cannot do 4K
         * is often perfect at 2K, so this is keyed to the GEOMETRY it was learned for and resets
         * on any change. */
        static int try_base = 0, try_base_w = 0, try_base_h = 0;
        if (width != try_base_w || height != try_base_h) {
            try_base = 0; try_base_w = width; try_base_h = height;
        }
        /* Bounded probe. Live use tolerates a one-time startup cost and ~a second of
         * lag, so this can be generous -- it is paid ONCE per decoder init, never per frame.
         * BOTH conditions are required: a decoder that has not been FED cannot be judged, or a
         * stream gap (waiting on a keyframe) would convict a healthy decoder. */
        /* 8.0, not 4.0, because the work test includes no_inbuf.
         * no_inbuf can climb to 30 in well under a second (a 2000 us dequeue timeout), so the
         * counter no longer meaningfully delays anything and the DEADLINE became the only real
         * protection against walking past a decoder that is merely slow to initialise. Doubling it
         * is the conservative direction: the only cost is a later fallback on a decoder that was
         * going to fail anyway, and it is paid ONCE per decoder init, never per frame -- which is
         * exactly the latitude a one-time startup cost allows. */
        const double PROBE_S = 8.0;
        const long PROBE_MIN_NALS = 30;

        static const char *HEVC_TRY[] = {
            "OMX.qcom.video.decoder.hevc",   /* qcom nodes: declared to 8192x4320 / 3840x2160 */
            "c2.mtk.hevc.decoder",           /* gandalf: 3840x2160, the only 4K-capable one here */
            "c2.qti.hevc.decoder",
            "OMX.MTK.VIDEO.DECODER.HEVC",    /* sam/frodo: takes 2K in practice */
            "c2.android.hevc.decoder",       /* SOFTWARE -- merry/pippin's working path at 2K */
            "OMX.google.hevc.decoder",       /* SOFTWARE, older name */
            NULL };
        static const char *AVC_TRY[] = {
            "OMX.qcom.video.decoder.avc",
            "c2.mtk.avc.decoder",
            "c2.qti.avc.decoder",
            "OMX.MTK.VIDEO.DECODER.AVC",
            "c2.android.avc.decoder",
            "OMX.google.h264.decoder",
            NULL };

        AMediaCodec *codec = NULL;
        const char *used = NULL;
        const char **try_list = hevc ? HEVC_TRY : AVC_TRY;
        /* Which candidate we committed to, so the gate below knows where to resume from. -1 means
         * "not from the named list" (the createDecoderByType last resort), where there is nothing
         * left to fall through to and the gate must therefore stay out of the way. */
        int used_index = -1;

        for (int ti = try_base; !codec && (force_codec || try_list[ti]); ti++) {
            const char *nm = force_codec ? force_codec : try_list[ti];
            AMediaCodec *c = AMediaCodec_createCodecByName(nm);
            if (!c) {
                /* SAY SO. This used to `continue` silently, which meant "which decoders does this
                 * node actually HAVE" was unanswerable from the log -- and a name present in
                 * /vendor/etc/media_codecs*.xml is NOT necessarily creatable (sam advertises a qcom
                 * decoder at 8192x4320 that does not exist on the hardware). A skipped candidate is
                 * evidence, not noise. */
                fprintf(stderr, "hwdec:   %s not present on this node -- next\n", nm);
                if (force_codec) break;
                continue;
            }
            if (AMediaCodec_configure(c, fmt, NULL /* no surface */, NULL, 0) != AMEDIA_OK) {
                fprintf(stderr, "hwdec:   %s cannot configure %dx%d -- next\n", nm, width, height);
                AMediaCodec_delete(c);
                if (force_codec) break;
                continue;
            }
            if (AMediaCodec_start(c) != AMEDIA_OK) {
                fprintf(stderr, "hwdec:   %s configured but would not start -- next\n", nm);
                AMediaCodec_delete(c);
                if (force_codec) break;
                continue;
            }
            codec = c; used = nm; used_index = ti;
        }

        if (!codec) {
            /* Last resort, and SAID OUT LOUD -- a silent fallback to a software or 1080p decoder is
             * exactly how capable hardware got written off. */
            fprintf(stderr, "hwdec: no named decoder took %dx%d; falling back to "
                            "createDecoderByType(%s) -- expect a lower ceiling\n",
                    width, height, mime);
            codec = AMediaCodec_createDecoderByType(mime);
            if (!codec) { fprintf(stderr, "hwdec: no decoder at all for %s\n", mime); return 1; }
            if (AMediaCodec_configure(codec, fmt, NULL, NULL, 0) != AMEDIA_OK) {
                fprintf(stderr, "hwdec: configure failed\n"); return 1;
            }
            if (AMediaCodec_start(codec) != AMEDIA_OK) {
                fprintf(stderr, "hwdec: start failed\n"); return 1;
            }
            used = "(by type)";
        }
        fprintf(stderr, "hwdec: decoder started: %s  %dx%d %s (no surface, ByteBuffer output)\n",
                used, width, height, mime);

        FILE *out = out_path ? fopen(out_path, "wb") : NULL;
        geom_t geom; memset(&geom, 0, sizeof(geom));
        int have_geom = 0, dw = 0, dh = 0;
        uint8_t *frame = NULL;

        long feed_off = 0;
        int eof = 0, frames = 0, sent = 0, dropped = 0, client_gone = 0, fatal = 0;
        /* NOT `static`, which is what this was. A static counter is shared by every session, so
         * the second client to connect would inherit the first one's exhausted countdown and could
         * end its session immediately -- a bug that only ever appears on reconnect, i.e. never in
         * a single test run. */
        int idle_rounds = 0;
        double t0 = now_s(), t_last = t0, t_diag = t0, t_beat = t0, next_send = 0;
        /* Per-session, deliberately NOT static: the gate's VERDICT persists across sessions (in
         * try_base) but its EVIDENCE must not -- a new session re-earns the frame. */
        int delivered = 0;
        double probe_deadline = t0 + PROBE_S;
        int64_t pts = 0;
        long fed = 0, fed_bytes = 0, no_inbuf = 0, tryagain = 0, skipped_ps = 0, oversize = 0;
        int ps_changed = 0;
        /* The csd-0 we configured this session with, kept so a mid-stream parameter set can be
         * compared against it rather than trusted or discarded. */
        uint8_t *csd_copy = malloc((size_t)csd_end);
        long csd_len = csd_end;
        if (csd_copy) memcpy(csd_copy, buf, (size_t)csd_end);

        while (!g_stop && !client_gone && !fatal) {
            /* ---- feed ---- */
            if (!eof) {
                /* DEADLOCK FIXED HERE. The first version only read more input when the buffer was
                 * FULLY consumed, but it also refused to feed a NAL until it had seen the NEXT start
                 * code to bound it. So the moment it held a partial NAL it would neither feed (no
                 * terminator) nor read (buffer not empty) -- it waited on itself. Symptom was
                 * `fed=3 frames=0 buffered=65456` frozen forever, which from outside looked exactly
                 * like the hardware refusing to decode. Now: if a complete NAL cannot be formed, READ.
                 */
                int scl_a = 0, scl_b = 0;
                long s_probe = find_sc(buf, len, feed_off, &scl_a);
                long e_probe = (s_probe >= 0) ? find_sc(buf, len, s_probe + scl_a, &scl_b) : -1;
                if (s_probe < 0 || e_probe < 0) {
                    if (len >= INBUF_CAP - 65536) {          /* make room before topping up */
                        memmove(buf, buf + feed_off, (size_t)(len - feed_off));
                        len -= feed_off; feed_off = 0;
                    }
                    /* CLAMP THE READ. Compaction only frees what has been CONSUMED; if the codec
                     * stops accepting input, feed_off stops advancing and compaction reclaims
                     * nothing, so `read(buf + len, 65536)` would run off the end of a 4 MB buffer
                     * and corrupt the heap -- silently, and three frames away from anything that
                     * mentions decoding. Never write into a buffer without asking what is left. */
                    size_t room = (size_t)(INBUF_CAP - len);
                    if (room == 0) {
                        fprintf(stderr, "hwdec: input buffer full with nothing consumed "
                                        "(decoder wedged?) -- ending session\n");
                        fatal = 1;
                    } else {
                        ssize_t n = read(in_fd, buf + len, room < 65536 ? room : 65536);
                        if (n > 0) len += n; else eof = 1;
                    }
                }
                if (feed_off < len) {
                    /* TWO SEPARATE start-code lengths. This was one variable, and the second
                     * find_sc OVERWROTE it -- so `buf[s + scl]` (the NAL header byte) was read at
                     * the wrong offset whenever the two start codes differed in length, 3 vs 4.
                     * Harmless while nothing read the header; wrong the moment anything did. */
                    int scl_s = 0, scl_e = 0;
                    long s = find_sc(buf, len, feed_off, &scl_s);
                    long e = (s >= 0) ? find_sc(buf, len, s + scl_s, &scl_e) : -1;
                    long chunk_end = (e > 0) ? e : len;
                    if (s >= 0 && (e > 0 || eof)) {
                        int nt = (s + scl_s < len) ? nal_type(buf + s + scl_s, hevc) : -1;

                        /* ⭐ THE FIX, AND THE WHOLE BUG. Re-feeding mid-stream VPS/SPS/PPS as
                         * ordinary input buffers WEDGES the MediaTek decoder: it accepts them for a
                         * few seconds, then stops returning input buffers and stops emitting
                         * frames, permanently. Measured: baseline died at 96-126
                         * frames every run; skipping these ran 896 frames at the full 15.4 fps
                         * source rate. They are redundant anyway: the same bytes were supplied as
                         * csd-0 at configure(), and this camera repeats them before every IDR.
                         *
                         * BUT DO NOT DISCARD THEM BLINDLY. This project has it documented that a
                         * camera which RECONNECTS loses its encoder settings, so the parameter sets
                         * really can change under us mid-stream. Silently ignoring a CHANGED SPS
                         * would decode the new stream with stale geometry -- garbage that still
                         * looks like video, the failure mode this file already refuses elsewhere.
                         * So: compare. Identical -> drop it, that is the every-IDR case. Different
                         * -> end the session loudly; the next one re-reads csd-0 from the stream
                         * and reconfigures, which is code that already exists and is tested. */
                        if (is_param_set(nt, hevc)) {
                            size_t psz = (size_t)(chunk_end - s);
                            if (csd_copy && mem_contains(csd_copy, (size_t)csd_len,
                                                         buf + s, psz)) {
                                skipped_ps++;
                            } else {
                                fprintf(stderr, "hwdec: parameter set type%d (%zuB) DIFFERS from "
                                                "csd-0 -- stream reconfigured, ending session to "
                                                "pick up the new geometry\n", nt, psz);
                                ps_changed = 1;
                                fatal = 1;      /* ends the session; the loop reconfigures */
                            }
                            feed_off = chunk_end;
                            goto fed_done;
                        }

                        ssize_t ib = AMediaCodec_dequeueInputBuffer(codec, 2000);
                        if (ib >= 0) {
                            size_t cap = 0;
                            uint8_t *dst = AMediaCodec_getInputBuffer(codec, (size_t)ib, &cap);
                            size_t sz = (size_t)(chunk_end - s);
                            /* PTS drift is no longer a concern now that parameter sets never reach
                             * here: every buffer fed IS a picture, so one increment per feed is one
                             * increment per frame. (Tested separately as its own arm -- giving
                             * non-picture NALs pts=0 changed nothing, so it is not shipped.) */
                            int64_t use_pts = pts;

                            if (dst && sz <= cap) {
                                memcpy(dst, buf + s, sz);
                                AMediaCodec_queueInputBuffer(codec, (size_t)ib, 0, sz, use_pts, 0);
                                pts += 33333;
                                fed++; fed_bytes += (long)sz;
                            } else {
                                /* NAME IT. This branch silently discarded a NAL; if a real slice
                                 * ever exceeds the codec's input capacity that is a fact worth
                                 * knowing, not a quiet drop. */
                                fprintf(stderr, "hwdec: NAL type%d %zuB does not fit input buffer "
                                                "(cap %zu) -- queueing empty\n", nt, sz, cap);
                                oversize++;
                                AMediaCodec_queueInputBuffer(codec, (size_t)ib, 0, 0, use_pts, 0);
                            }
                            feed_off = chunk_end;
                        } else {
                            no_inbuf++;
                            if (feed_off > INBUF_CAP / 2) {
                                memmove(buf, buf + feed_off, (size_t)(len - feed_off));
                                len -= feed_off; feed_off = 0;
                            }
                        }
                    }
                }
                fed_done: ;
            } else if (eof) {
                ssize_t ib = AMediaCodec_dequeueInputBuffer(codec, 2000);
                if (ib >= 0)
                    AMediaCodec_queueInputBuffer(codec, (size_t)ib, 0, 0, pts,
                                                 AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);
            }

            /* ---- drain ----
             * DRAIN UNTIL EMPTY, NOT ONCE PER FEED. One drain per loop iteration deadlocked the
             * MediaTek decoder after ~8 s every time: `fed`, `frames` and `buffered` froze while
             * no_inbuf and tryagain climbed 1:1 forever -- the codec owning every input buffer and
             * emitting nothing. The synchronous MediaCodec contract wants the output queue emptied
             * before you ask for more input capacity; a 1:1 feed/drain ratio starves it whenever
             * the decoder emits in bursts, which a decoder with a reference-frame DPB does. */
            int drained = 0, done = 0;
            AMediaCodecBufferInfo info;
            ssize_t ob;
            /* First call waits (5 ms) so an idle loop does not spin; subsequent calls poll with 0
             * so we empty whatever is ready without paying a timeout per buffer. */
            while (!done && !client_gone && !fatal &&
                   (ob = AMediaCodec_dequeueOutputBuffer(codec, &info, drained ? 0 : 5000))
                       != AMEDIACODEC_INFO_TRY_AGAIN_LATER) {
                drained++;
            if (ob >= 0) {
                if (info.size > 0) {
                    frames++;
                    delivered = 1;      /* the ONLY thing that promotes a candidate from provisional */
                    size_t cap = 0;
                    uint8_t *src = AMediaCodec_getOutputBuffer(codec, (size_t)ob, &cap);

                    /* Rate limit BEFORE the scale: Frigate wants 5 fps and the camera sends 15, so
                     * two of every three frames cost nothing but the decode the hardware already
                     * did. Scaling them would be pure waste. */
                    int want = 1;
                    if (target_fps > 0) {
                        double t = now_s();
                        if (next_send == 0) next_send = t;
                        if (t < next_send) { want = 0; dropped++; }
                        else next_send += 1.0 / target_fps;
                        /* If we have fallen behind (a stall, a slow link), resynchronise rather
                         * than sprinting to catch up on stale frames -- for detection, LATE frames
                         * are worth less than CURRENT ones. */
                        if (want && next_send < t - 1.0) next_send = t + 1.0 / target_fps;
                    }

                    if (want && src && (csock >= 0 || out)) {
                        if (!have_geom) {
                            /* The format-changed event normally arrives first and fills this in.
                             * If it did not, we have no honest way to interpret the bytes. */
                            fprintf(stderr, "hwdec: frame before output format -- cannot interpret\n");
                            fatal = 1;
                        } else if (emit_i420(src + info.offset, &geom, frame, dw, dh) != 0) {
                            fprintf(stderr, "hwdec: UNSUPPORTED color_format=%d -- refusing to emit "
                                            "(it would be plausible-looking garbage)\n", geom.colour);
                            fatal = 1;
                        } else {
                            size_t fsz = (size_t)dw * dh * 3 / 2;
                            if (out) fwrite(frame, 1, fsz, out);
                            if (csock >= 0 && write_all(csock, frame, fsz) != 0) {
                                fprintf(stderr, "hwdec: client gone after %d frames\n", sent);
                                client_gone = 1;
                            } else {
                                sent++;
                            }
                        }
                    }

                    if (bench && (frames % 30 == 0)) {
                        double t = now_s();
                        fprintf(stderr, "  %d frames  %.1f fps (last 30: %.1f fps)\n",
                                frames, frames / (t - t0), 30.0 / (t - t_last));
                        t_last = t;
                    }
                }
                AMediaCodec_releaseOutputBuffer(codec, (size_t)ob, false);
                /* `done`, not `break` -- these must end the SESSION, and a bare break now only
                 * escapes the drain loop, which would silently turn "stream ended" into "spin
                 * forever on a finished decoder". */
                if (info.flags & AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM) done = 1;
                if (max_frames && frames >= max_frames) done = 1;
            } else if (ob == AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
                AMediaFormat *of = AMediaCodec_getOutputFormat(codec);
                int32_t w = 0, h = 0;
                AMediaFormat_getInt32(of, AMEDIAFORMAT_KEY_WIDTH, &w);
                AMediaFormat_getInt32(of, AMEDIAFORMAT_KEY_HEIGHT, &h);
                read_geom(of, w, h, &geom);
                AMediaFormat_delete(of);

                dw = out_w ? out_w : geom.crop_w;
                dh = out_h ? out_h : geom.crop_h;
                dw &= ~1; dh &= ~1;
                free(frame);
                frame = malloc((size_t)dw * dh * 3 / 2);
                if (!frame) { fprintf(stderr, "hwdec: oom for frame\n"); fatal = 1; }
                have_geom = 1;
                fprintf(stderr, "hwdec: output %dx%d color_format=%d stride=%d slice_h=%d "
                                "crop=%d,%d+%dx%d -> emitting %dx%d yuv420p\n",
                        w, h, geom.colour, geom.stride, geom.slice_h,
                        geom.crop_x, geom.crop_y, geom.crop_w, geom.crop_h, dw, dh);
                if (geom.colour != CF_I420 && geom.colour != CF_NV12) {
                    fprintf(stderr, "hwdec: WARNING color_format %d is neither I420(19) nor "
                                    "NV12(21); emitting would produce garbage\n", geom.colour);
                }
            }
            }   /* end drain loop -- exited on TRY_AGAIN_LATER, i.e. output queue empty */

            if (done) break;
            /* The drain loop always ends on TRY_AGAIN, so count it here rather than inside:
             * `tryagain` now means "times the output queue ran dry", which is the useful reading. */
            tryagain++;
            /* Input exhausted and the codec has stopped producing: the stream is over. Bounded so a
             * decoder that simply has nothing more to say cannot hold the session open forever. */
            if (eof && feed_off >= len && frames > 0) {
                if (++idle_rounds > 200) break;
            } else {
                idle_rounds = 0;
            }

            /* ---- DELIVERY GATE ----
             * Walk past a candidate that started and then said nothing. Conditions, all required:
             *   !delivered        -- it has never handed back a frame
             *   used_index >= 0   -- we came from the NAMED list (not the last-resort decoder)
             *   try_list[used_index + 1] -- there is somewhere to fall through TO. With nothing
             *                        left, ending the session would be a spin, not a fix: better
             *                        to run on and let the caller see an honest zero.
             *   (fed + oversize + no_inbuf) >= PROBE_MIN_NALS -- it has been OFFERED enough work
             *   past the deadline -- and the clock only matters once the other three hold.
             *
             * ATTEMPTS, NOT SUCCESSES: NOT `fed` ALONE.
             * `fed` only increments when a NAL was successfully copied into an
             * input buffer. There are THREE ways to fail to feed and `fed` counts none of
             * them: the cap-0 branch increments `oversize`, and a decoder that never hands
             * back an input buffer at all increments `no_inbuf`. A wedged decoder produces
             * exactly one of those, so testing `fed` made the gate structurally blind in the very
             * condition it exists to catch (a 4K decoy hangs for the full 240 s).
             *
             * AND ALL THREE ARE REQUIRED. A live decoy's stderr shows the chain committing to
             * OMX.MTK.VIDEO.DECODER.HEVC at 3840x2160 and then going silent -- with NO cap-0 lines.
             * So the stall is not demonstrably the oversize branch, and a gate on one branch could
             * still not fire. "Any attempt to move data" is the honest measure of offered work;
             * naming a specific branch is a guess about a failure that is not isolated.
             *
             * The keyframe-wait protection this condition was written for SURVIVES INTACT: during
             * a genuine stream gap there is nothing to feed, so NONE of the three counters move
             * and silence still cannot convict anything. And no_inbuf climbing while the decoder
             * is legitimately busy cannot convict either, because that decoder has DELIVERED, and
             * `delivered` vetoes the gate outright. */
            if (!delivered && !fatal && used_index >= 0 && try_list[used_index + 1] &&
                (fed + oversize + no_inbuf) >= PROBE_MIN_NALS && now_s() > probe_deadline) {
                /* Report ALL THREE counters. "fed 0 / oversize 30 / no_inbuf 0" and
                 * "fed 0 / oversize 0 / no_inbuf 30" are different decoders failing in different
                 * ways, and a single number cannot tell them apart -- which is exactly how the
                 * first version of this gate was written against a branch that never fired. */
                fprintf(stderr, "hwdec: %s configured and started but delivered 0 frames in %.1fs "
                                "(fed=%ld oversize=%ld no_inbuf=%ld) -- walking past it to %s\n",
                        used, PROBE_S, fed, oversize, no_inbuf, try_list[used_index + 1]);
                try_base = used_index + 1;   /* the next session starts at the NEXT candidate */
                fatal = 1;                   /* ends the session; the loop reconfigures */
            }

            double t = now_s();
            if (t - t_diag >= 3.0) {
                /* NAME THE STUCK NAL. "dequeueInputBuffer never succeeds" has several causes and
                 * they are indistinguishable from the counters alone -- but they predict DIFFERENT
                 * pending NALs. A parameter set or an IDR slice waiting here says the decoder wants
                 * a whole ACCESS UNIT rather than the one-NAL-per-buffer we feed it; an ordinary
                 * trailing slice says it is a plain DPB stall. One fprintf separates them, which is
                 * cheaper than either patch. */
                int pn_type = -1;
                long pn_size = -1;
                {
                    int scl_d;
                    long sd = find_sc(buf, len, feed_off, &scl_d);
                    if (sd >= 0 && sd + scl_d < len) {
                        int scl_e;
                        long ed = find_sc(buf, len, sd + scl_d, &scl_e);
                        pn_type = nal_type(buf + sd + scl_d, hevc);
                        pn_size = (ed > 0 ? ed : len) - sd;
                    }
                }
                fprintf(stderr, "  [diag] fed=%ld (%.1f MB) frames=%d sent=%d dropped=%d"
                                " no_inbuf=%ld tryagain=%ld eof=%d buffered=%ld"
                                " pending_nal=type%d/%ldB skipped_ps=%ld oversize=%ld\n",
                        fed, fed_bytes / 1e6, frames, sent, dropped, no_inbuf, tryagain, eof,
                        len - feed_off, pn_type, pn_size, skipped_ps, oversize);
                t_diag = t;
            }
            if (t - t_beat >= 5.0) {
                beat(beat_path, frames, frames / (t - t0), csock >= 0 ? 1 : 0,
                     eof ? "input-eof" : "decoding");
                t_beat = t;
            }
        }

        double el = now_s() - t0;
        fprintf(stderr, "hwdec: session %d DONE %d frames (%d sent, %d dropped) in %.2fs = %.1f fps\n",
                sessions, frames, sent, dropped, el, el > 0 ? frames / el : 0.0);
        beat(beat_path, frames, el > 0 ? frames / el : 0.0, 0, fatal ? "fatal" : "session-end");

        if (out) fclose(out);
        free(frame);
        free(csd_copy);
        AMediaCodec_stop(codec);
        AMediaCodec_delete(codec);
        AMediaFormat_delete(fmt);
        if (csock >= 0) close(csock);
        reap_input(child, serve_port ? in_fd : -1);
        last_rc = (frames > 0) ? 0 : 1;

        /* A fatal misinterpretation must not spin: it will recur identically on reconnect, and a
         * tight respawn loop turns one bug into a log flood plus a hot tablet.
         * EXCEPT a changed parameter set, which is the one "fatal" that is EXPECTED to succeed on
         * retry -- the next session re-reads csd-0 from the stream and configures to the new
         * geometry. Lumping it in with the unrecoverable cases would turn a routine camera
         * reconnect into a dead decoder that only a human restart could fix. */
        if (fatal && !ps_changed) break;
    } while (serve_port && !once && !g_stop);

    free(buf);
    if (lsock >= 0) close(lsock);
    beat(beat_path, 0, 0.0, 0, "stopped");
    return last_rc;
}
