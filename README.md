# access_tropess

Download NASA TROPESS (MUSES) AIRS and CrIS L2 HDO netCDF files from GES DISC onto
GLADE, for `data_engineering_wiso-sat`. Each collection goes into a new folder of its
own. Two collections were partly downloaded before into older folders, and for those
a run fetches only the granules whose file names are not already there. The old
folders are only ever listed. It is structured like `access_imerg` and its siblings:
one script driven by one YAML config, a thin `utils/` layer, per-worker log files, and
a run that is safe to repeat.

```
tropess_download.py               the downloader
tropess_search.ipynb              scratch notebook for browsing CMR and a file
config/config_download.yaml       every collection not fully on disk, into the raw tree
config/config_download_tiny.yaml  a few days of three collections into a separate directory, for a smoke test
utils/                            config loading, logging
run_download.sh                   start the download on a login node
monitor_download.sh               watch the run's footprint on the node, stop it if it grows
job_download.pbs                  fallback batch job (Casper)
HANDBACK.md                       the report of the 2026-10-22 download for data_engineering_wiso-sat
```

## One time setup

GES DISC needs an Earthdata Login with the GES DISC application approved on the
account. The credentials go in `~/.netrc`, which earthaccess reads; never print or
commit them. Install the environment with `uv sync`. It builds on a uv-managed Python
3.13 only, as in the sibling repos.

## Running

```bash
# what would be downloaded, plus an audit of the old folders; no transfers
uv run python tropess_download.py --config config/config_download.yaml --dry-run

# a few days into a separate directory, as a smoke test
./run_download.sh config/config_download_tiny.yaml

# everything missing
./run_download.sh
```

`run_download.sh` starts the download under `setsid`, in a session of its own, so it
survives logout and anything that kills the starting shell's process group (`nohup`
alone only blocks SIGHUP). It runs at `nice -n 19` so it yields to anything
interactive on the shared node.

Once the listing finishes, the run records its host and pid in
`logs/<log_file stem>.pid`, and it deletes that file when it ends. The host matters:
Derecho's login nodes share GLADE but not their process tables.

```bash
./run_download.sh --status              # host, pid, whether it is alive
tail -n 40 logs/tropess_download.log    # main log: totals, verification, failures
ls logs/                                 # one log per worker, one line per file
```

Stopping is always safe. `kill -INT <pid>` or a plain `kill <pid>` sweeps partial
files and removes the pid file on the way out, and a rerun picks up exactly what is
missing. A second run over the same download directory is refused while the first is
alive.

`run_download.sh` also starts `monitor_download.sh`, detached. It waits for the run's
pid file and then logs one line every 5 minutes to `logs/<log_file stem>_monitor.log`:
the run's CPU and resident memory, your user's memory against the login node's
per-user cap, the node's load and free memory, and the run's progress and MB/s. If the
run uses more than 2 cores or 2 GiB for two samples in a row, the monitor stops it
with `kill -INT`, and a rerun resumes. The limits can be set from the environment
(`MAX_CORES`, `MAX_RSS_MB`, `INTERVAL`, `BREACHES`). `--status` shows the latest line.

`job_download.pbs` runs the same download as a batch job. Submit it from the repo with
`qsub -A "$PBS_ACCOUNT" job_download.pbs`. It targets Casper, because Derecho compute
nodes have no outbound internet.

## Config

| key | meaning |
|---|---|
| `directories.download` | the raw root; each collection lands in `<download>/<instrument>/<ShortName.version>/<year>/` |
| `directories.logs`, `log_file` | where the main log, the worker logs and the pid file go |
| `n_processes` | parallel downloads; keep it small |
| `date_range` | inclusive `['YYYY-MM-DD', 'YYYY-MM-DD']`, or `null` for the whole record |
| `retries` | attempts per file before it is reported failed |
| `collections` | one entry per collection, see below |

Each collection has a `name` (`ShortName.version`, as GES DISC names it, e.g.
`TRPSDL2HDOAIRSFS.1`), its CMR `concept_id`, and its `instrument` folder (`AIRS` or
`CRIS`). A partly held collection also names its old folder as `existing`, and a
collection may set its own `date_range`.

## How it works

GES DISC lays each collection out as
`TROPESS_<type>/<ShortName>.<version>/<year>/<file>.nc`, one file per day, e.g.

```
TROPESS_Standard/TRPSDL2HDOAIRSFS.1/2026/TROPESS_AIRS-Aqua_L2_Standard_HDO_20260531_MUSES_R1p24_FS_F0p9_J1.nc
```

The run lists each collection with a CMR search on its concept id. Each granule
comes with its HTTPS link, its size (in MiB, precise enough that rounding gives the
exact byte count) and its CMR dates, so the total volume is known before anything
transfers. The local tree gets a new folder per collection, split by year as on the
server:

```
<download>/AIRS/TRPSDL2HDOAIRSFS.1/2026/TROPESS_AIRS-Aqua_L2_Standard_HDO_20260531_MUSES_R1p24_FS_F0p9_J1.nc
```

For a collection with an `existing` folder, a granule whose exact file name is in that
folder is left out. A granule that replaces an old file under a new name (a later
algorithm for the same date) is fetched, and the old file stays.

Each file in the new folders is checked with one `stat`. If its size matches CMR's it
is skipped; if it is missing or of a different size it is downloaded. `n_processes`
workers each stream their files over their own authenticated session. Each file is
written to `<file>.part`, and accepted only if its byte count equals CMR's and it
opens with h5py, has a readable root `time`, and carries the collection's `ShortName`
and `VersionID`; CMR publishes no checksums. Only then is it renamed into place. A
failed file is retried `retries` times with backoff, except on a 401 or 403, before it
is reported. Stray `.part` files in the run's folders are swept before and after every
run and on Ctrl-C.

The run ends with a verification pass that repeats the size and content checks on
every listed file and exits non-zero if anything is missing or bad. It then writes a
manifest per collection, `<ShortName.version>_manifest.csv` in the collection's
folder, with one row per verified file: file name, bytes, CMR size (MB), CMR updated
and revision dates, download time (UTC), URL, and the time of the CMR query, which is
the cutoff for a collection that is still growing.

A `--dry-run` reports, per collection, the granules CMR lists, the algorithm and
format versions seen, any date with more than one granule, and the dates to fetch. It
also audits the old folders: granules CMR no longer lists (with the one that now
covers that date) and files whose size differs from CMR's. It changes nothing.
