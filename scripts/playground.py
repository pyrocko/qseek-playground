"""Run the qseek playground examples and measure the results.

The script runs with the Python of the qseek checkout (see the justfile) and uses
only the standard library. It reads the documented outputs of a run: the detection
table `csv/detections.csv` and the log `qseek.log`.

Commands:
    config   write the search configuration of a run
    search   write the configuration, run `qseek search` and extract the metrics
    metrics  extract the metrics of a finished run
    compare  compare the metrics of a run with the baseline or another run
    bless    make the metrics of a run the baseline of the example
    runs     list the runs of an example with their key metrics
    catalog  download the reference catalog of an example
    dashboard  serve the dashboard to look at and compare the runs
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import csv
import hashlib
import json
import math
import os
import platform
import re
import resource
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
EARTH_RADIUS = 6371e3
RUNS_DIR = "runs"
BASELINE_DIR = "baseline"
METRICS_FILE = "metrics.json"
RUNINFO_FILE = "playground-run.json"
DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"
RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RUNNING_TIMEOUT = 120.0  # s since the last log line until a run counts as stopped


# Metrics shown by `compare`. `better` gives the direction of an improvement; metrics
# with a direction are gated: a run fails the comparison when it is worse than the
# reference by more than max(tolerance, rel_tolerance * |reference|). The tolerances
# can be overridden per example in `[tolerances]` of example.toml.
@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    better: Literal["higher", "lower"] | None = None
    tolerance: float = 0.0
    rel_tolerance: float = 0.0
    same_machine: bool = False  # gate only when host and GPU match the reference


METRICS = (
    MetricSpec("detections.n_events", "detections"),
    MetricSpec("detections.n_events_min_picks", "detections with min. picks"),
    MetricSpec("detections.picks_median", "picks per event, median", "higher", 1.0),
    MetricSpec(
        "detections.rms_median_s",
        "residual RMS, median [s]",
        "lower",
        tolerance=0.005,
        rel_tolerance=0.1,
    ),
    MetricSpec("detections.semblance_median", "semblance, median"),
    MetricSpec("detections.nn_distance_median_m", "nearest neighbor, median [m]"),
    MetricSpec(
        "detections.uncertainty_horizontal_median_m", "uncertainty horiz., median [m]"
    ),
    MetricSpec(
        "detections.uncertainty_vertical_median_m", "uncertainty vert., median [m]"
    ),
    MetricSpec("detections.n_magnitudes", "detections with magnitude"),
    MetricSpec(
        "runtime.search_time_s",
        "search time [s]",
        "lower",
        tolerance=5.0,
        rel_tolerance=0.2,
        same_machine=True,
    ),
    MetricSpec("runtime.wall_time_s", "wall time [s]"),
    MetricSpec("runtime.peak_rss_mib", "peak memory [MiB]"),
    MetricSpec("runtime.processing_rate_mib_s_median", "processing rate [MiB/s]"),
    MetricSpec("reference.n_matched", "reference events detected", "higher"),
    MetricSpec("reference.recall", "recall"),
    MetricSpec(
        "reference.epicenter_offset_median_m",
        "epicenter offset, median [m]",
        "lower",
        tolerance=25.0,
        rel_tolerance=0.1,
    ),
    MetricSpec("reference.epicenter_offset_p90_m", "epicenter offset, p90 [m]"),
    MetricSpec(
        "reference.depth_offset_abs_median_m",
        "depth offset |dz|, median [m]",
        "lower",
        tolerance=25.0,
        rel_tolerance=0.1,
    ),
    MetricSpec("reference.depth_offset_median_m", "depth offset dz, median [m]"),
    MetricSpec(
        "reference.time_offset_abs_median_s",
        "origin time offset, median [s]",
        "lower",
        tolerance=0.05,
    ),
    MetricSpec("reference.magnitude_difference_median", "magnitude - reference"),
)

# Metrics of the paired detections of two runs, B against A, one value per
# comparison. Lost well-constrained detections are gated: B fails the comparison when
# it loses more than max(tolerance, rel_tolerance * well-constrained detections of A).
ASSOCIATION_METRICS = (
    MetricSpec("association.n_paired", "paired detections"),
    MetricSpec(
        "association.n_lost_good",
        "lost: only in A, min. picks",
        "lower",
        tolerance=2.0,
        rel_tolerance=0.01,
    ),
    MetricSpec("association.n_new_good", "new: only in B, min. picks"),
    MetricSpec("association.n_only_a", "only in A, all"),
    MetricSpec("association.n_only_b", "only in B, all"),
    MetricSpec("association.n_unchanged", "unchanged pairs, all columns"),
    MetricSpec("association.shift_horizontal_median_m", "horizontal shift, median [m]"),
    MetricSpec("association.shift_horizontal_p90_m", "horizontal shift, p90 [m]"),
    MetricSpec("association.shift_depth_median_m", "depth shift B - A, median [m]"),
    MetricSpec("association.shift_depth_abs_median_m", "|depth shift|, median [m]"),
    MetricSpec(
        "association.time_shift_abs_median_s", "|origin time shift|, median [s]"
    ),
    MetricSpec("association.picks_delta_median", "picks B - A, median"),
    MetricSpec("association.picks_gained_fraction", "pairs with more picks in B"),
    MetricSpec("association.picks_lost_fraction", "pairs with fewer picks in B"),
    MetricSpec("association.rms_delta_median_s", "RMS B - A, median [s]"),
    MetricSpec("association.rms_improved_fraction", "pairs with lower RMS in B"),
    MetricSpec("association.semblance_delta_median", "semblance B - A, median"),
    MetricSpec("association.magnitude_delta_median", "magnitude B - A, median"),
)


# Examples


@dataclass
class Example:
    name: str
    path: Path
    settings: dict[str, Any]

    @classmethod
    def load(cls, name: str) -> Example:
        path = (ROOT / name).resolve()
        settings_file = path / "example.toml"
        if not settings_file.exists():
            raise SystemExit(f"{name}: no example.toml in {path}")
        with settings_file.open("rb") as f:
            settings = tomllib.load(f)
        return cls(name=path.name, path=path, settings=settings)

    @property
    def runs_dir(self) -> Path:
        return self.path / RUNS_DIR

    @property
    def baseline_dir(self) -> Path:
        return self.path / BASELINE_DIR

    @property
    def baseline(self) -> Path:
        return self.baseline_dir / METRICS_FILE

    @property
    def reference(self) -> dict[str, Any]:
        return self.settings.get("reference", {})

    def rundir(self, run: str) -> Path:
        """Run directory; the baseline is a run directory of its own."""
        if run == "baseline":
            return self.baseline_dir
        return self.runs_dir / run

    def default_config(self) -> str:
        return self.settings.get("config", f"{self.name}.json")


def check_run_name(name: str) -> str:
    """Refuse run names that are reserved or leave the runs directory."""
    if name == "baseline":
        raise SystemExit("the run name baseline is reserved")
    if not RUN_NAME.fullmatch(name):
        raise SystemExit(
            f"invalid run name {name!r}: use letters, digits, '.', '_' and '-'"
        )
    return name


# Search configuration


def parse_override(text: str) -> tuple[list[str], Any]:
    """Parse `key.path=value`; the value is JSON, or a string if it is not."""
    if "=" not in text:
        raise SystemExit(f"invalid override {text!r}, expected key.path=value")
    key, value = text.split("=", 1)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    return key.split("."), parsed


def apply_override(config: dict[str, Any], path: list[str], value: Any) -> None:
    node: Any = config
    for i, key in enumerate(path[:-1]):
        if isinstance(node, list):
            node = node[int(key)]
            continue
        if key not in node or not isinstance(node[key], (dict, list)):
            node[key] = [] if path[i + 1].isdigit() else {}
        node = node[key]
    last = path[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def write_config(
    example: Example,
    run: str,
    config_file: str | None,
    overrides: list[str],
    ssst_from: str | None,
) -> tuple[Path, dict[str, Any]]:
    """Write the configuration of a run to `<example>/runs/<run>.json`."""
    config_file = config_file or example.default_config()
    config = json.loads((example.path / config_file).read_text())

    applied: dict[str, Any] = {}
    check_run_name(run)
    if ssst_from:
        check_run_name(ssst_from)
        if not example.rundir(ssst_from).exists():
            raise SystemExit(f"run {ssst_from} does not exist in {example.runs_dir}")
        applied["station_corrections"] = {
            "corrections": "SourceSpecificStationCorrections",
            "import_rundirs": [f"{RUNS_DIR}/{ssst_from}"],
        }
    for text in overrides:
        path, value = parse_override(text)
        applied[".".join(path)] = value
    for key, value in applied.items():
        apply_override(config, key.split("."), value)

    # qseek creates the run directory <project_dir>/<config stem>, relative to the
    # working directory, which is the example directory.
    config["project_dir"] = RUNS_DIR
    example.runs_dir.mkdir(exist_ok=True)
    run_config = example.runs_dir / f"{run}.json"
    run_config.write_text(json.dumps(config, indent=2) + "\n")
    info = {
        "config": config_file,
        "overrides": applied,
        "config_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
    }
    return run_config, info


# Environment


def run_text(cmd: list[str], cwd: Path | None = None) -> str | None:
    try:
        out = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def qseek_checkout() -> Path | None:
    with contextlib.suppress(metadata.PackageNotFoundError):
        dist = metadata.distribution("qseek")
        direct_url = dist.read_text("direct_url.json")
        if direct_url:
            url = json.loads(direct_url).get("url", "")
            if url.startswith("file://"):
                return Path(url.removeprefix("file://"))
    return None


def environment() -> dict[str, Any]:
    env: dict[str, Any] = {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "gpu": None,
        "qseek_version": None,
        "qseek_commit": None,
        "qseek_dirty": None,
        "plugins": {},
    }
    with contextlib.suppress(metadata.PackageNotFoundError):
        env["qseek_version"] = metadata.version("qseek")
    checkout = qseek_checkout()
    if checkout:
        env["qseek_commit"] = run_text(["git", "rev-parse", "HEAD"], cwd=checkout)
        status = run_text(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout
        )
        env["qseek_dirty"] = bool(status) if status is not None else None
    for entry_point in metadata.entry_points(group="qseek.modules"):
        dist = entry_point.dist
        env["plugins"][entry_point.name] = dist.version if dist else None
    if shutil.which("nvidia-smi"):
        gpus = run_text(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
        env["gpu"] = gpus.splitlines()[0] if gpus else None
    return env


# Search


def qseek_executable() -> str:
    exe = Path(sys.executable).parent / "qseek"
    if not exe.exists():
        raise SystemExit(f"qseek is not installed in {sys.prefix}, run `just setup`")
    return str(exe)


def peak_rss_mib() -> float:
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # kilobytes on Linux, bytes on macOS
    return rss / 1024**2 if sys.platform == "darwin" else rss / 1024


def search(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    rundir = example.rundir(check_run_name(args.run))
    if rundir.exists():
        if not args.force:
            raise SystemExit(f"{rundir} exists, use --force to replace it")
        shutil.rmtree(rundir)

    run_config, info = write_config(
        example, args.run, args.config, args.set, args.ssst_from
    )
    cmd = [
        qseek_executable(),
        "--quiet",
        "search",
        str(run_config.relative_to(example.path)),
    ]
    print(f"running {' '.join(cmd)} in {example.path}", flush=True)

    created = datetime.now(timezone.utc)
    start = time.perf_counter()
    exit_code = subprocess.call(cmd, cwd=example.path)
    wall_time = time.perf_counter() - start

    runinfo = {
        "example": example.name,
        "run": args.run,
        "created": created.isoformat(),
        **info,
        "command": cmd[1:],
        "exit_code": exit_code,
        "wall_time_s": round(wall_time, 2),
        "peak_rss_mib": round(peak_rss_mib(), 1),
        "environment": environment(),
    }
    if not rundir.exists():
        print(f"qseek did not create {rundir}", file=sys.stderr)
        return exit_code or 1
    (rundir / RUNINFO_FILE).write_text(json.dumps(runinfo, indent=2) + "\n")

    metrics = extract_metrics(example, args.run)
    print_metrics(metrics)
    return exit_code


# Metrics


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * q / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def median(values: list[float]) -> float | None:
    return percentile(values, 50.0)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def rounded(value: float | None, ndigits: int = 3) -> float | None:
    return None if value is None else round(value, ndigits)


def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * (
        math.sin(dlmb / 2) ** 2
    )
    return 2 * EARTH_RADIUS * math.asin(math.sqrt(a))


def to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    number = float(value)
    return number if math.isfinite(number) else None


@dataclass
class Event:
    time: datetime
    lat: float
    lon: float
    depth: float  # m
    magnitude: float | None
    row: dict[str, str] | None = None


def load_detections(rundir: Path, magnitude_column: str | None) -> list[Event]:
    table = rundir / "csv" / "detections.csv"
    if not table.exists():
        return []
    with table.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if rows and magnitude_column is None:
        magnitude_column = next(
            (
                col
                for col in rows[0]
                if re.match(r"^M\w*-", col)
                and "-error-" not in col
                and "-n-stations-" not in col
            ),
            None,
        )
    return [
        Event(
            time=datetime.fromisoformat(row["time"]),
            lat=float(row["lat"]),
            lon=float(row["lon"]),
            depth=float(row["depth"]),
            magnitude=to_float(row.get(magnitude_column)) if magnitude_column else None,
            row=row,
        )
        for row in rows
    ]


def load_reference(path: Path) -> list[Event]:
    """Load an event catalog in the FDSN text format."""
    events = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("|")
        time_ = datetime.fromisoformat(fields[1])
        if time_.tzinfo is None:
            time_ = time_.replace(tzinfo=timezone.utc)
        events.append(
            Event(
                time=time_,
                lat=float(fields[2]),
                lon=float(fields[3]),
                depth=float(fields[4]) * 1e3,
                magnitude=to_float(fields[10]),
            )
        )
    return events


def match_events(
    detections: list[Event], reference: list[Event], max_dt: float
) -> list[tuple[int, int, float]]:
    """Match detections one-to-one to reference events, closest origin time first."""
    by_time = sorted(
        (det.time.timestamp(), i_det) for i_det, det in enumerate(detections)
    )
    times = [t for t, _ in by_time]
    pairs = []
    for i_ref, ref in enumerate(reference):
        t_ref = ref.time.timestamp()
        lo = bisect.bisect_left(times, t_ref - max_dt)
        hi = bisect.bisect_right(times, t_ref + max_dt)
        for t_det, i_det in by_time[lo:hi]:
            dt = round(t_det - t_ref, 6)
            pairs.append((abs(dt), i_ref, i_det, dt))
    pairs.sort()
    used_ref: set[int] = set()
    used_det: set[int] = set()
    matches = []
    for _, i_ref, i_det, dt in pairs:
        if i_ref in used_ref or i_det in used_det:
            continue
        used_ref.add(i_ref)
        used_det.add(i_det)
        matches.append((i_ref, i_det, dt))
    return sorted(matches)


def parse_duration(text: str) -> float:
    days = 0
    if "day" in text:
        day_part, text = text.split(",", 1)
        days = int(day_part.split()[0])
    hours, minutes, seconds = text.strip().split(":")
    return days * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def log_metrics(rundir: Path) -> dict[str, Any]:
    log = rundir / "qseek.log"
    if not log.exists():
        return {}
    text = log.read_text(errors="replace")
    finished = re.findall(r"finished search in (.+)$", text, re.MULTILINE)
    rates = [float(r) for r in re.findall(r"processing rate ([\d.]+)MiB/s", text)]
    batches = [
        parse_duration(d)
        for d in re.findall(r"processed - batch \d+/\d+ .* in (\S+)$", text, re.M)
    ]
    return {
        "search_time_s": rounded(parse_duration(finished[-1]), 2) if finished else None,
        "completed": bool(finished),
        "n_batches": len(batches),
        "batch_time_s_median": rounded(median(batches)),
        "batch_time_s_p95": rounded(percentile(batches, 95.0)),
        "processing_rate_mib_s_median": rounded(median(rates), 1),
    }


def column(events: list[Event], name: str) -> list[float]:
    values = (to_float(ev.row.get(name)) for ev in events if ev.row)
    return [v for v in values if v is not None]


def local_xyz(events: list[Event]) -> list[tuple[float, float, float]]:
    """Cartesian coordinates in meters: east, north and depth."""
    if not events:
        return []
    lat0 = sum(ev.lat for ev in events) / len(events)
    lon0 = sum(ev.lon for ev in events) / len(events)
    cos_lat = math.cos(math.radians(lat0))
    return [
        (
            math.radians(ev.lon - lon0) * cos_lat * EARTH_RADIUS,
            math.radians(ev.lat - lat0) * EARTH_RADIUS,
            ev.depth,
        )
        for ev in events
    ]


def nearest_neighbor_distances(
    points: list[tuple[float, float, float]],
) -> list[float]:
    """3D distance of every point to its nearest neighbor.

    Sweeps outward along the sorted east coordinate and stops once the east
    distance alone exceeds the closest neighbor found.
    """
    order = sorted(range(len(points)), key=lambda i: points[i][0])
    xs = [points[i][0] for i in order]
    distances = [math.inf] * len(points)
    for rank, i in enumerate(order):
        best = math.inf
        for step in (1, -1):
            j = rank + step
            while 0 <= j < len(order) and abs(xs[j] - xs[rank]) < best:
                best = min(best, math.dist(points[i], points[order[j]]))
                j += step
        distances[i] = best
    return [d for d in distances if math.isfinite(d)]


def picks_of(event: Event) -> float:
    return to_float((event.row or {}).get("n_picks")) or 0.0


def associate(
    det_a: list[Event], det_b: list[Event], min_picks: int, max_dt: float
) -> tuple[dict[str, Any], list[list[float]]]:
    """Pair the detections of two runs one-to-one by origin time, closest first.

    The statistics of the shifts and changes use the pairs with at least `min_picks`
    picks in A or in B.

    Returns:
        The summary of the comparison and the pairs as
        [index A, index B, dt B - A in s, horizontal shift in m, dz B - A in m].
    """
    matches = match_events(det_b, det_a, max_dt)
    good_a = [picks_of(ev) >= min_picks for ev in det_a]
    good_b = [picks_of(ev) >= min_picks for ev in det_b]
    paired_a = {i_a for i_a, _, _ in matches}
    paired_b = {i_b for _, i_b, _ in matches}

    pairs = []
    unchanged = 0
    dh, dz, dt_abs, d_picks, d_rms, d_semblance, d_magnitude = ([] for _ in range(7))
    for i_a, i_b, dt in matches:
        a, b = det_a[i_a], det_b[i_b]
        h = distance(a.lat, a.lon, b.lat, b.lon)
        z = b.depth - a.depth
        pairs.append([i_a, i_b, round(dt, 3), round(h, 1), round(z, 1)])
        # every column of the detection table, location, picks, residuals, magnitudes
        if a.row == b.row:
            unchanged += 1
        if not (good_a[i_a] or good_b[i_b]):
            continue
        dh.append(h)
        dz.append(z)
        dt_abs.append(abs(dt))
        d_picks.append(picks_of(b) - picks_of(a))
        for values, col in ((d_rms, "rms"), (d_semblance, "semblance")):
            va = to_float((a.row or {}).get(col))
            vb = to_float((b.row or {}).get(col))
            if va is not None and vb is not None:
                values.append(vb - va)
        if a.magnitude is not None and b.magnitude is not None:
            d_magnitude.append(b.magnitude - a.magnitude)

    def fraction(values: list[float], positive: bool) -> float | None:
        if not values:
            return None
        n = sum(1 for v in values if (v > 0 if positive else v < 0))
        return rounded(n / len(values))

    summary = {
        "max_time_difference_s": max_dt,
        "min_picks": min_picks,
        "n_a": len(det_a),
        "n_b": len(det_b),
        "n_a_good": sum(good_a),
        "n_b_good": sum(good_b),
        "n_paired": len(matches),
        "n_paired_good": len(dh),
        "n_only_a": len(det_a) - len(matches),
        "n_only_b": len(det_b) - len(matches),
        "n_lost_good": sum(1 for i, g in enumerate(good_a) if g and i not in paired_a),
        "n_new_good": sum(1 for i, g in enumerate(good_b) if g and i not in paired_b),
        "n_unchanged": unchanged,
        "identical": len(det_a) == len(det_b) == len(matches) == unchanged,
        "shift_horizontal_median_m": rounded(median(dh), 1),
        "shift_horizontal_p90_m": rounded(percentile(dh, 90.0), 1),
        "shift_depth_median_m": rounded(median(dz), 1),
        "shift_depth_abs_median_m": rounded(median([abs(v) for v in dz]), 1),
        "time_shift_abs_median_s": rounded(median(dt_abs)),
        "picks_delta_median": rounded(median(d_picks), 1),
        "picks_gained_fraction": fraction(d_picks, positive=True),
        "picks_lost_fraction": fraction(d_picks, positive=False),
        "rms_delta_median_s": rounded(median(d_rms), 4),
        "rms_improved_fraction": fraction(d_rms, positive=False),
        "semblance_delta_median": rounded(median(d_semblance), 4),
        "magnitude_delta_median": rounded(median(d_magnitude), 2),
    }
    return summary, pairs


def run_detections(example: Example, name: str) -> list[Event] | None:
    rundir = example.rundir(name)
    if not (rundir / "csv" / "detections.csv").exists():
        return None
    return load_detections(rundir, example.reference.get("magnitude_column"))


def associate_runs(
    example: Example, name_a: str, name_b: str
) -> tuple[dict[str, Any] | None, list[list[float]]]:
    det_a = run_detections(example, name_a)
    det_b = run_detections(example, name_b)
    if det_a is None or det_b is None:
        return None, []
    settings = example.settings.get("metrics", {})
    return associate(
        det_a,
        det_b,
        settings.get("min_picks", 8),
        settings.get("pair_max_time_difference", 1.0),
    )


def extract_metrics(example: Example, run: str) -> dict[str, Any]:
    if run == "baseline":
        raise SystemExit("the baseline keeps the metrics of its run, bless it again")
    rundir = example.rundir(run)
    if not rundir.exists():
        raise SystemExit(f"run {run} does not exist in {example.runs_dir}")
    settings = example.settings.get("metrics", {})
    reference_settings = example.reference
    min_picks = settings.get("min_picks", 8)

    runinfo_file = rundir / RUNINFO_FILE
    runinfo = json.loads(runinfo_file.read_text()) if runinfo_file.exists() else {}

    detections = load_detections(rundir, reference_settings.get("magnitude_column"))
    picks = column(detections, "n_picks")
    magnitudes = [ev.magnitude for ev in detections if ev.magnitude is not None]
    good = [ev for ev in detections if picks_of(ev) >= min_picks]
    nn_distances = nearest_neighbor_distances(local_xyz(good)) if len(good) > 1 else []
    detection_metrics = {
        "n_events": len(detections),
        "n_events_min_picks": sum(1 for p in picks if p >= min_picks),
        "min_picks": min_picks,
        "picks_median": rounded(median(picks), 1),
        "picks_mean": rounded(mean(picks), 2),
        "stations_median": rounded(median(column(detections, "n_stations")), 1),
        "rms_median_s": rounded(median(column(detections, "rms")), 4),
        "semblance_median": rounded(median(column(detections, "semblance")), 4),
        "semblance_min": rounded(min(column(detections, "semblance"), default=None), 4),
        # clustering of the detections with min. picks; lower is tighter
        "nn_distance_median_m": rounded(median(nn_distances), 1),
        "azimuthal_coverage_median_deg": rounded(
            median(column(detections, "azimuthal_coverage")), 1
        ),
        "uncertainty_horizontal_median_m": rounded(
            median(column(detections, "uncertainty_horizontal")), 1
        ),
        "uncertainty_vertical_median_m": rounded(
            median(column(detections, "uncertainty_vertical")), 1
        ),
        "n_magnitudes": len(magnitudes),
        "magnitude_min": rounded(min(magnitudes, default=None), 2),
        "magnitude_max": rounded(max(magnitudes, default=None), 2),
    }

    reference_metrics: dict[str, Any] = {}
    catalog = reference_settings.get("catalog")
    if catalog:
        reference = load_reference(example.path / catalog)
        max_dt = reference_settings.get("max_time_difference", 3.0)
        matches = match_events(detections, reference, max_dt)
        offsets, dz, dt, dmag = [], [], [], []
        for i_ref, i_det, time_diff in matches:
            ref, det = reference[i_ref], detections[i_det]
            offsets.append(distance(ref.lat, ref.lon, det.lat, det.lon))
            dz.append(det.depth - ref.depth)
            dt.append(abs(time_diff))
            if det.magnitude is not None and ref.magnitude is not None:
                dmag.append(det.magnitude - ref.magnitude)

        matched = {i_ref for i_ref, _, _ in matches}
        by_magnitude: dict[str, list[int]] = {}
        for i_ref, ref in enumerate(reference):
            label = "none" if ref.magnitude is None else f"M{math.floor(ref.magnitude)}"
            counts = by_magnitude.setdefault(label, [0, 0])
            counts[0] += i_ref in matched
            counts[1] += 1

        reference_metrics = {
            "catalog": catalog,
            "max_time_difference_s": max_dt,
            "n_events": len(reference),
            "n_matched": len(matches),
            "recall": rounded(len(matches) / len(reference)) if reference else None,
            "recall_by_magnitude": {
                label: f"{found}/{total}"
                for label, (found, total) in sorted(by_magnitude.items())
            },
            "n_detections_not_in_reference": len(detections) - len(matches),
            "epicenter_offset_median_m": rounded(median(offsets), 1),
            "epicenter_offset_p90_m": rounded(percentile(offsets, 90.0), 1),
            "depth_offset_median_m": rounded(median(dz), 1),
            "depth_offset_abs_median_m": rounded(median([abs(d) for d in dz]), 1),
            "time_offset_abs_median_s": rounded(median(dt)),
            "magnitude_difference_median": rounded(median(dmag), 2),
            "missed": [
                {
                    "time": ref.time.isoformat(),
                    "magnitude": ref.magnitude,
                    "lat": ref.lat,
                    "lon": ref.lon,
                    "depth_m": ref.depth,
                }
                for i_ref, ref in enumerate(reference)
                if i_ref not in matched
            ],
        }

    runtime = {
        "exit_code": runinfo.get("exit_code"),
        "wall_time_s": runinfo.get("wall_time_s"),
        "peak_rss_mib": runinfo.get("peak_rss_mib"),
        **log_metrics(rundir),
    }

    metrics = {
        "schema": SCHEMA_VERSION,
        "example": example.name,
        "run": run,
        "created": runinfo.get("created"),
        "config": {
            key: runinfo.get(key) for key in ("config", "overrides", "config_sha256")
        },
        "environment": runinfo.get("environment", {}),
        "runtime": runtime,
        "detections": detection_metrics,
        "reference": reference_metrics,
    }
    (rundir / METRICS_FILE).write_text(json.dumps(metrics, indent=2) + "\n")
    return metrics


def get(metrics: dict[str, Any], key: str) -> Any:
    node: Any = metrics
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def print_table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        cells = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        print("  ".join(cells).rstrip())


def print_metrics(metrics: dict[str, Any]) -> None:
    env = metrics.get("environment", {})
    commit = (env.get("qseek_commit") or "unknown")[:10]
    dirty = " (dirty)" if env.get("qseek_dirty") else ""
    print(f"\n{metrics['example']} / {metrics['run']}: qseek {commit}{dirty}")
    for spec in METRICS:
        value = get(metrics, spec.key)
        if value is not None:
            print(f"  {spec.label:<34} {fmt(value)}")
    by_mag = get(metrics, "reference.recall_by_magnitude")
    if by_mag:
        recall = ", ".join(f"{k}: {v}" for k, v in by_mag.items())
        print(f"  {'recall by magnitude':<34} {recall}")
    missed = get(metrics, "reference.missed") or []
    for ev in missed:
        print(f"  missed {ev['time']} M{fmt(ev['magnitude'])}")


def metrics(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    print_metrics(extract_metrics(example, args.run))
    return 0


# Compare


def load_metrics(example: Example, name: str, extract: bool = True) -> dict[str, Any]:
    path = example.rundir(name) / METRICS_FILE
    if not path.exists():
        if extract and name != "baseline" and example.rundir(name).exists():
            return extract_metrics(example, name)
        raise SystemExit(f"no metrics at {path}")
    return json.loads(path.read_text())


def same_machine(a: dict[str, Any], b: dict[str, Any]) -> bool:
    env_a, env_b = a.get("environment", {}), b.get("environment", {})
    return env_a.get("host") == env_b.get("host") and env_a.get("gpu") == env_b.get(
        "gpu"
    )


def compare_metrics(
    current: dict[str, Any],
    reference: dict[str, Any],
    tolerances: dict[str, dict[str, float]],
    association: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare the metrics of a run with a reference run.

    Returns:
        The rows of the comparison, the regressions and the reference events that
        are newly missed or newly detected.
    """
    machine = same_machine(current, reference)
    rows = []
    regressions = []
    for spec in METRICS:
        ref_value, cur_value = get(reference, spec.key), get(current, spec.key)
        row: dict[str, Any] = {
            "key": spec.key,
            "label": spec.label,
            "better": spec.better,
            "reference": ref_value,
            "current": cur_value,
            "delta": None,
            "limit": None,
            "gated": spec.better is not None and (machine or not spec.same_machine),
            "status": "",
        }
        if isinstance(ref_value, (int, float)) and isinstance(cur_value, (int, float)):
            delta = cur_value - ref_value
            row["delta"] = delta
            if spec.better is not None:
                override = tolerances.get(spec.key, {})
                tolerance = override.get("tolerance", spec.tolerance)
                rel_tolerance = override.get("rel_tolerance", spec.rel_tolerance)
                limit = max(tolerance, rel_tolerance * abs(ref_value))
                row["limit"] = limit
                worse = -delta if spec.better == "higher" else delta
                if worse > limit:
                    row["status"] = "regression" if row["gated"] else "worse"
                    if row["gated"]:
                        regressions.append(spec.label)
                elif worse < -limit:
                    row["status"] = "better"
        rows.append(row)

    association_rows = []
    for spec in ASSOCIATION_METRICS if association else ():
        value = association.get(spec.key.split(".", 1)[1])
        row = {
            "key": spec.key,
            "label": spec.label,
            "better": spec.better,
            "value": value,
            "limit": None,
            "gated": spec.better is not None,
            "status": "",
        }
        if spec.better is not None and isinstance(value, (int, float)):
            override = tolerances.get(spec.key, {})
            limit = max(
                override.get("tolerance", spec.tolerance),
                override.get("rel_tolerance", spec.rel_tolerance)
                * association["n_a_good"],
            )
            row["limit"] = limit
            if value > limit:
                row["status"] = "regression"
                regressions.append(spec.label)
        association_rows.append(row)

    ref_missed = {ev["time"] for ev in get(reference, "reference.missed") or []}
    cur_missed = {ev["time"]: ev for ev in get(current, "reference.missed") or []}
    return {
        "association": (
            {**association, "rows": association_rows} if association else None
        ),
        "rows": rows,
        "regressions": regressions,
        "same_machine": machine,
        "newly_missed": [cur_missed[t] for t in sorted(set(cur_missed) - ref_missed)],
        "newly_detected": sorted(ref_missed - set(cur_missed)),
    }


