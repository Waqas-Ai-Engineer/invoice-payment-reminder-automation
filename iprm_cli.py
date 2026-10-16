"""Command-line interface for iprm."""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from dataclasses import asdict
from datetime import date, datetime
from typing import List, Optional

import iprm_core as core

DEMO_TODAY = date(2026, 10, 16)


def _fmt_table(rows: List[core.PlanRow]) -> str:
    head = f"{'INVOICE':<9}{'CUSTOMER':<22}{'AMOUNT':>12} {'DAYS':>5}  {'ACTION':<17}{'TIER':<12}REASON"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r.invoice_id:<9}{r.customer[:21]:<22}{r.amount:>12,.2f} {r.days_overdue:>5}  "
                     f"{r.action + '/' + r.channel:<17}{r.tier:<12}{r.reason}")
    return "\n".join(lines)


def _fmt_aging(ag: dict) -> str:
    lines = ["AGING REPORT (outstanding, excludes paid/void/disputed)", f"{'BUCKET':<10}{'COUNT':>6}{'TOTAL':>14}"]
    for b, v in ag.items():
        lines.append(f"{b:<10}{v['count']:>6}{v['total']:>14,.2f}")
    od = sum(v["total"] for b, v in ag.items() if b != "not due")
    lines.append(f"{'OVERDUE':<10}{'':>6}{od:>14,.2f}")
    return "\n".join(lines)


def _fmt_csv(rows: List[core.PlanRow]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["invoice_id", "customer", "amount", "currency", "days_overdue", "action", "channel", "tier", "reason", "message"])
    for r in rows:
        w.writerow([r.invoice_id, r.customer, r.amount, r.currency, r.days_overdue, r.action, r.channel, r.tier, r.reason, r.message])
    return buf.getvalue()


def _run(csv_path: str, cfg_path: Optional[str], today: date, fmt: str, messages: bool) -> str:
    cfg = core.load_config(cfg_path)
    invs = core.load_invoices(csv_path)
    rows = core.plan(invs, today, cfg)
    if fmt == "json":
        return json.dumps([asdict(r) for r in rows], indent=2)
    if fmt == "csv":
        return _fmt_csv(rows)
    out = [f"Payment reminder plan for {today.isoformat()}", "", _fmt_table(rows), "", _fmt_aging(core.aging(invs, today, cfg))]
    send = [r for r in rows if r.action == "send"]
    calls = [r for r in rows if r.action == "owner_call"]
    out += ["", f"{len(send)} reminders to send, {len(calls)} needs a personal call, "
            f"{sum(r.amount for r in send + calls):,.2f} chased today."]
    if messages and send:
        out += ["", "MESSAGES"]
        out += [f"[{r.invoice_id} via {r.channel}] {r.message}" for r in send]
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="iprm.py", description="Invoice payment reminder planner: CSV in, escalating reminder plan out. Never sends anything.")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="run on bundled sample data (offline)")
    d.add_argument("--messages", action="store_true", help="also print rendered messages")
    for name, h in (("plan", "build today's reminder plan"), ("aging", "print the aging report")):
        s = sub.add_parser(name, help=h)
        s.add_argument("csv", help="invoice CSV file")
        s.add_argument("--config", help="JSON config (see example_config.json)")
        s.add_argument("--today", help="override today's date, YYYY-MM-DD")
        if name == "plan":
            s.add_argument("--format", choices=["table", "json", "csv"], default="table")
            s.add_argument("--messages", action="store_true", help="print rendered messages")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "demo":
            print(_run("example_invoices.csv", "example_config.json", DEMO_TODAY, "table", args.messages))
            print("\n(Sample data only. Nothing was sent.)")
            return 0
        today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
        if args.cmd == "aging":
            cfg = core.load_config(args.config)
            print(_fmt_aging(core.aging(core.load_invoices(args.csv), today, cfg)))
            return 0
        print(_run(args.csv, args.config, today, args.format, args.messages))
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
