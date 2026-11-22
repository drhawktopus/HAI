"""The rigby A1 payload gate's collection pin (read by fleet_deploy_tolkien.rigby_a1_gate, `fleet_deploy --rigby-a1`).
Every test_*.py in this folder runs; change these in the same edit that adds or removes tests. A test module that
cannot import prints the same OK as one that passes, so the gate fails on any other count."""
# 52 = test_a1_payload 11, test_a1_task 3, test_lab_provider 9, test_rigby_validity 4, test_map_brief 12,
# test_a1_fellowship 13.
# 53 (+1 test_rigby_validity: the payload's early_stop IS a1_stop.decide).
# 54 (+1 test_rigby_validity: cap hits at rigby's tested max are the model's own, scored).
# 55 (+1 test_a1_task RigbyResumesFromItsCheckpoint: per-turn checkpoint + ReplayChat resume, fleet parity).
# 57 (+2 test_a1_payload: the runaway guard - turn_cap reaches llama-server, a cut there nudges and the row stays valid; without a cap a length cut stays a cap hit).
# 58 (+1 test_a1_payload: a conversation that outgrows the context ends `context_full`, graded - scored at rigby's tested max, void below it; it used to end as an engine error, void and re-run).
# 61 (+3 net test_a1_payload: CONTEXT COMPACTION - Inspect's summary compaction past the threshold keeps the system prompt + brief and carries on; nothing below it; an overflow forces a compaction and retries; context_full only when even that does not fit).
# 65 (+3 test_lab_provider: effort levels verbatim to the foundry and refused by tolkien, the plan's own options with the call config laid over, the tool schema byte-equal to the harness converter; +1 test_a1_payload: a turn_cap that leaves no room is refused before any call) - the GPU A1 calls moved onto this provider.
# 70 (+5 test_lab_provider.OpenAIBackend: the openai backend for Strata - stream parse, loop cut, thinking
# switches, Strata's over-context refusal as the compaction signal, host required).
EXPECT_TESTS = 70
EXPECT_SKIPS = 0
