"""Hard Bench - tool division task pool (candidate set for calibration), with gold calls and a wrong call each.

Harder than the Phase 5 eight: eight tools with near-neighbours (read_log vs search_log, restart vs stop vs
scale), arguments that must be DERIVED (words to numbers, "double it", negations, distractor numbers in the
request), a three-argument tool, and no-fit traps that sound like operations but have no tool. Scored by the
tool payload's own score_tool (tool name + every gold argument, strings case-insensitive).

TOOLS and the GBNF tool-name rule are generated from one list so the prompt and the grammar cannot disagree.
`selftest(score_tool)` proves every gold call scores full marks and every WRONG call does not - the
instrument's positive and negative control before any tablet time is spent.
"""
import json

TOOLS = [
    ("read_log", "read_log(service, lines): read the last N lines of a service's log"),
    ("search_log", "search_log(service, pattern): return the log lines of a service that contain a pattern"),
    ("restart_service", "restart_service(service): restart a system service"),
    ("stop_service", "stop_service(service): stop a system service and leave it stopped"),
    ("scale_service", "scale_service(service, replicas): set the number of running replicas of a service"),
    ("query_db", "query_db(table, id): look up a record by id in a table"),
    ("update_record", "update_record(table, id, status): set the status field of a record"),
    ("send_alert", "send_alert(channel, severity): send an alert to a chat channel (severity: info, warning or critical)"),
]
TOOL_NAMES = [t for t, _ in TOOLS] + ["none"]

PREAMBLE = (
    "You are a tool-using agent. You have exactly these tools:\n"
    + "".join("- %s\n" % d for _, d in TOOLS)
    + 'If no tool fits the request, use the tool "none" with empty arguments.\n\n'
    "Request: {req}\n\n"
    'Respond with ONLY a JSON object of the form {{"tool": "<name>", "arguments": {{...}}}} and nothing else.'
)

# Runtime text of the grammar rule (each alternative is  "\"name\""  - a GBNF string holding a quoted name).
# The generator writes the payload's GBNF with repr(), so no backslash is ever hand-escaped in source.
GBNF_TOOLNAME = "toolname ::= " + " | ".join('"\\"%s\\""' % t for t in TOOL_NAMES)


def _t(req, tool, args):
    return {"req": req, "gold_tool": tool, "gold_args": args}


TASKS = {
    "readlog_words": _t("Show me the last two hundred lines of the postgres log.", "read_log", {"service": "postgres", "lines": 200}),
    "readlog_dozen": _t("I need the final dozen lines from the nginx log.", "read_log", {"service": "nginx", "lines": 12}),
    "readlog_negation": _t("Don't restart the auth service - just show me the last 20 lines of its log.", "read_log", {"service": "auth", "lines": 20}),
    "readlog_first_step": _t("The payments service keeps failing. Before anything else, read its last 30 log lines.", "read_log", {"service": "payments", "lines": 30}),
    "search_timeout": _t("Find every line containing 'timeout' in the api-gateway log.", "search_log", {"service": "api-gateway", "pattern": "timeout"}),
    "search_oom": _t("Grep the redis log for OOM.", "search_log", {"service": "redis", "pattern": "OOM"}),
    "stop_worker": _t("Shut down the worker service and do not bring it back up.", "stop_service", {"service": "worker"}),
    "stop_mailer": _t("Kill the mailer service and leave it stopped.", "stop_service", {"service": "mailer"}),
    "restart_bounce": _t("The cache service is wedged - bounce it.", "restart_service", {"service": "cache"}),
    "scale_five": _t("Run five replicas of the frontend service.", "scale_service", {"service": "frontend", "replicas": 5}),
    "scale_double": _t("The search service has 3 replicas right now; double that.", "scale_service", {"service": "search", "replicas": 6}),
    "scale_zero": _t("Scale the batch service down to zero replicas.", "scale_service", {"service": "batch", "replicas": 0}),
    "query_customer": _t("Customer number 1003 filed a complaint; pull their record from the customers table.", "query_db", {"table": "customers", "id": 1003}),
    "query_word_id": _t("Look up order forty-two in the orders table.", "query_db", {"table": "orders", "id": 42}),
    "query_distractor": _t("Server 7 is throwing errors, but first look up user 314 in the users table.", "query_db", {"table": "users", "id": 314}),
    "update_paid": _t("Mark invoice 88 in the invoices table as paid.", "update_record", {"table": "invoices", "id": 88, "status": "paid"}),
    "update_resolved": _t("Ticket 517 is resolved now - update it in the tickets table.", "update_record", {"table": "tickets", "id": 517, "status": "resolved"}),
    "alert_critical": _t("Send a critical-severity alert to the ops channel.", "send_alert", {"channel": "ops", "severity": "critical"}),
    "alert_warning": _t("Disk usage on the database host just hit 85%; warn the db-team channel at warning severity.", "send_alert", {"channel": "db-team", "severity": "warning"}),
    "nofit_email": _t("Email the quarterly report to the finance team.", "none", None),
    "nofit_translate": _t("Translate 'good morning' into Spanish.", "none", None),
    "nofit_deploy": _t("Deploy version 2.3 of the billing service.", "none", None),
    "nofit_disk": _t("How much free disk space is left on the server?", "none", None),
    "nofit_reboot_host": _t("Reboot the whole physical server.", "none", None),
}

