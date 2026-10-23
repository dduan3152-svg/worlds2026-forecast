# Rules verification and simulator assumptions

Information cutoff: **2026-10-23 07:25:50 UTC / 15:25:50 Beijing**.
The official [Worlds 2026 Primer](https://lolesports.com/en-GB/news/worlds-2026-primer), published October 5, schedules the first Swiss broadcast for **October 23 at 17:00 UTC**. No Swiss result is available before the specified cutoff. The opening pairings are a fixed input supplied and verified by the main data pipeline.

## Primary evidence

The [2026 World Championship Event Specific Ruleset v1.01](https://cdn.sanity.io/files/dsfx7636/news_live/faa5ce974e58615911fbee931c6123e2785a8b46.pdf) was reached through [Riot Competitive Operations](https://competitiveops.riotgames.com/en-US/library?game=lol). Relevant sections are 4.1.2–4.1.4 (match lengths and First Selection), 4.3.2–4.3.4 (draw restrictions and bracket placement), and 5.1 (patch). Machine-readable values and source routing are in `data/rules.json`.

The rules specify random same-record draws, position advancement to avoid immediate or forced conflicts, and a discretionary rematch exception when no eligible draw exists. They do not provide a complete implementation of every operational placement decision. The probability law below is therefore an explicit operationalization, not a recovered official computer algorithm.

## Two draw probability laws

### Default: `next_available_slot`

Within a record group, draw all balls in uniformly random order. Place each newly drawn team into the earliest empty slot for which a legal completion of that group's current draw remains possible. A prospective partner cannot be a prior Swiss opponent. Completion checking applies to the remaining slots in the current draw; it does not condition on unknown later results.

For each possible ball order, a complete matching induces a sequence of slot numbers, one for each ball in draw order. Sorting matchings lexicographically by those sequences implements earliest feasible placement. The simulator enumerates every permutation (at most 8! = 40,320) and counts which legal matching wins. Cached weights are then sampled across tournaments. Thus the simulator samples exactly from its stated procedure, without assuming every legal matching has equal weight.

The two methods are observably different: in a six-team illustrative group where team 0 cannot face teams 1 or 2, procedural and uniform-legal matching probabilities differ by up to 1/60. This is a mathematical example, not a measured effect on any actual team's title chance.

### Sensitivity alternative: `uniform_valid`

Enumerate legal unordered perfect matchings and sample them equally. This is a comparison assumption, not an assertion about Riot's live drawing mechanism.

If the legal-matching set is empty, both implementations use an unrestricted random pairing and count the case as a discretionary-rule fallback. Any nonzero count must be disclosed rather than silently presented as an exact forecast of an official decision.

## What the model does not estimate

The simulator consumes a fixed 16-by-16 single-game probability matrix. It estimates neither roster strength nor version effects itself. BO3 and BO5 conversion assumes independent games with unchanged win probability. Game-side choice, pick-order choice, adaptation, champion-pool depletion, and shared series-level shocks have no separately estimated coefficients. Alternative input matrices and draw methods can be used as sensitivity scenarios, but increasing simulation count alone cannot validate these assumptions.

Current [Liquipedia 2026 format information](https://liquipedia.net/leagueoflegends/World_Championship/2026) reports Fearless Draft for BO3/BO5. The indexed text was accessible; direct fetching returned 403. The retrieved event-specific primary PDF does not explicitly restate a Fearless clause. The official [2026 International Events Competition Ruleset v1.1](https://cdn.sanity.io/files/dsfx7636/news_live/d29e446e0a0d0813a8caaae1d2817dd82cfe79c6.pdf), listed by Riot on July 27, was subsequently recovered from the official directory’s raw embedded page data and retained locally as PDF and extracted text. Its sections 4.5.6–4.5.7 explicitly handle unavailable champion selections in Fearless matches: an unavailable pick is corrected by restarting selection at that error, whereas an unavailable ban does not earn a restart. However, neither retrieved PDF expressly defines cross-team pick carryover, ordinary-ban carryover, game-5 reset, or the Worlds-specific scope of Fearless series. Thus the detailed format remains secondarily confirmed, with this precise primary-evidence boundary retained in the data. No invented champion-pool advantage enters the model.

## Output interpretation

All marginal probabilities use 0–1 units. The six terminal Swiss records have totals 2, 3, 3, 3, 3, and 2 teams in every simulation. Second-round opponent distributions are available unconditionally and conditional on an opening win or loss. Impossible conditioning events are undefined (`NaN`, or JSON `null`), never assigned a misleading zero-probability distribution.

Unordered final pairs occupy only the upper triangle of their matrix, so counts sum to the simulation count. Stored paths are random illustrative realizations, not a declared most-likely bracket. Reported standard errors concern finite Monte Carlo sampling conditional on the supplied model; parameter, data, and model uncertainty require separate analysis.

## Updating after Swiss results

The optional `known_matches` parameter accepts records with `round`, `a`, `b`, and `winner`. Completed rounds must form a contiguous prefix. A partly completed current round is supported if its full announced draw is also supplied through `announced_rounds`; round 1 already has its fixed draw. A fully observed round supplies its own complete pairings automatically. The complete next-round draw can be fixed once previous rounds have finished.

Observed results are replayed deterministically and only the unresolved outcomes are sampled. There is no rejection-sampling penalty for observing an unlikely match result. Calls with observed results require an explicit updated `as_of_utc`, and reject impossible chronology, wrong record groups, repeated participants, rematches without an official exception, and observations that have exactly zero probability under the supplied matrix.

Once all 33 Swiss results are known, `quarterfinal_bracket` fixes four ordered matches: matches 0/1 occupy one half and matches 2/3 the other. The simulator verifies actual qualifiers, 3–0 versus 3–2 pairings, and opposite-half placement. It accepts either left/right orientation and either match position within a half.

`known_knockout` accepts `stage`, `a`, `b`, and `winner`, with stages `quarterfinal`, `semifinal`, and `final`. It supports a partly completed current stage. All four quarterfinal results are required before an observed semifinal; both semifinal results are required before an observed final. Known pairings and outcomes are never redrawn. When the whole event is known, zero matches remain sampled and the actual champion has conditional probability one.

The probability matrix is not automatically refitted from newly observed results: the data/model pipeline remains responsible for that update. The simulator computes future probabilities conditional on the supplied updated matrix and fixed observations.

## Optional within-series correlation sensitivity

`series_correlation=0` preserves the IID baseline exactly. For an explicitly supplied positive rho, a series has a latent single-game probability drawn from a Beta distribution with mean p and concentration kappa = 1/rho − 1. Its kth moment is the product of (kappa*p + j)/(kappa + j) for j from 0 through k−1. Substituting these moments into the BO3/BO5 polynomials integrates series-win probabilities analytically.

This sensitivity represents shared form within one series. The latent value resets for every new series, so it does not simulate persistent tournament-wide form, draft-specific dependence, or adaptation. Rho is a declared assumption, not a fitted parameter. The numerical checks cover complementary probabilities, the equal-strength value, endpoints, and the limit where perfect within-series correlation reduces the series-win probability to the single-game probability.
