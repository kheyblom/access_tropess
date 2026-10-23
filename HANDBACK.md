# Hand-back: TROPESS AIRS and CrIS HDO download (2026-10-22)

From `access_tropess` to the `data_engineering_wiso-sat` session, which updates
`docs/products/tropess.md` and `STATE.md` from it. The brief was
`data_engineering_wiso-sat/docs/handoff-tropess-download.md`.

**Done.** Every granule of the 14 collections that was not already on disk is
downloaded and verified: **12,127 files, 237.01 GB, 0 failures**. The run lasted
2026-10-22 17:32–18:49 MT (23:32–00:49 UTC), on login node derecho3 with 4
connections. One transient 502 was recovered on retry. Nothing in the four old
folders was touched: their entry counts are unchanged (6,361 / 1,426 / 1,865 /
1,208) and nothing in them is newer than 2026-09-16.

## New folders

Raw root `/glade/derecho/scratch/kheyblom/data/wisosat/raw/`. Each folder is
`<instrument>/<ShortName.version>/<YYYY>/`, with the manifest
`<ShortName.version>_manifest.csv` at the top of the collection folder (next to
the year folders, so a `*/TROPESS_*.nc` glob over the year folders does not see it).

| folder | downloaded | CMR granules | dates | cutoff (CMR query, UTC) |
| --- | --- | --- | --- | --- |
| `AIRS/TRPSDL2HDOAIRSORS.1` | 6,362 | 6,362 | 2002-08-30..2020-03-30 | 2026-10-22T23:32:57 |
| `AIRS/TRPSYL2HDOAIRSFS.1` | 1,814 | 1,814 | 2021-02-01..2026-05-31 | 2026-10-22T23:33:09 |
| `AIRS/TRPSYL2HDOAIRSFS.2` | 30 | 30 | 2026-06-01..06-30 | **2026-10-22T23:33:11** |
| `AIRS/TRPSDL2HDOAIRSFS.1` | 564 | 1,815 (1,251 in the old folder) | 2021-02-01..2026-05-31 | 2026-10-22T23:33:12 |
| `AIRS/TRPSDL2HDOAIRSFS.2` | 30 | 30 | 2026-06-01..06-30 | **2026-10-22T23:33:14** |
| `AIRS/TRPSFL2HDOAIRSFS.2` | 30 | 30 | 2026-06-01..06-30 | **2026-10-22T23:33:16** |
| `CRIS/TRPSYL2HDOCRSFS.1` | 110 | 110 | 2021-02-01..05-21 | 2026-10-22T23:33:20 |
| `CRIS/TRPSDL2HDOCRSFS.1` | 110 | 110 | 2021-02-01..05-21 | 2026-10-22T23:33:21 |
| `CRIS/TRPSDL2HDOCRSAUS.1` | 91 | 91 | 2019-11-01..2020-01-31 | 2026-10-22T23:33:24 |
| `CRIS/TRPSYL2HDOCRS1FS.1` | 1,887 | 1,887 | 2021-04-01..2026-05-31 | 2026-10-22T23:33:25 |
| `CRIS/TRPSYL2HDOCRS1FS.2` | 139 | 139 | 2026-06-01..10-17 | **2026-10-22T23:33:28** |
| `CRIS/TRPSDL2HDOCRS1FS.1` | 684 | 1,887 (1,203 in the old folder) | 2021-04-01..2026-05-31 | 2026-10-22T23:33:30 |
| `CRIS/TRPSDL2HDOCRS1FS.2` | 139 | 139 | 2026-06-01..10-17 | **2026-10-22T23:33:34** |
| `CRIS/TRPSFL2HDOCRS1FS.2` | 137 | 137 | 2026-06-01..10-15 | **2026-10-22T23:33:35** |

The v2 collections (bold cutoffs) are live. Their data runs to the last date in
the table.

