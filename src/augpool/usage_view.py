"""Terminal presentation for account-level credit and local session usage."""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from augpool.pool import Pool
from augpool.select import rank_accounts
from augpool.state import State


_RESET = "\x1b[0m"
_BOLD = "1"
_DIM = "2"
_CYAN = "36"
_GREEN = "32"
_YELLOW = "33"
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
_SESSION_TIMELINE_DAYS = 30


def format_compact(value: float | int | None) -> str:
    """Format a count with one compact decimal and a lowercase unit."""
    if value is None:
        return "—"
    number = float(value)
    magnitude = abs(number)
    for threshold, suffix in (
        (1_000_000_000, "b"),
        (1_000_000, "m"),
        (1_000, "k"),
    ):
        if magnitude >= threshold:
            short = f"{number / threshold:.1f}".rstrip("0").rstrip(".")
            return f"{short}{suffix}"
    if number.is_integer():
        return f"{int(number)}"
    return f"{number:.1f}".rstrip("0").rstrip(".")


def format_relative_time(timestamp: float | None, *, now: float | None = None) -> str:
    """Describe a timestamp as a short, stable relative age."""
    if timestamp is None:
        return "never"
    now = time.time() if now is None else now
    age = max(0, int(now - float(timestamp)))
    if age < 60:
        return "just now"
    if age < 3_600:
        return f"{age // 60}m ago"
    if age < 86_400:
        return f"{age // 3_600}h ago"
    return f"{age // 86_400}d ago"


def format_sparkline(values: list[int]) -> str:
    """Render non-negative counts with visible zero days and a relative peak."""
    counts = [max(0, int(value)) for value in values]
    peak = max(counts, default=0)
    if peak == 0:
        return "·" * len(counts)
    points: list[str] = []
    for count in counts:
        if count == 0:
            points.append("·")
            continue
        level = math.ceil((count / peak) * len(_SPARK_BLOCKS)) - 1
        points.append(_SPARK_BLOCKS[level])
    return "".join(points)


