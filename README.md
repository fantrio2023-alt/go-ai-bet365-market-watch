# Bet365 Market Watch

Standalone GO AI Bet365 Market Watch repository, independent from the primary Odds API signal scanner.

## Purpose

- Query Odds-API.io for Bet365 football pregame/live event availability and priced markets.
- Notify the dedicated ntfy topic when an event transitions to exactly one active priced market.
- Keep failures in this workflow independent from the main GO AI scanner.

## Required GitHub Actions secret

- `ODDS_API_IO_KEY` — Odds-API.io key with Bet365 pregame access.

## Workflow

**Runtime location:** the actual scheduled monitor runs from [`fantrio2023-alt/go-ai-monitor`](https://github.com/fantrio2023-alt/go-ai-monitor/actions/workflows/bet365-market-watch-isolated.yml), where the existing `ODDS_API_KEY` pool is configured. GitHub does not share repository secrets with this source repository automatically. A manual run of `.github/workflows/bet365-market-watch.yml` in this repository will fail until its own `ODDS_API_KEY` / `ODDS_API_KEYS` secrets are configured. The canonical workflow checks this repository's `main` branch for the latest watcher source and runs every 30 minutes.

## Notification

Default ntfy topic: `goai-al-2026-94731-market-watch`.

## HTTP 401

A 401 is an authentication rejection; it does not by itself establish the remaining credit balance. Verify the key belongs to the account with Bet365 access and that the provider accepts the API-key format used by this client.
