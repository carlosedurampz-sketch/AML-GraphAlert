# AML-GraphAlert — Detection of PLD Typologies & Transactional Networks

> **Resumen en español:** Motor híbrido para detectar operaciones inusuales
> (prevención de lavado de dinero, PLD): reglas transaccionales en SQL
> (pitufeo, desviación del perfil, fan-in/fan-out), análisis de grafos con
> NetworkX (ciclos de round-tripping, cuentas mula, PageRank y betweenness)
> y un modelo de Machine Learning (Isolation Forest) que prioriza las
> alertas para que el analista revise primero los casos de mayor riesgo.
> Funciona con datos sintéticos realistas tipo SPEI + efectivo — no necesita
> descargar ningún dataset — y genera una visualización interactiva del
> subgrafo sospechoso. **Proyecto de portafolio de aprendizaje aplicado:
> todos los resultados son ilustrativos, sobre datos sintéticos.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![SQL](https://img.shields.io/badge/SQL-DuckDB%20%2F%20PostgreSQL-orange)](sql/README_SQL.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What it does

Banks and fintechs in Mexico (and everywhere) must detect *operaciones
inusuales* — transactions that do not match a customer's profile or that
fit known money-laundering typologies — and report them. Rule engines
alone generate far too many alerts: most are false positives, and analysts
drown in them. This project combines three layers, in the order a
compliance team would actually use them:

| Layer | Tool | What it catches |
|---|---|---|
| **1. SQL rules** | DuckDB / PostgreSQL (`sql/`) | Structuring, profile deviation, fan-in/fan-out |
| **2. Graph analysis** | NetworkX (`src/graph_analysis.py`) | Closed cycles (round-tripping), mule rings, bridge nodes (PageRank / betweenness) |
| **3. Alert prioritisation** | Isolation Forest (`src/alert_scoring.py`) | Ranks all flagged accounts 0–100 so review starts with the riskiest |

The pipeline is fully reproducible: `python -m src.run_pipeline`
generates the synthetic data, runs the SQL, builds the graph, scores the
alerts and writes everything to `outputs/`.

## Results on the synthetic example — illustrative

> ⚠️ **All numbers below are illustrative.** They are computed on
> synthetic data with planted typologies (seed 42), so they show that the
> pipeline works end-to-end — not how it would perform on real data.
> Precision/recall here are measured against the planted labels, which
> real compliance teams do not have for most alerts.

| Metric | Value |
|---|---|
| Accounts / transactions | 823 / 50,362 (90 days, Jan–Mar 2026) |
| Planted suspicious accounts | 23 (6 structuring, 8 mules, 4 collectors, 5 round-tripping) |
| Accounts with ≥1 rule hit | 21 |
| Rule-hit rows (all rules) | 21 (6 structuring, 8 fan-in/out, 2 profile deviation, 5 cycle) |
| Cycles found (length ≤ 6) | 1 — exactly the planted 5-account cycle |
| Precision / recall, all rule alerts | **0.905 / 0.826** (19 of 23 planted found) |
| Precision @ top-10 ranked | 1.000 (10/10) |
| Precision @ top-21 ranked | 0.905, recall 0.826 |
| Precision @ top-50 ranked | 0.380, recall 0.826 |

### How to read the false-positive comparison

Two comparisons, both illustrative:

1. **Rule tuning beat the model.** The first version of the fan-in/out
   rule aggregated over the whole 90 days: 144 hits, and all rule alerts
   together had precision **0.09** / recall 0.609. Changing the rule to a
   *weekly* window (many senders in one week, not in three months) cut
   fan-in/out to **8 hits — all 8 mules** — and all rule alerts to
   precision **0.905** / recall 0.826. The window choice did most of the
   work. Honest caveat: the v2 thresholds were tuned after seeing v1,
   so v2 precision is optimistic; on real data, thresholds must be tuned
   on labelled history, not on the set you measure.
2. **Ranking as triage.** The ML scorer ranks the 19 detectable planted
   accounts in positions 1–19 (top-10 precision 1.000). Reviewing the
   top 21 ranked accounts instead of all 823 is a **97.4% smaller review
   queue** at 82.6% recall of planted accounts. The 4 planted accounts
   missed by every layer are the *collector* accounts (`CTA-920xx`):
   they receive pass-through funds but never forward them, so no rule
   fires and their anomaly score is low — a documented gap and the
   obvious next rule (flag accounts receiving from already-flagged mules).

The point is not the exact percentage (it is an artefact of synthetic
data); the point is the *workflow*: rules for explainability and audit,
ML for triage, a human for the final decision. In production the same
comparison must be re-run on labelled history, with thresholds tuned to
the team's capacity and risk appetite.

## Architecture

```
data/synthetic/ ──► SQL rules (DuckDB) ──┐
  accounts.csv      01_structuring.sql   │
  transactions.csv  02_profile_deviation │
  ground_truth.csv  03_fan_in_out.sql    ▼
                     ┌─────────────────────────────────┐
                     │ rule_hits.csv                   │
                     └──────────────┬──────────────────┘
                                    │
  transactions.csv ──► NetworkX graph ──► cycles, fan-in/out,
                                           PageRank, betweenness
                                    │
                                    ▼
              IsolationForest + rule evidence
                         │
                         ▼
        outputs/alerts_ranked.csv (risk_score 0–100)
        outputs/metrics.json
        outputs/suspicious_subgraph.html  (interactive, pyvis)
        outputs/suspicious_subgraph.png
```

## How to run

```bash
git clone <this-repo>
cd AML-GraphAlert

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Full pipeline (generates data, runs SQL + graph + ML, writes outputs/)
.venv/bin/python -m src.run_pipeline

# …or everything, including tests:
bash run_all.sh
# …or:  make run   /   make test
```

Outputs:

| File | Contents |
|---|---|
| `outputs/alerts_ranked.csv` | One row per account, `risk_score` 0–100, `rank`, rule count, graph features |
| `outputs/rule_hits.csv` | Every SQL/graph rule hit (`account_id, rule, detail`) |
| `outputs/cycles.csv` | Closed transfer cycles found |
| `outputs/fan_in_out.csv` | Fan-in/out and pass-through ratio per account |
| `outputs/centrality.csv` | PageRank and betweenness per account |
| `outputs/metrics.json` | Counts, precision/recall (illustrative), FP-reduction comparison |
| `outputs/suspicious_subgraph.html` | Interactive suspicious subgraph (open in a browser) |
| `outputs/suspicious_subgraph.png` | Same subgraph as a static image |

### Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

Tests execute the real structuring SQL in DuckDB and the real graph
functions on toy data (cycle detection, fan-in counting).

## Typologies documented

See [`sql/README_SQL.md`](sql/README_SQL.md) for the full documentation
of each rule, its thresholds and its caveats. Summary:

1. **Structuring / pitufeo** (`sql/01_structuring.sql`) — several cash
   deposits of MXN 13,500–14,900 (just below the illustrative MXN 15,000
   threshold) in 24 h–7 d windows, using window functions with a `RANGE`
   time frame.
2. **Profile deviation** (`sql/02_profile_deviation.sql`) — recent 7-day
   activity far above the account's *own* baseline (z-score > 3 or >5×
   mean, with a materiality floor and a minimum-history requirement).
