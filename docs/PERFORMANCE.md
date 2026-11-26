# Performance measurements (Phase 7, SRS §16.1, NFR-001)

Targets are NOT guarantees: they apply to the agreed demo/test dataset on this machine (Linux VM, Python 3.12.3, Gradio 6.32.0). Measured with `scripts/perf_check.py` on a temporary database (never the real app database).

| Measurement | Dataset | Time | Target | Result |
| --- | --- | ---: | --- | --- |
| login (Owner) | seeded demo database | 0.064 s | <= 2 s | PASS |
| price calculation (Friday Classic Wedding) | seeded demo database | 0.008 s | <= 2 s | PASS |
| dashboard load (Owner) | seeded demo database | 0.035 s | <= 2 s | PASS |
| global search ('example') | seeded demo database | 0.075 s | <= 3 s | PASS |
| booking report (30 days) | seeded demo database | 0.004 s | <= 2 s | PASS |
| financial report (30 days) | seeded demo database | 0.017 s | <= 2 s | PASS |
| booking create + deposit payment | seeded demo database | 0.089 s | <= 2 s | PASS |
| login (Owner) | 200-booking fixture | 0.028 s | <= 2 s | PASS |
| price calculation (Friday Classic Wedding) | 200-booking fixture | 0.008 s | <= 2 s | PASS |
| dashboard load (Owner) | 200-booking fixture | 0.041 s | <= 2 s | PASS |
| global search ('example') | 200-booking fixture | 0.037 s | <= 3 s | PASS |
| booking report (30 days) | 200-booking fixture | 0.153 s | <= 2 s | PASS |
| financial report (30 days) | 200-booking fixture | 1.608 s | <= 2 s | PASS |
| booking create + deposit payment | 200-booking fixture | 0.061 s | <= 2 s | PASS |
| Gradio cold start to HTTP 200 | seeded demo | 9.152 s | no target | PASS |

Notes:
- The seeded demo database has 3 example customers and 1 example quotation; the 200-booking fixture has 200 Confirmed bookings with deposits, so dashboard/search/report timings are measured on real data, not an empty database.
- Gradio cold start is dominated by Gradio importing its frontend assets the first time on a machine (decision A-12 documents the VM proxy quirk); subsequent starts on the same machine are faster. It is not part of the NFR-001 targets.
- No performance bug was found that required a fix: all service-layer timings are below their targets on both datasets (see the table). The closest is the financial report on 200 bookings (1.608 s vs a 2 s target) — it recomputes per-booking reconciliation rows, so it grows with booking count; on the seeded demo database it is 0.017 s.
