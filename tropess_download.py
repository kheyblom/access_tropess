"""Download NASA TROPESS AIRS and CrIS L2 HDO files from GES DISC with earthaccess.

TROPESS publishes one collection per product type, instrument, stream and
collection version, e.g. ``TRPSDL2HDOAIRSFS.1`` (Standard, AIRS, Forward
Stream, version 1), and GES DISC lays each one out as
``TROPESS_<type>/<short name>.<version>/<year>/<file>.nc``, one file per day,
e.g. ``TROPESS_Standard/TRPSDL2HDOAIRSFS.1/2026/TROPESS_AIRS-Aqua_L2_Standard_
HDO_20260531_MUSES_R1p24_FS_F0p9_J1.nc``. The files are found through a CMR
search on each collection's concept id rather than by walking that tree, which
also yields each file's exact size and its CMR dates. The local tree gets one
new folder per collection, named for it, under the instrument folder and split
by year as on the server:
``<download>/<instrument>/<short name>.<version>/<year>/<file>``.

Which files are fetched is driven by the config: the list of ``collections``
(each with its ``name``, ``concept_id`` and ``instrument`` folder) and
``date_range`` (``null`` for the whole record). A collection that was partly
downloaded before into an old folder names that folder as ``existing``; a
granule whose exact file name is already there is left out, and the old folder
is only ever listed, never written. earthaccess handles the login and the
search; the files are then streamed over its authenticated HTTPS session by
``n_processes`` worker processes, each with its own session and its own log
file.

CMR publishes no checksums, so each transfer is checked before it is accepted:
its byte count must equal CMR's, and the file must open with h5py, have a
readable root ``time`` dataset, and carry the collection's ``ShortName`` and
``VersionID`` in its root attributes. Downloads are restartable: a file whose
local copy already matches the CMR size is skipped, and one of any other size
is fetched again. Transfers land on a ``.part`` file that is only renamed into
place, over any older copy, once it passes, and stray ``.part`` files are swept
before and after a run and on Ctrl-C. The closing verification pass repeats the
size and content checks on every expected file, so a rerun picks up exactly the
files it lists, and each collection then gets a manifest CSV in its folder. A
``--dry-run`` also audits the old folders against CMR (names CMR no longer
lists, files whose size differs, granules that replace them) and reports
without changing anything.
"""

import logging
import argparse
import csv
import os
import re
import time
import signal
import socket
import multiprocessing
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from multiprocessing import Pool

from utils.path_utils import (
    load_config,
)
from utils.log_utils import (
    setup_logging,
)

import earthaccess
import h5py
import requests

# 'TRPSDL2HDOAIRSFS.1' -> short name 'TRPSDL2HDOAIRSFS', collection version '1';
# this is both how CMR and GES DISC name a collection and the new folder's name
COLLECTION_RE = re.compile(r'^(?P<short_name>TRPS[A-Z0-9]+)\.(?P<version>\d+)$')
# 'TROPESS_AIRS-Aqua_L2_Standard_HDO_20020830_MUSES_R1p6_RS_F0p8_J0.nc',
# 'TROPESS_CrIS-JPSS1_L2_Standard_HDO_20210401_MUSES_R1p12_FS_F0p1.nc' (no joint
# flag in early files), 'TROPESS_CrIS-SNPP_L2_Standard_HDO_20200131_MUSES_R1p9_
# SC_AUS_F0p1.nc' (a special collection). Anchored at '.nc', so the local
# '*_target.nc' copies in an old folder are not taken for granules.
FILENAME_RE = re.compile(
    r'^TROPESS_(?P<platform>[A-Za-z]+-[A-Za-z0-9]+)'
    r'_L2_(?P<type>Summary|Standard|Full)_HDO'
    r'_(?P<date>\d{8})_MUSES'
    r'_(?P<algorithm>R\d+p\d+)'
    r'_(?P<stream>RS|FS|SC_[A-Z]+)'
    r'_(?P<format>F\d+p\d+)'
    r'(?:_(?P<joint>J\d))?'
    r'\.nc$'
)
# '.../TROPESS_Standard/TRPSDL2HDOAIRSFS.1/2026/TROPESS_..._J1.nc'; the year
# directory is kept locally, the collection directory is checked
URL_RE = re.compile(r'/(?P<collection>[^/]+)/(?P<year>\d{4})/(?P<name>[^/]+)$')
# statuses that mean the account may not fetch the file at all, typically an
# unapproved GES DISC application or EULA; retrying cannot fix these
ACCESS_DENIED = (401, 403)
# columns of the per collection manifest; 'cmr_updated' is the provider's
# Update date, 'cmr_revision_date' when CMR last changed the record, and
# 'cmr_query_utc' the time of the listing, i.e. the cutoff for a live collection
MANIFEST_COLUMNS = (
    'file_name', 'bytes', 'cmr_size_mb', 'cmr_updated', 'cmr_revision_date',
    'downloaded_utc', 'url', 'cmr_query_utc',
)
# processName keeps the workers apart in the shared console stream
LOG_FORMAT = '%(asctime)s [%(levelname)s] %(processName)s %(name)s: %(message)s'

