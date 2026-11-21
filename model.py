"""
ZEC 3020 Breakout: the rules, and the TickerDB data they run on.

Rules, decided only at each daily close (00:00 UTC):
  1. Buy when ZEC closes at its highest close of the last 30 days, unless the
     day's volume is low.
  2. Sell when ZEC closes below its 20-day SMA, or when the day's volume is low
     (TickerDB's volume_ratio_zone is "low" or "extremely_low": under 70% of its
     average). Then wait in cash for the next buy.
  3. A missing input changes nothing.
  4. Long or cash only, 1x, no leverage, no shorts. In the backtest, orders fill
     at the next daily open.

Used by backtest.py (history since 2021) and run.py (today's decision).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

API = "https://api.tickerdb.com/v1"
TICKER = "ZECUSD"

# Parameters
HIGH_DAYS = 30                          # buy at the highest close of this many days (today included)
LOW_VOLUME = ("low", "extremely_low")   # volume_ratio_zone values that sell and block a buy


# ---------------------------------------------------------------- data
def api_key() -> str:
    key = os.environ.get("TICKERDB_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set TICKERDB_API_KEY (free key: see https://tickerdb.com/setup).")
    return key


def get(path: str, params: dict | None = None) -> dict:
    url = f"{API}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    # A named User-Agent: Python's default one is refused (403) in front of the API.
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key()}", "Accept": "application/json",
                                               "User-Agent": "tickerdb-zec-3020-breakout/1.0 (+https://github.com/tickerdb/zec-3020-breakout)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def fetch_daily(start: str, end: str | None = None) -> list[dict]:
    """ZECUSD daily rows from TickerDB: open/high/low/close, the 20-day SMA and the volume zone,
    oldest first. One /v1/history request (about 2 credits per 50 days)."""
    params = {
        "tickers": TICKER,
        "timeframe": "daily",
        "fields": "trend_ma20_price,volume_ratio_zone",
        "ohlcv": "true",
        "start": start,
    }
    if end:
        params["end"] = end
    rows: list[dict] = []
    page = get("/history", params)
    tokens = list(page.get("page_tokens") or [])
    while True:
        rows += page.get("rows", [])
        if page.get("next_page_token"):
            page = get("/history", {**params, "page_token": page["next_page_token"]})
        elif tokens:
            page = get("/history", {**params, "page_token": tokens.pop(0)})
        else:
            break
    out = [
        {
            "date": r["date"],
            "open": float(r["ohlcv_open"]),
            "high": float(r["ohlcv_high"]),
            "low": float(r["ohlcv_low"]),
            "close": float(r["ohlcv_close"]),
            "ma20": None if r.get("trend_ma20_price") is None else float(r["trend_ma20_price"]),
            "volume_zone": r.get("volume_ratio_zone") or None,
        }
        for r in rows
        if r.get("ohlcv_close") is not None
    ]
    out.sort(key=lambda r: r["date"])
    return out


def latest_published_day() -> str | None:
    """The newest crypto date TickerDB has published (GET /v1/status, free)."""
    status = get("/status")
    return (((status.get("timeframes") or {}).get("daily") or {}).get("asset_classes") or {}).get("crypto", {}).get("date")


# ---------------------------------------------------------------- signals
def breakout(rows: list[dict], i: int) -> bool:
    """Row i's close is the highest of HIGH_DAYS consecutive daily closes ending at it.
    False with less history or a missing day: unknown is not a breakout."""
    if i < HIGH_DAYS - 1:
        return False
    window = rows[i - HIGH_DAYS + 1:i + 1]
    first = date.fromisoformat(window[0]["date"])
    if any(r["date"] != (first + timedelta(days=k)).isoformat() for k, r in enumerate(window)):
        return False
    return all(rows[i]["close"] >= r["close"] for r in window)


def low_volume(rows: list[dict], i: int) -> bool:
    """The day's volume zone is low. An unknown zone is not low: no change."""
    return rows[i].get("volume_zone") in LOW_VOLUME


def buy_signal(rows: list[dict], i: int) -> bool:
    return breakout(rows, i) and not low_volume(rows, i)


def sell_reason(rows: list[dict], i: int) -> str | None:
    """Why to sell after row i's close, or None."""
    if low_volume(rows, i):
        return "sell (low volume)"
    ma20 = rows[i]["ma20"]
    if ma20 is not None and rows[i]["close"] < ma20:
        return "sell (below 20-day SMA)"
    return None


@dataclass
class Position:
    side: str = "cash"         # "long" or "cash"
    entry_price: float = 0.0
    entry_date: str = ""


def decide(rows: list[dict], i: int, pos: Position) -> tuple[list[str], str]:
    """What to do after the close of row i, given the current position.
    Returns the actions and the side to hold afterwards."""
    if pos.side == "long":
        reason = sell_reason(rows, i)
        return ([reason], "cash") if reason else ([], "long")
    if buy_signal(rows, i):
        return ["buy"], "long"
    return [], "cash"
