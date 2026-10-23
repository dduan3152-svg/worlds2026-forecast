"""Build and verify the frozen S16 forecast release, using only local files.

Run only after the analysis, figures, report, and documentation are final:
    python src/package_release.py

Writes MANIFEST.json in the project and S16_2026_forecast_repro_v1.zip in
its parent. The manifest hashes every included file except itself. The ZIP
uses sorted member names, fixed permissions, and the information cutoff as
its timestamp, so rerunning with unchanged inputs is deterministic within
the same Python/zlib environment. No analysis or network request is run.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = "worlds2026-forecast-v1.0.0.zip"
ROOT_FILES = {"README.md", "CHANGELOG.md", "requirements.txt", "config.json", "CONTRIBUTING.md", "THIRD_PARTY_NOTICES.md", ".gitignore", ".gitattributes"}
ALLOWED_SUFFIXES = {
    ".github": {".yml", ".yaml", ".md"},
    "src": {".py"},
    "data": {".csv", ".json", ".gz"},
    "outputs": {".csv", ".json", ".txt", ".log", ".png", ".pdf"},
    "figures": {".png", ".pdf", ".svg", ".json", ".ttf", ".otf", ".txt"},
    "docs": {".pdf", ".md", ".json", ".docx"},
    "examples": {".json", ".md", ".py"},
    "evidence": {".json", ".md"},
}
REQUIRED_FILES = {
    *ROOT_FILES,
    "src/package_release.py", "src/run_analysis.py", "src/model.py", "src/simulator.py",
    "src/fetch_history.py", "src/update_state_example.py", "src/build_report.py",
    "data/history_chaincc_teams_2026.csv.gz", "data/history_chaincc_players_2026.csv.gz",
    "data/model_observations.csv", "data/teams.json", "data/known_state.json", "data/rules.json",
    "outputs/simulation_base.json", "outputs/simulation_log.json", "outputs/bootstrap_log.json",
    "outputs/stage_probabilities.csv", "outputs/bootstrap_samples.csv",
    "outputs/independent_validation.json", "outputs/simulation_checks.json",
    "figures/manifest.json", "figures/visual_qa.json",
    "figures/assets/NotoSansSC-Regular.ttf", "figures/assets/NotoSansSC-LICENSE.txt",
    "figures/assets/NotoSansCJKsc-Regular.otf", "figures/assets/NotoSansCJK-LICENSE.txt",
    "docs/worlds2026_report.pdf", "docs/report_build_manifest.json",
    "examples/known_state_updated_example.json",
    "evidence/history_coverage.json", "evidence/history_sources.md",
    "evidence/history_lineup_coverage.json", "evidence/official_first_round.json",
    "evidence/teams_sources.json", "evidence/rules_methodology.md", "evidence/rules_validation.json",
}
EXPECTED_V1_COUNTS = {
    "baseline": 100000,
    "sensitivity": 1500000,
    "bootstrap": 2000000,
    "total": 3600000,
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def exclusion_reason(relative: Path, path: Path) -> str | None:
    if relative.as_posix() in {".gitignore", ".gitattributes"}:
        return None
    if any(part.startswith(".") and part != ".github" for part in relative.parts):
        return "hidden or temporary file"
    if any(part in {"tmp", "__pycache__", ".pytest_cache"} for part in relative.parts):
        return "temporary render or Python cache"
    if relative.name == "MANIFEST.json" and len(relative.parts) == 1:
        return "manifest is inserted separately; no recursive self-hash"
    if relative.name.endswith(".meta.json"):
        return "raw HTTP response metadata omitted"
    if path.suffix.lower() in {".html", ".htm", ".js", ".mjs", ".map"}:
        return "complete fetched webpage, script, or source map omitted"
    if relative.parts[0] == "evidence" and path.suffix.lower() in {".pdf", ".txt"}:
        return "complete third-party rules document omitted; linked audit retained"
    if path.suffix.lower() == ".json" and relative.parts[0] in {"data", "evidence"}:
        content = load_json(path)
        if isinstance(content, dict) and isinstance(content.get("headers"), dict):
            return "raw HTTP response metadata omitted"
    if len(relative.parts) == 1:
        return None if relative.name in ROOT_FILES else "outside explicit release allowlist"
    if path.suffix.lower() not in ALLOWED_SUFFIXES.get(relative.parts[0], set()):
        return "outside explicit release allowlist"
    return None


def inventory(root: Path) -> tuple[list[dict], dict]:
    records = []
    excluded = Counter()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"Release does not follow symlinks: {relative}")
        if not path.is_file():
            continue
        reason = exclusion_reason(relative, path)
        if reason:
            # A previous manifest is deliberately outside the payload count;
            # excluding it from this statistic also preserves rerun identity.
            if relative.as_posix() != "MANIFEST.json":
                excluded[reason] += 1
            continue
        records.append({"path": relative.as_posix(), "size_bytes": path.stat().st_size,
                        "sha256": digest(path)})
    missing = REQUIRED_FILES - {entry["path"] for entry in records}
    if missing:
        raise ValueError(f"Required release files missing or excluded: {sorted(missing)}")
    return records, dict(sorted(excluded.items()))


def production_counts(root: Path) -> dict:
    """Compute counts from completed production records, then cross-check totals."""
    simulation = load_json(root / "outputs/simulation_log.json")
    bootstrap = load_json(root / "outputs/bootstrap_log.json")
    baseline = load_json(root / "outputs/simulation_base.json")
    if not simulation.get("completed") or not bootstrap.get("completed"):
        raise ValueError("Production runs have not completed.")
    scenarios = simulation["scenarios"]
    if len({entry["scenario"] for entry in scenarios}) != len(scenarios):
        raise ValueError("Duplicate scenario names would double-count production runs.")
    base_entries = [entry for entry in scenarios if entry["scenario"] == "baseline"]
    additional = [entry for entry in scenarios if entry["scenario"] != "baseline"]
    if len(base_entries) != 1 or any(not entry.get("all_invariants_passed") for entry in scenarios):
        raise ValueError("Scenario baseline or invariant checks are inconsistent.")
    replicas = bootstrap["replicate_logs"]
    if len({entry["replicate"] for entry in replicas}) != len(replicas):
        raise ValueError("Duplicate bootstrap replicate indices.")
    computed = {
        "baseline": int(base_entries[0]["n_completed"]),
        "sensitivity": sum(int(entry["n_completed"]) for entry in additional),
        "bootstrap": sum(int(entry["completed"]) for entry in replicas),
    }
    computed["total"] = sum(computed.values())
    if (computed["baseline"] != int(simulation["baseline_completed"])
            or computed["baseline"] != int(baseline["n"])
            or computed["sensitivity"] != int(simulation["additional_completed"])
            or computed["bootstrap"] != int(bootstrap["total_tournaments_completed"])
            or len(scenarios) != int(simulation["sensitivity_scenarios_including_baseline"])
            or len(replicas) != int(bootstrap["replicates_completed"])
            or any(int(entry["completed"]) != int(bootstrap["tournaments_each"]) for entry in replicas)):
        raise ValueError("Completed logs disagree with their constituent run counts.")
    if computed != EXPECTED_V1_COUNTS:
        raise ValueError(f"This v1 release requires {EXPECTED_V1_COUNTS}; logs contain {computed}.")
    return {
        **computed,
        "unit": "complete future tournament realizations",
        "sensitivity_scenarios_excluding_baseline": len(additional),
        "sensitivity_scenarios_including_baseline": len(scenarios),
        "bootstrap_replicates": len(replicas),
        "bootstrap_tournaments_each": int(bootstrap["tournaments_each"]),
        "calculation": "sum of completed entries in the final production logs; baseline counted once",
        "excluded_from_total": "validation runs and synthetic state-update demonstrations",
        "source_logs": ["outputs/simulation_log.json", "outputs/bootstrap_log.json"],
    }


def source_index(root: Path) -> list[dict]:
    """Collect cited URLs, not complete articles or raw HTTP headers."""
    records: dict[str, dict] = {}
    url_fields = {"url", "source_url", "source", "publisher_page", "published_file_url",
                  "source_page", "discovered_via", "corroborating_sources"}

    def add(url: str, provenance: str, metadata: dict | None = None):
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            return
        entry = records.setdefault(url, {"url": url, "documented_in": set(),
                                         "source_ids": set(), "titles": set()})
        entry["documented_in"].add(provenance)
        if metadata:
            if metadata.get("id"):
                entry["source_ids"].add(str(metadata["id"]))
            if metadata.get("title"):
                entry["titles"].add(str(metadata["title"]))

    def visit(value, provenance: str, field: str = ""):
        if isinstance(value, dict):
            add(value.get("url"), provenance, value)
            for key, child in value.items():
                visit(child, provenance, key)
        elif isinstance(value, list):
            for child in value:
                visit(child, provenance, field)
        elif isinstance(value, str) and field in url_fields:
            add(value, provenance)

    paths = ["data/rules.json", "data/teams.json", "data/eliminated_teams.json",
             "evidence/teams_sources.json", "evidence/official_first_round.json",
             "evidence/history_coverage.json", "evidence/history_lineup_coverage.json",
             "evidence/history_lineup_context.json", "evidence/history_independent_qa.json"]
    for relative in paths:
        visit(load_json(root / relative), relative)
    for relative in ["evidence/history_sources.md", "evidence/rules_methodology.md"]:
        for url in re.findall(r"https?://[^\s<>\]\)]+", (root / relative).read_text(encoding="utf-8")):
            add(url.rstrip(".,;"), relative)
    for path in sorted((root / "figures/assets").glob("*.source.json")):
        visit(load_json(path), path.relative_to(root).as_posix())
    return [{key: sorted(value) if isinstance(value, set) else value for key, value in entry.items()}
            for _, entry in sorted(records.items())]


def relative_reference(raw: str, root: Path) -> str:
    """Resolve build-manifest references after moving the whole project."""
    path = Path(raw)
    if not path.is_absolute():
        return path.as_posix()
    if root.name not in path.parts:
        raise ValueError(f"Build manifest references a file outside the project: {raw}")
    return Path(*path.parts[path.parts.index(root.name) + 1:]).as_posix()


def validate_artifacts(root: Path, records: list[dict]) -> None:
    hashes = {entry["path"]: entry["sha256"] for entry in records}
    report = load_json(root / "docs/report_build_manifest.json")
    for name, expected in report["input_hashes"].items():
        if hashes.get(name) != expected:
            raise ValueError(f"Report input changed after its final build: {name}")
    if hashes.get(report["output"]) != report["sha256"]:
        raise ValueError("Report PDF differs from its build manifest.")
    figures = load_json(root / "figures/manifest.json")
    if hashes.get(figures["font"]["path"]) != figures["font"]["sha256"]:
        raise ValueError("Figure font differs from the recorded font.")
    for figure in figures["figures"]:
        for raw in figure["input_files"]:
            name = relative_reference(raw, root)
            if hashes.get(name) != figure["input_sha256"][Path(raw).name]:
                raise ValueError(f"Figure input changed after its final build: {name}")
        for entry in figure["outputs"]:
            name = relative_reference(entry["file"], root)
            if hashes.get(name) != entry["sha256"]:
                raise ValueError(f"Figure changed after its build manifest: {name}")
    if not load_json(root / "outputs/independent_validation.json").get("all_checks_passed"):
        raise ValueError("The independent output validation has not passed.")


def manifest_document(root: Path, records: list[dict], exclusions: dict) -> dict:
    config = load_json(root / "config.json")
    coverage = load_json(root / "evidence/history_coverage.json")
    lineup = load_json(root / "evidence/history_lineup_coverage.json")
    return {
        "manifest_schema_version": "1.0",
        "release": "S16 2026 Worlds forecast reproducibility package",
        "version": config["version"],
        "information_cutoff_utc": config["cutoff_utc"],
        "information_cutoff_beijing": config["cutoff_beijing"],
        "archive_name": ARCHIVE_NAME,
        "archive_root": root.name,
        "hash_algorithm": "SHA-256",
        "self_hash_policy": "MANIFEST.json is included in the ZIP but omitted from files to avoid a recursive hash.",
        "file_count_excluding_manifest": len(records),
        "total_payload_bytes_excluding_manifest": sum(entry["size_bytes"] for entry in records),
        "production_runs": production_counts(root),
        "sources": source_index(root),
        "licensing_and_provenance": {
            "match_data_publisher": coverage["publisher"],
            "match_data_metadata": coverage["publisher_metadata"],
            "player_data_license": lineup["license"],
            "match_data_snapshot_sha256": coverage["raw_sha256"],
            "player_data_snapshot_sha256": lineup["source_snapshot_sha256"],
            "font_licenses": ["figures/assets/NotoSansSC-LICENSE.txt", "figures/assets/NotoSansCJK-LICENSE.txt"],
            "attribution_details": "evidence/history_sources.md and evidence/history_lineup_notes.md",
        },
        "packaging": {
            "sorted_members": True,
            "fixed_zip_timestamp": config["cutoff_utc"],
            "zip_compression": "DEFLATE level 9",
            "source_files_changed": False,
            "excluded_counts_by_reason": exclusions,
            "omitted_material": ["tmp and Python caches", "all original webpage HTML and JavaScript",
                                 "raw HTTP response metadata, including *.meta.json",
                                 "complete third-party rule PDFs and extracted TXT; source links and analysis retained"],
        },
        "files": records,
    }


def verify_zip(path: Path, root_name: str, records: list[dict], manifest_bytes: bytes) -> None:
    expected = {f"{root_name}/{entry['path']}": entry for entry in records}
    manifest_name = f"{root_name}/MANIFEST.json"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected) | {manifest_name}:
            raise ValueError("ZIP member set is incomplete or contains duplicates/unexpected files.")
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename:
                raise ValueError(f"Unsafe ZIP member path: {info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("ZIP contains an unexpected symlink.")
            payload = archive.read(info)
            if info.filename == manifest_name:
                if payload != manifest_bytes:
                    raise ValueError("Archived manifest does not match the generated manifest.")
                continue
            entry = expected[info.filename]
            if len(payload) != entry["size_bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError(f"ZIP member integrity error: {info.filename}")
        bad = archive.testzip()
        if bad is not None:
            raise ValueError(f"ZIP CRC verification failed: {bad}")


def package(root: Path) -> dict:
    records, exclusions = inventory(root)
    validate_artifacts(root, records)
    manifest = manifest_document(root, records, exclusions)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    timestamp = datetime.fromisoformat(manifest["information_cutoff_utc"].replace("Z", "+00:00"))
    zip_date = (timestamp.year, timestamp.month, timestamp.day, timestamp.hour, timestamp.minute, timestamp.second)
    destination = root.parent / ARCHIVE_NAME
    temporary_zip = None
    temporary_manifest = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".S16-release-", suffix=".zip", dir=root.parent, delete=False) as handle:
            temporary_zip = Path(handle.name)
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            payloads = {entry["path"]: entry for entry in records}
            for name in sorted([*payloads, "MANIFEST.json"]):
                if name == "MANIFEST.json":
                    payload = manifest_bytes
                else:
                    payload = (root / name).read_bytes()
                    entry = payloads[name]
                    if len(payload) != entry["size_bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                        raise ValueError(f"Source file changed while packaging: {name}")
                info = zipfile.ZipInfo(f"{root.name}/{name}", date_time=zip_date)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        verify_zip(temporary_zip, root.name, records, manifest_bytes)
        for entry in records:
            if digest(root / entry["path"]) != entry["sha256"]:
                raise ValueError(f"Source file changed during final verification: {entry['path']}")
        with tempfile.NamedTemporaryFile(prefix=".manifest-", suffix=".json", dir=root, delete=False) as handle:
            temporary_manifest = Path(handle.name)
            handle.write(manifest_bytes)
        temporary_manifest.replace(root / "MANIFEST.json")
        temporary_zip.replace(destination)
        return {
            "archive": str(destination), "archive_bytes": destination.stat().st_size,
            "archive_sha256": digest(destination), "manifest": str(root / "MANIFEST.json"),
            "included_files_with_manifest": len(records) + 1,
            "production_runs": manifest["production_runs"],
            "verification": "Complete member inventory, safe paths, SHA-256, size, and ZIP CRC all passed.",
        }
    finally:
        for temporary in [temporary_zip, temporary_manifest]:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(package(args.root.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
