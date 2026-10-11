"""Bet365 watcher using Odds-API.io v3 and ODDS_API_IO_KEY."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.odds-api.io/v3"
API_KEY = (os.getenv("ODDS_API_IO_KEY") or "").strip()
TOPIC = os.getenv("GO_AI_MARKET_WATCH_NTFY_TOPIC", "goai-al-2026-94731-market-watch").strip()
STATE_FILE = os.getenv("GO_AI_MARKET_WATCH_STATE", ".go_ai_market_watch_state.json")
TIMEOUT = int(os.getenv("GO_AI_MARKET_WATCH_TIMEOUT", "20"))
SPORT = os.getenv("GO_AI_MARKET_WATCH_SPORT", "football").strip() or "football"
BOOKMAKER = os.getenv("GO_AI_MARKET_WATCH_BOOKMAKER", "Bet365").strip() or "Bet365"
MAX_EVENTS = max(1, min(10, int(os.getenv("GO_AI_MARKET_WATCH_MAX_EVENTS", "10"))))


def api_get(path, params):
    query = dict(params)
    query["apiKey"] = API_KEY
    url = BASE + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": "GO-AI-BET365-MARKET-WATCH/3.0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not print the exception or URL: the API key is a query parameter.
        if exc.code == 401:
            print("MARKET WATCH: ODDS_API_IO_AUTH_FAILED HTTP 401 — provider rejected ODDS_API_IO_KEY.")
        elif exc.code == 403:
            print("MARKET WATCH: ODDS_API_IO_FORBIDDEN HTTP 403 — account/key lacks access.")
        elif exc.code == 429:
            print("MARKET WATCH: ODDS_API_IO_RATE_LIMIT HTTP 429 — provider rate limit or quota reached.")
        else:
            print("MARKET WATCH: ODDS_API_IO_HTTP_ERROR status=" + str(exc.code) + " endpoint=/" + path.lstrip("/") + ".")
        raise


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
    if not isinstance(markets, list):
        return []
    names = set()
    price_fields = {"home", "draw", "away", "over", "under", "yes", "no", "odd", "price"}
    for market in markets:
        if not isinstance(market, dict):
            continue
        rows = market.get("odds")
        if not isinstance(rows, list):
            continue
        active = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                if key.casefold() not in price_fields or value in (None, ""):
                    continue
                try:
                    if float(value) > 1.0:
                        active = True
                        break
                except (TypeError, ValueError):
                    continue
            if active:
                break
        if active:
            names.add(str(market.get("name") or market.get("key") or "unnamed_market"))
    return sorted(names, key=str.casefold)


def get_bookmaker_markets(event):
    books = event.get("bookmakers")
    if not isinstance(books, dict):
        return []
    for name, markets in books.items():
        if norm(name) == norm(BOOKMAKER):
            return markets if isinstance(markets, list) else []
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
            raise RuntimeError("ntfy returned HTTP " + str(response.status))


def main():
    if not API_KEY:
        print("MARKET WATCH: AUTH_NOT_CONFIGURED — ODDS_API_IO_KEY is empty in the canonical workflow.")
        return 2

    # Two bounded API calls per poll: discover events, then price up to 10 in one multi request.
    discovered = api_get("events", {"sport": SPORT, "bookmaker": BOOKMAKER})
    if not isinstance(discovered, list):
        print("MARKET WATCH: ODDS_API_IO_BAD_EVENTS_RESPONSE — expected JSON list.")
        return 1
    events = [e for e in discovered if isinstance(e, dict) and e.get("id") is not None]
    if not events:
        print("MARKET WATCH: result=OK provider=Odds-API.io sport=" + SPORT + " events_returned=0 selected_events=0 bet365_events_checked=0 alerts_sent=0")
        print("MARKET WATCH: NO_EVENTS — no events returned for the requested sport/bookmaker.")
        return 0

    state = load_state()
    offset = int(state.get("_event_offset", 0)) % len(events)
    ordered = events[offset:] + events[:offset]
    selected = ordered[:MAX_EVENTS]
    ids = [str(e["id"]) for e in selected]
    odds_data = api_get("odds/multi", {"eventIds": ",".join(ids), "bookmakers": BOOKMAKER})
    if isinstance(odds_data, dict):
        priced_events = [odds_data]
    elif isinstance(odds_data, list):
        priced_events = odds_data
    else:
        priced_events = []
    by_id = {str(e.get("id")): e for e in priced_events if isinstance(e, dict) and e.get("id") is not None}

    previous = state
    current = dict(previous)
    current["_event_offset"] = (offset + len(selected)) % max(1, len(events))
    current["_last_run_utc"] = datetime.now(timezone.utc).isoformat()
    current["_last_provider"] = "Odds-API.io"
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

    checked = 0
    alerts = []
    for item in selected:
        event_id = str(item["id"])
        event = by_id.get(event_id, item)
        names = active_market_names(get_bookmaker_markets(event))
        if not names:
            continue
        checked += 1
        home = event.get("home") or item.get("home") or "Unknown"
        away = event.get("away") or item.get("away") or "Unknown"
        match = str(home) + " vs " + str(away)
        league = event.get("league") or item.get("league") or {}
        league_name = league.get("name", "unknown league") if isinstance(league, dict) else str(league)
        event_date = event.get("date") or item.get("date") or "unknown"
        current[event_id] = {
            "match": match, "sport": SPORT, "league": league_name,
            "market_names": names, "market_count": len(names),
            "last_seen_utc": datetime.now(timezone.utc).isoformat(), "commence_time": event_date,
        }
        old = previous.get(event_id, {})
        if len(names) == 1 and int(old.get("market_count", -1)) != 1:
            alerts.append((event_id, match, league_name, event_date, names[0], old.get("market_count")))

    sent = 0
    for event_id, match, league_name, event_date, market_name, old_count in alerts:
        body = (
            "GO AI — ПРИОРИТЕТЕН ФУТБОЛЕН МАЧ\n\n"
            + "Мач: " + match + "\nЛига: " + str(league_name) + "\n"
            + "Източник: Bet365 (Odds-API.io)\nНалични пазари: 1\n"
            + "Единствен останал пазар: " + market_name + "\nНачало: " + str(event_date) + "\n"
            + "Предишен брой пазари: " + (str(old_count) if old_count is not None else "няма предишна снимка") + "\n\n"
            + "Приоритетно известие за намалял брой пазари; само по себе си не доказва манипулация."
        )
        ntfy("GO AI: Bet365 — останал е 1 пазар", body, event_id)
        sent += 1
        print("MARKET WATCH ALERT: " + match + " | market=" + market_name + " | league=" + str(league_name))

    save_state(current)
    print(
        "MARKET WATCH: result=OK provider=Odds-API.io sport=" + SPORT
        + " events_returned=" + str(len(events))
        + " selected_events=" + str(len(selected))
        + " bet365_events_checked=" + str(checked)
        + " alerts_sent=" + str(sent) + " ntfy_topic=" + TOPIC
    )
    if not checked:
        print("MARKET WATCH: NO_PRICED_BET365_MARKETS — event discovery succeeded but selected events had no priced Bet365 markets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
