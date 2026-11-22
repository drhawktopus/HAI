"""Run the head's test suite anywhere: on a stranger's machine, in the lab's gate, or on a laptop with no lab at all.

    python tests/run_suite.py            (from the head's folder; exit 0 only when every test passed AND the count is
                                          the pinned one in tests/gate_pin.py)

Before any test module is imported it:
- points every store the head writes at a fresh temp folder, and every notification channel at nothing;
- gives the suite its own example users (the names in tests/fixtures/example_users.json), never the household's;
- refuses any network connection that is not to this machine, any name lookup but localhost, and any ssh or scp,
  so a test that would reach a real service fails loudly instead of reaching it.
A test that needs a service starts a fake one on loopback, or patches the call."""
import os
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HEAD = os.path.dirname(HERE)
ROOT = tempfile.mkdtemp(prefix="hai-tests-")


def _p(name):
    return os.path.join(ROOT, name)


SETTINGS = {
    # the example users (the same shape as a real head's settings, example names only)
    "PROFILES": "Alex,Avery,Jordan,Riley,Morgan",
    "HA_CONTROL_USERS": "Alex,Avery",
    "IMG_UNLIMITED_USERS": "Jordan",
    "USER_NAMESPACE": ("Riley:nursing,Morgan:nursing_morgan,Jordan:jordan,test-homelab:homelab,"
                       "test-nursing:nursing_shared,test-law:law,ChatGPTSupervisor:homelab"),
    "USER_DEFAULT_PERSONA": "Riley:nursing,Morgan:nursing,Officer Parker:law",
    "POWER_USERS": "ClaudeSupervisor,ChatGPTSupervisor,Jordan",
    "ADMIN_USERS": "Alex,ClaudeSupervisor",
    "OWNER_USER": "Alex",
    "NURSING_PERSONA_USER": "Riley",
    "LAW_PERSONA_USER": "Officer Rowan Parker",
    "LAW_STATE": "Illinois", "LAW_STATE_SOURCES": "the Illinois Compiled Statutes (ILCS) at ilga.gov; court opinions at illinoiscourts.gov",
    "HA_AREAS": "living_room, kitchen, bedroom, office, gamer_zone, laundry_room, dining_room, avery_s_room, infrastructure",
    "PC2_ALIASES": "honker,avery,primary",      # the brain device (the roster fixture's second machine)
    # the lab's model choices, as test data: the code names no model, and the tests are written against these
    "BRAIN_MODEL": "qwen3.5:9b", "HEAVY_MODEL": "qwen3.8:27b", "FAILOVER_MODEL": "qwen3:8b", "PC1_BRAIN_MODEL": "qwen3:14b",
    "MCCOY_MODEL": "tev1:latest", "VLM_MODEL": "qwen3-vl:8b", "EMBED_MODEL": "nomic-embed-text",
    "OVERNIGHT_PLANNER_MODEL": "qwen3.6:35b-a3b", "OVERNIGHT_PLAN_CRITIC_MODEL": "qwen3-coder:30b",
    "OVERNIGHT_DRIVER_LADDER": "qwen3-coder:30b,qwen3.6:35b-a3b", "ADVERSARY_CRITIC_MODELS": "qwen3.6:35b-a3b,qwen3-coder:30b",
    "NURSING_CRITIQUE_MODEL": "medgemma:27b", "MEDICAL_REVIEW_MODEL": "ii-medical:8b-q4km",
    "TRIAGE_REWRITE_MODEL_SCOPE": '{"qwen3.6:27b":null,"qwen3.5:9b":["doctrine","manual"]}',
    "USERCORPORA_REGISTRY": os.path.join(HERE, "fixtures", "example_users.json"),
    # the devices the scheduler places on (a lab's roster, so the placement tests weigh real tables)
    "HAI_ROSTER_FILE": os.path.join(HERE, "fixtures", "lab_roster.json"),
    # every store the head writes, under the temp root
    "HAI_STATE_DIR": _p("state"), "HAI_NOTES_DIR": _p("notes"), "HAI_FLEET_RUN_STATE": _p("fleet-run-state.json"),
    "HAI_STATED_RUN": _p("fleet-run-state.json"), "REMEDY_STATE_DIR": _p("remedies"),
    "ESCALATION_STATE_DIR": _p("escalations"), "ABSURD_STATE_DIR": _p("absurd"),
    "CONTRIB_STATE_DIR": _p("contributions"), "USER_PLANS_DIR": _p("user-plans"),
    "HAI_FLEET_WATCH_FILE": _p("fleet-watches.json"), "HAI_RESCUE_STATE": _p("rescue-state.json"),
    "AUDIT_FILE": _p("audit.jsonl"), "SELFENG_PROPOSALS": _p("proposals.yaml"),
    "SELFENG_COMPLETED": _p("completed.json"), "SELFENG_PLANS_DIR": _p("plans"),
    "EVAL_CAMPAIGN_ROOT": _p("eval-campaigns"), "TABFOUNDRY_SHARE": _p("tabjobs"),
    "HAI_CHAT_WINDOWS_DIR": _p("chat-windows"),
    # nothing to notify, nothing to probe, no box to reach
    "CONTRIB_NOTIFY_WEBHOOK": "", "HAWKADMIN_DM_TOKEN": "", "OVERNIGHT_DM_TOKEN": "", "OVERNIGHT_NOTIFY_WEBHOOK": "",
    "ABSURD_NOTIFY_WEBHOOK": "", "EMBED_ENABLED": "0", "HAI_THINK_CAPS_PROBE": "0", "HAI_CHAT_WINDOWS": "",
    "MCCOY_ACTIVE_USERS": "", "PC1_SSH_HOST": "tests-no-ssh.invalid", "PC1_SSH_KEY": os.devnull,
    "PC1_SWAP": "http://127.0.0.1:9",           # the lead's machine in the roster fixture is PC1
    # the lead machine's own settings, as test data (a lab's gate would otherwise lend the suite its live ones, and a
    # stranger's run would have none): its factory and dataset folders, its wake address, its ssh login
    "PC1_FOUNDRY_ROOT": "F:\\foundry", "PC1_DATASET_ROOT": "D:\\Datasets", "PC1_MAC": "00-00-00-00-00-01",
    "PC1_SSH_USER": "lab",
    # the zone the suite's fixtures were written in (the lab's wall clock is a setting; UTC when unset)
    "HAI_TZ": "America/Chicago",
    # the tests that check the isolation itself read this, so they run under this runner wherever it runs
    "HAI_TEST_ISOLATED": "1",
}

