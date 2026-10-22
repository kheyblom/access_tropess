We need to build a pipeline that downloads every NASA TROPESS (MUSES) AIRS and CrIS L2 HDO granule that is not already on disk, from GES DISC, so that `data_engineering_wiso-sat` can build zarr stores from them. The overall job is set out in the handoff, `/glade/work/kheyblom/data_engineering/data_engineering_wiso-sat/docs/handoff-tropess-download.md`; the collections are described in that repo's `docs/products/tropess.md`, section "Collections: stream, product type, platform". Four download folders already exist under `/glade/derecho/scratch/kheyblom/data/wisosat/raw/{AIRS,CRIS}/` and must not be touched. We will work through each task one at a time together.

# 1. init CLAUDE.md for the codebase [COMPLETED]
Write a CLAUDE.md in the same shape as `../access_tropomi/CLAUDE.md`: purpose, *Current working state*, settled design choices, a remote section (GES DISC and CMR facts), local data, environment and commands, sibling conventions, compute and allocation, and git. Pull in the conventions and lessons from the siblings, especially imerg (the other GES DISC/earthaccess downloader) and tropomi (the most recent).

# 2. init a git repo [COMPLETED]
Initialize a git repo here and connect it to the remote (see general notes).
- `origin` fetches over HTTPS and pushes over SSH (`git@github.com:kheyblom/access_tropess.git`), as the siblings do, because HTTPS has no credential helper on this system.
- branch `main`. Copy the sibling `.gitignore` (with `logs/*.pid`).
- local repo initialized (2026-10-22), `origin` set (fetch HTTPS, push SSH). **The GitHub repo `kheyblom/access_tropess` does not exist yet** (`git ls-remote` over SSH: "Repository not found", 2026-10-22). I asked for it to be created with `gh`, but `gh` is not installed on Derecho and its login is interactive, so I (the user) need to create it, empty, on github.com. Then push `main` and mark this task complete.
- 2026-10-22: I created the repo; `main` is pushed and tracks `origin/main`.

# 3. Plan and build the codebase [COMPLETED]
Build the pipeline to be consistent with the sibling access codebases: one `tropess_download.py` driven by a YAML config, `utils/` copied from a sibling, `config/config_download.yaml` plus a `_tiny` one, `run_download.sh` (detached login-node run with `--status`) and `monitor_download.sh` (from tropomi), `job_download.pbs` (Casper fallback), `README.md`, a `tropess_search.ipynb` scratch notebook, and a uv environment pinned to a uv-managed Python 3.13 (see imerg for why not 3.14).