def compare(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    current = load_metrics(example, args.run)
    reference = load_metrics(example, args.against)
    association, _ = associate_runs(example, args.against, args.run)
    result = compare_metrics(
        current, reference, example.settings.get("tolerances", {}), association
    )
    if args.run != "baseline":
        out = example.rundir(args.run) / f"comparison-{args.against}.json"
        out.write_text(json.dumps(result, indent=2) + "\n")

    env_ref = reference.get("environment", {})
    env_cur = current.get("environment", {})
    print(
        f"{example.name}: {args.run} "
        f"(qseek {(env_cur.get('qseek_commit') or '?')[:10]}"
        f"{', dirty' if env_cur.get('qseek_dirty') else ''}) "
        f"vs {args.against} (qseek {(env_ref.get('qseek_commit') or '?')[:10]})"
    )
    if not result["same_machine"]:
        print(
            f"note: different machine ({env_ref.get('host')}, {env_ref.get('gpu')}),"
            " runtime is not gated"
        )
    if association:
        print(
            f"\npaired detections of B ({args.run}) and A ({args.against}), within"
            f" {association['max_time_difference_s']} s origin time; shifts and"
            f" changes of the pairs with >= {association['min_picks']} picks in A or B"
        )
        if association["identical"]:
            print("  B is identical to A")
        print_table(
            [
                (
                    f"  {row['label']}",
                    fmt(row["value"]),
                    "WORSE" if row["status"] == "regression" else "",
                )
                for row in result["association"]["rows"]
            ]
        )
        print()
    else:
        print("\nno detections of both runs, skipping the paired comparison\n")
    status_text = {
        "regression": "WORSE",
        "worse": "worse (other machine)",
        "better": "better",
        "": "",
    }
    rows = [
        (
            row["label"],
            fmt(row["reference"]),
            fmt(row["current"]),
            fmt(row["delta"]),
            status_text[row["status"]],
        )
        for row in result["rows"]
    ]
    print_table([("metric", args.against, args.run, "delta", ""), *rows])

    for ev in result["newly_missed"]:
        print(f"newly missed: {ev['time']} M{fmt(ev['magnitude'])}")
    for time_ in result["newly_detected"]:
        print(f"newly detected: {time_}")

    if result["regressions"]:
        print(f"\nREGRESSION: {', '.join(result['regressions'])}")
        return 1
    print("\nno regressions")
    return 0


def bless(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    metrics_ = load_metrics(example, check_run_name(args.run))
    if metrics_.get("runtime", {}).get("completed") is False:
        raise SystemExit(f"run {args.run} did not complete, not blessing it")
    rundir = example.rundir(args.run)
    # The baseline is committed: its metrics, detections and resolved configuration,
    # so runs can be compared with it after the run directory is gone. The installed
    # plugins stay in the run directory.
    sources = [rundir / "csv" / "detections.csv", rundir / "search.json"]
    for source in sources:
        if not source.exists():
            raise SystemExit(f"{source} is missing, not blessing run {args.run}")
    metrics_.get("environment", {}).pop("plugins", None)
    staging = example.path / f".{BASELINE_DIR}.new"
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "csv").mkdir(parents=True)
    (staging / METRICS_FILE).write_text(json.dumps(metrics_, indent=2) + "\n")
    shutil.copy(sources[0], staging / "csv")
    shutil.copy(sources[1], staging)
    if example.baseline_dir.exists():
        old = example.path / f".{BASELINE_DIR}.old"
        if old.exists():
            shutil.rmtree(old)
        example.baseline_dir.rename(old)
        staging.rename(example.baseline_dir)
        shutil.rmtree(old)
    else:
        staging.rename(example.baseline_dir)
    print(f"wrote {example.baseline_dir.relative_to(ROOT)}/ from run {args.run}")
    return 0


def runs(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    header = ("run", "created", "qseek", "events", "min. picks", "lost", "new")
    rows = [(*header, "shift [m]", "ref.", "time [s]")]
    entries = []
    if example.baseline.exists():
        entries.append(("baseline", json.loads(example.baseline.read_text())))
    for path in sorted(example.runs_dir.glob(f"*/{METRICS_FILE}")):
        entries.append((path.parent.name, json.loads(path.read_text())))
    for name, m in entries:
        association = None
        if name != "baseline" and example.baseline.exists():
            association, _ = associate_runs(example, "baseline", name)
        association = association or {}
        env = m.get("environment", {})
        commit = (env.get("qseek_commit") or "?")[:8] + (
            "+" if env.get("qseek_dirty") else ""
        )
        matched = get(m, "reference.n_matched")
        total = get(m, "reference.n_events")
        rows.append(
            (
                name,
                (m.get("created") or "-")[:16],
                commit,
                fmt(get(m, "detections.n_events")),
                fmt(get(m, "detections.n_events_min_picks")),
                fmt(association.get("n_lost_good")),
                fmt(association.get("n_new_good")),
                fmt(association.get("shift_horizontal_median_m")),
                f"{matched}/{total}" if matched is not None else "-",
                fmt(get(m, "runtime.search_time_s")),
            )
        )
    print_table(rows)
    return 0


def catalog(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    url = example.reference.get("url")
    path = example.reference.get("catalog")
    if not url or not path:
        raise SystemExit(
            f"{example.name}: no reference url and catalog in example.toml"
        )
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    (example.path / path).write_bytes(data)
    n_events = sum(1 for line in data.decode().splitlines() if line and line[0] != "#")
    print(f"wrote {n_events} events to {example.name}/{path}")
    return 0


# Dashboard


def list_examples() -> list[str]:
    return sorted(
        path.name
        for path in ROOT.iterdir()
        if path.is_dir() and (path / "example.toml").exists()
    )


def run_progress(rundir: Path) -> dict[str, Any]:
    """Progress of a run without metrics, from the files qseek writes while running."""
    progress: dict[str, Any] = {"time_progress": None, "n_detections": 0}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        state = json.loads((rundir / "progress.json").read_text())
        progress["time_progress"] = state.get("time_progress")
    with contextlib.suppress(OSError), (rundir / "detections.jsonl").open() as f:
        progress["n_detections"] = sum(1 for _ in f)
    log = rundir / "qseek.log"
    log_age = time.time() - log.stat().st_mtime if log.exists() else math.inf
    progress["log_age_s"] = round(log_age, 1) if math.isfinite(log_age) else None
    progress["state"] = "running" if log_age < RUNNING_TIMEOUT else "stopped"
    return progress


# Comparisons with the baseline, keyed by the creation times of both runs
_association_cache: dict[tuple[str, ...], dict[str, Any] | None] = {}


def baseline_association(
    example: Example, name: str, created: str | None, baseline_created: str | None
) -> dict[str, Any] | None:
    key = (example.name, name, str(created), str(baseline_created))
    if key not in _association_cache:
        _association_cache[key] = associate_runs(example, "baseline", name)[0]
    return _association_cache[key]


def runs_overview(example: Example) -> dict[str, Any]:
    tolerances = example.settings.get("tolerances", {})
    baseline = (
        json.loads(example.baseline.read_text()) if example.baseline.exists() else None
    )
    entries = []
    if baseline:
        entries.append({"name": "baseline", "state": "baseline", "metrics": baseline})
    if example.runs_dir.exists():
        for rundir in sorted(example.runs_dir.iterdir()):
            if not rundir.is_dir() or ".bak-" in rundir.name:
                continue
            metrics_file = rundir / METRICS_FILE
            if metrics_file.exists():
                metrics_ = json.loads(metrics_file.read_text())
                completed = metrics_.get("runtime", {}).get("completed")
                entry = {
                    "name": rundir.name,
                    "state": "incomplete" if completed is False else "done",
                    "metrics": metrics_,
                }
            else:
                progress = run_progress(rundir)
                entry = {
                    "name": rundir.name,
                    "state": progress["state"],
                    "progress": progress,
                    "metrics": None,
                }
            entries.append(entry)

    for entry in entries:
        if baseline and entry["metrics"] and entry["name"] != "baseline":
            association = baseline_association(
                example,
                entry["name"],
                entry["metrics"].get("created"),
                baseline.get("created"),
            )
            result = compare_metrics(
                entry["metrics"], baseline, tolerances, association
            )
            entry["vs_baseline"] = {
                "association": {
                    key: value
                    for key, value in (association or {}).items()
                    if key != "rows"
                }
                or None,
                "regressions": result["regressions"],
                "better": [
                    r["label"] for r in result["rows"] if r["status"] == "better"
                ],
                "newly_missed": len(result["newly_missed"]),
            }
    return {
        "example": example.name,
        "settings": example.settings,
        "metric_specs": [
            {"key": spec.key, "label": spec.label, "better": spec.better}
            for spec in METRICS
        ],
        "association_specs": [
            {"key": spec.key, "label": spec.label, "better": spec.better}
            for spec in ASSOCIATION_METRICS
        ],
        "runs": entries,
    }


def project(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """East and north in km relative to (lat0, lon0)."""
    east = math.radians(lon - lon0) * math.cos(math.radians(lat0)) * EARTH_RADIUS
    north = math.radians(lat - lat0) * EARTH_RADIUS
    return round(east / 1e3, 4), round(north / 1e3, 4)


def run_detail(example: Example, name: str) -> dict[str, Any]:
    """Metrics, configuration, detections and reference matches of a run."""
    rundir = example.rundir(name)
    metrics_file = rundir / METRICS_FILE
    metrics_ = json.loads(metrics_file.read_text()) if metrics_file.exists() else None
    has_detections = (rundir / "csv" / "detections.csv").exists()

    config_: dict[str, Any] | None = None
    if (rundir / "search.json").exists():
        config_ = json.loads((rundir / "search.json").read_text())
    if config_ is None:
        config_file = example.path / example.default_config()
        config_ = json.loads(config_file.read_text())
        for key, value in (
            ((metrics_ or {}).get("config") or {}).get("overrides", {}).items()
        ):
            apply_override(config_, key.split("."), value)

    octree = config_.get("octree", {})
    location = octree.get("location", {})
    lat0, lon0 = location.get("lat", 0.0), location.get("lon", 0.0)
    # the octree center is the location plus its shifts
    shifts = {
        "east": location.get("east_shift", 0.0),
        "north": location.get("north_shift", 0.0),
        "depth": location.get("depth", 0.0) - location.get("elevation", 0.0),
    }
    volume = {
        axis: [(b + shifts[axis]) / 1e3 for b in octree[f"{axis}_bounds"]]
        for axis in ("east", "north", "depth")
        if f"{axis}_bounds" in octree
    }

    detections: list[Event] = []
    if has_detections:
        detections = load_detections(rundir, example.reference.get("magnitude_column"))
    columns: dict[str, list[Any]] = {
        key: []
        for key in (
            "time", "east", "north", "depth", "semblance", "n_picks", "n_stations",
            "rms", "magnitude", "uncertainty_horizontal", "uncertainty_vertical",
        )
    }  # fmt: skip
    for ev in detections:
        east, north = project(ev.lat, ev.lon, lat0, lon0)
        row = ev.row or {}
        columns["time"].append(ev.time.isoformat())
        columns["east"].append(east)
        columns["north"].append(north)
        columns["depth"].append(round(ev.depth / 1e3, 4))
        columns["magnitude"].append(ev.magnitude)
        for key in (
            "semblance", "n_picks", "n_stations", "rms",
            "uncertainty_horizontal", "uncertainty_vertical",
        ):  # fmt: skip
            columns[key].append(to_float(row.get(key)))

    reference_events: list[dict[str, Any]] = []
    catalog_file = example.reference.get("catalog")
    if catalog_file and (example.path / catalog_file).exists():
        reference = load_reference(example.path / catalog_file)
        max_dt = example.reference.get("max_time_difference", 3.0)
        matches = {
            i_ref: (i_det, dt)
            for i_ref, i_det, dt in match_events(detections, reference, max_dt)
        }
        for i_ref, ref in enumerate(reference):
            east, north = project(ref.lat, ref.lon, lat0, lon0)
            event: dict[str, Any] = {
                "time": ref.time.isoformat(),
                "magnitude": ref.magnitude,
                "east": east,
                "north": north,
                "depth": round(ref.depth / 1e3, 4),
                "match": None,
            }
            if i_ref in matches:
                i_det, dt = matches[i_ref]
                det = detections[i_det]
                event["match"] = {
                    "index": i_det,
                    "dt": round(dt, 3),
                    "offset_m": round(distance(ref.lat, ref.lon, det.lat, det.lon), 1),
                    "dz_m": round(det.depth - ref.depth, 1),
                    "magnitude": det.magnitude,
                }
            reference_events.append(event)

    return {
        "name": name,
        "metrics": metrics_,
        "config": config_,
        "volume": volume,
        "has_detections": has_detections,
        "detections": columns,
        "reference": reference_events,
    }


def hypodd_runs(example: Example) -> list[dict[str, Any]]:
    """HypoDD runs of `scripts/hypodd.py` with their comparison, newest first.

    `comparison` is the content of `hypodd-comparison.json`, or None before
    `just hypodd-compare` ran.
    """
    out = []
    if not example.runs_dir.is_dir():
        return out
    for rundir in example.runs_dir.iterdir():
        runinfo_file = rundir / RUNINFO_FILE
        if not rundir.is_dir() or not runinfo_file.exists():
            continue
        runinfo = json.loads(runinfo_file.read_text())
        if "hypodd" not in runinfo:
            continue
        comparison_file = rundir / "hypodd-comparison.json"
        comparison = None
        modified = runinfo_file.stat().st_mtime
        if comparison_file.exists():
            comparison = json.loads(comparison_file.read_text())
            modified = max(modified, comparison_file.stat().st_mtime)
        out.append(
            {
                "run": rundir.name,
                "source_run": runinfo.get("source_run"),
                "created": runinfo.get("created"),
                "modified": modified,
                "summary": runinfo["hypodd"],
                "comparison": comparison,
            }
        )
    return sorted(out, key=lambda r: r["modified"], reverse=True)


def is_run(example: Example, name: str) -> bool:
    """Whether `name` is the baseline or a run directory of the example."""
    if name == "baseline":
        return example.baseline.exists()
    return example.runs_dir.is_dir() and any(
        path.is_dir() and path.name == name for path in example.runs_dir.iterdir()
    )


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/") if p]
        query = urllib.parse.parse_qs(url.query)
        try:
            if not parts:
                self.send(DASHBOARD_HTML.read_bytes(), "text/html; charset=utf-8")
                return
            if parts[0] != "api":
                self.send_json({"error": "not found"}, 404)
                return
            if parts[1:] == ["examples"]:
                self.send_json(list_examples())
                return
            if len(parts) < 4 or parts[1] != "examples":
                self.send_json({"error": "not found"}, 404)
                return
            if parts[2] not in list_examples():
                self.send_json({"error": f"no example {parts[2]}"}, 404)
                return
            example = Example.load(parts[2])
            if parts[3:] == ["runs"]:
                self.send_json(runs_overview(example))
            elif parts[3:] == ["hypodd"]:
                self.send_json(hypodd_runs(example))
            elif len(parts) == 5 and parts[3] == "runs":
                if not is_run(example, parts[4]):
                    self.send_json({"error": f"no run {parts[4]}"}, 404)
                    return
                self.send_json(run_detail(example, parts[4]))
            elif parts[3:] == ["compare"]:
                if not all(k in query for k in ("a", "b")):
                    self.send_json({"error": "compare needs the runs a and b"}, 400)
                    return
                if not all(is_run(example, query[k][0]) for k in ("a", "b")):
                    self.send_json({"error": "unknown run"}, 404)
                    return
                name_a, name_b = query["a"][0], query["b"][0]
                association, pairs = associate_runs(example, name_a, name_b)
                result = compare_metrics(
                    load_metrics(example, name_b, extract=False),
                    load_metrics(example, name_a, extract=False),
                    example.settings.get("tolerances", {}),
                    association,
                )
                result["pairs"] = pairs
                self.send_json(result)
            else:
                self.send_json({"error": "not found"}, 404)
        except (SystemExit, KeyError, OSError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:  # a half-written file of a running search
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: Any, status: int = 200) -> None:
        self.send(json.dumps(data).encode(), "application/json", status)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def dashboard(args: argparse.Namespace) -> int:
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"qseek playground dashboard on http://{args.host}:{args.port}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


def config(args: argparse.Namespace) -> int:
    example = Example.load(args.example)
    check_run_name(args.run)
    run_config, _ = write_config(
        example, args.run, args.config, args.set, args.ssst_from
    )
    print(run_config.relative_to(ROOT))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_: str, *, run: bool = True) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_)
        sub.add_argument("example", help="example directory, e.g. campi-flegrei")
        if run:
            sub.add_argument("run", help="name of the run in <example>/runs/")
        return sub

    for name, help_ in (
        ("config", "write the search configuration of a run"),
        ("search", "run the search and extract the metrics"),
    ):
        sub = add(name, help_)
        sub.add_argument("--config", help="search configuration of the example")
        sub.add_argument(
            "--set",
            action="append",
            default=[],
            metavar="KEY.PATH=VALUE",
            help="override a field of the configuration, the value is JSON or a string",
        )
        sub.add_argument(
            "--ssst-from",
            metavar="RUN",
            help="apply source-specific station corrections from this run"
            " (needs a plugin providing SourceSpecificStationCorrections)",
        )
        if name == "search":
            sub.add_argument("--force", action="store_true", help="replace the run")

    add("metrics", "extract the metrics of a run")
    sub = add("compare", "compare a run with the baseline or another run")
    sub.add_argument("against", nargs="?", default="baseline")
    add("bless", "make the metrics of a run the baseline")
    add("runs", "list the runs of an example", run=False)
    add("catalog", "download the reference catalog", run=False)
    sub = commands.add_parser("dashboard", help="serve the dashboard")
    sub.add_argument("--host", default="127.0.0.1")
    sub.add_argument("--port", type=int, default=2214)

    args = parser.parse_args()
    handlers = {
        "config": config,
        "search": search,
        "metrics": metrics,
        "compare": compare,
        "bless": bless,
        "runs": runs,
        "catalog": catalog,
        "dashboard": dashboard,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
