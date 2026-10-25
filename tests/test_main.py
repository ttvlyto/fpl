import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from sleeperfpl.__main__ import main


class FetchStatusTests(unittest.TestCase):
    def test_fetch_status_runs_bootstrap_only(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.json"
            cfg.write_text(json.dumps({"league_id": "L1", "owner": "me", "db_path": "t.db"}))
            out = io.StringIO()
            with mock.patch("sleeperfpl.__main__.fetch_bootstrap", return_value=7) as boot, redirect_stdout(out):
                self.assertEqual(main(["--config", str(cfg), "fetch-status"]), 0)
            boot.assert_called_once()
            self.assertIn("fpl players: 7", out.getvalue())



class CrontabTests(unittest.TestCase):
    def test_fetch_status_never_shares_a_minute_with_matchday(self):
        lines = [l for l in (Path(__file__).parent.parent / "deploy" / "crontab.example").read_text().splitlines()
                 if l and not l.startswith("#")]
        status = [l.split()[0] for l in lines if "fetch-status" in l]
        self.assertTrue(status)
        for minute in status:
            self.assertNotIn(minute, ("0", "30", "*/30"))   # matchday runs at :00 and :30 under the same lock


if __name__ == "__main__":
    unittest.main()
