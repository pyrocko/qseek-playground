# Campi Flegrei, February 2025

Detect and locate the earthquakes of ten days of the February 2025 swarm at [Campi Flegrei](https://en.wikipedia.org/wiki/Campi_Flegrei), the volcanic caldera west of Naples, Italy. Between 12 and 22 February 2025, INGV located 211 earthquakes within 0.1° of the caldera, 113 of them on 16 and 17 February, up to Md 3.9.

The setup is the same as in the [Campi Flegrei example](../campi-flegrei/README.md) of one day, with a finer search grid. With about ten times as many detections, it separates changes that the one-day example is too small to show, such as different weightings of station corrections.

## Run it

From the root of the playground, after `just setup`:

```sh title="Download the data and run the search"
just download campi-flegrei-2025-02
just search campi-flegrei-2025-02 dev
```

The download writes about 10 GB of waveforms into `sds/` and the StationXML files into `metadata/`. All 19 requested stations recorded in this period.

The search takes about 14 minutes on a workstation with an NVIDIA GeForce RTX 4060; without a GPU it takes longer. It ends with one line of metrics:

```text
campi-flegrei-2025-02 / dev: qseek b773a6a051
  det 6379  ≥picks 3750  picks 9  rms 0.2784  sem max 1.413  ref 209/211  epi 279.7 m  850 s
  missed 2, see `just compare` or metrics.json
```

Qseek finds 6379 detections, 3750 of them with at least 8 picks. It detects 209 of the 211 catalog events, all from Md 2 up; the two it misses are Md 1.0 and Md 1.5. Its epicenters are 280 m (median) from those of the catalog, its depths 215 m. `just metrics campi-flegrei-2025-02 dev` prints all metrics.

Look at the detections in the web UI or with the waveforms in Pyrocko Snuffler:

```sh title="Explore the run"
just explore campi-flegrei-2025-02 dev
just snuffler campi-flegrei-2025-02 dev
```

The run directory `runs/dev/` holds the detections in `csv/detections.csv`, for QGIS and plotting, and the metrics in `metrics.json`. The [run directory](https://pyrocko.github.io/qseek/results/run-directory/) page of the documentation describes all files.

## Compare runs

Change the configuration of a run with `--set` and compare it with the run `dev`:

```sh title="Compare a run with other PhaseNet weights with the run dev"
just search campi-flegrei-2025-02 original --set image_function.pretrained=original
just compare campi-flegrei-2025-02 original dev
```

The comparison pairs the detections of both runs by origin time, within 1 s, and reports the detections lost and added, how far the paired detections moved, and how their picks and residuals changed. `just dashboard` shows the comparison with maps and histograms.

## Setup

| | |
| --- | --- |
| Stations | 19 stations of the INGV networks `IV` and `IX`, `HH`, `EH` or `HN` channels, from [FDSN Rush](https://miili.github.io/FDSN-rush/) with `download.json` |
| Search volume | 12 km × 12 km × 6 km around 40.827°N, 14.139°E; 1 km root nodes, refined over 6 levels to 31.25 m |
| Image function | PhaseNet with the `volpick` weights |
| Travel times | Fast marching in the 1D model `campi-flegrei.nd` |
| Magnitudes | Local magnitude with the Campi Flegrei attenuation model ([Petrosino et al., 2008](https://doi.org/10.1785/0120070131)) |

The search configuration is `campi-flegrei.json`. It differs from the one-day example only in the number of octree levels, 6 instead of 4. `just search` copies it to `runs/<run>.json`, applies the `--set` overrides and runs the search from this directory.

## Reference catalog

`reference/ingv-catalog.txt` holds the [INGV catalog](https://webservices.ingv.it/fdsnws/event/1/) of 12 to 22 February 2025: 211 earthquakes within 0.1° of the caldera, from Md 1.0 to Md 3.9; 167 of them are below Md 2. `just catalog campi-flegrei-2025-02` downloads it again with the query in `example.toml`.

A detection matches a catalog event when their origin times differ by at most 3 s; each catalog event matches at most one detection. The metrics compare the matched pairs: epicenter offset, depth offset (positive when Qseek is deeper), origin time offset and the difference between ML and Md. The catalog lists only the larger events, so detections without a match are not false detections.

## Station corrections

Source-specific station corrections (SSST) refine the locations in a second search, from the picks of a first run. They need a plugin that provides the `SourceSpecificStationCorrections` module:

```sh title="Second search with SSST from the run dev"
just ssst campi-flegrei-2025-02 ssst dev
just compare campi-flegrei-2025-02 ssst dev
```

Judge station corrections on the map, by how tightly the detections cluster, rather than by the match with the catalog: the catalog locations have errors of their own.