LOG = logging.getLogger(__name__)

# per worker HTTPS session and settings, filled in by init_worker; sessions
# cannot be shared between processes, so each worker keeps its own here
_WORKER_STATE = {}


def parse_collection(name):
    """Split a configured collection into its short name and version.

    Args:
        name (str): Collection as written in the config, e.g. 'TRPSDL2HDOAIRSFS.1'.

    Returns:
        tuple: (short_name, version), e.g. ('TRPSDL2HDOAIRSFS', '1').

    Raises:
        ValueError: If the name is not '<short name>.<version>'.
    """
    match = COLLECTION_RE.match(name)
    if match is None:
        raise ValueError(f"cannot parse collection {name!r}, expected e.g. 'TRPSDL2HDOAIRSFS.1'")
    return match.group('short_name'), match.group('version')


def collection_root(settings, collection):
    """The new local folder of one collection.

    Args:
        settings (dict): The loaded configuration.
        collection (dict): One entry of the config's ``collections``.

    Returns:
        str: e.g. '<download>/AIRS/TRPSDL2HDOAIRSFS.1'.
    """
    return os.path.join(
        settings['directories']['download'], collection['instrument'], collection['name']
    )


def existing_root(settings, collection):
    """The old download folder of a partly held collection, if it has one.

    Args:
        settings (dict): The loaded configuration.
        collection (dict): One entry of the config's ``collections``.

    Returns:
        str | None: e.g. '<download>/AIRS/TRPSDL2HDOAIRSFS_1-20240912_174250',
            or None for a collection with nothing on disk before.

    Raises:
        ValueError: If the old folder is, or contains, the new one; the old
            folders must never be written to.
    """
    if not collection.get('existing'):
        return None
    # absolute in the configs, so a test downloading elsewhere still compares
    # against the real old folder; a relative one is taken from the download root
    path = os.path.normpath(
        os.path.join(settings['directories']['download'], collection['existing'])
    )
    new = os.path.normpath(collection_root(settings, collection))
    if new == path or new.startswith(path + os.sep):
        raise ValueError(f'{collection["name"]}: new folder {new} is inside the existing {path}')
    return path


def pid_file(settings):
    """Path of the file recording which host and pid are running the download.

    Args:
        settings (dict): The loaded configuration.

    Returns:
        str: e.g. '<logs>/tropess_download.pid'.
    """
    stem, _ = os.path.splitext(settings['log_file'])
    return os.path.join(settings['directories']['logs'], f'{stem}.pid')


def write_pid_file(settings):
    """Record the host and pid of this run.

    A download started on one login node is invisible from the others -- GLADE
    is shared but process tables are not -- so the host has to be written down
    alongside the pid or a later session cannot find the run to check or stop it.

    Args:
        settings (dict): The loaded configuration.

    Returns:
        str: The path written.
    """
    path = pid_file(settings)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(f'{socket.gethostname()} {os.getpid()}\n')
    return path


def login():
    """Log in to NASA Earthdata with the credentials in ~/.netrc.

    Returns:
        earthaccess.Auth: The authenticated session state.

    Raises:
        RuntimeError: If the login did not succeed.
    """
    auth = earthaccess.login(strategy='netrc')
    if not auth or not auth.authenticated:
        raise RuntimeError('earthdata login failed, check ~/.netrc')
    return auth


def raise_interrupt(signum, frame):
    """Signal handler that turns SIGTERM into a KeyboardInterrupt.

    Args:
        signum (int): The signal received.
        frame (frame): The interrupted stack frame, unused.

    Raises:
        KeyboardInterrupt: Always, so the run cleans up as it would on Ctrl-C.
    """
    raise KeyboardInterrupt(f'received signal {signum}')


def install_signal_handlers():
    """Make SIGINT and SIGTERM stop the run cleanly.

    A job started in the background from a non-interactive shell, which is how
    run_download.sh starts it, inherits SIGINT as ignored, and Python leaves an
    ignored SIGINT alone, so without this ``kill -INT`` does nothing. SIGTERM,
    from a plain ``kill`` or a PBS walltime, would otherwise end the process
    without sweeping its partial files or removing the pid file.
    """
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, raise_interrupt)


