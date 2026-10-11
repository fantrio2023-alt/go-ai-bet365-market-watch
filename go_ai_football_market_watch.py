"""Standalone Bet365 Market Watch, isolated from the primary GO AI scanner."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.odds-api.io/v3"
PRIMARY_BOOKMAKER = os.getenv("GO_AI_MARKET_WATCH_BOOKMAKER", "Bet365").strip() or "Bet365"
TOPIC = os.getenv("GO_AI_MARKET_WATCH_NTFY_TOPIC", "goai-al-2026-94731-market-watch").strip()
STATE_FILE = os.getenv("GO_AI_MARKET_WATCH_STATE", ".go_ai_market_watch_state.json")
TIMEOUT = int(os.getenv("GO_AI_MARKET_WATCH_TIMEOUT", "20"))


def api_keys():
    value = os.getenv("ODDS_API_IO_KEY", "").strip()
    return [("ODDS_API_IO_KEY", value)] if value else []


ACTIVE_KEY_NAME = None
ACTIVE_KEY_VALUE = None


def get(path, params):
    global ACTIVE_KEY_NAME, ACTIVE_KEY_VALUE
    candidates = api_keys()
    if not candidates:
        raise RuntimeError("Missing ODDS_API_IO_KEY for the Bet365 Odds-API.io feed")
    if ACTIVE_KEY_VALUE:
        candidates.sort(key=lambda item: item[1] != ACTIVE_KEY_VALUE)
    last_auth_error = None
    for index, (env_name, key) in enumerate(candidates):
        query = dict(params)
        query["apiKey"] = key
        url = f"{BASE}{path}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"User-Agent": "GO-AI-BET365-MARKET-WATCH/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                last_auth_error = exc
                if index < len(candidates) - 1:
                    print(f"MARKET WATCH: credential {env_name} rejected with HTTP 401; trying alternate configured credential.")
                    continue
            raise
        if ACTIVE_KEY_NAME != env_name:
            ACTIVE_KEY_NAME, ACTIVE_KEY_VALUE = env_name, key
            print(f"MARKET WATCH: authenticated with configured credential source={env_name}.")
        return payload
    if last_auth_error:
        raise last_auth_error
    raise RuntimeError("No usable Odds-API.io credential")


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
        odds = market.get("odds")
        has_price = isinstance(odds, list) and any(
            isinstance(row, dict) and any(v not in (None, "") for k, v in row.items()
                                         if k not in ("name", "selection", "updatedAt"))
            for row in odds
        )
        if has_price:
            names.append(str(market.get("name") or market.get("market") or "Unnamed market"))
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
    if not api_keys():
        print("MARKET WATCH: AUTH_NOT_CONFIGURED — set the ODDS_API_IO_KEY secret in this repository.")
        return 1

    events_by_id = {}
    try:
        for status in ("pending", "live"):
            data = get("/events", {"sport": "football", "bookmaker": PRIMARY_BOOKMAKER,
                                   "status": status, "limit": 500})
            if isinstance(data, list):
                for event in data:
                    if isinstance(event, dict) and event.get("id") is not None:
                        event["_watch_status"] = status
                        events_by_id[str(event["id"])] = event
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            print("MARKET WATCH: AUTH_FAILED HTTP 401 — Odds-API.io rejected ODDS_API_IO_KEY. "
                  "This does not by itself indicate remaining credits. Check key/account access and auth format.")
            return 1
        print(f"MARKET WATCH: PROVIDER_HTTP_ERROR status={exc.code} during event discovery.")
        return 1
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"MARKET WATCH: PROVIDER_CONNECTION_ERROR type={type(exc).__name__}.")
        return 1

    events = list(events_by_id.values())
    if not events:
        print("MARKET WATCH: AUTH_OK events=0 — provider request succeeded but returned no Bet365 football events.")
        return 0

    previous = load_state()
    current = {}
    alerts = []
    ids = [str(e["id"]) for e in events]
    for offset in range(0, len(ids), 10):
        batch = ids[offset:offset + 10]
        try:
            data = get("/odds/multi", {"eventIds": ",".join(batch), "bookmakers": PRIMARY_BOOKMAKER})
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                print("MARKET WATCH: AUTH_FAILED HTTP 401 on odds lookup — check Odds-API.io key/account.")
            else:
                print(f"MARKET WATCH: PROVIDER_HTTP_ERROR status={exc.code} during odds lookup.")
            return 1
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"MARKET WATCH: PROVIDER_CONNECTION_ERROR during odds lookup type={type(exc).__name__}.")
            return 1

        rows = data if isinstance(data, list) else []
        for row in rows:
            if not isinstance(row, dict) or row.get("id") is None:
                continue
            event_id = str(row["id"])
            event = events_by_id.get(event_id, {})
            bookmakers = row.get("bookmakers") or {}
            markets = bookmakers.get(PRIMARY_BOOKMAKER)
            if markets is None:
                markets = next((v for k, v in bookmakers.items() if norm(k) == norm(PRIMARY_BOOKMAKER)), [])
            names = active_market_names(markets)
            current[event_id] = {
                "match": f'{event.get("home", "Unknown")} vs {event.get("away", "Unknown")}',
                "status": event.get("_watch_status", "unknown"),
                "market_names": names, "market_count": len(names),
                "last_seen_utc": datetime.now(timezone.utc).isoformat(),
            }
            old = previous.get(event_id, {})
            if len(names) == 1 and int(old.get("market_count", -1)) != 1:
                alerts.append((event_id, event, names[0], old.get("market_count")))

    sent = 0
    for event_id, event, market_name, old_count in alerts:
        state = current[event_id]
        start = event.get("date") or event.get("start") or event.get("commence_time") or "unknown"
        body = (
            "GO AI — ПРИОРИТЕТЕН ФУТБОЛЕН МАЧ\n\n"
            f"Мач: {state['match']}\nСтатус на фийда: {str(state['status']).upper()}\n"
            f"Източник: {PRIMARY_BOOKMAKER}\nНалични активни пазари: 1\n"
            f"Единствен останал пазар: {market_name}\nНачало (данни от доставчика): {start}\n"
            f"Предишен брой активни пазари: {old_count if old_count is not None else 'няма предишна снимка'}\n\n"
            "Приоритетно известие. Това е сигнал за намалял брой пазари, не доказателство за манипулация."
        )
        ntfy("GO AI: Bet365 — останал е 1 пазар", body, event_id)
        sent += 1
        print(f"MARKET WATCH ALERT: {state['match']} | market={market_name} | status={str(state['status']).upper()}")

    save_state(current)
    print(f"MARKET WATCH: provider=Odds-API.io bookmaker={PRIMARY_BOOKMAKER} events={len(events)} "
          f"markets_checked={len(current)} one_market={sum(1 for x in current.values() if x['market_count'] == 1)} "
          f"alerts_sent={sent} ntfy_topic={TOPIC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
