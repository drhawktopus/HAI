"""Hard Bench - work family W1: harder, work-shaped tasks that separate the top models (Project 34 charter step 2).

the owner approved the shape: three deterministic parts, writing/explanation left judged and out of W1.
  code_* (8) implement / fix to a spec, graded by hidden checks (the f1/f2 contract)
  reason_* (8) one exact answer, given as `ANSWER = <value>` in the final python block, checked exactly
  ops_* (8) lab troubleshooting from the lab's own incidents: `ANSWER = ("<cause letter>", "<fix letter>")`
Every task is graded by the payload's own score() (code + "lambda: <expr>" checks in a bounded python3 child), so W1
runs wherever f1/f2 run: the tablets, rigby, and every GPU factory, through grade_jobs.py.

REFERENCE: a known-right answer per task (every check True). NEGATIVE: a plausible WRONG answer per task (at least
one check False) - the instrument's negative control; a check set that passes a wrong answer cannot tell right from
wrong. selftest() runs both through a payload's score(). An exception is checked with unittest's callable
assertRaises, which returns None when the error is raised (one expression, no try needed)."""

FINAL = ("Use exactly one fenced code block in your whole answer: a ```python block at the very end containing the "
         "complete %s. Do not put any other ``` fence anywhere in the answer (the first fence is the one graded).")
ANSWER_BLOCK = FINAL % "assignment `ANSWER = ...` - nothing else is graded"
CODE_BLOCK = FINAL % "code (imports included)"
RAISES = "__import__('unittest').TestCase().assertRaises"
DT = "__import__('datetime').datetime"


def _mc(question, causes, fixes):
    lines = ([question, "", "Possible root causes:"] + ["%s) %s" % kv for kv in causes] +
             ["", "Possible fixes:"] + ["%s) %s" % kv for kv in fixes] +
             ["", "Pick the single best root cause and the single best fix. " + ANSWER_BLOCK +
              ' The value is a tuple of two letters, e.g. ANSWER = ("A", "C").'])
    return "\n".join(lines)


def _clocked(cls_call, body):
    """A check driving an object with a fake clock t[0]: `body` sees the object as `c` and the clock list as `t`."""
    return "lambda: (lambda t: (lambda c: (%s,)[-1])(%s))([0.0])" % (body, cls_call)