# A plausible wrong call per task: the near-neighbour tool, or the right tool with the distractor argument.
WRONG = {
    "readlog_words": ("read_log", {"service": "postgres", "lines": 2}),
    "readlog_dozen": ("read_log", {"service": "nginx", "lines": 10}),
    "readlog_negation": ("restart_service", {"service": "auth"}),
    "readlog_first_step": ("restart_service", {"service": "payments"}),
    "search_timeout": ("read_log", {"service": "api-gateway", "lines": 100}),
    "search_oom": ("search_log", {"service": "redis", "pattern": "error"}),
    "stop_worker": ("restart_service", {"service": "worker"}),
    "stop_mailer": ("scale_service", {"service": "mailer", "replicas": 0}),
    "restart_bounce": ("stop_service", {"service": "cache"}),
    "scale_five": ("scale_service", {"service": "frontend", "replicas": 1}),
    "scale_double": ("scale_service", {"service": "search", "replicas": 3}),
    "scale_zero": ("stop_service", {"service": "batch"}),
    "query_customer": ("query_db", {"table": "complaints", "id": 1003}),
    "query_word_id": ("query_db", {"table": "orders", "id": 4}),
    "query_distractor": ("query_db", {"table": "users", "id": 7}),
    "update_paid": ("query_db", {"table": "invoices", "id": 88}),
    "update_resolved": ("update_record", {"table": "tickets", "id": 517, "status": "open"}),
    "alert_critical": ("send_alert", {"channel": "ops", "severity": "warning"}),
    "alert_warning": ("send_alert", {"channel": "db-team", "severity": "critical"}),
    "nofit_email": ("send_alert", {"channel": "finance", "severity": "info"}),
    "nofit_translate": ("query_db", {"table": "translations", "id": 1}),
    "nofit_deploy": ("restart_service", {"service": "billing"}),
    "nofit_disk": ("read_log", {"service": "server", "lines": 10}),
    "nofit_reboot_host": ("restart_service", {"service": "server"}),
}

CONTROL_REQ = "Restart the redis service."   # the payload's control_tool, which must stay valid in this tool set


def selftest(score_tool):
    assert set(WRONG) == set(TASKS), "WRONG must cover every task exactly"
    bad = []
    for name, t in TASKS.items():
        assert t["gold_tool"] in TOOL_NAMES, (name, t["gold_tool"])
        gold = json.dumps({"tool": t["gold_tool"], "arguments": t["gold_args"] or {}})
        p, tot, err = score_tool(gold, t["gold_tool"], t["gold_args"])
        if p != tot:
            bad.append((name, "gold scores %d/%d (%s)" % (p, tot, err)))
        wt, wa = WRONG[name]
        assert wt in TOOL_NAMES, (name, wt)
        p, tot, err = score_tool(json.dumps({"tool": wt, "arguments": wa}), t["gold_tool"], t["gold_args"])
        if p == tot:
            bad.append((name, "WRONG call scores full marks"))
    print("tool pool controls: %d/%d tasks clean (gold full marks, wrong call refused)" % (
        len(TASKS) - len({b[0] for b in bad}), len(TASKS)))
    for b in bad:
        print("  FAIL", b)
    assert not bad