def check_access(url):
    """Fetch the first byte of one file to prove the account may download.

    Earthdata login succeeding is not enough: GES DISC also requires its
    application to be approved on the account, and until it is every download
    fails with 403 'EULA Acceptance Failure'. Checking one file up front turns
    that into one clear error instead of a failure per file.

    Args:
        url (str): The HTTPS link of any file in the run.

    Raises:
        RuntimeError: If the server refuses the request, with its explanation
            and, when it gives one, the URL where access can be granted.
    """
    session = earthaccess.get_requests_https_session()
    with session.get(url, headers={'Range': 'bytes=0-0'}, stream=True, timeout=(30, 300)) as response:
        if response.ok:
            return
        try:
            detail = response.json()
        except ValueError:
            detail = {}
    message = detail.get('error_description') or response.reason
    resolution = detail.get('resolution_url')
    raise RuntimeError(
        f'GES DISC refused {url} with {response.status_code} ({message})'
        + (f'; grant access at {resolution}' if resolution else '')
    )


def parse_date_range(date_range):
    """Turn the configured date range into dates.

    Args:
        date_range (list | None): Inclusive ['YYYY-MM-DD', 'YYYY-MM-DD'], or
            None for no limit.

    Returns:
        tuple | None: (first, last) as datetime.date, or None.
    """
    if date_range is None:
        return None
    first, last = (date.fromisoformat(str(value)) for value in date_range)
    return first, last


def granule_size_bytes(granule):
    """Exact size of a granule in bytes, from its CMR metadata.

    GES DISC reports the size in MB meaning MiB, to enough digits that
    rounding recovers the byte count exactly. Anything that cannot be turned
    into bytes is an error rather than a guess, since every skip and
    verification relies on it.

    Args:
        granule (earthaccess.DataGranule): A search result.

    Returns:
        tuple: (bytes, megabytes), the latter as CMR lists it, for the manifest.

    Raises:
        ValueError: If the metadata carries no usable size.
    """
    scale = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3}
    info = granule['umm'].get('DataGranule', {}).get('ArchiveAndDistributionInformation', [])
    total = 0
    for entry in info:
        if 'SizeInBytes' in entry:
            total += int(entry['SizeInBytes'])
        elif 'Size' in entry and entry.get('SizeUnit') in scale:
            total += round(float(entry['Size']) * scale[entry['SizeUnit']])
        else:
            raise ValueError(f'no usable size in {entry}')
    if not total:
        raise ValueError('granule metadata carries no size')
    return total, total / 1024**2


def provider_date(granule, kind):
    """One of the provider's dates for a granule, as CMR lists it.

    Args:
        granule (earthaccess.DataGranule): A search result.
        kind (str): 'Insert', 'Update', 'Create' or 'Delete'.

    Returns:
        str: The ISO timestamp, or '' if CMR lists none.
    """
    for entry in granule['umm'].get('ProviderDates', []):
        if entry.get('Type') == kind:
            return entry.get('Date', '')
    return ''


def search_granules(settings, collection, dates):
    """Search CMR for every granule of one collection in the configured dates.

    Args:
        settings (dict): The loaded configuration.
        collection (dict): One entry of the config's ``collections``.
        dates (tuple | None): (first, last) dates, or None for everything.

    Returns:
        tuple: (granules, query_time), the earthaccess.DataGranule results and
            the UTC time the search started, which is the listing's cutoff.
    """
    query = {'concept_id': collection['concept_id']}
    if dates is not None:
        first, last = dates
        query['temporal'] = (f'{first.isoformat()}T00:00:00', f'{last.isoformat()}T23:59:59')
    LOG.info(f'searching CMR for {collection["name"]}: {query}')
    query_time = datetime.now(timezone.utc)
    return earthaccess.search_data(**query), query_time


