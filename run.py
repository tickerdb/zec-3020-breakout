"""
Run ZEC 3020 Breakout for today: what to do after the latest daily close.

    export TICKERDB_API_KEY=tdb_...      # free key: https://tickerdb.com/setup
    python run.py

Schedule it once a day, after TickerDB publishes the day that just closed
(a ZEC day closes at 00:00 UTC; TickerDB publishes it within the hour), for
example 04:30 UTC. Each run:

  0. The first time (no state.json): reads what the model holds now, its newest
     book (/v1/models/zec-3020-breakout/holdings, 1 credit), and starts from it.
  1. Checks GET /v1/status (free). If yesterday's (UTC) day is not published
     yet, it exits with code 75 so your scheduler can retry in 15 minutes.
  2. Fetches the last 40 days of ZECUSD from /v1/history, fresh every run
     (about 2 credits; daily runs fit the free plan's 250 credits a month).
  3. Applies the rules to the newest day, given the position in state.json,
     prints what to do and the position (side, entry date and price), and saves it.

It never places orders: it tells you (or your agent) what the model does.
A day is only decided once; running twice on the same day does nothing new.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import book
import model
from model import HIGH_DAYS, TICKER, Position, decide, fetch_daily, latest_published_day

HERE = Path(__file__).resolve().parent
STATE = HERE / "state.json"
NOT_READY = 75  # EX_TEMPFAIL: try again later
SLUG = "zec-3020-breakout"
LOOKBACK_DAYS = HIGH_DAYS + 10


def from_book(b: dict | None, rows: list[dict]) -> tuple[Position, str]:
    """The position to start from: the model's, once its book's next-open orders are filled. A buy decided at
    the book's close enters at the next open, which for ZEC is that close (00:00 UTC)."""
    if b is None:
        return Position(), ""
    held = [h for h in book.after_next_open(b) if h.get("ticker") == TICKER]
    if not held:
        return Position(), b["date"]
    h = held[0]
    if h.get("position_state") == "entering":
        close = next((r["close"] for r in rows if r["date"] == b["date"]), 0.0)
        return Position("long", float(close or 0.0), b["date"]), b["date"]
    return Position("long", float(h.get("entry_price") or 0.0), h.get("entry_date") or ""), b["date"]


def load_state(rows: list[dict]) -> tuple[Position, str]:
    if STATE.exists():
        s = json.loads(STATE.read_text())
        return Position(s.get("side", "cash"), float(s.get("entry_price", 0)), s.get("entry_date", "")), s.get("last_decided", "")
    b = book.fetch(model.get, SLUG)
    if b:
        book.show(b)
    return from_book(b, rows)


def describe(pos: Position) -> str:
    return pos.side + (f" since {pos.entry_date} at ${pos.entry_price:,.2f}" if pos.side != "cash" else "")


def save_state(pos: Position, day: str) -> None:
    STATE.write_text(json.dumps({"side": pos.side, "entry_price": pos.entry_price, "entry_date": pos.entry_date,
                                 "last_decided": day}, indent=2) + "\n")


def main() -> int:
    just_closed = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    published = latest_published_day()
    if not published or published < just_closed:
        print(f"TickerDB has not published {just_closed} yet (latest: {published}). Try again in 15 minutes.")
        return NOT_READY

    start = (datetime.fromisoformat(just_closed) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    rows = fetch_daily(start)
    if not rows or rows[-1]["date"] < just_closed:
        print(f"No {TICKER} row for {just_closed} yet. Try again in 15 minutes.")
        return NOT_READY

    pos, last_decided = load_state(rows)
    day = rows[-1]["date"]
    if day <= last_decided:
        save_state(pos, last_decided)
        print(f"{day} was already decided. Position: {describe(pos)} ({TICKER} closed at ${rows[-1]['close']:,.2f})")
        return 0

    i = len(rows) - 1
    close = rows[i]["close"]
    actions, side = decide(rows, i, pos)
    if side != pos.side:
        # A new position enters at about today's price (the backtest fills at the next open, 00:00 UTC).
        pos = Position(side, close, day) if side != "cash" else Position()
    save_state(pos, day)

    print(f"{TICKER} close {day}: ${close:,.2f}")
    print("Action: " + (", then ".join(actions) if actions else "none, hold"))
    print(f"Position: {describe(pos)}")
    if pos.side == "long":
        ma20 = rows[i]["ma20"]
        if ma20 is not None:
            print(f"  sells at a close below the 20-day SMA (now ${ma20:,.2f}) or on a low-volume day")
    return 0


if __name__ == "__main__":
    sys.exit(main())