TASKS = {
    # ================================================================== code (implement / fix to a spec)
    "code_ttl_lru": {
        "kind": "impl",
        "prompt": (
            "Implement a Python class TTLCache(capacity, clock=time.monotonic): a thread-safe LRU cache with a "
            "per-entry TTL. clock() returns seconds; use it for every time decision. An entry put at time t with ttl "
            "s is live while clock() < t + s. Methods: get(key) returns the value or None (an expired entry counts "
            "as absent and is removed; a hit makes the key most-recently-used); put(key, value, ttl) inserts or "
            "replaces (replacing refreshes the TTL and makes the key most-recently-used; inserting a NEW key into a "
            "full cache first removes every expired entry, then, if still full, evicts the least-recently-used live "
            "entry); delete(key) returns True only if a live entry was removed; len(cache) is the number of live "
            "entries. Guard all state with a lock. " + CODE_BLOCK),
        "checks": [
            _clocked("TTLCache(2, clock=lambda: t[0])",
                     "c.put('a',1,10), c.put('b',2,10), c.get('a'), c.put('c',3,10), "
                     "c.get('b') is None and c.get('a')==1 and c.get('c')==3"),
            _clocked("TTLCache(4, clock=lambda: t[0])", "c.put('a',1,5), t.__setitem__(0,5.0), c.get('a') is None"),
            _clocked("TTLCache(4, clock=lambda: t[0])",
                     "c.put('a',1,5), c.put('b',2,50), t.__setitem__(0,10.0), len(c)==1"),
            _clocked("TTLCache(4, clock=lambda: t[0])",
                     "c.put('a',1,5), t.__setitem__(0,4.0), c.put('a',9,5), t.__setitem__(0,8.0), c.get('a')==9"),
            _clocked("TTLCache(2, clock=lambda: t[0])",
                     "c.put('a',1,1), c.put('b',2,100), t.__setitem__(0,5.0), c.put('c',3,100), "
                     "c.get('b')==2 and c.get('c')==3 and len(c)==2"),
            _clocked("TTLCache(4, clock=lambda: t[0])",
                     "c.put('a',1,5), c.put('b',2,1), t.__setitem__(0,2.0), "
                     "[c.delete('a'), c.delete('a'), c.delete('b'), c.delete('zz')]==[True, False, False, False]"),
            "lambda: (lambda th, c: (lambda ts: ([x.start() for x in ts], [x.join() for x in ts], len(c)==50)[-1])("
            "[th.Thread(target=lambda i=i: [c.put((i,j), j, 100) or c.get((i,j)) for j in range(300)]) "
            "for i in range(8)]))(__import__('threading'), TTLCache(50))",
        ],
    },
    "code_merge_bugfix": {
        "kind": "debug",
        "prompt": (
            "This function should merge overlapping OR touching integer intervals ([1,3] and [4,6] touch and merge "
            "into [1,6]), return a NEW list of [start, end] lists sorted by start, accept intervals in any order, "
            "return [] for an empty input, and must not modify its input (neither the outer list nor the inner "
            "lists). It has several bugs. Fix them; keep the name and signature.\n\n"
            "def merge_intervals(intervals):\n"
            "    intervals.sort()\n"
            "    out = [intervals[0]]\n"
            "    for s, e in intervals[1:]:\n"
            "        if s < out[-1][1]:\n"
            "            out[-1][1] = e\n"
            "        else:\n"
            "            out.append([s, e])\n"
            "    return out\n\n" + CODE_BLOCK),
        "checks": [
            "lambda: merge_intervals([]) == []",
            "lambda: merge_intervals([[1,3],[4,6]]) == [[1,6]]",
            "lambda: merge_intervals([[1,10],[2,3]]) == [[1,10]]",
            "lambda: merge_intervals([[8,9],[1,2],[5,6]]) == [[1,2],[5,6],[8,9]]",
            "lambda: (lambda x: (merge_intervals(x), x == [[5,7],[1,3],[2,4]])[-1])([[5,7],[1,3],[2,4]])",
            "lambda: (lambda x: (merge_intervals(x)[0].__setitem__(1, 99), x == [[1,3],[2,4]])[-1])([[1,3],[2,4]])",
            "lambda: merge_intervals([[1,5],[2,3],[6,8],[10,11]]) == [[1,8],[10,11]]",
        ],
    },
    "code_semver_sort": {
        "kind": "impl",
        "prompt": (
            "Write sort_versions(versions) returning a new list of SemVer 2.0.0 version strings sorted by "
            "precedence, lowest first. Rules: compare MAJOR.MINOR.PATCH numerically; a version with a pre-release "
            "(after '-') has LOWER precedence than the same version without one; pre-release identifiers are "
            "compared dot by dot: numeric identifiers numerically, alphanumeric ones in ASCII order, a numeric "
            "identifier is lower than an alphanumeric one, and a shorter set of identifiers is lower when all "
            "preceding ones are equal; build metadata (after '+') is ignored for precedence, and versions of equal "
            "precedence keep their input order. Do not modify the input list. " + CODE_BLOCK),
        "checks": [
            "lambda: sort_versions(['1.0.0','1.0.0-rc.1','1.0.0-beta.11','1.0.0-beta.2','1.0.0-beta',"
            "'1.0.0-alpha.beta','1.0.0-alpha.1','1.0.0-alpha']) == ['1.0.0-alpha','1.0.0-alpha.1','1.0.0-alpha.beta',"
            "'1.0.0-beta','1.0.0-beta.2','1.0.0-beta.11','1.0.0-rc.1','1.0.0']",
            "lambda: sort_versions(['1.10.0','1.2.0','1.9.9']) == ['1.2.0','1.9.9','1.10.0']",
            "lambda: sort_versions(['2.0.0+b','1.0.0','2.0.0+a']) == ['1.0.0','2.0.0+b','2.0.0+a']",
            "lambda: sort_versions(['1.0.0-a','1.0.0-10','1.0.0-2']) == ['1.0.0-2','1.0.0-10','1.0.0-a']",
            "lambda: (lambda v: (sort_versions(v), v == ['1.0.1','1.0.0'])[-1])(['1.0.1','1.0.0'])",
        ],
    },
    "code_cron_next": {
        "kind": "impl",
        "prompt": (
            "Write next_fire(expr, after) -> datetime.datetime: the earliest minute strictly after `after` (a naive "
            "datetime; seconds and microseconds of the result are 0) that matches the 5-field cron expression "
            "`minute hour day-of-month month day-of-week`. Each field supports '*', a number, a range 'a-b', a step "
            "'*/s' or 'a-b/s', and comma-separated lists of those. day-of-week is 0-6 with 0 = Sunday. When BOTH "
            "day-of-month and day-of-week are restricted (neither is '*'), a day matches if EITHER matches (standard "
            "cron); otherwise only the restricted one applies. " + CODE_BLOCK),
        "checks": [
            "lambda: next_fire('*/15 * * * *', %s(2026,1,1,10,7,30)) == %s(2026,1,1,10,15)" % (DT, DT),
            "lambda: next_fire('0 9 * * 1-5', %s(2026,10,2,9,0)) == %s(2026,10,5,9,0)" % (DT, DT),
            "lambda: next_fire('30 4 29 2 *', %s(2026,3,1)) == %s(2028,2,29,4,30)" % (DT, DT),
            "lambda: next_fire('0 0 13 * 5', %s(2026,10,2,12,0)) == %s(2026,10,9,0,0)" % (DT, DT),
            "lambda: next_fire('5,35 1-3/2 * * *', %s(2026,1,1,1,35)) == %s(2026,1,1,3,5)" % (DT, DT),
            "lambda: next_fire('59 23 31 12 *', %s(2026,12,31,23,59)) == %s(2027,12,31,23,59)" % (DT, DT),
        ],
    },
    "code_token_bucket": {
        "kind": "impl",
        "prompt": (
            "Implement class TokenBucket(rate, capacity, clock=time.monotonic): it starts FULL with `capacity` tokens "
            "and refills continuously at `rate` tokens per second (fractional tokens accumulate) up to `capacity`. "
            "allow(n=1) returns True and removes n tokens if at least n are available, otherwise returns False and "
            "removes nothing. allow(n) with n > capacity raises ValueError. Use clock() for all time decisions. " +
            CODE_BLOCK),
        "checks": [
            _clocked("TokenBucket(1, 3, clock=lambda: t[0])",
                     "[c.allow(), c.allow(), c.allow(), c.allow()] == [True, True, True, False]"),
            _clocked("TokenBucket(1, 3, clock=lambda: t[0])",
                     "c.allow(3), t.__setitem__(0,1.5), [c.allow(), c.allow()] == [True, False]"),
            _clocked("TokenBucket(1, 3, clock=lambda: t[0])",
                     "c.allow(3), t.__setitem__(0,0.5), c.allow() is False, t.__setitem__(0,1.0), c.allow() is True"),
            _clocked("TokenBucket(1, 3, clock=lambda: t[0])",
                     "c.allow(3), t.__setitem__(0,100.0), c.allow(3), c.allow() is False"),
            _clocked("TokenBucket(1, 3, clock=lambda: t[0])", "c.allow(2), c.allow(2) is False and c.allow(1) is True"),
            "lambda: %s(ValueError, TokenBucket(1, 3).allow, 4) is None" % RAISES,
        ],
    },
    "code_json_pointer": {
        "kind": "impl",
        "prompt": (
            "Write resolve(doc, pointer) implementing RFC 6901 JSON Pointer lookup on Python dicts/lists. '' is the "
            "whole document; otherwise the pointer must start with '/', and each reference token is unescaped "
            "('~1' -> '/', then '~0' -> '~'). On a list the token must be a non-negative integer written without "
            "leading zeros ('0' is fine, '01' is not, '-' is not allowed). Errors: a pointer not starting with '/' "
            "or a malformed array index raises ValueError; a missing object key raises KeyError; an index past the "
            "end raises IndexError. " + CODE_BLOCK),
        "checks": [
            "lambda: resolve({'a': {'b': [10, 20]}}, '/a/b/1') == 20",
            "lambda: resolve({'a/b': 1, 'm~n': 2}, '/a~1b') == 1 and resolve({'a/b': 1, 'm~n': 2}, '/m~0n') == 2",
            "lambda: resolve({'~1': 'tilde-one'}, '/~01') == 'tilde-one'",
            "lambda: resolve({'x': 1}, '') == {'x': 1} and resolve({'': 7}, '/') == 7",
            "lambda: %s(ValueError, resolve, {'a': 1}, 'a') is None" % RAISES,
            "lambda: %s(ValueError, resolve, {'a': [1, 2]}, '/a/01') is None and "
            "%s(ValueError, resolve, {'a': [1]}, '/a/-') is None" % (RAISES, RAISES),
            "lambda: %s(KeyError, resolve, {'a': 1}, '/b') is None and "
            "%s(IndexError, resolve, {'a': [1]}, '/a/1') is None" % (RAISES, RAISES),
        ],
    },
    "code_parse_duration": {
        "kind": "impl",
        "prompt": (
            "Write parse_duration(s) -> int seconds for strings like '1h30m', '2d', '45s', '1h 5m 3s'. Units are d, "
            "h, m, s; each component is a non-negative integer followed by a unit; components must appear in the "
            "order d, h, m, s, each unit at most once, optionally separated by single spaces; at least one component "
            "is required; values are not capped ('90s' is 90). Anything else raises ValueError (empty string, an "
            "unknown unit, a repeated unit, out-of-order units, a missing number, decimals). " + CODE_BLOCK),
        "checks": [
            "lambda: [parse_duration(x) for x in ['1h30m', '2d', '45s', '1h 5m 3s', '90s']] == [5400, 172800, 45, 3903, 90]",
            "lambda: parse_duration('1d1h1m1s') == 90061",
            "lambda: all(%s(ValueError, parse_duration, x) is None for x in ['', '5x', '1h1h', '30m1h'])" % RAISES,
            "lambda: all(%s(ValueError, parse_duration, x) is None for x in ['h', '1.5h', '1h  5m', ' 1h'])" % RAISES,
        ],
    },
    "code_topo_order": {
        "kind": "impl",
        "prompt": (
            "Write topo_order(tasks) where tasks maps each task name to a list of the names it depends on. Return a "
            "list of every task such that each task comes after all its dependencies; whenever several tasks are "
            "ready, take the alphabetically smallest first. Raise ValueError if a dependency names an unknown task "
            "or if there is a cycle. " + CODE_BLOCK),
        "checks": [
            "lambda: topo_order({'b': ['a'], 'a': [], 'c': ['a']}) == ['a', 'b', 'c']",
            "lambda: topo_order({'d': [], 'c': ['d'], 'b': [], 'a': ['c']}) == ['b', 'd', 'c', 'a']",
            "lambda: topo_order({}) == []",
            "lambda: %s(ValueError, topo_order, {'a': ['b'], 'b': ['a']}) is None" % RAISES,
            "lambda: %s(ValueError, topo_order, {'a': ['zz']}) is None" % RAISES,
            "lambda: topo_order({'x': [], 'y': ['x'], 'z': ['x', 'y'], 'w': []}) == ['w', 'x', 'y', 'z']",
        ],
    },
    # ================================================================== reason (one exact answer)
    "reason_diffsq": {
        "kind": "answer",
        "prompt": ("Find every pair of positive integers (x, y) with x^2 - y^2 = 2025. Show your method. " +
                   ANSWER_BLOCK + " ANSWER is a list of (x, y) tuples sorted by x."),
        "checks": ["lambda: ANSWER == [(51, 24), (53, 28), (75, 60), (117, 108), (205, 200), (339, 336), (1013, 1012)]"],
    },
    "reason_schedule": {
        "kind": "answer",
        "prompt": ("Six jobs run on 2 identical machines; a job runs on one machine without interruption. Durations: "
                   "A=3, B=3, C=2, D=4, E=2, F=3. D may start only after A and B finish; E only after C; F only after D "
                   "and E. What is the minimum possible makespan (time from the start until every job is done)? " +
                   ANSWER_BLOCK + " ANSWER is an int."),
        "checks": ["lambda: ANSWER == 10"],
    },
    "reason_kv_bytes": {
        "kind": "answer",
        "prompt": ("A transformer has 64 layers and 8 KV heads with head dimension 128. How many bytes does its KV "
                   "cache take for 32,768 tokens, storing both K and V at 2 bytes per element (fp16) and nothing "
                   "else? " + ANSWER_BLOCK + " ANSWER is an int (bytes)."),
        "checks": ["lambda: ANSWER == 8589934592"],
    },
    "reason_dice": {
        "kind": "answer",
        "prompt": ("Three fair six-sided dice are rolled. What is the probability that their sum is at least 15? " +
                   ANSWER_BLOCK + " ANSWER is a (numerator, denominator) tuple in lowest terms."),
        "checks": ["lambda: tuple(ANSWER) == (5, 54)"],
    },
    "reason_weekdays": {
        "kind": "answer",
        "prompt": ("How many weekdays (Monday to Friday) are there from 2026-03-02 to 2026-04-30 inclusive, if "
                   "2026-04-03 is a holiday and does not count? " + ANSWER_BLOCK + " ANSWER is an int."),
        "checks": ["lambda: ANSWER == 43"],
    },
    "reason_paths": {
        "kind": "answer",
        "prompt": ("On a grid, a path goes from (0,0) to (6,6) using only steps of +1 in x or +1 in y. How many such "
                   "paths avoid both points (3,3) and (2,4)? " + ANSWER_BLOCK + " ANSWER is an int."),
        "checks": ["lambda: ANSWER == 299"],
    },
    "reason_eta": {
        "kind": "answer",
        "prompt": ("A job reported 1,200 of 9,000 units done at 10:00:00 and 1,950 of 9,000 at 10:05:00. At that "
                   "constant rate, at what clock time does it finish? " + ANSWER_BLOCK +
                   " ANSWER is a string 'HH:MM:SS'."),
        "checks": ["lambda: ANSWER == '10:52:00'"],
    },
    "reason_logic": {
        "kind": "answer",
        "prompt": ("Four boxes - mothership, bighonker, softserve, rigby - each take exactly one of four jobs: "
                   "render, caption, grade, chat. (1) mothership is not grading. (2) softserve takes neither render "
                   "nor caption. (3) rigby takes grade or chat. (4) bighonker does not render. (5) chat is not on "
                   "softserve. Which box takes which job? " + ANSWER_BLOCK +
                   " ANSWER is a dict mapping each box name to its job."),
        "checks": ["lambda: ANSWER == {'mothership': 'render', 'bighonker': 'caption', 'softserve': 'grade', "
                   "'rigby': 'chat'}"],
    },
    # ================================================================== ops (the lab's own incidents)
    "ops_cifs_sqlite": {
        "kind": "mc",
        "prompt": _mc(
            "A Pi-hole container under Docker Compose keeps restarting. Its config directory is bind-mounted from a "
            "CIFS/SMB network share. The logs repeat 'database is locked' and 'disk I/O error' for gravity.db.",
            [("A", "Another process already listens on port 53"),
             ("B", "SQLite's locking (WAL / byte-range locks) does not work reliably on a CIFS/SMB mount"),
             ("C", "The container hits its memory limit and is OOM-killed"),
             ("D", "The image is corrupted and needs a re-pull"),
             ("E", "An upstream DNS loop makes Pi-hole query itself")],
            [("A", "Move Pi-hole to port 5353"), ("B", "Raise the container's memory limit"),
             ("C", "Keep the config/database on a local disk volume (back it up to the share separately)"),
             ("D", "docker compose pull and recreate"), ("E", "Disable DNSSEC")]),
        "checks": ["lambda: tuple(ANSWER) == ('B', 'C')"],
    },
    "ops_macvlan_host_v2": {
        "kind": "mc",
        "prompt": _mc(
            "A container on a Docker macvlan network has its own LAN IP, 10.0.0.21. Every other device on the "
            "LAN can reach it, but the Docker host itself cannot (pings and DNS queries from the host time out). The "
            "container must keep its own LAN IP.",
            [("A", "The container's firewall drops the host's address"),
             ("B", "By design, a macvlan parent interface cannot talk to its own macvlan children"),
             ("C", "The container has the wrong subnet mask"), ("D", "The host's ARP cache is stale"),
             ("E", "Two devices hold 10.0.0.21")],
            [("A", "Restart the Docker daemon"),
             ("B", "Add a macvlan 'shim' interface on the host and route 10.0.0.21 through it"),
             ("C", "Put the container on network_mode: host"), ("D", "Flush the host's ARP cache"),
             ("E", "Enable promiscuous mode on the switch port")]),
        "checks": ["lambda: tuple(ANSWER) == ('B', 'B')"],
    },
    "ops_parallel_spill": {
        "kind": "mc",
        "prompt": _mc(
            "On an 8 GB GPU, a 5.6 GB model served by ollama ran well. After setting OLLAMA_NUM_PARALLEL=2 to run two "
            "workers, total throughput FELL, and /api/ps now shows size_vram smaller than size.",
            [("A", "The GPU driver crashed and fell back to CPU"),
             ("B", "Each parallel slot allocates its own KV cache; the second does not fit in 8 GB, so part of the "
                   "model spills to system RAM"),
             ("C", "Thermal throttling from the extra load"), ("D", "The two workers deadlock on a file lock"),
             ("E", "ollama ignores OLLAMA_NUM_PARALLEL on consumer GPUs")],
            [("A", "Reboot the box"), ("B", "Set OLLAMA_NUM_PARALLEL=1 (or shrink the context) so everything stays "
                                             "on the GPU"),
             ("C", "Improve case cooling"), ("D", "Add a third worker to keep the GPU busy"),
             ("E", "Switch to a larger quantization")]),
        "checks": ["lambda: tuple(ANSWER) == ('B', 'B')"],
    },
    "ops_maxpath": {
        "kind": "mc",
        "prompt": _mc(
            "A Windows box (registry LongPathsEnabled = 0) finishes a job and copies its output folder to a UNC "
            "share with shutil.copytree. Jobs with long names fail with FileNotFoundError: 'The system cannot find "
            "the path specified', while short-named jobs ship fine. The share is reachable and writable.",
            [("A", "The share is full"), ("B", "The destination path exceeds Windows' 260-character MAX_PATH"),
             ("C", "The job's output folder was deleted before the copy"), ("D", "An antivirus scanner locks the files"),
             ("E", "SMB signing rejects the session")],
            [("A", "Retry the copy three times"),
             ("B", "Copy using the \\\\?\\ (\\\\?\\UNC\\) long-path form, or shorten the names"),
             ("C", "Grant the account Full Control on the share"), ("D", "Free disk space on the share"),
             ("E", "Disable SMB signing")]),
        "checks": ["lambda: tuple(ANSWER) == ('B', 'B')"],
    },
    "ops_python3_stub": {
        "kind": "mc",
        "prompt": _mc(
            "On a Windows box, a grader runs each submitted program with subprocess.run(['python3', '-c', prog]) and "
            "parses a RESULT line. Every submission scores 0 and nothing raises. `where python3` finds "
            "C:\\Users\\<user>\\AppData\\Local\\Microsoft\\WindowsApps\\python3.exe.",
            [("A", "The submissions are all wrong"), ("B", "python3 resolves to the Microsoft Store app-execution-alias "
                                                         "stub, which exits without running the program"),
             ("C", "The subprocess timeout is too short"), ("D", "Windows Defender blocks subprocess"),
             ("E", "The RESULT line is printed to stderr")],
            [("A", "Lengthen the timeout"), ("B", "Run the child with sys.executable (or a real python on PATH, or "
                                                   "disable the alias)"),
             ("C", "Whitelist python3 in Defender"), ("D", "Parse stderr too"), ("E", "Rewrite the submissions")]),
        "checks": ["lambda: tuple(ANSWER) == ('B', 'B')"],
    },
    "ops_stale_image": {
        "kind": "mc",
        "prompt": _mc(
            "A service's Python code is COPYed into its image by its Dockerfile. You edit app.py on the host and run "
            "`docker compose restart app`. The container comes back up, but it still behaves like the old code.",
            [("A", "Python cached the old bytecode in the bind mount"),
             ("B", "The code is baked into the image at build time; restart reuses the old image"),
             ("C", "The edit was saved with the wrong line endings"), ("D", "Compose restarted a different service"),
             ("E", "The healthcheck rolled the container back")],
            [("A", "Delete __pycache__ and restart"),
             ("B", "docker compose build app, then docker compose up -d app (and verify the file hash inside it)"),
             ("C", "Convert the file to LF"), ("D", "Restart the Docker daemon"), ("E", "Disable the healthcheck")]),
        "checks": ["lambda: tuple(ANSWER) == ('B', 'B')"],
    },
    "ops_pydantic_null": {
        "kind": "mc",
        "prompt": _mc(
            "A daemon POSTs its status every minute to a FastAPI endpoint whose pydantic v2 model declares "
            "`last_tflops: float = None`. The daemon always sends the key, with null when it has no reading. The "
            "daemon swallows send errors. The status board has shown the daemon as DOWN for hours although it is "
            "healthy, and a hand-made test request without that key returns 200.",
            [("A", "The board's freshness threshold is too short"),
             ("B", "Under pydantic v2 an explicit null is invalid for `float`, so every real push is rejected with 422"),
             ("C", "The daemon's clock is skewed"), ("D", "The endpoint is rate-limited"),
             ("E", "The network drops POST requests")],
            [("A", "Raise the freshness threshold"), ("B", "Declare the field Optional[float] = None"),
             ("C", "Sync the daemon's clock"), ("D", "Push less often"), ("E", "Retry each push three times")]),
        "checks": ["lambda: tuple(ANSWER) == ('B', 'B')"],
    },
    "ops_schtasks_once": {
        "kind": "mc",
        "prompt": _mc(
            "A job is launched on Windows with `schtasks /create /tn Bench /sc once /st 23:59 ...` followed "
            "immediately by `schtasks /run /tn Bench`. It runs correctly - and then starts again on its own at "
            "23:59 the same night, colliding with other work.",
            [("A", "/run queues a second instance"),
             ("B", "/sc once /st arms a real one-shot trigger at the next occurrence of that time"),
             ("C", "The task's restart-on-failure setting relaunched it"), ("D", "Another user ran it"),
             ("E", "Windows Update rebooted the box and replayed the task")],
            [("A", "Use /sc onlogon instead"), ("B", "Create it with a past start date (/sd) so the trigger never fires "
                                                  "by itself, then /run it"),
             ("C", "Set restart count to 0"), ("D", "Make the task private"), ("E", "Pause Windows Update")]),
        "checks": ["lambda: tuple(ANSWER) == ('B', 'B')"],
    },
}

