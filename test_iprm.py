import unittest
from datetime import date

import iprm_core as core
import iprm_cli

TODAY = date(2026, 10, 16)
CFG = core.load_config(None)


def inv(**kw):
    base = dict(invoice_id="X", customer="C", email="a@b.test", phone="", amount=100.0, currency="USD",
                issue_date=date(2026, 9, 1), due_date=date(2026, 10, 1), status="open",
                last_reminder=None, reminders_sent=0)
    base.update(kw)
    return core.Invoice(**base)


class TestPlan(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual([core.bucket_for(n) for n in (-1, 0, 1, 7, 8, 21, 22, 45, 46)],
                         ["not due", "not due", "1-7", "1-7", "8-21", "8-21", "22-45", "22-45", "46+"])

    def test_friendly_send(self):
        r = core.plan([inv(due_date=date(2026, 10, 12))], TODAY, CFG)[0]
        self.assertEqual((r.action, r.tier), ("send", "friendly"))
        self.assertIn("X", r.message)

    def test_firm_and_final(self):
        self.assertEqual(core.plan([inv(due_date=date(2026, 10, 3))], TODAY, CFG)[0].tier, "firm")
        self.assertEqual(core.plan([inv(due_date=date(2026, 9, 10))], TODAY, CFG)[0].tier, "final")

    def test_cooldown_waits(self):
        r = core.plan([inv(last_reminder=date(2026, 10, 14), reminders_sent=1)], TODAY, CFG)[0]
        self.assertEqual(r.action, "wait")

    def test_skip_statuses(self):
        for s in ("paid", "void", "disputed"):
            self.assertEqual(core.plan([inv(status=s)], TODAY, CFG)[0].action, "skip")

    def test_high_value_owner_call(self):
        self.assertEqual(core.plan([inv(amount=9000)], TODAY, CFG)[0].action, "owner_call")

    def test_max_reminders_owner_call(self):
        self.assertEqual(core.plan([inv(reminders_sent=4)], TODAY, CFG)[0].action, "owner_call")

    def test_pre_due(self):
        r = core.plan([inv(due_date=date(2026, 10, 19))], TODAY, CFG)[0]
        self.assertEqual((r.action, r.tier), ("send", "pre_due"))

    def test_no_contact(self):
        r = core.plan([inv(email="")], TODAY, CFG)[0]
        self.assertEqual(r.action, "owner_call")

    def test_aging_excludes_paid(self):
        ag = core.aging([inv(), inv(status="paid")], TODAY, CFG)
        self.assertEqual(sum(v["count"] for v in ag.values()), 1)


class TestCli(unittest.TestCase):
    def test_demo_runs(self):
        self.assertEqual(iprm_cli.main(["demo"]), 0)

    def test_missing_file(self):
        self.assertEqual(iprm_cli.main(["plan", "nope.csv"]), 2)

    def test_example_csv_loads(self):
        self.assertEqual(len(core.load_invoices("example_invoices.csv")), 10)


if __name__ == "__main__":
    unittest.main()