# The lab tools' story detector, when the checkout has it beside the head (tools/ in the published tree, Lab/bin in the
# lab's): the story tests run against it, and skip with their reason when it is not there.
for _cand in (os.path.join(HEAD, "..", "tools"), os.path.join(HEAD, "..", "Lab", "bin")):
    if os.path.exists(os.path.join(_cand, "code_narrative_scan.py")):
        SETTINGS["CODE_LAB_BIN_TEST"] = os.path.normpath(_cand)
        break

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _loopback(host):
    host = str(host or "").strip("[]").lower()
    return (host in _LOOPBACK_NAMES or host.startswith("127.") or host in ("::1", "0:0:0:0:0:0:0:1")
            or host.endswith(".localhost"))


def _guard_network():
    real_connect, real_connect_ex, real_getaddrinfo = (socket.socket.connect, socket.socket.connect_ex,
                                                       socket.getaddrinfo)

    def _check(sock, address):
        if sock.family == getattr(socket, "AF_UNIX", None):
            return
        host = address[0] if isinstance(address, tuple) else address
        if not _loopback(host):
            raise ConnectionRefusedError("the test suite reaches no network (tried %r)" % (address,))

    def connect(self, address):
        _check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(self, address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *a, **k):
        if host is not None and not _loopback(host):
            try:
                socket.inet_pton(socket.AF_INET6 if ":" in str(host) else socket.AF_INET, str(host))
            except (OSError, ValueError):
                raise socket.gaierror(socket.EAI_NONAME, "the test suite resolves no names (%r)" % (host,))
        return real_getaddrinfo(host, *a, **k)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, getaddrinfo

    real_popen_init = subprocess.Popen.__init__

    def popen_init(self, args, *a, **k):
        argv = args if isinstance(args, (list, tuple)) else str(args).split()
        if argv and os.path.basename(str(argv[0])).lower() in ("ssh", "ssh.exe", "scp", "scp.exe"):
            raise PermissionError("the test suite reaches no other machine (%s)" % argv[0])
        return real_popen_init(self, args, *a, **k)

    subprocess.Popen.__init__ = popen_init


def main():
    os.environ.update(SETTINGS)
    for d in ("state", "notes", "remedies", "escalations", "absurd", "contributions", "user-plans", "plans",
              "eval-campaigns", "tabjobs", "chat-windows"):
        os.makedirs(_p(d), exist_ok=True)
    _guard_network()
    for p in (HEAD, HERE):
        if p not in sys.path:
            sys.path.insert(0, p)
    import gate_pin
    suite = unittest.defaultTestLoader.discover(HERE, top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    skipped = len(result.skipped)
    reasons = {}
    for _test, why in result.skipped:
        reasons[why] = reasons.get(why, 0) + 1
    for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print("skipped %3d: %s" % (n, why[:160]))
    # the pin is the lab tree's; a head without an optional test file runs exactly that file's tests fewer
    absent = {f: n for f, n in getattr(gate_pin, "OPTIONAL_TESTS", {}).items() if not os.path.exists(os.path.join(HERE, f))}
    want = gate_pin.EXPECT_TESTS - sum(absent.values())
    # a skip says "could not measure here" and names why; the skip count is pinned only in the full lab tree, where
    # every measurement is possible (elsewhere each skip is listed above with its reason)
    skips_ok = skipped == gate_pin.EXPECT_SKIPS if not absent else True
    count_ok = result.testsRun == want and skips_ok
    print("[COUNT: %s - %d collected (pinned %d, less %d in %d optional file(s) this head does not carry), %d skipped%s]"
          % ("ok" if count_ok else "MISMATCH", result.testsRun, gate_pin.EXPECT_TESTS, sum(absent.values()),
             len(absent), skipped, " (pinned %d)" % gate_pin.EXPECT_SKIPS if not absent else ""))
    return 0 if result.wasSuccessful() and count_ok else 1


if __name__ == "__main__":
    sys.exit(main())
