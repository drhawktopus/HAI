"""Resolve Windows hostname via RDP server certificate (fallback when SMB/445 is unavailable).

Reads the Subject CN from the self-signed certificate presented during the TLS handshake
initiated by an X.224 Connection Request with RDP Negotiation Request.

Usage:
    python rdpname.py <ip>
    python rdpname.py --control  # verify connectivity using known good host

The control hosts come from the fleet table (FLEET_HOSTS); with no fleet there is no control,
and that is stated rather than assumed."""
import re
import socket
import ssl
import sys

import lab_settings as S

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Hard-coded X.224 Connection Request + RDP_NEG_REQ
# requesting SSL|HYBRID protocols.
# TPKT(len=19) + X.224 LI(14) + CR(dst/sref=0, class=0)
# + NegotiationRequest(type=1, flags=0, len=8, proto=SSL|HYBRID)
X224_CR = bytes.fromhex("030000130ee000000000000100080003000000")


def cn_from_der(der):
    """Pull the Subject CN without a full ASN.1 parser.

    Windows RDP certs put the hostname in as a BMPString (UTF-16BE). We locate the CN OID
    (2.5.4.3 -> 55 04 03) and decode the string that follows it. Falls back to scanning for a
    plausible UTF-16BE hostname run, which is what these certs contain and little else.
    """
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        cn = x509.load_der_x509_certificate(der).subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if cn:
            return cn[0].value
    except Exception:
        pass
    at = der.find(bytes.fromhex("550403"))
    if at >= 0:
        tag = der[at + 3]
        ln = der[at + 4]
        raw = der[at + 5:at + 5 + ln]
        if tag == 0x1E:                       # BMPString / UTF-16BE
            return raw.decode("utf-16-be", "replace")
        if tag in (0x0C, 0x13, 0x16):         # UTF8String / PrintableString / IA5String
            return raw.decode("utf-8", "replace")
    runs = re.findall(rb"(?:\x00[\x20-\x7e]){4,}", der)
    if runs:
        return max(runs, key=len).decode("utf-16-be", "replace")
    return None


def rdpname(ip, port=3389, timeout=8):
    try:
        sock = socket.create_connection((ip, port), timeout=timeout)
    except OSError as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    try:
        sock.settimeout(timeout)
        sock.sendall(X224_CR)
        resp = sock.recv(1024)
        if not resp:
            return None, "server closed after the X.224 connection request"
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_ciphers("ALL:@SECLEVEL=0")
        except ssl.SSLError:
            pass
        with ctx.wrap_socket(sock, server_hostname=ip) as tls:
            der = tls.getpeercert(binary_form=True)
        if not der:
            return None, "TLS completed but no certificate was presented"
        cn = cn_from_der(der)
        return ({"RDP certificate CN": cn, "cert bytes": len(der)} if cn
                else None), (None if cn else "certificate carried no readable CN")
    except Exception as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    finally:
        try:
            sock.close()
        except Exception:
            pass


def report(ip, label=""):
    names, err = rdpname(ip)
    print(f"  {ip}{'  (' + label + ')' if label else ''}")
    if names:
        for k, v in names.items():
            print(f"      {k:<22} {v}")
        return True
    print(f"      -- no name: {err}")
    return False


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fleet = S.fleet_hosts()
    if "--control" in sys.argv or not args:
        if not fleet:
            print("no fleet configured (FLEET_HOSTS empty): no control host to validate against.")
            sys.exit(2)
        print("POSITIVE CONTROL (known Windows host with RDP):")
        ok = sum(report(ip, lbl) for lbl, ip in fleet.items())
        print(f"\n  control: {ok}/{len(fleet)} answered with a name.")
        if not ok:
            print("  ⚠ INSTRUMENT IS BLIND -- a negative below would mean nothing.")
        print()
    if args:
        print("TARGETS:")
        for ip in args:
            report(ip)
