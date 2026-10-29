"""Application supervisor and fixed-source GitHub updater for Windows and development."""

from contextlib import contextmanager
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid
import zipfile
import tempfile

REPOSITORY = 'CoxkeLamagra/VSM-Toolbox-Win'
from vsm.runtime import FILES, LEGACY_FILES, MAX_RUNTIME_FILES, release_files

BUSY = (
    'queued',
    'checking',
    'downloading',
    'installing',
    'restarting',
    'backing-up',
    'restoring',
    'cleaning',
    'clearing',
)
ROOT = Path(__file__).resolve().parent


@contextmanager
def database(path, **options):
    """Commit/rollback and release file handles before Windows restore/cleanup."""
    connection = sqlite3.connect(path, **options)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def sync_directory(path):
    # Windows does not support opening directories with os.open/fsync.
    # Files are flushed before atomic replacement on both platforms.
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def supervisor_lock(path, blocking=False):
    with path.open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if not lock.tell():
                lock.write(b'\0')
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def directory():
    db = Path(os.environ.get('VSM_DATABASE', ROOT / 'data' / 'vsm.sqlite3'))
    return Path(os.environ.get('VSM_UPDATE_DIR', db.parent / 'updates')).resolve()


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temp.open('x') as handle:
            os.chmod(temp, 0o600)
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def status():
    base = directory()
    result = read_json(base / 'status.json', {})
    try:
        alive = time.time() - (base / 'heartbeat').stat().st_mtime < 20
    except FileNotFoundError:
        alive = False
    result['available'] = os.environ.get('VSM_UPDATER') == '1' and alive
    result.setdefault('state', 'idle')
    result.setdefault('current_revision', os.environ.get('VSM_REVISION', 'local'))
    return result


def request(action, **payload):
    if action not in ('check', 'install', 'backup', 'restore', 'cleanup', 'clear-data'):
        raise ValueError('Unknown update action')
    base = directory()
    base.mkdir(parents=True, exist_ok=True)
    with supervisor_lock(base / 'request.lock', blocking=True):
        current = status()
        if not current['available']:
            raise RuntimeError(
                'The update supervisor is not running. Start or reinstall the Windows service.'
            )
        if current['state'] in BUSY or (base / 'request.json').exists():
            raise RuntimeError('An update operation is already in progress')
        atomic_json(
            base / 'status.json',
            {**current, 'state': 'queued', 'message': 'Update operation queued.'},
        )
        atomic_json(base / 'request.json', {'action': action, **payload})
    return {'queued': True}


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in (
            'api.github.com',
            'github.com',
            'codeload.github.com',
        ):
            raise ValueError('Unexpected update download redirect')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            redirected.remove_header('Authorization')
        return redirected


def download(url, limit):
    opener = urllib.request.build_opener(HTTPSRedirect())
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'VSM-Toolbox-Updater/1', 'Accept': 'application/vnd.github+json'},
    )
    if urllib.parse.urlparse(url).hostname == 'api.github.com':
        token = os.environ.get('VSM_GITHUB_TOKEN')
        if token:
            # Authenticate only the initial API request, never a redirected download.
            req.add_unredirected_header('Authorization', 'Bearer ' + token)
    with opener.open(req, timeout=30) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError('Update download exceeds the size limit')
    return body


def latest_revision():
    # The commit endpoint includes file patches, which can exceed the metadata
    # limit for large changes. A branch ref provides only the pinned object SHA.
    info = json.loads(
        download(f'https://api.github.com/repos/{REPOSITORY}/git/ref/heads/main', 100_000)
    )
    obj = info.get('object') if isinstance(info, dict) else None
    revision = obj.get('sha') if isinstance(obj, dict) else None
    if (
        not isinstance(obj, dict)
        or obj.get('type') != 'commit'
        or not isinstance(revision, str)
        or not re.fullmatch('[0-9a-f]{40}', revision)
    ):
        raise ValueError('GitHub returned an invalid commit revision')
    return revision