def list_collection(settings, collection, dates):
    """List one collection's granules and where each goes locally.

    Args:
        settings (dict): The loaded configuration.
        collection (dict): One entry of the config's ``collections``.
        dates (tuple | None): (first, last) dates, or None for everything.

    Returns:
        dict: The collection's listing: 'collection' (the config entry),
            'root' (the new folder), 'existing' (the old folder or None),
            'query_time', 'granules' (every granule CMR lists in the dates, as
            dicts with url, name, date, algorithm, format, size, size_mb,
            updated, revision_date and local_path), 'held' (the names left out
            because the old folder has them) and 'files' (the granules that
            belong in the new folder).

    Raises:
        ValueError: If CMR returns a granule of another collection, or one
            whose link does not sit under the collection's directory; either
            would file data in the wrong place.
    """
    short_name, version = parse_collection(collection['name'])
    root = collection_root(settings, collection)
    existing = existing_root(settings, collection)
    found, query_time = search_granules(settings, collection, dates)

    granules = []
    for granule in found:
        reference = granule['umm'].get('CollectionReference', {})
        if (reference.get('ShortName'), str(reference.get('Version'))) != (short_name, version):
            raise ValueError(f'{collection["name"]}: CMR returned a granule of {reference}')
        links = granule.data_links()
        if not links:
            LOG.warning(f'skipping {granule["umm"].get("GranuleUR")}: no download link')
            continue
        url = links[0]
        url_match = URL_RE.search(url)
        if url_match is None or url_match.group('collection') != collection['name']:
            raise ValueError(f'{collection["name"]}: unexpected link {url}')
        name = url_match.group('name')
        match = FILENAME_RE.match(name)
        if match is None:
            LOG.warning(f'skipping {url}: cannot parse filename')
            continue
        day = datetime.strptime(match.group('date'), '%Y%m%d').date()
        if dates is not None and not dates[0] <= day <= dates[1]:
            continue
        size, size_mb = granule_size_bytes(granule)
        granules.append({
            'url': url,
            'name': name,
            'date': day,
            'algorithm': match.group('algorithm'),
            'format': match.group('format'),
            'size': size,
            'size_mb': size_mb,
            'updated': provider_date(granule, 'Update'),
            'revision_date': granule['meta'].get('revision-date', ''),
            'local_path': os.path.join(root, url_match.group('year'), name),
        })
    granules.sort(key=lambda item: item['name'])

    # names only, as the handoff asks: a granule the old folder already holds
    # under the same name is not fetched again, whatever its size
    held_names = set(existing_names(existing)) if existing else set()
    return {
        'collection': collection,
        'root': root,
        'existing': existing,
        'query_time': query_time,
        'granules': granules,
        'held': [item for item in granules if item['name'] in held_names],
        'files': [item for item in granules if item['name'] not in held_names],
    }


def existing_names(directory):
    """Names of the TROPESS granules in an old download folder.

    One listing of a flat folder. Only names that parse as granules count, so
    the local '*_target.nc' copies and the L3 'AIRS_TROPESS_gridded_*' files
    that share these folders are ignored.

    Args:
        directory (str): The old folder.

    Returns:
        list: The granule file names, sorted.

    Raises:
        FileNotFoundError: If the folder does not exist, which would otherwise
            silently turn a 'missing only' collection into a full download.
    """
    if not os.path.isdir(directory):
        raise FileNotFoundError(f'existing folder {directory} not found')
    return sorted(name for name in os.listdir(directory) if FILENAME_RE.match(name))


def local_state(files):
    """Split a collection's files by what is on disk, one stat each.

    Args:
        files (list): Granule dicts with 'local_path' and 'size'.

    Returns:
        tuple: (present, missing, wrong_size) lists of granule dicts; wrong
            size entries also carry 'local_size'.
    """
    present, missing, wrong_size = [], [], []
    for item in files:
        try:
            local_size = os.stat(item['local_path']).st_size
        except FileNotFoundError:
            missing.append(item)
            continue
        if local_size == item['size']:
            present.append(item)
        else:
            wrong_size.append({**item, 'local_size': local_size})
    return present, missing, wrong_size


def format_size(n_bytes):
    """Human readable file size.

    Args:
        n_bytes (int): Size in bytes.

    Returns:
        str: The size in MB, or GB once it passes 1024 MB.
    """
    mb = n_bytes / 1024**2
    return f'{mb:.1f} MB' if mb < 1024 else f'{mb / 1024:.2f} GB'


def date_runs(days, limit=60):
    """Write a set of dates compactly, as runs of consecutive days.

    Args:
        days (iterable): datetime.date values.
        limit (int): Most runs to write out before summarising the rest.

    Returns:
        str: e.g. '2023-07-21, 2023-10-19, 2024-09-05..2026-05-31'.
    """
    runs = []
    for day in sorted(set(days)):
        if runs and day - runs[-1][1] == timedelta(days=1):
            runs[-1][1] = day
        else:
            runs.append([day, day])
    text = [
        first.isoformat() if first == last else f'{first.isoformat()}..{last.isoformat()}'
        for first, last in runs
    ]
    if len(text) > limit:
        return ', '.join(text[:limit]) + f', ... and {len(text) - limit} more runs'
    return ', '.join(text) or 'none'


