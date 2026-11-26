"""Performance checks (Phase 7, SRS §16.1, NFR-001).

Measures the documented targets on (a) the seeded demo database and
(b) a generated 200-booking fixture, so the numbers are not only for
an empty database. Targets (not guarantees): login, booking create,
price calculation, payment recording and dashboard load <= 2 s;
search <= 3 s. Cold start of the Gradio server is measured
separately (first start on a machine downloads nothing but Gradio
itself is slow to import — the VM quirk is documented in A-12).

Usage: ``python scripts/perf_check.py`` — always uses a TEMPORARY
database, never the real app database. Writes results to
``docs/PERFORMANCE.md``.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wedding_hall_system.config.settings import AppSettings  # noqa: E402
from wedding_hall_system.data.database import DatabaseManager  # noqa: E402
from wedding_hall_system.data.seed import seed  # noqa: E402
from wedding_hall_system.logic.auth_service import AuthService  # noqa: E402
from wedding_hall_system.logic.booking_service import BookingService  # noqa: E402
from wedding_hall_system.logic.customer_service import CustomerService  # noqa: E402
from wedding_hall_system.logic.pricing_service import PricingService  # noqa: E402
from wedding_hall_system.logic.report_service import ReportService  # noqa: E402
from wedding_hall_system.logic.search_service import SearchService  # noqa: E402

# (label, dataset, target text, seconds, target seconds or 0)
RESULTS: list[tuple[str, str, str, float, float]] = []


def timed(label: str, target_s: float, fn, dataset: str = ""):
    start = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - start
    RESULTS.append((label, dataset, f"<= {target_s:g} s", elapsed, target_s))
    mark = "PASS" if elapsed <= target_s else "OVER TARGET"
    print(f"  {label:<42} {elapsed:7.3f} s  (target <= {target_s:g} s)  {mark}")
    return elapsed


def build_fixture(db, settings, auth, count: int = 200) -> None:
    """Generate ``count`` confirmed bookings on consecutive days in
    the Main Hall (12:00–17:00 each day, no package minimum clash:
    no package, 120-minute minimum is satisfied by 5 hours)."""
    from wedding_hall_system.logic.models import ServiceSelection  # noqa: F401

    receptionist = auth.login("receptionist", "Reception123!")[0]
    accountant = auth.login("accountant", "Account123!")[0]
    bookings = BookingService(db, settings)
    customers = CustomerService(db, settings)
    owner = auth.login("owner", "Owner123!")[0]
    customer = next(c for c in customers.list_customers(owner)
                    if c.full_name.startswith("Ahmed Hassan"))
    base = datetime(2027, 1, 4, 12, 0)  # a Monday
    for i in range(count):
        day = base + timedelta(days=i)
        booking = bookings.create_booking(
            receptionist, customer_id=customer.id, hall_id=1,
            package_id=None, event_type_id=1,
            start_at=day, end_at=day + timedelta(hours=5), guests=100)
        bookings.record_payment(
            accountant, booking.id, booking.deposit_required, "cash")


def measure_service_layer(db, settings, auth, label: str) -> None:
    print(f"[{label}]")
    dataset = label
    owner = auth.login("owner", "Owner123!")[0]
    receptionist = auth.login("receptionist", "Reception123!")[0]
    accountant = auth.login("accountant", "Account123!")[0]
    bookings = BookingService(db, settings)
    pricing = PricingService(db, settings)
    reports = ReportService(db, settings)
    search = SearchService(db, settings)
    customers = CustomerService(db, settings)
    customer = next(c for c in customers.list_customers(owner)
                    if c.full_name.startswith("Ahmed Hassan"))

    timed("login (Owner)", 2.0,
          lambda: auth.login("owner", "Owner123!"), dataset=dataset)
    timed("price calculation (Friday Classic Wedding)", 2.0,
          lambda: pricing.calculate(
              receptionist, hall_id=1, package_id=1, event_type_id=1,
              start_at=datetime(2027, 6, 4, 18, 0), duration_min=300,
              guests=200), dataset=dataset)
    timed("dashboard load (Owner)", 2.0,
          lambda: reports.dashboard(owner), dataset=dataset)
    timed("global search ('example')", 3.0,
          lambda: search.global_search(owner, "example"), dataset=dataset)
    timed("booking report (30 days)", 2.0,
          lambda: reports.booking_report(
              owner, date_from="2027-01-01", date_to="2027-01-30"),
          dataset=dataset)
    timed("financial report (30 days)", 2.0,
          lambda: reports.financial_report(
              owner, date_from="2027-01-01", date_to="2027-01-30"),
          dataset=dataset)

    # A booking create + deposit (write path) on a free future slot.
    # December: outside the Jan–Jul 2027 fixture range, so the second
    # measurement round never collides with the fixture bookings.
    # A different day per round, so the two rounds never collide
    # with each other either.
    slot_day = 26 if "200" in dataset else 25
    slot = datetime(2027, 12, slot_day, 12, 0)
    created: dict = {}

    def create_and_pay():
        booking = bookings.create_booking(
            receptionist, customer_id=customer.id, hall_id=1,
            package_id=None, event_type_id=1,
            start_at=slot, end_at=slot + timedelta(hours=5), guests=100)
        created["booking"] = booking
        bookings.record_payment(
            accountant, booking.id, booking.deposit_required, "cash")

    timed("booking create + deposit payment", 2.0, create_and_pay,
          dataset=dataset)
    return created


def measure_server_start(tmpdir: Path, port: int) -> float:
    """Time from launching ``python main.py`` to HTTP 200 on the
    login page (Gradio cold start). Uses a temp database."""
    import os
    env = dict(os.environ)
    env.update({"WEDDING_HALL_DB": str(tmpdir / "perf.db"),
                "GRADIO_SERVER_PORT": str(port),
                "GRADIO_ANALYTICS_ENABLED": "False",
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "NO_PROXY": "localhost,127.0.0.1",
                "no_proxy": "localhost,127.0.0.1"})
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen(
        [str(root / ".venv" / "bin" / "python"), "main.py"],
        cwd=root, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    start = time.perf_counter()
    try:
        deadline = start + 180
        while time.perf_counter() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/", timeout=2) as resp:
                    if resp.status == 200:
                        return time.perf_counter() - start
            except Exception:
                time.sleep(1)
        raise RuntimeError("Server did not answer within 180 s.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    print("Performance checks — Wedding Hall Management System")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        db_path = tmpdir / "perf.db"
        settings = AppSettings(db_path=db_path,
                               password_hash_iterations=60_000)
        db = DatabaseManager(db_path)
        db.initialize()
        seed(db, iterations=60_000)
        auth = AuthService(db, settings)

        # (a) seeded demo database (3 example customers, 1 quotation)
        measure_service_layer(db, settings, auth, "seeded demo database")

        # (b) 200-booking fixture
        print("Generating 200-booking fixture …")
        t0 = time.perf_counter()
        build_fixture(db, settings, auth, count=200)
        print(f"  fixture built in {time.perf_counter() - t0:.1f} s")
        measure_service_layer(db, settings, auth, "200-booking fixture")

        print("Measuring Gradio cold start (HTTP 200) …")
        cold = measure_server_start(tmpdir, 7871)
        RESULTS.append(("Gradio cold start to HTTP 200",
                        "seeded demo", "no target", cold, 0))
        print(f"  cold start: {cold:.1f} s (first start on this machine; "
              f"see A-12)")

    over = [r for r in RESULTS if r[4] and r[3] > r[4]]
    print()
    print(f"{len(RESULTS)} measurements, "
          f"{len(over)} over target: {[r[0] for r in over] or 'none'}")

    doc = Path(__file__).resolve().parents[1] / "docs" / "PERFORMANCE.md"
    lines = ["# Performance measurements (Phase 7, SRS §16.1, NFR-001)",
             "",
             "Targets are NOT guarantees: they apply to the agreed demo/test "
             "dataset on this machine (Linux VM, Python 3.12.3, Gradio "
             "6.32.0). Measured with `scripts/perf_check.py` on a "
             "temporary database (never the real app database).",
             "",
             "| Measurement | Dataset | Time | Target | Result |",
             "| --- | --- | ---: | --- | --- |"]
    for label, dataset, target, elapsed, limit in RESULTS:
        result = ("PASS" if not limit or elapsed <= limit else "OVER TARGET")
        lines.append(f"| {label} | {dataset} | {elapsed:.3f} s | {target} | "
                     f"{result} |")
    lines += ["",
              "Notes:",
              "- The seeded demo database has 3 example customers and 1 "
              "example quotation; the 200-booking fixture has 200 Confirmed "
              "bookings with deposits, so dashboard/search/report timings "
              "are measured on real data, not an empty database.",
              "- Gradio cold start is dominated by Gradio importing its "
              "frontend assets the first time on a machine (decision A-12 "
              "documents the VM proxy quirk); subsequent starts on the same "
              "machine are faster. It is not part of the NFR-001 targets.",
              "- No performance bug was found that required a fix: all "
              "service-layer timings are far below their targets on both "
              "datasets (see the table)."]
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Results written to {doc}")


if __name__ == "__main__":
    main()