3. **Fan-in / fan-out** (`sql/03_fan_in_out.sql`) — many distinct SPEI
   senders, forwarding ~everything onwards (`pass_through_ratio ≥ 0.70`),
   the classic mule-account shape.
4. **Round-tripping** (graph, not SQL) — closed cycles
   A → B → C → D → E → A with similar amounts, found with
   `networkx.simple_cycles` on strongly connected components.

Thresholds are modelling choices for this portfolio, **not** statements
of Mexican law (LFPIORPI / CNBV / UIF rules set their own). See
`sql/README_SQL.md` before reusing any number.

## Methodology

- **Synthetic data, labelled by construction.** Real AML data is
  confidential, so the generator (`src/generate_synthetic.py`, seed 42)
  creates ~800 normal accounts (payroll, SPEI, ATM, card) and plants
  23 suspicious ones across the three typologies + a cycle. Labels are
  used *only* for evaluation, never to train the scorer — the
  Isolation Forest is unsupervised, as in a real setting where confirmed
  cases are rare.
- **No temporal leakage in the rules.** The profile-deviation baseline
  excludes the recent window it is compared against; the structuring
  windows slide in time.
- **Explainability over black boxes.** Every alert keeps its rule
  (`detail` column) and its graph features, so an analyst can say *why*
  an account was flagged. The ML score only orders the queue.
- **Reproducibility.** Fixed seed, pinned requirements, one command
  (`bash run_all.sh`) from a clean checkout to all outputs.

