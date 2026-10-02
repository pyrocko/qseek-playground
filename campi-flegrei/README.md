# Campi Flegrei

Detect and locate the earthquakes of one day at [Campi Flegrei](https://en.wikipedia.org/wiki/Campi_Flegrei), the volcanic caldera west of Naples, Italy. On 20 May 2024, an Md 4.4 earthquake struck the caldera during a swarm. This example is the [quick start](https://pyrocko.github.io/qseek/getting-started/quick-start/) of the Qseek documentation, which explains the configuration step by step.

## Run it

From the root of the playground, after `just setup`:

```sh title="Download the data and run the search"
just download campi-flegrei
just search campi-flegrei dev
```

The download takes about 5 minutes and writes about 1 GB of waveforms into `sds/` and the StationXML files into `metadata/`. Of the 19 requested stations, 18 recorded on that day: `IV.CAWE` had no channels in May 2024.

The search takes about 90 seconds on a workstation with an NVIDIA GeForce RTX 4060; without a GPU it takes longer. It ends with the metrics of the run:

```text
campi-flegrei / dev: qseek 99442a96b8
  detections                         732
  detections with min. picks         521
  picks per event, median            12
  residual RMS, median [s]           0.2835
  nearest neighbor, median [m]       153.2
  search time [s]                    83.47
  reference events detected          45
  epicenter offset, median [m]       240.6
  recall by magnitude                M1: 32/32, M2: 8/8, M3: 4/4, M4: 1/1
```

Look at the detections in the web UI or with the waveforms in Pyrocko Snuffler:

```sh title="Explore the run"
just explore campi-flegrei dev
just snuffler campi-flegrei dev
```

The run directory `runs/dev/` holds the detections in `csv/detections.csv`, for QGIS and plotting, and the metrics in `metrics.json`. The [run directory](https://pyrocko.github.io/qseek/results/run-directory/) page of the documentation describes all files.

## Compare with the baseline

`baseline/` holds the metrics, detections and configuration of a reference run of this example. Compare your run with it:

```sh title="Compare the run dev with the baseline"
just compare campi-flegrei dev
```

The comparison pairs the detections of both runs by origin time, within 1 s. A run of the same Qseek version on the same machine reproduces the baseline exactly and reports `B is identical to A`. A changed configuration reports the detections lost and added, how far the paired detections moved, and how their picks and residuals changed; it fails when it loses more of the detections with at least 8 picks than 2 or 1%, whichever is larger. `just dashboard` shows the comparison with maps and histograms.

## Setup

| | |
| --- | --- |
| Stations | 18 stations of the INGV networks `IV` and `IX`, `HH`, `EH` or `HN` channels, from [FDSN Rush](https://miili.github.io/FDSN-rush/) with `download.json` |
| Search volume | 12 km × 12 km × 6 km around 40.827°N, 14.139°E; 1 km root nodes, refined over 4 levels to 125 m |
| Image function | PhaseNet with the `volpick` weights |
| Travel times | Fast marching in the 1D model `campi-flegrei.nd` |
| Magnitudes | Local magnitude with the Campi Flegrei attenuation model ([Petrosino et al., 2008](https://doi.org/10.1785/0120070131)) |

The search configuration is `campi-flegrei.json`. `just search` copies it to `runs/<run>.json`, applies the `--set` overrides and runs the search from this directory.

## Reference catalog

`reference/ingv-catalog.txt` holds the [INGV catalog](https://webservices.ingv.it/fdsnws/event/1/) of 20 May 2024: 45 earthquakes within 0.1° of the caldera, from Md 1.0 to Md 4.4. `just catalog campi-flegrei` downloads it again with the query in `example.toml`.

A detection matches a catalog event when their origin times differ by at most 3 s; each catalog event matches at most one detection. The metrics compare the matched pairs: epicenter offset, depth offset (positive when Qseek is deeper), origin time offset and the difference between ML and Md. The catalog lists only the larger events, so detections without a match are not false detections.

## Station corrections

Source-specific station corrections (SSST) refine the locations in a second search, from the picks of a first run. They need a plugin that provides the `SourceSpecificStationCorrections` module:

```sh title="Second search with SSST from the run dev"
just ssst campi-flegrei ssst dev
just compare campi-flegrei ssst
```

Against the baseline, SSST moves the paired detections by 175 m (median) and lowers the residual RMS of 83% of them; the median distance between neighboring detections drops from 153 m to 133 m, partly because SSST finds 7 more detections with at least 8 picks. It also loses 6 and adds 13 of them, which fails the comparison. Three of the lost detections have no counterpart within 14 s; the other three moved by 1.9 to 5.3 s in origin time, beyond the 1 s pairing window.
