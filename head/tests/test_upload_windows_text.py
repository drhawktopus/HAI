"""Windows text files without a BOM are indexed as text, while binary files are not."""
import os
import unittest

import ingest


class WindowsTextIsIndexed(unittest.TestCase):
    def body(self, name, data):
        return ingest.extract_markdown(name, data, "source")[1]

    def test_an_excel_ansi_csv_keeps_its_accents(self):
        md = self.body("people.csv", "name,city\nJosé,México\n".encode("cp1252"))
        self.assertIn("José,México", md)

    def test_a_note_with_a_curly_apostrophe_and_a_dash(self):
        md = self.body("note.txt", "It’s a note – with a dash.\n".encode("cp1252"))
        self.assertIn("It’s a note – with a dash.", md)

    def test_utf16_without_a_mark_both_byte_orders(self):
        for enc in ("utf-16-le", "utf-16-be"):
            md = self.body("query.sql", "SELECT name FROM people WHERE id = 1;\n".encode(enc))
            self.assertIn("SELECT name FROM people WHERE id = 1;", md, enc)

    def test_utf8_is_still_read_as_utf8_not_as_windows_1252(self):
        md = self.body("people.csv", "name\nJosé\n".encode("utf-8"))
        self.assertIn("José", md)
        self.assertNotIn("JosÃ", md)

    def test_binary_is_still_stored_only(self):
        for data in (bytes(range(256)) * 8, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + os.urandom(64) + b"\x00\x01\x02",
                     b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 40):
            with self.assertRaises(ingest.StoredFileOnly):
                ingest.extract_markdown("blob.bin", data, "source")


if __name__ == "__main__":
    unittest.main()
