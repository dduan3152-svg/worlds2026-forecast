# Historical lineups supplement

This supplement joins the public ChainCC player snapshot to the already frozen historical game IDs. It does not change the baseline fit, tournament simulations, bootstrap, historical backtests, or probability figures.

## Reproduction

Run `python src/extract_historical_lineups.py` from the project root. The raw snapshot is `data/history_chaincc_players_2026.csv.gz`; its public source is https://chaincc.lol/data/chaincc-players-2026.csv.gz and metadata/hash are preserved in `evidence/history_chaincc_players_2026.meta.json`. ChainCC publishes the dataset under CC BY 4.0; credit chaincc.lol.

## Coverage and outputs

- `data/historical_lineups.csv`: 5,698 team-game records for 2,849 selected real games, each with five observed roles and player handles. The 2,597 actual games in the frozen primary model are flagged (5,194 team-game records).
- `data/historical_lineup_summary.csv`: descriptive historical lineup counts for all 63 observed teams.
- `data/historical_lineups_vs_worlds_rosters.csv`: descriptive comparison for all 19 Worlds entrants against the separately sourced reported Worlds starters.
- `evidence/history_lineup_coverage.json`: completeness, field reconciliation, input/output hashes and limitations.
- `evidence/history_lineup_context.json`: TES's confirmed ZUIAN-to-369 starting-roster change and public primary-source reference.

All 28,490 selected player rows reconcile with the frozen team snapshot on team, opponent, side, result, date, length, source league and patch. Every selected team-game has one each of top, jungle, mid, bot and support, with five nonempty distinct handles. The two official residual outcomes lack real player rows and remain missing: no lineup or game ID has been invented.

## Interpretation

Eighteen of the 19 teams' latest observed five match their reported Worlds five after case/whitespace normalization. TES differs at top lane: the real latest lineup has ZUIAN, whereas the reported Worlds lineup has 369 following the club's October 6 visa announcement. This is an actual player change. The exact expected TES five has zero games in this selected sample; 369's 43 observed earlier games had different jungle, bot and support players. The comparison is not a numerical estimate of the effect of substitution.

Historical match lineups are not complete registration lists, and player handles are not stable person IDs. Unverified renames are not merged. Worlds starter reports are never used to populate earlier matches or as a feature in historical backtesting. The tables support further lineup-aware modeling only after a causal/time-safe feature design and held-out validation; the published baseline remains unchanged.
