import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import tarfile
import tempfile
import unittest
from unittest import mock
import app
import updater


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = self.base/'workspace.sqlite3'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        self.environment=mock.patch.dict(os.environ,{'VSM_DATABASE':str(self.db),'VSM_UPDATE_DIR':str(self.base/'updates'),
                                                   'VSM_HOST':'127.0.0.1','VSM_PORT':str(port),'VSM_REVISION':'1'*40})
        self.environment.start()
        with mock.patch.object(app.storage,'DB',self.db):
            app.initialize()
            with app.connect() as conn:
                conn.execute("INSERT INTO streams (name) VALUES ('Original service')")
                conn.execute("INSERT INTO users (username,password_hash,role) VALUES ('admin','not-used','Admin')")
                conn.execute("INSERT INTO sessions VALUES ('hashed-token',1,'csrf',9999999999)")
        self.supervisor=updater.Supervisor()
        self.supervisor.start()
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def tearDown(self):
        self.supervisor.stop()
        self.environment.stop()
        self.temp.cleanup()

    def archive(self, revision='a'*40, broken=False, duplicate=False):
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w:gz') as archive:
            for filename in updater.FILES:
                data=(updater.ROOT/filename).read_bytes()
                if broken and filename=='app.py':
                    data=data.replace(b"if __name__ == '__main__':",b"if __name__ == '__main__':\n    with sqlite3.connect(os.environ['VSM_DATABASE']) as db:\n        db.execute(\"UPDATE streams SET name='Bad migration'\")\n    raise RuntimeError('Intentional failed startup')")
                member=tarfile.TarInfo('VSM-Toolbox-'+revision+'/'+filename)
                member.size=len(data)
                archive.addfile(member,io.BytesIO(data))
                if duplicate and filename=='app.py':
                    archive.addfile(member,io.BytesIO(data))
            # Unknown paths must never be extracted.
            member=tarfile.TarInfo('../../escaped')
            member.size=4
            archive.addfile(member,io.BytesIO(b'bad!'))
        return output.getvalue()

    def fake_download(self, archive):
        def response(url,limit):
            if '/git/ref/heads/main' in url:
                return json.dumps({'object':{'sha':'a'*40,'type':'commit'}}).encode()
            self.assertTrue(url.endswith('/'+'a'*40))
            return archive
        return mock.patch('updater.download',side_effect=response)

    def stream_name(self):
        with updater.database(self.db) as conn:
            return conn.execute('SELECT name FROM streams').fetchone()[0]

    def test_update_and_restart_keeps_code_data_and_revokes_sessions(self):
        with self.fake_download(self.archive()):
            self.supervisor.update('install')
        self.assertEqual(updater.status()['state'],'succeeded')
        self.assertEqual(self.supervisor.active['revision'],'a'*40)
        self.assertEqual(self.stream_name(),'Original service')
        self.assertFalse((self.base/'escaped').exists())
        with updater.database(self.db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM users').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
        backup=Path(updater.status()['backup'])
        self.assertTrue(backup.exists())
        if os.name != 'nt':  # Windows permissions are controlled by the installation ACL.
            self.assertEqual(backup.stat().st_mode & 0o777,0o600)
        self.supervisor.stop()
        self.supervisor=updater.Supervisor()
        self.assertEqual(self.supervisor.active['revision'],'a'*40)
        self.supervisor.start()
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_failed_new_application_restores_code_and_database(self):
        with self.fake_download(self.archive(broken=True)):
            self.supervisor.update('install')
        self.assertEqual(updater.status()['state'],'failed')
        self.assertEqual(self.supervisor.active['revision'],'1'*40)
        self.assertEqual(self.stream_name(),'Original service')
        self.assertTrue(self.supervisor.healthy(timeout=10))
        with updater.database(self.db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM sessions').fetchone()[0],1)

    def test_validation_failure_does_not_stop_current_application(self):
        original_pid=self.supervisor.child.pid
        with self.fake_download(self.archive(duplicate=True)):
            self.supervisor.update('install')
        self.assertEqual(updater.status()['state'],'failed')
        self.assertEqual(self.supervisor.child.pid,original_pid)
        self.assertEqual(self.stream_name(),'Original service')

    def test_backup_failure_restarts_previous_application(self):
        with self.fake_download(self.archive()), mock.patch('updater.backup_database',side_effect=OSError('Disk full')):
            self.supervisor.update('install')
        self.assertEqual(updater.status()['state'],'failed')
        self.assertTrue(self.supervisor.healthy(timeout=10))
        self.assertEqual(self.stream_name(),'Original service')

    def test_crash_recovery_restores_pending_backup(self):
        self.supervisor.stop()
        backup=self.base/'updates'/'backups'/'recover.sqlite3'
        updater.backup_database(self.db,backup)
        previous=dict(self.supervisor.active)
        with updater.database(self.db) as conn:
            conn.execute("UPDATE streams SET name='Interrupted migration'")
        updater.atomic_json(self.base/'updates'/'pending.json',{'previous':previous,'backup':str(backup)})
        self.supervisor=updater.Supervisor()
        self.supervisor.recover()
        self.assertEqual(self.stream_name(),'Original service')
        self.assertEqual(self.supervisor.active,previous)
        self.assertFalse((self.base/'updates'/'pending.json').exists())
        self.supervisor.start()
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_queue_rejects_concurrent_requests_and_disabled_supervisor(self):
        with self.assertRaises(RuntimeError):
            updater.request('install')
        with mock.patch.dict(os.environ,{'VSM_UPDATER':'1'}):
            (self.base/'updates'/'heartbeat').touch()
            self.supervisor.report('idle','Ready')
            self.assertTrue(updater.request('check')['queued'])
            with self.assertRaises(RuntimeError):
                updater.request('install')
            self.assertEqual(updater.read_json(self.base/'updates'/'request.json'),{'action':'check'})
        with self.assertRaises(ValueError):
            updater.request('arbitrary-url')

    def test_check_reports_revision_without_installation(self):
        original_pid=self.supervisor.child.pid
        with mock.patch('updater.latest_revision',return_value='a'*40):
            self.supervisor.update('check')
        self.assertEqual(updater.status()['latest_revision'],'a'*40)
        self.assertEqual(self.supervisor.child.pid,original_pid)
        self.assertFalse((self.base/'updates'/'active.json').exists())


class DownloadPolicyTest(unittest.TestCase):
    def test_latest_revision_uses_compact_branch_ref_instead_of_commit_diff(self):
        revision = 'a' * 40
        def response(url, limit):
            self.assertEqual(url, f'https://api.github.com/repos/{updater.REPOSITORY}/git/ref/heads/main')
            self.assertEqual(limit, 100_000)
            return json.dumps({'object': {'sha': revision, 'type': 'commit'}}).encode()
        with mock.patch('updater.download', side_effect=response) as download:
            self.assertEqual(updater.latest_revision(), revision)
        download.assert_called_once()

    def test_latest_revision_rejects_malformed_refs(self):
        for info in (None, [], {}, {'object': []}, {'object': {'sha': 123, 'type': 'commit'}},
                     {'object': {'sha': 'a'*40, 'type': 'tag'}}, {'object': {'sha': 'bad', 'type': 'commit'}}):
            with self.subTest(info=info), mock.patch('updater.download', return_value=json.dumps(info).encode()):
                with self.assertRaisesRegex(ValueError, 'invalid commit revision'):
                    updater.latest_revision()

    def test_download_still_rejects_oversized_responses(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'x' * 101
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'exceeds the size limit'):
                updater.download('https://api.github.com/repos/example/repo/git/ref/heads/main', 100)
        response.__enter__.return_value.read.assert_called_once_with(101)

    def test_token_is_only_attached_to_initial_github_api_request(self):
        response=mock.MagicMock()
        response.__enter__.return_value.read.return_value=b'{}'
        opener=mock.Mock()
        opener.open.return_value=response
        with mock.patch.dict(os.environ,{'VSM_GITHUB_TOKEN':'test-token-not-a-real-secret'}), mock.patch('urllib.request.build_opener',return_value=opener):
            self.assertEqual(updater.download('https://api.github.com/repos/example/repo/commits/main',100),b'{}')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'),'Bearer test-token-not-a-real-secret')
        redirected=updater.HTTPSRedirect().redirect_request(request,None,302,'Found',{},'https://codeload.github.com/example/repo/tar.gz/revision')
        self.assertIsNone(redirected.get_header('Authorization'))
        with self.assertRaises(ValueError):
            updater.HTTPSRedirect().redirect_request(request,None,302,'Found',{},'http://codeload.github.com/example/repo/archive')
        with self.assertRaises(ValueError):
            updater.HTTPSRedirect().redirect_request(request,None,302,'Found',{},'https://other.example/archive')
