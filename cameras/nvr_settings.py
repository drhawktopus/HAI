"""The cameras lane's NVR: its address and its login, from the lab's settings (see cam_settings).

    from nvr_settings import HOST, CRED, login, rtsp
    HOST          the NVR's address (NVR_HOST)
    CRED          the camera credentials document (CAMERA_CRED_DOC): markdown tables with **Username** and
                  **Password** rows, the NVR's first
    login()       (user, password): NVR_USER / NVR_PASSWORD when both are set, else the document
    rtsp()        the prefix of every stream URL: rtsp:// then the login (percent-encoded), then HOST:554
"""
import os
import re
import sys
import urllib.parse

from cam_settings import S, need

HOST = need("NVR_HOST", "the NVR's address")
CRED = S.setting("CAMERA_CRED_DOC", "")


def login():
    user, pw = os.environ.get("NVR_USER", ""), os.environ.get("NVR_PASSWORD", "")
    if user and pw:
        return user, pw
    if not CRED:
        sys.exit("set NVR_USER and NVR_PASSWORD, or CAMERA_CRED_DOC (the camera credentials document)")
    with open(CRED, encoding="utf-8") as f:
        t = f.read()
    return (re.search(r"\*\*Username\*\*\s*\|\s*`([^`]+)`", t).group(1),
            re.search(r"\*\*Password\*\*\s*\|\s*`([^`]+)`", t).group(1))


def rtsp():
    user, pw = login()
    return "rtsp://%s:%s@%s:554" % (urllib.parse.quote(user, safe=""), urllib.parse.quote(pw, safe=""), HOST)
