[简体中文](README.md) | **English**

# Worlds 2026 Forecast · S16 Championship Simulation

Estimate team strength from historical results, then simulate the Swiss stage and knockouts to answer one question: **What is each team's probability of winning Worlds?**

Python 3.12 · Bradley–Terry · Monte Carlo · Offline reproducibility

[Report (Chinese)](docs/worlds2026_report.pdf) · [Detailed methodology (Chinese)](docs/methodology.md) · [Full probability table](outputs/stage_probabilities.csv) · [Data provenance](evidence/history_sources.md) · [Changelog (Chinese)](CHANGELOG.md) · [Automatic updates (Chinese)](docs/updates.md)

> **Frozen forecast v1.0.0:** information cutoff **October 23, 2026, 15:25:50 Beijing time (UTC+08:00; 07:25:50 UTC)**. These are model estimates at that cutoff, not a live ranking or confirmed match results. This is an independent personal analysis project, with no affiliation with or endorsement by Riot Games.

## Results preview

![Championship probabilities and model-resampling intervals](figures/02_champion_probabilities.png)

*Figures retain their original Chinese annotations.*

| Team | Quarterfinals | Semifinals | Final | Champion |
|---|---:|---:|---:|---:|
| GEN | 86.8% | 66.4% | 48.5% | 34.1% |
| HLE | 76.8% | 50.3% | 29.9% | 16.3% |
| BLG | 75.6% | 47.3% | 26.8% | 13.8% |
| T1 | 74.8% | 46.1% | 25.6% | 12.8% |
| DK | 67.4% | 38.0% | 19.2% | 8.6% |
| G2 | 52.5% | 23.4% | 9.2% | 3.2% |
| AL | 52.3% | 22.7% | 8.6% | 2.8% |
| KC | 48.0% | 19.3% | 6.7% | 2.0% |

These results come from the saved **100,000 baseline full-tournament simulations**, sorted by championship probability. The full CSV includes 19 teams; teams already eliminated at the cutoff have zero probability of reaching subsequent stages.

GEN's baseline championship probability is approximately **34.1%**, with a **90% model-resampling interval of 15.9%–46.6%**. This interval reflects variation across resampled models and does not cover every source of model risk. The frequency with which a team ranks first across resampled models is also distinct from its probability of winning the tournament.

## Matchup win-probability heatmap

Each cell shows the frozen model's probability that the row team beats the column team in a single game (BO1). Click the image to view it at full size.

[![BO1 win-probability heatmap for the 16 Swiss-stage teams](figures/01_matchup_matrix_bo1.png)](figures/01_matchup_matrix_bo1.png)

## What the project does

- **Historical modeling:** the primary sample contains 2,599 game outcomes across 1,005 series and 61 teams. Team strength is estimated using a Bradley–Terry model with time decay and L2 regularization.
- **Temporal backtesting:** hyperparameters are selected using May–July validation folds. August through the information cutoff is held out for testing, with basic Elo and a constant 50% predictor as baselines.
- **Full-tournament simulation:** fixes the known opening draw, pairs Swiss teams by record, avoids rematches, switches between BO1 and BO3 where required, and simulates the BO5 knockout stage.
- **Uncertainty analysis:** 15 sensitivity scenarios with 100,000 simulations each, plus 200 model refits with 10,000 tournament simulations per refit. Two additional scenarios downweight observations with historical lineups that differ from the reported Worlds rosters.
- **Auditable outputs:** preserves input snapshots, sources, validation records, random seeds, result tables, and a SHA-256 manifest.

The main production logs above record **3.6 million complete tournament simulations**, not 3.6 million independent model fits.

## Simulation algorithm

A time-decayed Bradley–Terry model estimates team strengths $\theta$ and a blue-side term $\beta$. Single-game probabilities are then converted to series probabilities:

$$
\begin{aligned}
p_{AB} &= \frac12\sum_{s\in\{-1,1\}}\sigma\!\left(\frac{\theta_A-\theta_B+s\beta}{T}\right),\quad \sigma(x)=\frac{1}{1+e^{-x}},\quad T=1.5 \\
P_{\mathrm{BO3}}(p)&=3p^2-2p^3,\qquad P_{\mathrm{BO5}}(p)=10p^3-15p^4+6p^5 \\
\widehat P(A\text{ wins Worlds})&=\frac{1}{N}\sum_{r=1}^{N}\mathbf{1}\{C_r=A\},\quad N=100{,}000
\end{aligned}
$$

Known results are fixed, while draws and match outcomes are sampled according to the Swiss and knockout rules; $C_r$ is the champion in simulation $r$. The series conversion assumes independent games conditional on fixed strengths. Model uncertainty is assessed separately using a series-cluster bootstrap. [Detailed methodology (Chinese)](docs/methodology.md)

## Quick start

To read the findings, open the [PDF report (Chinese)](docs/worlds2026_report.pdf); Python is not required.

To run the code, download or clone this repository and enter its root directory. Python 3.12 is recommended:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python src/check_repository.py
```

In Windows Command Prompt, activate the virtual environment with `.venv\Scripts\activate`.

`check_repository.py` scans for common credential patterns and local absolute paths, verifies release-file hashes, and runs the existing output checks and a small synthetic state-update demonstration in a temporary copy. It does not overwrite the repository's frozen outputs. GitHub Actions runs the same checks.

### Full reproduction

```bash
python src/run_analysis.py --phase all
python src/verify_outputs.py
python src/build_figures.py
python src/build_report.py
```

A full run includes millions of simulations. Runtime depends on your hardware, and the commands overwrite outputs in the current project copy. Use a separate clone or copy for reproduction. The pipeline uses frozen local inputs and **requires no network access or API key**; installing dependencies initially requires network access.

The modeling phases can also be run separately with `--phase fit`, `--phase simulate`, and `--phase uncertainty`. Pinned dependencies, input data, and random seeds support reproducibility; floating-point optimization may differ slightly across platforms. See [environment.json](outputs/environment.json) for the original execution environment.

### Updating tournament state

See the [update procedure in the detailed methodology (Chinese)](docs/methodology.md#比赛结束后更新) for steps, fields, and limitations. Preserve the previous version before changing the information cutoff, known results, or announced draws, and record all changes. An update that fixes new results without refitting team strength should explicitly state that it still uses the old strength model.

Frozen-data processing, modeling, simulation, and report generation continue to support offline use. The automatic result updater reads only ChainCC's public CSV, licensed under CC BY 4.0, and does not use a Riot API key. Changes to model-training data still require separate review and a new version.

## Project structure

```text
.
├── src/                  # Data preparation, models, simulation, validation, figures/reports
├── data/                 # Frozen data, rules, rosters, and known tournament state
├── outputs/              # Probabilities, matrices, backtests, sensitivity results, and logs
├── figures/              # PNG/PDF figures and bundled fonts
├── docs/                 # Chinese report and detailed methodology
├── evidence/             # Sources, official schedule snapshots, and audit notes
├── examples/             # Explicitly labeled synthetic state-update demonstrations
├── .github/workflows/    # Automated integrity and execution checks
├── config.json           # Information cutoff, hyperparameter grids, and random seeds
├── requirements.txt      # Pinned dependencies
└── MANIFEST.json          # SHA-256 manifest of release files
```

## Interpreting the probabilities

The model does not explicitly estimate champion draft effects, patch adaptation, player form, or psychological factors. Cross-region observations are limited. The BO3/BO5 baseline assumes independent games conditional on fixed team strengths. The draw logic is an auditable interpretation of public rules, not the tournament organizer's internal algorithm.

Backtests evaluate historical game-level predictions; they do not directly establish that championship probabilities for a single Worlds event are calibrated. More simulations reduce Monte Carlo sampling error, but do not eliminate model bias. Detailed assumptions and limitations are preserved in the [methodology (Chinese)](docs/methodology.md).

## Data, attribution, and usage

Match data comes from **[ChainCC](https://chaincc.lol/free/data/)**.