_TTL = """import threading, time
from collections import OrderedDict

class TTLCache:
    def __init__(self, capacity, clock=time.monotonic):
        self.capacity, self.clock = capacity, clock
        self.d = OrderedDict()
        self.lock = threading.Lock()

    def _live(self, k):
        v = self.d.get(k)
        if v is None:
            return False
        if self.clock() >= v[1]:
            del self.d[k]
            return False
        return True

    def get(self, key):
        with self.lock:
            if not self._live(key):
                return None
            self.d.move_to_end(key)
            return self.d[key][0]

    def put(self, key, value, ttl):
        with self.lock:
            now = self.clock()
            if key in self.d:
                self.d[key] = (value, now + ttl)
                self.d.move_to_end(key)
                return
            if len(self.d) >= self.capacity:
                for k in [k for k, v in self.d.items() if now >= v[1]]:
                    del self.d[k]
            if len(self.d) >= self.capacity:
                self.d.popitem(last=False)
            self.d[key] = (value, now + ttl)

    def delete(self, key):
        with self.lock:
            if not self._live(key):
                return False
            del self.d[key]
            return True

    def __len__(self):
        with self.lock:
            now = self.clock()
            return sum(1 for v in self.d.values() if now < v[1])
"""

_SEMVER = """def sort_versions(versions):
    def key(v):
        core = v.split('+', 1)[0]
        main, _, pre = core.partition('-')
        nums = tuple(int(x) for x in main.split('.'))
        if not pre:
            return (nums, 1, ())
        ids = tuple((0, int(p), '') if p.isdigit() else (1, 0, p) for p in pre.split('.'))
        return (nums, 0, ids)
    return sorted(versions, key=key)
"""

