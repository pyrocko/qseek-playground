# Utah FORGE, April 2024

Detect and locate the microseismicity of three days, 4 to 6 April 2024, at the [Utah FORGE](https://utahforge.com/) geothermal test site near Milford, Utah, in a 3D velocity model.

The example is a test bed for travel times in a 3D velocity model. It uses the [`FastMarching3D`](https://pyrocko.github.io/qseek/configuration/ray-tracers/#3d-velocity-model) ray tracer with the NonLinLoc grids `FORGE_3Dv3_5_large`. The example has no reference catalog: compare runs with each other, see below.

## Run it

From the root of the playground, after `just setup`:

```sh title="Download the data and run the search"
just download forge-2024
just search forge-2024 dev
```

The download takes about 1 minute and writes 2.3 GB of waveforms into `sds/` and the StationXML files into `metadata/`. One station, the node `6K.CS01`, records at 1000 Hz and makes up 1.7 GB of it.

The search takes about 80 seconds on a workstation with an NVIDIA GeForce RTX 4060.

Look at the detections in the web UI or with the waveforms in Pyrocko Snuffler:

```sh title="Explore the run"
just explore forge-2024 dev
just snuffler forge-2024 dev
```

## Network

| Station | Channels | Sampling rate | Elevation | Position relative to the model origin |
| --- | --- | --- | --- | --- |
| `UU.FORK` | `EH[Z12]` | 200 Hz | 1408 m | 0.9 km E, 0.3 km S |
| `UU.FOR2` | `HH[ZNE]` | 200 Hz | 1760 m | 2.1 km E, 1.0 km S |
| `UU.FSB1` | `DN[Z12]` | 500 Hz | 1697 m | 0.4 km E, 2.1 km S |
| `UU.FSB3` | `DN[Z12]` | 500 Hz | 1701 m | 1.4 km E, 1.0 km N |
| `6K.CS01` | `GN[Z12]` | 1000 Hz | 1627 m | 1.0 km W, 1.5 km S |

`download.json` selects these five stations. The original configuration also listed `UU.FORK2`, `UU.FORK3`, `UU.FORB` and `UU.FSB2`; EarthScope returns no waveforms of the first three for these days, and `UU.FSB2` was blacklisted. With five stations, the search requires picks at three of them (`min_stations`).

## Setup

| | |
| --- | --- |
| Stations | 5 stations of the networks `UU` and `6K` from [FDSN Rush](https://miili.github.io/FDSN-rush/) with `download.json` |
| Pre-processing | Downsampling to 200 Hz, bandpass 20–50 Hz |
| Search volume | 4.8 km × 3.6 km × 2.8 km; 400 m root nodes, refined over 5 levels to 25 m |
| Image function | PhaseNet with the `original` weights, picks above 0.2 |
| Travel times | Fast marching in the 3D P- and S-wave models of `models/` |
| Magnitudes | None |

The search configuration is `forge.json`. `just search` copies it to `runs/<run>.json`, applies the `--set` overrides and runs the search from this directory.

Depths in the detections are relative to sea level. The origin of the search volume and of the velocity models is 38.50402°N, 112.89639°W at 1650 m above sea level, near the surface at the site. The depth bounds of the octree are depths below this origin, 300 m to 3100 m, which is 1350 m above to 1450 m below sea level.

## Velocity model

The models are the composite 3D seismic velocity model of the Utah FORGE site: Finger, C., Niemz, P., Ermert, L., & Lanza, F. (2024). *Utah FORGE: Composite 3D Seismic Velocity Model* [Data set]. GDR. University of Utah Seismograph Stations. <https://doi.org/10.15121/2305384>

`models/` holds the P- and S-wave velocity models of the site as NonLinLoc grids (`.hdr` header and `.buf` data): 111 × 75 × 67 nodes with a spacing of 50 m, 5.55 km east–west, 3.75 km north–south and 3.35 km deep, starting 200 m above the origin. The grids store slowness times length (`SLOW_LEN`). The `reference_location` in `forge.json` places the grids, because their coordinates are relative.

The search volume lies inside the grid of the models, so Qseek needs no travel times outside of it.

## Compare runs

The example has no reference catalog, so `just search` prints no catalog metrics. Compare a run with another run, e.g. one with a different image function or another interpolation of the travel times:

```sh title="Compare a run with the run dev"
just search forge-2024 cubic --set ray_tracers.0.interpolation_method=cubic --set ray_tracers.1.interpolation_method=cubic
just compare forge-2024 cubic dev
```

The comparison pairs the detections of both runs by origin time, within 1 s, and reports the detections lost and added, how far the paired detections moved, and how their picks and residuals changed. `just dashboard` shows the comparison with maps and histograms.
