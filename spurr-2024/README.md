# Mount Spurr, December 2024

Detect and locate the earthquakes of three days at [Mount Spurr](https://en.wikipedia.org/wiki/Mount_Spurr), the stratovolcano 125 km west of Anchorage, Alaska, during its 2024–2025 unrest. From 27 to 29 December 2024, the USGS catalog lists 234 earthquakes within 12 km of the volcano, 167 of them on 28 December, from ML −1.1 to ML 3.2.

The example is a test bed for station weighting. Its network is inhomogeneous: ten Alaska Volcano Observatory (AVO) stations within 32 km of the swarm, then nothing out to 79 km, then ten regional stations from 79 km to 131 km. Four of the regional stations are the Redoubt Volcano network, clustered within a 15° sector to the south. Most detections are picked only at the local stations: 1124 of the 1758 detections of the run below have no pick at a regional station.

## Run it

From the root of the playground, after `just setup`:

```sh title="Download the data and run the search"
just download spurr-2024
just search spurr-2024 dev
```

The download takes about 30 seconds and writes about 830 MB of waveforms into `sds/` and the StationXML files into `metadata/`.

The search takes about 2 minutes on a workstation with an NVIDIA GeForce RTX 4060. It ends with the metrics of the run:

```text
spurr-2024 / dev: qseek 3552b0cf96
  detections                         1758
  detections with min. picks         1046
  picks per event, median            9
  residual RMS, median [s]           0.3485
  nearest neighbor, median [m]       296.3
  search time [s]                    109.6
  reference events detected          228
  recall                             0.974
  epicenter offset, median [m]       415.9
  depth offset |dz|, median [m]      1309
  depth offset dz, median [m]        -575
```

Qseek finds 228 of the 234 catalog events; the 6 missed events are ML 0.12 or smaller. The detections lie 416 m (median) from the catalog epicenters and 575 m (median) shallower. 262 detections have at least 20 picks.

Look at the detections in the web UI or with the waveforms in Pyrocko Snuffler:

```sh title="Explore the run"
just explore spurr-2024 dev
just snuffler spurr-2024 dev
```

## Network

All stations record broadband `BH` channels at 50 Hz. The distances are from the center of the search volume, 61.30°N, 152.27°W, 1 km west of the summit.

| Group | Stations | Distance | Azimuth |
| --- | --- | --- | --- |
| Local, AVO network `AV` | `SPBG`, `SPCP`, `SPCN`, `SPCL`, `SPCG`, `SPWE`, `SPU`, `SPBL`, `SPNN`, `STLK` | 7–32 km | all around |
| Regional, Alaska network `AK` | `M20K`, `SSN`, `CAPN`, `SKN`, `FIRE`, `N19K` | 79–131 km | 325°, 77°, 134°, 27°, 98°, 247° |
| Regional cluster, Redoubt Volcano network `AV` | `RDT`, `RDDF`, `RDJH`, `RED` | 81–102 km | 185–200° |

- The local network is denser on the southeast flank: `SPCP` and `SPCN` are 4.9 km apart, `SPBG` and `SPCL` 7.3 km.
- The four Redoubt stations lie within 26 km of each other, `RDDF` and `RDJH` only 6.4 km apart. They put four stations into one direction of the stack.
- The example has no co-located sensors: in December 2024, no AVO station near Mount Spurr shares its site with a second sensor under another location code. The closest pair, `AV.N20K` and `AV.SPCN`, is 2.9 km apart, but `AV.N20K` has no waveforms at EarthScope for these days, nor have `AK.O20K` and `AK.M19K`; the example leaves the three out.
- The largest azimuthal gap of the regional stations is 78°, between `N19K` (247°) and `M20K` (325°).
- `AK.N19K` recorded 73% of the three days; `AV.SPBG` has short gaps. Qseek logs a warning per channel for each 5-minute batch without data.

## Setup

| | |
| --- | --- |
| Stations | 20 stations of the networks `AV` and `AK`, `BH` channels, from [FDSN Rush](https://miili.github.io/FDSN-rush/) with `download.json` |
| Search volume | 24 km × 24 km × 20 km around 61.30°N, 152.27°W, from 4 km above to 16 km below sea level; 2 km root nodes, refined over 4 levels to 250 m |
| Image function | PhaseNet with the `volpick` weights |
| Travel times | Fast marching in the 1D model `spurr.nd` |
| Magnitudes | None: Qseek has no local magnitude model for Alaska |

The search configuration is `spurr.json`. `just search` copies it to `runs/<run>.json`, applies the `--set` overrides and runs the search from this directory.

Depths in the configuration, the velocity model and the detections are relative to sea level, as in the USGS catalog. The summit is 3374 m above sea level, and the search volume reaches 4 km above sea level to include the shallowest catalog events. The 1D model has no topography: 99 detections, 43 of them with at least 8 picks, locate above the summit.

## Velocity model

`spurr.nd` is the P-wave model that AVO uses to locate earthquakes at Mount Spurr ([Jolly et al., 1994](https://doi.org/10.1029/94JB00136)), as listed in appendix E of the [AVO catalog for 2010](https://doi.org/10.3133/ds645) (Dixon et al., 2011):

| Top of layer [km] | Vp [km/s] | Vp/Vs | Vs [km/s] | Density [g/cm³] |
| --- | --- | --- | --- | --- |
| −3.00 | 5.1 | 1.81 | 2.82 | 2.55 |
| −2.00 | 5.5 | 1.81 | 3.04 | 2.62 |
| 5.25 | 6.3 | 1.74 | 3.62 | 2.78 |
| 27.25 | 7.2 | 1.78 | 4.05 | 3.03 |

Vs follows from the Vp/Vs ratios of the AVO model, the density from Vp with the Nafe–Drake relation of [Brocher (2005)](https://doi.org/10.1785/0120050077). `spurr.nd` extends the first layer to 4 km above sea level, the top of the search volume.

## Reference catalog

`reference/usgs-catalog.txt` holds the [USGS ComCat](https://earthquake.usgs.gov/fdsnws/event/1/) events of 27–29 December 2024 within 12 km of 61.30°N, 152.27°W and above 16 km depth: 234 earthquakes, 220 located by AVO and 14 by the Alaska Earthquake Center, from ML −1.1 to ML 3.2. `just catalog spurr-2024` downloads it again with the query in `example.toml`. The depth limit leaves out the intermediate-depth earthquakes of the subducting slab, about 100 km below the volcano.

A detection matches a catalog event when their origin times differ by at most 3 s; each catalog event matches at most one detection. The metrics compare the matched pairs: epicenter offset, depth offset (positive when Qseek is deeper) and origin time offset. The catalog lists only the larger events, so detections without a match are not false detections.

## Compare station weightings

By default, Qseek gives the 4 closest stations full weight and tapers the others over 166 km, twice the mean interstation distance of this network. Change the weighting of a run with `--set` and compare it with the run `dev`:

```sh title="Compare a run with another weighting with the run dev"
just search spurr-2024 waterlevel --set distance_weights.waterlevel=0.2
just compare spurr-2024 waterlevel dev
```

The comparison pairs the detections of both runs by origin time, within 1 s, and reports the detections lost and added, how far the paired detections moved, and how their picks and residuals changed. `just dashboard` shows the comparison with maps and histograms.