def report_listing(listing, state):
    """Log what CMR lists for one collection and what this run will do with it.

    Args:
        listing (dict): One collection's listing, from list_collection.
        state (tuple): (present, missing, wrong_size), from local_state.
    """
    name = listing['collection']['name']
    granules = listing['granules']
    present, missing, wrong_size = state
    if not granules:
        LOG.warning(f'{name}: CMR lists no granules in the configured dates')
        return
    total = sum(item['size'] for item in granules)
    LOG.info(
        f'{name} ({listing["collection"]["concept_id"]}): {len(granules)} granules '
        f'({format_size(total)}), {min(item["date"] for item in granules)}..'
        f'{max(item["date"] for item in granules)}, '
        f'CMR queried {listing["query_time"].isoformat(timespec="seconds")}'
    )
    versions = Counter((item['algorithm'], item['format']) for item in granules)
    LOG.info(
        f'  {name} algorithm/format: '
        + ', '.join(f'{a} {f} ({n})' for (a, f), n in sorted(versions.items()))
    )
    # the handoff found one file per date in every collection; anything else
    # needs reporting back
    per_date = Counter(item['date'] for item in granules)
    for day, count in sorted(per_date.items()):
        if count > 1:
            names = [item['name'] for item in granules if item['date'] == day]
            LOG.warning(f'  {name}: {count} granules for {day}: {names}')
    if listing['existing']:
        LOG.info(
            f'  {name}: {len(listing["held"])} already in {listing["existing"]}, left out'
        )
    to_fetch = missing + wrong_size
    LOG.info(
        f'  {name} -> {listing["root"]}: {len(listing["files"])} files, '
        f'{len(present)} present, {len(to_fetch)} to fetch '
        f'({format_size(sum(item["size"] for item in to_fetch))}; '
        f'{len(missing)} missing, {len(wrong_size)} wrong size)'
    )
    if to_fetch:
        LOG.info(f'  {name} dates to fetch: {date_runs(item["date"] for item in to_fetch)}')
    for item in wrong_size:
        LOG.warning(
            f'  {name}: {item["local_path"]} is {item["local_size"]} bytes, CMR lists {item["size"]}'
        )


def audit_existing(listing, dates):
    """Compare an old download folder with CMR, and log what differs.

    Nothing is changed. Reports the old folder's granules whose size differs
    from CMR's, and those CMR no longer lists, with the granule that now covers
    the same date, if any (e.g. a later algorithm's reprocessing).

    Args:
        listing (dict): One collection's listing, from list_collection.
        dates (tuple | None): The (first, last) dates searched, so an old file
            outside a small test's dates is not reported as gone from CMR.
    """
    directory = listing['existing']
    if directory is None:
        return
    name = listing['collection']['name']
    by_name = {item['name']: item for item in listing['granules']}
    by_date = defaultdict(list)
    for item in listing['granules']:
        by_date[item['date']].append(item)

    checked, gone, mismatched = 0, 0, 0
    for local_name in existing_names(directory):
        day = datetime.strptime(FILENAME_RE.match(local_name).group('date'), '%Y%m%d').date()
        if dates is not None and not dates[0] <= day <= dates[1]:
            continue
        checked += 1
        item = by_name.get(local_name)
        if item is None:
            gone += 1
            others = [
                f'{other["name"]} (updated {other["updated"]})' for other in by_date.get(day, [])
            ]
            LOG.warning(
                f'  {name}: old {local_name} is not in CMR'
                + (f'; CMR has {", ".join(others)} for that date' if others else '')
            )
            continue
        local_size = os.stat(os.path.join(directory, local_name)).st_size
        if local_size != item['size']:
            mismatched += 1
            LOG.warning(
                f'  {name}: old {local_name} is {local_size} bytes, CMR lists {item["size"]}'
                ' (left as it is)'
            )
    LOG.info(
        f'  {name}: old folder has {checked} granules in the dates searched; {gone} not in CMR, '
        f'{mismatched} of a different size'
    )


def worker_log_file(settings, worker=None):
    """Path of the per-process log file, for the current worker unless one is named.

    Args:
        settings (dict): The loaded configuration.
        worker (str, optional): Worker name to build a path for. Defaults to the
            name of the calling process.

    Returns:
        str: e.g. '<logs>/tropess_download_forkpoolworker-1.log'.
    """
    stem, extension = os.path.splitext(settings['log_file'])
    worker = worker or multiprocessing.current_process().name.lower()
    return os.path.join(settings['directories']['logs'], f'{stem}_{worker}{extension}')


def init_worker(settings):
    """Give each worker process its own log file and Earthdata session.

    Runs once per worker at pool startup, so each worker logs in once and
    reuses its session, and the token it carries, for every file it handles.
    A forked worker inherits the main process's signal handlers, so they are
    put back: the pool stops its workers with SIGTERM, which should simply end
    them, and an interrupt is the main process's to handle.

    Args:
        settings (dict): The loaded configuration.
    """
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    setup_logging(worker_log_file(settings), fmt=LOG_FORMAT)
    login()
    _WORKER_STATE['session'] = earthaccess.get_requests_https_session()
    _WORKER_STATE['retries'] = settings['retries']
    LOG.info('worker ready, logged in to earthdata')


