# Bet365 Market Watch

Source repository for the GO AI Bet365 market watcher. The live monitor uses **Odds-API.io v3** and the **ODDS_API_IO_KEY** secret. It does not use the separate The Odds API key pool.

## What it does

- Finds football events offered by Bet365 through Odds-API.io.
- Fetches odds for at most 10 events per poll using the provider's multi-event endpoint.
- Tracks the number of priced markets across runs.
- Sends an ntfy notification when a match transitions to exactly one priced market.
- Runs on a conservative 30-minute schedule in the canonical runtime.

## Canonical runtime and secret

The scheduled monitor runs from [fantrio2023-alt/go-ai-monitor](https://github.com/fantrio2023-alt/go-ai-monitor/actions/workflows/bet365-market-watch-isolated.yml). The secret must exist there as the Actions secret `ODDS_API_IO_KEY`. GitHub does not share secrets between repositories. The source repository's manual workflow only checks Python syntax and intentionally makes no API request, so it cannot fail because of a missing local secret or spend API quota.

## Provider endpoints

- `GET https://api.odds-api.io/v3/events?sport=football&bookmaker=Bet365`
- `GET https://api.odds-api.io/v3/odds/multi` for up to 10 event IDs per request.

The API key is never printed in workflow logs. HTTP 401 is reported as an authentication rejection; if it appears in the canonical run, the value stored in `ODDS_API_IO_KEY` must be replaced with a valid key from the Odds-API.io account.

## Notification

Default ntfy topic: `goai-al-2026-94731-market-watch`.
