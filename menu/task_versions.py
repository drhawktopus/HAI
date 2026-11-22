"""Versioned benchmark tasks: the one record of which task id replaces which, and which payload generation defines it.

A task whose text changes in a way that could move its results gets a NEW id; the old id keeps every result it ever
had, graded by the payload generation that ran it (that file is never rewritten, and its sha256 stays pinned in the
order manifests). So a row always says which text it answered, and no row of one version can be read as the other.

Two readers, one meaning each:
  - the MENU keeps the ids apart (build_menu: a row is graded by the generation that defines its id, and every rule
    that applies to a task applies to the ids it supersedes);
  - the FILLER treats them as one question (menu_fill: a cell measured on the old id counts as measured for the new
    one, and the old id's price is the new one's), because a versioned task asks the same question: same options,
    same answer, same checks, only the named text changed (test_task_versions proves this against the payload that
    ran the old id).

Add a version here, give the new text its new id in the task file, and generate its payload under the new name in
PAYLOAD_GENERATIONS; never edit the old payload."""

# new id -> the id it replaces
SUPERSEDES = {
    # a real LAN address in the question replaced by a neutral one (10.0.0.21); options, answer and checks unchanged
    "ops_macvlan_host_v2": "ops_macvlan_host",
    "ops_macvlan_host_v2_sh": "ops_macvlan_host_sh",     # its letter-shuffled copy (hard_tasks_w1ops)
    # a real-looking LAN address in a hidden check replaced by a neutral one (10.0.0.1); the prompt is unchanged
    "valid_ipv4_v2": "valid_ipv4",
    # an advisor request carrying material the public tree does not (the adult rule): an ordinary request with the same
    # labels (tool / image / job, no named person); the advisor set's results keep the original id
    "img_forest_v2": "img_forest",
}