_CRON = """import datetime

def _field(spec, lo, hi):
    out = set()
    for part in spec.split(','):
        rng, _, step = part.partition('/')
        step = int(step) if step else 1
        if rng == '*':
            a, b = lo, hi
        elif '-' in rng:
            a, b = (int(x) for x in rng.split('-'))
        else:
            a = b = int(rng)
        out.update(range(a, b + 1, step))
    return out

def next_fire(expr, after):
    m, h, dom, mon, dow = expr.split()
    M, H = _field(m, 0, 59), _field(h, 0, 23)
    D, MO, W = _field(dom, 1, 31), _field(mon, 1, 12), _field(dow, 0, 6)
    t = after.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    def day_ok(d):
        dm, dw = d.day in D, (d.isoweekday() % 7) in W
        if dom != '*' and dow != '*':
            return dm or dw
        if dom != '*':
            return dm
        if dow != '*':
            return dw
        return True
    for _ in range(366 * 8):
        if t.month in MO and day_ok(t):
            for hh in sorted(H):
                for mm in sorted(M):
                    c = t.replace(hour=hh, minute=mm)
                    if c >= t:
                        return c
        t = (t + datetime.timedelta(days=1)).replace(hour=0, minute=0)
    raise ValueError('no match')
"""

_BUCKET = """import time

class TokenBucket:
    def __init__(self, rate, capacity, clock=time.monotonic):
        self.rate, self.capacity, self.clock = rate, capacity, clock
        self.tokens, self.t = float(capacity), clock()

    def allow(self, n=1):
        if n > self.capacity:
            raise ValueError('n exceeds capacity')
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False
"""