def prepare_release(revision, target):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Invalid revision')
    data = download(f'https://api.github.com/repos/{REPOSITORY}/tarball/{revision}', 20_000_000)
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
        unpacked = compressed.read(20_000_001)
    if len(unpacked) > 20_000_000:
        raise ValueError('Unpacked update exceeds the size limit')
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode='r:') as archive:
        manifests = [
            m.name
            for m in archive.getmembers()
            if m.name.endswith('/update-manifest.json') and m.name.count('/') == 1
        ]
        if len(manifests) != 1:
            raise ValueError('Update archive has no unambiguous manifest')
        prefix = manifests[0].rsplit('/', 1)[0] + '/'
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/', prefix):
            raise ValueError('Invalid update archive root')
        member = next(m for m in archive.getmembers() if m.name == manifests[0])
        if not member.isfile() or member.size > 16384:
            raise ValueError('Unsafe update manifest')
        with archive.extractfile(member) as source:
            manifest = json.loads(source.read())
        files = release_files(manifest, allow_legacy=False)
        # Copy only validated, declared regular files. Never extract archive paths.
        for filename in files:
            members = [m for m in archive.getmembers() if m.name == prefix + filename]
            if len(members) != 1 or not members[0].isfile() or members[0].size > 2_000_000:
                raise ValueError('Missing or unsafe update file: ' + filename)
            destination = target / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(members[0]) as source:
                destination.write_bytes(source.read())
    minimum = manifest.get('python_minimum')
    if (
        not isinstance(minimum, list)
        or len(minimum) != 2
        or any(type(n) is not int for n in minimum)
        or tuple(minimum) > sys.version_info[:2]
    ):
        raise ValueError('The update requires a different Python runtime')
    for filename in files:
        if filename.endswith('.py'):
            compile((target / filename).read_bytes(), str(target / filename), 'exec')
    (target / 'REVISION').write_text(revision + '\n')
    return hashlib.sha256(data).hexdigest()


