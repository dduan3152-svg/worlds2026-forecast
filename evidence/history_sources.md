# Historical data provenance and coverage

## Frozen information boundary

The agreed prediction cutoff is **2026-10-23 07:25:50 UTC**, equivalent to
2026-10-23 15:25:50 in Asia/Shanghai. No Swiss-stage result is used.

For individual game records, the parser interprets the source's unzoned date
as UTC and requires the recorded start plus game duration to precede the
cutoff. This interpretation is consistent with the independently checked
official match schedules. The latest retained individual game starts on
2026-10-18 at 19:48:36 UTC, so none of these records is near the boundary.

Riot's series endpoint provides scheduled start times and completed status,
but not a reliable actual end timestamp. Its verification layer excludes
anything scheduled within twelve hours before the cutoff. There are no
relevant missing matches in that boundary interval.

## Published individual-game snapshot

- Publisher: **ChainCC**, <https://chaincc.lol/free/data/>.
- Exact public file: <https://chaincc.lol/data/chaincc-teams-2026.csv.gz>.
- Saved without modifying the compressed payload:
  `data/history_chaincc_teams_2026.csv.gz`.
- SHA-256:
  `ae1008ed9424a5eda59a873e9d70d06669337a2068d48497efbbd695e1c8105d`.
- The publisher page states an update time of
  **2026-10-22T06:40:55-03:00**, a **CC BY 4.0** licence, and attribution
  **chaincc.lol**. This attribution applies to the published snapshot and
  its normalized derivatives. It does not imply Riot endorsement.
- The full file contains **19,506 team rows**, representing **9,753 real
  games**. Every game has exactly one Blue row and one Red row, exactly one
  winner, reciprocal opponent names, and matching game metadata.
- The model-ready selection has **2,849 real individual games**, **63
  teams**, and **1,107 series grouping keys**, from 2026-01-14 through
  2026-10-18. No result or game order has been synthesized.

The publisher's `bo_format`, `match_in_series`, and `game_in_match` columns
are entirely empty. Their absence is preserved. Each row in
`history_games.csv` is one observed game: `wins_a + wins_b = 1`, and
`best_of = 1` describes that observation unit, not the parent series format.

`game_number` and Blue/Red are actual source fields. For records whose
`game_id` contains `_game_N`, the prefix is retained as a series grouping
key. For other records, grouping uses the team pair, competition, split,
increasing source game numbers, and a maximum twelve-hour gap. UTC
midnight alone does not split a series. Derived grouping keys are labelled
as derived, and the aggregate table leaves the original planned
`best_of` blank.

## Full official series reconciliation

The underlying public Riot schedule endpoint is
<https://esports-api.lolesports.com/persisted/gw/getSchedule?hl=en-US>.
The official site is <https://lolesports.com/>. The downloader discovers
league IDs from Riot's public league catalogue and retains the selected
IDs explicitly in the reproducible script.

Twenty-eight paginated JSON responses cover **all 1,004 completed 2026
series available from the selected official feeds**: LCK, LPL, LEC, LCS,
LCP, CBLOL, First Stand, MSI, Worlds play-ins, Americas Cup and DCGI.
The raw pages and response metadata, including timestamps and SHA-256
hashes, are retained under `evidence/history_riot_schedule_*`.

After normalizing the official KRX and source DRX labels to the same
Kiwoom DRX team, the reconciliation finds:

| Result of comparison | Series |
|---|---:|
| All observed winner counts exactly equal the official score | 1,002 |
| Source omits one observed game | 2 |
| Entire official series absent from the selected source | 0 |
| Contradictory winner counts | 0 |

The two confirmed omissions are deliberately stored as **aggregate
residual counts**, not fabricated individual game rows:

| Scheduled UTC date | Official score | Published games retained | Added residual |
|---|---|---|---|
| 2026-08-01 10:00 | DK 2–0 GEN | DK's game 2 win | DK 1 win, GEN 0 |
| 2026-08-07 09:00 | TT 2–1 WE | Games 1–2, one win each | TT 1 win, WE 0 |

These are the **only two rows** in `data/history_supplements.csv`.
They include official source URLs, official match IDs and raw snapshot
hashes. They have no invented side assignment, draft, timestamp, game
identifier, or game number. Use the 2,849 game rows plus these two residual
counts, or use an appropriately reconciled aggregate table; do not add
the full official series scores on top of already observed games.

