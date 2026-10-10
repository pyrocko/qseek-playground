# Norcia, November 2016

Detect and locate the aftershocks of three days of the [2016 central Italy sequence](https://en.wikipedia.org/wiki/2016_Central_Italy_earthquakes), two weeks after the Mw 6.5 Norcia earthquake of 30 October 2016. From 15 to 17 November 2016, the INGV catalog lists 1071 earthquakes in the search volume, from ML 0.7 to Mw 3.8, along the normal-fault system between Visso in the north and Amatrice in the south.

The example is a test bed for station weighting in a tectonic setting. Three networks overlap: the permanent INGV network, the SISMIKO emergency network installed after the Amatrice earthquake, and a seismic microzonation network in the town of Amatrice. The microzonation network puts 12 stations within 2.9 km of a common center, 24 km south-southeast of the center of the search volume. The other 37 stations are 8.7 km (median) from their nearest neighbor. The 12 stations are a quarter of the 49 stations and record 19% of the picks of the run below.

## Run it

From the root of the playground, after `just setup`:

```sh title="Download the data and run the search"
just download norcia-2016
just search norcia-2016 dev
```

The download takes about 12 minutes and writes 4.4 GiB of waveforms into `sds/` and the StationXML files into `metadata/`.

The search takes about 7 minutes on a workstation with an NVIDIA GeForce RTX 4060. It ends with the metrics of the run, here of the run `dev`:

```text
norcia-2016 / dev: qseek 3552b0cf96
  detections                         3732
  detections with min. picks         3602
  picks per event, median            54
  residual RMS, median [s]           0.3095
  nearest neighbor, median [m]       404.7
  search time [s]                    398.5
  reference events detected          1013
  recall                             0.946
  epicenter offset, median [m]       635.2
  depth offset |dz|, median [m]      4655
  depth offset dz, median [m]        -4650
```

Qseek finds 1013 of the 1071 catalog events: all 12 events of M3 and above, 205 of the 206 events from M2 to M3, 783 of the 830 events from M1 to M2 and 13 of the 23 events below M1. The detections lie 635 m (median) from the catalog epicenters. They are 4.7 km (median) shallower than the INGV routine locations, but only 0.4 km shallower than the relocations of Chiaraluce et al. (2017), see [Reference catalog](#reference-catalog). Every detection has at least 8 picks; 3602 of the 3732 have at least 20, the minimum for a well-constrained detection in this example (`min_picks` in `example.toml`); the smaller networks of the other examples use 8.

Look at the detections in the web UI or with the waveforms in Pyrocko Snuffler:

```sh title="Explore the run"
just explore norcia-2016 dev
just snuffler norcia-2016 dev
```

## Network

The stations are all velocity sensors: broadband and short-period `HH` and `EH` channels at 100, 125 and 200 Hz. Qseek resamples all traces to 100 Hz before phase picking. The distances are from the center of the search volume, 42.82°N, 13.17°E, 7 km east-northeast of Norcia.

| Group | Stations | Distance | Azimuth |
| --- | --- | --- | --- |
| Permanent, INGV network `IV` | `NRCA`, `MMO1`, `FDMO`, `LNSS`, `CESI`, `GUMA`, `CSP1`, `RM33`, `CAMP`, `TERO`, `ARRO`, `OFFI`, `ASSB` | 4.8–48.4 km | all around |
| Temporary, SISMIKO emergency network `8P` | `T1245`, `T1214`, `T1213`, `T1244`, `T1212`, `T1202`, `T1216`, `T1218`, `T1204`, `T1256`, `T1241`, `T1217`, `T1215`, `T1243`, `T1221`, `T1219`, `T1220`, `T1246`, `T1211`, `T1247` | 4.3–43.4 km | all around |
| Temporary, EMERSITO network `XO` | `AM05` | 23.0 km | 40° |
| Microzonation cluster at Amatrice, `3A` and `IV` | `3A.MZ08`, `MZ10`, `MZ12`, `MZ25`, `MZ26`, `MZ27`, `MZ28`, `MZ29`, `MZ30`, `MZ31`, `IV.T1299`, `IV.SMA1` | 22.6–25.3 km | 147–156° |
| Microzonation, outside the cluster, `3A` | `MZ103`, `MZ24`, `MZ11` | 15.0–20.8 km | 147–157° |

- The cluster lies in and around the town of Amatrice: its 12 stations are within 2.9 km of their centroid at 42.626°N, 13.301°E. With `3A.MZ24`, `3A.MZ11` and `8P.T1204`, 3 km to the north, 15 stations lie in a 13° sector from 143° to 156°, 20.0–25.3 km from the center.
- Seven pairs of stations in the cluster are less than 500 m apart, the closest `3A.MZ12` and `3A.MZ30` at 202 m. `3A.MZ08` is 403 m from the permanent station `IV.T1299`.
- The example has no co-located sensors under different location codes. 34 of the 49 sites also record an accelerometer (`HN`) under the same station code; `download.json` takes only the `HH` and `EH` channels.
- Outside the cluster, the median distance to the nearest station is 8.7 km. The largest azimuthal gap of all stations from the center is 42°.
- The `8P` stations record short-period Lennartz sensors (`EH`, 100 or 125 Hz) or broadband sensors (`HH`, 100 Hz: `T1243`, `T1245`, `T1246`, `T1247`, `T1256`). The `3A` stations record 5 s Lennartz sensors at 100 Hz, `3A.MZ103` a SARA SS20 at 200 Hz.
- `IV.MC2`, `IV.MF5`, `IV.MNTT`, `IV.PF6` and `IV.SAP2`, all within 45 km, have no waveforms at INGV for these days, `IV.MOMA` only 1.5 hours; the example leaves the six out.

The data have gaps. Qseek logs a warning per channel for each 5-minute batch without data.

- The microzonation network was being removed on 17 November: 7 of the 10 `3A` stations of the cluster stop between 09:05 and 12:12 UTC, and `3A.MZ11` at 13:07 UTC. `3A.MZ24`, `MZ25`, `MZ26` and `MZ27` record until the end. `3A.MZ103` stops on 16 November at 00:59 UTC, and `3A.MZ30` recorded 29% of the three days.
- `8P.T1202`, `T1204`, `T1217` and `T1244` start on 16 November between 10:40 and 10:52 UTC.
- `XO.AM05` has no data on 16 November; `8P.T1241` recorded 59% and `IV.SMA1` 72% of the three days.

## Setup

| | |
| --- | --- |
| Stations | 49 stations of the networks `IV`, `8P`, `3A` and `XO`, `HH` and `EH` channels, from [FDSN Rush](https://miili.github.io/FDSN-rush/) with `download.json` |
| Search volume | 40 km × 64 km × 24 km around 42.82°N, 13.17°E, from 2 km above to 22 km below sea level; 2 km root nodes, refined over 4 levels to 250 m |
| Image function | PhaseNet with the `instance` weights, trained on the Italian INSTANCE data set |
| Travel times | Fast marching in the 1D model `norcia.nd` |
| Magnitudes | None: Qseek has no local magnitude model for Italy |

The search configuration is `norcia.json`. `just search` copies it to `runs/<run>.json`, applies the `--set` overrides and runs the search from this directory.

The search volume holds 7680 root nodes. It spans 42.53–43.11°N and 12.93–13.41°E, the area of the catalog query: 1071 of the 1096 INGV events of these days within 0.5° of 42.85°N, 13.15°E lie in it above 20 km depth.

Depths in the configuration, the velocity model and the detections are relative to sea level. The stations lie from 253 m to 1541 m above sea level.

## Velocity model

`norcia.nd` is the 1D P- and S-wave gradient model with which Chiaraluce et al. (2017) relocated the first three months of the sequence with NonLinLoc, a gradient version of the minimum 1D model of the region ([Chiaraluce et al., 2017](https://doi.org/10.1785/0220160221), electronic supplement, Figure S2). The paper gives the model only as a figure; the values below are digitized from the nodes of that figure, to about ±0.01 km/s.

| Depth [km] | Vp [km/s] | Vs [km/s] | Vp/Vs | Density [g/cm³] |
| --- | --- | --- | --- | --- |
| −2 | 5.32 | 2.75 | 1.93 | 2.59 |
| 0 | 5.63 | 2.81 | 2.00 | 2.64 |
| 4 | 6.22 | 3.34 | 1.86 | 2.77 |
| 8 | 6.23 | 3.39 | 1.84 | 2.77 |
| 12 | 6.24 | 3.36 | 1.86 | 2.77 |
| 20 | 6.27 | 3.44 | 1.82 | 2.78 |
| 30 | 7.50 | 4.00 | 1.88 | 3.12 |

The velocities change linearly between the nodes. The density follows from Vp with the Nafe–Drake relation of [Brocher (2005)](https://doi.org/10.1785/0120050077). `norcia.nd` keeps the velocities of 30 km depth down to 60 km.

The layered CIA model of [Herrmann et al. (2011)](https://doi.org/10.1785/0120100184), derived from surface-wave dispersion, has a slower upper crust, 3.75 km/s in the first 1.5 km. With it, the same search gives origin times 0.67 s (median) earlier than the relocations of Chiaraluce et al. (2017).

## Reference catalog

`reference/ingv-catalog.txt` holds the events of the [INGV FDSN event service](https://webservices.ingv.it/fdsnws/event/1/) from 15 to 17 November 2016 within the search volume, 42.53–43.11°N and 12.93–13.41°E, and above 20 km depth: 1071 earthquakes, 324 on 15, 381 on 16 and 366 on 17 November. 1054 have a local magnitude, 16 a duration magnitude and the largest, 3 km east of Norcia on 16 November, Mw 3.8. 262 events are revised locations of the INGV bulletin, the others locations of the INGV seismic surveillance. `just catalog norcia-2016` downloads the catalog again with the query in `example.toml`.

A detection matches a catalog event when their origin times differ by at most 3 s; each catalog event matches at most one detection. The metrics compare the matched pairs: epicenter offset, depth offset (positive when Qseek is deeper) and origin time offset. The catalog lists only the larger events, so detections without a match are not false detections.

The depth offset to the INGV catalog comes from the catalog. The relocated catalog of Chiaraluce et al. (2017, Table S1 of the electronic supplement) lists 816 events from 15 to 17 November; 800 of them are in the INGV catalog, with a median depth of 6.1 km in the relocated catalog and 10.1 km in the INGV catalog. The detections of `dev` match 783 of the 816 relocated events within 3 s. Against the relocations, they are 0.4 km shallower, 0.04 s later and 480 m off in epicenter (medians). The INGV catalog gives origin times 0.9 s (median) earlier than the detections.

## Compare station weightings

At the Qseek commit of the run above (`3552b0c`), the configuration key of the station weighting is `distance_weights`; newer versions name it `station_weights`. By default, Qseek gives the 4 closest stations of a node full weight and tapers the others over 63 km, twice the mean interstation distance of this network. At the center of the search volume, 5 km deep, the 12 stations at Amatrice keep weights of 0.88–0.92, a quarter of the summed weight of all stations; at the northern end of the sequence, 43.05°N, they keep 0.33–0.38. Change the weighting of a run with `--set` and compare it with the run `dev`:

```sh title="Compare a run with a shorter distance taper with the run dev"
just search norcia-2016 taper20 --set distance_weights.distance_taper=20000
just compare norcia-2016 taper20 dev
```

The comparison pairs the detections of both runs by origin time, within 1 s, and reports the detections lost and added, how far the paired detections moved, and how their picks and residuals changed. `just dashboard` shows the comparison with maps and histograms.

With a 20 km taper, the cluster keeps weights of 0.29–0.45 at the center and less than 0.01 at the northern end. That run gives 4333 detections and finds 1025 of the 1071 catalog events. It pairs 3625 detections with `dev`, moves them by 165 m horizontally and 269 m up (medians), and loses 92 and adds 432 detections with at least 20 picks; `just compare` flags the lost detections. The cluster stops recording on 17 November, so the detections of 15 and 16 November show its effect: on these days, the cluster stations give a median of 13 and 12 picks per detection in `dev`.