def _paint(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\x1b[{code}m{text}{_RESET}"


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun if count == 1 else noun + 's'}"


def _weight_label(weight: float) -> str:
    number = f"{weight:.2f}".rstrip("0").rstrip(".")
    return f"weight {number}×"


def _status_label(row: dict[str, Any], now: float) -> str:
    if not row["enabled"]:
        return "disabled"
    if row["in_cooldown"]:
        remaining = max(0, float(row["cooldown_until"] or now) - now)
        minutes = max(1, int(remaining // 60))
        return f"cooldown {minutes}m"
    if row["active"]:
        return "active"
    return "ready"


def usage_report_payload(
    pool: Pool,
    state: State,
    usage: dict[str, float] | None,
    cache: dict[str, Any] | None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Build serializable account usage data shared by JSON and terminal views."""
    now = time.time() if now is None else now
    ranked = rank_accounts(pool, state, usage, now=now)
    ranked_by_email = {item.account.email: item for item in ranked}
    ordered_accounts = [item.account for item in ranked]
    ordered_accounts.extend(account for account in pool.accounts if not account.enabled)

    end_day = datetime.fromtimestamp(now, timezone.utc).date()
    start_day = end_day - timedelta(days=_SESSION_TIMELINE_DAYS - 1)
    tracked_by_account = {account.email: 0 for account in pool.accounts}
    history_days: list[dict[str, Any]] = []
    for offset in range(_SESSION_TIMELINE_DAYS):
        day_key = (start_day + timedelta(days=offset)).isoformat()
        by_account: dict[str, int] = {}
        for account in pool.accounts:
            count = state.for_account(account.email).sessions_by_day.get(day_key, 0)
            if count > 0:
                by_account[account.email] = count
                tracked_by_account[account.email] += count
        history_days.append(
            {
                "date": day_key,
                "sessions": sum(by_account.values()),
                "accounts": by_account,
            }
        )

    rows: list[dict[str, Any]] = []
    analytics_available = usage is not None
    for account in ordered_accounts:
        account_state = state.for_account(account.email)
        ranked_item = ranked_by_email.get(account.email)
        credits = None
        if usage is not None and account.email in usage:
            credits = float(usage[account.email])
        in_cooldown = bool(
            account.enabled
            and account_state.cooldown_until is not None
            and account_state.cooldown_until > now
        )
        rows.append(
            {
                "email": account.email,
                "label": account.label,
                "notes": account.notes,
                "enabled": account.enabled,
                "active": account.email == pool.active_email,
                "weight": account.weight,
                "score": ranked_item.score if ranked_item is not None else None,
                "credits_consumed": credits,
                "credit_share": None,
                "local_sessions": account_state.local_uses,
                "tracked_sessions_30d": tracked_by_account.get(account.email, 0),
                "last_selected_at": account_state.last_selected_at,
                "source": (
                    "analytics"
                    if credits is not None
                    else "local"
                    if account_state.local_uses
                    else "unknown"
                ),
                "in_cooldown": in_cooldown,
                "cooldown_until": account_state.cooldown_until,
            }
        )

    total_credits: float | None
    if analytics_available:
        total_credits = sum(float(row["credits_consumed"] or 0) for row in rows)
        if total_credits > 0:
            for row in rows:
                if row["credits_consumed"] is not None:
                    row["credit_share"] = row["credits_consumed"] / total_credits
    else:
        total_credits = None

    fetched_at = (cache or {}).get("fetched_at")
    age_seconds = None
    if fetched_at is not None:
        age_seconds = max(0, int(now - float(fetched_at)))

    return {
        "window": {
            "start_date": (cache or {}).get("start_date"),
            "end_date": (cache or {}).get("end_date"),
            "fetched_at": fetched_at,
            "age_seconds": age_seconds,
        },
        "totals": {
            "accounts": len(pool.accounts),
            "enabled_accounts": len(pool.enabled_accounts()),
            "credits_consumed": total_credits,
            "local_sessions": sum(int(row["local_sessions"]) for row in rows),
        },
        "session_history": {
            "timezone": "UTC",
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "tracked_sessions": sum(day["sessions"] for day in history_days),
            "by_day": history_days,
        },
        "accounts": rows,
        "errors": list((cache or {}).get("errors") or []),
    }


def _bar(ratio: float, width: int) -> str:
    ratio = min(1.0, max(0.0, ratio))
    filled = min(width, max(0, int(math.ceil(ratio * width))))
    return "█" * filled + "░" * (width - filled)


def _row_color(status: str) -> str:
    if status.startswith("cooldown"):
        return _YELLOW
    if status == "active":
        return _CYAN
    if status == "disabled":
        return _DIM
    return _GREEN


def render_usage_dashboard(
    pool: Pool,
    state: State,
    usage: dict[str, float] | None,
    cache: dict[str, Any] | None,
    *,
    width: int = 88,
    color: bool = False,
    now: float | None = None,
) -> str:
    """Render a Tokscale-inspired static dashboard without third-party packages."""
    now = time.time() if now is None else now
    width = max(24, int(width))
    report = usage_report_payload(pool, state, usage, cache, now=now)
    totals = report["totals"]
    rows = report["accounts"]

    title = _paint("augpool", f"{_BOLD};{_CYAN}", color) + " " + _paint(
        "usage", _BOLD, color
    )
    lines = [title]

    window = report["window"]
    if window["start_date"] and window["end_date"]:
        age = format_relative_time(window["fetched_at"], now=now)
        lines.append(
            _paint(
                _clip(
                    f"{window['start_date']} → {window['end_date']} · updated {age}",
                    width,
                ),
                _DIM,
                color,
            )
        )
    else:
        lines.append(
            _paint(_clip("current-month account balance", width), _DIM, color)
        )

    credit_total = totals["credits_consumed"]
    credits_summary = (
        f"{format_compact(credit_total)} credits"
        if credit_total is not None
        else "credits unavailable"
    )
    summary = " · ".join(
        (
            _plural(totals["accounts"], "account"),
            credits_summary,
            _plural(totals["local_sessions"], "session"),
        )
    )
    lines.extend(("", _clip(summary, width), ""))

    history = report["session_history"]
    history_title = (
        f"Sessions over time · {_SESSION_TIMELINE_DAYS}d UTC · "
        f"{history['tracked_sessions']} tracked"
    )
    lines.append(_paint(_clip(history_title, width), _BOLD, color))
    visible_days = max(1, min(len(history["by_day"]), width - 12))
    chart_days = history["by_day"][-visible_days:]
    chart_start = chart_days[0]["date"][5:]
    chart_end = chart_days[-1]["date"][5:]
    sparkline = format_sparkline([day["sessions"] for day in chart_days])
    lines.append(
        _paint(chart_start, _DIM, color)
        + " "
        + _paint(sparkline, _CYAN, color)
        + " "
        + _paint(chart_end, _DIM, color)
    )
    if history["tracked_sessions"] == 0:
        lines.append(
            _paint(
                _clip("No dated sessions yet · tracking starts after upgrade", width),
                _DIM,
                color,
            )
        )
    lines.append(_paint("─" * width, _DIM, color))

    if not rows:
        lines.extend(
            (
                "",
                _paint("No accounts yet", _BOLD, color),
                _clip("augpool import --self --email you@company.com", width),
            )
        )
        return "\n".join(lines)

    total_sessions = max(1, totals["local_sessions"])
    for index, row in enumerate(rows):
        status = _status_label(row, now)
        status_color = _row_color(status)
        marker = "●" if row["active"] else "○"
        display_name = row["email"]
        if row["label"] and row["label"] != row["email"]:
            display_name = f"{row['label']} · {row['email']}"

        if row["credits_consumed"] is not None:
            share = float(row["credit_share"] or 0)
            right = f"{format_compact(row['credits_consumed'])}  {share * 100:.1f}%  {status}"
        elif usage is None:
            share = int(row["local_sessions"]) / total_sessions
            right = f"{share * 100:.1f}%  {status}"
        else:
            share = 0.0
            right = f"—  {status}"

        if width < 32:
            if row["credits_consumed"] is not None:
                right = f"{format_compact(row['credits_consumed'])} {status}"
            elif usage is None:
                right = f"{row['local_sessions']}s {status}"
            else:
                right = f"— {status}"
        right = _clip(right, width - 5)

        prefix_width = 2
        name_width = max(1, width - prefix_width - 2 - len(right))
        name = _clip(display_name, name_width).ljust(name_width)
        first_line = (
            _paint(marker, status_color, color)
            + " "
            + _paint(name, _BOLD if row["enabled"] else _DIM, color)
            + "  "
            + _paint(right, status_color, color)
        )
        lines.append(first_line)

        metadata_parts = [
            _plural(int(row["local_sessions"]), "session"),
            f"used {format_relative_time(row['last_selected_at'], now=now)}",
        ]
        if width >= 72:
            metadata_parts.append(row["source"])
        if row["weight"] != 1.0:
            metadata_parts.append(_weight_label(float(row["weight"])))
        metadata = " · ".join(metadata_parts)
        available = width - 4 - len(metadata)
        if available >= 6:
            usage_bar = _bar(share, available)
            second_line = (
                "  "
                + _paint(usage_bar, status_color if row["enabled"] else _DIM, color)
                + "  "
                + _paint(metadata, _DIM, color)
            )
        else:
            second_line = "  " + _paint(_clip(metadata, width - 2), _DIM, color)
        lines.append(second_line)
        if index != len(rows) - 1:
            lines.append("")

    lines.append(_paint("─" * width, _DIM, color))
    if usage is None:
        lines.append(
            _clip("Usage bars use local sessions · run augpool refresh for Analytics", width)
        )
    else:
        lines.append(
            _paint(
                _clip("Credits from Analytics · lower usage ranks first", width),
                _DIM,
                color,
            )
        )
    lines.append(
        _paint(
            _clip("Sessions are local account selections (run/use events)", width),
            _DIM,
            color,
        )
    )
    for error in report["errors"]:
        lines.append(_paint(_clip(f"warning: {error}", width), _YELLOW, color))
    return "\n".join(lines)
