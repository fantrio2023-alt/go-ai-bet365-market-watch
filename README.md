# Bet365 Market Watch

The Bet365 market watcher uses **The Odds API v4** and the same key pool as the main GO AI monitor.

## Key source and rotation

The canonical workflow is in [fantrio2023-alt/go-ai-monitor](https://github.com/fantrio2023-alt/go-ai-monitor/actions/workflows/bet365-market-watch-isolated.yml). It injects the `ODDS_API__KEY` secret, plus the established `ODDS_API_KEY`, `ODDS_API_KEYS`, and numbered `ODDS_API_KEY_1` through `ODDS_API_KEY_30` secrets. The watcher deduplicates the pool, rotates the starting key between runs, and retries another key only after HTTP 401 or 429. Secret values are never printed.

## Request and quota

- One request per scheduled run, every 30 minutes.
- Endpoint: `GET https://api.the-odds-api.com/v4/sports/soccer_spain_la_liga/odds/`
- Requests Bet365 prices for `h2h,spreads,totals`.
- Logs the provider's remaining/used request headers without exposing any key.
- Default ntfy topic: `goai-al-2026-94731-market-watch`.

The standalone source workflow only validates syntax and makes no provider request. The live runtime uses the main repository's secrets because GitHub does not share Actions secrets between repositories.
