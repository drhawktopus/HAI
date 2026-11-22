"""W1 ops, letter-shuffled. The 8 W1 ops tasks put the correct cause
at B in all 8 and the correct fix at B in 7, so a model leaning on "B" scored without reading. Same incidents, same
option texts; only the letters move (each text keeps its own letter's slot), under NEW task ids `<id>_sh` so no
row can be confused with an unshuffled one. Built from hard_tasks_w1 by parsing each prompt back into its options;
the parse is checked by rebuilding the original prompt byte for byte."""
import hard_tasks_w1 as W

LETTERS = "ABCDE"
# correct (cause, fix) letter per task - explicit, spread across A-E, no task keeps its original pair
TARGET = {
    "ops_cifs_sqlite": ("D", "A"),
    "ops_macvlan_host_v2": ("A", "E"),
    "ops_parallel_spill": ("E", "C"),
    "ops_maxpath": ("C", "D"),
    "ops_python3_stub": ("B", "E"),
    "ops_stale_image": ("D", "A"),
    "ops_pydantic_null": ("E", "B"),
    "ops_schtasks_once": ("A", "C"),
}


def _parse(prompt):
    """(question, causes, fixes) from a W._mc prompt; asserts W._mc rebuilds it exactly."""
    head, rest = prompt.split("\n\nPossible root causes:\n", 1)
    cause_block, rest = rest.split("\n\nPossible fixes:\n", 1)
    fix_block = rest.split("\n\nPick the single best", 1)[0]

    def opts(block):
        out = []
        for line in block.split("\n"):
            assert line[1:3] == ") " and line[0] in LETTERS, line
            out.append((line[0], line[3:]))
        assert [k for k, _ in out] == list(LETTERS[:len(out)]), out
        return out
    causes, fixes = opts(cause_block), opts(fix_block)
    assert W._mc(head, causes, fixes) == prompt, "option parse does not round-trip"
    return head, causes, fixes


def _move(options, right, target):
    """Relabel so the text at letter `right` sits at letter `target`; the others keep their relative order."""
    texts = [t for _, t in options]
    keep = texts[LETTERS.index(right)]
    others = [t for i, t in enumerate(texts) if i != LETTERS.index(right)]
    others.insert(LETTERS.index(target), keep)
    return [(LETTERS[i], t) for i, t in enumerate(others)]


TASKS, REFERENCE, NEGATIVE = {}, {}, {}
for _name, (_c, _f) in TARGET.items():
    _t = W.TASKS[_name]
    _old = eval(W.REFERENCE[_name].split("=", 1)[1])        # e.g. ('B', 'C') - the original right letters
    _q, _causes, _fixes = _parse(_t["prompt"])
    _nc, _nf = _move(_causes, _old[0], _c), _move(_fixes, _old[1], _f)
    assert dict(_nc)[_c] == dict(_causes)[_old[0]] and dict(_nf)[_f] == dict(_fixes)[_old[1]]
    _id = _name + "_sh"
    TASKS[_id] = dict(_t, prompt=W._mc(_q, _nc, _nf), checks=["lambda: tuple(ANSWER) == (%r, %r)" % (_c, _f)])
    REFERENCE[_id] = "ANSWER = (%r, %r)\n" % (_c, _f)
    NEGATIVE[_id] = "ANSWER = (%r, %r)\n" % (_c, next(x for x in LETTERS if x != _f))

def selftest(score, work_dir):
    """W1's own selftest over this pool's tables: every REFERENCE passes, every NEGATIVE fails."""
    g = dict(W.selftest.__globals__, TASKS=TASKS, REFERENCE=REFERENCE, NEGATIVE=NEGATIVE)
    return type(W.selftest)(W.selftest.__code__, g)(score, work_dir)
