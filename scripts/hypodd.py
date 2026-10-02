"""Relocate the detections of a playground run with HypoDD and compare the locations.

The script runs with the Python of the qseek checkout (see the justfile) and uses
the standard library, `playground.py` and the settings model of `qseek export
hypodd`. HypoDD's `ph2dt` and `hypoDD` are taken from `HYPODD_BIN`, or from the PATH.

Commands:
    relocate  export a run with `qseek export hypodd`, run ph2dt and hypoDD, and
              write the relocations as a run directory of their own
    compare   compare the locations of runs on their common events, with the
              double-difference residuals of every run evaluated by hypoDD
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from playground import (
    EARTH_RADIUS,
    RUNINFO_FILE,
    Event,
    Example,
    apply_override,
    check_run_name,
    distance,
    environment,
    extract_metrics,
    load_detections,
    load_reference,
    local_xyz,
    match_events,
    median,
    nearest_neighbor_distances,
    parse_override,
    percentile,
    print_metrics,
    print_table,
    qseek_executable,
    rounded,
)

EXPORT_DIR = "hypodd"
EXPORT_CONFIG = "hypodd-export.json"
COMPARISON_FILE = "hypodd-comparison.json"
EVALUATION_DIR = "hypodd-evaluation"
# hypoDD loops forever when the inversion fails with NaN
HYPODD_TIMEOUT = 600.0
# Damping of the evaluation run: the locations do not move in the single iteration
EVALUATION_DAMPING = 1e6

RELOC_COLUMNS = [
    "id",
    "lat",
    "lon",
    "depth",
    "x",
    "y",
    "z",
    "ex",
    "ey",
    "ez",
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "second",
    "mag",
    "nccp",
    "nccs",
    "nctp",
    "ncts",
    "rcc",
    "rct",
    "cid",
]


# HypoDD


def hypodd_binary(name: str) -> str:
    bin_dir = os.environ.get("HYPODD_BIN")
    path = Path(bin_dir).expanduser() / name if bin_dir else shutil.which(name)
    if not path or not Path(path).exists():
        raise SystemExit(f"{name} not found, set HYPODD_BIN or add it to the PATH")
    return str(path)


def run_binary(name: str, control_file: str, cwd: Path) -> None:
    """Run ph2dt or hypoDD, its output goes to <name>.out in `cwd`."""
    with (cwd / f"{name}.out").open("wb") as out:
        try:
            result = subprocess.run(
                [hypodd_binary(name), control_file],
                cwd=cwd,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=HYPODD_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SystemExit(
                f"{name} did not finish within {HYPODD_TIMEOUT:.0f} s, it loops"
                f" forever when the inversion fails with NaN; see {cwd}/{name}.log"
            ) from None
    if result.returncode != 0:
        raise SystemExit(f"{name} failed with exit code {result.returncode} in {cwd}")


def read_text(file: Path) -> str:
    """Read a HypoDD log, which contains NUL characters."""
    return file.read_text(errors="replace").replace("\0", "")


def hypodd_summary(directory: Path) -> dict[str, Any]:
    """Summary of a hypoDD run from its log and outputs."""
    log = read_text(directory / "hypoDD.log")
    # the iteration table has the columns CC, RMSCC and its change with dt.cc
    cc_columns = r"(\d+)\s+" if "dt.cc" in log else ""
    cc_rms = r"\s+(\d+)\s+(-?[\d.]+)" if cc_columns else ""
    iterations = re.findall(
        rf"^\s*(\d+)\s+(?:\d+\s+)?(\d+)\s+(\d+)\s+{cc_columns}(\d+)\s+(-?[\d.]+)"
        rf"{cc_rms}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)"
        r"\s+(\d+)\s*$",
        log,
        re.MULTILINE,
    )
    initial_rms = re.findall(
        r"initial data:.*?absolute ct rms \[s\] =\s*([\d.]+)", log, re.S
    )
    clusters = re.findall(r"^Cluster\s+\d+:\s+(\d+) events", log, re.MULTILINE)
    ph2dt_log = read_text(directory / "ph2dt.log")
    selected = re.search(r"events selected =\s*(\d+)", ph2dt_log)
    reloc = read_reloc(directory / "hypoDD.reloc")
    summary: dict[str, Any] = {
        "n_events_exported": sum(1 for _ in event_ids(directory)),
        "n_events_ph2dt": int(selected.group(1)) if selected else None,
        "n_clusters": len(clusters),
        "cluster_sizes": [int(n) for n in clusters],
        "n_relocated": len(reloc),
        "initial_ct_rms_ms": rounded(1e3 * float(initial_rms[0]), 1)
        if initial_rms
        else None,
    }
    if iterations:
        columns = [
            "it",
            "ev",
            "ct",
            *(["cc"] if cc_columns else []),
            "rmsct",
            "rmsct_change",
            *(["rmscc", "rmscc_change"] if cc_columns else []),
            "rmsst",
            "dx",
            "dy",
            "dz",
            "dt",
            "os",
            "aq",
            "cnd",
        ]
        last = dict(zip(columns, iterations[-1], strict=True))
        summary["n_iterations"] = int(last["it"])
        summary["final"] = {
            "events_percent": int(last["ev"]),
            "ct_data_percent": int(last["ct"]),
            "ct_rms_ms": int(last["rmsct"]),
            "shift_x_m": int(last["dx"]),
            "shift_y_m": int(last["dy"]),
            "shift_z_m": int(last["dz"]),
            "shift_t_ms": int(last["dt"]),
            "centroid_shift_m": int(last["os"]),
        }
        if cc_columns:
            summary["final"]["cc_data_percent"] = int(last["cc"])
            summary["final"]["cc_rms_ms"] = int(last["rmscc"])
        cnds = [int(row[-1]) for row in iterations]
        summary["cnd_median"] = median(cnds)
    return summary


def read_reloc(file: Path) -> dict[int, dict[str, float]]:
    """Read hypoDD.reloc, keyed by event ID."""
    events = {}
    if not file.exists():
        return events
    for line in file.read_text().splitlines():
        values = line.split()
        if len(values) != len(RELOC_COLUMNS):
            continue
        row = {
            key: float(value) for key, value in zip(RELOC_COLUMNS, values, strict=True)
        }
        events[int(row["id"])] = row
    return events


def reloc_time(row: dict[str, float]) -> datetime:
    base = datetime(
        int(row["year"]),
        int(row["month"]),
        int(row["day"]),
        int(row["hour"]),
        int(row["minute"]),
        tzinfo=timezone.utc,
    )
    return base + timedelta(seconds=row["second"])


@dataclass
class ExportedEvent:
    id: int
    uid: str
    time: datetime  # origin time of the detection
    hypodd_time: datetime  # origin time the travel times refer to


def event_ids(directory: Path) -> list[ExportedEvent]:
    with (directory / "event_ids.csv").open(newline="") as f:
        return [
            ExportedEvent(
                id=int(row["id"]),
                uid=row["uid"],
                time=datetime.fromisoformat(row["time"]),
                hypodd_time=datetime.fromisoformat(row["hypodd_time"]),
            )
            for row in csv.DictReader(f)
        ]


# Relocate


def shift_location(
    row: dict[str, str], lat: float, lon: float, depth: float
) -> dict[str, str]:
    """Move a row of the detection table to a new location."""
    row = dict(row)
    lat0, lon0 = float(row["lat"]), float(row["lon"])
    cos_lat = math.cos(math.radians(lat0))
    d_east = math.radians(lon - lon0) * cos_lat * EARTH_RADIUS
    d_north = math.radians(lat - lat0) * EARTH_RADIUS
    for key, delta in (("east_shift", d_east), ("north_shift", d_north)):
        if row.get(key):
            row[key] = f"{float(row[key]) + delta:.2f}"
    row["lat"] = f"{lat:.6f}"
    row["lon"] = f"{lon:.6f}"
    row["depth"] = f"{depth:.2f}"
    if "WKT_geom" in row:
        row["WKT_geom"] = f"POINT Z({lon} {lat} {-depth})"
    return row


def write_relocations(source: Path, rundir: Path) -> int:
    """Write the relocated detections as the detection table of the run.

    The rows keep the columns of the source run; location and origin time come from
    hypoDD. The residual RMS and the location uncertainties are removed, they do not
    apply to the relocations. Detections that hypoDD did not relocate are left out.
    """
    directory = rundir / EXPORT_DIR
    with (source / "csv" / "detections.csv").open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = {datetime.fromisoformat(row["time"]): row for row in reader}

    reloc = read_reloc(directory / "hypoDD.reloc")
    extra = ["hypodd_id", "hypodd_cluster", "hypodd_nctp", "hypodd_ncts", "hypodd_rct"]
    out_rows = []
    for event in event_ids(directory):
        if event.id not in reloc:
            continue
        rel = reloc[event.id]
        row = shift_location(
            rows[event.time], rel["lat"], rel["lon"], rel["depth"] * 1e3
        )
        row["time"] = str(reloc_time(rel))
        for key in ("rms", "uncertainty_horizontal", "uncertainty_vertical"):
            if key in row:
                row[key] = ""
        row.update(
            hypodd_id=str(event.id),
            hypodd_cluster=str(int(rel["cid"])),
            hypodd_nctp=str(int(rel["nctp"])),
            hypodd_ncts=str(int(rel["ncts"])),
            hypodd_rct=f"{rel['rct']:.4f}",
        )
        out_rows.append(row)

    out_rows.sort(key=lambda row: row["time"])
    (rundir / "csv").mkdir(exist_ok=True)
    with (rundir / "csv" / "detections.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames + extra)
        writer.writeheader()
        writer.writerows(out_rows)
    return len(out_rows)


def relocate(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    source = example.rundir(args.source)
    if not (source / "csv" / "detections.csv").exists():
        raise SystemExit(f"run {args.source} has no detections")
    name = check_run_name(args.run or f"{args.source}-hypodd")
    rundir = example.rundir(name)
    if rundir.exists():
        if not args.force:
            raise SystemExit(f"{rundir} exists, use --force to replace it")
        shutil.rmtree(rundir)
    rundir.mkdir(parents=True)

    # the settings of the export, complete, so that --set reaches into lists
    from qseek.exporters.hypodd import HypoDD

    settings: dict[str, Any] = {}
    if args.config:
        settings = json.loads(Path(args.config).read_text())
    if args.cc and not settings.get("cross_correlation"):
        settings["cross_correlation"] = {}
    exporter = HypoDD.model_validate(settings)
    config: dict[str, Any] = exporter.model_dump(mode="json")
    for text in args.set:
        apply_override(config, *parse_override(text))
    config_file = rundir / EXPORT_CONFIG
    config_file.write_text(json.dumps(config, indent=2) + "\n")

    created = datetime.now(timezone.utc)
    start = time.perf_counter()
    cmd = [
        qseek_executable(),
        "export",
        "hypodd",
        str(source.relative_to(example.path)),
        str((rundir / EXPORT_DIR).relative_to(example.path)),
        "--config",
        str(config_file.relative_to(example.path)),
    ]
    print(f"running {' '.join(cmd)} in {example.path}", flush=True)
    exit_code = subprocess.call(cmd, cwd=example.path)
    if exit_code:
        return exit_code

    directory = rundir / EXPORT_DIR
    run_binary("ph2dt", "ph2dt.inp", directory)
    run_binary("hypoDD", "hypoDD.inp", directory)
    n_relocated = write_relocations(source, rundir)
    wall_time = time.perf_counter() - start

    if (source / "search.json").exists():
        shutil.copy(source / "search.json", rundir / "search.json")
    summary = hypodd_summary(directory)
    runinfo = {
        "example": example.name,
        "run": name,
        "created": created.isoformat(),
        "source_run": args.source,
        "config": EXPORT_CONFIG,
        "overrides": args.set,
        "command": cmd[1:],
        "exit_code": 0,
        "wall_time_s": round(wall_time, 2),
        "environment": environment(),
        "hypodd": summary,
    }
    (rundir / RUNINFO_FILE).write_text(json.dumps(runinfo, indent=2) + "\n")

    print(
        f"hypoDD relocated {n_relocated} of {summary['n_events_exported']} detections"
    )
    print_table([(key, json.dumps(value)) for key, value in summary.items()])
    print_metrics(extract_metrics(example, name))
    # compare with the source run; `compare` with more runs replaces it
    return compare(argparse.Namespace(example=args.example, hypodd_run=name, runs=[]))


# Compare


@dataclass
class LocationSet:
    """Locations of the exported events in one run, keyed by HypoDD event ID."""

    run: str
    events: dict[int, Event]


def run_locations(
    example: Example, run: str, exported: list[ExportedEvent], max_dt: float
) -> LocationSet:
    """Pair the detections of a run with the exported events by origin time."""
    detections = load_detections(example.rundir(run), None)
    if not detections:
        raise SystemExit(f"run {run} has no detections")
    reference = [
        Event(time=ev.time, lat=0.0, lon=0.0, depth=0.0, magnitude=None)
        for ev in exported
    ]
    matches = match_events(detections, reference, max_dt)
    return LocationSet(
        run=run,
        events={exported[i_ref].id: detections[i_det] for i_ref, i_det, _ in matches},
    )


def relocated_locations(example: Example, run: str) -> LocationSet:
    """Locations of a HypoDD run, keyed by HypoDD event ID."""
    detections = load_detections(example.rundir(run), None)
    return LocationSet(
        run=run,
        events={int(ev.row["hypodd_id"]): ev for ev in detections if ev.row},
    )


def evaluate(
    directory: Path,
    workdir: Path,
    locations: dict[int, Event],
    exported: dict[int, ExportedEvent],
) -> dict[str, Any]:
    """Double-difference residuals of a location set, calculated by hypoDD.

    hypoDD runs one iteration with a damping so high that the locations stay in
    place, on the differential times of the HypoDD run: the catalog times `dt.ct`
    and, if the run has them, the cross-correlation times `dt.cc`. The travel
    times of each event are shifted to the origin time of the location set, so the
    origin times count as well. All data are used, without reweighting.
    """
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    for station_file in ("station.sel", "station.dat"):
        if (directory / station_file).exists():
            shutil.copy(directory / station_file, workdir / station_file)

    delays = {
        event_id: (ev.time - exported[event_id].hypodd_time).total_seconds()
        for event_id, ev in locations.items()
    }
    with (workdir / "event.sel").open("w") as f:
        for event_id, ev in sorted(locations.items()):
            t = exported[event_id].hypodd_time
            f.write(
                f"{t:%Y%m%d} {t:%H%M%S}{t.microsecond // 10000:02d}"
                f" {ev.lat:.6f} {ev.lon:.6f} {ev.depth / 1e3:.4f}"
                f" 0.0 0.0 0.0 0.0 {event_id}\n"
            )

    with (directory / "dt.ct").open() as fin, (workdir / "dt.ct").open("w") as fout:
        keep = False
        for line in fin:
            if line.startswith("#"):
                _, id1, id2 = line.split()
                keep = int(id1) in delays and int(id2) in delays
                if keep:
                    dt1, dt2 = delays[int(id1)], delays[int(id2)]
                    fout.write(line)
            elif keep:
                sta, tt1, tt2, weight, phase = line.split()
                fout.write(
                    f"{sta} {float(tt1) - dt1:.4f} {float(tt2) - dt2:.4f}"
                    f" {weight} {phase}\n"
                )

    if (directory / "dt.cc").exists():
        with (directory / "dt.cc").open() as fin, (workdir / "dt.cc").open("w") as fout:
            keep = False
            for line in fin:
                if line.startswith("#"):
                    _, id1, id2, _otc = line.split()
                    keep = int(id1) in delays and int(id2) in delays
                    if keep:
                        shift = delays[int(id1)] - delays[int(id2)]
                        fout.write(line)
                elif keep:
                    sta, dt, weight, phase = line.split()
                    fout.write(f"{sta} {float(dt) - shift:.6f} {weight} {phase}\n")

    # one iteration of one weighting set, catalog start, keep air-quakes, no clustering
    control = (directory / "hypoDD.inp").read_text().splitlines()
    out: list[str] = []
    state = "head"
    for line in control:
        values = line.split()
        if line.startswith("*") or not values:
            out.append(line)
            if line.startswith("* OBSCC"):
                state = "clustering"
            elif line.startswith("* ISTART"):
                state = "solution"
            continue
        if state == "clustering":
            out.append("0 0 -999 -999 -999")
            state = "head"
        elif state == "solution":
            out.append("2 2 0 1")
            out.append(f"1 1 0.5 -999 -999 1 0.5 -999 -999 {EVALUATION_DAMPING:g}")
            state = "sets"
        elif state == "sets" and len(values) == 10:
            continue
        else:
            out.append(line)
            state = "tail"
    (workdir / "hypoDD.inp").write_text("\n".join(out) + "\n")
    run_binary("hypoDD", "hypoDD.inp", workdir)

    # IDX of hypoDD.res: 1 cc P, 2 cc S, 3 catalog P, 4 catalog S
    data_types = {"1": "cc_P", "2": "cc_S", "3": "P", "4": "S"}
    residuals: dict[str, list[float]] = {key: [] for key in data_types.values()}
    with (workdir / "hypoDD.res").open() as f:
        for line in f:
            values = line.split()
            if len(values) < 9 or values[0] == "STA":
                continue
            residuals[data_types[values[4]]].append(float(values[6]))

    def stats(values: list[float]) -> dict[str, Any]:
        abs_values = [abs(v) for v in values]
        return {
            "n": len(values),
            "rms_ms": rounded(math.sqrt(sum(v * v for v in values) / len(values)), 1)
            if values
            else None,
            "abs_median_ms": rounded(median(abs_values), 1),
            "abs_p90_ms": rounded(percentile(abs_values, 90.0), 1),
        }

    result = {
        "all": stats(residuals["P"] + residuals["S"]),
        "P": stats(residuals["P"]),
        "S": stats(residuals["S"]),
    }
    if residuals["cc_P"] or residuals["cc_S"]:
        result["cc"] = stats(residuals["cc_P"] + residuals["cc_S"])
        result["cc_P"] = stats(residuals["cc_P"])
        result["cc_S"] = stats(residuals["cc_S"])
    return result


def location_metrics(
    events: list[Event], reference: list[Event], max_dt: float
) -> dict[str, Any]:
    xyz = local_xyz(events)
    nn = nearest_neighbor_distances(xyz) if len(xyz) > 1 else []
    nn_h = nearest_neighbor_distances([(x, y, 0.0) for x, y, _ in xyz])
    depths = [ev.depth for ev in events]
    metrics: dict[str, Any] = {
        "nn_distance_median_m": rounded(median(nn), 1),
        "nn_distance_horizontal_median_m": rounded(median(nn_h), 1),
        "depth_median_m": rounded(median(depths), 1),
        "depth_p10_m": rounded(percentile(depths, 10.0), 1),
        "depth_p90_m": rounded(percentile(depths, 90.0), 1),
    }
    if reference:
        matches = match_events(events, reference, max_dt)
        offsets, dz = [], []
        for i_ref, i_det, _ in matches:
            ref, det = reference[i_ref], events[i_det]
            offsets.append(distance(ref.lat, ref.lon, det.lat, det.lon))
            dz.append(det.depth - ref.depth)
        metrics["reference"] = {
            "n_matched": len(matches),
            "epicenter_offset_median_m": rounded(median(offsets), 1),
            "depth_offset_median_m": rounded(median(dz), 1),
            "depth_offset_abs_median_m": rounded(median([abs(v) for v in dz]), 1),
        }
    return metrics


def shift_metrics(events: list[Event], reference: list[Event]) -> dict[str, Any]:
    """Shift of the locations to the reference locations, of the same events.

    The relative shift removes the median shift first: HypoDD does not constrain
    the absolute position of a cluster well, the relative shift measures how
    differently the events are arranged.
    """
    a = local_xyz(reference + events)
    ref_xyz, ev_xyz = a[: len(reference)], a[len(reference) :]
    deltas = [
        tuple(e - r for e, r in zip(ev, ref, strict=True))
        for ev, ref in zip(ev_xyz, ref_xyz, strict=True)
    ]
    med = [median([d[i] for d in deltas]) or 0.0 for i in range(3)]
    relative = [math.dist(d, med) for d in deltas]
    return {
        "shift_horizontal_median_m": rounded(
            median([math.hypot(*d[:2]) for d in deltas]), 1
        ),
        "shift_depth_median_m": rounded(med[2], 1),
        "shift_east_median_m": rounded(med[0], 1),
        "shift_north_median_m": rounded(med[1], 1),
        "relative_shift_median_m": rounded(median(relative), 1),
        "relative_shift_p90_m": rounded(percentile(relative, 90.0), 1),
    }


def compare(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    hypodd_run = example.rundir(args.hypodd_run)
    directory = hypodd_run / EXPORT_DIR
    if not (directory / "dt.ct").exists():
        raise SystemExit(f"{args.hypodd_run} is not a HypoDD run of hypodd.py relocate")
    runinfo = json.loads((hypodd_run / RUNINFO_FILE).read_text())
    source_run = runinfo["source_run"]
    max_dt = example.settings.get("metrics", {}).get("pair_max_time_difference", 1.0)

    exported = event_ids(directory)
    exported_by_id = {ev.id: ev for ev in exported}
    runs = [source_run, *(r for r in args.runs if r != source_run)]
    sets = [run_locations(example, run, exported, max_dt) for run in runs]
    sets.append(relocated_locations(example, args.hypodd_run))
    common = set.intersection(*(set(s.events) for s in sets))
    if not common:
        raise SystemExit("the runs have no common events")
    common_ids = sorted(common)

    reference_settings = example.reference
    reference: list[Event] = []
    if reference_settings.get("catalog"):
        reference = load_reference(example.path / reference_settings["catalog"])
    ref_max_dt = reference_settings.get("max_time_difference", 3.0)

    hypodd_events = [sets[-1].events[i] for i in common_ids]
    results = {}
    for loc_set in sets:
        events = [loc_set.events[i] for i in common_ids]
        print(f"evaluating {loc_set.run} with hypoDD", flush=True)
        results[loc_set.run] = {
            "dd_residuals": evaluate(
                directory,
                hypodd_run / EVALUATION_DIR / loc_set.run,
                {i: loc_set.events[i] for i in common_ids},
                exported_by_id,
            ),
            **location_metrics(events, reference, ref_max_dt),
            "vs_hypodd": shift_metrics(events, hypodd_events),
        }

    comparison = {
        "example": example.name,
        "hypodd_run": args.hypodd_run,
        "source_run": source_run,
        "runs": [s.run for s in sets],
        "n_common_events": len(common_ids),
        "n_relocated": len(sets[-1].events),
        "hypodd": runinfo.get("hypodd", {}),
        "results": results,
    }
    out_file = hypodd_run / COMPARISON_FILE
    out_file.write_text(json.dumps(comparison, indent=2) + "\n")

    rows = [("metric", *comparison["runs"])]
    keys = (
        "dd_residuals.all.rms_ms",
        "dd_residuals.all.abs_median_ms",
        "dd_residuals.all.abs_p90_ms",
        "dd_residuals.P.abs_median_ms",
        "dd_residuals.S.abs_median_ms",
        "dd_residuals.cc.n",
        "dd_residuals.cc.abs_median_ms",
        "dd_residuals.cc.abs_p90_ms",
        "dd_residuals.cc_P.abs_median_ms",
        "dd_residuals.cc_S.abs_median_ms",
        "nn_distance_median_m",
        "nn_distance_horizontal_median_m",
        "depth_median_m",
        "depth_p10_m",
        "depth_p90_m",
        "reference.n_matched",
        "reference.epicenter_offset_median_m",
        "reference.depth_offset_median_m",
        "reference.depth_offset_abs_median_m",
        "vs_hypodd.shift_horizontal_median_m",
        "vs_hypodd.shift_depth_median_m",
        "vs_hypodd.relative_shift_median_m",
        "vs_hypodd.relative_shift_p90_m",
    )
    for key in keys:
        row = [key]
        for run in comparison["runs"]:
            node: Any = results[run]
            for part in key.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            row.append("-" if node is None else str(node))
        rows.append(tuple(row))
    print(f"{len(common_ids)} common events of {', '.join(comparison['runs'])}")
    print_table(rows)
    print(f"written to {out_file.relative_to(example.path)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    sub = commands.add_parser("relocate", help="relocate the detections of a run")
    sub.add_argument("example", help="example directory, e.g. campi-flegrei")
    sub.add_argument("source", help="run whose detections are relocated")
    sub.add_argument("run", nargs="?", help="name of the new run, <source>-hypodd")
    sub.add_argument("--config", help="JSON settings of `qseek export hypodd`")
    sub.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY.PATH=VALUE",
        help="override a setting of the export, e.g. ph2dt.max_separation=3000",
    )
    sub.add_argument(
        "--cc",
        action="store_true",
        help="cross-correlate the waveforms for dt.cc, with the default settings",
    )
    sub.add_argument("--force", action="store_true", help="replace the run")

    sub = commands.add_parser("compare", help="compare runs on their common events")
    sub.add_argument("example", help="example directory, e.g. campi-flegrei")
    sub.add_argument("hypodd_run", help="run of `relocate`")
    sub.add_argument("runs", nargs="*", help="other runs, e.g. ssst")

    args = parser.parse_args()
    return {"relocate": relocate, "compare": compare}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
