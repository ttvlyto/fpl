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


if __name__ == "__main__":
    unittest.main()