**Manifest columns:** `file_name, bytes, cmr_size_mb, cmr_updated` (CMR
`ProviderDates` Update)`, cmr_revision_date` (CMR `meta.revision-date`)`,
downloaded_utc` (the file's mtime)`, url, cmr_query_utc`. There is one row per file
in the new folder. For the two partly held collections, the manifest lists only the
files in the new folder, not the old folder's.

**Integrity**, checked on every file, at download and again in a final pass:
- the byte count equals CMR's size exactly (CMR `Size` is MiB to full precision);
- the file opens with h5py and its root `time` reads;
- the root `ShortName` and `VersionID` match the collection (v2 files carry
  `VersionID` 2).

CMR gives no checksums.

## Differences from the handoff table

- **Counts:** all as in the handoff. The live v2 collections stand at:
  CrIS-JPSS1 FS Summary v2 **139** (handoff 138+), FS Standard v2 **139**
  (138+), FS Full v2 137, and AIRS FS v2 30 each.
- **AIRS v2 ends at 2026-06-30** in all three types, while CrIS-JPSS1 v2 runs to
  2026-10-15..17. AIRS FS v2 has not advanced since June.
- **New collections:** none. CMR lists 16 TROPESS HDO collections at GES DISC:
  the 14 above plus the 2 complete on disk (`TRPSYL2HDOAIRSORS.1`
  C2756762233-GES_DISC, `TRPSYL2HDOCRSRS.1` C2756345387-GES_DISC).
- **Dates with two files:** none. Every collection has one granule per date.
- **The old folders against CMR:** every old `TROPESS_*.nc` that CMR still lists
  matches it by name and size (AIRS FS Standard 1,251/1,251; CrIS-JPSS1 FS Standard
  1,203/1,208). Only the 5 below are no longer in CMR.

### Replaced granules (CrIS-JPSS1 FS Standard v1, `tropess.md` R18)

All 5 new files are R1p24 F0p9 J0. Each is in the new folder, and the old file it
replaces is still in `CRIS/TRPSDL2HDOCRS1FS_1-20240912_023602/`.

| date | old file (kept, no longer in CMR) | new file | CMR updated |
| --- | --- | --- | --- |
| 2021-05-21 | `..._20210521_MUSES_R1p12_FS_F0p1.nc` | `..._20210521_MUSES_R1p24_FS_F0p9_J0.nc` | 2025-09-11 |
| 2022-03-08 | `..._20220308_MUSES_R1p15_FS_F0p3.nc` | `..._20220308_MUSES_R1p24_FS_F0p9_J0.nc` | 2025-09-11 |
| 2022-08-13 | `..._20220813_MUSES_R1p17_FS_F0p4.nc` | `..._20220813_MUSES_R1p24_FS_F0p9_J0.nc` | 2025-09-10 |
| 2023-12-27 | `..._20231227_MUSES_R1p22_FS_F0p9_J0.nc` | `..._20231227_MUSES_R1p24_FS_F0p9_J0.nc` | 2025-09-09 |
| 2024-08-21 | `..._20240821_MUSES_R1p23_FS_F0p9_J0.nc` | `..._20240821_MUSES_R1p24_FS_F0p9_J0.nc` | 2025-09-04 |

(`...` = `TROPESS_CrIS-JPSS1_L2_Standard_HDO`.)

### The "missing only" contents, against the handoff's expectations

- **AIRS FS Standard v1, 564 files:** 2023-07-21, 2023-10-19, and 562 days
  2024-09-05..2026-05-31. That span has 634 days. CMR lists no file for the
  other 72, in 18 runs (e.g. 2024-09-17..19, 2024-10-04..19), so they are
  upstream gaps, not download gaps. FS Summary v1 lacks nearly the same days.
  Over the whole v1 record, 5 days are only in Summary (2022-06-19, 2022-11-06,
  2024-04-03, 2024-11-11, 2024-12-03) and 6 only in Standard (2021-02-12,
  2024-02-07, 2024-08-05, 2025-01-12, 2025-01-24, 2025-03-05).
- **CrIS-JPSS1 FS Standard v1, 684 files:** the 5 replacements above, 46 dates
  inside the old window (2022-05-29..2024-09-01; R19 said 46), and all 633 days
  2024-09-06..2026-05-31 (no upstream gaps).

## Failures and gaps

- No failures remain. One 502 (AIRS RS Standard 2019-02-26) was recovered on retry.
- No download gaps: every granule CMR lists in these collections is on disk. AIRS
  still has no data 2020-04..2021-01 in any collection (R1).

## Re-running

`access_tropess` re-runs as a top-up: `./run_download.sh` from that repo fetches
only granules not on disk (for example, new v2 days) and rewrites the manifests
with the new cutoff.