_POINTER = """def resolve(doc, pointer):
    if pointer == '':
        return doc
    if not pointer.startswith('/'):
        raise ValueError('pointer must start with /')
    cur = doc
    for tok in pointer[1:].split('/'):
        tok = tok.replace('~1', '/').replace('~0', '~')
        if isinstance(cur, list):
            if not tok.isdigit() or (len(tok) > 1 and tok[0] == '0'):
                raise ValueError('bad array index %r' % tok)
            i = int(tok)
            if i >= len(cur):
                raise IndexError(i)
            cur = cur[i]
        else:
            if tok not in cur:
                raise KeyError(tok)
            cur = cur[tok]
    return cur
"""

_DURATION = """import re

_RX = re.compile(r'^(?:(\\d+)d)?(?: ?(\\d+)h)?(?: ?(\\d+)m)?(?: ?(\\d+)s)?$')

def parse_duration(s):
    if not s or s != s.strip():
        raise ValueError(s)
    m = _RX.match(s)
    if not m or not any(m.groups()):
        raise ValueError(s)
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se
"""

_TOPO = """import heapq

def topo_order(tasks):
    for t, deps in tasks.items():
        for d in deps:
            if d not in tasks:
                raise ValueError('unknown dependency %r' % d)
    need = {t: set(deps) for t, deps in tasks.items()}
    ready = [t for t, d in need.items() if not d]
    heapq.heapify(ready)
    out = []
    while ready:
        t = heapq.heappop(ready)
        out.append(t)
        for u, d in need.items():
            if t in d:
                d.discard(t)
                if not d:
                    heapq.heappush(ready, u)
    if len(out) != len(tasks):
        raise ValueError('cycle')
    return out
"""

