"""Bet365 market watcher using The Odds API v4 and the main GO AI key pool."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.the-odds-api.com/v4"
TOPIC = os.getenv("GO_AI_MARKET_WATCH_NTFY_TOPIC", "goai-al-2026-94731-market-watch").strip()
STATE_FILE = os.getenv("GO_AI_MARKET_WATCH_STATE", ".go_ai_market_watch_state.json")
TIMEOUT = int(os.getenv("GO_AI_MARKET_WATCH_TIMEOUT", "20"))
SPORT = os.getenv("GO_AI_MARKET_WATCH_SPORT", "soccer_spain_la_liga").strip() or "soccer_spain_la_liga"
BOOKMAKER = os.getenv("GO_AI_MARKET_WATCH_BOOKMAKER", "bet365").strip().casefold() or "bet365"
MARKETS = os.getenv("GO_AI_MARKET_WATCH_MARKETS", "h2h,spreads,totals").strip() or "h2h"
MAX_EVENTS = max(1, min(100, int(os.getenv("GO_AI_MARKET_WATCH_MAX_EVENTS", "100"))))


def _split_key_pool(raw):
    """Parse a single key or an aggregate comma/newline/JSON key pool."""
    value = str(raw or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                value = "\n".join(str(item) for item in parsed)
        except (TypeError, ValueError):
            pass
    result = []
    for part in __import__("re").split(r"[\n,;]+", value):
        key = part.strip().strip('"').strip("'").strip()
        if "=" in key and key.split("=", 1)[0].strip().lower() in {
            "odds_api_key", "odds_api_keys", "odds_api__key", "apikey", "api_key"
        }:
            key = key.split("=", 1)[1].strip().strip('"').strip("'").strip()
        if key:
            result.append(key)
    return result


def load_keys():
    """Load and deduplicate all configured GO AI Odds API key-pool formats."""
    keys = []
    # ODDS_API__KEY is explicitly supported, along with existing aggregate
    # ODDS_API_KEY / ODDS_API_KEYS secrets, which may contain many comma-separated keys.
    for name in ("ODDS_API__KEY", "ODDS_API_KEYS", "ODDS_API_KEY"):
        keys.extend(_split_key_pool(os.getenv(name, "")))
    for i in range(1, 31):
        keys.extend(_split_key_pool(os.getenv(f"ODDS_API_KEY_{i}", "")))
    return list(dict.fromkeys(keys))[:30]

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


def fetch_odds(sport, keys, start_index):
    url_path = f"/sports/{urllib.parse.quote(sport, safe='_')}/odds/"
    last_status = None
    # Try keys in rotation. Only retry a different key on authentication/quota errors.
    for offset in range(len(keys)):
        index = (start_index + offset) % len(keys)
        query = urllib.parse.urlencode({
            "apiKey": keys[index],
            "bookmakers": BOOKMAKER,
            "markets": MARKETS,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        })
        request = urllib.request.Request(
            BASE + url_path + "?" + query,
            headers={"User-Agent": "GO-AI-BET365-MARKET-WATCH/4.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
                remaining = response.headers.get("x-requests-remaining", "unknown")
                used = response.headers.get("x-requests-used", "unknown")
                return payload, index, remaining, used
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            if exc.code in (401, 429):
                continue
            print(f"MARKET WATCH: ODDS_API_HTTP_ERROR status={exc.code}; key rotation stopped.")
            return None, index, "unknown", "unknown"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"MARKET WATCH: REQUEST_FAILED type={type(exc).__name__}; no key or URL details logged.")
            return None, index, "unknown", "unknown"
    print(f"MARKET WATCH: KEY_POOL_FAILED tried={len(keys)} last_http_status={last_status}; check The Odds API key pool/quota.")
    return None, (start_index + len(keys)) % len(keys), "unknown", "unknown"


def active_markets(event):
    books = event.get("bookmakers")
    if not isinstance(books, list):
        return []
    for book in books:
        if not isinstance(book, dict):
            continue
        if norm(book.get("key")) != BOOKMAKER and norm(book.get("title")) != BOOKMAKER:
            continue
        markets = book.get("markets")
        if not isinstance(markets, list):
            return []
        active = []
        for market in markets:
            if not isinstance(market, dict):
                continue
            outcomes = market.get("outcomes")
            if not isinstance(outcomes, list):
                continue
            has_price = False
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                try:
                    if float(outcome.get("price", 0)) > 1.0:
                        has_price = True
                        break
                except (TypeError, ValueError):
                    pass
            if has_price:
                active.append(str(market.get("key") or market.get("name") or "unnamed_market"))
        return sorted(set(active), key=str.casefold)
    return []


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
    keys = load_keys()
    if not keys:
        print("MARKET WATCH: AUTH_NOT_CONFIGURED — no ODDS_API__KEY / ODDS_API_KEY pool entries were injected.")
        return 2

    state = load_state()
    key_start = int(state.get("_key_offset", 0)) % len(keys)
    payload, used_index, remaining, used = fetch_odds(SPORT, keys, key_start)
    if payload is None:
        return 1
    if not isinstance(payload, list):
        print("MARKET WATCH: BAD_RESPONSE — expected a JSON list from The Odds API v4.")
        return 1

    previous = state
    current = dict(previous)
    current["_key_offset"] = (used_index + 1) % len(keys)
    current["_last_run_utc"] = datetime.now(timezone.utc).isoformat()
    current["_last_provider"] = "The Odds API"
    current["_last_sport"] = SPORT
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

    events = [event for event in payload if isinstance(event, dict) and event.get("id") is not None]
    checked = 0
    alerts = []
    for event in events[:MAX_EVENTS]:
        names = active_markets(event)
        if not names:
            continue
        checked += 1
        event_id = str(event["id"])
        match = f'{event.get("home_team") or "Unknown"} vs {event.get("away_team") or "Unknown"}'
        league = event.get("sport_title") or SPORT
        event_date = event.get("commence_time") or "unknown"
        current[event_id] = {
            "match": match, "sport": SPORT, "league": league,
            "market_names": names, "market_count": len(names),
            "last_seen_utc": datetime.now(timezone.utc).isoformat(), "commence_time": event_date,
        }
        old = previous.get(event_id, {})
        if len(names) == 1 and int(old.get("market_count", -1)) != 1:
            alerts.append((event_id, match, league, event_date, names[0], old.get("market_count")))

    sent = 0
    for event_id, match, league, event_date, market_name, old_count in alerts:
        body = (
            "GO AI — ПРИОРИТЕТЕН ФУТБОЛЕН МАЧ\n\n"
            f"Мач: {match}\nЛига: {league}\n"
            "Източник: Bet365 (The Odds API)\nНалични пазари: 1\n"
            f"Единствен останал пазар: {market_name}\nНачало: {event_date}\n"
            f"Предишен брой пазари: {old_count if old_count is not None else 'няма предишна снимка'}\n\n"
            "Приоритетно известие за намалял брой пазари; само по себе си не доказва манипулация."
        )
        ntfy("GO AI: Bet365 — останал е 1 пазар", body, event_id)
        sent += 1
        print(f"MARKET WATCH ALERT: {match} | market={market_name} | league={league}")

    save_state(current)
    print(
        f"MARKET WATCH: result=OK provider=The Odds API sport={SPORT} "
        f"key_pool={len(keys)} selected_key_slot={used_index + 1} "
        f"remaining={remaining} used={used} events_returned={len(events)} "
        f"bet365_events_checked={checked} alerts_sent={sent} ntfy_topic={TOPIC}"
    )
    if not checked:
        print("MARKET WATCH: NO_PRICED_BET365_MARKETS — request succeeded, but no priced Bet365 markets were returned for this sport.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
