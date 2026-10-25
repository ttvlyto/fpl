# Sleeper EPL rankings

Builds a static web page for a Sleeper Premier League fantasy league, using
your league's exact scoring:

- your best starting XI and drop/add swap tips
- top free agents per position
- match odds from Kalshi and Polymarket, used to adjust projections
- a live match-day section
- a report card on past projections

## Requirements

Python 3.10+, standard library only. No API keys.

## Quick start

```bash
cp config.example.json config.json   # then edit it: set league_id and owner

python3 -m sleeperfpl fetch-daily
python3 -m sleeperfpl fetch-rosters
python3 -m sleeperfpl fetch-history --force   # first time only; takes a few minutes
python3 -m sleeperfpl fetch-markets
python3 -m sleeperfpl build                   # writes site/index.html
```

Open `site/index.html` in a browser.

To check how accurate the projections have been on past weeks, run
`python3 -m sleeperfpl backtest` (add `fetch-history --season 2025` once to
include last season).

## Documentation

See [docs/README.md](docs/README.md) for every command and config key, how
projections work, the scoring map and overrides files, server deployment
(cron + systemd), the database, and the code layout.
