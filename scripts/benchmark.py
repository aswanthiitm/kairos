#!/usr/bin/env python3
"""
Scale evidence.

The prototype ships a ~45k-row estate. That says nothing about behaviour at
enterprise volume, so this replays the same pipeline against progressively
larger synthetic order books and prints the curve. It is deliberately a
measurement, not a claim: run it and read the numbers.

    python scripts/benchmark.py --rows 45000 450000 4500000
"""
import argparse, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "warehouse.duckdb")
BENCH = os.path.join(ROOT, "runtime", "bench.duckdb")


def build(scale: int) -> int:
    """Fan the real order book out by `scale`, keeping its distributions."""
    os.makedirs(os.path.dirname(BENCH), exist_ok=True)
    if os.path.exists(BENCH):
        os.remove(BENCH)
    con = duckdb.connect(BENCH)
    con.execute("ATTACH '%s' AS src (READ_ONLY)" % DB)
    con.execute("CREATE TABLE orders AS SELECT * FROM src.orders WHERE 1=0")
    # column-agnostic: the schema evolves, the benchmark should not need editing
    for i in range(scale):
        con.execute("INSERT INTO orders SELECT * REPLACE (order_id || '-%d' AS order_id) "
                    "FROM src.orders" % i)
    con.execute("CREATE INDEX idx_od ON orders(order_date)")
    n = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    con.close()
    return n


def timed(fn, reps=3):
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); fn(); ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return ts[len(ts) // 2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", type=int, nargs="+", default=[1, 10, 100])
    a = ap.parse_args()

    print("%-14s %-12s %-14s %-14s %-14s" %
          ("rows", "build s", "daily agg ms", "lattice ms", "cohort DiD ms"))
    print("-" * 72)
    for sc in a.scales:
        t0 = time.perf_counter()
        n = build(sc)
        build_s = time.perf_counter() - t0
        con = duckdb.connect(BENCH, read_only=True)
        agg = timed(lambda: con.execute(
            "SELECT order_date, SUM(net_revenue) FROM orders WHERE region='North' "
            "GROUP BY 1").fetchall())
        lattice = timed(lambda: con.execute(
            "SELECT warehouse_id, segment, channel, tier, SUM(net_revenue), SUM(units) "
            "FROM orders WHERE order_date BETWEEN DATE '2026-08-17' AND DATE '2026-08-30' "
            "GROUP BY 1,2,3,4").fetchall())
        did = timed(lambda: con.execute(
            "SELECT order_date, CASE WHEN warehouse_id='WH-3' THEN 't' ELSE 'c' END g, "
            "SUM(net_revenue) FROM orders WHERE region='North' AND order_date >= "
            "DATE '2026-07-19' GROUP BY 1,2").fetchall())
        con.close()
        print("%-14s %-12.1f %-14.1f %-14.1f %-14.1f"
              % ("{:,}".format(n), build_s, agg, lattice, did))
    print("\nThe three queries above are the ones SIFT, SPLIT and the counterfactual "
          "actually run.\nEverything else in a run is bounded by the segment and window, "
          "not by estate size.")
    if os.path.exists(BENCH):
        os.remove(BENCH)


if __name__ == "__main__":
    main()