def backup_database(db, backup):
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(backup.parent, 0o700)
    with database(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed; update cancelled')
        with database(backup) as target:
            source.backup(target)
    os.chmod(backup, 0o600)


def restore_database(db, backup):
    # The application must be stopped before restoring, including on crash recovery.
    if not backup.is_file():
        raise RuntimeError('Update backup is missing; refusing to claim recovery')
    replacement = db.with_name(db.name + '.restore')
    shutil.copyfile(backup, replacement)
    os.chmod(replacement, 0o600)
    with replacement.open('r+b') as handle:
        os.fsync(handle.fileno())
    os.replace(replacement, db)
    for suffix in ('-wal', '-shm', '-journal'):
        Path(str(db) + suffix).unlink(missing_ok=True)
    sync_directory(db.parent)


BACKUP_LIMIT = 128 * 1024 * 1024
BACKUP_FILES = (*FILES, 'database.sqlite3')


def backup_path(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(
        r'[A-Za-z0-9_-]+(?:\.vsm-backup\.zip|\.sqlite3)', identifier
    ):
        raise ValueError('Invalid backup identifier')
    path = directory() / 'backups' / identifier
    if path.is_symlink() or not path.is_file():
        raise ValueError('Backup not found')
    return path


def validate_backup(path, destination=None):
    if path.stat().st_size > BACKUP_LIMIT:
        raise ValueError('Backup exceeds 128 MiB')
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_RUNTIME_FILES + 1:
            raise ValueError('Backup contains too many files')
        if any(i.file_size > BACKUP_LIMIT for i in infos):
            raise ValueError('Oversized backup entry')
        if any(
            i.file_size > BACKUP_LIMIT
            or i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            or i.flag_bits & 1
            or (i.external_attr >> 16) & 0o170000 == 0o120000
            for i in infos
        ):
            raise ValueError('Unsupported encrypted, linked or oversized backup entry')
        if archive.getinfo('update-manifest.json').file_size > 16384:
            raise ValueError('Application manifest is too large')
        application_manifest = json.loads(archive.read('update-manifest.json'))
        backup_files = (*release_files(application_manifest), 'database.sqlite3')
        if len(infos) != len(backup_files) + 1 or {i.filename for i in infos} != {
            *backup_files,
            'backup.json',
        }:
            raise ValueError(
                'Backup must contain exactly a supported application profile and database'
            )
        if (
            sum(i.file_size for i in infos) > BACKUP_LIMIT
            or archive.getinfo('backup.json').file_size > 16384
        ):
            raise ValueError('Unpacked backup exceeds its size limit')
        manifest = json.loads(archive.read('backup.json'))
        if (
            not isinstance(manifest, dict)
            or manifest.get('format') != 1
            or not isinstance(manifest.get('sha256'), dict)
            or set(manifest['sha256']) != set(backup_files)
        ):
            raise ValueError('Unsupported backup format')
        if not isinstance(manifest.get('revision'), str) or len(manifest['revision']) > 100:
            raise ValueError('Invalid backup revision')
        for name in backup_files:
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != manifest['sha256'][name]:
                raise ValueError('Backup checksum mismatch: ' + name)
            if name != 'database.sqlite3' and len(content) > 2_000_000:
                raise ValueError('Application file too large')
            if name.endswith('.py'):
                compile(content, name, 'exec')
            if destination:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                os.chmod(target, 0o600)
        minimum = application_manifest.get('python_minimum', [])
        if (
            not isinstance(minimum, list)
            or len(minimum) != 2
            or any(type(n) is not int for n in minimum)
            or tuple(minimum) > sys.version_info[:2]
        ):
            raise ValueError('Backup requires a different Python version')
        return manifest


def validate_backup_database(path):
    with database(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Backup database failed integrity validation')
        expected = {
            'streams': {'id', 'name', 'customer', 'outcome', 'description'},
            'steps': {
                'id',
                'stream_id',
                'name',
                'owner',
                'position',
                'work_minutes',
                'wait_minutes',
            },
            'processes': {'id', 'name', 'owner', 'description', 'procedure'},
            'step_processes': {'step_id', 'process_id'},
            'users': {'id', 'username', 'password_hash', 'role'},
            'sessions': {'token_hash', 'user_id', 'csrf_token', 'expires_at'},
            'login_attempts': {'address', 'failures', 'started_at'},
        }
        for table, columns in expected.items():
            if not columns <= {r[1] for r in conn.execute('PRAGMA table_info(' + table + ')')}:
                raise ValueError('Backup database is missing required schema: ' + table)
        if not conn.execute("SELECT 1 FROM users WHERE role='Admin' LIMIT 1").fetchone():
            raise ValueError('Backup must contain at least one Admin account')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Backup contains broken data relationships')


def create_backup(active, db, reason):
    files = (
        *release_files(read_json(Path(active['path']) / 'update-manifest.json')),
        'database.sqlite3',
    )
    base = directory() / 'backups'
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    identifier = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex + '.vsm-backup.zip'
    final = base / identifier
    with tempfile.TemporaryDirectory(dir=base) as temporary:
        temporary = Path(temporary)
        backup_database(db, temporary / 'database.sqlite3')
        hashes = {}
        package = temporary / 'bundle.zip'
        with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in files:
                source = (
                    temporary / 'database.sqlite3'
                    if name == 'database.sqlite3'
                    else Path(active['path']) / name
                )
                if source.is_symlink() or not source.is_file():
                    raise ValueError('Application backup source missing or linked: ' + name)
                data = source.read_bytes()
                if len(data) > BACKUP_LIMIT:
                    raise ValueError('Backup source exceeds size limit')
                hashes[name] = hashlib.sha256(data).hexdigest()
                archive.writestr(name, data)
            archive.writestr(
                'backup.json',
                json.dumps(
                    {
                        'format': 1,
                        'revision': active['revision'],
                        'created_at': time.time(),
                        'reason': reason,
                        'sha256': hashes,
                    }
                ),
            )
        validate_backup(package)
        os.chmod(package, 0o600)
        with package.open('r+b') as handle:
            os.fsync(handle.fileno())
        os.replace(package, final)
        sync_directory(base)
    return final


def unpack_backup(path):
    target = directory() / 'releases' / ('restore-' + uuid.uuid4().hex)
    target.mkdir(parents=True)
    try:
        metadata = validate_backup(path, target)
        validate_backup_database(target / 'database.sqlite3')
        (target / 'REVISION').write_text(metadata['revision'] + '\n')
        return target, metadata
    except Exception:
        shutil.rmtree(target)
        raise


def list_backups():
    results = []
    pending = read_json(directory() / 'pending.json', {})
    for path in sorted(
        [
            *(directory() / 'backups').glob('*.vsm-backup.zip'),
            *(directory() / 'backups').glob('*.sqlite3'),
        ],
        reverse=True,
    ):
        if path.is_symlink():
            continue
        entry = {
            'id': path.name,
            'size': path.stat().st_size,
            'created_at': path.stat().st_mtime,
            'protected': pending.get('backup') == str(path),
            'restorable': path.name.endswith('.vsm-backup.zip'),
        }
        if path.suffix == '.sqlite3':
            entry['reason'] = 'Legacy database-only backup'
            results.append(entry)
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.getinfo('backup.json').file_size > 16384:
                    raise ValueError('Oversized metadata')
                metadata = json.loads(archive.read('backup.json'))
                if not isinstance(metadata, dict):
                    raise ValueError('Invalid metadata')
            entry.update({k: metadata.get(k) for k in ('revision', 'reason', 'created_at')})
        except (
            ValueError,
            OSError,
            KeyError,
            zipfile.BadZipFile,
            NotImplementedError,
            RuntimeError,
        ):
            entry['reason'] = 'Invalid backup (can be removed)'
            entry['restorable'] = False
        results.append(entry)
    return results


def delete_backups(identifiers):
    if (
        not isinstance(identifiers, list)
        or not identifiers
        or any(not isinstance(i, str) for i in identifiers)
    ):
        raise ValueError('Select backup identifiers to remove')
    paths = [backup_path(i) for i in set(identifiers)]
    pending = read_json(directory() / 'pending.json', {})
    if any(str(p) == pending.get('backup') for p in paths):
        raise ValueError('Cannot delete a backup required for recovery')
    for path in paths:
        path.unlink()
    return len(paths)


def clear_business_data(db):
    """Clear business records and their links, retaining accounts and settings."""
    with database(db) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        counts = {
            table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
            for table in ('streams', 'steps', 'processes', 'pcf_entries')
        }
        conn.execute('DELETE FROM streams')
        conn.execute('DELETE FROM processes')
        # Parent deletion is RESTRICTed: remove PCF leaves before their parents.
        for kind in ('Task', 'Activity', 'Process', 'Process Group', 'Category'):
            conn.execute('DELETE FROM pcf_entries WHERE entry_type=?', (kind,))
        return counts


class Supervisor:
    def __init__(self):
        self.base = directory()
        self.base.mkdir(parents=True, exist_ok=True)
        self.db = Path(os.environ.get('VSM_DATABASE', ROOT / 'data' / 'vsm.sqlite3')).resolve()
        revision_file = ROOT / 'REVISION'
        initial_revision = (
            revision_file.read_text().strip()
            if revision_file.exists()
            else os.environ.get('VSM_REVISION', 'local')
        )
        self.initial = {'path': str(ROOT), 'revision': initial_revision}
        self.active = read_json(self.base / 'active.json', self.initial)
        self.child = None
        self.output = None
        self.python_executable = sys.executable
        self.stopping = threading.Event()

    def report(self, state, message, **extra):
        previous = read_json(self.base / 'status.json', {})
        atomic_json(
            self.base / 'status.json',
            {
                **previous,
                'state': state,
                'message': message,
                'current_revision': self.active['revision'],
                'backup_protocol': 1,
                'data_reset_protocol': 1,
                'updated_at': time.time(),
                **extra,
            },
        )

    def start(self):
        environment = {
            **os.environ,
            'VSM_UPDATER': '1',
            'VSM_BACKUP_PROTOCOL': '1',
            'VSM_DATA_RESET_PROTOCOL': '1',
            'VSM_UPDATE_DIR': str(self.base),
            'VSM_DATABASE': str(self.db),
            'VSM_REVISION': self.active['revision'],
        }
        self.child = subprocess.Popen(
            [self.python_executable, str(Path(self.active['path']) / 'app.py')],
            cwd=self.active['path'],
            env=environment,
            stdout=self.output,
            stderr=subprocess.STDOUT if self.output is not None else None,
        )

    def stop(self):
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
        self.child = None

    def healthy(self, timeout=30):
        host = os.environ.get('VSM_HOST', '127.0.0.1')
        if host in ('0.0.0.0', ''):
            host = '127.0.0.1'
        url = f'http://{host}:{os.environ.get("VSM_PORT", "8080")}/api/health'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            manifest = read_json(Path(self.active['path']) / 'update-manifest.json')
            release_files(manifest)
            protocol = manifest['update_protocol']
        except (OSError, ValueError, TypeError):
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stopping.is_set():
            if self.child.poll() is not None:
                return False
            try:
                with opener.open(url, timeout=2) as response:
                    data = json.load(response)
                if (
                    data.get('status') == 'ok'
                    and data.get('update_protocol') == protocol
                    and data.get('revision') == self.active['revision']
                ):
                    return True
            except (OSError, ValueError):
                pass
            self.stopping.wait(0.3)
        return False

    def recover(self):
        pending = read_json(self.base / 'pending.json')
        if pending:
            if pending.get('bundle'):
                target, metadata = unpack_backup(Path(pending['backup']))
                restore_database(self.db, target / 'database.sqlite3')
                (target / 'database.sqlite3').unlink()
                self.active = {'path': str(target), 'revision': metadata['revision']}
            else:
                restore_database(self.db, Path(pending['backup']))
                self.active = pending['previous']
            atomic_json(self.base / 'active.json', self.active)
            (self.base / 'pending.json').unlink()
            self.report(
                'failed', 'Interrupted update recovered. Previous code and database restored.'
            )

    def update(self, action):
        if action not in ('check', 'install'):
            raise ValueError('Unknown update action')
        previous = dict(self.active)
        target = None
        pending = False
        committed = False
        try:
            self.report('checking', 'Checking the latest GitHub main revision…')
            revision = latest_revision()
            if action == 'check':
                self.report(
                    'idle',
                    (
                        'You are up to date.'
                        if revision == self.active['revision']
                        else 'A different main revision is available.'
                    ),
                    latest_revision=revision,
                )
                return
            if revision == self.active['revision']:
                self.report(
                    'idle',
                    'You are already running the latest main revision.',
                    latest_revision=revision,
                )
                return
            self.report(
                'downloading', 'Downloading and validating the update…', latest_revision=revision
            )
            target = self.base / 'releases' / (revision + '-' + uuid.uuid4().hex[:8])
            target.mkdir(parents=True)
            digest = prepare_release(revision, target)
            if self.stopping.is_set():
                raise RuntimeError('Update interrupted by service shutdown')
            self.report(
                'installing', 'Backing up application files and database before switching code…'
            )
            self.stop()
            backup = create_backup(previous, self.db, 'pre-update')
            atomic_json(
                self.base / 'pending.json',
                {'previous': previous, 'backup': str(backup), 'bundle': True},
            )
            pending = True
            self.active = {'path': str(target), 'revision': revision}
            self.report(
                'restarting',
                'Starting the updated application and checking health…',
                backup=str(backup),
            )
            self.start()
            if not self.healthy():
                raise RuntimeError('Updated application failed its health check')
            # Existing sessions must not survive code or schema changes.
            with database(self.db) as conn:
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('DELETE FROM sessions')
            atomic_json(self.base / 'active.json', self.active)
            (self.base / 'pending.json').unlink()
            pending = False
            committed = True
            self.report(
                'succeeded', 'Update complete. Sign in again to continue.', archive_sha256=digest
            )
        except Exception as exc:
            if committed:
                raise
            if pending:
                self.stop()
            self.active = previous
            if pending:
                self.recover()
            if self.child is None and not self.stopping.is_set():
                self.start()
            if target and target.exists():
                shutil.rmtree(target)
            self.report('failed', 'Update failed: ' + str(exc) + '. Previous application retained.')

    def maintenance(self, operation):
        action = operation.get('action')
        if action in ('check', 'install'):
            return self.update(action)
        safety = None
        target = None
        pending = False
        committed = False
        try:
            if action == 'cleanup':
                self.report('cleaning', 'Removing selected backups…')
                count = delete_backups(operation.get('ids'))
                self.report('succeeded', f'Removed {count} backup(s).')
                return
            if action == 'backup':
                self.report('backing-up', 'Creating an application and database backup…')
                # SQLite's online backup API captures a consistent snapshot while
                # the web process remains available. Runtime code is immutable;
                # the supervisor serializes maintenance operations.
                safety = create_backup(self.active, self.db, 'manual')
                self.report(
                    'succeeded', 'Application and database backup created.', backup=str(safety)
                )
                return
            if action == 'clear-data':
                self.report(
                    'clearing', 'Creating a full safety backup before clearing business data…'
                )
                self.stop()
                safety = create_backup(self.active, self.db, 'pre-data-clear')
                atomic_json(
                    self.base / 'pending.json',
                    {'previous': dict(self.active), 'backup': str(safety), 'bundle': True},
                )
                pending = True
                counts = clear_business_data(self.db)
                self.report(
                    'restarting',
                    'Starting the application after clearing business data…',
                    backup=str(safety),
                )
                self.start()
                if not self.healthy():
                    raise RuntimeError('Application failed its health check after clearing data')
                (self.base / 'pending.json').unlink()
                pending = False
                committed = True
                self.report(
                    'succeeded',
                    'Value streams, delivery steps, processes, and PCF entries cleared. Accounts, branding, tags, and backups were retained.',
                    backup=str(safety),
                    cleared=counts,
                )
                return
            if action != 'restore':
                raise ValueError('Unknown maintenance operation')
            # Validate everything before stopping the running application.
            source = backup_path(operation.get('id'))
            target, metadata = unpack_backup(source)
            self.report('restoring', 'Creating a safety backup before restoring…')
            self.stop()
            safety = create_backup(self.active, self.db, 'pre-restore')
            atomic_json(
                self.base / 'pending.json',
                {'previous': dict(self.active), 'backup': str(safety), 'bundle': True},
            )
            pending = True
            restore_database(self.db, target / 'database.sqlite3')
            (target / 'database.sqlite3').unlink()
            self.active = {'path': str(target), 'revision': metadata['revision']}
            with database(self.db) as conn:
                conn.execute('PRAGMA foreign_keys=ON')
                conn.execute('DELETE FROM sessions')
            self.report('restarting', 'Starting the restored application…', backup=str(safety))
            self.start()
            if not self.healthy():
                raise RuntimeError('Restored application failed its health check')
            atomic_json(self.base / 'active.json', self.active)
            (self.base / 'pending.json').unlink()
            pending = False
            committed = True
            self.report('succeeded', 'Backup restored. Sign in using an account from the backup.')
        except Exception as exc:
            if committed:
                raise
            if pending:
                self.stop()
                self.recover()
            if self.child is None:
                self.start()
            if target and str(target) != self.active['path']:
                shutil.rmtree(target, ignore_errors=True)
            self.report('failed', 'Backup operation failed: ' + str(exc))

    def heartbeat(self):
        while not self.stopping.is_set():
            (self.base / 'heartbeat').touch()
            self.stopping.wait(2)

    def run(self):
        with supervisor_lock(self.base / 'supervisor.lock'):
            self.recover()
            # Requests left by a stopped instance are never silently applied on startup.
            (self.base / 'request.json').unlink(missing_ok=True)
            self.report('idle', 'Ready to check for updates.')
            threading.Thread(target=self.heartbeat, daemon=True).start()
            self.start()
            try:
                while not self.stopping.wait(0.5):
                    if self.child.poll() is not None:
                        raise RuntimeError('Application exited unexpectedly')
                    request_file = self.base / 'request.json'
                    if request_file.exists():
                        operation = read_json(request_file)
                        request_file.unlink()
                        self.maintenance(operation)
            finally:
                self.stop()
                (self.base / 'heartbeat').unlink(missing_ok=True)


if __name__ == '__main__':
    os.umask(0o077)
    supervisor = Supervisor()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: supervisor.stopping.set())
    supervisor.run()
