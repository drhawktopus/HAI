"""The next generation of a tablet payload: a copy with exactly the named changes, never an edit of the payload itself.

A payload file is never rewritten (task_versions): results are graded by the generation that ran them and its sha256
stays pinned in the order manifests. A change to a payload therefore makes a new file, <name>_v2.py (or _v<n+1> after
_v<n>), that task_versions.PAYLOAD_GENERATIONS maps the old one to; new orders run it (newest_payload), old rows keep
theirs. The changes this makes, each derived from the lab's own records:

  - a task whose id task_versions.SUPERSEDES replaces takes the new id, in place (order kept), and its addresses take
    the ones the existing version of that task uses, read from the generation pair that first versioned it;
  - the default of FELLOW_STAGE_URL becomes empty: the factory hands the payload its stage server (TABLET_STAGE_URL);
  - a code string naming a sibling payload the payload loads (exactly "<name>.py", a file in the payload folder) takes
    that payload's next generation, so a new generation never loads an old one.

Comments and docstrings are cleaned afterwards by the faithful edit, which checks that code is unchanged. Verify with
--check: the new file loads, its tasks equal its parent's with only those renames and addresses, and its code equals
its parent's apart from docstrings and those changes.

    python payload_generation.py --out <dir> <payload.py>...     writes each new generation into <dir>
    python payload_generation.py --check <parent.py> <new.py>    verifies one pair, exit 1 on any difference
"""
import ast
import importlib.util
import json
import os
import re
import sys

import menu_paths as MP
import task_versions as TV

IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")     # the same address pattern as test_task_versions
STAGE_DEFAULT = re.compile(r'(os\.environ\.get\("FELLOW_STAGE_URL",\s*)"[^"]+"(\))')
TASK_TABLES = ("TASKS", "VLM_TASKS")


def next_name(name):
    m = re.fullmatch(r"(.*)_v(\d+)\.py", name)
    return "%s_v%d.py" % (m.group(1), int(m.group(2)) + 1) if m else name[:-3] + "_v2.py"


def successor(name):
    """The generation that follows a payload file: the recorded one, else the name the next one gets."""
    return TV.PAYLOAD_GENERATIONS.get(name) or next_name(name)


def _docstring_nodes(tree):
    out = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body and \
                isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
            out.add(id(body[0].value))
    return out


def _load(path, tag):
    spec = importlib.util.spec_from_file_location(tag, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def address_map():
    """{old task id: (new id, {old address: new address})}, read from the generation pairs that versioned each task."""
    out = {}
    old_of = {new: old for new, old in TV.SUPERSEDES.items()}
    root = MP.need("PAYLOADS")
    for old_f, new_f in TV.PAYLOAD_GENERATIONS.items():
        a = _load(os.path.join(root, old_f), "pg_a").TASKS
        b = _load(os.path.join(root, new_f), "pg_b").TASKS
        for new_id, old_id in old_of.items():
            if old_id in a and new_id in b:
                ta, tb = json.dumps(a[old_id], sort_keys=True), json.dumps(b[new_id], sort_keys=True)
                if IPV4.sub("<a>", ta) != IPV4.sub("<a>", tb):
                    raise SystemExit("%s -> %s changed more than an address in %s" % (old_id, new_id, new_f))
                subst = {x: y for x, y in zip(IPV4.findall(ta), IPV4.findall(tb)) if x != y}
                out.setdefault(old_id, (new_id, {}))[1].update(subst)
    return out


def _table_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets) \
                and isinstance(node.value, ast.Dict):
            return node.value
    return None


def _offset(lines, lineno, col):
    """A character offset into the source from an ast position (whose column counts UTF-8 bytes)."""
    line = lines[lineno - 1]
    return sum(len(x) for x in lines[:lineno - 1]) + len(line.encode("utf-8")[:col].decode("utf-8"))


