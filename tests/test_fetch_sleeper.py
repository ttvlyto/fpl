import json
import unittest

from sleeperfpl.db import connect, latest
from sleeperfpl.fetch_sleeper import fetch_league, fetch_players, fetch_rosters, fetch_trending
from sleeperfpl.http import FetchError


def fake(responses):
    """get_json stand-in: match the URL path after /v1/ (query string ignored)."""
    def get(url, **kwargs):
        path = url.split("/v1/", 1)[1].split("?", 1)[0]
        if path not in responses:
            raise FetchError(f"no fake for {path}")
        value = responses[path]
        if isinstance(value, Exception):
            raise value
        return value
    return get


class FetchSleeperTests(unittest.TestCase):
    def setUp(self):
        self.conn = connect(":memory:")

    def test_fetch_league_returns_sport_and_stores_users(self):
        sport = fetch_league(self.conn, "L1", get=fake({
            "league/L1": {"sport": "clubsoccer:epl", "scoring_settings": {"goal": 6}, "roster_positions": ["GK"]},
            "league/L1/users": [{"user_id": "u1", "display_name": "me"}],
        }))
        self.assertEqual(sport, "clubsoccer:epl")
        self.assertEqual(latest(self.conn, "sleeper_users")[0]["display_name"], "me")
        data = json.loads(latest(self.conn, "sleeper_league")[0]["data_json"])
        self.assertEqual(data["scoring_settings"], {"goal": 6})

    def test_fetch_players_skips_inactive_and_clubless(self):
        n = fetch_players(self.conn, "s", get=fake({"players/s": {
            "1": {"player_id": "1", "full_name": "A B", "team": "ARS", "active": True, "fantasy_positions": ["MID"]},
            "2": {"player_id": "2", "full_name": "C D", "team": None, "active": True},
            "3": {"player_id": "3", "full_name": "E F", "team": "CHE", "active": False},
        }}))
        self.assertEqual(n, 1)
        rows = latest(self.conn, "sleeper_players")
        self.assertEqual([r["player_id"] for r in rows], ["1"])
        self.assertEqual(json.loads(rows[0]["positions_json"]), ["MID"])

    def test_trending_failure_is_non_fatal(self):
        self.assertEqual(fetch_trending(self.conn, "s", get=fake({})), 0)
        self.assertEqual(latest(self.conn, "sleeper_trending"), [])

    def test_trending_stored(self):
        n = fetch_trending(self.conn, "s", get=fake({"players/s/trending/add": [{"player_id": "7", "count": 40}]}))
        self.assertEqual(n, 1)
        self.assertEqual(latest(self.conn, "sleeper_trending")[0]["count"], 40)

    def test_failed_rosters_fetch_keeps_previous_snapshot(self):
        fetch_rosters(self.conn, "L1", get=fake({"league/L1/rosters": [
            {"roster_id": 1, "owner_id": "u1", "players": ["1"], "starters": ["1"], "reserve": None}]}))
        with self.assertRaises(FetchError):
            fetch_rosters(self.conn, "L1", get=fake({"league/L1/rosters": FetchError("down")}))
        rows = latest(self.conn, "sleeper_rosters")
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["players_json"]), ["1"])


if __name__ == "__main__":
    unittest.main()
