import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hai_stall_recorder as R


def slot(task, decoded, processing=True, prompt=212, done=212):
    return {"id": 0, "is_processing": processing, "id_task": task, "n_prompt_tokens": prompt,
            "n_prompt_tokens_processed": done, "next_token": [{"n_decoded": decoded}]}


class StallRule(unittest.TestCase):
    def test_frozen_counters_fire_once_at_the_line(self):
        w = R.Watch(stall_s=60)
        self.assertEqual(w.update(1, [slot(7, 236)], 0), [])
        self.assertEqual(w.update(1, [slot(7, 236)], 59), [], "not before the line")
        fired = w.update(1, [slot(7, 236)], 60)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][1], 60)
        self.assertEqual(w.update(1, [slot(7, 236)], 600), [], "one capture per task, never a repeat")

    def test_moving_counters_never_fire(self):
        w = R.Watch(stall_s=60)
        for t in range(0, 600, 2):
            self.assertEqual(w.update(1, [slot(7, t)], t), [])

    def test_prompt_progress_counts_as_progress(self):
        w = R.Watch(stall_s=60)
        for t in range(0, 600, 30):
            self.assertEqual(w.update(1, [slot(7, 0, prompt=120000, done=t * 500)], t), [])

    def test_an_idle_slot_never_counts(self):
        w = R.Watch(stall_s=60)
        for t in range(0, 600, 30):
            self.assertEqual(w.update(1, [slot(7, 236, processing=False)], t), [])

    def test_a_new_task_rearms(self):
        w = R.Watch(stall_s=60)
        w.update(1, [slot(7, 236)], 0)
        self.assertEqual(len(w.update(1, [slot(7, 236)], 60)), 1)
        w.update(1, [slot(8, 10)], 70)
        self.assertEqual(len(w.update(1, [slot(8, 10)], 130)), 1)


if __name__ == "__main__":
    unittest.main()
