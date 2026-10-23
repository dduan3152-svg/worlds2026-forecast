#!/usr/bin/env python3
"""Render shareable Chinese scientific figures from the model's CSV outputs.

This module reads model results only. It never changes probabilities, fits a
model, performs simulations, or invents missing uncertainty estimates.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors, font_manager, patheffects
from matplotlib.ft2font import FT2Font
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter
import numpy as np

BASE = Path(__file__).resolve().parents[1]
INK = "#183247"
MUTED = "#5C6E7E"
GRID = "#E5EBF0"
PAPER = "#FCFDFE"
REGION_COLORS = {
    "LCK": "#2B6CB0", "LPL": "#CD5548", "LEC": "#7961A7",
    "LCS": "#BD8A24", "LCP": "#168A83", "CBLOL": "#748491",
}
RECORDS = ["3-0", "3-1", "3-2", "2-3", "1-3", "0-3"]
RECORD_COLORS = ["#17616B", "#3B9EA5", "#A2D6D3", "#F1C394", "#DF955C", "#B45D40"]


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), reader.fieldnames


def number(row, key, default=None):
    value = row.get(key, "")
    return default if value in {None, "", "NaN", "nan"} else float(value)


def pct(value, decimals=1):
    if 0 < value < 0.001:
        return "<0.1%"
    return f"{100 * value:.{decimals}f}%"


def weight_label(value):
    return f"{value:,.0f}" if abs(value - round(value)) < 1e-8 else f"{value:,.1f}"


def configure_font(root):
    font_path = root / "figures/assets/NotoSansCJKsc-Regular.otf"
    if not font_path.exists():
        font_path = root / "figures/assets/NotoSansSC-Regular.ttf"
    if not font_path.exists():
        raise FileNotFoundError("A bundled CJK font is required; missing figures/assets/NotoSansCJKsc-Regular.otf")
    font_manager.fontManager.addfont(str(font_path))
    face = FT2Font(str(font_path))
    required = "英雄联盟世界赛瑞士轮冠军概率预测行胜列蓝红方模型重采样误差数据截至北京时间分组平均实际胜率晋级淘汰权重—"
    missing = [char for char in required if ord(char) not in face.get_charmap()]
    if missing:
        raise RuntimeError("The chosen font lacks Chinese glyphs: " + "".join(missing))
    plt.rcParams.update({
        "font.family": [face.family_name, "DejaVu Sans"],
        "font.size": 11, "axes.labelcolor": INK, "text.color": INK,
        "xtick.color": MUTED, "ytick.color": INK,
        "axes.edgecolor": GRID, "axes.facecolor": PAPER,
        "figure.facecolor": PAPER, "savefig.facecolor": PAPER,
        "axes.unicode_minus": False, "pdf.fonttype": 42,
        "ps.fonttype": 42, "svg.fonttype": "path",
    })
    warnings.filterwarnings("error", message="Glyph .* missing from font")
    return {"path": str(font_path.relative_to(root)), "family": face.family_name,
            "sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
            "required_glyphs_present": True}


def context(root):
    config = json.loads((root / "config.json").read_text())
    teams = json.loads((root / "data/teams.json").read_text())
    return config, {t["code"]: t for t in teams}


def header(fig, title, subtitle, x=0.095):
    fig.text(x, 0.965, title, fontsize=24, color=INK, ha="left", va="top")
    fig.text(x, 0.922, subtitle, fontsize=11.6, color=MUTED, ha="left", va="top")


def footer(fig, config, notes, source, x=0.095, first_y=0.095):
    for idx, line in enumerate(notes):
        fig.text(x, first_y - idx * 0.023, line, fontsize=9.3, color=MUTED, ha="left", va="top")
    when = dt.datetime.fromisoformat(config["cutoff_beijing"]).strftime("%Y-%m-%d %H:%M:%S")
    fig.text(x, 0.025, f"数据截至北京时间 {when}  |  来源：ChainCC、Riot 官方赛果及模型输出 {source}",
             fontsize=8.9, color=MUTED, ha="left", va="bottom")


def clean_axes(ax, grid_axis="x"):
    for side in ["top", "right", "left"]:
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="both", length=0, pad=7)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def save(fig, output_dir, name, input_paths, details):
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for extension in ["png", "pdf"]:
        path = output_dir / f"{name}.{extension}"
        fig.savefig(path, dpi=220, bbox_inches="tight", pad_inches=0.16,
                    metadata={"Creator": "Worlds 2026 reproducible prediction model"})
        outputs.append({"file": path.relative_to(output_dir.parent).as_posix(), "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    plt.close(fig)
    return {"figure": name, "input_files": [p.relative_to(output_dir.parent).as_posix() for p in input_paths],
            "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths},
            "outputs": outputs, **details}


def matchup_matrix(root, output_dir, config, teams):
    path = root / "outputs/matrix_bo1.csv"
    rows, fields = read_csv(path)
    columns = fields[1:]
    labels = [row[fields[0]] for row in rows]
    if len(rows) != 16 or len(columns) != 16 or set(labels) != set(columns):
        raise ValueError("Expected the complete 16-by-16 matrix with team labels in its first column")
    data = np.array([[float(row[c]) for c in columns] for row in rows])
    if not np.all(np.isfinite(data)) or np.any(data < 0) or np.any(data > 1):
        raise ValueError("Matrix values must be probabilities in [0,1]")
    for i, team in enumerate(labels):
        data[i, columns.index(team)] = np.nan
    fig = plt.figure(figsize=(13.2, 12.6))
    header(fig, "BO1 对阵胜率", "读取方式：行队伍战胜列队伍的概率；队伍顺序保留原始结果文件。", x=0.115)
    ax = fig.add_axes([0.115, 0.165, 0.745, 0.705])
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad("#EEF2F5")
    img = ax.imshow(np.ma.masked_invalid(data), vmin=0, vmax=1, cmap=cmap, aspect="equal")
    ax.set_xticks(range(16), columns, fontsize=10)
    ax.set_yticks(range(16), labels, fontsize=10.5)
    ax.xaxis.tick_top()
    ax.tick_params(length=0, pad=8)
    ax.set_ylabel("行：目标队伍", fontsize=11, labelpad=20)
    ax.set_xticks(np.arange(-0.5, 16, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, 16, 1), minor=True)
    ax.grid(which="minor", color=PAPER, linewidth=0.7)
    ax.tick_params(which="minor", bottom=False, left=False)
    for side in ax.spines.values():
        side.set_visible(False)
    for i in range(16):
        for j in range(16):
            p = data[i, j]
            if np.isnan(p):
                ax.text(j, i, "—", ha="center", va="center", fontsize=12, color="#8799A7")
            else:
                ax.text(j, i, f"{p * 100:.1f}%", ha="center", va="center", fontsize=8.8,
                        color="white" if p < .24 or p > .77 else INK)
    cax = fig.add_axes([0.89, 0.27, 0.022, 0.48])
    cb = fig.colorbar(img, cax=cax)
    cb.set_ticks([0, .25, .5, .75, 1], labels=["0%", "25%", "50%", "75%", "100%"])
    cb.ax.tick_params(length=0, pad=6)
    cb.set_label("行队伍胜率", labelpad=12, fontsize=10)
    cb.outline.set_visible(False)
    footer(fig, config,
           ["未来单局蓝／红方按各 50% 边际化；未假定未来比赛的实际选边。",
            "本图显示固定模型参数下的点估计，不包含胜率估计的不确定性区间。"], path.name, x=0.115, first_y=0.095)
    return save(fig, output_dir, "01_matchup_matrix_bo1", [path],
                {"rows": labels, "columns": columns, "unit": "row beats column probability", "diagonal": "masked"})


def champion_bars(root, output_dir, config, teams):
    path = root / "outputs/stage_probabilities.csv"
    rows, _ = read_csv(path)
    rows.sort(key=lambda row: -number(row, "champion"))
    estimates = np.array([number(row, "champion") for row in rows])
    eliminated = [str(row.get("status", "")).startswith("eliminated") for row in rows]
    if np.any(estimates < 0) or np.any(estimates > 1) or not np.isclose(estimates.sum(), 1, atol=1e-6):
        raise ValueError("Champion estimates must sum to one")
    bootstrap_path = root / "outputs/bootstrap_summary.csv"
    boot = {}
    inputs = [path]
    if bootstrap_path.exists():
        boot_rows, _ = read_csv(bootstrap_path)
        boot = {row["team"]: row for row in boot_rows}
        inputs.append(bootstrap_path)
    has_intervals = bool(boot) and all(row["team"] in boot and number(boot[row["team"]], "champion_q05") is not None
                                           and number(boot[row["team"]], "champion_q95") is not None for row in rows)
    lows = np.array([number(boot[row["team"]], "champion_q05") for row in rows]) if has_intervals else None
    highs = np.array([number(boot[row["team"]], "champion_q95") for row in rows]) if has_intervals else None
    if has_intervals and (np.any(lows > highs) or np.any(lows < 0) or np.any(highs > 1)):
        raise ValueError("Invalid bootstrap quantiles")
    y = np.arange(len(rows))
    fig = plt.figure(figsize=(12.6, 10.2))
    n = int(config.get("simulation_count", 100000))
    subtitle = f"固定拟合模型模拟 {n:,} 次完整赛事；按冠军点估计排序。"
    header(fig, "谁最可能夺冠", subtitle, x=0.12)
    ax = fig.add_axes([0.12, 0.225, 0.72, 0.625])
    palette = [REGION_COLORS.get(teams.get(row["team"], {}).get("region"), "#718096") for row in rows]
    ax.barh(y, 100 * estimates, height=.63, color=palette, zorder=2)
    if has_intervals:
        halo = [patheffects.Stroke(linewidth=3.7, foreground="white", alpha=.88), patheffects.Normal()]
        for i, (lo, hi) in enumerate(zip(lows, highs)):
            ax.plot([100 * lo, 100 * hi], [i, i], color=INK, linewidth=1.5, zorder=4, path_effects=halo)
            for endpoint in [lo, hi]:
                ax.plot([100 * endpoint] * 2, [i - .12, i + .12], color=INK, linewidth=1.4, zorder=4, path_effects=halo)
    labels = [f"{row['team']}   {teams.get(row['team'], {}).get('region', '')}" for row in rows]
    ax.set_yticks(y, labels, fontsize=11)
    ax.invert_yaxis()
    right = np.maximum(estimates, highs) * 100 if has_intervals else estimates * 100
    limit = max(10, math.ceil((right.max() * 1.15 + 3) / 5) * 5)
    ax.set_xlim(0, limit)
    for i, value in enumerate(estimates):
        label = "0%（已淘汰）" if eliminated[i] else pct(value)
        ax.text(right[i] + limit * .015, i, label, va="center", ha="left", fontsize=10.5,
                color=MUTED if eliminated[i] else INK)
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_xlabel("夺冠概率", labelpad=12, fontsize=11)
    clean_axes(ax)
    present_regions = [r for r in REGION_COLORS if any(teams.get(row["team"], {}).get("region") == r for row in rows)]
    handles = [Patch(facecolor=REGION_COLORS[r], label=r) for r in present_regions]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(.12, .155), ncol=6,
               frameon=False, fontsize=9.5, borderaxespad=0, columnspacing=1.6, handlelength=1.25)
    notes = []
    if has_intervals:
        notes.append("黑色线段：90% 模型重采样区间（第 5–95 百分位）；固定超参数，不覆盖全部模型风险。")
    else:
        notes.append("模型重采样区间尚未提供；本图只显示固定参数下的冠军点估计。")
    mc = [number(row, "champion_mc_se") for row in rows]
    if all(value is not None for value in mc):
        halfwidth = max(mc) * 1.96 * 100
        notes.append(f"MC 数值误差另计：各队点估计的近似 95% MC 误差半宽最大 {halfwidth:.2f} 个百分点。")
    else:
        notes.append("模型重采样反映历史样本变化；蒙特卡洛（MC）误差反映有限仿真次数，两者不同。")
    if any(eliminated):
        out = "、".join(rows[i]["team"] for i, value in enumerate(eliminated) if value)
        notes.append(f"{out} 已在入围赛出局；其 0% 由截止时已知赛果确定。")
    elif any(value == 0 and not out for value, out in zip(estimates, eliminated)):
        notes.append("显示 0.0% 只表示这次固定参数仿真未出现该队夺冠，并不证明真实概率为零。")
    footer(fig, config, notes, "stage_probabilities.csv / bootstrap_summary.csv", x=.12, first_y=.105)
    return save(fig, output_dir, "02_champion_probabilities", inputs,
                {"sort": [r["team"] for r in rows], "bootstrap_intervals_rendered": has_intervals,
                 "interval": "model-resampling q05-q95", "mc_error_is_separate": True})


def calibration(root, output_dir, config, teams):
    path = root / "outputs/calibration.csv"
    rows, _ = read_csv(path)
    rows.sort(key=lambda row: number(row, "bin_low"))
    favorite = any("favorite" in row.get("note", "").lower() for row in rows)
    active = [row for row in rows if number(row, "map_weight", 0) > 0]
    xp = np.array([number(row, "mean_prediction") for row in active])
    yp = np.array([number(row, "observed_win_rate") for row in active])
    if np.any(xp < 0) or np.any(xp > 1) or np.any(yp < 0) or np.any(yp > 1):
        raise ValueError("Calibration probabilities out of bounds")
    w = np.array([number(row, "map_weight", 0) for row in rows])
    fig = plt.figure(figsize=(10.8, 10.8))
    subtitle = "时间顺序留出回测：以赛前模型看好的队伍为视角。" if favorite else "时间顺序留出回测：比较分组平均预测胜率与实际胜率。"
    header(fig, "概率说得有多准", subtitle, x=.105)
    ax = fig.add_axes([.105, .395, .8, .465])
    ax.plot([0, 1], [0, 1], linestyle=(0, (5, 4)), color="#8A99A5", linewidth=1.4, label="理想校准", zorder=1)
    ax.plot(xp, yp, color="#217989", linewidth=2.1, zorder=3)
    wa = np.array([number(row, "map_weight") for row in active])
    sizes = 70 + 155 * wa / wa.max()
    ax.scatter(xp, yp, s=sizes, color="#217989", edgecolors="white", linewidth=1.5, zorder=4, label="模型分组结果")
    for i, (x, y, weight) in enumerate(zip(xp, yp, wa)):
        offset = (-8, -24) if x > .68 else (9, 15)
        ax.annotate(f"{weight_label(weight)} 局权重", (x, y), xytext=offset, textcoords="offset points",
                    fontsize=9.5, color=INK, ha="left" if x <= .68 else "right",
                    bbox={"boxstyle": "round,pad=.25", "facecolor": PAPER, "edgecolor": "none", "alpha": .9})
    lower = .5 if favorite else 0
    ax.set_xlim(lower, 1)
    ax.set_ylim(min(lower, math.floor(float(yp.min()) * 10) / 10), 1.055 if float(yp.max()) > .97 else 1)
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
    ax.set_xticks(np.linspace(lower, 1, 6))
    ax.set_yticks(np.linspace(lower, 1, 6))
    ax.set_xlabel("分组平均预测胜率", labelpad=12)
    ax.set_ylabel("分组实际胜率", labelpad=12)
    clean_axes(ax, grid_axis="both")
    ax.legend(loc="lower right" if favorite else "upper left", frameon=False, fontsize=10)
    weights = fig.add_axes([.105, .175, .8, .13])
    centers = [(number(row, "bin_low") + number(row, "bin_high")) / 2 for row in rows]
    widths = [(number(row, "bin_high") - number(row, "bin_low")) * .72 for row in rows]
    weights.bar(centers, w, width=widths, color="#A3CBD1", zorder=2)
    for x, value in zip(centers, w):
        weights.text(x, value + max(w) * .04, weight_label(value), ha="center", va="bottom", fontsize=10)
    weights.set_xlim(lower, 1)
    weights.set_ylim(0, max(w) * 1.32)
    weights.set_xticks(centers, [f"{number(r, 'bin_low')*100:.0f}–{number(r, 'bin_high')*100:.0f}%" for r in rows], fontsize=10)
    weights.set_ylabel("局数权重", labelpad=15)
    weights.set_xlabel("预测概率分组", labelpad=11)
    clean_axes(weights, grid_axis="y")
    perspective_note = "每局按预测更强的一方计一次；空分组不绘制校准点。" if favorite else "每局以双方视角各计一半权重，未把总局数翻倍。"
    footer(fig, config,
           [f"回测总局数权重：{weight_label(w.sum())}。{perspective_note}",
            "同一系列赛内的地图可能相关；图示权重不等于相互独立的样本量。"], path.name, x=.105, first_y=.09)
    return save(fig, output_dir, "03_calibration", [path],
                {"bins": len(rows), "map_weight_total": float(w.sum()), "bin_map_weights": w.tolist(),
                 "confidence_bands": "none; not available in source CSV"})


def read_swiss(path):
    rows, fields = read_csv(path)
    if {"team", "record", "probability"} <= set(fields):
        table = {}
        for row in rows:
            table.setdefault(row["team"], {})[row["record"]] = float(row["probability"])
        labels = list(table)
        data = np.array([[table[t][r] for r in RECORDS] for t in labels])
    else:
        selected_columns = []
        for record in RECORDS:
            alternatives = [record, record.replace("-", "_"), "p_" + record.replace("-", "_"),
                            "record_" + record.replace("-", "_"), record + "_probability"]
            column = next((candidate for candidate in alternatives if candidate in fields), None)
            if column is None:
                raise ValueError(f"Missing Swiss terminal-record probability column for {record}; available={fields}")
            selected_columns.append(column)
        labels = [row["team"] for row in rows]
        data = np.array([[float(row[c]) for c in selected_columns] for row in rows])
    if len(labels) != 16 or np.any(data < 0) or np.any(data > 1) or not np.allclose(data.sum(axis=1), 1, atol=1e-6):
        raise ValueError("Swiss record probabilities must have 16 teams and sum to one for every team")
    if not np.allclose(data.sum(axis=0), [2, 3, 3, 3, 3, 2], atol=1e-5):
        raise ValueError("Swiss terminal-record totals do not match the tournament's 2/3/3/3/3/2 invariant")
    return labels, data


def swiss_records(root, output_dir, config, teams):
    path = root / "outputs/swiss_records.csv"
    labels, data = read_swiss(path)
    qualify = data[:, :3].sum(axis=1)
    ordering = np.argsort(-qualify, kind="stable")
    data, qualify = data[ordering], qualify[ordering]
    labels = [labels[i] for i in ordering]
    fig = plt.figure(figsize=(12.6, 10.2))
    header(fig, "瑞士轮会以什么战绩结束", "按八强晋级概率排序；每条横带合计 100%。", x=.105)
    ax = fig.add_axes([.105, .225, .73, .625])
    left = np.zeros(16)
    y = np.arange(16)
    for j, (record, color) in enumerate(zip(RECORDS, RECORD_COLORS)):
        values = data[:, j] * 100
        ax.barh(y, values, left=left, height=.66, color=color, edgecolor=PAPER, linewidth=.6, zorder=2)
        for i, width in enumerate(values):
            if width >= 5.5:
                ax.text(left[i] + width / 2, i, f"{width:.1f}%", ha="center", va="center", fontsize=8.6,
                        color="white" if j in {0, 1, 5} else INK)
        left += values
    for i, probability in enumerate(qualify):
        ax.plot([probability * 100] * 2, [i - .36, i + .36], color=INK, linewidth=.9, zorder=4)
        ax.text(1.025, i, pct(probability), transform=ax.get_yaxis_transform(), va="center", fontsize=10.5)
    ax.text(1.025, 1.03, "八强概率", transform=ax.transAxes, fontsize=10.5, color=MUTED)
    ax.set_yticks(y, labels, fontsize=11)
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    ax.set_xlabel("该终局战绩的概率", labelpad=12)
    clean_axes(ax)
    handles = [Patch(facecolor=color, label=record + (" 晋级" if i < 3 else " 淘汰"))
               for i, (record, color) in enumerate(zip(RECORDS, RECORD_COLORS))]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(.105, .155), ncol=6,
               frameon=False, fontsize=10, borderaxespad=0, columnspacing=1.35, handlelength=1.2)
    footer(fig, config,
           ["青色系为晋级八强，橙色系为淘汰；黑色短线标出累计晋级概率。",
            "每次完整仿真均有 2 支 3–0、3 支 3–1、3 支 3–2 队伍晋级；本图显示各队的概率分布。"],
           path.name, x=.105, first_y=.1)
    return save(fig, output_dir, "04_swiss_records", [path],
                {"sort": labels, "record_order": RECORDS, "qualify_probability": qualify.tolist(),
                 "terminal_record_invariant_verified": True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=BASE)
    parser.add_argument("--only", nargs="+", choices=["matrix", "champion", "calibration", "swiss"])
    parser.add_argument("--strict", action="store_true", help="Fail if a required input CSV is not ready")
    args = parser.parse_args()
    root = args.root.resolve()
    output_dir = root / "figures"
    output_dir.mkdir(exist_ok=True)
    font_info = configure_font(root)
    config, teams = context(root)
    specifications = {
        "matrix": ("matrix_bo1.csv", matchup_matrix),
        "champion": ("stage_probabilities.csv", champion_bars),
        "calibration": ("calibration.csv", calibration),
        "swiss": ("swiss_records.csv", swiss_records),
    }
    chosen = args.only or list(specifications)
    manifest = {"created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "font": font_info,
                "cutoff_utc": config["cutoff_utc"], "figures": [], "pending": []}
    for name in chosen:
        filename, function = specifications[name]
        if not (root / "outputs" / filename).exists():
            manifest["pending"].append(filename)
            if args.strict:
                raise FileNotFoundError(root / "outputs" / filename)
            continue
        item = function(root, output_dir, config, teams)
        manifest["figures"].append(item)
        print(json.dumps({"created": item["figure"], "outputs": [o["file"] for o in item["outputs"]],
                          "bootstrap_intervals_rendered": item.get("bootstrap_intervals_rendered")}, ensure_ascii=False))
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    if manifest["pending"]:
        print(json.dumps({"pending": manifest["pending"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
