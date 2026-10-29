"""Portable checks for Windows locking, DB handles and SCM stop forwarding.

Native service registration/restart is additionally exercised by Windows CI.
"""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import updater


class PortabilityTest(unittest.TestCase):
    def test_windows_lock_is_nonblocking_and_released_on_error(self):
        msvcrt = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=mock.Mock())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'supervisor.lock'
            with mock.patch.dict(sys.modules, msvcrt=msvcrt), mock.patch.object(
                updater, 'os', SimpleNamespace(name='nt', SEEK_END=os.SEEK_END)
            ):
                with self.assertRaisesRegex(RuntimeError, 'operation failed'):
                    with updater.supervisor_lock(path):
                        raise RuntimeError('operation failed')
                self.assertEqual([call.args[1:] for call in msvcrt.locking.call_args_list],
                                 [(2, 1), (0, 1)])
                self.assertEqual(path.read_bytes(), b'\0')
                msvcrt.locking.side_effect = OSError('already locked')
                with self.assertRaises(OSError):
                    with updater.supervisor_lock(path):
                        self.fail('A second supervisor must not enter')

    def test_windows_does_not_attempt_directory_fsync(self):
        with mock.patch.object(updater, 'os', SimpleNamespace(name='nt')):
            updater.sync_directory(Path('not-opened'))

    def test_database_commits_rolls_back_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'business # café.sqlite3'
            with updater.database(path) as connection:
                connection.execute('CREATE TABLE sample (name TEXT)')
                connection.execute("INSERT INTO sample VALUES ('saved')")
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')
            with self.assertRaises(RuntimeError):
                with updater.database(path) as connection:
                    connection.execute("INSERT INTO sample VALUES ('rolled back')")
                    raise RuntimeError('rollback')
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')
            with updater.database(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
                self.assertEqual(connection.execute('SELECT name FROM sample').fetchall(), [('saved',)])
            # Replacing a closed database is also required by restore on Windows.
            path.replace(path.with_suffix('.restored'))


class ServiceHostTest(unittest.TestCase):
    def load_host(self):
        modules = {
            'servicemanager': mock.Mock(),
            'win32service': SimpleNamespace(SERVICE_STOP_PENDING=3),
            'win32serviceutil': SimpleNamespace(ServiceFramework=type('Framework', (), {
                '__init__': lambda self, args: None,
                'ReportServiceStatus': mock.Mock(),
            })),
        }
        spec = importlib.util.spec_from_file_location('vsm_service_test', updater.ROOT / 'windows_service.py')
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
        return module

    def test_stop_before_and_after_supervisor_creation(self):
        module = self.load_host()
        service = module.VSMService([])
        service.SvcStop()
        self.assertTrue(service.stop_requested.is_set())
        service.supervisor = SimpleNamespace(stopping=mock.Mock())
        service.SvcStop()
        service.supervisor.stopping.set.assert_called_once()

    def test_service_configures_local_storage_and_real_python_interpreter(self):
        module = self.load_host()
        service = module.VSMService([])
        job = mock.Mock()
        job.QueryInformationJobObject.return_value = {'BasicLimitInformation': {'LimitFlags': 0}}
        job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 8192
        supervisor = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'local-data'
            (root / 'service.json').write_text(json.dumps({
                'data_directory': str(data), 'python_executable': 'C:/Python313/python.exe',
            }), encoding='utf-8-sig')
            with (mock.patch.object(module, 'ROOT', root),
                  mock.patch.dict(sys.modules, win32api=mock.Mock(), win32job=job),
                  mock.patch.dict(os.environ),
                  mock.patch.object(sys, 'path', list(sys.path)),
                  mock.patch.object(module.os, 'chdir'),
                  mock.patch.object(module, 'RotatingFileHandler'),
                  mock.patch.object(module.logging, 'basicConfig'),
                  mock.patch.object(updater, 'Supervisor', return_value=supervisor)):
                service.run_application()
                self.assertEqual(os.environ['VSM_HOST'], '0.0.0.0')
                self.assertEqual(os.environ['VSM_PORT'], '8080')
                self.assertEqual(os.environ['VSM_COOKIE_SECURE'], '0')
                self.assertEqual(os.environ['VSM_DATABASE'], str(data / 'vsm.sqlite3'))
                self.assertEqual(supervisor.python_executable, 'C:/Python313/python.exe')
                supervisor.run.assert_called_once()
                self.assertTrue(supervisor.output.closed)
                job.AssignProcessToJobObject.assert_called_once()

    def test_installer_contract_keeps_data_and_code_separate(self):
        installer = (updater.ROOT / 'scripts/install-windows.ps1').read_text()
        self.assertIn('NT AUTHORITY\\LocalService', installer)
        self.assertIn("'--startup', 'auto'", installer)
        self.assertIn('http://127.0.0.1:8080/api/health', installer)
        self.assertIn("'pywin32==311'", installer)
        uninstall = (updater.ROOT / 'scripts/uninstall-windows.ps1').read_text()
        self.assertNotIn('Remove-Item', uninstall)
