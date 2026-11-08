# Third-party software and data

dgeo itself is licensed under the [GNU AGPL-3.0-or-later](LICENSE). It depends on, or can be used
with, the following third-party components. Their licences apply to them, not to dgeo.

## Python dependencies

| Package | Licence |
|---|---|
| numpy, scipy, pandas | BSD-3-Clause |
| matplotlib | Matplotlib License (PSF-based, BSD-compatible) |
| requests | Apache-2.0 |
| rasterio | BSD-3-Clause |
| streamlit, pydeck | Apache-2.0 |
| tzdata | Apache-2.0 |
| pytest (development only) | MIT |

## Docker image

The Docker image installs **FFmpeg** from the Debian archive (used through a subprocess to convert
audio files). Debian's FFmpeg build is licensed under the GPL/LGPL; its source code is available from
the Debian archive. If you redistribute the built image, you are also redistributing those binaries.

## Data sources (not included in this repository)

dgeo does not ship any flight or elevation data. When you use it, you fetch data from the services
below and must respect their terms.

### ADS-B

- **adsb.lol** — live API and daily `globe_history` dumps, licensed under the
  [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  Attribution is required, and share-alike applies to derived databases you redistribute.
  Data © adsb.lol contributors.
- **airplanes.live** — REST API intended for **non-commercial use**, rate-limited to 1 request per
  second (see <https://airplanes.live/api-guide>). dgeo queries it at most every ~3 s and disables it
  automatically if access is refused.

### Elevation

- **Copernicus DEM GLO-30** — free licence from the European Union / ESA. Products derived from it
  must carry the attribution:

  > © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS
  > by the European Union and ESA; all rights reserved.

  Modified data must instead state that it was "produced using Copernicus WorldDEM-30" followed by the
  same notice (see the licence, art. 6).