# a payload file -> the next generation of it (each generation defines its own ids; none is ever rewritten)
PAYLOAD_GENERATIONS = {
    "map_swarm_code_hard.py": "map_swarm_code_hard_v2.py",
    "map_swarm_code_hard_w1.py": "map_swarm_code_hard_w1_v2.py",
    "map_swarm_code_hard_w1ops.py": "map_swarm_code_hard_w1ops_v2.py",
    # the public generation (payload_generation.py): comments cleaned, no stage-server default (the factory passes
    # it), the versioned tasks under their new ids; every other task and every line of code as its parent's
    "fellow_gauntlet.py": "fellow_gauntlet_v2.py",
    "fellow_gauntlet_v3.py": "fellow_gauntlet_v4.py",
    "map_chat_swarm.py": "map_chat_swarm_v2.py",
    "map_swarm_code.py": "map_swarm_code_v2.py",
    "map_swarm_code_hard_05b.py": "map_swarm_code_hard_05b_v2.py",
    "map_swarm_code_hard_3b.py": "map_swarm_code_hard_3b_v2.py",
    "map_swarm_code_hard_c1.py": "map_swarm_code_hard_c1_v2.py",
    "map_swarm_code_hard_c1_c05b.py": "map_swarm_code_hard_c1_c05b_v2.py",
    "map_swarm_code_hard_c1_c15b.py": "map_swarm_code_hard_c1_c15b_v2.py",
    "map_swarm_code_hard_c1_q3508bnt.py": "map_swarm_code_hard_c1_q3508bnt_v2.py",
    "map_swarm_code_hard_c1_q352bnt.py": "map_swarm_code_hard_c1_q352bnt_v2.py",
    "map_swarm_code_hard_chat1.py": "map_swarm_code_hard_chat1_v2.py",
    "map_swarm_code_hard_chat1_c05b.py": "map_swarm_code_hard_chat1_c05b_v2.py",
    "map_swarm_code_hard_chat1_c15b.py": "map_swarm_code_hard_chat1_c15b_v2.py",
    "map_swarm_code_hard_chat1_q3508bnt.py": "map_swarm_code_hard_chat1_q3508bnt_v2.py",
    "map_swarm_code_hard_chat1_q352bnt.py": "map_swarm_code_hard_chat1_q352bnt_v2.py",
    "map_swarm_code_hard_f2.py": "map_swarm_code_hard_f2_v2.py",
    "map_swarm_code_hard_f2_05b.py": "map_swarm_code_hard_f2_05b_v2.py",
    "map_swarm_code_hard_f2_q3508b.py": "map_swarm_code_hard_f2_q3508b_v2.py",
    "map_swarm_code_hard_f2_q3508bnt.py": "map_swarm_code_hard_f2_q3508bnt_v2.py",
    "map_swarm_code_hard_f2_q3508bt16k.py": "map_swarm_code_hard_f2_q3508bt16k_v2.py",
    "map_swarm_code_hard_f2_q352b.py": "map_swarm_code_hard_f2_q352b_v2.py",
    "map_swarm_code_hard_f2_q352bnt.py": "map_swarm_code_hard_f2_q352bnt_v2.py",
    "map_swarm_code_hard_f2_q352bt16k.py": "map_swarm_code_hard_f2_q352bt16k_v2.py",
    "map_swarm_code_hard_f2tests.py": "map_swarm_code_hard_f2tests_v2.py",
    "map_swarm_code_hard_q3508bnt.py": "map_swarm_code_hard_q3508bnt_v2.py",
    "map_swarm_code_hard_q352bnt.py": "map_swarm_code_hard_q352bnt_v2.py",
    "map_swarm_code_hard_t00.py": "map_swarm_code_hard_t00_v2.py",
    "map_swarm_code_hard_t03.py": "map_swarm_code_hard_t03_v2.py",
    "map_swarm_code_hard_t09.py": "map_swarm_code_hard_t09_v2.py",
    "map_swarm_code_hard_v2.py": "map_swarm_code_hard_v3.py",
    "map_swarm_code_hard_w1_c05b.py": "map_swarm_code_hard_w1_c05b_v2.py",
    "map_swarm_code_hard_w1_c15b.py": "map_swarm_code_hard_w1_c15b_v2.py",
    "map_swarm_code_hard_w1_q3508bnt.py": "map_swarm_code_hard_w1_q3508bnt_v2.py",
    "map_swarm_code_hard_w1_q352bnt.py": "map_swarm_code_hard_w1_q352bnt_v2.py",
    "map_swarm_code_hard_w1_v2.py": "map_swarm_code_hard_w1_v3.py",
    "map_swarm_code_hard_w1ops_v2.py": "map_swarm_code_hard_w1ops_v3.py",
    "map_swarm_code_hard_w1tests.py": "map_swarm_code_hard_w1tests_v2.py",
    "map_team_role.py": "map_team_role_v2.py",
    "map_team_verify.py": "map_team_verify_v2.py",
    "map_tool_swarm.py": "map_tool_swarm_v2.py",
    "map_tool_swarm_hard.py": "map_tool_swarm_hard_v2.py",
    "map_tool_swarm_hard_c05b.py": "map_tool_swarm_hard_c05b_v2.py",
    "map_tool_swarm_hard_c15b.py": "map_tool_swarm_hard_c15b_v2.py",
    "map_tool_swarm_hard_q3508bnt.py": "map_tool_swarm_hard_q3508bnt_v2.py",
    "map_tool_swarm_hard_q352bnt.py": "map_tool_swarm_hard_q352bnt_v2.py",
    "map_vlm_swarm.py": "map_vlm_swarm_v2.py",
    "map_vlm_swarm_hard.py": "map_vlm_swarm_hard_v2.py",
    "map_vlm_swarm_hard_4x.py": "map_vlm_swarm_hard_4x_v2.py",
    "probe_cli_multiline.py": "probe_cli_multiline_v2.py",
    "probe_cli_output.py": "probe_cli_output_v2.py",
    "speed_rewrite_probe.py": "speed_rewrite_probe_v2.py",
}

_REPLACED_BY = {old: new for new, old in SUPERSEDES.items()}


def current(task):
    """The id a task is asked under now: `task` itself, or the newest id that supersedes it."""
    seen = set()
    while task in _REPLACED_BY and task not in seen:
        seen.add(task)
        task = _REPLACED_BY[task]
    return task


def predecessors(task):
    """Every id `task` supersedes, nearest first."""
    out = []
    while task in SUPERSEDES and SUPERSEDES[task] not in out:
        task = SUPERSEDES[task]
        out.append(task)
    return out


def newest_payload(payload):
    """The newest generation of a payload file name (a path keeps its folder): what a NEW order runs."""
    head, sep, name = payload.replace("\\", "/").rpartition("/")
    seen = set()
    while name in PAYLOAD_GENERATIONS and name not in seen:
        seen.add(name)
        name = PAYLOAD_GENERATIONS[name]
    return (head + sep + name) if sep else name


def generations(payload):
    """A payload file name and every later generation of it, oldest first: the files that may define a row's id."""
    out = [payload]
    while out[-1] in PAYLOAD_GENERATIONS and PAYLOAD_GENERATIONS[out[-1]] not in out:
        out.append(PAYLOAD_GENERATIONS[out[-1]])
    return out
