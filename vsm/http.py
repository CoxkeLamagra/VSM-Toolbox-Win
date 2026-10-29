"""Http responsibilities for VSM Toolbox."""

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import updater
from .auth import ApiError, public_user, validate_account, password_hash, password_matches
from .config import ROOT, ROLES, SESSION_SECONDS, PASSWORD_ITERATIONS, APP_VERSION, UPDATE_PROTOCOL
from .branding import branding, validate_branding, branding_stylesheet, BRANDING_DEFAULTS
from .storage import connect
from .repository import ContentRepository
from .pcf import PCFImporter
from .resources import RESOURCES
from .environments import EnvironmentRepository
from .runtime import STATIC_ASSETS

ADMIN_READ_PATHS = ('/api/roles', '/api/users', '/api/updates', '/api/backups', '/api/maintenance')


CONTENT_SECURITY_POLICY = "default-src 'self'; style-src 'self'; img-src 'self' data:; script-src 'self'; frame-ancestors 'none'"


WRITES_PAUSED = ('installing', 'restarting', 'backing-up', 'restoring', 'clearing')


class BoundedHTTPServer(ThreadingHTTPServer):
    """Bound worker count, idle reads, and total connection lifetime."""

    max_workers = 32
    idle_timeout = 15
    request_deadline = 120
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.workers = threading.BoundedSemaphore(self.max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(self.idle_timeout)
            super().process_request(request, client_address)
        except BaseException:
            self.workers.release()
            raise

    def process_request_thread(self, request, client_address):
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        deadline = threading.Timer(self.request_deadline, expire)
        deadline.daemon = True
        try:
            deadline.start()
            super().process_request_thread(request, client_address)
        finally:
            deadline.cancel()
            self.workers.release()


class Handler(BaseHTTPRequestHandler):
    def environment_repository(self, conn, user):
        token_hash = hashlib.sha256(self.session_token().encode()).hexdigest()
        return EnvironmentRepository(conn, user, token_hash)

    def response_headers(self, status, length, content_type, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def respond(self, status, data, cookie=None):
        payload = json.dumps(data, allow_nan=False).encode()
        headers = {'Cache-Control': 'no-store'}
        if cookie:
            headers['Set-Cookie'] = cookie
        if status == 429:
            headers['Retry-After'] = '300'
        self.response_headers(status, len(payload), 'application/json; charset=utf-8', headers)
        self.wfile.write(payload)

    def session_token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie.get('vsm_session')
            return token.value if token and re.fullmatch(r'[A-Za-z0-9_-]{43}', token.value) else ''
        except CookieError:
            return ''

    def session(self, conn):
        token = hashlib.sha256(self.session_token().encode()).hexdigest()
        return conn.execute(
            'SELECT users.id, username, role, csrf_token FROM sessions '
            'JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires_at>?',
            (token, time.time()),
        ).fetchone()

    def require_user(self, conn, roles=ROLES, csrf=False):
        user = self.session(conn)
        if not user:
            raise ApiError(401, 'Sign in to continue')
        if user['role'] not in roles:
            raise ApiError(403, 'Your role does not allow this action')
        if csrf and not hmac.compare_digest(
            self.headers.get('X-CSRF-Token', '').encode(), user['csrf_token'].encode()
        ):
            raise ApiError(403, 'Missing or invalid CSRF token')
        return user

    def selected_environment(self, conn, user=None):
        return self.environment_repository(conn, user or self.session(conn)).selected()

    def require_environment(self, conn, roles=ROLES, csrf=False):
        user = self.require_user(conn, csrf=csrf)
        environment = self.selected_environment(conn, user)
        if environment is None:
            raise ApiError(409, 'Create or join an environment first')
        requested = self.headers.get('X-Environment-ID')
        if requested is not None and requested != str(environment['id']):
            raise ApiError(409, 'The active environment changed. Refresh before continuing.')
        if environment['role'] not in roles:
            raise ApiError(403, 'Your environment role does not allow this action')
        return environment

    def environment_member(self, conn, ident, roles=ROLES):
        return self.environment_repository(conn, self.require_user(conn)).member(ident, roles)

    def environment_overview(self, conn):
        return self.environment_repository(conn, self.require_user(conn)).overview()

    def environment_members(self, conn, ident):
        return self.environment_repository(conn, self.require_user(conn)).members(ident)

    def environment_mutation(self, conn, parts, method, data):
        return self.environment_repository(conn, self.require_user(conn, csrf=True)).mutate(
            parts, method, data
        )

    def session_cookie(self, token, expires=False):
        secure = '; Secure' if os.environ.get('VSM_COOKIE_SECURE') == '1' else ''
        return f'vsm_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if expires else SESSION_SECONDS}{secure}'

    def create_session(self, conn, user):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        conn.execute(
            'DELETE FROM sessions WHERE expires_at<=? OR token_hash=?',
            (time.time(), hashlib.sha256(self.session_token().encode()).hexdigest()),
        )
        conn.execute(
            'INSERT INTO sessions VALUES (?,?,?,?)',
            (
                hashlib.sha256(token.encode()).hexdigest(),
                user['id'],
                csrf,
                time.time() + SESSION_SECONDS,
            ),
        )
        first = conn.execute(
            'SELECT environment_id FROM environment_members WHERE user_id=? ORDER BY environment_id LIMIT 1',
            (user['id'],),
        ).fetchone()
        conn.execute(
            'INSERT INTO session_environments VALUES (?,?)',
            (
                hashlib.sha256(token.encode()).hexdigest(),
                first['environment_id'] if first else None,
            ),
        )
        return {
            'user': public_user(user),
            'csrf_token': csrf,
            'setup_required': False,
        }, self.session_cookie(token)

    def auth_mutation(self, conn, path, method, data):
        if path == '/api/auth/register' and method == 'POST':
            if conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise ApiError(
                    403, 'Registration is closed. An Admin must create additional accounts.'
                )
            if set(data) != {'username', 'password'}:
                raise ValueError('Username and password are required')
            validate_account(data, creating=True)
            cursor = conn.execute(
                'INSERT INTO users (username,password_hash,role) VALUES (?,?,?)',
                (data['username'], password_hash(data['password']), 'Admin'),
            )
            conn.execute(
                'INSERT INTO environment_members(environment_id,user_id) SELECT id,? FROM environments WHERE id=1',
                (cursor.lastrowid,),
            )
            conn.execute(
                'UPDATE environments SET created_by=? WHERE id=1 AND created_by IS NULL',
                (cursor.lastrowid,),
            )
            user = conn.execute('SELECT * FROM users WHERE id=?', (cursor.lastrowid,)).fetchone()
            result, cookie = self.create_session(conn, user)
            return 201, result, cookie
        if path == '/api/auth/login' and method == 'POST':
            if (
                set(data) != {'username', 'password'}
                or not isinstance(data.get('username'), str)
                or not isinstance(data.get('password'), str)
            ):
                raise ValueError('Username and password are required')
            if len(data['username']) > 64 or len(data['password']) > 128:
                raise ValueError('Invalid credentials')
            now = time.time()
            # SQLite NOCASE folds ASCII only; account names are ASCII on creation.
            username = data['username'].translate(
                str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
            )
            source = self.client_address[0]
            address = 'account:' + hashlib.sha256((source + '\0' + username).encode()).hexdigest()
            source_key = 'source:' + hashlib.sha256(source.encode()).hexdigest()
            conn.execute('DELETE FROM login_attempts WHERE started_at<=?', (now - 300,))
            attempt = conn.execute(
                'SELECT failures FROM login_attempts WHERE address=?', (address,)
            ).fetchone()
            if attempt and attempt['failures'] >= 10:
                return (
                    429,
                    {'error': 'Too many failed sign-in attempts. Try again in five minutes.'},
                    None,
                )
            user = conn.execute(
                'SELECT * FROM users WHERE username=?', (data['username'],)
            ).fetchone()
            # The dummy hash keeps unknown usernames on the same password-check path.
            stored = (
                user['password_hash']
                if user
                else f'pbkdf2_sha256${PASSWORD_ITERATIONS}$' + '00' * 16 + '$' + '00' * 32
            )
            valid = password_matches(data['password'], stored)
            if not user or not valid:
                # The broad budget suppresses failed guesses, never valid logins
                # for unrelated accounts sharing a proxy. Keep both budgets bounded.
                source_attempt = conn.execute(
                    'SELECT failures FROM login_attempts WHERE address=?', (source_key,)
                ).fetchone()
                conn.execute(
                    'INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(address) DO UPDATE SET failures=failures+1',
                    (source_key, now),
                )
                conn.execute(
                    'INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(address) DO UPDATE SET failures=failures+1',
                    (address, now),
                )
                conn.execute(
                    'DELETE FROM login_attempts WHERE address IN (SELECT address FROM login_attempts ORDER BY started_at,address LIMIT MAX(0,(SELECT count(*) FROM login_attempts)-10000))'
                )
                if source_attempt and source_attempt['failures'] >= 100:
                    return (
                        429,
                        {'error': 'Too many failed sign-in attempts. Try again in five minutes.'},
                        None,
                    )
                return 401, {'error': 'Invalid username or password'}, None
            conn.execute('DELETE FROM login_attempts WHERE address=?', (address,))
            result, cookie = self.create_session(conn, user)
            return 200, result, cookie
        if path == '/api/auth/logout' and method == 'POST':
            self.require_user(conn, csrf=True)
            conn.execute(
                'DELETE FROM sessions WHERE token_hash=?',
                (hashlib.sha256(self.session_token().encode()).hexdigest(),),
            )
            return 200, {'signed_out': True}, self.session_cookie('', expires=True)
        raise ApiError(404, 'Not found')

    def account_mutation(self, conn, parts, method, data):
        self.require_user(conn, ('Admin',), csrf=True)
        if len(parts) not in (2, 3) or len(parts) == 3 and not parts[2].isdigit():
            raise ApiError(404, 'Not found')
        ident = int(parts[2]) if len(parts) == 3 else None
        if (method == 'POST') != (ident is None):
            raise ApiError(405, 'Unsupported method for this resource')
        current = (
            conn.execute('SELECT * FROM users WHERE id=?', (ident,)).fetchone()
            if ident is not None
            else None
        )
        if ident is not None and not current:
            raise ApiError(404, 'Account not found')
        if method != 'DELETE':
            validate_account(data, creating=method == 'POST')
            if (
                'username' in data
                and conn.execute(
                    'SELECT id FROM users WHERE username=? AND id!=?',
                    (data['username'], ident or -1),
                ).fetchone()
            ):
                raise ApiError(409, 'Username is already in use')
        if (
            current
            and current['role'] == 'Admin'
            and (method == 'DELETE' or data.get('role', 'Admin') != 'Admin')
        ):
            if conn.execute("SELECT count(*) FROM users WHERE role='Admin'").fetchone()[0] <= 1:
                raise ApiError(409, 'The last Admin cannot be removed or demoted')
        if current and (method == 'DELETE' or data.get('role', current['role']) != 'Admin'):
            memberships = conn.execute(
                "SELECT m.environment_id,m.role FROM environment_members m JOIN users u ON u.id=m.user_id WHERE m.user_id=? AND COALESCE(m.role,u.role)='Admin'",
                (ident,),
            )
            for member in memberships:
                if method != 'DELETE' and member['role'] is not None:
                    continue
                others = conn.execute(
                    "SELECT count(*) FROM environment_members m JOIN users u ON u.id=m.user_id WHERE m.environment_id=? AND m.user_id!=? AND COALESCE(m.role,u.role)='Admin'",
                    (member['environment_id'], ident),
                ).fetchone()[0]
                if not others:
                    raise ApiError(
                        409, 'Transfer environment administration before removing its last Admin'
                    )
        if method == 'DELETE':
            conn.execute('DELETE FROM users WHERE id=?', (ident,))
            return {'deleted': ident}
        values = dict(data)
        if 'password' in values:
            values['password_hash'] = password_hash(values.pop('password'))
        if method == 'POST':
            values.setdefault('role', 'Viewer')
            cursor = conn.execute(
                f'INSERT INTO users ({",".join(values)}) VALUES ({",".join("?" for _ in values)})',
                list(values.values()),
            )
            ident = cursor.lastrowid
            environment = self.selected_environment(conn)
            if environment and environment['role'] == 'Admin':
                conn.execute(
                    'INSERT INTO environment_members(environment_id,user_id) VALUES (?,?)',
                    (environment['id'], ident),
                )
        else:
            conn.execute(
                f'UPDATE users SET {",".join(k+"=?" for k in values)} WHERE id=?',
                [*values.values(), ident],
            )
            conn.execute('DELETE FROM sessions WHERE user_id=?', (ident,))
        return public_user(conn.execute('SELECT * FROM users WHERE id=?', (ident,)).fetchone())

    def do_GET(self):
        path = urlparse(self.path).path
        if not path.startswith('/api/') and path != '/branding.css':
            return self.serve_asset(path)
        try:
            download = re.fullmatch(r'/api/backups/([A-Za-z0-9_.-]+)/download', path)
            with connect() as conn:
                # Read the session, content, and assignments from one snapshot.
                # Release the database before writing to a potentially slow client.
                conn.execute('BEGIN')
                if path == '/api/health':
                    conn.execute('SELECT 1').fetchone()
                    result = {
                        'status': 'ok',
                        'update_protocol': UPDATE_PROTOCOL,
                        'revision': os.environ.get('VSM_REVISION', 'local'),
                    }
                elif path in ('/api/branding', '/branding.css'):
                    theme = branding(conn)
                    if path == '/api/branding':
                        result = {'branding': theme, 'defaults': BRANDING_DEFAULTS}
                    else:
                        result = branding_stylesheet(
                            theme,
                            conn.execute(
                                'SELECT id,color FROM tags WHERE environment_id=?',
                                ((self.selected_environment(conn) or {}).get('id', -1),),
                            ),
                        )
                elif path == '/api/session':
                    user = self.session(conn)
                    result = {
                        'user': public_user(user) if user else None,
                        'csrf_token': user['csrf_token'] if user else None,
                        'environment': self.selected_environment(conn, user) if user else None,
                        'version': APP_VERSION,
                        'revision': os.environ.get('VSM_REVISION', 'local'),
                        'setup_required': not bool(
                            conn.execute('SELECT 1 FROM users LIMIT 1').fetchone()
                        ),
                    }
                else:
                    admin_only = any(
                        path == base or path.startswith(base + '/') for base in ADMIN_READ_PATHS
                    )
                    self.require_user(conn, ('Admin',) if admin_only else ROLES)
                    result = None if download else self.read_api(conn, path)
            if download:
                return self.download_backup(download[1])
            if path == '/branding.css':
                self.response_headers(
                    200, len(result), 'text/css; charset=utf-8', {'Cache-Control': 'no-store'}
                )
                self.wfile.write(result)
                return
            return self.respond(200, result)
        except ApiError as exc:
            return self.respond(exc.status, {'error': str(exc)})

    def read_api(self, conn, path):
        if path == '/api/maintenance':
            operation = updater.status()
            return {
                'operation': operation,
                'supported': os.environ.get('VSM_DATA_RESET_PROTOCOL') == '1'
                and operation.get('data_reset_protocol') == 1,
            }
        if path == '/api/backups':
            return {
                'backups': updater.list_backups(),
                'operation': updater.status(),
                'supported': os.environ.get('VSM_BACKUP_PROTOCOL') == '1',
            }
        if path == '/api/updates':
            return updater.status()
        if path == '/api/roles':
            return list(ROLES)
        if path == '/api/users':
            return [
                public_user(row) for row in conn.execute('SELECT * FROM users ORDER BY username')
            ]
        if re.fullmatch(r'/api/users/[0-9]+', path):
            row = conn.execute(
                'SELECT * FROM users WHERE id=?', (int(path.rsplit('/', 1)[1]),)
            ).fetchone()
            if row is None:
                raise ApiError(404, 'Account not found')
            return public_user(row)
        if path == '/api/environments':
            return self.environment_overview(conn)
        if re.fullmatch(r'/api/environments/[0-9]+/members', path):
            return self.environment_members(conn, int(path.split('/')[3]))
        if re.fullmatch(r'/api/environments/[0-9]+', path):
            return self.environment_member(conn, int(path.split('/')[3]))
        environment = self.require_environment(conn)
        repository = ContentRepository(conn, environment['id'])
        if path == '/api/data':
            return repository.snapshot()
        parts = path.strip('/').split('/')
        if len(parts) in (2, 3) and parts[1] in RESOURCES:
            if len(parts) == 2:
                return repository.list(parts[1])
            if parts[2].isdigit():
                return repository.get(parts[1], int(parts[2]))
        raise ApiError(404, 'Not found')

    def serve_asset(self, path):
        if path not in STATIC_ASSETS:
            return self.respond(404, {'error': 'Not found'})
        filename, mime = STATIC_ASSETS[path]
        payload = (ROOT / 'web' / filename).read_bytes()
        self.response_headers(
            200,
            len(payload),
            mime + '; charset=utf-8',
            {'Content-Security-Policy': CONTENT_SECURITY_POLICY, 'Cache-Control': 'no-cache'},
        )
        self.wfile.write(payload)

    def download_backup(self, identifier):
        try:
            source = updater.backup_path(identifier)
        except ValueError as exc:
            raise ApiError(404, str(exc)) from exc
        # Stream from an opened file so cleanup cannot replace a later lookup.
        try:
            handle = source.open('rb')
        except FileNotFoundError as exc:
            raise ApiError(404, 'Backup not found') from exc
        with handle:
            self.response_headers(
                200,
                os.fstat(handle.fileno()).st_size,
                'application/octet-stream',
                {
                    'Content-Disposition': 'attachment; filename="' + source.name + '"',
                    'Cache-Control': 'no-store',
                },
            )
            shutil.copyfileobj(handle, self.wfile)

    def require_backup_engine(self):
        if os.environ.get('VSM_BACKUP_PROTOCOL') != '1' or not updater.status()['available']:
            raise ApiError(
                409,
                'Restart with the Windows service installer to enable full application backups.',
            )

    def upload_backup(self):
        source = None
        try:
            with connect() as conn:
                self.require_user(conn, ('Admin',), csrf=True)
            self.require_backup_engine()
            if updater.status()['state'] in updater.BUSY:
                raise ApiError(409, 'Another maintenance operation is in progress')
            if self.headers.get('Content-Type') != 'application/octet-stream':
                raise ApiError(415, 'Upload the backup as application/octet-stream')
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > updater.BACKUP_LIMIT:
                raise ApiError(413, 'Upload must be between 1 byte and 128 MiB')
            base = updater.directory() / 'backups'
            base.mkdir(parents=True, exist_ok=True)
            os.chmod(base, 0o700)
            source = base / ('.upload-' + uuid.uuid4().hex)
            with source.open('xb') as handle:
                os.chmod(source, 0o600)
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError('Incomplete backup upload')
                    handle.write(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            with tempfile.TemporaryDirectory(dir=base) as temporary:
                updater.validate_backup(source, Path(temporary))
                updater.validate_backup_database(Path(temporary) / 'database.sqlite3')
            identifier = 'uploaded-' + uuid.uuid4().hex + '.vsm-backup.zip'
            os.replace(source, base / identifier)
            source = None
            self.respond(201, {'id': identifier})
        except ApiError as exc:
            self.respond(exc.status, {'error': str(exc)})
        except (
            ValueError,
            OSError,
            sqlite3.Error,
            updater.zipfile.BadZipFile,
            KeyError,
            TypeError,
            RecursionError,
        ) as exc:
            self.respond(400, {'error': 'Invalid backup upload: ' + str(exc)})
        finally:
            if source:
                source.unlink(missing_ok=True)

    def maintenance_request(self, conn, path, data):
        """Validate an Admin request before handing it to the supervisor queue."""
        self.require_user(conn, ('Admin',), csrf=True)
        action = path.rsplit('/', 1)[1]
        payload = {}
        if path.startswith('/api/backups/'):
            self.require_backup_engine()
            if action == 'create':
                if data:
                    raise ValueError('Backup creation accepts an empty object')
                action = 'backup'
            elif action == 'restore':
                if set(data) != {'id'}:
                    raise ValueError('A backup identifier is required')
                updater.backup_path(data['id'])
                payload = {'id': data['id']}
            else:
                if set(data) != {'ids'} or not isinstance(data['ids'], list) or not data['ids']:
                    raise ValueError('Select backups to remove')
                for identifier in data['ids']:
                    updater.backup_path(identifier)
                payload = {'ids': data['ids']}
        else:
            if action == 'install':
                self.require_backup_engine()
            if data:
                raise ValueError('Update requests do not accept configuration or source overrides')
        try:
            return updater.request(action, **payload)
        except RuntimeError as exc:
            raise ApiError(409, str(exc)) from exc

    def mutate(self, method):
        # Browser writes must originate from this host; no cross-origin API access.
        origin = self.headers.get('Origin')
        if (
            origin
            and (
                urlparse(origin).scheme not in ('http', 'https')
                or urlparse(origin).netloc != self.headers.get('Host')
            )
        ) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return self.respond(403, {'error': 'Cross-origin writes are disabled'})
        path = urlparse(self.path).path
        parts = path.strip('/').split('/')
        if path == '/api/backups/upload' and method == 'POST':
            return self.upload_backup()
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return self.respond(415, {'error': 'Content-Type must be application/json'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 1_000_000:
                return self.respond(413, {'error': 'Request too large'})
            data = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            with connect() as conn:
                # Serialize bootstrap and account changes, including the last-Admin invariant.
                conn.execute('BEGIN IMMEDIATE')
                if updater.status()['state'] in WRITES_PAUSED:
                    raise ApiError(
                        503,
                        'An update is being installed. Please wait for the application to restart.',
                    )
                if method == 'POST' and path in (
                    '/api/backups/create',
                    '/api/backups/restore',
                    '/api/backups/cleanup',
                    '/api/updates/check',
                    '/api/updates/install',
                ):
                    result = self.maintenance_request(conn, path, data)
                    status, cookie = 202, None
                elif path == '/api/maintenance/clear-data' and method == 'POST':
                    self.require_user(conn, ('Admin',), csrf=True)
                    self.require_backup_engine()
                    if (
                        os.environ.get('VSM_DATA_RESET_PROTOCOL') != '1'
                        or updater.status().get('data_reset_protocol') != 1
                    ):
                        raise ApiError(
                            409,
                            'Rerun the Windows service installer to enable safe data clearing.',
                        )
                    if data != {'confirmation': 'DELETE ALL BUSINESS DATA'}:
                        raise ValueError(
                            'Type DELETE ALL BUSINESS DATA to confirm clearing business data'
                        )
                    try:
                        result = updater.request('clear-data')
                    except RuntimeError as exc:
                        raise ApiError(409, str(exc)) from exc
                    status, cookie = 202, None
                elif (
                    path in ('/api/pcf_import/preview', '/api/pcf_import/apply')
                    and method == 'POST'
                ):
                    environment = self.require_environment(conn, ('Admin', 'Editor'), csrf=True)
                    required = (
                        {'csv'}
                        if path.endswith('/preview')
                        else {'csv', 'fingerprint', 'decisions'}
                    )
                    if set(data) != required:
                        raise ValueError(
                            'Supply CSV text and, for apply, the preview fingerprint and overwrite decisions'
                        )
                    importer = PCFImporter(conn, data['csv'], environment['id'])
                    result = (
                        importer.preview()
                        if path.endswith('/preview')
                        else importer.apply(data['fingerprint'], data['decisions'])
                    )
                    status, cookie = 200, None
                elif path == '/api/branding' and method == 'PUT':
                    self.require_user(conn, ('Admin',), csrf=True)
                    theme = validate_branding(data)
                    conn.execute(
                        "INSERT INTO settings(key,value) VALUES ('branding',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (json.dumps(theme),),
                    )
                    status, result, cookie = 200, theme, None
                elif len(parts) >= 2 and parts[:2] == ['api', 'environments']:
                    result = self.environment_mutation(conn, parts, method, data)
                    status, cookie = (201 if method == 'POST' and len(parts) == 2 else 200), None
                elif path.startswith('/api/auth/'):
                    status, result, cookie = self.auth_mutation(conn, path, method, data)
                elif len(parts) >= 2 and parts[:2] == ['api', 'users']:
                    result = self.account_mutation(conn, parts, method, data)
                    status, cookie = (201 if method == 'POST' else 200), None
                else:
                    spec = RESOURCES.get(parts[1]) if len(parts) > 1 else None
                    environment = self.require_environment(
                        conn, spec.write_roles if spec else ('Admin', 'Editor'), csrf=True
                    )
                    result = ContentRepository(conn, environment['id']).mutate(parts, method, data)
                    status, cookie = (201 if method == 'POST' else 200), None
            self.respond(status, result, cookie)
        except ApiError as exc:
            self.respond(exc.status, {'error': str(exc)})
        except (ValueError, sqlite3.IntegrityError) as exc:
            self.respond(400, {'error': str(exc)})

    def do_POST(self):
        self.mutate('POST')

    def do_PUT(self):
        self.mutate('PUT')

    def do_DELETE(self):
        self.mutate('DELETE')
