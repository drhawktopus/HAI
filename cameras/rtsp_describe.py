"""Verify the RTSP streams for real: authenticate, DESCRIBE, read the SDP.

The Encode config table CLAIMS main=1280x720@10 on 4K-capable cameras. A config
table is a claim; the SDP the server actually returns is the measurement. Also
decodes the H.264 SPS from sprop-parameter-sets, which is the encoder's own word
on resolution and cannot disagree with the stream.

Read-only: DESCRIBE + TEARDOWN, never PLAY.
"""
import base64
import hashlib
import re
import socket

from nvr_settings import HOST, login
PORT = 554

USER, PW = login()


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def describe(url):
    s = socket.create_connection((HOST, PORT), timeout=6)
    s.settimeout(6)
    req = "DESCRIBE %s RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n" % url
    s.sendall(req.encode())
    chal = s.recv(4000).decode("utf-8", "replace")
    m = re.search(r'realm="([^"]+)".*?nonce="([^"]+)"', chal, re.S)
    if not m:
        s.close()
        return chal
    realm, nonce = m.group(1), m.group(2)
    resp = md5("%s:%s:%s" % (md5("%s:%s:%s" % (USER, realm, PW)), nonce,
                             md5("DESCRIBE:%s" % url)))
    auth = ('Digest username="%s", realm="%s", nonce="%s", uri="%s", response="%s"'
            % (USER, realm, nonce, url, resp))
    s.sendall(("DESCRIBE %s RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n"
               "Authorization: %s\r\n\r\n" % (url, auth)).encode())
    out = s.recv(8000).decode("utf-8", "replace")
    s.sendall(("TEARDOWN %s RTSP/1.0\r\nCSeq: 3\r\nAuthorization: %s\r\n\r\n"
               % (url, auth)).encode())
    s.close()
    return out


def sps_dims(sdp):
    """Decode width/height from the H.264 SPS in sprop-parameter-sets."""
    m = re.search(r"sprop-parameter-sets=([^,;\r\n]+)", sdp)
    if not m:
        return None
    try:
        sps = base64.b64decode(m.group(1) + "==")
    except Exception:
        return None
    # minimal exp-golomb SPS parse
    bits = "".join(format(b, "08b") for b in sps[1:])
    i = [0]

    def u(n):
        v = int(bits[i[0]:i[0] + n], 2)
        i[0] += n
        return v

    def ue():
        z = 0
        while bits[i[0]] == "0":
            z += 1
            i[0] += 1
        i[0] += 1
        return (1 << z) - 1 + (int(bits[i[0]:i[0] + z], 2) if z else 0) + (0 if z else 0) \
            if not z else (1 << z) - 1 + int(bits[i[0]:i[0] + z], 2) + 0 * (i.__setitem__(0, i[0] + z) or 0)

    def ue2():
        z = 0
        while bits[i[0]] == "0":
            z += 1
            i[0] += 1
        i[0] += 1
        val = (1 << z) - 1
        if z:
            val += int(bits[i[0]:i[0] + z], 2)
            i[0] += z
        return val

    def se():
        k = ue2()
        return (k + 1) // 2 if k % 2 else -(k // 2)

    try:
        prof = u(8); u(8); u(8)          # profile, constraints, level
        ue2()                             # sps id
        if prof in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
            cf = ue2()
            if cf == 3:
                u(1)
            ue2(); ue2(); u(1)
            if u(1):                      # seq_scaling_matrix_present
                return None               # bail rather than guess
        ue2()                             # log2_max_frame_num
        poc = ue2()
        if poc == 0:
            ue2()
        elif poc == 1:
            u(1); se(); se()
            for _ in range(ue2()):
                se()
        ue2(); u(1)                       # max_num_ref_frames, gaps_allowed
        w_mbs = ue2() + 1
        h_mus = ue2() + 1
        frame_mbs_only = u(1)
        if not frame_mbs_only:
            u(1)
        u(1)                              # direct_8x8
        cl = u(1)
        co = [0, 0, 0, 0]
        if cl:
            co = [ue2(), ue2(), ue2(), ue2()]
        width = w_mbs * 16 - co[0] * 2 - co[1] * 2
        height = (2 - frame_mbs_only) * h_mus * 16 - co[2] * 2 - co[3] * 2
        return width, height
    except Exception:
        return None


for ch in (1, 2):
    for sub, name in ((0, "MAIN"), (1, "SUB")):
        url = ("rtsp://%s:%d/cam/realmonitor?channel=%d&subtype=%d" % (HOST, PORT, ch, sub))
        sdp = describe(url)
        status = sdp.split("\r\n")[0]
        print("== ch%d %s -> %s" % (ch, name, status))
        if "200 OK" in status:
            for line in sdp.splitlines():
                if re.match(r"^(a=rtpmap|a=x-dimensions|a=control:trackID|m=)", line):
                    print("     %s" % line.strip())
            d = sps_dims(sdp)
            print("     SPS-decoded resolution: %s" % (("%dx%d" % d) if d else "unparsed"))
        print()
