# Third-party notices

ReelLife is © Nicholas Hadler and contributors, licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE).

It ships these third-party files unmodified. Each keeps its own licence;
the licence texts sit beside the files.

| What | Where | Licence |
|---|---|---|
| [htmx](https://htmx.org) 2.0.11 | `cues/static/cues/vendor/htmx.min.js` | 0BSD (`htmx.LICENSE`) |
| [Alpine.js CSP build](https://alpinejs.dev) 3.17.5 | `cues/static/cues/vendor/alpine-csp.min.js` | MIT |
| [AG Grid Community](https://www.ag-grid.com) 36.2.0 | `cues/static/cues/vendor/ag-grid-community.min.js` | MIT (`ag-grid-community.LICENSE`) |
| [Instrument Sans](https://github.com/Instrument/instrument-sans) | `cues/static/cues/vendor/instrument-sans-*.woff2` | SIL OFL 1.1 (`instrument-sans.LICENSE`) |
| [IBM Plex Mono](https://github.com/IBM/plex) | `cues/static/cues/vendor/ibm-plex-mono-*.woff2` | SIL OFL 1.1 (`ibm-plex-mono.LICENSE`) |
| RapidCue cue sheet template (2016 v3) | `cues/exporters/cue_sheet/rapidcue_template.xlsx` | ASCAP and BMI's published template, included unchanged so exports match what RapidCue accepts |
| Pro Tools test exports from [ptulsconv](https://github.com/iluvcapra/ptulsconv) | `fixtures/third_party/ptulsconv/` (tests only) | MIT (`LICENSE` there) |

`cues/static/cues/vendor/VERSIONS` records each vendored file's package,
version, licence and SHA-256; `scripts/vendor_assets.py` regenerates it.
Python and npm dependencies are installed from their registries, not
vendored; `uv.lock` and `package-lock.json` pin them.