def attribute_value(value):
    """A root attribute as a plain scalar, whatever form h5py returns it in.

    Args:
        value: The attribute, e.g. np.bytes_(b'TRPSDL2HDOAIRSFS') or
            array([1], dtype=int32).

    Returns:
        The single value, decoded to str if it was bytes, or None if empty.
    """
    if getattr(value, 'shape', ()) != ():
        if value.size != 1:
            return None
        value = value.ravel()[0]
    if isinstance(value, bytes):
        value = value.decode()
    return value


def check_file(path, short_name, version):
    """Check that a file is a readable granule of the expected collection.

    CMR gives no checksums, so this stands in for one: the file must open with
    h5py, its root ``time`` dataset must read, and its root ``ShortName`` and
    ``VersionID`` must name the collection it was listed under.

    Args:
        path (str): The file to check.
        short_name (str): The collection's short name, e.g. 'TRPSDL2HDOAIRSFS'.
        version (str): The collection version, e.g. '1'.

    Raises:
        ValueError: If the file opens but is not what it should be.
        OSError: If h5py cannot open or read it.
    """
    with h5py.File(path, 'r') as handle:
        if 'time' not in handle:
            raise ValueError('no root time dataset')
        handle['time'][...]
        found_name = attribute_value(handle.attrs.get('ShortName'))
        found_version = attribute_value(handle.attrs.get('VersionID'))
    if found_name != short_name:
        raise ValueError(f'ShortName is {found_name!r}, expected {short_name!r}')
    try:
        version_ok = int(found_version) == int(version)
    except (TypeError, ValueError):
        version_ok = False
    if not version_ok:
        raise ValueError(f'VersionID is {found_version!r}, expected {version}')


def fetch(url, tmp_path):
    """Stream one URL to a local file over the worker's session.

    Args:
        url (str): The granule's HTTPS link.
        tmp_path (str): Where to write it.

    Returns:
        int: The number of bytes written.

    Raises:
        requests.HTTPError: If the server answered with an error status.
    """
    written = 0
    # connect timeout, then the longest wait allowed between two chunks
    with _WORKER_STATE['session'].get(url, stream=True, timeout=(30, 300)) as response:
        response.raise_for_status()
        with open(tmp_path, 'wb') as handle:
            for chunk in response.iter_content(chunk_size=1024**2):
                handle.write(chunk)
                written += len(chunk)
    return written


def download_file(job):
    """Download a single file, check it, and move it into place.

    Args:
        job (tuple): (index, total, url, local_path, size, short_name, version);
            index and total are only used to tag the log lines.

    Returns:
        bool: True if the file is in place afterwards, False if every attempt
            failed.
    """
    index, total, url, local_path, size, short_name, version = job
    tag = f'[{index}/{total}]'
    name = os.path.basename(local_path)

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    # transfer to a temporary name so an interrupted run never leaves a file
    # that looks complete to the size check
    tmp_path = local_path + '.part'
    attempts = _WORKER_STATE['retries']
    for attempt in range(1, attempts + 1):
        LOG.info(
            f'{tag} starting {url} ({format_size(size)}) -> {local_path}'
            + (f' (attempt {attempt}/{attempts})' if attempt > 1 else '')
        )
        start = time.monotonic()
        try:
            written = fetch(url, tmp_path)
            # a dropped connection can end the stream early without an error
            if written != size:
                raise IOError(f'got {written} bytes, CMR lists {size}')
            check_file(tmp_path, short_name, version)
            os.replace(tmp_path, local_path)
        except Exception as error:
            # keep going with the other files; failures are reported at the end
            LOG.error(f'{tag} failed to download {name}: {error}')
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            response = getattr(error, 'response', None)
            if isinstance(error, requests.HTTPError) and response is not None \
                    and response.status_code in ACCESS_DENIED:
                # a permission problem, not a transient one; a retry only adds load
                return False
            if attempt < attempts:
                time.sleep(2**attempt)
            continue
        elapsed = time.monotonic() - start
        rate = size / 1024**2 / elapsed if elapsed else 0
        LOG.info(
            f'{tag} finished {name} ({format_size(size)}) '
            f'in {elapsed:.1f} s ({rate:.1f} MB/s)'
        )
        return True
    return False


