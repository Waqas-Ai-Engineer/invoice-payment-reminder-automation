"""Core logic for invoice-payment-reminder-automation (iprm).

Turns an invoice CSV into a prioritised payment-reminder plan: aging buckets,
escalation tier, channel, and a rendered message. Pure standard library.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG: Dict[str, Any] = {
    "business_name": "Your Business",
    "pay_link": "https://example.com/pay",
    "pre_due_days": 3,          # gentle heads-up this many days before due date
    "cooldown_days": 5,         # min days between reminders to the same invoice
    "max_reminders": 4,         # after this, escalate to a human call
    "tiers": [                  # days overdue (>=) -> tier name
        {"min_days": 1, "name": "friendly"},
        {"min_days": 8, "name": "firm"},
        {"min_days": 22, "name": "final"},
        {"min_days": 46, "name": "call_owner"},
    ],
    "high_value_threshold": 5000,   # amounts >= this skip straight to owner review
    "skip_statuses": ["paid", "void", "disputed"],
}

BUCKETS = [("not due", None), ("1-7", 7), ("8-21", 21), ("22-45", 45), ("46+", None)]


@dataclass
class Invoice:
    """One invoice row from the CSV."""
    invoice_id: str
    customer: str
    email: str
    phone: str
    amount: float
    currency: str
    issue_date: date
    due_date: date
    status: str
    last_reminder: Optional[date]
    reminders_sent: int


@dataclass
class PlanRow:
    """One decision: what to do about one invoice today."""
    invoice_id: str
    customer: str
    amount: float
    currency: str
    days_overdue: int
    bucket: str
    tier: str
    action: str       # send | wait | skip | owner_call
    channel: str      # email | sms | none | phone
    reason: str
    message: str = ""
    priority: float = 0.0


def _d(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_config(path: Optional[str]) -> Dict[str, Any]:
    """Load a JSON config, falling back to defaults for missing keys."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path:
        with open(path, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    return cfg


def load_invoices(path: str) -> List[Invoice]:
    """Read invoices from CSV. Required: invoice_id, customer, amount, due_date."""
    rows: List[Invoice] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = {"invoice_id", "customer", "amount", "due_date"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError("CSV is missing required columns: " + ", ".join(sorted(missing)))
        for n, r in enumerate(reader, start=2):
            try:
                rows.append(Invoice(
                    invoice_id=r["invoice_id"].strip(),
                    customer=r["customer"].strip(),
                    email=(r.get("email") or "").strip(),
                    phone=(r.get("phone") or "").strip(),
                    amount=float(r["amount"]),
                    currency=(r.get("currency") or "USD").strip() or "USD",
                    issue_date=_d(r.get("issue_date", "")) or _d(r["due_date"]),  # type: ignore
                    due_date=_d(r["due_date"]),  # type: ignore
                    status=(r.get("status") or "open").strip().lower(),
                    last_reminder=_d(r.get("last_reminder", "")),
                    reminders_sent=int(r.get("reminders_sent") or 0),
                ))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"row {n}: {exc}") from exc
    return rows


def bucket_for(days_overdue: int) -> str:
    """Aging bucket label for a number of days overdue."""
    if days_overdue <= 0:
        return "not due"
    if days_overdue <= 7:
        return "1-7"
    if days_overdue <= 21:
        return "8-21"
    if days_overdue <= 45:
        return "22-45"
    return "46+"


def tier_for(days_overdue: int, cfg: Dict[str, Any]) -> str:
    """Escalation tier name for days overdue (empty if not overdue)."""
    name = ""
    for t in sorted(cfg["tiers"], key=lambda x: x["min_days"]):
        if days_overdue >= t["min_days"]:
            name = t["name"]
    return name


def money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


def render_message(inv: Invoice, tier: str, days: int, cfg: Dict[str, Any]) -> str:
    """Render the reminder text for an invoice and tier."""
    amt = money(inv.amount, inv.currency)
    biz, link = cfg["business_name"], cfg["pay_link"]
    if tier == "pre_due":
        return (f"Hi {inv.customer}, a friendly heads-up from {biz}: invoice {inv.invoice_id} "
                f"({amt}) is due on {inv.due_date.isoformat()}. Pay online: {link}")
    if tier == "friendly":
        return (f"Hi {inv.customer}, quick reminder from {biz}: invoice {inv.invoice_id} ({amt}) "
                f"was due {inv.due_date.isoformat()}. If it's already paid, thank you! Otherwise: {link}")
    if tier == "firm":
        return (f"Hello {inv.customer}, invoice {inv.invoice_id} ({amt}) is now {days} days overdue. "
                f"Please arrange payment this week or reply if there is an issue: {link}")
    if tier == "final":
        return (f"Final notice from {biz}: invoice {inv.invoice_id} ({amt}) is {days} days overdue. "
                f"Please pay by return or contact us today to agree a plan: {link}")
    return ""


def plan(invoices: List[Invoice], today: date, cfg: Dict[str, Any]) -> List[PlanRow]:
    """Decide today's action for every invoice, highest priority first."""
    out: List[PlanRow] = []
    for inv in invoices:
        days = (today - inv.due_date).days
        bucket = bucket_for(days)
        base = dict(invoice_id=inv.invoice_id, customer=inv.customer, amount=inv.amount,
                    currency=inv.currency, days_overdue=max(days, 0), bucket=bucket)
        if inv.status in cfg["skip_statuses"]:
            out.append(PlanRow(**base, tier="", action="skip", channel="none",
                               reason=f"status is {inv.status}"))
            continue
        channel = "email" if inv.email else ("sms" if inv.phone else "none")
        if days < 0:
            if -days == cfg["pre_due_days"] and inv.reminders_sent == 0 and channel != "none":
                out.append(PlanRow(**base, tier="pre_due", action="send", channel=channel,
                                   reason="due soon heads-up",
                                   message=render_message(inv, "pre_due", days, cfg),
                                   priority=inv.amount * 0.1))
            else:
                out.append(PlanRow(**base, tier="", action="wait", channel="none",
                                   reason=f"due in {-days} days"))
            continue
        tier = tier_for(days, cfg) or "friendly"
        owner = (tier == "call_owner" or inv.amount >= cfg["high_value_threshold"]
                 or inv.reminders_sent >= cfg["max_reminders"])
        if owner:
            why = ("high value" if inv.amount >= cfg["high_value_threshold"]
                   else "max reminders reached" if inv.reminders_sent >= cfg["max_reminders"]
                   else "over 45 days overdue")
            out.append(PlanRow(**base, tier="call_owner", action="owner_call", channel="phone",
                               reason=f"{why}: needs a personal call",
                               priority=inv.amount * (1 + days / 30)))
            continue
        if inv.last_reminder and (today - inv.last_reminder).days < cfg["cooldown_days"]:
            out.append(PlanRow(**base, tier=tier, action="wait", channel="none",
                               reason=f"reminded {(today - inv.last_reminder).days}d ago (cooldown {cfg['cooldown_days']}d)"))
            continue
        if channel == "none":
            out.append(PlanRow(**base, tier=tier, action="owner_call", channel="phone",
                               reason="no email or phone on file", priority=inv.amount))
            continue
        out.append(PlanRow(**base, tier=tier, action="send", channel=channel,
                           reason=f"{days} days overdue -> {tier} reminder",
                           message=render_message(inv, tier, days, cfg),
                           priority=inv.amount * (1 + days / 30)))
    out.sort(key=lambda r: (-r.priority, r.invoice_id))
    return out


def aging(invoices: List[Invoice], today: date, cfg: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """Outstanding total and count per aging bucket (skipped statuses excluded)."""
    res = {b: {"count": 0, "total": 0.0} for b, _ in BUCKETS}
    for inv in invoices:
        if inv.status in cfg["skip_statuses"]:
            continue
        b = bucket_for((today - inv.due_date).days)
        res[b]["count"] += 1
        res[b]["total"] += inv.amount
    return res
