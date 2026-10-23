"""Reproducible, vectorized Worlds 2026 Swiss/knockout simulations.

The default information cutoff is 2026-10-23 07:25:50 UTC.  Swiss starts at
17:00 UTC that day: the supplied opening draw is fixed, but no Swiss result
is conditioned on.  This module does not fetch data or estimate strengths.

``next_available_slot`` operationalizes the published draw language, rather
than claiming access to an unpublished tournament-operations algorithm:
draw a uniformly random permutation of each record group; put each drawn
team in the earliest slot compatible with a complete, rematch-free draw.
All permutations are enumerated for these small groups, so their induced
matching probabilities are exact *for this operationalization*.  They need
not be uniform over legal matchings.  ``uniform_valid`` is an explicit
alternative for draw-mechanism sensitivity analysis.

By default BO3 and BO5 probabilities assume independent games with a constant p1.
An optional Beta-mixture correlation parameter provides a sensitivity case.
First Selection, Fearless Draft, within-series adaptation, match-day form,
and parameter uncertainty are not separately estimated here.  Use alternate
p1 matrices for external sensitivity scenarios; simulation error is not
model uncertainty.  Detailed rule provenance lives in ../data/rules.json.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from itertools import combinations, permutations
from math import factorial
from time import perf_counter
from typing import Any

import numpy as np


CUTOFF_UTC = "2026-10-23T07:25:50Z"
SWISS_START_UTC = "2026-10-23T17:00:00Z"
RECORD_LABELS = ("3-0", "3-1", "3-2", "2-3", "1-3", "0-3")
DRAW_METHODS = ("next_available_slot", "uniform_valid")
_BITS = (np.uint32(1) << np.arange(16, dtype=np.uint32)).astype(np.uint16)


def series_probability(
    p: np.ndarray | float, best_of: int, correlation: float = 0.0,
) -> np.ndarray:
    """IID baseline or a Beta-mixture sensitivity with intraclass correlation.

    Positive correlation draws a latent win probability afresh per series;
    games are independent conditional on it. This does not introduce
    between-series momentum or estimate champion-pool/draft dynamics.
    """
    p = np.asarray(p, dtype=float)
    if not np.isfinite(correlation) or not 0 <= correlation < 1:
        raise ValueError("series_correlation must be in [0,1).")
    if best_of == 1:
        return p.copy()
    if best_of not in (3, 5):
        raise ValueError("Only BO1, BO3, and BO5 are supported.")
    if correlation:
        kappa = 1 / correlation - 1
        alpha = kappa * p
        moments = [np.ones_like(p)]
        for j in range(best_of):
            moments.append(moments[-1] * (alpha + j) / (kappa + j))
        result = (3 * moments[2] - 2 * moments[3]) if best_of == 3 else (
            10 * moments[3] - 15 * moments[4] + 6 * moments[5]
        )
        return np.clip(result, 0, 1)
    if best_of == 3:
        return 3 * p**2 - 2 * p**3
    return 10 * p**3 - 15 * p**4 + 6 * p**5


def _matchings(vertices: tuple[int, ...]):
    if not vertices:
        yield ()
        return
    a = vertices[0]
    for position in range(1, len(vertices)):
        b = vertices[position]
        rest = vertices[1:position] + vertices[position + 1 :]
        for matching in _matchings(rest):
            yield ((a, b),) + matching


@lru_cache(maxsize=4)
def _catalog(size: int) -> tuple[np.ndarray, np.ndarray, tuple]:
    """All unordered perfect matchings and compact edge masks for a group."""
    if size not in (2, 4, 6, 8):
        raise ValueError("Swiss record groups must contain 2, 4, 6, or 8 teams.")
    edges = tuple(combinations(range(size), 2))
    edge_index = {edge: index for index, edge in enumerate(edges)}
    matches = np.asarray(list(_matchings(tuple(range(size)))), dtype=np.int8)
    masks = np.zeros(len(matches), dtype=np.uint32)
    for i, matching in enumerate(matches):
        for a, b in matching:
            masks[i] |= np.uint32(1 << edge_index[(int(a), int(b))])
    return matches, masks, edges


@lru_cache(maxsize=4)
def _permutation_preferences(size: int) -> np.ndarray:
    """Order complete matchings by earliest feasible placement of drawn balls.

    For a given ball permutation, each matching has a unique placement:
    pairs are numbered by the time their first endpoint was drawn.  The
    endpoints take the first and second slots of their pair in draw order.
    Lexicographically minimizing slot numbers in ball-draw order is exactly
    greedy earliest-slot placement with a full-completion feasibility check.
    A precomputed ordering lets each forbidden graph reuse this computation.
    """
    catalog, _, _ = _catalog(size)
    orders = np.asarray(list(permutations(range(size))), dtype=np.int8)
    draw_rank = np.argsort(orders, axis=1)
    row = np.arange(len(orders))[:, None]
    codes = np.empty((len(orders), len(catalog)), dtype=np.uint32)
    for index, pairs in enumerate(catalog):
        ranks = draw_rank[:, pairs]
        first_rank = ranks.min(axis=2)
        pair_slot = np.argsort(np.argsort(first_rank, axis=1), axis=1) * 2
        positions = np.empty((len(orders), size), dtype=np.uint32)
        positions[row, ranks[:, :, 0]] = pair_slot + (ranks[:, :, 0] > ranks[:, :, 1])
        positions[row, ranks[:, :, 1]] = pair_slot + (ranks[:, :, 1] > ranks[:, :, 0])
        code = np.zeros(len(orders), dtype=np.uint32)
        for column in range(size):
            code = code * size + positions[:, column]
        codes[:, index] = code
    # At most 105 matchings (size=8); uint8 stores their indices exactly.
    return np.argsort(codes, axis=1).astype(np.uint8)


@lru_cache(maxsize=65536)
def _draw_cdf(size: int, forbidden_mask: int, method: str) -> tuple[np.ndarray, bool]:
    """Cached exact categorical distribution for the chosen draw assumption."""
    _, edge_masks, _ = _catalog(size)
    valid = (edge_masks & np.uint32(forbidden_mask)) == 0
    waiver = not bool(valid.any())
    if waiver:
        # The rules permit officials to waive rematch avoidance if no draw
        # exists.  Their discretionary choice is unknown; this fallback is
        # explicitly counted and uses an unrestricted random draw.
        valid = np.ones_like(valid)
    if method == "uniform_valid" or forbidden_mask == 0 or waiver:
        weights = valid.astype(float)
    elif method == "next_available_slot":
        preferences = _permutation_preferences(size)
        remaining = np.arange(len(preferences))
        weights = np.zeros(len(valid), dtype=np.int64)
        for priority in range(len(valid)):
            candidates = preferences[remaining, priority]
            accepted = valid[candidates]
            weights += np.bincount(candidates[accepted], minlength=len(valid))
            remaining = remaining[~accepted]
            if not len(remaining):
                break
        if len(remaining) or int(weights.sum()) != factorial(size):
            raise RuntimeError("Permutation-to-draw distribution is incomplete.")
    else:
        raise ValueError(f"draw_method must be one of {DRAW_METHODS}.")
    cdf = np.cumsum(weights, dtype=float)
    cdf /= cdf[-1]
    cdf[-1] = 1.0
    return cdf, waiver


def draw_distribution(
    size: int,
    forbidden_pairs: list[tuple[int, int]],
    draw_method: str = "next_available_slot",
) -> dict[str, Any]:
    """Inspectable matching probabilities; local team indices are 0..size-1."""
    if draw_method not in DRAW_METHODS:
        raise ValueError(f"draw_method must be one of {DRAW_METHODS}.")
    matches, _, edges = _catalog(size)
    edge_index = {edge: index for index, edge in enumerate(edges)}
    forbidden = 0
    for a, b in forbidden_pairs:
        pair = tuple(sorted((a, b)))
        if pair not in edge_index:
            raise ValueError(f"Invalid forbidden pair: {pair}.")
        forbidden |= 1 << edge_index[pair]
    cdf, waiver = _draw_cdf(size, forbidden, draw_method)
    return {
        "matchings": matches.copy(),
        "probabilities": np.diff(np.r_[0.0, cdf]),
        "repeat_waiver_required": waiver,
        "draw_method": draw_method,
    }


def _draw_groups(
    members: np.ndarray,
    history: np.ndarray,
    rng: np.random.Generator,
    method: str,
) -> tuple[np.ndarray, np.ndarray]:
    n, size = members.shape
    catalog, _, edges = _catalog(size)
    row = np.arange(n)
    forbidden = np.zeros(n, dtype=np.uint32)
    for bit, (a, b) in enumerate(edges):
        prior = (history[row, members[:, a]] & _BITS[members[:, b]]) != 0
        forbidden |= prior.astype(np.uint32) << np.uint32(bit)
    unique_masks, inverse = np.unique(forbidden, return_inverse=True)
    cdfs = np.empty((len(unique_masks), len(catalog)), dtype=float)
    waivers = np.empty(len(unique_masks), dtype=bool)
    for i, mask in enumerate(unique_masks):
        cdfs[i], waivers[i] = _draw_cdf(size, int(mask), method)
    # Limit temporary memory for large runs; one random variate per group.
    uniform = rng.random(n)
    chosen = np.empty(n, dtype=np.int16)
    chunk_size = 32768
    for start in range(0, n, chunk_size):
        stop = min(start + chunk_size, n)
        chosen[start:stop] = np.sum(
            uniform[start:stop, None] >= cdfs[inverse[start:stop]], axis=1
        )
    local_pairs = catalog[chosen]
    matches = members[np.arange(n)[:, None, None], local_pairs]
    return matches, waivers[inverse]


def _validate_inputs(teams, first_round, p1, n, keep_examples):
    if len(teams) != 16 or len(set(teams)) != 16:
        raise ValueError("Exactly 16 uniquely named Swiss teams are required.")
    if not isinstance(n, (int, np.integer)) or n <= 0:
        raise ValueError("n must be a positive integer.")
    if not isinstance(keep_examples, (int, np.integer)) or keep_examples < 0:
        raise ValueError("keep_examples must be a nonnegative integer.")
    if len(first_round) != 8 or any(len(pair) != 2 for pair in first_round):
        raise ValueError("first_round must contain eight two-team matches.")
    flattened = [team for pair in first_round for team in pair]
    if set(flattened) != set(teams) or len(set(flattened)) != 16:
        raise ValueError("Every team must occur exactly once in the opening draw.")
    p1 = np.asarray(p1, dtype=float)
    if p1.shape != (16, 16):
        raise ValueError("p1 must be a 16-by-16 single-game win-probability matrix.")
    offdiag = ~np.eye(16, dtype=bool)
    if not np.isfinite(p1[offdiag]).all() or np.any((p1[offdiag] < 0) | (p1[offdiag] > 1)):
        raise ValueError("Off-diagonal p1 values must be finite probabilities.")
    if not np.allclose((p1 + p1.T)[offdiag], 1.0, atol=1e-10, rtol=0):
        raise ValueError("p1[A,B] + p1[B,A] must equal one.")
    p1 = p1.copy()
    np.fill_diagonal(p1, 0.5)  # Diagonal is never used; tolerate an input NaN diagonal.
    lookup = {team: index for index, team in enumerate(teams)}
    first = np.array([[lookup[a], lookup[b]] for a, b in first_round], dtype=np.int8)
    return p1, first


def _prepare_updates(teams, first, known_matches, announced_rounds, as_of_utc):
    """Normalize sequential Swiss observations without conditioning by rejection."""
    known_matches = list(known_matches or [])
    lookup = {team: index for index, team in enumerate(teams)}
    expected_counts = {1: 8, 2: 8, 3: 8, 4: 6, 5: 3}
    observed = {r: {} for r in expected_counts}
    announced = {1: np.asarray(first, dtype=np.int8)}
    if known_matches and as_of_utc is None:
        raise ValueError("Observed results require an explicit updated as_of_utc cutoff.")
    cutoff = as_of_utc or CUTOFF_UTC
    parsed = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of_utc must include a timezone.")
    parsed = parsed.astimezone(timezone.utc)
    start = datetime.fromisoformat(SWISS_START_UTC.replace("Z", "+00:00"))
    if known_matches and parsed < start:
        raise ValueError("Swiss observations cannot precede the scheduled Swiss start.")
    cutoff = parsed.isoformat().replace("+00:00", "Z")

    def pair_key(a, b):
        return tuple(sorted((int(a), int(b))))

    for record in known_matches:
        if record.get("stage", "swiss") != "swiss":
            raise ValueError("Update support currently covers Swiss results only.")
        r = int(record["round"])
        if r not in observed:
            raise ValueError("Known Swiss rounds must be numbered 1 through 5.")
        try:
            a, b, winner = lookup[record["a"]], lookup[record["b"]], lookup[record["winner"]]
        except KeyError as exc:
            raise ValueError(f"Unknown team or missing known-match field: {exc}.") from exc
        if a == b or winner not in (a, b):
            raise ValueError("Known winner must be one of two distinct opponents.")
        key = pair_key(a, b)
        if key in observed[r]:
            raise ValueError("Duplicate known match; resolve source conflicts before simulating.")
        observed[r][key] = winner

    for raw_round, pairings in (announced_rounds or {}).items():
        r = int(raw_round)
        if r not in expected_counts:
            raise ValueError("Announced Swiss rounds must be numbered 1 through 5.")
        try:
            indexed = np.array([[lookup[a], lookup[b]] for a, b in pairings], dtype=np.int8)
        except (KeyError, ValueError) as exc:
            raise ValueError("Malformed announced pairing or unknown team.") from exc
        if r == 1 and {pair_key(*p) for p in indexed} != {pair_key(*p) for p in first}:
            raise ValueError("Announced round 1 conflicts with first_round.")
        announced[r] = indexed

    # A complete observed round itself supplies its full actual pairing list.
    for r in expected_counts:
        if len(observed[r]) > expected_counts[r]:
            raise ValueError(f"Too many observed matches in Swiss round {r}.")
        if r not in announced and len(observed[r]) == expected_counts[r]:
            announced[r] = np.array(list(observed[r]), dtype=np.int8)
        if observed[r] and r not in announced:
            raise ValueError("A partially completed later round requires its complete announced pairings.")
        if r in announced:
            indexed = announced[r]
            if indexed.shape != (expected_counts[r], 2):
                raise ValueError(f"Round {r} requires all {expected_counts[r]} announced matches.")
            if len(np.unique(indexed)) != indexed.size:
                raise ValueError(f"A team appears more than once in announced round {r}.")
            legal_keys = {pair_key(*pair) for pair in indexed}
            if not set(observed[r]).issubset(legal_keys):
                raise ValueError(f"Observed results conflict with the announced round {r} draw.")
    completed = 0
    for r in expected_counts:
        if len(observed[r]) == expected_counts[r]:
            completed = r
        else:
            break
    supplied_rounds = {r for r in observed if observed[r]} | set(announced)
    if any(r > completed + 1 for r in supplied_rounds):
        raise ValueError("Known results/draws require a contiguous completed-round prefix.")
    return observed, announced, completed, cutoff


def _prepare_knockout_updates(teams, completed_rounds, bracket, known, cutoff):
    lookup = {team: index for index, team in enumerate(teams)}
    observed = {stage: {} for stage in ("quarterfinal", "semifinal", "final")}
    known = list(known or [])
    if (bracket is not None or known) and completed_rounds != 5:
        raise ValueError("A fixed knockout draw or result requires all 33 Swiss results.")
    if known and bracket is None:
        raise ValueError("Known knockout results require the complete official quarterfinal bracket.")
    indexed = None
    if bracket is not None:
        try:
            indexed = np.asarray([[lookup[a], lookup[b]] for a, b in bracket], dtype=np.int8)
        except (KeyError, ValueError) as exc:
            raise ValueError("Malformed quarterfinal bracket or unknown team.") from exc
        if indexed.shape != (4, 2) or len(np.unique(indexed)) != 8:
            raise ValueError("quarterfinal_bracket must contain four matches with eight distinct teams.")
    phase_starts = {
        "quarterfinal": "2026-11-03T22:00:00Z",
        "semifinal": "2026-11-07T22:00:00Z",
        "final": "2026-11-14T19:00:00Z",
    }
    parsed_cutoff = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    aliases = {"qf": "quarterfinal", "sf": "semifinal", "f": "final"}
    for record in known:
        stage = str(record["stage"]).lower()
        stage = aliases.get(stage, stage)
        if stage not in observed:
            raise ValueError("Known knockout stage must be quarterfinal, semifinal, or final.")
        if parsed_cutoff < datetime.fromisoformat(phase_starts[stage].replace("Z", "+00:00")):
            raise ValueError(f"Observed {stage} results precede that stage's scheduled start.")
        try:
            a, b, winner = lookup[record["a"]], lookup[record["b"]], lookup[record["winner"]]
        except KeyError as exc:
            raise ValueError("Unknown knockout team or missing match field.") from exc
        if a == b or winner not in (a, b):
            raise ValueError("Known knockout winner must be one of two distinct opponents.")
        key = tuple(sorted((a, b)))
        if key in observed[stage]:
            raise ValueError("Duplicate known knockout match.")
        observed[stage][key] = winner
    if any(len(observed[stage]) > count for stage, count in zip(observed, (4, 2, 1))):
        raise ValueError("Too many results for a knockout stage.")
    if observed["semifinal"] and len(observed["quarterfinal"]) != 4:
        raise ValueError("Known semifinals require all four quarterfinal results.")
    if observed["final"] and len(observed["semifinal"]) != 2:
        raise ValueError("A known final requires both semifinal results.")
    return indexed, observed


def simulate(
    teams: list[str],
    first_round: list[tuple[str, str]],
    p1: np.ndarray,
    n: int,
    seed: int,
    draw_method: str = "next_available_slot",
    keep_examples: int = 3,
    known_matches: list[dict[str, Any]] | None = None,
    announced_rounds: dict[int, list[tuple[str, str]]] | None = None,
    as_of_utc: str | None = None,
    quarterfinal_bracket: list[tuple[str, str]] | None = None,
    known_knockout: list[dict[str, Any]] | None = None,
    series_correlation: float = 0.0,
) -> dict[str, Any]:
    """Simulate the future tournament conditional on its known opening draw.

    Array axes always follow ``teams``. Returned probability units are 0..1.
    ``round2.conditional_*`` has shape (2,16,16), with 0=opening loss and
    1=opening win; impossible conditioning events return NaN (JSON null via
    ``to_jsonable``). ``final_pairs`` uses the upper triangle for unordered
    finalists, so its counts sum to n, not 2*n. ``examples`` are random
    illustrative realizations, never a claim about the single likeliest path.

    ``mc_standard_errors`` describe conditional Monte Carlo sampling error
    only. Re-run with alternative p1 inputs/model assumptions for sensitivity.
    All raw counts are returned, permitting aggregation of independent runs.

    Updates: ``known_matches=[{'round':1,'a':'GEN','b':'IG','winner':'GEN'}]``
    fixes observed Swiss results. Completed rounds must be a contiguous
    prefix; a partially completed later round must have its entire draw in
    ``announced_rounds={2:[('GEN','T1'), ...]}``. Complete observed rounds
    supply their own pairings automatically. An announced next complete
    round is also supported. Supply an explicit updated ``as_of_utc`` when
    observations are present. Known results are replayed deterministically,
    not sampled or accepted by rejection. Once all Swiss results are fixed,
    ``quarterfinal_bracket`` fixes four ordered matches (0/1 in one half,
    2/3 in the other). ``known_knockout`` takes stage/a/b/winner records;
    stages are quarterfinal, semifinal, final. Completed prior stages are
    required before later observed stages. Re-estimating p1 is external.

    Optional ``series_correlation`` supplies a Beta-binomial sensitivity:
    kappa=1/rho-1, alpha=kappa*p. A fresh latent probability is drawn for
    each series. rho=0 exactly preserves the IID baseline; rho is not fitted.
    """
    started = perf_counter()
    if draw_method not in DRAW_METHODS:
        raise ValueError(f"draw_method must be one of {DRAW_METHODS}.")
    p1, first = _validate_inputs(teams, first_round, p1, n, keep_examples)
    observed, announced, completed_rounds, cutoff = _prepare_updates(
        teams, first, known_matches, announced_rounds, as_of_utc
    )
    fixed_bracket, knockout_observed = _prepare_knockout_updates(
        teams, completed_rounds, quarterfinal_bracket, known_knockout, cutoff
    )
    rng = np.random.default_rng(seed)
    series_p = {best_of: series_probability(p1, best_of, series_correlation) for best_of in (1, 3, 5)}
    wins = np.zeros((n, 16), dtype=np.int8)
    losses = np.zeros((n, 16), dtype=np.int8)
    history = np.zeros((n, 16), dtype=np.uint16)
    row = np.arange(n)[:, None]
    n_examples = min(int(keep_examples), n)
    examples = [
        {"simulation_index": i, "kind": "random_illustrative_realization", "stages": []}
        for i in range(n_examples)
    ]
    checks = {
        "wrong_record_pairings": 0,
        "swiss_repeat_matches": 0,
        "repeat_waiver_group_count": 0,
        "swiss_matches": 0,
        "knockout_matches": 0,
        "fixed_swiss_match_instances": 0,
        "sampled_swiss_matches": 0,
        "fixed_knockout_match_instances": 0,
        "sampled_knockout_matches": 0,
    }

    def play(matches, best_of, stage, round_number, record_before=None):
        a, b = matches[:, :, 0], matches[:, :, 1]
        probability = series_p[best_of][a, b]
        fixed = np.full(a.shape[1], -1, dtype=np.int8)
        stage_observations = observed[round_number] if stage == "swiss" else knockout_observed[stage]
        if stage_observations:
            if not np.all(matches == matches[0]):
                raise RuntimeError("Observed match pairings must be fixed, not random across simulations.")
            if stage != "swiss" and not set(stage_observations).issubset(
                {tuple(sorted(map(int, pair))) for pair in matches[0]}
            ):
                raise ValueError("Observed knockout results conflict with the fixed bracket or earlier winners.")
            for j, pair in enumerate(matches[0]):
                fixed[j] = stage_observations.get(tuple(sorted(map(int, pair))), -1)
        sampled = fixed < 0
        a_wins = np.empty(a.shape, dtype=bool)
        a_wins[:, sampled] = rng.random((n, int(sampled.sum()))) < probability[:, sampled]
        for j in np.flatnonzero(~sampled):
            observed_a_win = a[0, j] == fixed[j]
            observed_probability = probability[:, j] if observed_a_win else 1 - probability[:, j]
            if np.any(observed_probability <= 0):
                raise ValueError("An observed outcome has zero probability under p1; revise the model input.")
            a_wins[:, j] = observed_a_win
        winner = np.where(a_wins, a, b)
        loser = np.where(a_wins, b, a)
        if stage == "swiss":
            checks["wrong_record_pairings"] += int(np.count_nonzero(
                (wins[row, a] != wins[row, b]) | (losses[row, a] != losses[row, b])
            ))
            checks["swiss_repeat_matches"] += int(np.count_nonzero(
                history[row, a] & _BITS[b]
            ))
            wins[row, winner] += 1
            losses[row, loser] += 1
            history[row, a] |= _BITS[b]
            history[row, b] |= _BITS[a]
            checks["swiss_matches"] += int(a.size)
            checks["fixed_swiss_match_instances"] += int((~sampled).sum()) * n
            checks["sampled_swiss_matches"] += int(sampled.sum()) * n
        else:
            checks["knockout_matches"] += int(a.size)
            checks["fixed_knockout_match_instances"] += int((~sampled).sum()) * n
            checks["sampled_knockout_matches"] += int(sampled.sum()) * n
        for i in range(n_examples):
            examples[i]["stages"].append({
                "stage": stage,
                "round": round_number,
                "record_before": record_before,
                "best_of": best_of,
                "matches": [
                    {
                        "a": teams[int(a[i, j])],
                        "b": teams[int(b[i, j])],
                        "winner": teams[int(winner[i, j])],
                        "p_a_single_game": float(p1[a[i, j], b[i, j]]),
                        "p_a_series": float(probability[i, j]),
                        "status": "observed" if fixed[j] >= 0 else "simulated",
                    }
                    for j in range(a.shape[1])
                ],
            })
        return winner

    opening = np.broadcast_to(first, (n, 8, 2))
    play(opening, 1, "swiss", 1, "0-0")
    first_result = wins.copy()
    first_win_counts = first_result.sum(axis=0, dtype=np.int64)
    round2_counts = np.zeros((16, 16), dtype=np.int64)
    round2_conditional = np.zeros((2, 16, 16), dtype=np.int64)

    # (round, wins, losses, group size, BO). Group sizes are invariants of Swiss.
    groups = (
        (2, 1, 0, 8, 1), (2, 0, 1, 8, 1),
        (3, 2, 0, 4, 3), (3, 1, 1, 8, 1), (3, 0, 2, 4, 3),
        (4, 2, 1, 6, 3), (4, 1, 2, 6, 3),
        (5, 2, 2, 6, 3),
    )
    for round_number, w, l, size, best_of in groups:
        eligible = (wins == w) & (losses == l)
        if not np.all(eligible.sum(axis=1) == size):
            raise RuntimeError(f"Unexpected group size at round {round_number}, {w}-{l}.")
        members = np.nonzero(eligible)[1].reshape(n, size)
        if round_number in announced:
            if not np.all(wins == wins[0]) or not np.all(losses == losses[0]):
                # Earlier groups of this same announced round may already
                # contain simulated results. Only the current pre-round
                # record-group membership must nevertheless remain fixed.
                if not np.all(members == members[0]):
                    raise ValueError("An announced draw cannot follow unresolved prior rounds.")
            all_pairs = announced[round_number]
            selected = all_pairs[
                (wins[0, all_pairs[:, 0]] == w) & (losses[0, all_pairs[:, 0]] == l)
            ]
            if selected.shape != (size // 2, 2) or set(selected.ravel()) != set(members[0]):
                raise ValueError(f"Announced round {round_number} violates record groups or active-team membership.")
            matches = np.broadcast_to(selected, (n, size // 2, 2))
            if np.any(history[row, matches[:, :, 0]] & _BITS[matches[:, :, 1]]):
                raise ValueError("Announced Swiss rematch needs an explicit official waiver; not silently accepted.")
            waiver = np.zeros(n, dtype=bool)
        else:
            matches, waiver = _draw_groups(members, history, rng, draw_method)
        checks["repeat_waiver_group_count"] += int(waiver.sum())
        if round_number == 2:
            a = matches[:, :, 0].astype(np.int64)
            b = matches[:, :, 1].astype(np.int64)
            counts = (
                np.bincount((a * 16 + b).ravel(), minlength=256)
                + np.bincount((b * 16 + a).ravel(), minlength=256)
            ).reshape(16, 16)
            round2_counts += counts
            round2_conditional[w] += counts
        play(matches, best_of, "swiss", round_number, f"{w}-{l}")

    qualified = wins == 3
    record_counts = np.stack([
        ((wins == w) & (losses == l)).sum(axis=0, dtype=np.int64)
        for w, l in ((3, 0), (3, 1), (3, 2), (2, 3), (1, 3), (0, 3))
    ], axis=1)

    def record_members(w, l, count):
        members = np.nonzero((wins == w) & (losses == l))[1].reshape(n, count)
        return members

    def shuffled(members):
        # Independent continuous keys induce uniform permutations in each row.
        order = np.argsort(rng.random(members.shape), axis=1)
        return np.take_along_axis(members, order, axis=1)

    if fixed_bracket is None:
        three_zero = shuffled(record_members(3, 0, 2))
        three_two = shuffled(record_members(3, 2, 3))
        rest = shuffled(np.column_stack((record_members(3, 1, 3), three_two[:, 2])))
        quarterfinals = np.empty((n, 4, 2), dtype=np.int8)
        # QF0/QF1 comprise one half; QF2/QF3 comprise the other.
        quarterfinals[:, 0, :] = np.column_stack((three_zero[:, 0], three_two[:, 0]))
        quarterfinals[:, 1, :] = rest[:, :2]
        quarterfinals[:, 2, :] = np.column_stack((three_zero[:, 1], three_two[:, 1]))
        quarterfinals[:, 3, :] = rest[:, 2:]
    else:
        if set(fixed_bracket.ravel()) != set(np.nonzero(qualified[0])[0]):
            raise ValueError("Fixed quarterfinal participants do not equal the eight known Swiss qualifiers.")
        qf_losses = losses[0, fixed_bracket]
        seed_matches = np.any(qf_losses == 0, axis=1)
        if (
            int(seed_matches.sum()) != 2
            or not np.all(np.sort(qf_losses[seed_matches], axis=1) == [0, 2])
            or int(seed_matches[:2].sum()) != 1 or int(seed_matches[2:].sum()) != 1
        ):
            raise ValueError("Fixed bracket violates 3-0 versus 3-2 or opposite-half requirements.")
        quarterfinals = np.broadcast_to(fixed_bracket, (n, 4, 2))
    qf_winners = play(quarterfinals, 5, "quarterfinal", 1)
    semifinalists = np.bincount(qf_winners.ravel(), minlength=16)
    finalists = play(qf_winners.reshape(n, 2, 2), 5, "semifinal", 2)
    finalist_counts = np.bincount(finalists.ravel(), minlength=16)
    champions = play(finalists.reshape(n, 1, 2), 5, "final", 3).ravel()
    champion_counts = np.bincount(champions, minlength=16)
    final_low = finalists.min(axis=1).astype(np.int64)
    final_high = finalists.max(axis=1).astype(np.int64)
    final_counts = np.bincount(final_low * 16 + final_high, minlength=256).reshape(16, 16)
    qualified_counts = qualified.sum(axis=0, dtype=np.int64)
    counts = {
        "champion": champion_counts,
        "finalist": finalist_counts,
        "semifinalist": semifinalists,
        "qualified": qualified_counts,
        "first_round_win": first_win_counts,
    }
    probabilities = {key: value / n for key, value in counts.items()}
    mc_standard_errors = {
        key: np.sqrt(value * (1 - value) / n)
        for key, value in probabilities.items()
    }
    conditional_denominators = np.stack((n - first_win_counts, first_win_counts))
    conditional_probabilities = np.full((2, 16, 16), np.nan)
    np.divide(
        round2_conditional, conditional_denominators[:, :, None],
        out=conditional_probabilities,
        where=conditional_denominators[:, :, None] != 0,
    )
    for i in range(n_examples):
        examples[i]["swiss_final_records"] = {
            team: f"{wins[i, j]}-{losses[i, j]}" for j, team in enumerate(teams)
        }
        examples[i]["quarterfinal_bracket"] = [
            {"half": j // 2, "teams": [teams[int(a)], teams[int(b)]]}
            for j, (a, b) in enumerate(quarterfinals[i])
        ]
        examples[i]["champion"] = teams[int(champions[i])]

    qf_flat = quarterfinals.reshape(n, 8)
    checks.update({
        "terminal_record_errors": int(np.count_nonzero((wins == 3) == (losses == 3))),
        "qualified_per_tournament_min": int(qualified.sum(axis=1).min()),
        "qualified_per_tournament_max": int(qualified.sum(axis=1).max()),
        "quarterfinal_duplicate_team_errors": int(np.count_nonzero(
            np.any(np.diff(np.sort(qf_flat, axis=1), axis=1) == 0, axis=1)
        )),
        "three_zero_vs_three_two_errors": int(np.count_nonzero(
            np.any(losses[np.arange(n)[:, None, None], quarterfinals] == 0, axis=2)
            & ~np.all(np.sort(losses[np.arange(n)[:, None, None], quarterfinals], axis=2) == [0, 2], axis=2)
        )),
        "three_zero_same_half_errors": int(np.count_nonzero(
            ((losses[row, qf_flat[:, :4]] == 0).sum(axis=1) != 1)
            | ((losses[row, qf_flat[:, 4:]] == 0).sum(axis=1) != 1)
        )),
        "champion_count_total": int(champion_counts.sum()),
        "finalist_count_total": int(finalist_counts.sum()),
        "semifinalist_count_total": int(semifinalists.sum()),
        "qualified_count_total": int(qualified_counts.sum()),
        "final_pair_count_total": int(final_counts.sum()),
        "record_count_totals": record_counts.sum(axis=0),
        "round2_row_total_errors": int(np.count_nonzero(round2_counts.sum(axis=1) != n)),
        "round2_conditioning_total_errors": int(np.count_nonzero(
            round2_conditional.sum(axis=2) != conditional_denominators
        )),
        "draw_cache": dict(_draw_cdf.cache_info()._asdict()),
    })
    required_zeros = (
        "wrong_record_pairings", "terminal_record_errors", "quarterfinal_duplicate_team_errors",
        "three_zero_vs_three_two_errors", "three_zero_same_half_errors",
        "round2_row_total_errors", "round2_conditioning_total_errors",
    )
    if any(checks[key] for key in required_zeros):
        raise RuntimeError(f"Tournament invariant failed: {checks}")
    if checks["swiss_repeat_matches"] and not checks["repeat_waiver_group_count"]:
        raise RuntimeError("An unwaived Swiss rematch occurred.")
    if checks["swiss_matches"] != 33 * n or checks["knockout_matches"] != 7 * n:
        raise RuntimeError("Incorrect tournament match count.")
    if not np.array_equal(record_counts.sum(axis=0), n * np.array([2, 3, 3, 3, 3, 2])):
        raise RuntimeError("Incorrect Swiss terminal-record distribution.")
    return {
        "teams": list(teams), "n": int(n), "seed": int(seed), "draw_method": draw_method,
        "series_correlation": float(series_correlation),
        "cutoff_utc": cutoff, "swiss_start_utc": SWISS_START_UTC,
        "updates": {
            "known_swiss_match_count": sum(map(len, observed.values())),
            "completed_swiss_rounds": completed_rounds,
            "announced_rounds": sorted(announced),
            "known_knockout_match_count": sum(map(len, knockout_observed.values())),
            "quarterfinal_bracket_fixed": fixed_bracket is not None,
            "scope": "Swiss prefix/current draw; fixed full quarterfinal bracket after Swiss; sequential knockout-stage observations",
            "p1_updated_from_observations_here": False,
        },
        "counts": counts, "probabilities": probabilities,
        "mc_standard_errors": mc_standard_errors,
        "records": {
            "labels": list(RECORD_LABELS), "counts": record_counts,
            "probabilities": record_counts / n,
        },
        "round2": {
            "opponent_counts": round2_counts, "opponent_probabilities": round2_counts / n,
            "condition_labels": ["opening_loss", "opening_win"],
            "conditional_denominators": conditional_denominators,
            "conditional_counts": round2_conditional,
            "conditional_probabilities": conditional_probabilities,
        },
        "final_pairs": {"counts": final_counts, "probabilities": final_counts / n,
                        "orientation": "unordered_pairs_in_upper_triangle"},
        "examples": examples, "checks": checks,
        "assumptions": {
            "draw": "Published slot-advance language operationalized; not claimed as complete official algorithm."
            if draw_method == "next_available_slot" else "Uniform legal perfect matchings; sensitivity alternative, not official draw claim.",
            "series": "IID constant single-game probability; no separately fitted draft, side, or adaptation effect."
            if not series_correlation else "Beta-mixture per-series probability; supplied within-series correlation, no fitted side or draft effects; latent form resets each series.",
            "repeat_waiver": "If no legal perfect matching exists, simulate unrestricted draw and count the discretionary-rule fallback.",
            "uncertainty": "Reported standard errors reflect Monte Carlo error conditional on p1, not model/data uncertainty.",
            "examples": "Random realizations; these are not most-probable brackets or a fixed future schedule.",
        },
        "elapsed_seconds": perf_counter() - started,
    }


def to_jsonable(value: Any) -> Any:
    """Convert returned arrays/scalars and undefined conditional probabilities."""
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    if isinstance(value, np.generic):
        return to_jsonable(value.item())
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value
