from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path


FIELDS = [
    "order_id",
    "supplier_id",
    "customer_id",
    "sku",
    "plant",
    "order_date",
    "promised_date",
    "current_eta",
    "ordered_qty",
    "confirmed_qty",
    "received_qty",
    "on_hand",
    "allocated",
    "safety_stock",
    "unit_price",
    "contract_price",
    "currency",
    "customer_priority",
    "supplier_risk",
    "order_status",
    "expected_exception",
]

SCENARIOS = [
    "NO_EXCEPTION",
    "NO_EXCEPTION",
    "NO_EXCEPTION",
    "LATE_SHIPMENT",
    "INVENTORY_SHORTAGE",
    "QUANTITY_SHORTFALL",
    "PRICE_MISMATCH",
    "DATA_QUALITY",
]


def generate_orders(count: int = 200, seed: int = 42, as_of: date | None = None) -> list[dict]:
    """Generate repeatable ERP-like purchase/order-line records.

    Each exception scenario changes only the fields required to trigger its label,
    which makes the dataset useful as a ground-truth evaluation set.
    """
    rng = random.Random(seed)
    as_of = as_of or date(2026, 9, 11)
    priorities = ["STANDARD", "STANDARD", "HIGH", "CRITICAL"]
    risks = ["LOW", "LOW", "MEDIUM", "HIGH"]
    plants = ["PHX", "DAL", "ATL", "LAX"]
    statuses = ["OPEN", "CONFIRMED", "IN_TRANSIT"]
    rows: list[dict] = []

    for i in range(1, count + 1):
        scenario = SCENARIOS[(i - 1) % len(SCENARIOS)]
        ordered = rng.choice([20, 40, 60, 100, 150, 250, 500])
        order_date = as_of - timedelta(days=rng.randint(3, 35))
        promised_date = as_of + timedelta(days=rng.randint(1, 14))
        contract_price = round(rng.uniform(8, 240), 2)
        row = {
            "order_id": f"PO-{10000 + i}",
            "supplier_id": f"SUP-{rng.randint(1, 20):03d}",
            "customer_id": f"CUS-{rng.randint(1, 50):03d}",
            "sku": f"SKU-{rng.randint(1, 80):04d}",
            "plant": rng.choice(plants),
            "order_date": order_date.isoformat(),
            "promised_date": promised_date.isoformat(),
            "current_eta": (promised_date - timedelta(days=rng.randint(0, 3))).isoformat(),
            "ordered_qty": ordered,
            "confirmed_qty": ordered,
            "received_qty": 0,
            "on_hand": ordered + rng.randint(25, 250),
            "allocated": rng.randint(0, 20),
            "safety_stock": rng.randint(5, 20),
            "unit_price": contract_price,
            "contract_price": contract_price,
            "currency": "USD",
            "customer_priority": rng.choice(priorities),
            "supplier_risk": rng.choice(risks),
            "order_status": rng.choice(statuses),
            "expected_exception": scenario,
        }

        if scenario == "LATE_SHIPMENT":
            row["current_eta"] = (promised_date + timedelta(days=rng.randint(2, 12))).isoformat()
        elif scenario == "INVENTORY_SHORTAGE":
            row["on_hand"] = rng.randint(0, max(1, ordered // 3))
            row["allocated"] = rng.randint(0, int(row["on_hand"]))
            row["safety_stock"] = rng.randint(5, 20)
        elif scenario == "QUANTITY_SHORTFALL":
            row["confirmed_qty"] = max(1, ordered - rng.randint(1, max(2, ordered // 2)))
        elif scenario == "PRICE_MISMATCH":
            row["unit_price"] = round(contract_price * rng.uniform(1.04, 1.18), 2)
        elif scenario == "DATA_QUALITY":
            row[rng.choice(["supplier_id", "sku", "current_eta"])] = ""

        rows.append(row)
    return rows


def write_csv(rows: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic ERP orders")
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("data/synthetic_orders.csv"))
    args = parser.parse_args()
    rows = generate_orders(args.count, args.seed)
    write_csv(rows, args.output)
    print(f"Wrote {len(rows)} orders to {args.output}")


if __name__ == "__main__":
    main()

