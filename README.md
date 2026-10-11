# Bet365 Market Watch

Standalone GO AI Bet365 Market Watch repository, independent from the primary Odds API signal scanner.

## Purpose

- Query Odds-API.io for Bet365 football pregame/live event availability and priced markets.
- Notify the dedicated ntfy topic when an event transitions to exactly one active priced market.
- Keep failures in this workflow independent from the main GO AI scanner.

## Required GitHub Actions secret

- `ODDS_API_IO_KEY` — Odds-API.io key with Bet365 pregame access.

## Workflow

`.github/workflows/bet365-market-watch.yml` runs on manual dispatch and a conservative 30-minute schedule while the provider authentication is being diagnosed. It does not use the primary Odds API key pool or alter the tennis/football model.

## Notification

Default ntfy topic: `goai-al-2026-94731-market-watch`.

## HTTP 401

A 401 is an authentication rejection; it does not by itself establish the remaining credit balance. Verify the key belongs to the account with Bet365 access and that the provider accepts the API-key format used by this client.
