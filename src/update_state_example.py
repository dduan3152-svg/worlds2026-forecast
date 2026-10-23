"""Run synthetic examples of updating the tournament after observed results.

Run from any directory:
    python /path/to/worlds2026-model-v1/src/update_state_example.py --n 10000

Every result and future draw in these examples comes from examples[0] of
outputs/simulation_base.json, a Monte Carlo realization. NONE is an official
result or an official later-round draw. This script writes only
examples/known_state_updated_example.json; it never reads or writes the real
data/known_state.json. The illustrative update dates are synthetic too.

Two supported update patterns are demonstrated:
1. Two complete Swiss rounds, plus the full announced third-round pairing.
2. All Swiss results, plus the full announced quarterfinal bracket.

The strength matrix is deliberately held fixed to isolate state updating.
For real updates, verify official results and refit the model separately.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from simulator import simulate, to_jsonable


ROOT = Path(__file__).resolve().parents[1]
WARNING = (
    "仅供调用演示：全部赛果、后续抽签和更新时间均为合成示例，"
    "来自 simulation_base.json 首条蒙特卡洛样本；不是官方结果，"
    "不得覆盖真实 data/known_state.json。"
)


def build_states(base: dict) -> tuple[list[tuple[str, str]], dict]:
    """Extract illustrative states from exactly one simulated realization."""
    example = base["examples"][0]
    if example.get("kind") != "random_illustrative_realization":
        raise ValueError("The source example must be a synthetic realization.")
    swiss = [stage for stage in example["stages"] if stage["stage"] == "swiss"]
    if any(match.get("status") != "simulated" for stage in swiss for match in stage["matches"]):
        raise ValueError("Use the original pre-event base run with all Swiss results simulated.")
    opening = [(match["a"], match["b"]) for stage in swiss if stage["round"] == 1
               for match in stage["matches"]]
    all_swiss = [
        {"round": stage["round"], "a": match["a"], "b": match["b"], "winner": match["winner"]}
        for stage in swiss for match in stage["matches"]
    ]
    third_round = [(match["a"], match["b"]) for stage in swiss if stage["round"] == 3
                   for match in stage["matches"]]
    quarterfinals = [tuple(pair["teams"]) for pair in example["quarterfinal_bracket"]]
    if (len(opening), len(all_swiss), len(third_round), len(quarterfinals)) != (8, 33, 8, 4):
        raise ValueError("Source realization is incomplete.")
    states = {
        "two_swiss_rounds_plus_round3_draw": {
            "is_synthetic": True,
            "description_zh": "锁定首两轮 16 场合成赛果和第三轮全部 8 场合成对阵，再模拟后续。",
            "simulator_kwargs": {
                "known_matches": [match for match in all_swiss if match["round"] <= 2],
                "announced_rounds": {3: third_round},
                "as_of_utc": "2026-10-25T10:00:00Z",
            },
        },
        "all_swiss_plus_quarterfinal_bracket": {
            "is_synthetic": True,
            "description_zh": "锁定全部 33 场合成瑞士轮赛果和合成八强签位，只模拟淘汰赛。",
            "simulator_kwargs": {
                "known_matches": all_swiss,
                "quarterfinal_bracket": quarterfinals,
                "as_of_utc": "2026-11-01T12:00:00Z",
            },
        },
    }
    return opening, states


def load_matrix(path: Path, teams: list[str]) -> np.ndarray:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        label = reader.fieldnames[0]
        rows = {row[label]: row for row in reader}
    return np.asarray([[float(rows[a][b]) for b in teams] for a in teams], dtype=float)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=10000, help="Simulations per synthetic update example.")
    parser.add_argument("--seed", type=int, default=20261024)
    args = parser.parse_args()
    if args.n < 1:
        parser.error("--n must be positive")

    source = ROOT / "outputs/simulation_base.json"
    raw = source.read_bytes()
    base = json.loads(raw)
    teams = base["teams"]
    opening, states = build_states(base)
    p1 = load_matrix(ROOT / "outputs/matrix_bo1.csv", teams)

    for offset, (name, state) in enumerate(states.items()):
        # Direct state progression: these calls do not reject simulations
        # until their randomly generated history happens to match observations.
        result = simulate(
            teams=teams,
            first_round=opening,
            p1=p1,
            n=args.n,
            seed=args.seed + offset,
            draw_method=base["draw_method"],
            keep_examples=0,
            **state["simulator_kwargs"],
        )
        state["demo_run"] = {
            "n": args.n,
            "seed": args.seed + offset,
            "updates": result["updates"],
            "checks": result["checks"],
        }
        print(f"SYNTHETIC demo completed: {name}; n={args.n}")

    document = {
        "is_synthetic": True,
        "official_results": False,
        "warning_zh": WARNING,
        "source": "outputs/simulation_base.json -> examples[0]",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_simulation_index": base["examples"][0]["simulation_index"],
        "first_round": opening,
        "strength_matrix": "outputs/matrix_bo1.csv, held fixed for this state-update demonstration",
        "states": states,
    }
    destination = ROOT / "examples/known_state_updated_example.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(to_jsonable(document), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(WARNING)
    print(f"Wrote {destination}")


if __name__ == "__main__":
    main()
