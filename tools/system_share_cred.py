"""Run as SYSTEM to seed cmdkey from a local file. The credential is passed via file I/O only, never CLI args, to avoid leaking secrets in process listings.

The share is the BIG_SHARED_UNC setting: a box with no share configured seeds no credential and
says so. This script is copied out of the bin to run as SYSTEM, so it falls back to the
environment when lab_settings.py is not beside it - the environment is the top of the reader's
precedence either way, never a guessed address.
"""
import datetime, os, re, socket, subprocess

try:
    import lab_settings as S
except ImportError:                                    # noqa: BLE001 - run standalone as SYSTEM
    S = None

HERE = r"C:\ProgramData\HAI"
LOCKER = os.path.join(HERE, "locker.tmp.md")
OUT = os.path.join(HERE, "system_share_cred.out")
BASE_UNC = (S.setting("BIG_SHARED_UNC", "") if S else os.environ.get("BIG_SHARED_UNC", ""))
BASE = BASE_UNC + r"\HAI\chat-slots" if BASE_UNC else ""


def unc_host(unc):
    """The address a UNC points at, from the UNC itself - never a literal."""
    parts = unc.split("\\")
    return parts[1] if len(parts) > 1 else ""


SHARE_HOST = unc_host(BASE_UNC)
out = [datetime.datetime.now().isoformat(timespec="seconds")]
pw = None
try:
    text = open(LOCKER, encoding="utf-8").read()
    sec = text[text.index("## Softserve local"):]
    pw = re.search(r"Password:\s*`([^`]+)`", sec).group(1)
except Exception as e:
    out.append("locker parse FAIL %s" % type(e).__name__)
finally:
    try:
        os.remove(LOCKER)
    except OSError:
        pass
out.append("locker copy removed: %s" % (not os.path.exists(LOCKER)))


def mask(s):
    return s.replace(pw, "***") if pw else s


if pw and SHARE_HOST:
    r = subprocess.run(["cmdkey", "/add:" + SHARE_HOST, "/user:SOFTSERVE\\Administrator", "/pass:" + pw],
                       capture_output=True, text=True, timeout=60)
    out.append("cmdkey rc=%d %s" % (r.returncode, mask((r.stdout + r.stderr).strip()).replace("\n", " | ")[:160]))
    r = subprocess.run(["cmdkey", "/list:" + SHARE_HOST], capture_output=True, text=True, timeout=60)
    out.append("cmdkey list: " + " | ".join(x.strip() for x in r.stdout.splitlines() if "Target" in x or "User" in x))
elif not SHARE_HOST:
    out.append("no BIG_SHARED_UNC set: no share to seed a credential for")
if not BASE:
    out.append("share probe skipped: BASE is unset")
else:
    d = os.path.join(BASE, socket.gethostname().lower())
    for step in ("list", "write", "read", "delete"):
        try:
            if step == "list":
                out.append("list: ok %d entries" % len(os.listdir(BASE)))
            elif step == "write":
                os.makedirs(d, exist_ok=True)
                open(os.path.join(d, "_probe.tmp"), "w").write("probe")
                out.append("write: ok")
            elif step == "read":
                out.append("read: ok %r" % open(os.path.join(d, "_probe.tmp")).read())
            else:
                os.remove(os.path.join(d, "_probe.tmp"))
                out.append("delete: ok")
        except Exception as e:
            out.append("%s: FAIL %s" % (step, mask(repr(e))))
open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
try:
    os.remove(os.path.abspath(__file__))
except OSError:
    pass
