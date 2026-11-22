"""Backend/API scenarios for the Engineering Awareness Benchmark.

Two small Python services: retry idempotency and concurrent withdrawals. The
fixtures pass on the happy path; the hidden defects are the retry and race
assumptions a careful engineer must state and fix.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from pathlib import Path

ORDERS_SOURCE = '''ORDERS = []


def create_order(order_id, amount):
    ORDERS.append({"id": order_id, "amount": amount})
    return ORDERS[-1]
'''

ORDERS_TEST = '''import unittest
from orders import ORDERS, create_order


class OrdersTest(unittest.TestCase):
    def test_create(self):
        ORDERS.clear()
        order = create_order("a", 10)
        self.assertEqual(order["id"], "a")
        self.assertEqual(len(ORDERS), 1)


if __name__ == "__main__":
    unittest.main()
'''

ORDERS_GOOD = '''import threading

ORDERS = {}
_LOCK = threading.Lock()


def create_order(order_id, amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    with _LOCK:
        if order_id in ORDERS:
            return ORDERS[order_id]
        ORDERS[order_id] = {"id": order_id, "amount": amount}
        return ORDERS[order_id]
'''

BALANCE_SOURCE = '''BALANCE = {"amount": 100}


def withdraw(amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    BALANCE["amount"] -= amount
    return True
'''

BALANCE_TEST = '''import unittest
from balance import BALANCE, withdraw


class BalanceTest(unittest.TestCase):
    def test_withdraw(self):
        BALANCE["amount"] = 100
        self.assertTrue(withdraw(10))
        self.assertEqual(BALANCE["amount"], 90)


if __name__ == "__main__":
    unittest.main()
'''

BALANCE_GOOD = '''import threading

BALANCE = {"amount": 100}
_LOCK = threading.Lock()


def withdraw(amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    with _LOCK:
        if BALANCE["amount"] >= amount:
            BALANCE["amount"] -= amount
            return True
        return False
'''


def _load(tree: Path, name: str):
    path = tree / (name + ".py")
    spec = importlib.util.spec_from_file_location(f"bench_{name}_{abs(hash(str(tree)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _visible_tests(tree: Path) -> list:
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                          cwd=str(tree), capture_output=True, text=True, timeout=120)
    return [{"name": "visible tests", "ok": proc.returncode == 0,
             "detail": (proc.stderr or proc.stdout).strip()[-400:]}]


def _run_threads(target, count=20):
    errors, results = [], []
    def worker():
        try:
            results.append(target())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors


def _check_orders(tree: Path) -> list:
    checks = []
    module = _load(tree, "orders")
    module.ORDERS.clear()
    module.create_order("a", 10)
    module.create_order("a", 10)
    checks.append({"name": "the same order id creates one order", "ok": len(module.ORDERS) == 1,
                   "detail": f"orders={len(module.ORDERS)}"})
    module.ORDERS.clear()
    _, errors = _run_threads(lambda: module.create_order("b", 10))
    checks.append({"name": "concurrent retries create one order", "ok":
                   not errors and len(module.ORDERS) == 1,
                   "detail": f"orders={len(module.ORDERS)} errors={errors[:2]}"})
    module.ORDERS.clear()
    module.create_order("c", 10)
    module.create_order("d", 10)
    checks.append({"name": "different orders are kept", "ok": len(module.ORDERS) == 2,
                   "detail": f"orders={len(module.ORDERS)}"})
    try:
        module.create_order("e", -1)
        invalid_ok = False
    except ValueError:
        invalid_ok = True
    except Exception:
        invalid_ok = False
    checks.append({"name": "invalid amount is rejected", "ok": invalid_ok,
                   "detail": "a non-positive amount must not create an order"})
    return checks


def _check_balance(tree: Path) -> list:
    checks = []
    module = _load(tree, "balance")
    module.BALANCE["amount"] = 100
    results, errors = _run_threads(lambda: module.withdraw(10))
    checks.append({"name": "concurrent withdrawals do not overdraw", "ok":
                   not errors and module.BALANCE["amount"] == 0 and sum(1 for r in results if r) == 10,
                   "detail": f"balance={module.BALANCE['amount']} successes={sum(1 for r in results if r)}"})
    module.BALANCE["amount"] = 100
    checks.append({"name": "insufficient funds are refused", "ok": module.withdraw(200) is False,
                   "detail": "a withdrawal above the balance must return False"})
    module.BALANCE["amount"] = 100
    try:
        module.withdraw(-1)
        invalid_ok = False
    except ValueError:
        invalid_ok = True
    except Exception:
        invalid_ok = False
    checks.append({"name": "negative withdrawal is rejected", "ok": invalid_ok,
                   "detail": "a negative amount must raise ValueError"})
    return checks


ORDERS_GOOD_AGENT = {
    "findings": [
        {"severity": "major", "claim": "The client retries POST /orders; the same order id must be idempotent", "evidence": ["orders.py:1"]},
        {"severity": "major", "claim": "Concurrent retries race on the list; the check-and-insert must be atomic", "evidence": ["orders.py:1"]},
        {"severity": "major", "claim": "A non-positive amount must be rejected", "evidence": ["orders.py:1"]},
    ],
    "decision": {
        "approach": "Deduplicate by order id under a lock and validate the amount before insert.",
        "alternatives": ["Return the existing order on retry", "Reject duplicate ids with 409"],
        "assumptions": ["order_id is the client idempotency key"],
        "why": "A timeout retry is the same logical order; a list append duplicates it and a read-then-write races.",
    },
    "self_correction": [{"wrong": "First added a duplicate check outside the lock", "detected_by": "Ran concurrent creates", "fix": "Moved the check and insert under one lock", "evidence": ["orders.py"]}],
    "scope": {"changed_files": ["orders.py"], "not_changed": ["tests/test_orders.py"]},
    "closure": {"tests": ["python -m unittest discover -s tests"], "residual_risks": []},
}

ORDERS_BAD_AGENT = {
    "findings": [{"severity": "minor", "claim": "Added a check", "evidence": ["orders.py"]}],
    "decision": {"approach": "Checked the id", "alternatives": [], "assumptions": [], "why": ""},
    "self_correction": [],
    "scope": {"changed_files": ["orders.py"], "not_changed": []},
    "closure": {"tests": [], "residual_risks": []},
}

BALANCE_GOOD_AGENT = {
    "findings": [
        {"severity": "major", "claim": "The read-modify-write on the balance races and can overdraw the balance", "evidence": ["balance.py:1"]},
        {"severity": "major", "claim": "The check and decrement must be one atomic critical section", "evidence": ["balance.py:1"]},
        {"severity": "major", "claim": "A negative amount must be rejected", "evidence": ["balance.py:1"]},
    ],
    "decision": {
        "approach": "Validate the amount and perform the check and decrement under one lock.",
        "alternatives": ["Use a database conditional update", "Use an atomic compare-and-swap"],
        "assumptions": ["the in-process lock is sufficient for this fixture"],
        "why": "Two requests can both see enough balance before either decrements; the lock makes the check and update atomic and prevents an overdraft.",
    },
    "self_correction": [{"wrong": "First added a balance check before the lock", "detected_by": "Ran 20 concurrent withdrawals", "fix": "Moved the check inside the lock", "evidence": ["balance.py"]}],
    "scope": {"changed_files": ["balance.py"], "not_changed": ["tests/test_balance.py"]},
    "closure": {"tests": ["python -m unittest discover -s tests"], "residual_risks": []},
}

BALANCE_BAD_AGENT = {
    "findings": [{"severity": "minor", "claim": "Added a check", "evidence": ["balance.py"]}],
    "decision": {"approach": "Checked the balance", "alternatives": [], "assumptions": [], "why": ""},
    "self_correction": [],
    "scope": {"changed_files": ["balance.py"], "not_changed": []},
    "closure": {"tests": [], "residual_risks": []},
}

SCENARIOS = {
    "api-idempotency": {
        "id": "api-idempotency", "domain": "backend",
        "task": "The mobile client retries POST /orders when a request times out. Make order creation safe to retry.",
        "fixture": {
            "orders.py": ORDERS_SOURCE,
            "tests/test_orders.py": ORDERS_TEST,
            "README.md": "# Orders service\n\nPOST /orders is retried by the mobile client on timeout.\n",
            "pyproject.toml": '[project]\nname = "acme-orders"\nversion = "0.1.0"\n',
        },
        "blind_spots": [
            {"id": "idempotency", "weight": 1, "signals": ["idempot", "retry", "duplicate", "same id", "dedupe"], "evidence": ["orders.py"], "description": "A retried request is the same logical order."},
            {"id": "concurrency", "weight": 1, "signals": ["lock", "race", "thread", "atomic"], "evidence": ["orders.py"], "description": "Concurrent retries race on the check-and-insert."},
            {"id": "validation", "weight": 1, "signals": ["amount", "negative", "invalid"], "evidence": ["orders.py"], "description": "A non-positive amount must be rejected."},
        ],
        "judgment": {"signals": ["idempot", "lock", "atomic", "amount"], "min_signals": 2},
        "allowed_paths": ["orders.py", "tests/", "README.md", "pyproject.toml"],
        "forbidden_paths": [],
        "check": _check_orders,
        "regression": _visible_tests,
        "controls": {
            "good": {"files": {"orders.py": ORDERS_GOOD}, "agent": ORDERS_GOOD_AGENT},
            "bad": {"files": {"orders.py": ORDERS_SOURCE}, "agent": ORDERS_BAD_AGENT},
        },
    },
    "api-concurrency": {
        "id": "api-concurrency", "domain": "backend",
        "task": "Make withdrawals safe under concurrent requests. The balance must never go negative.",
        "fixture": {
            "balance.py": BALANCE_SOURCE,
            "tests/test_balance.py": BALANCE_TEST,
            "README.md": "# Balance service\n\nWithdrawals arrive from several concurrent requests.\n",
            "pyproject.toml": '[project]\nname = "acme-balance"\nversion = "0.1.0"\n',
        },
        "blind_spots": [
            {"id": "atomic", "weight": 1, "signals": ["lock", "atomic", "race", "critical section"], "evidence": ["balance.py"], "description": "The check and decrement must be atomic."},
            {"id": "overdraft", "weight": 1, "signals": ["overdraft", "insufficient", "negative balance"], "evidence": ["balance.py"], "description": "Concurrent withdrawals must not overdraw."},
            {"id": "validation", "weight": 1, "signals": ["negative", "zero", "invalid"], "evidence": ["balance.py"], "description": "A non-positive amount must be rejected."},
        ],
        "judgment": {"signals": ["lock", "atomic", "overdraft", "negative"], "min_signals": 2},
        "allowed_paths": ["balance.py", "tests/", "README.md", "pyproject.toml"],
        "forbidden_paths": [],
        "check": _check_balance,
        "regression": _visible_tests,
        "controls": {
            "good": {"files": {"balance.py": BALANCE_GOOD}, "agent": BALANCE_GOOD_AGENT},
            "bad": {"files": {"balance.py": BALANCE_SOURCE}, "agent": BALANCE_BAD_AGENT},
        },
    },
}
