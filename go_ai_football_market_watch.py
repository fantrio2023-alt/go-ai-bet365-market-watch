"""Standalone Bet365 market watch using the existing The Odds API key pool."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.the-odds-api.com/v4/sports"
PRIMARY_BOOKMAKER = os.getenv("GO_AI_MARKET_WATCH_BOOKMAKER", "bet365").strip().lower() or "bet365"
TOPIC = os.getenv("GO_AI_MARKET_WATCH_NTFY_TOPIC", "goai-al-2026-94731-market-watch").strip()
STATE_FILE = os.getenv("GO_AI_MARKET_WATCH_STATE", ".go_ai_market_watch_state.json")
TIMEOUT = int(os.getenv("GO_AI_MARKET_WATCH_TIMEOUT", "20"))
# One league per run keeps credit use controlled; the list rotates between runs.
SPORTS = [x.strip() for x in os.getenv(
    "GO_AI_MARKET_WATCH_SPORTS",
    "soccer_epl,soccer_uefa_champs_league,soccer_spain_la_liga,soccer_germany_bundesliga,soccer_italy_serie_a,soccer_france_ligue_one"
).split(",") if x.strip()]
MARKETS = os.getenv("GO_AI_MARKET_WATCH_MARKETS", "h2h,spreads,totals").strip() or "h2h"
REGIONS = os.getenv("GO_AI_MARKET_WATCH_REGIONS", "eu").strip() or "eu"


def normalize_keys(raw):
    value = str(raw or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                value = "\n".join(str(x) for x in parsed)
        except (TypeError, ValueError):
            pass
    out = []
    for part in re.split(r"[\n,;]+", value):
        key = part.strip().strip('"').strip("'").strip()
        if "=" in key and key.split("=", 1)[0].strip().lower() in {
            "odds_api_key", "odds_api_keys", "apikey", "api_key"
        }:
            key = key.split("=", 1)[1].strip().strip('"').strip("'").strip()
        if key:
            out.append(key)
    return out


def api_keys():
    keys, seen = [], set()
    aggregate = []
    for env_name in ("ODDS_API_KEYS", "ODDS_API_KEY"):
        aggregate.extend(normalize_keys(os.getenv(env_name, "")))
    sources = aggregate[:]
    if len(aggregate) < 30:
        for index in range(1, 31):
            sources.extend(normalize_keys(os.getenv(f"ODDS_API_KEY_{index}", "")))
    for key in sources:
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys[:30]


def get_odds(sport, key):
    params = {
        "apiKey": key,
        "bookmakers": PRIMARY_BOOKMAKER,
        "markets": MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    url = f"{BASE}/{urllib.parse.quote(sport, safe='_')}/odds/?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "GO-AI-BET365-MARKET-WATCH/2.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))
        headers = response.headers
    return payload, headers


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def norm(value):
    return " ".join(str(value or "").casefold().split())


def active_market_names(markets):
    names = []
    for market in markets if isinstance(markets, list) else []:
        if not isinstance(market, dict):
            continue
        outcomes = market.get("outcomes")
        has_price = isinstance(outcomes, list) and any(
            isinstance(row, dict) and row.get("price") not in (None, "")
            for row in outcomes
        )
        if has_price:
            names.append(str(market.get("key") or market.get("name") or "unnamed_market"))
    return sorted(set(names), key=str.casefold)


def ntfy(title, body, event_id):
    if not TOPIC:
        raise RuntimeError("GO_AI_MARKET_WATCH_NTFY_TOPIC is empty")
    url = "https://ntfy.sh/" + urllib.parse.quote(TOPIC, safe="")
    request = urllib.request.Request(
        url, data=body.encode("utf-8"), method="POST",
        headers={
            "Title": title, "Priority": "urgent", "Tags": "rotating_light,soccer",
            "Click": "https://www.bet365.com/", "X-Event-ID": str(event_id),
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")


def main():
    keys = api_keys()
    if not keys:
        print("MARKET WATCH: AUTH_NOT_CONFIGURED — no ODDS_API_KEY / ODDS_API_KEYS secret is available in this repository.")
        return 1
    state = load_state()
    sport_index = int(state.get("_sport_index", -1))
    sport = SPORTS[(sport_index + 1) % len(SPORTS)]
    key_index = int(state.get("_key_index", -1)) % len(keys)
    ordered_keys = keys[key_index:] + keys[:key_index]
    payload = None
    used_index = None
    last_error = None
    for attempt, key in enumerate(ordered_keys):
        try:
            payload, headers = get_odds(sport, key)
            used_index = (key_index + attempt) % len(keys)
            remaining = headers.get("x-requests-remaining", "unknown")
            used = headers.get("x-requests-used", "unknown")
            last_error = None
            print(f"MARKET WATCH: provider=The Odds API key_pool={len(keys)} selected_key_slot={used_index + 1} sport={sport} remaining={remaining} used={used}")
            break
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (401, 429) and attempt < len(ordered_keys) - 1:
                print(f"MARKET WATCH: key slot {((key_index + attempt) % len(keys)) + 1} returned HTTP {exc.code}; trying next configured key.")
                continue
            if exc.code == 401:
                print("MARKET WATCH: AUTH_FAILED HTTP 401 — all configured The Odds API keys were rejected.")
            elif exc.code == 429:
                print("MARKET WATCH: QUOTA_OR_RATE_LIMIT HTTP 429 — all configured keys were rate-limited or out of quota.")
            else:
                print(f"MARKET WATCH: PROVIDER_HTTP_ERROR status={exc.code} sport={sport}.")
            return 1
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"MARKET WATCH: PROVIDER_CONNECTION_OR_DATA_ERROR type={type(exc).__name__} sport={sport}.")
            return 1
    if last_error or payload is None:
        return 1
    events = payload if isinstance(payload, list) else []
    previous = state
    current = dict(previous)
    current.update({"_sport_index": (sport_index + 1) % len(SPORTS),
                    "_key_index": (used_index + 1) % len(keys),
                    "_last_sport": sport, "_last_run_utc": datetime.now(timezone.utc).isoformat()})
    # Retain cross-league observations so the next rotation can compare the same match.
    now = datetime.now(timezone.utc)
    for old_id, old_value in list(current.items()):
        if str(old_id).startswith("_") or not isinstance(old_value, dict):
            continue
        try:
            seen = datetime.fromisoformat(str(old_value.get("last_seen_utc", "")).replace("Z", "+00:00"))
            if (now - seen).total_seconds() > 7 * 86400:
                current.pop(old_id, None)
        except (ValueError, TypeError):
            current.pop(old_id, None)
    alerts = []
    checked = 0
    for event in events:
        if not isinstance(event, dict) or event.get("id") is None:
            continue
        bookmakers = event.get("bookmakers") or []
        book = next((b for b in bookmakers if norm(b.get("key")) == norm(PRIMARY_BOOKMAKER)
                     or norm(b.get("title")) == norm("Bet365")), None)
        if not book:
            continue
        event_id = str(event["id"])
        names = active_market_names(book.get("markets"))
        match = f'{event.get("home_team", "Unknown")} vs {event.get("away_team", "Unknown")}'
        current[event_id] = {
            "match": match, "sport": sport, "market_names": names,
            "market_count": len(names), "last_seen_utc": datetime.now(timezone.utc).isoformat(),
            "commence_time": event.get("commence_time", "unknown"),
        }
        checked += 1
        old = previous.get(event_id, {})
        if len(names) == 1 and int(old.get("market_count", -1)) != 1:
            alerts.append((event_id, event, names[0], old.get("market_count"), match))

    sent = 0
    for event_id, event, market_name, old_count, match in alerts:
        body = (
            "GO AI — ПРИОРИТЕТЕН ФУТБОЛЕН МАЧ\n\n"
            f"Мач: {match}\nЛига/API feed: {sport}\n"
            f"Източник: Bet365 (The Odds API)\nНалични пазари в наблюдавания фийд: 1\n"
            f"Единствен останал пазар: {market_name}\n"
            f"Начало: {event.get('commence_time', 'unknown')}\n"
            f"Предишен брой пазари: {old_count if old_count is not None else 'няма предишна снимка'}\n\n"
            "Приоритетно известие. Това е сигнал за намалял брой пазари сред заявените h2h/spreads/totals, "
            "не доказателство за манипулация."
        )
        ntfy("GO AI: Bet365 — останал е 1 пазар", body, event_id)
        sent += 1
        print(f"MARKET WATCH ALERT: {match} | market={market_name} | sport={sport}")

    save_state(current)
    print(f"MARKET WATCH: result=OK sport={sport} events_returned={len(events)} bet365_events_checked={checked} "
          f"one_market={sum(1 for k,v in current.items() if not k.startswith('_') and v.get('market_count') == 1)} "
          f"alerts_sent={sent} ntfy_topic={TOPIC}")
    if not checked:
        print("MARKET WATCH: NO_BET365_EVENTS — request succeeded but this sport feed returned no Bet365 odds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
