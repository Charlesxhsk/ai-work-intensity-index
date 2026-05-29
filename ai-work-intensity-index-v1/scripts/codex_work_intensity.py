#!/usr/bin/env python3
"""Compute AI+ work intensity from local Codex logs."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import os
import re
import sqlite3
from pathlib import Path
from typing import Any


TURN_USAGE_RE = re.compile(
    r"post sampling token usage turn_id=([0-9a-f-]+) "
    r"total_usage_tokens=(\d+) "
    r"auto_compact_scope_tokens=(\d+) "
    r"estimated_token_count=Some\((\d+)\)"
)
THREAD_RE = re.compile(r"(?:thread_id|thread\.id)=([0-9a-f-]+)")
MODEL_RE = re.compile(r"turn\{[^}]*?model=([^\s}]+)")


def parse_tz_offset(value: str) -> dt.timedelta:
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", value)
    if not match:
        raise argparse.ArgumentTypeError("Use an offset like +08:00 or -0500.")
    sign, hours, minutes = match.groups()
    delta = dt.timedelta(hours=int(hours), minutes=int(minutes))
    return delta if sign == "+" else -delta


def local_day_to_utc_ts(day: dt.date, offset: dt.timedelta) -> int:
    local_midnight = dt.datetime.combine(day, dt.time.min)
    utc_time = (local_midnight - offset).replace(tzinfo=dt.timezone.utc)
    return int(utc_time.timestamp())


def local_date_from_ts(ts: int, offset: dt.timedelta) -> str:
    return (dt.datetime.fromtimestamp(ts, dt.timezone.utc) + offset).date().isoformat()


def default_codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    return Path(env_home).expanduser() if env_home else Path.home() / ".codex"


def connect_logs_db(codex_home: Path) -> sqlite3.Connection:
    db_path = codex_home / "logs_2.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"Codex log database not found: {db_path}")
    uri = "file:" + db_path.as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def extract_thread(body: str) -> str | None:
    match = THREAD_RE.search(body)
    return match.group(1) if match else None


def extract_model(body: str) -> str | None:
    match = MODEL_RE.search(body)
    return match.group(1) if match else None


def detect_latest_thread(con: sqlite3.Connection, start_ts: int, end_ts: int) -> str | None:
    rows = con.execute(
        """
        select feedback_log_body as body
        from logs
        where ts >= ? and ts < ?
          and (feedback_log_body like '%thread_id=%' or feedback_log_body like '%thread.id=%')
        order by ts desc, ts_nanos desc, id desc
        limit 500
        """,
        (start_ts, end_ts),
    )
    for row in rows:
        thread = extract_thread(row["body"] or "")
        if thread:
            return thread
    return None


def collect_turns(
    con: sqlite3.Connection,
    start_ts: int,
    end_ts: int,
    offset: dt.timedelta,
    excluded_thread: str | None,
) -> list[dict[str, Any]]:
    latest_by_turn: dict[str, dict[str, Any]] = {}
    rows = con.execute(
        """
        select id, ts, ts_nanos, feedback_log_body as body
        from logs
        where ts >= ? and ts < ?
          and target = 'codex_core::session::turn'
          and feedback_log_body like '%post sampling token usage%'
        order by ts, ts_nanos, id
        """,
        (start_ts, end_ts),
    )
    for row in rows:
        body = row["body"] or ""
        match = TURN_USAGE_RE.search(body)
        if not match:
            continue
        thread = extract_thread(body)
        if excluded_thread and thread == excluded_thread:
            continue
        turn_id = match.group(1)
        record = {
            "id": row["id"],
            "ts": row["ts"],
            "ts_nanos": row["ts_nanos"],
            "day": local_date_from_ts(row["ts"], offset),
            "thread": thread,
            "turn": turn_id,
            "model": extract_model(body),
            "tokens": int(match.group(2)),
            "auto_compact_scope_tokens": int(match.group(3)),
            "estimated_tokens": int(match.group(4)),
        }
        old = latest_by_turn.get(turn_id)
        if old is None or (record["ts"], record["ts_nanos"], record["id"]) > (
            old["ts"],
            old["ts_nanos"],
            old["id"],
        ):
            latest_by_turn[turn_id] = record
    return list(latest_by_turn.values())


def collect_api_requests(
    con: sqlite3.Connection,
    start_ts: int,
    end_ts: int,
    offset: dt.timedelta,
    excluded_thread: str | None,
) -> collections.Counter[str]:
    counts: collections.Counter[str] = collections.Counter()
    rows = con.execute(
        """
        select ts, feedback_log_body as body
        from logs
        where ts >= ? and ts < ?
          and target = 'codex_otel.log_only'
          and feedback_log_body like '%event.name="codex.api_request"%'
        """,
        (start_ts, end_ts),
    )
    for row in rows:
        body = row["body"] or ""
        thread = extract_thread(body)
        if excluded_thread and thread == excluded_thread:
            continue
        counts[local_date_from_ts(row["ts"], offset)] += 1
    return counts


def intensity_score(turns: int, tokens: int, api_requests: int) -> float:
    return (
        7.2 * math.log1p(turns)
        + 6.0 * math.log1p(tokens / 10000)
        + 2.4 * math.log1p(api_requests)
    )


def summarize(
    turns: list[dict[str, Any]],
    api_counts: collections.Counter[str],
    days: list[str],
) -> list[dict[str, Any]]:
    by_day: dict[str, dict[str, Any]] = {
        day: {
            "date": day,
            "turns": 0,
            "tokens": 0,
            "api_requests": api_counts[day],
            "threads": set(),
            "models": collections.Counter(),
        }
        for day in days
    }
    for turn in turns:
        day = turn["day"]
        if day not in by_day:
            continue
        item = by_day[day]
        item["turns"] += 1
        item["tokens"] += turn["tokens"]
        if turn["thread"]:
            item["threads"].add(turn["thread"])
        if turn["model"]:
            item["models"][turn["model"]] += 1

    result = []
    for day in days:
        item = by_day[day]
        score = intensity_score(item["turns"], item["tokens"], item["api_requests"])
        result.append(
            {
                "date": day,
                "turns": item["turns"],
                "tokens": item["tokens"],
                "api_requests": item["api_requests"],
                "score": round(score, 1),
                "threads": len(item["threads"]),
                "models": dict(item["models"]),
            }
        )
    return result


def render_markdown(rows: list[dict[str, Any]], excluded_thread: str | None) -> str:
    lines = [
        "| date | turns | tokens | API requests | score |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['date']} | {row['turns']} | {row['tokens']:,} | "
            f"{row['api_requests']} | {row['score']:.1f} |"
        )
    total_turns = sum(row["turns"] for row in rows)
    total_tokens = sum(row["tokens"] for row in rows)
    total_api = sum(row["api_requests"] for row in rows)
    total_score = intensity_score(total_turns, total_tokens, total_api)
    lines.extend(
        [
            "",
            f"Total: {total_turns} turns, {total_tokens:,} tokens, {total_api} API requests.",
            f"Aggregate score if treated as one period: {total_score:.1f}.",
        ]
    )
    if excluded_thread:
        lines.append(f"Excluded current review thread: {excluded_thread}.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=default_codex_home())
    parser.add_argument("--date", help="Anchor local date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--days", type=int, default=1, help="Number of days ending at --date.")
    parser.add_argument("--tz-offset", type=parse_tz_offset, default=parse_tz_offset("+08:00"))
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument(
        "--include-current-thread",
        action="store_true",
        help="Count the latest/current Codex thread instead of excluding it.",
    )
    args = parser.parse_args()

    if args.days < 1:
        raise SystemExit("--days must be at least 1.")

    if args.date:
        anchor_day = dt.date.fromisoformat(args.date)
    else:
        anchor_day = (dt.datetime.now(dt.timezone.utc) + args.tz_offset).date()

    first_day = anchor_day - dt.timedelta(days=args.days - 1)
    day_objects = [first_day + dt.timedelta(days=i) for i in range(args.days)]
    day_strings = [day.isoformat() for day in day_objects]
    start_ts = local_day_to_utc_ts(first_day, args.tz_offset)
    end_ts = local_day_to_utc_ts(anchor_day + dt.timedelta(days=1), args.tz_offset)

    con = connect_logs_db(args.codex_home)
    excluded_thread = None
    if not args.include_current_thread:
        excluded_thread = detect_latest_thread(con, start_ts, end_ts)

    turns = collect_turns(con, start_ts, end_ts, args.tz_offset, excluded_thread)
    api_counts = collect_api_requests(con, start_ts, end_ts, args.tz_offset, excluded_thread)
    rows = summarize(turns, api_counts, day_strings)

    if args.format == "json":
        print(
            json.dumps(
                {
                    "formula": "7.2*ln(1+turns)+6.0*ln(1+tokens/10000)+2.4*ln(1+api_requests)",
                    "timezone_offset": "+08:00",
                    "excluded_thread": excluded_thread,
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_markdown(rows, excluded_thread))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
