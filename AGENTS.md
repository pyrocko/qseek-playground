# AGENTS.md — qseek-playground

The playground has two jobs:

1. **User examples.** Each example directory is a complete, reproducible Qseek search on real data, with a README for users.
2. **Closing the loop for Qseek development.** An agent changes Qseek, runs the examples on real data and compares the run with a committed baseline run: which detections were lost or added, how far the detections moved and how their picks, residuals and semblance changed. `just compare` exits non-zero on a regression. A reference catalog gives a coarse, independent check on top.

The repository is public. Keep it free of private code and credentials.

## How Qseek is run

- Everything that imports Qseek runs with the venv of the Qseek checkout: `uv run --project ../qseek --no-sync`. `QSEEK_DIR` points to another checkout.
- Qseek is an editable install, so Python changes in `../qseek` take effect in the next run. After changes to the C extensions in `src/qseek/ext/`, run `just setup` to rebuild them.
- `just setup` runs `uv sync --inexact`, so plugins installed into `../qseek/.venv` stay installed.
- FDSN Rush runs with `uvx fdsn-rush`, in its own environment.
- `scripts/playground.py` uses only the standard library. It reads the documented outputs of a run, `csv/detections.csv` and `qseek.log`, not Qseek internals, so the metrics stay comparable across refactors.

## The loop

```sh
just setup                                   # once, and after C extension changes
just download campi-flegrei                  # once, about 1 GB
# change Qseek in ../qseek
just search campi-flegrei <run> --force      # about 90 s on the RTX 4060 workstation
just compare campi-flegrei <run>             # vs. the baseline, exit code 1 on regression
just compare campi-flegrei <run> <other>     # vs. another run
just runs campi-flegrei                      # overview of all runs
```

`just compare` prints the paired comparison first, then the run metrics, and writes both to `runs/<run>/comparison-<against>.json`. Read that file instead of parsing the printed table.

`just dashboard` serves the same comparison live on http://127.0.0.1:2214 for the user (`scripts/dashboard.html`, a single file without external dependencies, served by `playground.py dashboard` with a JSON API under `/api/examples/<example>/`: `runs`, `runs/<run>`, `compare?a=&b=` and `hypodd`). When a run finishes, the dashboard makes it B. Keep the page in step when you add or rename metrics: the tables come from `METRICS` and `ASSOCIATION_METRICS`, the tiles and histograms are defined in the page (`renderTiles`, `DISTS`).

- Name runs after the change, e.g. `fix-octree-split`, and keep one run of the unchanged code for comparison.
- Try configuration changes with `--set key.path=value` instead of editing the committed search configuration. The overrides are recorded in the run's `metrics.json`.
- `just search` runs `qseek --quiet search`: the console shows only errors and no live statistics view, which keeps the output an agent reads short. `runs/<run>/qseek.log` still holds the full INFO log that the metrics are read from. Read the log file when you need the warnings.
- Run one search at a time: searches share the GPU and the port of Qseek's HTTP server.
- Qseek's results are deterministic on the same machine and commit: a repeated run gives identical detection metrics. Runtime varies by about 15%. Any change in a detection metric comes from the change you made.
- `just bless <example> <run>` copies the run's `metrics.json`, `csv/detections.csv` and resolved `search.json` to `<example>/baseline/`, so runs can be paired with the baseline after its run directory is gone. Bless only an intended improvement, from a run of a committed Qseek version with no other changes (`qseek_dirty: false`), and commit the baseline together with an explanation of what improved. `baseline` is reserved as a run name.

## Metrics

### Paired detections, run against run

`just compare` and the dashboard pair the detections of B with those of A one-to-one by origin time, closest first, within `pair_max_time_difference` of `[metrics]` in `example.toml` (1 s). The comparison reports:

| Metric | Meaning |
| --- | --- |
| `n_paired`, `n_unchanged`, `identical` | Pairs, pairs whose rows in `csv/detections.csv` are equal in every column (location, picks, residuals, semblance, magnitudes, uncertainties), and whether B reproduces A exactly |
| `n_lost_good`, `n_new_good` | Detections with at least `min_picks` picks only in A (lost) or only in B (new). **Gated:** B fails when it loses more than `max(2, 1% of A's detections with min. picks)` |
| `n_only_a`, `n_only_b` | The same for all detections |
| `shift_horizontal_median_m`, `_p90_m` | Horizontal shift of the pairs |
| `shift_depth_median_m`, `shift_depth_abs_median_m` | Depth shift B − A: the signed median is a systematic bias, the absolute median the scatter |
| `time_shift_abs_median_s` | Origin time shift |
| `picks_delta_median`, `picks_gained_fraction`, `picks_lost_fraction` | Change of the picks per pair |
| `rms_delta_median_s`, `rms_improved_fraction` | Change of the residual RMS per pair |
| `semblance_delta_median`, `magnitude_delta_median` | Change of semblance and magnitude per pair |

The shifts and changes use the pairs with at least `min_picks` picks in A or in B, so weak detections do not dominate them. The pairing uses the origin time only: a detection whose origin time moved by more than the window counts as lost in A and new in B, and in a dense swarm two events less than a window apart can swap partners. Check the lost and new detections in the dashboard before you trust a count. Interpret them by the change you made: a station correction should move detections and lower the RMS; a refactor should give `identical`.

On Campi Flegrei, SSST from the baseline loses 6 detections with ≥ 8 picks and adds 13: 3 of the lost ones, up to 21 picks, have no detection in B within 14 s; the other 3 moved by 1.9–5.3 s in origin time. It moves the pairs by 175 m (median) and lowers the RMS of 83% of them.