_MERGE = """def merge_intervals(intervals):
    out = []
    for s, e in sorted([list(x) for x in intervals]):
        if out and s <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out
"""

REFERENCE = {
    "code_ttl_lru": _TTL,
    "code_merge_bugfix": _MERGE,
    "code_semver_sort": _SEMVER,
    "code_cron_next": _CRON,
    "code_token_bucket": _BUCKET,
    "code_json_pointer": _POINTER,
    "code_parse_duration": _DURATION,
    "code_topo_order": _TOPO,
    "reason_diffsq": "ANSWER = [(51, 24), (53, 28), (75, 60), (117, 108), (205, 200), (339, 336), (1013, 1012)]\n",
    "reason_schedule": "ANSWER = 10\n",
    "reason_kv_bytes": "ANSWER = 2 * 64 * 8 * 128 * 32768 * 2\n",
    "reason_dice": "ANSWER = (5, 54)\n",
    "reason_weekdays": "ANSWER = 43\n",
    "reason_paths": "ANSWER = 299\n",
    "reason_eta": "ANSWER = '10:52:00'\n",
    "reason_logic": "ANSWER = {'mothership': 'render', 'bighonker': 'caption', 'softserve': 'grade', 'rigby': 'chat'}\n",
    "ops_cifs_sqlite": "ANSWER = ('B', 'C')\n",
    "ops_macvlan_host_v2": "ANSWER = ('B', 'B')\n",
    "ops_parallel_spill": "ANSWER = ('B', 'B')\n",
    "ops_maxpath": "ANSWER = ('B', 'B')\n",
    "ops_python3_stub": "ANSWER = ('B', 'B')\n",
    "ops_stale_image": "ANSWER = ('B', 'B')\n",
    "ops_pydantic_null": "ANSWER = ('B', 'B')\n",
    "ops_schtasks_once": "ANSWER = ('B', 'B')\n",
}