def cleanup_partial_files(directories):
    """Remove leftover *.part files from a run that was killed mid-transfer.

    Only the year folders this run writes to are looked at, one listing each,
    never an old download folder. Note this removes every *.part in them, so
    two runs must not share a download directory.

    Args:
        directories (iterable): Local directories the run downloads into.

    Returns:
        int: Number of files removed.
    """
    removed = 0
    for directory in sorted(directories):
        if not os.path.isdir(directory):
            continue
        for filename in os.listdir(directory):
            if not filename.endswith('.part'):
                continue
            path = os.path.join(directory, filename)
            try:
                os.remove(path)
            except OSError as error:
                LOG.error(f'could not remove partial download {path}: {error}')
                continue
            LOG.warning(f'removed partial download {path}')
            removed += 1
    LOG.info(f'cleaned up {removed} partial downloads')
    return removed


def verify_downloads(listings):
    """Check every expected file is on disk, the CMR size, and readable.

    The content check is the same as each transfer passed before its rename,
    repeated here so the closing verdict covers files from earlier runs too.

    Args:
        listings (list): The collection listings, from list_collection.

    Returns:
        tuple: (ok, good), whether every file passed, and the set of local
            paths that did, for the manifests.
    """
    files = [(listing, item) for listing in listings for item in listing['files']]
    LOG.info(f'verifying {len(files)} files (size, h5py, ShortName, VersionID)')
    missing, incomplete, unreadable = [], [], []
    good = set()
    verified_bytes = 0
    for listing, item in files:
        short_name, version = parse_collection(listing['collection']['name'])
        path = item['local_path']
        if not os.path.exists(path):
            missing.append(item)
            continue
        local_size = os.path.getsize(path)
        if local_size != item['size']:
            incomplete.append((item, local_size))
            continue
        try:
            check_file(path, short_name, version)
        except Exception as error:
            unreadable.append((item, error))
            continue
        good.add(path)
        verified_bytes += item['size']

    # list the problem files first, then the summary line
    for item in missing:
        LOG.error(f'missing: {item["local_path"]} (remote {item["url"]})')
    for item, local_size in incomplete:
        LOG.error(
            f'size mismatch: {item["local_path"]} is {local_size} bytes, '
            f'CMR lists {item["size"]} (remote {item["url"]})'
        )
    for item, error in unreadable:
        # the right size but bad content: only possible if the file changed
        # after it passed this check on download, and a rerun skips it on its
        # size, so it has to be looked at by hand
        LOG.error(f'bad content: {item["local_path"]}: {error}')

    LOG.info(
        f'verified {len(good)}/{len(files)} files ({format_size(verified_bytes)}), '
        f'{len(missing)} missing, {len(incomplete)} incomplete, {len(unreadable)} bad content'
    )
    return not (missing or incomplete or unreadable), good


def write_manifest(listing, good):
    """Write a collection's manifest CSV into its folder.

    One row per file in the new folder that passed verification. It is
    rewritten in full after every run, so the CMR query time is that of the
    listing the files were last verified against. The download time is the
    file's modification time, i.e. when its transfer was renamed into place.

    Args:
        listing (dict): One collection's listing, from list_collection.
        good (set): Local paths that passed verification.

    Returns:
        tuple: (path, rows), the manifest written (None if there were no
            rows) and the number of rows.
    """
    rows = []
    query_time = listing['query_time'].isoformat(timespec='seconds')
    for item in listing['files']:
        if item['local_path'] not in good:
            continue
        stat = os.stat(item['local_path'])
        rows.append({
            'file_name': item['name'],
            'bytes': stat.st_size,
            'cmr_size_mb': repr(item['size_mb']),
            'cmr_updated': item['updated'],
            'cmr_revision_date': item['revision_date'],
            'downloaded_utc': datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                .isoformat(timespec='seconds'),
            'url': item['url'],
            'cmr_query_utc': query_time,
        })
    if not rows:
        return None, 0
    path = os.path.join(listing['root'], f'{listing["collection"]["name"]}_manifest.csv')
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, path)
    return path, len(rows)