def _read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def transform(src, amap):
    """The parent's source with the named changes; returns (new source, list of what changed)."""
    changes = []
    lines = [x + "\n" for x in src.split("\n")]     # ast numbers lines by "\n" ("\r\n" keeps its "\r" in the line)
    edits = []
    tree = ast.parse(src)
    for table in TASK_TABLES:
        node = _table_node(tree, table)
        if node is None:
            continue
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value in amap:
                new_id, subst = amap[k.value]
                vs, ve = _offset(lines, v.lineno, v.col_offset), _offset(lines, v.end_lineno, v.end_col_offset)
                body = src[vs:ve]
                for x, y in subst.items():
                    if x not in body:
                        raise SystemExit("%s: address %s not in its text" % (k.value, x))
                    body = body.replace(x, y)
                ks, ke = _offset(lines, k.lineno, k.col_offset), _offset(lines, k.end_lineno, k.end_col_offset)
                edits.append((vs, ve, body))
                edits.append((ks, ke, repr(new_id)))
                changes.append("%s.%s -> %s" % (table, k.value, new_id))
    root = MP.need("PAYLOADS")
    docs = _docstring_nodes(tree)
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs \
                and re.fullmatch(r"[A-Za-z0-9_]+\.py", n.value) and os.path.isfile(os.path.join(root, n.value)):
            s, e = _offset(lines, n.lineno, n.col_offset), _offset(lines, n.end_lineno, n.end_col_offset)
            literal = src[s:e]
            if literal.count(n.value) != 1:
                raise SystemExit("cannot place the sibling name %s in %r" % (n.value, literal))
            edits.append((s, e, literal.replace(n.value, successor(n.value))))
            changes.append("loads %s -> %s" % (n.value, successor(n.value)))
    for s, e, text in sorted(edits, reverse=True):
        src = src[:s] + text + src[e:]
    src, n = STAGE_DEFAULT.subn(r'\1""\2', src)
    if n:
        changes.append("FELLOW_STAGE_URL default emptied (%d)" % n)
    return src, changes


def code_only(src):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body and \
                isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and \
                isinstance(body[0].value.value, str):
            body[0].value.value = ""
    return ast.dump(tree)


def check(parent, new, amap=None):
    """Problems with new as the next generation of parent (empty list = it is exactly that)."""
    amap = address_map() if amap is None else amap
    psrc, nsrc = _read(parent), _read(new)
    want_src, _ = transform(psrc, amap)
    problems = []
    if code_only(want_src) != code_only(nsrc):
        problems.append("code differs from the parent's beyond the named changes")
    pm, nm = _load(parent, "pg_p"), _load(new, "pg_n")
    for table in TASK_TABLES:
        pt, nt = getattr(pm, table, None), getattr(nm, table, None)
        if pt is None and nt is None:
            continue
        want = {}
        for tid, t in pt.items():
            if tid in amap:
                new_id, subst = amap[tid]
                text = json.dumps(t)
                for x, y in subst.items():
                    text = text.replace(x, y)
                want[new_id] = json.loads(text)
            else:
                want[tid] = t
        if list(want) != list(nt) or want != nt:
            problems.append("%s differ from the parent's beyond the named versions" % table)
    if STAGE_DEFAULT.search(nsrc):
        problems.append("FELLOW_STAGE_URL still has a default")
    return problems


def main(argv):
    if argv[:1] == ["--check"] and len(argv) == 3:
        problems = check(argv[1], argv[2])
        print("\n".join(problems) if problems else "OK: %s is %s with the named changes only"
              % (os.path.basename(argv[2]), os.path.basename(argv[1])))
        return 1 if problems else 0
    if argv[:1] != ["--out"] or len(argv) < 3:
        print(__doc__)
        return 2
    out, names = argv[1], argv[2:]
    os.makedirs(out, exist_ok=True)
    amap = address_map()
    for p in names:
        name = os.path.basename(p)
        if name in TV.PAYLOAD_GENERATIONS:
            raise SystemExit("%s already has a next generation (%s): version that one"
                             % (name, TV.PAYLOAD_GENERATIONS[name]))
        new_name = next_name(name)
        if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(p)), new_name)):
            raise SystemExit("%s exists beside %s: refusing to overwrite a payload" % (new_name, name))
        src, changes = transform(_read(p), amap)
        with open(os.path.join(out, new_name), "w", encoding="utf-8", newline="") as f:
            f.write(src)
        print("%-44s -> %-48s %s" % (name, new_name, "; ".join(changes) or "no code change"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