Plan it with me before building. The proposed design:
- **listing**: enumerate each collection from CMR by its concept id (earthaccess `search_data(concept_id=...)`, as imerg; the handoff's `granules.umm_json` query underneath). Each granule gives the `GET DATA` HTTPS link, the size, `ProviderDates` (Update) and `meta.revision-date`. Check every granule's `CollectionReference` against the configured `ShortName.version`. Record the CMR query time (UTC); it is the cutoff for the live v2 collections.
- **exact sizes**: CMR's `Size` is MiB to full precision, so `round(Size * 1024**2)` is the byte count (as imerg found). Checked 2026-10-22 on `..._20020901_MUSES_R1p6_RS_F0p8_J0.nc`: 330,614 bytes local = CMR exactly.
- **selection**: a `collections` list in the config. Each entry has `name` (`ShortName.version`), `concept_id`, `instrument` (the raw-tree folder, `AIRS` or `CRIS`) and, for the two partly held collections, `existing` (the old download folder). A global `date_range` (ISO dates, `null` = everything) for the tiny config.
- **local layout** (set by the handoff): `<download>/<instrument>/<ShortName.version>/<YYYY>/<file>`, the year taken from the GES DISC path, with `directories.download` = `/glade/derecho/scratch/kheyblom/data/wisosat/raw/`. This differs from the sibling `<download>/<version>/raw/<product>/` layout.
- **missing only**: for a collection with `existing`, a granule whose exact file name is in that folder is left out (one `os.listdir`; names only, as the handoff says). The 5 CrIS-JPSS1 replacements have new names, so they are fetched. The existing folders are only ever listed, never written.
- **skip rule** in the new folders: skip a file whose local size equals CMR's; a file of any other size is re-downloaded to `.part` and renamed over it.
- **transfer**: imerg's `Pool` of workers over `earthaccess.get_requests_https_session()`, 4 workers, streaming to `<file>.part`. Retries with backoff; no retries on 401/403. A one-byte `check_access` before the pool, dry run included.
- **integrity** (per file, in the worker, before the rename): bytes equal CMR's; `h5py` opens it and reads the root `time` dataset; root `ShortName` and `VersionID` match the collection. A failure deletes the `.part` and retries. CMR gives no checksums.
- **verification**: a final pass over every listed file in the new folders, size and the same content check, which exits non-zero if anything is missing or bad. A rerun picks up exactly those files.
- **manifest**: per collection, `<ShortName.version>_manifest.csv` in its folder, rewritten after each run from the listing and what is on disk: file name, bytes, CMR size (MB), CMR updated, CMR revision date, download time (UTC, the file's mtime), URL, and the CMR query time.
- `--dry-run` reports per collection: CMR granules and date span, the algorithm/format tokens seen, files to fetch and their bytes, files already in the new folder, names left out because the existing folder holds them, existing-folder `TROPESS_*` files with no CMR counterpart (expected: the 5 replaced CrIS-JPSS1 files) and existing-folder files whose size differs from CMR (reported, never touched), and any date with more than one granule.
- **never touch**: nothing is written outside the new collection folders and `logs/`, except sweeping the run's own `.part` files.
- **Decided (2026-10-22): design approved as proposed.**

Build (2026-10-22):
- `tropess_download.py`, `config/config_download.yaml` (the 14 collections, into the raw tree) and `config/config_download_tiny.yaml` (into `data/tropess_tiny/`: AIRS FS Standard v1 2024-09-01..07, CrIS-JPSS1 FS Standard v1 2024-08-20..22, AIRS FS Summary v2 2026-06-01..03; 7 files, 255 MB), `utils/`, `run_download.sh`, `monitor_download.sh` (both from tropomi), `job_download.pbs` (Casper, 2 cores, 4 h), `README.md`, `tropess_search.ipynb`, and a uv venv (Python 3.13.12, earthaccess 0.19.0, h5py 3.16.0).
- additions to the design: a collection may set its own `date_range` (used by the tiny config); `existing` is an absolute path, so a test into another directory compares against the real old folder; the pid file covers the verification pass too.
- `earthaccess.search_datasets` takes no short-name pattern; the notebook queries CMR's `collections.umm_json` with `options[short_name][pattern]` directly. It lists 16 TROPESS HDO collections at GES DISC: the 14 configured plus the 2 complete on disk. No new collection (2026-10-22).
- tiny dry run: 7 files, 255.3 MB; 4 AIRS days and 2 CrIS days left out as held; the old CrIS 2024-08-21 R1p23 file is reported as replaced by R1p24. Access check passed.
- full dry run (2026-10-22 23:09–23:11, login node derecho3, `nice -n 19`, ~2 min): **12,126 files, 237.00 GB, all missing**, no warnings beyond the 5 replacements. Access check passed. Full output: `logs/dryrun_full.out`.

| collection | CMR granules | dates | to fetch | size |
|---|---|---|---|---|
| `TRPSDL2HDOAIRSORS.1` | 6,362 | 2002-08-30..2020-03-30 | 6,362 | 36.13 GB |
| `TRPSYL2HDOAIRSFS.1` | 1,814 | 2021-02-01..2026-05-31 | 1,814 | 4.25 GB |
| `TRPSYL2HDOAIRSFS.2` | 30 | 2026-06-01..06-30 | 30 | 0.12 GB |
| `TRPSDL2HDOAIRSFS.1` | 1,815 (1,251 held) | 2021-02-01..2026-05-31 | 564 | 30.84 GB |
| `TRPSDL2HDOAIRSFS.2` | 30 | 2026-06-01..06-30 | 30 | 1.48 GB |
| `TRPSFL2HDOAIRSFS.2` | 30 | 2026-06-01..06-30 | 30 | 18.80 GB |
| `TRPSYL2HDOCRSFS.1` | 110 | 2021-02-01..05-21 | 110 | 0.27 GB |
| `TRPSDL2HDOCRSFS.1` | 110 | 2021-02-01..05-21 | 110 | 5.64 GB |
| `TRPSDL2HDOCRSAUS.1` | 91 | 2019-11-01..2020-01-31 | 91 | 0.84 GB |
| `TRPSYL2HDOCRS1FS.1` | 1,887 | 2021-04-01..2026-05-31 | 1,887 | 5.83 GB |
| `TRPSYL2HDOCRS1FS.2` | 138 | 2026-06-01..10-16 | 138 | 0.80 GB |
| `TRPSDL2HDOCRS1FS.1` | 1,887 (1,203 held) | 2021-04-01..2026-05-31 | 684 | 36.49 GB |
| `TRPSDL2HDOCRS1FS.2` | **139** (handoff 138) | 2026-06-01..10-17 | 139 | 9.72 GB |
| `TRPSFL2HDOCRS1FS.2` | 137 | 2026-06-01..10-15 | 137 | 85.83 GB |

- every count matches the handoff; CrIS-JPSS1 FS Standard v2 grew by one day (live). One granule per date everywhere.
- AIRS FS Standard v1 to fetch: 2023-07-21, 2023-10-19, then 2024-09-05..2026-05-31 minus upstream gaps (CMR lists no file for, e.g., 2024-09-17..19). The old folder's 1,251 granules all match CMR by name and size.
- CrIS-JPSS1 FS Standard v1 to fetch: the 5 replacements (2021-05-21, 2022-03-08, 2022-08-13, 2023-12-27, 2024-08-21, all R1p24, updated 2025-09-04..09-11), dates inside the old window, and the days after 2024-09-05. The old folder's other 1,203 granules match CMR by name and size; its 5 replaced files are kept.

# 4. Test the pipeline [COMPLETED]
Run the tiny config end-to-end before any real download, into a separate scratch directory, on a login node (no core-hours). The tiny config covers a few days of one full collection and of one "missing only" collection (to prove names in the existing folder are left out).
- `./run_download.sh config/config_download_tiny.yaml`. Check `--status`, the per-worker logs, a clean verification pass, the manifest and `done :-)`.
- rerun it and confirm every file is skipped.
- interrupt a run with `kill -INT`. Confirm the `.part` files are swept, and that a rerun resumes and verifies.
- spot-check a v1 and a v2 (F2p10) file with xarray/h5py.
- fix anything found, commit, and delete the test data (with my okay).

Results (2026-10-22, login node derecho3, 2 workers):
- run: 7 files (255.3 MB) in 10 s, 0 errors. 3 AIRS FS Standard v1 days (09-05..07) fetched and the 4 held ones left out; the CrIS-JPSS1 2024-08-21 R1p24 replacement fetched; 3 AIRS FS Summary v2 days. Verified 7/7, three manifests written, `done :-)`.
- CMR's MiB sizes rounded to the exact byte count for every file, including v2 and a CrIS size given to 15 digits. The v2 (F2p10) files carry `VersionID` 2 and still have `time` at the root.
- rerun: all 7 present, nothing downloaded, verified 7/7, manifests rewritten with the new query time.
- found and fixed: the monitor waited up to 2 h for a pid file that a 10 s run had written and removed between two of its 60 s polls. `run_download.sh` now passes the launched pid, and the monitor stops waiting once that process is gone (retested: it exited 45 s after an all-skip run).
- `kill -INT` mid-transfer (a copy of the tiny config into my scratchpad, 1 worker): the in-flight `.part` was swept, the pid file removed, no processes left. Resume: 7 downloaded, verified 7/7.
- wrong size: one file trimmed by 500 bytes; the rerun fetched only that file and verified.
- bad content: one v2 file overwritten with zeros at the same size; the rerun skipped it on size, verification reported `bad content` and exited 1, and the manifest left it out. Deleting it and rerunning fetched it again. So a right-size corrupt file needs deleting by hand, as designed.
- spot checks: a v1 (R1p23 F0p9) and a v2 (R1p25 F2p10) file open with h5py and xarray (`target` 29,142 and 15,679 rows; `level` 17).
- the scratchpad copy is deleted. The tiny data is still at `/glade/derecho/scratch/kheyblom/data/tropess_tiny/` (7 files, 255 MB, plus 3 manifests) until I okay deleting it.

# 5. Audit before downloading [COMPLETED]
Dry-run the real config and report back before anything downloads:
- per collection: CMR count and size against the handoff table, and the CMR query time.
- the "missing only" contents against the handoff's expectations: AIRS FS Standard v1 = 2023-07-21, 2023-10-19, and every day 2024-09-05 .. 2026-05-31; CrIS-JPSS1 FS Standard v1 = 46 dates inside 2021-04-01 .. 2024-09-05, the 5 replacements, and the days after 2024-09-05.
- dates with two files, new collections (re-query `TRPS*L2HDO*` at GES DISC), and existing-folder files that differ from CMR.
- quota check: scratch was 70.3 of 150 TiB used on 2026-10-22; the job is ~237 GB in ~12,100 files.

Results (2026-10-22 23:30 UTC, `logs/dryrun_full.out`): the same as the task 3 dry run. 12,126 files, 237.00 GB, all missing; the counts match the handoff except CrIS-JPSS1 FS Standard v2 (139, live); one granule per date everywhere; the 5 CrIS-JPSS1 replacements and nothing else in the old folders differs from CMR. No new collection (16 at GES DISC: the 14 configured and the 2 complete). Quota is ample. The tiny test data is deleted (my okay, 2026-10-22).

# 6. Download the missing files [IN PROGRESS]
Fetch everything the audit lists into the new collection folders.
- needs my approval before starting, including the route: a monitored login-node run (`./run_download.sh`, as tropomi) or the Casper batch job (`qsub -A "$PBS_ACCOUNT" job_download.pbs`). Show me any job script and its cost first.
- monitor with `./run_download.sh --status`, and rerun if verification reports missing files.
- done when verification reports every listed file present, the right size and readable, and every collection has its manifest.

Run (approved 2026-10-22: login node, detached, so it survives my disconnecting; I first asked for Casper, then a Derecho shared node, then settled on the login node):
- started 2026-10-22 17:32 (MT) on derecho3 with `./run_download.sh` (4 workers, `nice -n 19`, `setsid nohup`, own session 321132, main pid 321148), with `monitor_download.sh` logging to `logs/tropess_download_monitor.log`.
- listing done 17:33: **12,127 files** (one more than the audit: a live v2 collection grew). First minute: ~350 files, no errors.
- `job_download.pbs` is back on Casper (with the venv python called directly). The develop queue was not used: the sibling notes say Derecho compute nodes have no outbound internet, which is unverified for shared nodes.

# 7. Hand back to data_engineering_wiso-sat
Report, as the handoff's "What to hand back" asks:
- each new folder's path, files downloaded vs CMR count, and the cutoff (CMR query time) for the v2 collections;
- the manifest paths;
- failures, gaps, and anything that differs from the handoff table: counts, new collections, dates with two files, replaced granules.

# Later (not scheduled)
- top-up reruns of the live v2 collections (FS v2 keeps growing).
- Derecho scratch is subject to purge. The raw tree may eventually need a copy on campaign storage.

# Remote and local facts (checked 2026-10-22)
- remote: GES DISC, `https://data.gesdisc.earthdata.nasa.gov/data/TROPESS_{Summary,Standard,Full,Reanalysis_Summary,...}/<ShortName>.<version>/<YYYY>/<file>.nc`, one file per day. Earthdata Login with the GES DISC application approved (already done for imerg, 2026-09-29; `~/.netrc` exists).
- the 14 collections to fetch, with CMR concept ids, counts and sizes, are in the handoff's "What to download" table.
- local: `/glade/derecho/scratch/kheyblom/data/wisosat/raw/` (Lustre). Existing folders and entries: `AIRS/TRPSYL2HDOAIRSORS_1-20240806_185241` 6,361; `AIRS/TRPSDL2HDOAIRSFS_1-20240912_174250` 1,426 (1,251 `TROPESS_*.nc`, 173 `*_target.nc`, 2 L3 `AIRS_TROPESS_gridded_*`); `CRIS/TRPSYL2HDOCRSRS_1-20240923_230130` 1,865; `CRIS/TRPSDL2HDOCRS1FS_1-20240912_023602` 1,208. `raw/AIRS` and `raw/CRIS` also hold a `.DS_Store` each; leave them.
- quota: scratch 70.3 of 150 TiB used (2026-10-22).

# General notes:
- the git remote is: https://github.com/kheyblom/access_tropess.git
- ensure to always use proper git version controlling as changes are made
- use and update a CLAUDE.md that will take this information and effectively and efficiently handle this project
- follow the same structure, workflow and rules as the other access pipelines in @/glade/u/home/kheyblom/work/data_access. `access_tropomi` is the most recent; `access_imerg` is the other GES DISC/earthaccess downloader.
- the rules in the handoff hold throughout: never modify, move, rename or delete anything already in the raw tree; never edit a netCDF file; never print, log or commit credentials.
- it is always important to minimize compute costs on derecho. we have a finite allocation and we need to managing our usage.
- keep notes on your current working state. you may lose connection to the HPC system or I may need to start new sessions, so I need you to be able to easily pick up where you left off.
- additional tasks may come up that need to occur between the above tasks. this task list can be flexible, but if substantial changes are need, I need to approve them.
- mark tasks as completed when they are completed