def transfer(settings, to_fetch, directories):
    """Download the listed files in a pool of worker processes.

    Args:
        settings (dict): The loaded configuration.
        to_fetch (list): (listing, granule) pairs of the files to download.
        directories (set): Every folder the run writes to, for the sweep of
            partial files.

    Returns:
        list: One bool per file, True if it is in place.
    """
    # number the jobs so each log line says which file of how many it is
    jobs = [
        (index, len(to_fetch), item['url'], item['local_path'], item['size'],
         *parse_collection(listing['collection']['name']))
        for index, (listing, item) in enumerate(to_fetch, start=1)
    ]
    n_processes = min(settings['n_processes'], len(jobs)) or 1
    LOG.info(
        f'starting {n_processes} worker processes, '
        f'each logging to {worker_log_file(settings, worker="<worker>")}'
    )
    try:
        # chunksize=1 hands out one file at a time, so a worker that draws a
        # slow file does not hold up a whole pre-assigned block
        with Pool(processes=n_processes, initializer=init_worker, initargs=(settings,)) as pool:
            results = pool.map(download_file, jobs, chunksize=1)
    except BaseException as error:
        # covers ctrl-c too; the pool has terminated and joined its workers by
        # the time this runs, so nothing is still writing to a .part file
        LOG.error(f'download interrupted ({type(error).__name__}), cleaning up before exiting')
        cleanup_partial_files(directories)
        raise
    cleanup_partial_files(directories)

    for (index, _, url, *_), ok in zip(jobs, results):
        if not ok:
            LOG.error(f'[{index}/{len(jobs)}] did not download: {url}')
    LOG.info(f'downloaded {results.count(True)} files, {results.count(False)} failed')
    return results


def main(settings, dry_run=False):

    install_signal_handlers()
    os.makedirs(settings['directories']['logs'], exist_ok=True)
    log_file = os.path.join(settings['directories']['logs'], settings['log_file'])
    setup_logging(log_file, fmt=LOG_FORMAT)

    names = [collection['name'] for collection in settings['collections']]
    LOG.info(
        f'downloading TROPESS collections {names} '
        f'(dates: {settings["date_range"] or "all"}, unless a collection sets its own) from GES DISC'
    )
    LOG.info(f'running as pid {os.getpid()} on {socket.gethostname()}')
    login()

    # build the full file list up front, one search per collection, so the
    # total volume is known before any transfer starts
    listings = []
    states = {}
    for collection in settings['collections']:
        # a collection may narrow the dates for itself, which lets a small
        # test pick a few days of each collection
        dates = parse_date_range(collection.get('date_range', settings['date_range']))
        listing = list_collection(settings, collection, dates)
        listings.append(listing)
        states[collection['name']] = local_state(listing['files'])
        report_listing(listing, states[collection['name']])
        if dry_run:
            audit_existing(listing, dates)

    expected = [item for listing in listings for item in listing['files']]
    to_fetch = []
    for listing in listings:
        _, missing, wrong_size = states[listing['collection']['name']]
        to_fetch += [(listing, item) for item in missing + wrong_size]
    LOG.info(
        f'{len(expected)} files expected in the new folders '
        f'({format_size(sum(item["size"] for item in expected))}), '
        f'{len(to_fetch)} to fetch ({format_size(sum(item["size"] for _, item in to_fetch))})'
    )

    # one byte of one file, so a missing permission stops the run here, dry
    # run included, rather than failing every file in turn
    probe = to_fetch[0][1] if to_fetch else (expected[0] if expected else None)
    if probe is not None:
        try:
            check_access(probe['url'])
        except RuntimeError as error:
            LOG.error(str(error))
            raise SystemExit(1)
        LOG.info('access check passed')

    if dry_run:
        LOG.info('dry run, nothing downloaded')
        return

    if not expected:
        LOG.warning('no files matched the configuration, nothing to download')
        return

    directories = {os.path.dirname(item['local_path']) for item in expected}
    LOG.info(f'wrote {write_pid_file(settings)}')
    try:
        # clear debris from any previous run before the workers start writing
        cleanup_partial_files(directories)
        if to_fetch:
            transfer(settings, to_fetch, directories)
        else:
            LOG.info('every file is already present, nothing to download')

        # final check against the CMR sizes and the file contents, in the main
        # log; a rerun picks up exactly the files reported missing or incomplete
        ok, good = verify_downloads(listings)
        for listing in listings:
            path, n_rows = write_manifest(listing, good)
            if path:
                LOG.info(
                    f'{listing["collection"]["name"]}: wrote {path} ({n_rows} of '
                    f'{len(listing["granules"])} CMR granules; '
                    f'{len(listing["held"])} in the old folder)'
                )
    finally:
        # a stale pid file would point at a process that is gone, or worse at
        # a pid the system has since reused
        if os.path.exists(pid_file(settings)):
            os.remove(pid_file(settings))
    if not ok:
        LOG.error('download incomplete, rerun to retry the files listed above')
        raise SystemExit(1)

    LOG.info('all files downloaded and verified')
    LOG.info('done :-)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='download nasa tropess airs and cris l2 hdo files from ges disc via earthaccess.'
    )
    parser.add_argument(
        '--config',
        type=str,
        required=True,
        help='Path to YAML configuration file.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='List what would be downloaded, audit the old folders, and exit.',
    )
    args = parser.parse_args()
    settings = load_config(args.config)
    main(settings, dry_run=args.dry_run)
