# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Download the NASA TROPESS (MUSES) AIRS and CrIS L2 HDO netCDF files from GES DISC onto
GLADE, for `data_engineering_wiso-sat`, which builds zarr stores from them and does not
download. Four collections are already partly or fully on disk in old download folders.
The job is to fetch every granule of 14 collections that is not already there, into one
new folder per collection, and to hand back a report. The brief is
`/glade/work/kheyblom/data_engineering/data_engineering_wiso-sat/docs/handoff-tropess-download.md`;
the collections are described in that repo's `docs/products/tropess.md`. This is one of
a family of sibling downloaders in `../` (`access_tropomi`, `access_imerg`,
`access_mswep`, `access_gleam`, `access_smap`). It must stay consistent with them in
layout, config shape, logging and style, except where a deviation is recorded below.

Task list and status live in [TASKS.md](TASKS.md). Mark tasks complete there as they
finish. Keep the *Current working state* section below up to date at the end of every
working step, so that a new session can resume without re-deriving anything.

## Current working state

- Task 1 (this CLAUDE.md, and TASKS.md filled out) is done (2026-10-22).
- Task 2 is in progress: the local repo is on `main`, `origin` set (fetch HTTPS, push
  SSH). **The GitHub repo `kheyblom/access_tropess` does not exist yet** (2026-10-22).
  `gh` is not installed here and its login is interactive, so the user has to create it
  (empty) on github.com; then `git push -u origin main` and mark task 2 complete.
- Task 3 is complete (2026-10-22): the user approved the design, and everything is built
  and committed (see TASKS.md task 3 for the build notes and the dry run table).
- Full dry run (2026-10-22 23:09): **12,126 files, 237.00 GB to fetch**, matching the
  handoff except CrIS-JPSS1 FS Standard v2 at 139 (live, was 138). The 5 CrIS-JPSS1
  replacements are confirmed; one granule per date everywhere; the old folders match
  CMR by name and size. Output: `logs/dryrun_full.out`.
- **Next: task 4** (tiny end-to-end test, 7 files, 255 MB, login node). Ask the user
  before starting it. Not yet proven: that CMR's MiB size rounds to the exact byte
  count for every collection (it did for one RS Summary file), and that the v2 (F2p10)
  files carry `VersionID` 2 at the root.
- Facts checked (2026-10-22): `time`, `ShortName` (bytes) and `VersionID` (int32 array)
  are at the file root (v1 RS Summary); scratch is 70.3 of 150 TiB used.

## Rules from the handoff (always)

- **Never modify, move, rename or delete anything already in the raw tree.** The four
  existing folders stay as they are, including their extra files (L3
  `AIRS_TROPESS_gridded_*`, `*_target.nc` local copies) and the `.DS_Store` files in
  `raw/AIRS` and `raw/CRIS`. The run only ever lists them.
- Never edit a netCDF file (no `ncks`). File names stay exactly as distributed.
- Never print, log or commit credentials (`~/.netrc`, cookies, tokens). Never read
  `~/.netrc`.
- Keep parallel connections few (4).
- Show the user any job script and its cost before submitting.

## Settled design choices

From the handoff (2026-10-22):
- **One new folder per collection**, named by its GES DISC `ShortName.version`, under
  the instrument folder, with one subfolder per year as in the GES DISC path:
  `raw/AIRS/TRPSDL2HDOAIRSORS.1/2002/TROPESS_AIRS-Aqua_L2_Standard_HDO_20020830_MUSES_R1p6_RS_F0p8_J0.nc`.
  This deviates from the sibling `<download>/<version>/raw/<product>/` layout. Every
  folder stays under ~2,000 files (one file per day).
- **"Missing only" for the two partly held collections** (AIRS FS Standard v1, CrIS-JPSS1
  FS Standard v1): download into the new folder only the file names not in the existing
  folder. Compare exact names, not dates. For CrIS-JPSS1 that includes 5 upstream files
  that replace older local files for the same dates under R1p24 (`tropess.md` R18);
  download those too and keep the old ones.
- The two complete collections (AIRS RS Summary, CrIS-SNPP RS Summary) need nothing.
- **Enumerate from CMR** by concept id; don't build URLs. The `GET DATA` RelatedUrl is
  the HTTPS link.
- **Integrity** (no checksums in CMR): size equals CMR's; opens with h5py with a readable
  `time` dataset; root `ShortName` and `VersionID` match the collection. Re-fetch
  failures; report any that persist.
- **Manifest** per collection, one CSV in its folder: file name, bytes, CMR size (MB),
  CMR `updated`, download time (UTC), URL, and the CMR query time (the cutoff for the
  live v2 collections).

Approved by the user (2026-10-22, TASKS.md task 3):
- earthaccess login, search by concept id, and session (imerg pattern); 4 workers.
- Config `collections` list with `name`, `concept_id`, `instrument`, optional
  `existing` (absolute path) and optional per-collection `date_range` (the tiny config
  uses it), plus a global `date_range`.