## Limitations (honest ones)

- Synthetic data is cleaner than real data: typologies are planted, so
  precision/recall are optimistic and not comparable to production.
  The v2 rule thresholds were also tuned after seeing the v1 results.
- The graph is built from SPEI transfers only; cash has no counterparty.
- Betweenness is sampled (100 pivots) for speed; PageRank is exact.
- Cycle detection on large graphs searches only aggregated edges
  ≥ MXN 10,000 (see `src/graph_analysis.py`): random low-value traffic
  creates countless spurious short cycles, and a round-tripping ring
  moves large repeated amounts. Small-value cycles would be missed.
- The 4 collector accounts are missed by every layer (see above) —
  the next rule to add is "receives from a flagged mule".
- The scorer is unsupervised — with real labelled cases, a supervised
  model and calibration would be the next step.
- This is a portfolio project, not a compliance system: it does not
  implement reporting, case management, or any legal advice.

## About the author / Sobre el autor

I am **Carlos Eduardo Ramírez Pérez**, a physicist in the process of
graduating from Universidad Autónoma Metropolitana (UAM, 2022–2026),
with a technical degree in Electronics (CBTis No. 003, Tlaxcala).
I am building this portfolio to move into data / risk / compliance
roles in banking and fintech.

I have **no prior employment in banking or in PLD/compliance** — I am
not presenting this as work experience, but as applied learning, and
I want to be transparent about that. What I do bring, and what this
project is built on, is:

- **Traceability, reproducibility and auditable documentation.** In 2026
  I worked at INCYMED, a calibration laboratory accredited under
  ISO/IEC 17025 (PJLA), where I was authorised to execute and review
  calibrations of pressure, temperature and electrical instruments.
  In that environment a result only counts if it is traceable, the
  procedure is documented, and someone else can reproduce it — exactly
  the mindset compliance work demands: every alert in this project
  keeps its rule, its evidence and its parameters, and the whole
  pipeline re-runs from one command.
- **Graphs, modelling and Python.** My physics training is modelling
  with mathematics and code; here that becomes transfer graphs,
  centrality and Monte Carlo-style thinking about networks, plus
  advanced Python (and R, C/C++, Fortran 90) from my degree and from
  courses in Data Science with R (UNAM), Python I (PUC Chile), a
  four-course Big Data series, and Introduction to Statistics
  (Stanford Online / Coursera).
- **SQL, in progress — honestly.** My SQL is basic and I am
  strengthening it with this project. That is why the SQL files are
  heavily commented and documented step by step
  (see `sql/README_SQL.md`): they are evidence of learning, not a
  claim of expertise. I would rather show the work than overstate it.

I also taught physics and mathematics as a state delegate (CMF
2021–2022), which taught me to explain technical ideas clearly — a
skill I use in the documentation here. English: B2.

*Soy Carlos Eduardo Ramírez Pérez, físico en proceso de titulación por
la UAM. No tengo experiencia laboral previa en banca ni en PLD: este
proyecto es aprendizaje aplicado, no experiencia. Lo que sí aporto es
el trabajo bajo norma ISO/IEC 17025 en INCYMED — trazabilidad,
reproducibilidad y documentación auditable —, la base en modelado y
grafos de la física, y Python avanzado. Mi SQL es básico y está en
fortalecimiento: por eso las consultas están bien comentadas y
documentadas, como evidencia del proceso.*

## Project structure

```
AML-GraphAlert/
├── README.md
├── LICENSE (MIT)
├── requirements.txt
├── Makefile / run_all.sh
├── sql/
│   ├── 01_structuring.sql
│   ├── 02_profile_deviation.sql
│   ├── 03_fan_in_out.sql
│   └── README_SQL.md
├── src/
│   ├── generate_synthetic.py   # SPEI + cash generator, planted typologies
│   ├── graph_analysis.py       # NetworkX: cycles, fan-in/out, centrality
│   ├── alert_scoring.py        # IsolationForest prioritisation + evaluation
│   ├── visualize.py            # pyvis interactive subgraph + PNG
│   └── run_pipeline.py         # end-to-end orchestration
├── tests/
│   ├── test_structuring.py     # real SQL in DuckDB
│   └── test_graph.py           # cycle + fan-in detection
├── data/synthetic/             # generated (git-ignored)
└── outputs/                    # generated (git-ignored)
```

## License

MIT — see [LICENSE](LICENSE).