# a plausible WRONG answer per task - each must fail at least one check
NEGATIVE = {
    "code_ttl_lru": _TTL.replace("self.d.move_to_end(key)\n            return self.d[key][0]",
                                 "return self.d[key][0]"),                         # get() forgets recency
    "code_merge_bugfix": _MERGE.replace("s <= out[-1][1] + 1", "s <= out[-1][1]"),  # touching intervals not merged
    "code_semver_sort": _SEMVER.replace("(0, int(p), '') if p.isdigit() else (1, 0, p)", "(0, 0, p)"),  # ASCII only
    "code_cron_next": _CRON.replace("return dm or dw", "return dm and dw"),        # AND instead of cron's OR
    "code_token_bucket": _BUCKET.replace("min(self.capacity, ", "(").replace("* self.rate)", "* self.rate"),  # no cap
    "code_json_pointer": _POINTER.replace("tok.replace('~1', '/').replace('~0', '~')",
                                          "tok.replace('~0', '~').replace('~1', '/')"),  # wrong unescape order
    "code_parse_duration": _DURATION.replace("if not s or s != s.strip():", "if not s:").replace(
        "(?: ?(\\\\d+)h)", "(?: *(\\\\d+)h)"),                                     # tolerates bad spacing
    "code_topo_order": _TOPO.replace("heapq.heappop(ready)", "ready.pop()"),        # not alphabetical
    "reason_diffsq": "ANSWER = [(51, 24), (53, 28), (75, 60), (117, 108), (205, 200), (339, 336), (1013, 1012), (45, 0)]\n",
    "reason_schedule": "ANSWER = 9\n",
    "reason_kv_bytes": "ANSWER = 64 * 8 * 128 * 32768 * 2\n",
    "reason_dice": "ANSWER = (10, 108)\n",
    "reason_weekdays": "ANSWER = 44\n",
    "reason_paths": "ANSWER = 924\n",
    "reason_eta": "ANSWER = '10:47:00'\n",
    "reason_logic": "ANSWER = {'mothership': 'render', 'bighonker': 'grade', 'softserve': 'caption', 'rigby': 'chat'}\n",
    "ops_cifs_sqlite": "ANSWER = ('B', 'D')\n",
    "ops_macvlan_host_v2": "ANSWER = ('B', 'C')\n",
    "ops_parallel_spill": "ANSWER = ('A', 'A')\n",
    "ops_maxpath": "ANSWER = ('B', 'A')\n",
    "ops_python3_stub": "ANSWER = ('C', 'A')\n",
    "ops_stale_image": "ANSWER = ('A', 'A')\n",
    "ops_pydantic_null": "ANSWER = ('A', 'A')\n",
    "ops_schtasks_once": "ANSWER = ('A', 'A')\n",
}


def selftest(score, work_dir):
    """`score` is a payload's own score(code, checks, work_dir). Returns [(task, reason)]; empty = the instrument is
    sound: every REFERENCE passes every check, and every NEGATIVE fails at least one."""
    bad = []
    if set(TASKS) != set(REFERENCE) or set(TASKS) != set(NEGATIVE):
        bad.append(("<all>", "TASKS / REFERENCE / NEGATIVE keys differ"))
    for name, t in TASKS.items():
        if NEGATIVE.get(name) == REFERENCE.get(name):
            bad.append((name, "NEGATIVE is identical to REFERENCE - the mutation did not apply"))
            continue
        p, n, err = score(REFERENCE[name], t["checks"], work_dir)
        if p != n:
            bad.append((name, "REFERENCE passes %d/%d (%s)" % (p, n, err)))
        p, n, err = score(NEGATIVE[name], t["checks"], work_dir)
        if p == n:
            bad.append((name, "NEGATIVE passes every check - the checks cannot tell it from right"))
    return bad
