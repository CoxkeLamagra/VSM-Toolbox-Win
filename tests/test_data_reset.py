import json
import os
import sqlite3
from pathlib import Path
from unittest import mock

import app
import updater
import test_api
import test_updater


class DataResetTest(test_updater.UpdaterTest):
    test_update_and_restart_keeps_code_data_and_revokes_sessions = None
    test_failed_new_application_restores_code_and_database = None
    test_validation_failure_does_not_stop_current_application = None
    test_backup_failure_restarts_previous_application = None
    test_crash_recovery_restores_pending_backup = None
    test_queue_rejects_concurrent_requests_and_disabled_supervisor = None
    test_check_reports_revision_without_installation = None

    def seed_content(self):
        with updater.database(self.db) as db:
            db.execute("INSERT INTO steps(id,stream_id,name) VALUES (1,1,'Triage')")
            db.execute("INSERT INTO processes(id,name) VALUES (1,'Incident handling')")
            db.execute('INSERT INTO step_processes VALUES (1,1)')
            for index, kind in enumerate(app.PCF_TYPES, 1):
                db.execute(
                    'INSERT INTO pcf_entries(id,name,entry_type,parent_id,number) VALUES (?,?,?,?,1)',
                    (index, kind, kind, index - 1 if index > 1 else None),
                )
            db.execute('INSERT INTO stream_pcf VALUES (1,5)')
            db.execute("INSERT INTO tag_categories(id,name) VALUES (1,'Service')")
            db.execute("INSERT INTO tags(id,name,category_id) VALUES (1,'Critical',1)")
            for table in ('streams', 'steps', 'processes'):
                db.execute(f'INSERT INTO {table}_tags VALUES (1,1)')
            db.execute(
                "INSERT INTO settings VALUES ('branding',?)",
                (json.dumps({**app.BRANDING_DEFAULTS, 'name': 'Keep branding'}),),
            )

    def test_reset_backs_up_all_data_and_retains_configuration(self):
        self.seed_content()
        self.supervisor.maintenance({'action': 'clear-data'})
        status = updater.status()
        self.assertEqual(status['state'], 'succeeded', status)
        self.assertEqual(
            status['cleared'], {'streams': 1, 'steps': 1, 'processes': 1, 'pcf_entries': 5}
        )
        backup = Path(status['backup'])
        self.assertEqual(updater.validate_backup(backup)['reason'], 'pre-data-clear')
        with updater.database(self.db) as db:
            for table in (
                'streams',
                'steps',
                'processes',
                'pcf_entries',
                'stream_pcf',
                'step_processes',
                'streams_tags',
                'steps_tags',
                'processes_tags',
            ):
                self.assertEqual(
                    db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table
                )
            for table in ('users', 'sessions', 'tags', 'tag_categories'):
                self.assertEqual(
                    db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 1, table
                )
        with updater.database(self.db) as db:
            self.assertEqual(
                json.loads(
                    db.execute("SELECT value FROM settings WHERE key='branding'").fetchone()[0]
                )['name'],
                'Keep branding',
            )
        self.assertTrue(self.supervisor.healthy(timeout=10))
        self.assertFalse((self.supervisor.base / 'pending.json').exists())
        self.supervisor.maintenance({'action': 'restore', 'id': backup.name})
        self.assertEqual(updater.status()['state'], 'succeeded', updater.status())
        self.assertEqual(self.stream_name(), 'Original service')
        with updater.database(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pcf_entries').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT pcf_id FROM stream_pcf').fetchone()[0], 5)

    def test_backup_failure_never_calls_clear(self):
        self.seed_content()
        with (
            mock.patch('updater.create_backup', side_effect=OSError('Disk full')),
            mock.patch('updater.clear_business_data') as clear,
        ):
            self.supervisor.maintenance({'action': 'clear-data'})
        clear.assert_not_called()
        self.assertEqual(updater.status()['state'], 'failed')
        self.assertEqual(self.stream_name(), 'Original service')
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_failed_restart_recovers_safety_backup(self):
        self.seed_content()
        with mock.patch.object(self.supervisor, 'healthy', return_value=False):
            self.supervisor.maintenance({'action': 'clear-data'})
        self.assertEqual(updater.status()['state'], 'failed')
        self.assertTrue(self.supervisor.healthy(timeout=10))
        self.assertEqual(self.stream_name(), 'Original service')
        with updater.database(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM processes').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM pcf_entries').fetchone()[0], 5)

    def test_delete_transaction_rolls_back_on_failure(self):
        self.seed_content()
        with updater.database(self.db) as db:
            db.execute(
                "CREATE TRIGGER reject_clear BEFORE DELETE ON processes BEGIN SELECT RAISE(ABORT,'Simulated delete failure'); END"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            updater.clear_business_data(self.db)
        self.assertEqual(self.stream_name(), 'Original service')
        with updater.database(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM steps').fetchone()[0], 1)


class DataResetApiTest(test_api.ApiTest):
    test_service_delivery_lifecycle_and_persistence = None
    test_validation_and_origin_boundary = None
    test_static_application = None

    def test_admin_confirmation_csrf_and_engine_capability(self):
        path = '/api/maintenance/clear-data'
        payload = {'confirmation': 'DELETE ALL BUSINESS DATA'}
        self.assertEqual(self.request('GET', '/api/maintenance', client=self.new_client())[0], 401)
        for role in ('Editor', 'Viewer'):
            self.request(
                'POST',
                '/api/users',
                {'username': role, 'role': role, 'password': 'reset-password-123'},
            )
            client = self.new_client()
            self.request(
                'POST',
                '/api/auth/login',
                {'username': role, 'password': 'reset-password-123'},
                client=client,
            )
            self.assertEqual(self.request('GET', '/api/maintenance', client=client)[0], 403)
            self.assertEqual(self.request('POST', path, payload, client=client)[0], 403)
        self.assertEqual(self.request('POST', path, payload, headers={'X-CSRF-Token': ''})[0], 403)
        with (
            mock.patch.dict(
                os.environ, {'VSM_BACKUP_PROTOCOL': '1', 'VSM_DATA_RESET_PROTOCOL': ''}
            ),
            mock.patch(
                'updater.status',
                return_value={'state': 'idle', 'available': True, 'data_reset_protocol': 1},
            ),
        ):
            self.assertEqual(self.request('POST', path, payload)[0], 409)
        with (
            mock.patch.dict(
                os.environ, {'VSM_BACKUP_PROTOCOL': '1', 'VSM_DATA_RESET_PROTOCOL': '1'}
            ),
            mock.patch(
                'updater.status',
                return_value={'state': 'idle', 'available': True, 'data_reset_protocol': 1},
            ),
            mock.patch('updater.request', return_value={'queued': True}) as queue,
        ):
            for invalid in ({}, {'confirmation': 'yes'}, {**payload, 'extra': True}):
                self.assertEqual(self.request('POST', path, invalid)[0], 400)
            queue.assert_not_called()
            self.assertEqual(self.request('POST', path, payload)[0], 202)
            queue.assert_called_once_with('clear-data')
