"""Modular delivery boundaries and legacy backup recovery."""

import io
import json
from pathlib import Path
import tarfile
import tempfile
import subprocess
import sys
import os
import shutil
import re
import posixpath
import unittest
from unittest import mock
import updater
import test_updater
from vsm.runtime import FILES, LEGACY_FILES, release_files


class DeliveryTest(unittest.TestCase):
    def test_inventory_and_unsafe_paths(self):
        manifest = json.loads((updater.ROOT / 'update-manifest.json').read_text())
        self.assertEqual(set(release_files(manifest)), set(FILES))
        disk = {
            p.relative_to(updater.ROOT).as_posix()
            for directory in ('vsm', 'web')
            for p in (updater.ROOT / directory).rglob('*')
            if p.is_file() and p.suffix in ('.py', '.js', '.css', '.html')
        }
        self.assertEqual(disk, {name for name in FILES if name.startswith(('vsm/', 'web/'))})
        for name in (
            '../escape.py',
            'vsm/../../escape.py',
            '/tmp/escape.py',
            'web/../app.py',
            'web//file.js',
            'vsm/a\\b.py',
            'web/script.svg',
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                release_files({**manifest, 'files': [*FILES, name]})
        with self.assertRaises(ValueError):
            release_files({**manifest, 'files': [*FILES, FILES[0]]})
        legacy = {**manifest, 'update_protocol': 1, 'files': list(LEGACY_FILES)}
        self.assertEqual(release_files(legacy), LEGACY_FILES)
        with self.assertRaises(ValueError):
            release_files(legacy, allow_legacy=False)

    def test_frontend_imports_are_declared_runtime_assets(self):
        for name in FILES:
            if not name.endswith('.js'):
                continue
            source = (updater.ROOT / name).read_text()
            for specifier in re.findall(r"(?m)^import[\s\S]*?from\s+['\"]([^'\"]+)['\"]", source):
                self.assertTrue(specifier.startswith('.'), (name, specifier))
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), specifier))
                self.assertIn(resolved, FILES, (name, specifier))

    def test_staged_installer_payload_is_self_contained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in FILES:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(updater.ROOT / name, target)
            environment = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
            environment['VSM_DATABASE'] = str(root / 'check.sqlite3')
            result = subprocess.run(
                [
                    sys.executable,
                    '-c',
                    "import app; app.initialize(); assert app.UPDATE_PROTOCOL == 2; assert str(app.ROOT) == __import__('os').getcwd()",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_future_declared_modules_are_downloaded_and_compiled(self):
        manifest = json.loads((updater.ROOT / 'update-manifest.json').read_text())
        name = 'vsm/new_feature.py'
        manifest['files'].append(name)
        files = {filename: (updater.ROOT / filename).read_bytes() for filename in FILES}
        files['update-manifest.json'] = json.dumps(manifest).encode()
        files[name] = b'FEATURE = True\n'
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for filename, data in files.items():
                member = tarfile.TarInfo('VSM-test/' + filename)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch('updater.download', return_value=output.getvalue()),
        ):
            updater.prepare_release('a' * 40, Path(directory))
            self.assertEqual((Path(directory) / name).read_bytes(), files[name])

    def test_modular_legacy_and_modular_restore(self):
        fixture = test_updater.UpdaterTest()
        fixture.setUp()
        try:
            modular = updater.create_backup(fixture.supervisor.active, fixture.db, 'modular-test')
            legacy = fixture.base / 'legacy'
            legacy.mkdir()
            for name in LEGACY_FILES:
                target = legacy / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((updater.ROOT / name).read_bytes())
            manifest = json.loads((legacy / 'update-manifest.json').read_text())
            manifest.update(update_protocol=1, files=list(LEGACY_FILES))
            (legacy / 'update-manifest.json').write_text(json.dumps(manifest))
            (legacy / 'app.py').write_text(
                '''import json,os
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  payload=json.dumps({'status':'ok','update_protocol':1,'revision':os.environ['VSM_REVISION']}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
HTTPServer(('127.0.0.1',int(os.environ['VSM_PORT'])),Handler).serve_forever()
'''
            )
            bundle = updater.create_backup(
                {'path': str(legacy), 'revision': 'b' * 40}, fixture.db, 'legacy-test'
            )
            updater.validate_backup(bundle)
            fixture.supervisor.maintenance({'action': 'restore', 'id': bundle.name})
            self.assertEqual(updater.status()['state'], 'succeeded', updater.status())
            self.assertTrue(fixture.supervisor.healthy(timeout=5))
            self.assertEqual(fixture.stream_name(), 'Original service')
            fixture.supervisor.maintenance({'action': 'restore', 'id': modular.name})
            self.assertEqual(updater.status()['state'], 'succeeded', updater.status())
            self.assertTrue((Path(fixture.supervisor.active['path']) / 'vsm/http.py').is_file())
            self.assertEqual(fixture.stream_name(), 'Original service')
        finally:
            fixture.tearDown()
