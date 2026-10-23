#!/usr/bin/env python3
"""Extract observed 2026 five-player lineups, without changing the fitted model.

Inputs are frozen public ChainCC team/player snapshots and the primary model's
existing observation table. Current reported Worlds rosters are used ONLY for
descriptive comparisons; they are never assigned to a historical game.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


SOURCE_URL = "https://chaincc.lol/data/chaincc-players-2026.csv.gz"
ROLE_MAP = {"top": "top", "jng": "jungle", "mid": "mid", "bot": "bot", "sup": "support"}
ROLES = ("top", "jungle", "mid", "bot", "support")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def name_key(name: str) -> str:
    # No unverified player alias merges: only typography/case normalization.
    return " ".join(name.split()).casefold()


def roster_key(row: dict) -> tuple[str, ...]:
    return tuple(name_key(row[role]) for role in ROLES)


def raw_utc(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc
    ).isoformat().replace("+00:00", "Z")


def truth(value: str) -> int:
    if value.casefold() in {"true", "1", "1.0"}:
        return 1
    if value.casefold() in {"false", "0", "0.0"}:
        return 0
    raise ValueError(f"Unrecognized result: {value!r}")


def build(root: Path) -> dict:
    data, evidence = root / "data", root / "evidence"
    source = data / "history_chaincc_players_2026.csv.gz"
    inputs = [source, data / "history_games.csv", data / "model_observations.csv",
              data / "history_teams.csv", data / "teams.json", data / "eliminated_teams.json"]
    input_hashes = {str(p.relative_to(root)): sha256(p) for p in inputs}
    source_hash = input_hashes[str(source.relative_to(root))]
    history = read_csv(data / "history_games.csv")
    games = {g["game_id"]: g for g in history}
    if len(games) != len(history):
        raise ValueError("Historical game_id must be unique")
    observations = read_csv(data / "model_observations.csv")
    primary_ids = {r["game_id"] for r in observations if r.get("game_id")}
    if not primary_ids.issubset(games):
        raise ValueError("A primary model game_id is absent from frozen history")
    residuals = [r for r in observations if not r.get("game_id")]
    regions = {r["team"]: r["region"] for r in read_csv(data / "history_teams.csv")}
    worlds_teams = json.loads((data / "teams.json").read_text()) + json.loads(
        (data / "eliminated_teams.json").read_text()
    )
    reported = {}
    for team in worlds_teams:
        roster = {p["role"].lower(): p["player"] for p in team["roster"]}
        if set(roster) != set(ROLES):
            raise ValueError(f"Incomplete reported Worlds roster for {team['code']}")
        reported[team["code"]] = {
            "roster": roster,
            "sources": sorted({p["source"] for p in team["roster"]}),
            "status": team.get("status", "in_swiss_at_cutoff"),
            "as_of": team["as_of"],
        }

    selected: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    errors = []
    raw_count = selected_count = 0
    raw_roles = Counter()
    checks = Counter()
    with gzip.open(source, "rt", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw_count += 1
            raw_roles[row["position"]] += 1
            game = games.get(row["game_id"])
            if game is None:
                continue
            selected_count += 1
            game_id = row["game_id"]
            role = ROLE_MAP.get(row["position"])
            if role is None:
                errors.append({"game_id": game_id, "error": "unknown_role", "value": row["position"]})
                continue
            team_side = None
            for side in ("a", "b"):
                if row["team_name"] == game[f"team_{side}_name"]:
                    team_side = side
                    break
            if team_side is None:
                errors.append({"game_id": game_id, "error": "unknown_team", "value": row["team_name"]})
                continue
            opposite = "b" if team_side == "a" else "a"
            team_code = game[f"team_{team_side}"]
            expected = {
                "opponent_team_name": game[f"team_{opposite}_name"],
                "side": "Blue" if team_code == game["blue_team"] else "Red",
                "league": game["competition_raw"],
                "patch": game["patch"],
            }
            for key, value in expected.items():
                checks[key] += 1
                if row[key] != value:
                    errors.append({"game_id": game_id, "team": team_code, "error": key,
                                   "player_value": row[key], "team_value": value})
            for key, actual, expected_value in (
                ("date", raw_utc(row["date"]), game["date"]),
                ("result", truth(row["result"]), int(game[f"wins_{team_side}"])),
                ("gamelength", int(float(row["gamelength"])), int(game["gamelength_seconds"])),
            ):
                checks[key] += 1
                if actual != expected_value:
                    errors.append({"game_id": game_id, "team": team_code, "error": key,
                                   "player_value": actual, "team_value": expected_value})
            bucket = selected[(game_id, team_code)]
            if role in bucket:
                errors.append({"game_id": game_id, "team": team_code, "error": "duplicate_role", "role": role})
            bucket[role] = row

    rows = []
    for game in history:
        for side, opposite in (("a", "b"), ("b", "a")):
            team = game[f"team_{side}"]
            observed = selected.get((game["game_id"], team), {})
            if set(observed) != set(ROLES):
                errors.append({"game_id": game["game_id"], "team": team, "error": "incomplete_roles",
                               "observed": sorted(observed)})
            lineup = {role: observed.get(role, {}).get("playername", "") for role in ROLES}
            if len({name_key(v) for v in lineup.values()}) != 5 or not all(lineup.values()):
                errors.append({"game_id": game["game_id"], "team": team, "error": "nonunique_or_empty_player"})
            key = roster_key(lineup)
            rows.append({
                "game_id": game["game_id"], "series_id": game["series_id"], "date": game["date"],
                "competition": game["competition"], "event_scope": game["event_scope"], "split": game["split"],
                "team": team, "team_name": game[f"team_{side}_name"], "region": regions.get(team, ""),
                "opponent": game[f"team_{opposite}"],
                "side": "Blue" if team == game["blue_team"] else "Red", **lineup,
                "player_count": len(observed), "lineup_complete": int(set(observed) == set(ROLES)),
                "lineup_id": hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16],
                "is_primary_model_game": int(game["game_id"] in primary_ids),
                "source_url": SOURCE_URL, "source_snapshot_sha256": source_hash,
            })

    if errors:
        (evidence / "history_lineup_errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2) + "\n")
        raise ValueError(f"Historical lineup validation found {len(errors)} discrepancies; see history_lineup_errors.json")
    if selected_count != len(games) * 10 or len(rows) != len(games) * 2:
        raise ValueError("Player/game row counts do not reconcile")

    by_team = defaultdict(list)
    for row in rows:
        by_team[row["team"]].append(row)
    summary, comparison = [], []
    for team, team_rows in sorted(by_team.items()):
        team_rows.sort(key=lambda r: (r["date"], r["game_id"]))
        primary_rows = [r for r in team_rows if r["is_primary_model_game"]]
        latest = team_rows[-1]
        unique = {r["lineup_id"] for r in team_rows}
        primary_unique = {r["lineup_id"] for r in primary_rows}
        changed = sum(a["lineup_id"] != b["lineup_id"] for a, b in zip(team_rows, team_rows[1:]))
        record = {
            "team": team, "region": regions.get(team, ""), "observed_games": len(team_rows),
            "primary_games": len(primary_rows), "distinct_observed_lineups": len(unique),
            "distinct_primary_lineups": len(primary_unique), "adjacent_observed_lineup_changes": changed,
            "first_observed_utc": team_rows[0]["date"], "latest_observed_utc": latest["date"],
            "latest_game_id": latest["game_id"], "latest_competition": latest["competition"],
            **{f"latest_{role}": latest[role] for role in ROLES},
        }
        summary.append(record)
        if team in reported:
            current = reported[team]
            matching = [r for r in team_rows if roster_key(r) == roster_key(current["roster"])]
            primary_matching = [r for r in matching if r["is_primary_model_game"]]
            mismatched = [role for role in ROLES if name_key(latest[role]) != name_key(current["roster"][role])]
            comparison.append({
                **record, "worlds_status": current["status"],
                **{f"reported_worlds_{role}": current["roster"][role] for role in ROLES},
                "latest_matching_roles": len(ROLES) - len(mismatched),
                "latest_mismatching_roles": ";".join(mismatched),
                "observed_games_with_reported_five": len(matching),
                "primary_games_with_reported_five": len(primary_matching),
                "primary_fraction_with_reported_five": len(primary_matching) / len(primary_rows) if primary_rows else "",
                "first_observed_reported_five_utc": matching[0]["date"] if matching else "",
                "last_observed_reported_five_utc": matching[-1]["date"] if matching else "",
                "reported_roster_as_of": current["as_of"],
                "reported_roster_sources": " | ".join(current["sources"]),
                "use": "descriptive_only_not_a_historical_model_feature",
            })
    if set(r["team"] for r in comparison) != set(reported):
        raise ValueError("A reported Worlds team lacks observed historical lineup data")

    write_csv(data / "historical_lineups.csv", rows, list(rows[0]))
    write_csv(data / "historical_lineup_summary.csv", summary, list(summary[0]))
    write_csv(data / "historical_lineups_vs_worlds_rosters.csv", comparison, list(comparison[0]))
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cutoff_utc": json.loads((root / "config.json").read_text())["cutoff_utc"],
        "source_url": SOURCE_URL,
        "source_page": "https://chaincc.lol/free/data/",
        "license": "CC BY 4.0; attribute chaincc.lol, as described on the provider's preserved data page",
        "source_snapshot_sha256": source_hash,
        "raw_player_rows": raw_count,
        "raw_role_counts": dict(sorted(raw_roles.items())),
        "selected_game_count": len(games),
        "selected_player_rows": selected_count,
        "selected_team_game_lineup_rows": len(rows),
        "complete_team_game_lineups": sum(r["lineup_complete"] for r in rows),
        "primary_actual_game_count": len(primary_ids),
        "primary_team_game_lineup_rows": sum(r["is_primary_model_game"] for r in rows),
        "primary_actual_game_lineup_coverage": 1.0,
        "official_residual_outcomes_without_observed_lineup": [
            {k: r.get(k, "") for k in ("series_id", "date", "team_a", "team_b", "wins_a", "wins_b", "observation_type")}
            for r in residuals
        ],
        "team_count": len(by_team),
        "worlds_teams_compared": len(comparison),
        "worlds_latest_lineups_matching_all_five": sum(r["latest_matching_roles"] == 5 for r in comparison),
        "worlds_latest_mismatches": [r for r in comparison if r["latest_matching_roles"] != 5],
        "first_observed_utc": min(g["date"] for g in history),
        "last_observed_utc": max(g["date"] for g in history),
        "validation_errors": 0,
        "metadata_comparisons_by_field": dict(checks),
        "input_sha256": input_hashes,
        "outputs": {},
        "limitations": [
            "Actual five-player observations are extracted from ChainCC match records, not a complete registered/substitute roster.",
            "Raw player timestamps have no timezone label; existing frozen team-data UTC interpretation is retained and exact timestamps are reconciled.",
            "Two official residual game outcomes have no observed player rows and remain missing; no lineup is inferred or copied into them.",
            "Player handles are not stable person IDs. Case and whitespace are normalized for comparison; no unverified rename/alias merge is performed.",
            "Comparisons with reported Worlds starters are descriptive and may reflect substitutes, roles, naming differences, or source errors.",
            "The published frozen baseline, historical backtests, probabilities, simulations and bootstrap outputs have not been changed or refitted.",
            "Reported Worlds starters are never backfilled into past matches or used as a historical feature.",
            "Completeness here measures the selected game's five role records; it does not certify independent correctness of each name in the provider snapshot.",
        ],
    }
    for filename in ("historical_lineups.csv", "historical_lineup_summary.csv", "historical_lineups_vs_worlds_rosters.csv"):
        path = data / filename
        manifest["outputs"][str(path.relative_to(root))] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    # Verify the extraction did not mutate any frozen input, including baseline observations.
    for rel, original_hash in input_hashes.items():
        if sha256(root / rel) != original_hash:
            raise ValueError(f"Frozen input unexpectedly changed during extraction: {rel}")
    (evidence / "history_lineup_coverage.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = build(args.root)
    print(json.dumps({k: result[k] for k in (
        "selected_game_count", "selected_player_rows", "selected_team_game_lineup_rows",
        "primary_actual_game_count", "primary_team_game_lineup_rows", "team_count",
        "worlds_teams_compared", "worlds_latest_lineups_matching_all_five", "validation_errors",
    )}, ensure_ascii=False))
    for mismatch in result["worlds_latest_mismatches"]:
        print(json.dumps({k: mismatch[k] for k in (
            "team", "latest_observed_utc", "latest_matching_roles", "latest_mismatching_roles",
        )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
