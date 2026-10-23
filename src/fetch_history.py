#!/usr/bin/env python3
"""Normalize the bundled historical LoL snapshots entirely offline.

Standard library only. No network access or credentials are used.
The filename is retained for compatibility with existing reproduction commands.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import html
import io
import json
from pathlib import Path
import re

BASE = Path(__file__).resolve().parents[1]
DATA_URL = "https://chaincc.lol/data/chaincc-teams-2026.csv.gz"
INDEX_URL = "https://chaincc.lol/free/data/"
DEFAULT_CUTOFF = "2026-10-23T07:25:50Z"
RAW = BASE / "data/history_chaincc_teams_2026.csv.gz"
MAJOR = {"LCK", "LPL", "LEC", "LCS", "LCP", "CBLOL"}
SELECTED = MAJOR | {"FST", "MSI", "EWC", "AC", "WLDs", "DCGI"}
ALIASES = {
    "Gen.G": "GEN", "Hanwha Life Esports": "HLE", "T1": "T1",
    "Dplus Kia": "DK", "Anyone's Legend": "AL", "Bilibili Gaming": "BLG",
    "Top Esports": "TES", "Invictus Gaming": "IG", "G2 Esports": "G2",
    "Movistar KOI": "MKOI", "Karmine Corp": "KC", "Team Liquid": "TLAW",
    "LYON": "LYON", "CTBC Flying Oyster": "CFO", "Team Secret Whales": "TSW",
    "LØS": "LOS", "BNK FEARX": "BFX", "DN SOOPers": "DNS",
    "HANJIN BRION": "BRO", "KT Rolster": "KT", "Kiwoom DRX": "DRX",
    "Nongshim RedForce": "NS", "EDward Gaming": "EDG", "JD Gaming": "JDG",
    "LGD Gaming": "LGD", "LNG Esports": "LNG", "Ninjas in Pyjamas": "NIP",
    "Oh My God": "OMG", "Team WE": "WE", "ThunderTalk Gaming": "TT",
    "Ultra Prime": "UP", "Weibo Gaming": "WBG", "Fnatic": "FNC",
    "GiantX": "GX", "Karmine Corp Blue": "KCB", "Los Ratones": "LR",
    "Natus Vincere": "NAVI", "SK Gaming": "SK", "Shifters": "SFT",
    "Team Heretics": "TH", "Team Vitality": "VIT", "Cloud9": "C9",
    "Dignitas": "DIG", "Disguised": "DSG", "FlyQuest": "FLY",
    "Sentinels": "SEN", "Shopify Rebellion": "SR", "Deep Cross Gaming": "DCG",
    "DetonatioN FocusMe": "DFM", "Fukuoka SoftBank HAWKS gaming": "SHG",
    "GAM Esports": "GAM", "Ground Zero Gaming": "GZ", "MVK Esports": "MVK",
    "FURIA": "FUR", "Fluxo W7M": "FXW", "LOUD": "LOUD", "Leviatan": "LEV",
    "RED Canids": "RED", "Vivo Keyd Stars": "VKS", "paiN Gaming": "PNG",
    "Galions": "GL", "Solary": "SLY", "Team Secret": "TS",
}
def iso(value):
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_date(value):
    value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def dump_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def write_csv(path, rows, fields=None):
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def code(name):
    if name in ALIASES:
        return ALIASES[name]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    return (stem[:24] or "TEAM") + "_" + hashlib.sha256(name.encode()).hexdigest()[:6]


def normalize(cutoff):
    raw_bytes = RAW.read_bytes()
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    with gzip.open(RAW, "rt", encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle))
    groups = collections.defaultdict(list)
    for row in records:
        groups[row["game_id"]].append(row)
    rejected = []
    games = []
    excluded = collections.Counter()
    team_source_ids = collections.defaultdict(set)
    team_regions = collections.defaultdict(collections.Counter)
    for game_id, rows in groups.items():
        try:
            assert len(rows) == 2, "not_exactly_two_team_rows"
            assert {r["side"] for r in rows} == {"Blue", "Red"}, "invalid_sides"
            assert sum(r["result"] == "TRUE" for r in rows) == 1, "noncomplementary_results"
            assert {r["result"] for r in rows} <= {"TRUE", "FALSE"}, "invalid_boolean"
            left, right = sorted(rows, key=lambda r: code(r["team_name"]))
            assert left["team_name"] == right["opponent_team_name"], "opponent_disagreement"
            assert right["team_name"] == left["opponent_team_name"], "opponent_disagreement"
            for field in ["date", "league", "split", "game_number", "gamelength"]:
                assert left[field] == right[field], "paired_metadata_disagreement:" + field
            start = parse_date(left["date"])
            finish = start + dt.timedelta(seconds=int(float(left["gamelength"])))
            if start.year != 2026:
                excluded["outside_2026"] += 1
                continue
            if finish > cutoff:
                excluded["outcome_after_cutoff"] += 1
                continue
            league = left["league"]
            if league not in SELECTED:
                excluded["competition_not_selected:" + league] += 1
                continue
            competition = league
            if league == "WLDs":
                competition = "LPL_RQ" if start.month == 9 else "WORLDS"
                assert start.month in {9, 10}, "unexpected_WLDs_month"
            event_scope = "regional" if league in MAJOR else "international"
            if competition == "LPL_RQ":
                event_scope = "regional_qualifier"
            elif competition == "EWC":
                event_scope = "EWC_main" if dt.date(2026, 7, 15) <= start.date() <= dt.date(2026, 7, 19) else "EWC_qualifier"
            a, b = code(left["team_name"]), code(right["team_name"])
            assert a != b, "code_collision"
            blue = next(row for row in rows if row["side"] == "Blue")
            for row in rows:
                team_source_ids[row["team_name"]].add(row["team_id"])
                if league in MAJOR:
                    team_regions[row["team_name"]][league] += 1
            games.append({
                "series_id": "", "game_id": game_id, "date": iso(start),
                "competition": competition, "competition_raw": league,
                "event_scope": event_scope,
                "split": left["split"], "playoffs": left["playoffs"],
                "team_a": a, "team_b": b,
                "wins_a": int(left["result"] == "TRUE"),
                "wins_b": int(right["result"] == "TRUE"),
                "best_of": 1,
                "game_number": int(left["game_number"]),
                "blue_team": code(blue["team_name"]),
                "red_team": a if left["side"] == "Red" else b,
                "team_a_is_blue": int(left["side"] == "Blue"),
                "blue_won": int(blue["result"] == "TRUE"),
                "team_a_name": left["team_name"], "team_b_name": right["team_name"],
                "team_a_source_id": left["team_id"], "team_b_source_id": right["team_id"],
                "gamelength_seconds": left["gamelength"],
                "start_plus_duration_utc": iso(finish),
                "patch": left["patch"],
                "series_id_method": "", "source_url": DATA_URL,
                "source_snapshot_sha256": raw_sha,
            })
        except (AssertionError, ValueError, KeyError) as error:
            rejected.append({"game_id": game_id, "reason": str(error)})
    games.sort(key=lambda row: (row["date"], row["game_id"]))
    # These identifiers are grouping keys, not invented public match IDs.
    # Maps crossing UTC midnight remain in the same series. A game-number
    # reset or >12-hour separation starts a new group for a repeated pairing.
    active = {}
    series_groups = collections.defaultdict(list)
    for game in games:
        match_prefix = re.fullmatch(r"(.+)_game_\d+", game["game_id"])
        key = (game["competition"], game["split"], game["team_a"], game["team_b"])
        current = parse_date(game["date"])
        if match_prefix:
            sid = "source:" + match_prefix.group(1)
            method = "source_game_id_prefix"
        else:
            prior = active.get(key)
            if prior and game["game_number"] > prior["number"] and current - prior["time"] <= dt.timedelta(hours=12):
                sid = prior["sid"]
            else:
                sid = "derived:" + hashlib.sha256(("|".join(key) + "|" + game["game_id"]).encode()).hexdigest()[:20]
            active[key] = {"number": game["game_number"], "time": current, "sid": sid}
            method = "pair_competition_split_game_number_and_12h_gap"
        game["series_id"], game["series_id_method"] = sid, method
        series_groups[sid].append(game)
    series = []
    for sid, maps in series_groups.items():
        first = maps[0]
        nums = [m["game_number"] for m in maps]
        series.append({
            "series_id": sid, "date": first["date"], "last_game_date": maps[-1]["date"],
            "competition": first["competition"], "competition_raw": first["competition_raw"],
            "split": first["split"], "team_a": first["team_a"], "team_b": first["team_b"],
            "wins_a": sum(m["wins_a"] for m in maps),
            "wins_b": sum(m["wins_b"] for m in maps),
            "best_of": "", "observed_maps": len(maps),
            "game_numbers": "|".join(map(str, nums)),
            "map_numbers_contiguous_from_1": nums == list(range(1, len(nums) + 1)),
            "series_id_method": first["series_id_method"],
            "source_url": DATA_URL, "source_snapshot_sha256": raw_sha,
        })
    write_csv(BASE / "data/history_games.csv", games)
    write_csv(BASE / "data/history_series.csv", series)
    write_csv(BASE / "data/history_rejected_games.csv", rejected, ["game_id", "reason"])
    teams = []
    for name in sorted(team_source_ids, key=code):
        regional = team_regions[name]
        teams.append({"team": code(name), "team_name": name,
                      "source_team_ids": "|".join(sorted(team_source_ids[name])),
                      "region": regional.most_common(1)[0][0] if regional else "UNKNOWN",
                      "region_basis": "2026_domestic_league_rows" if regional else "no_selected_domestic_rows"})
    write_csv(BASE / "data/history_teams.csv", teams)
    coverage = []
    for competition in sorted({g["competition"] for g in games}):
        subset = [g for g in games if g["competition"] == competition]
        coverage.append({"competition": competition, "games": len(subset),
                         "series_groups": len({g["series_id"] for g in subset}),
                         "first_game": min(g["date"] for g in subset),
                         "last_game": max(g["date"] for g in subset),
                         "teams": len({g[k] for g in subset for k in ["team_a", "team_b"]})})
    index = BASE / "evidence/history_chaincc_index.html"
    dataset_info = {}
    if index.exists():
        markup = html.unescape(index.read_text())
        found = re.search(r'"dataset":(\{.*?\}),"', markup)
        # Only extract explicitly labelled fields; the original HTML is retained.
        for key in ["updated_at", "licence", "attribution"]:
            match = re.search(r'"' + key + r'":"([^"]+)"', markup)
            if match:
                dataset_info[key] = match.group(1)
    else:
        # The release omits complete publisher webpages. Preserve the frozen
        # provenance fields when rebuilding offline from the bundled snapshot.
        previous_coverage = BASE / "evidence/history_coverage.json"
        if previous_coverage.exists():
            previous_metadata = json.loads(previous_coverage.read_text()).get("publisher_metadata", {})
            dataset_info = {key: previous_metadata[key] for key in ["updated_at", "licence", "attribution"]
                            if key in previous_metadata}
    summary = {
        "cutoff_utc": iso(cutoff), "publisher": "ChainCC", "publisher_page": INDEX_URL,
        "published_file_url": DATA_URL, "publisher_metadata": dataset_info,
        "raw_sha256": raw_sha, "raw_team_rows": len(records), "raw_unique_games": len(groups),
        "selected_games": len(games), "selected_series_groups": len(series),
        "selected_teams": len(teams), "first_selected_game": games[0]["date"],
        "last_selected_game": games[-1]["date"], "coverage": coverage,
        "rejected_game_count": len(rejected), "exclusions": dict(excluded),
        "noncontiguous_series_groups": [s["series_id"] for s in series if not s["map_numbers_contiguous_from_1"]],
        "worlds_play_in_maps": sum(g["competition"] == "WORLDS" for g in games),
        "lpl_regional_qualifier_maps_relabelled_from_WLDs": sum(g["competition"] == "LPL_RQ" for g in games),
        "date_interpretation": "Source timestamps have no offset. Interpret as UTC; Worlds game start times align with official UTC schedules. Cutoff uses start plus gamelength, not merely date.",
        "observation_unit": "One row per actual observed game. wins_a + wins_b = 1; best_of=1 denotes the observation unit, not the parent match format.",
        "series_table_semantics": "Counts of observed actual games grouped by pairing, competition, split and game-number resets within 12 hours (or source match prefix). best_of is blank because publisher bo_format is entirely missing. No unobserved game order or match format is fabricated.",
        "selection": "Six major regional leagues plus First Stand, MSI, EWC including qualifiers, Americas Cup, Worlds play-ins, LPL regional qualifier and DCGI. Other academy/regional lower-tier competitions and KeSPA Cup excluded.",
        "limitations": [
            "This is a third-party published match-data snapshot, not a complete Riot-certified census. Independent primary-source spotchecks are separate evidence files.",
            "Raw WLDs also includes 12 LPL regional-qualifier maps in September; these are relabelled LPL_RQ. October WLDs contains 22 Worlds play-in maps.",
            "WLRQ is a different academy/amateur competition and is excluded; it must not be interpreted as a Worlds regional qualifier.",
            "Team Secret and Team Secret Whales have distinct source team IDs. Retained separately as TS and TSW pending identity confirmation (eight selected TS games).",
            "Roster changes, substitute appearances and competition strength are not inferred from team-result data. Selected EWC rows include domestic qualifiers.",
            "The source patch field uses values such as 16.20; retain it as a vendor label rather than silently converting it to Riot's 26.20 label or using it as an independently verified patch feature.",
            "All bo_format, match_in_series and game_in_match source fields are empty; derived series IDs and exact game_number are explicitly distinguished.",
            "Published dataset may be updated after this run. The included gzip, SHA-256 and metadata preserve the snapshot actually modeled.",
        ],
    }
    dump_json(BASE / "evidence/history_coverage.json", summary)
    print(json.dumps({k: summary[k] for k in ["selected_games", "selected_series_groups", "selected_teams", "first_selected_game", "last_selected_game", "rejected_game_count", "noncontiguous_series_groups"]}, indent=2))
    if rejected:
        raise RuntimeError("Input validation rejected games; inspect history_rejected_games.csv before modeling")


def reconcile_riot(cutoff):
    official = list(csv.DictReader((BASE / "data/history_official_series.csv").open()))
    series = list(csv.DictReader((BASE / "data/history_series.csv").open()))
    by_pair = collections.defaultdict(list)
    for row in series:
        by_pair[(row["competition"], row["team_a"], row["team_b"])].append(row)
    exact, deficient, contradictory, absent = [], [], [], []
    supplements = []
    for row in official:
        matches = by_pair[(row["competition"], row["team_a"], row["team_b"])]
        nearby = [x for x in matches if abs((parse_date(x["date"]) - parse_date(row["date"])).total_seconds()) <= 12 * 3600]
        if len(nearby) > 1:
            contradictory.append({"official": row, "reason": "multiple_nearby_source_series", "source": nearby})
            continue
        source = nearby[0] if nearby else None
        missing_a = int(row["wins_a"]) - (int(source["wins_a"]) if source else 0)
        missing_b = int(row["wins_b"]) - (int(source["wins_b"]) if source else 0)
        if missing_a < 0 or missing_b < 0:
            contradictory.append({"official": row, "source": source, "reason": "conflicting_winner_counts"})
            continue
        if missing_a + missing_b == 0:
            exact.append({"official_series_id": row["series_id"], "source_series_id": source["series_id"]})
            continue
        issue = {"official": row, "source": source, "missing_wins_a": missing_a, "missing_wins_b": missing_b}
        (deficient if source else absent).append(issue)
        supplements.append({"series_id": row["series_id"], "date": row["date"], "competition": row["competition"],
            "team_a": row["team_a"], "team_b": row["team_b"], "wins_a": missing_a, "wins_b": missing_b,
            "best_of": row["best_of"], "observation_type": "official_series_residual" if source else "official_series_total",
            "source_series_id": source["series_id"] if source else "", "game_number": "", "blue_team": "",
            "source_url": row["source_url"], "source_snapshot": row["source_snapshot"],
            "source_snapshot_sha256": row["source_snapshot_sha256"]})
    write_csv(BASE / "data/history_supplements.csv", supplements,
        ["series_id", "date", "competition", "team_a", "team_b", "wins_a", "wins_b", "best_of", "observation_type", "source_series_id", "game_number", "blue_team", "source_url", "source_snapshot", "source_snapshot_sha256"])
    audit = {"cutoff_utc": iso(cutoff), "official_series": len(official), "exact_score_matches": len(exact),
        "partial_source_series": len(deficient), "absent_source_series": len(absent),
        "contradictions": len(contradictory), "supplement_rows": len(supplements),
        "supplement_observed_games": sum(int(s["wins_a"]) + int(s["wins_b"]) for s in supplements),
        "note": "Supplements are aggregate outcome counts verified from official scores, never fabricated individual games or their side/order. Review contradictions before using supplements.",
        "exact": exact, "partial": deficient, "absent": absent, "conflicting": contradictory}
    dump_json(BASE / "evidence/history_official_reconciliation.json", audit)
    coverage_path = BASE / "evidence/history_coverage.json"
    coverage = json.loads(coverage_path.read_text())
    coverage["official_reconciliation"] = {k: v for k, v in audit.items() if k not in {"exact", "partial", "absent", "conflicting"}}
    coverage["official_coverage_basis"] = "All 2026 completed public Riot schedule series for the six major leagues, FST, MSI, Worlds, Americas Cup and DCGI; paginated back through the 2026 boundary. EWC is not present in that official Riot schedule feed."
    selected_game_rows = list(csv.DictReader((BASE / "data/history_games.csv").open()))
    scope_counts = collections.Counter(g["event_scope"] for g in selected_game_rows)
    competition_counts = collections.Counter(g["competition"] for g in selected_game_rows)
    coverage["model_sets"] = {
        "core": {"competitions": ["LCK", "LPL", "LEC", "LCS", "LCP", "CBLOL", "LPL_RQ", "FST", "MSI", "WORLDS"],
                 "actual_game_rows": sum(c["games"] for c in coverage["coverage"] if c["competition"] not in {"EWC", "AC", "DCGI"}),
                 "official_residual_game_counts": sum(int(s["wins_a"]) + int(s["wins_b"]) for s in supplements if s["competition"] not in {"EWC", "AC", "DCGI"})},
        "extensions": {"EWC_main_games": scope_counts["EWC_main"], "EWC_qualifier_games": scope_counts["EWC_qualifier"], "Americas_Cup_games": competition_counts["AC"], "DCGI_games": competition_counts["DCGI"],
                       "basis": "EWC main event provides additional direct international comparisons, while its qualifiers add domestic results. Americas Cup bridges LCS/CBLOL in March. DCGI bridges regions in October among teams outside Worlds. These different populations and timing make them suitable for a separate sensitivity specification."},
    }
    dump_json(coverage_path, coverage)
    print(json.dumps({k: v for k, v in audit.items() if k not in {"exact", "partial", "absent", "conflicting"}}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconcile-riot", action="store_true", help="Reconcile the bundled official snapshots without network")
    parser.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    args = parser.parse_args()
    for directory in [BASE / "data", BASE / "evidence"]:
        directory.mkdir(parents=True, exist_ok=True)
    normalize(parse_date(args.cutoff))
    if args.reconcile_riot:
        reconcile_riot(parse_date(args.cutoff))


if __name__ == "__main__":
    main()