The EWC feed is not present in Riot's regular schedule API. Its 232 games
are therefore not part of this complete reconciliation. The independent
QA files check its final against the event organizer's published result.

## Coverage and recommended model split

| Competition | Individual games | Derived series groups |
|---|---:|---:|
| LCK | 502 | 188 |
| LPL | 674 | 246 |
| LPL regional qualifier | 12 | 3 |
| LEC | 383 | 186 |
| LCS | 260 | 95 |
| LCP | 283 | 97 |
| CBLOL | 275 | 117 |
| First Stand | 45 | 13 |
| MSI | 71 | 20 |
| Worlds play-ins | 22 | 6 |
| EWC, including qualifiers | 232 | 103 |
| Americas Cup | 19 | 6 |
| DCGI | 71 | 27 |

A conservative core specification uses the six major regions, LPL
regional qualifier, First Stand, MSI and Worlds play-ins: **2,527 actual
game rows plus the two official residual wins**, representing **2,529
game outcomes across 60 teams**.

The extended specification can add EWC, Americas Cup and DCGI, reaching
**2,851 observed game outcomes**. The extension has an identifiable
purpose but a different competition mix:

- **EWC main event: 51 games**, dated July 15–19, provides additional
  direct international comparisons.
- **EWC qualifiers: 181 games**, before that event window, add mainly
  regional matchups. They are marked separately in `event_scope`.
- **Americas Cup: 19 games**, in March, adds comparisons between LCS
  and CBLOL teams.
- **DCGI: 71 games**, in October, provides recent international results
  among teams outside Worlds. Its participants and timing differ from
  the Worlds field, so its influence should be evaluated as sensitivity.

The recommendation to distinguish core and extension is methodological,
not a claim that the excluded observations are false.

## Known source-label limitations

1. `WLDs` includes both the **12 LPL regional qualifier games in September**
   and **22 Worlds play-in games in October**. The cleaned data explicitly
   separates `LPL_RQ` from `WORLDS`, preserving `competition_raw`.
2. `WLRQ` denotes another academy/amateur competition in this file. Its
   six games are excluded; they are not Worlds regional qualifiers.
3. `Team Secret` and `Team Secret Whales` have different source team IDs.
   The eight selected `Team Secret` games remain under `TS`, separate
   from `TSW`; no identity merge is assumed. These are EWC observations
   and do not affect the conservative core specification.
4. The source patch column includes labels such as `16.20`. It is retained
   verbatim, not silently converted to an official `26.20` label. No
   patch-based feature should be considered verified by this file alone.
5. Team-level outcomes do not establish which players or substitutes
   appeared. Roster adjustments require separately verified appearance
   data; no roster-change effect is inferred here.
6. Series grouping is explicit and reproducible, but derived IDs are not
   presented as Riot's real match IDs. The official IDs are in the separate
   reconciliation table.

## Oracle's Elixir discovery attempt

The original preferred source was
<https://oracleselixir.com/tools/downloads>, whose public app links to
<https://drive.google.com/drive/u/1/folders/1gLSw0RLjBbtaNy0dgnGQDAZOHIgCe-HH>.
The directory lists `2026_LoL_esports_match_data_from_OraclesElixir.csv`
with file ID `1hnpbrUpBMS1TZI7IovfpKeZfWJH1Aptm`. Its normal public
download returned **Google Drive quota exceeded**. That response is
retained as `history_oracleselixir_quota_error.html`, not treated as data.
No quota bypass was attempted. The old public S3 URLs checked for the
2026 full file and the October 22/23 dated snapshots returned 404.

The actual model source is the separately published ChainCC snapshot,
with the official Riot series reconciliation described above. Do not
describe the successful download as Oracle's Elixir itself.

## Reproduction

From the project root, use the included snapshots without network:

```bash
python src/fetch_history.py --reconcile-riot
```

This repository retains offline processing only. Network download and API
credential handling have been removed. Prepare and audit any future data
snapshot separately, then import it as a new version.

The default cutoff remains fixed at `2026-10-23T07:25:50Z`. The bundled gzip
and hashes are the reproducibility reference for this version. No third-party
Python package is required for the normalization command above.
