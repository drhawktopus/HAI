"""Chat input UX: the message textarea auto-grows with multi-line input (min/max height + JS auto-grow),
instead of staying a fixed 46px box. Verified statically (embedded CHAT_HTML)."""
import unittest
from pathlib import Path

APP = Path("app.py").read_text(encoding="utf-8")


class ChatInputAutoGrowTests(unittest.TestCase):
    def test_textarea_autogrows(self):
        self.assertIn("min-height:46px; max-height:220px", APP)              # CSS: can grow, capped
        self.assertNotIn("font-size:15px; height:46px; }", APP)              # old fixed height is gone
        self.assertIn("function autoGrowInput(", APP)                       # JS grows to scrollHeight
        self.assertIn("t.addEventListener('input', autoGrowInput)", APP)     # ...on every keystroke


class PhoneLayoutTests(unittest.TestCase):

    def test_the_open_chat_list_can_be_closed_on_a_phone(self):
        import app
        h = app.CHAT_HTML
        self.assertIn('id="sideClose"', h)
        self.assertIn('id="sideBackdrop"', h)
        self.assertIn("body:not(.side-hidden) #sideBackdrop", h)                 # dims + catches the tap
        self.assertIn("document.getElementById('sideBackdrop').onclick", h)
        self.assertGreaterEqual(h.count("closeSideIfNarrow();"), 3, "picking, starting or searching to a chat closes it")

    def test_a_bubble_never_grows_past_the_conversation(self):
        import app
        h = app.CHAT_HTML
        self.assertIn(".msg { max-width:min(760px, 100%); box-sizing:border-box; min-width:0;", h)
        self.assertIn(".bot table { display:block; max-width:100%; overflow-x:auto; }", h)

    def test_the_name_picker_fits_a_phone(self):
        """The 37 closeout's last leftover: "Who's chatting?" ran off the right edge at 375 px - flex centring overflows
        both sides; margin:auto centres only what fits."""
        import app
        h = app.CHAT_HTML
        self.assertIn("#gate > div { margin:auto; width:100%; max-width:560px; min-width:0; }", h)
        self.assertNotIn("align-items:center; justify-content:center; padding:24px; text-align:center;", h)
        self.assertIn("min-width:min(120px, 100%); max-width:100%;", h)


if __name__ == "__main__":
    unittest.main()
