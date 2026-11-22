r"""Derive rigby's scheduler-profile patch from the lab's Mothership one (mothership-build\workspace\flashnext\patches\
sched_profile.py). Upstream 23b0202 split the input-copy loop in two (host weights last) and added a comment line, so
the "copy the input tensors" anchor moved; every other anchor and the helper are unchanged. Writes
sched_profile_23b0202.py beside this file. Asserts the one replacement happened exactly once."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the engine folder
import engine_settings as E  # noqa: E402
SRC = E.foundry_mirror("workspace", "flashnext", "patches", "sched_profile.py")
DST = os.path.join(HERE, "sched_profile_23b0202.py")

OLD = '''sub("""        // copy the input tensors to the split backend
        for (int input_id = 0; input_id < split->n_inputs; input_id++) {""",
    """        const int64_t lab_t1 = ggml_time_us();
        // copy the input tensors to the split backend
        for (int input_id = 0; input_id < split->n_inputs; input_id++) {""")'''

NEW = '''sub("""        // copy the input tensors to the split backend
        // the weights in host memory are copied last, so that the copy callback can read the other inputs of the split
        for (int input_id = 0; input_id < split->n_inputs; input_id++) {
            if (!ggml_backend_sched_is_host_weight(split->inputs[input_id])) {""",
    """        const int64_t lab_t1 = ggml_time_us();
        // copy the input tensors to the split backend
        // the weights in host memory are copied last, so that the copy callback can read the other inputs of the split
        for (int input_id = 0; input_id < split->n_inputs; input_id++) {
            if (!ggml_backend_sched_is_host_weight(split->inputs[input_id])) {""")'''

text = open(SRC, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in text else "\n"
old, new = OLD.replace("\n", nl), NEW.replace("\n", nl)
assert text.count(old) == 1, "the Mothership patch's copy anchor matched %d times" % text.count(old)
text = text.replace(old, new)
text = text.replace("(scheduler profile, 5 edits)", "(scheduler profile, 5 edits, upstream 23b0202 anchors)")
open(DST, "w", encoding="utf-8", newline="").write(text)
print("wrote", DST)