- Files in the new folders are skipped when their size equals CMR's; any other size is
  fetched again. The content check runs in the worker before the rename, and again on
  every file in the verification pass. A right-size file that fails the content check
  is reported as "bad content" and is not re-fetched by a rerun (it would be skipped on
  size); look at it by hand.
- Manifest `<ShortName.version>_manifest.csv` in each collection folder, rewritten after
  each run with the verified files: `file_name, bytes, cmr_size_mb, cmr_updated`
  (`ProviderDates` Update)`, cmr_revision_date` (`meta.revision-date`)`,
  downloaded_utc` (the file's mtime)`, url, cmr_query_utc`.
- `--dry-run` also audits the old folders (names CMR no longer lists, with the
  granule now covering that date; size differences). It changes nothing.

## Remote: GES DISC and CMR (checked 2026-10-22)

- Files: `https://data.gesdisc.earthdata.nasa.gov/data/TROPESS_<type>/<ShortName>.<version>/<YYYY>/<file>.nc`,
  e.g. `.../TROPESS_Summary/TRPSYL2HDOAIRSFS.2/2026/TROPESS_AIRS-Aqua_L2_Summary_HDO_20260601_MUSES_R1p25_FS_F2p10_J1.nc`.
  One file per date in every collection (checked 2026-10-22); report it if that changes.
- Access needs an Earthdata Login with the GES DISC application approved. It was
  approved for imerg (2026-09-29), and `~/.netrc` exists. A HEAD passes without the
  approval, so `check_access` does a one-byte ranged GET (imerg lesson).
- CMR granule search:
  `https://cmr.earthdata.nasa.gov/search/granules.umm_json?collection_concept_id=<id>&page_size=2000&sort_key=start_date`
  (no auth). Per item:
  - `umm.RelatedUrls`: `GET DATA` (HTTPS), plus `GET DATA VIA DIRECT ACCESS` (s3) and
    OPeNDAP links, which are not used;
  - `umm.DataGranule.ArchiveAndDistributionInformation[0]`: `Name: Not provided`,
    `Size` in `MB` meaning MiB to full precision, so `round(Size * 1024**2)` is the
    exact byte count (330,614 bytes local = CMR for an AIRS RS Summary file);
  - `umm.ProviderDates`: `Insert` and `Update`; `meta.revision-date`;
  - `umm.CollectionReference`: `ShortName` and `Version` ('2').
- Collections, concept ids, counts and sizes: the handoff's "What to download" table,
  and the dry run table in TASKS.md task 3. CMR lists 16 TROPESS HDO collections at
  GES DISC (2026-10-22): the 14 configured and the 2 complete on disk.
  `earthaccess.search_datasets` takes no short-name pattern; query
  `collections.umm_json` with `options[short_name][pattern]=true` (see the notebook).
- File names: `TROPESS_<platform>_L2_<type>_HDO_<YYYYMMDD>_MUSES_<R1pNN>_<stream>_<F0pN>[_J<n>].nc`;
  platforms `AIRS-Aqua`, `CrIS-SNPP`, `CrIS-JPSS1`; stream `RS`, `FS` or `SC_AUS`; the
  joint flag is absent from early files.
  The v2 collections (FS, from 2026-06-01, R1p25, format F2p10) are live and growing.

## Local data (checked 2026-10-22)

- Raw root `/glade/derecho/scratch/kheyblom/data/wisosat/raw/`. It is **Lustre**: use
  `lfs find` scoped to one collection folder with a `-maxdepth`, never a broader path.
  The old folders are flat, so a single `os.listdir` per folder is enough.
- Existing folders (entries): `AIRS/TRPSYL2HDOAIRSORS_1-20240806_185241` 6,361;
  `AIRS/TRPSDL2HDOAIRSFS_1-20240912_174250` 1,426 (1,251 `TROPESS_*.nc`, 173
  `*_target.nc`, 2 `AIRS_TROPESS_gridded_*`); `CRIS/TRPSYL2HDOCRSRS_1-20240923_230130`
  1,865; `CRIS/TRPSDL2HDOCRS1FS_1-20240912_023602` 1,208.
- When comparing names with an existing folder, match `TROPESS_*.nc` only; the
  `*_target.nc` copies and L3 files are not upstream granules.
- Quota: scratch 70.3 of 150 TiB used; the job is ~237 GB in ~12,100 files. Derecho
  scratch is subject to purge.

## Environment and commands

- Python 3.13 environment managed by `uv`, on a **uv-managed interpreter only**:
  `.python-version` is `3.13`, and `pyproject.toml` sets `[tool.uv]
  python-preference = "only-managed"` and `requires-python = ">=3.12,<3.14"`. uv's
  `cpython-3.13.12` is installed under `~/.local/share/uv/python/`. Don't use 3.14:
  numpy segfaults on it (see `../access_imerg/CLAUDE.md`). Run everything as
  `uv run python <script> --config config/<file>.yaml`.
- Dependencies: `earthaccess` (0.19.0), `requests`, `pyyaml` (for
  `utils/path_utils.py`), `h5py` (integrity check), plus `xarray`, `netcdf4`,
  `h5netcdf` and `jupyter` for the search notebook and spot checks. The venv is built.
- earthaccess auth is `earthaccess.login(strategy='netrc')`. earthaccess 0.19 notes are in
  `../access_imerg/CLAUDE.md`.
- No build, lint or test tooling exists in this repo or its siblings. Verify with
  `--dry-run` and a `_tiny` config into a separate scratch directory before any real run.
- Downloaded files are untrusted data: when opening them from an ad hoc script, run
  Python with `-I`, keep the script outside the data folders, and pass paths as arguments.

## Sibling conventions to follow

`../access_tropomi` is the most recent sibling and the model for docs, run scripts and
the monitor. `../access_imerg` is the model for the CMR/earthaccess listing, the
per-file `Pool` transfer over an earthaccess session, `check_access` and signal handling.

Repo layout:
- `tropess_download.py` (one entry-point script)
- `tropess_search.ipynb` (a scratch notebook for browsing CMR and a file)
- `config/config_download*.yaml` (one YAML per run variant, including a `_tiny` smoke-test one)
- `utils/log_utils.py` and `utils/path_utils.py`, copied from tropomi/imerg
  (`setup_logging` takes a `fmt` arg).
- `logs/`, `run_download.sh`, `monitor_download.sh`, `job_download.pbs`, `README.md`

Config shape: `directories.download`, `directories.logs` (`./logs`), `log_file`,
`n_processes`, `retries` and the dataset selectors. `run_download.sh` and
`monitor_download.sh` extract `log_file` and `logs:` with `sed`, so keep those as
top-level, simple scalar lines.

Script shape:
- A module docstring explains the remote layout, the local layout and restart behaviour.
- Google-style docstrings (`Args:`, `Returns:`, `Raises:`).
- Module-level compiled regexes carry a comment showing an example match.
- `LOG = logging.getLogger(__name__)`, and
  `LOG_FORMAT = '%(asctime)s [%(levelname)s] %(processName)s %(name)s: %(message)s'`.
- `main(settings, dry_run=False)` plus `argparse` with `--config` (required) and
  `--dry-run`.
- The final log line is `done :-)`.

Robustness (lessons carried over from imerg and tropomi):
- Build the full file list and total size up front, and log them before any transfer.
- One-byte ranged GET (`check_access`) before starting the pool, dry run included. Don't
  retry on 401/403; retry 5xx and connection errors with backoff.
- Write to `.part` and rename into place only after the checks. Sweep the run's stray
  `.part` files before and after a run and on interrupt, only in the new folders.
- SIGINT/SIGTERM handlers in main: a background job started from a non-interactive
  shell inherits SIGINT as ignored. In `init_worker`, reset SIGTERM to `SIG_DFL` and
  ignore SIGINT, because on 3.13 the pool forks and workers inherit main's handlers.
- Write `logs/<stem>.pid` (`<host> <pid>`) once enumeration finishes, and remove it on
  exit. Refuse a second concurrent run over the same directory.
- Per-worker logs go to `<log stem>_<processname>.log`.
- Finish with a verification pass that exits non-zero if anything is missing. A rerun
  picks up exactly those files.

Running (tropomi pattern):
- `run_download.sh [config]` launches `setsid nohup nice -n 19 uv run python ...`,
  fully detached, and starts `monitor_download.sh`, which logs every 5 min to
  `logs/<stem>_monitor.log` and stops the run at > 2 cores or > 2 GiB RSS.
  `--status [config]` reads the pid file. Login nodes share GLADE but not process
  tables, so a run on another node has to be checked with `ssh <host>`.
- `job_download.pbs` is the fallback and targets **Casper** (`#PBS -q casper`). Derecho
  compute nodes have no outbound internet.

## Compute and allocation

The allocation is finite, so minimize core-hours. A download is network bound. The
sibling practice is a detached, `nice -n 19`, monitored run on a login node (the user
allowed this for tropomi, 2026-10-15), with the Casper PBS job as a fallback. The
handoff and the global rules say a download this size belongs off the login node unless
the user prefers otherwise, so **ask the user which route before starting any real
download**. At imerg's ~100–130 MB/s from GES DISC, ~237 GB is roughly 30–45 min.
Always show any `qsub`/`qcmd` command and wait for approval. `qsub -A "$PBS_ACCOUNT"`
needs a login shell (`bash -lc`) for the variable to be set.

## Git

- `origin` fetches from `https://github.com/kheyblom/access_tropess.git` but pushes
  over SSH (`git@github.com:kheyblom/access_tropess.git`), because HTTPS has no
  credential helper on this system while the GitHub SSH key works. Set it with
  `git remote set-url --push origin git@github.com:kheyblom/access_tropess.git`. Branch
  `main`.
- Commit each logical change as it is made. Use short imperative subjects, as in the
  siblings ("Add TROPOMI download pipeline and configs").
- `.gitignore` is the sibling copy plus `logs/*.pid`. It excludes `*.log`, `*.out`,
  `nohup*`, `.venv` and `draft*`. Track `pyproject.toml`, `uv.lock` and the search
  notebook, as the siblings do.