### Run metrics

`just search` and `just metrics` write `runs/<run>/metrics.json`:

| Section | Metrics |
| --- | --- |
| `environment` | host, GPU, CPU count, Qseek version, commit, `qseek_dirty`, installed plugins (not in the baseline) |
| `config` | base configuration, `--set` overrides, SHA-256 of the configuration |
| `detections` | number of detections, detections with at least `min_picks` picks, picks, stations, residual RMS, semblance, location uncertainty, magnitudes, `nn_distance_median_m`: median 3D distance of a detection with min. picks to its nearest neighbor. It falls with tighter clustering but also with more detections, so compare it only between runs with similar counts |
| `runtime` | `search_time_s` (from the log), `wall_time_s`, `peak_rss_mib`, batch times, processing rate, `completed`, `exit_code` |
| `reference` | `n_matched` and `recall` of the reference catalog, recall per magnitude, epicenter offset (median, p90), depth offset (signed and absolute median), origin time offset, magnitude difference, the missed events |

The reference catalog is coarse: it lists only the larger events, within `max_time_difference` (`[reference]`, 3 s) and has location errors of its own. Use it as an independent sanity check, not as ground truth. Unmatched detections are not false detections.

Gated run metrics in `just compare`: picks per event, residual RMS, search time, reference events detected and the epicenter, depth and origin time offsets to the catalog. Search time is gated only against a baseline from the same host and GPU. A run metric regresses when it is worse than the reference by more than `max(tolerance, rel_tolerance × |reference|)`. The defaults are in `METRICS` and `ASSOCIATION_METRICS` in `scripts/playground.py`; `[tolerances]` in `example.toml` overrides them per example.

### HypoDD reference

`scripts/hypodd.py` (`just hypodd`, `just hypodd-compare`) relocates the detections of a run with HypoDD as a reference for the Qseek locations. The binaries come from `HYPODD_BIN` (on the workstation `~/Development/HypoDD/bin`, see its `AGENTS.md`) or the `PATH`.

- `just hypodd <example> <from> [run]` runs `qseek export hypodd` into `runs/<run>/hypodd/`, then ph2dt and hypoDD in that directory, and writes the relocated detections to `runs/<run>/csv/detections.csv` with the columns of the source run. Location and origin time come from `hypoDD.reloc`; `rms` and the uncertainties are empty, the HypoDD columns `hypodd_id`, `hypodd_cluster`, `hypodd_nctp`, `hypodd_ncts`, `hypodd_rct` are added. `playground-run.json` holds the export settings and a summary of the hypoDD run (`hypodd`: linked and relocated events, clusters, final iteration, median CND).
- hypoDD loops forever when its inversion fails with NaN; the script stops it after 10 minutes. `qseek export hypodd` writes the top of the first layer 1 km above sea level to avoid one cause of it: a source at exactly the top of the first layer.
- The dashboard's HypoDD panel shows `hypodd-comparison.json` and the hypoDD summary of every HypoDD run (`/api/examples/<example>/hypodd`); it follows the HypoDD run that is A or B, or whose source run is.
- `just hypodd-compare <example> <hypodd-run> [runs...]` pairs the source run and the other runs with the exported events by origin time, restricts all to the common events and writes `runs/<hypodd-run>/hypodd-comparison.json`. The double-difference residuals come from a hypoDD run per location set in `runs/<hypodd-run>/hypodd-evaluation/<run>/`: one iteration with damping 10⁶ on the `dt.ct` of the HypoDD run, the travel times shifted to each run's origin times, all data and no clustering. `hypoDD.res` of that run holds the residuals at the given locations. Lower is better, but HypoDD minimizes this measure, so it favors the HypoDD locations; compare Qseek runs with each other on it.
- Defaults of the export: P weight 1, S weight 0.5, three sets of 5 iterations, damping 80, air-quakes kept (`IAQ=0`). On Campi Flegrei keeping the air-quakes relocates 340 instead of 323 detections at the same residuals. Equal P and S weights (`dev-hypodd-s1`, with air-quakes removed) lower the median residual from 52 to 49 ms and balance P and S (47 and 51 ms), but move the depths 180 m above the INGV depths.

## Examples

| Example | Data | Reference | Baseline |
| --- | --- | --- | --- |
| `campi-flegrei` | 20 May 2024, 18 INGV stations, about 1 GB | INGV catalog, 45 events | 732 detections, 521 with ≥ 8 picks, 153 m nearest neighbor; 45/45 catalog events matched |

### Add an example

Create a directory with:

- `README.md` for users: what the example shows, how to run it, what you should see.
- `example.toml`: `config`, `[reference]` with `catalog` (FDSN text format), `url`, `max_time_difference` and `magnitude_column`, and `[metrics]` with `min_picks` and `pair_max_time_difference`.
- `download.json` for FDSN Rush, writing `sds/` and `metadata/`.
- The search configuration and velocity models, with paths relative to the example directory.
- `reference/` with the catalog from `just catalog <example>`.
- `baseline/` from `just search` and `just bless`.

Add the example to the tables in `README.md` and here. Data stays out of git: `*/sds/`, `*/metadata/` and `*/runs/` are ignored.

## Conventions

- Format and lint with `uvx ruff format scripts` and `uvx ruff check scripts` (`ruff.toml`).
- Write the READMEs for seismologists, following Qseek's `DOCS_STYLE.md`: American spelling, second person, sentence-case headings, no emoji, real numbers only.
- Commit or push only when asked.
