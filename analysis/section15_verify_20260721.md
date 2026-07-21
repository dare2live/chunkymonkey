# §15-VERIFY — knife-merge behavior evidence（2026-07-21）

> **生命周期**：evidence-only / F8 close package  
> **Authority**：`analysis/foundation_phase_reeval_20260721.md` §3 F8 + eng_gov §15  
> **Gate**：`backend/config/foundation_done.yaml` `section_15` + `check_foundation_done.py`  
> **禁令**：不放宽 `commits/knife≤1.5` / `pre_knife` / L3 Rule10 / PIT / ≤40d

---

## 0. Verdict

| 项 | 值 |
|---|---|
| **F8** | **PASS** |
| **Window** | 连续 3 个 L3 foundation 刀 |
| **commits/knife** | **1.0**（3/3；≤1.5） |
| **pre-knife** | 3/3 `true`（ledger + this session live） |
| **phase_closure_ready** | **true** iff F1–F10 all PASS（this knife flips F8） |

---

## 1. Consecutive L3 foundation knives

| # | Knife | SHA / tip | commits | pre-knife name | Evidence |
|---:|---|---|---:|---|---|
| 1 | **E0-HIST** | `4f7a13af0` | 1 | `e0-hist` | ledger 2026-07-21 E0-HIST FIXED；8 files one `safe_commit` |
| 2 | **FND-GATE** | `eefd19e53` | 1 | `fnd-gate` | ledger 2026-07-21 FND-GATE FIXED；17 files one `safe_commit` |
| 3 | **§15-VERIFY** | this tip | 1 | `section15-verify` | live `chunkyctl pre-knife section15-verify` OK；this evidence + yaml flip |

**Mean commits/knife** = `(1+1+1)/3 = 1.0 ≤ 1.5`.

**Contrast（before adoption）**：process_efficiency §1.4 — 59 micro-commits / ~36h window；`commits/knife >> 1.5`.  
**After**：policy knife `464e6edf9` started adoption；this window closes **behavior** with three consecutive single-commit L3 knives + mandatory pre-knife.

---

## 2. Live probes（this session）

| Probe | Result |
|---|---|
| `scripts/chunkyctl pre-knife section15-verify` | **OK**（moth coupling + codegraph explore） |
| Wall-clock | **real 0.85s**（2026-07-21 ~19:20 Asia/Shanghai） |
| Prior T0 (`pre-knife s7-inventory`) | 0.64s — still sub-second; no new framework |

---

## 3. Checklist（F8 bar — not loosened）

- [x] `required_consecutive_l3_knives = 3`
- [x] each knife `commits ≥ 1` integer
- [x] each knife `pre_knife: true`
- [x] mean commits/knife ≤ `max_commits_per_knife` (1.5)
- [x] evidence recorded in `foundation_done.yaml` `section_15`
- [x] no L3/Rule10/PIT/≤40d relaxation
- [x] no E/F remeasure / Optuna / StrategyRelease in this knife

---

## 4. What this does **not** claim

- Does **not** delete S7 23 typed hard-stop wall
- Does **not** invent org provider land
- Does **not** run Type-B enrichment
- Does **not** auto-start E/F（`goal.md` still holds pause markers for F9）
- Does **not** replace eng_gov §15 binding — only proves behavior adoption

---

## 5. Gate wire

```bash
PYTHONPATH=backend .venv/bin/python3 backend/scripts/check_foundation_done.py
# expect: PASS … phase_closure_ready=True ; F8 PASS
```
