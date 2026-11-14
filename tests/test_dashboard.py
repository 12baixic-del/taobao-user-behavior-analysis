"""Dashboard render tests (run in CI; streamlit is in requirements.txt).

These tests protect the dashboard fix itself: they render app.py with
streamlit's AppTest and assert the displayed shares, so restoring the old
division (1,510 / 6,811 = 22.2%) or the old cross-item counts would fail
here even though the generator unit tests still pass.
"""
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent


def rendered_text():
    at = AppTest.from_file(str(REPO_ROOT / "app.py"), default_timeout=180)
    at.run()
    assert len(at.exception) == 0, list(at.exception)
    md = "\n".join(m.value for m in at.markdown)
    metrics = "\n".join(m.value for m in at.metric)
    help_text = "\n".join(
        (m.help or "") for m in at.metric if hasattr(m, "help"))
    return at, md + "\n" + metrics + "\n" + help_text


class TestDashboardRenders(unittest.TestCase):
    def test_shares_use_all_buyer_denominator(self):
        """看板显示全体购买用户口径 22.1%（1,510/6,846），不是 22.2%；
        商品级核对显示 51.5%（2,747/5,336），不是旧的 50.9%/52.4%。"""
        _, text = rendered_text()
        for needle in ["68.1%", "53.3%", "83.55%", "22.1%", "51.5%",
                       "2,747", "1,510", "6,846",
                       "100,150,807", "1.02%", "99.86%"]:
            self.assertIn(needle, text, f"missing in rendered text: {needle}")
        for banned in ["22.2%", "52.4%", "50.9%", "2,797", "2,715",
                       "2,539", "2,621"]:
            self.assertNotIn(banned, text,
                             f"stale number still rendered: {banned}")

    def test_sequential_caliber_renders(self):
        """切换到时序口径后正常渲染，无异常，且显示 22.1%。"""
        at, _ = rendered_text()
        at.radio[0].set_value("时序口径（pv → 意向 → 购买）").run()
        self.assertEqual(len(at.exception), 0, list(at.exception))
        md2 = "\n".join(m.value for m in at.markdown)
        for needle in ["85.8%", "62.2%", "22.1%", "1,510"]:
            self.assertIn(needle, md2, f"missing after switch: {needle}")


if __name__ == "__main__":
    unittest.main()
