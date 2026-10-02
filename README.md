# Qseek playground

Worked examples for [Qseek](https://github.com/pyrocko/qseek) on real seismic data. Each example downloads its waveforms and runs a search. The playground compares runs with each other, detection by detection, and with a reference catalog.

| Example | Data | Result |
| --- | --- | --- |
| [Campi Flegrei](campi-flegrei/) | 1 day, 18 stations of the INGV network, 20 May 2024 | 732 detections; all 45 events of the INGV catalog detected |

The examples follow the [Qseek documentation](https://pyrocko.github.io/qseek/); Campi Flegrei is its [quick start](https://pyrocko.github.io/qseek/getting-started/quick-start/).

## Requirements

- [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/)
- Python 3.12 or newer
- About 1 GB of disk space per example, and a CUDA GPU for fast phase picking (optional)

The playground runs Qseek from a source checkout next to it, in `../qseek`:

```sh title="Set up the playground"
git clone https://github.com/pyrocko/qseek.git
git clone https://github.com/pyrocko/qseek-playground.git
cd qseek-playground
just setup
```

`just setup` installs the checkout into `../qseek/.venv` and compiles its C extensions. To use a checkout in another place, set `QSEEK_DIR`, e.g. `QSEEK_DIR=~/src/qseek just setup`.

## Run an example

```sh title="Run the Campi Flegrei example"
just download campi-flegrei      # waveforms and station metadata, about 1 GB
just search campi-flegrei dev    # search into campi-flegrei/runs/dev/
just explore campi-flegrei dev   # open the run in the web UI
```

`just search` writes the run directory `<example>/runs/<run>/` and prints the metrics of the run: the detections, how many events of the reference catalog Qseek found and how far its locations are from the catalog.

Change a field of the configuration for one run with `--set`:

```sh title="Try other PhaseNet weights"
just search campi-flegrei original --set image_function.pretrained=original
just compare campi-flegrei original
```

The value is JSON, or a string if it does not parse as JSON, e.g. `--set 'octree.depth_bounds=[0, 8000]'`. Nested lists take an index: `--set ray_tracers.0.phases='["fm:P"]'`.

## Compare runs

`just compare campi-flegrei original` pairs the detections of the run `original` with those of the baseline by origin time. It reports the detections lost and added, how far the paired detections moved, and how their picks, residuals and semblance changed. Then it compares the metrics of both runs, including the match with the reference catalog.

```sh title="Open the dashboard"
just dashboard
```

The dashboard on http://127.0.0.1:2214 shows the same comparison for any two runs A and B: plan view and depth sections with the shift of every paired detection, histograms of the shifts and changes, the lost and new detections, the detections over time, the quality distributions of both runs, the run metrics across all runs, and the configuration fields that differ. It reads the run directories every 4 s, shows a search while it runs and makes a run B when it finishes.

## Relocate with HypoDD

The playground relocates the detections of a run with [HypoDD](https://www.ldeo.columbia.edu/~felixw/hypoDD.html), as a reference for the Qseek locations. Build `ph2dt` and `hypoDD` from the HypoDD distribution and point `HYPODD_BIN` to their directory:

```sh title="Relocate the runs dev and ssst with HypoDD"
export HYPODD_BIN=~/src/HypoDD/bin
just hypodd campi-flegrei dev                  # into campi-flegrei/runs/dev-hypodd/
just hypodd campi-flegrei ssst                 # into campi-flegrei/runs/ssst-hypodd/
just hypodd-compare campi-flegrei dev-hypodd ssst
```

`just hypodd` exports the run with `qseek export hypodd` into `runs/<run>-hypodd/hypodd/`, runs ph2dt and hypoDD there and writes the relocated detections to `runs/<run>-hypodd/csv/detections.csv`. The HypoDD run is a run like any other: `just compare`, `just runs` and the dashboard show it. It contains only the relocated detections, so `just compare` against the source run counts the others as lost. Change the export settings with `--set`, e.g. `--set hypodd.iterations.0.damping=50`.

`just hypodd` compares the HypoDD locations with those of the source run. `just hypodd-compare` adds other runs, on the events common to all of them:

- the double-difference residuals of every run's locations, evaluated by hypoDD on the same catalog differential times `dt.ct` without moving the events;
- the nearest-neighbor distance and the depth distribution;
- the epicenter and depth offsets to the reference catalog;
- the shift to the HypoDD locations, absolute and after removing the median shift.

The dashboard shows the comparison in its HypoDD panel, with the summary of the hypoDD run. On Campi Flegrei, HypoDD relocates 340 of the 424 exported detections of `dev`:

| Metric, 340 common events | `dev` | `ssst` | `dev-hypodd` |
| --- | --- | --- | --- |
| Double-difference residual, median abs. [ms] | 72.8 | 65.7 | 54.1 |
| P residual, median abs. [ms] | 66.8 | 60.9 | 34.4 |
| S residual, median abs. [ms] | 78.6 | 70.4 | 82.4 |
| Nearest neighbor, median [m] | 156 | 129 | 136 |
| Epicenter offset to INGV, median [m] | 241 | 239 | 253 |
| Relative shift to HypoDD, median [m] | 360 | 277 | 0 |

## Recipes

Run `just` to list all recipes. The example defaults to `campi-flegrei` and the run to `dev`.

| Recipe | What it does |
| --- | --- |
| `just setup` | Install the Qseek checkout into its `.venv`. Run it again after changing C extensions. |
| `just download <example>` | Download waveforms and station metadata with [FDSN Rush](https://miili.github.io/FDSN-rush/). |
| `just search <example> <run> [--set key=value] [--force]` | Run a search and extract its metrics. `--force` replaces an existing run. |
| `just ssst <example> <run> <from>` | Search with source-specific station corrections (SSST) from a previous run. Needs a plugin that provides `SourceSpecificStationCorrections`. |
| `just config <example> <run> [--set key=value]` | Only write the configuration of a run to `<example>/runs/<run>.json`. |
| `just metrics <example> <run>` | Extract the metrics of a run again. |
| `just compare <example> <run> [against]` | Compare a run with the baseline of the example, or with another run. |
| `just runs <example>` | List the runs of an example with their key metrics. |
| `just bless <example> <run>` | Make a run the new baseline: copies its metrics, detections and configuration to `<example>/baseline/`. |
| `just explore`, `just snuffler` | Open a run in the web UI or in Pyrocko Snuffler. |
| `just dashboard [port]` | Serve the playground dashboard on http://127.0.0.1:2214 to compare runs. |
| `just catalog <example>` | Download the reference catalog again. |
| `just hypodd <example> <from> [run] [--set key=value] [--force]` | Relocate the detections of a run with HypoDD into `<from>-hypodd`. Needs `HYPODD_BIN`. |
| `just hypodd-compare <example> <hypodd-run> [runs...]` | Compare a HypoDD run with its source run and other runs on their common events. |
| `just remove <example> <run>` | Delete a run. |

## Layout

```text
qseek-playground/
├── justfile                  # recipes
├── scripts/playground.py     # runs searches, extracts and compares metrics
├── scripts/dashboard.html    # the dashboard, served by `just dashboard`
├── scripts/hypodd.py         # relocates runs with HypoDD and compares the locations
└── campi-flegrei/            # one directory per example
    ├── README.md
    ├── example.toml          # search config, reference catalog, metric settings
    ├── download.json         # FDSN Rush download configuration
    ├── campi-flegrei.json    # search configuration
    ├── campi-flegrei.nd      # velocity model
    ├── reference/            # reference catalog
    ├── baseline/             # metrics, detections and configuration of a known-good run
    ├── sds/, metadata/       # downloaded data (not in git)
    └── runs/                 # run directories (not in git)
```
