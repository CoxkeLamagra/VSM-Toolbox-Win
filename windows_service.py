"""Windows SCM host. Kept outside web-update releases so service registration is stable."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

import servicemanager
import win32service
import win32serviceutil

ROOT = Path(__file__).resolve().parent


class VSMService(win32serviceutil.ServiceFramework):
    _svc_name_ = 'VSMToolbox'
    _svc_display_name_ = 'VSM Toolbox'
    _svc_description_ = 'Local VSM Toolbox web application on HTTP port 8080.'

    def __init__(self, args):
        super().__init__(args)
        import threading
        self.stop_requested = threading.Event()
        self.supervisor = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=30000)
        self.stop_requested.set()
        if self.supervisor is not None:
            self.supervisor.stopping.set()

    def SvcDoRun(self):
        try:
            self.run_application()
        except Exception:
            logging.exception('VSM service failed')
            servicemanager.LogErrorMsg('VSM Toolbox failed; inspect the local service log.')
            # A nonzero process exit lets SCM apply its configured recovery actions.
            os._exit(1)

    def run_application(self):
        import win32api
        import win32job
        # Keep all web children in the service's job. Even an abrupt service-host
        # failure must release port 8080 before SCM starts a replacement instance.
        self.job = win32job.CreateJobObject(None, None)
        limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
        limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
        win32job.AssignProcessToJobObject(self.job, win32api.GetCurrentProcess())
        settings = json.loads((ROOT / 'service.json').read_text(encoding='utf-8-sig'))
        data = Path(settings['data_directory'])
        if not data.is_absolute():
            raise ValueError('The service data directory must be absolute')
        data.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(data / 'service.log', maxBytes=5_000_000, backupCount=3,
                                      encoding='utf-8')
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        os.environ.update(VSM_HOST='0.0.0.0', VSM_PORT='8080', VSM_COOKIE_SECURE='0',
                          VSM_DATABASE=str(data / 'vsm.sqlite3'),
                          VSM_UPDATE_DIR=str(data / 'updates'))
        from updater import Supervisor
        self.supervisor = Supervisor()
        # Embedded pythonservice.exe is not a Python command-line interpreter.
        self.supervisor.python_executable = settings['python_executable']
        if self.stop_requested.is_set():
            self.supervisor.stopping.set()
            return
        # Child output goes to a local file instead of the service's absent console.
        with (data / 'application.log').open('a', encoding='utf-8') as output:
            self.supervisor.output = output
            logging.info('Starting VSM Toolbox on HTTP port 8080')
            self.supervisor.run()
            logging.info('VSM Toolbox stopped')


if __name__ == '__main__':
    win32serviceutil.HandleCommandLine(VSMService)
