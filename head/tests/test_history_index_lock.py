"""A user's chat index is read, changed and saved by many writers (a turn's save, a new chat, a rename, a branch...).
Each must hold history._WRITE_LOCK for the whole read-change-save, or a writer saving an older copy drops another's
change: a chat created while a job's turn was being saved went missing from the index, and the machine gate then
refused it as not existing."""
import pathlib
import tempfile
import threading
import unittest

import history


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = history.HISTORY_DIR
        history.HISTORY_DIR = pathlib.Path(self.tmp)

    def tearDown(self):
        history.HISTORY_DIR = self._orig


class EveryIndexSaveHoldsTheLock(_Base):
    def test_each_writer_saves_the_index_only_while_holding_the_lock(self):
        real_save, saves = history._save_index, []

        def checked(user, index):
            saves.append(history._WRITE_LOCK._is_owned())
            return real_save(user, index)

        history._save_index = checked
        try:
            u = "lockuser"
            history.append(u, "first question", "first answer")
            c = history.new_chat(u)["id"]
            history.append(u, "q", "a", chat=c)
            history.rename_chat(u, c, "renamed")
            history.set_title_if_unchanged(u, c, "titled", only_if_current="renamed")
            history.switch_chat(u, c)
            history.archive_chat(u, c, True)
            history.archive_chat(u, c, False)
            history.branch_chat(u, c, 1)
            history.delete_chat(u, c)
        finally:
            history._save_index = real_save
        self.assertGreaterEqual(len(saves), 8, "the writers saved the index fewer times than called - check the test")
        self.assertTrue(all(saves), "an index save ran without holding _WRITE_LOCK: %r" % saves)

    def test_a_new_chat_waits_for_a_save_in_progress_and_is_kept(self):
        u = "lockuser2"
        history.append(u, "q", "a")
        made = {}
        t = threading.Thread(target=lambda: made.setdefault("id", history.new_chat(u)["id"]))
        with history._WRITE_LOCK:                   # a turn's save in progress
            t.start()
            t.join(0.3)
            self.assertTrue(t.is_alive(), "new_chat changed the index while another writer held it")
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertIn(made["id"], [c["id"] for c in history.list_chats(u)["chats"]])


if __name__ == "__main__":
    unittest.main()
