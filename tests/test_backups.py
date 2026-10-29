import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import zipfile
from unittest import mock
import test_updater
import test_api
import updater
import app


class BackupTest(test_updater.UpdaterTest):
    test_update_and_restart_keeps_code_data_and_revokes_sessions=None
    test_failed_new_application_restores_code_and_database=None
    test_validation_failure_does_not_stop_current_application=None
    test_backup_failure_restarts_previous_application=None
    test_crash_recovery_restores_pending_backup=None
    test_queue_rejects_concurrent_requests_and_disabled_supervisor=None
    test_check_reports_revision_without_installation=None

    def manual(self):
        self.supervisor.maintenance({'action':'backup'})
        self.assertEqual(updater.status()['state'],'succeeded',updater.status())
        return Path(updater.status()['backup'])

    def modify_bundle(self,source,change):
        with zipfile.ZipFile(source) as archive: files={n:archive.read(n) for n in archive.namelist()}
        change(files)
        metadata=json.loads(files['backup.json'])
        for name in updater.BACKUP_FILES: metadata['sha256'][name]=updater.hashlib.sha256(files[name]).hexdigest()
        files['backup.json']=json.dumps(metadata).encode()
        target=source.parent/('uploaded-'+updater.uuid.uuid4().hex+'.vsm-backup.zip')
        with zipfile.ZipFile(target,'w') as archive:
            for name,content in files.items(): archive.writestr(name,content)
        return target

    def test_manual_backup_keeps_web_process_running(self):
        child = self.supervisor.child
        with mock.patch.object(self.supervisor, 'stop') as stop, mock.patch.object(self.supervisor, 'start') as start:
            self.manual()
        stop.assert_not_called()
        start.assert_not_called()
        self.assertIs(self.supervisor.child, child)
        self.assertIsNone(child.poll())
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_manual_backup_contains_code_database_and_checksums(self):
        with updater.database(self.db) as db:
            db.execute("INSERT INTO settings(key,value) VALUES ('branding',?)", (json.dumps({**app.BRANDING_DEFAULTS, 'name':'Backed-up brand'}),))
        source=self.manual()
        metadata=updater.validate_backup(source)
        self.assertEqual(metadata['reason'],'manual')
        target, _ = updater.unpack_backup(source)
        with updater.database(target/'database.sqlite3') as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM settings WHERE key='branding'").fetchone()[0])['name'], 'Backed-up brand')
        with zipfile.ZipFile(source) as archive:
            self.assertEqual(set(archive.namelist()),{*updater.BACKUP_FILES,'backup.json'})
            self.assertEqual(archive.read('app.py'),(updater.ROOT/'app.py').read_bytes())
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_bpmn_xml_survives_restore(self):
        xml = '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"><process id="p"><task id="t" name="Restore me"/></process></definitions>'
        with updater.database(self.db) as db:
            db.execute('INSERT INTO processes(name,bpmn_xml) VALUES (?,?)', ('Imported', xml))
        source = self.manual()
        with updater.database(self.db) as db:
            db.execute('DELETE FROM processes')
        self.supervisor.maintenance({'action': 'restore', 'id': source.name})
        self.assertEqual(updater.status()['state'], 'succeeded')
        with updater.database(self.db) as db:
            self.assertEqual(db.execute("SELECT bpmn_xml FROM processes WHERE name='Imported'").fetchone()[0], xml)

    def test_tag_catalog_and_assignments_survive_restore(self):
        with updater.database(self.db) as db:
            db.execute("INSERT INTO tag_categories(id,name) VALUES (1,'Services')")
            db.execute("INSERT INTO tags(id,name,color,category_id) VALUES (1,'Critical','#336699',1)")
            db.execute("INSERT INTO streams_tags SELECT id,1 FROM streams")
        source = self.manual()
        with updater.database(self.db) as db:
            db.execute('DELETE FROM streams_tags')
            db.execute('DELETE FROM tags')
        self.supervisor.maintenance({'action':'restore','id':source.name})
        self.assertEqual(updater.status()['state'],'succeeded',updater.status())
        with updater.database(self.db) as db:
            self.assertEqual(db.execute('SELECT name,color FROM tags').fetchone(),('Critical','#336699'))
            self.assertEqual(db.execute('SELECT count(*) FROM streams_tags').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT name FROM tag_categories').fetchone()[0],'Services')

    def test_pcf_hierarchy_and_stream_links_survive_restore(self):
        with updater.database(self.db) as db:
            db.execute("INSERT INTO pcf_entries(id,name,entry_type,number) VALUES (1,'IT Management','Category',1)")
            db.execute("INSERT INTO pcf_entries(id,name,entry_type,parent_id,number) VALUES (2,'Fault to Restoration','Process Group',1,1)")
            db.execute('INSERT INTO stream_pcf SELECT id,2 FROM streams')
        source=self.manual()
        with updater.database(self.db) as db:
            db.execute('DELETE FROM stream_pcf')
            db.execute('DELETE FROM pcf_entries WHERE id=2')
            db.execute('DELETE FROM pcf_entries WHERE id=1')
        self.supervisor.maintenance({'action':'restore','id':source.name})
        self.assertEqual(updater.status()['state'],'succeeded',updater.status())
        with updater.database(self.db) as db:
            self.assertEqual(db.execute('SELECT parent_id FROM pcf_entries WHERE id=2').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT pcf_id FROM stream_pcf').fetchone()[0],2)

    def test_multiple_environments_and_memberships_survive_restore(self):
        with updater.database(self.db) as db:
            db.execute("INSERT INTO environment_members VALUES (1,1,'Admin')")
            db.execute("INSERT INTO environments(id,name,created_by,created_at) VALUES (2,'Second environment',1,1)")
            db.execute("INSERT INTO environment_members VALUES (2,1,'Admin')")
            db.execute("INSERT INTO streams(id,name,environment_id) VALUES (2,'Second service',2)")
            db.execute("INSERT INTO pcf_entries(id,name,entry_type,number,environment_id) VALUES (1,'First category','Category',1,1)")
            db.execute("INSERT INTO pcf_entries(id,name,entry_type,number,environment_id) VALUES (2,'Second category','Category',1,2)")
            db.execute('INSERT INTO stream_pcf VALUES (2,2)')
            db.execute("INSERT INTO tags(id,name,environment_id) VALUES (1,'Critical',1)")
            db.execute("INSERT INTO tags(id,name,environment_id) VALUES (2,'Critical',2)")
        source=self.manual()
        updater.clear_business_data(self.db)
        with updater.database(self.db) as db:db.execute("UPDATE environments SET name='Changed' WHERE id=2")
        self.supervisor.maintenance({'action':'restore','id':source.name})
        self.assertEqual(updater.status()['state'],'succeeded',updater.status())
        with updater.database(self.db) as db:
            self.assertEqual(db.execute('SELECT name FROM environments WHERE id=2').fetchone()[0],'Second environment')
            self.assertEqual(db.execute('SELECT count(*) FROM environment_members').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT name FROM streams WHERE environment_id=2').fetchone()[0],'Second service')
            self.assertEqual(db.execute('SELECT pcf_id FROM stream_pcf WHERE stream_id=2').fetchone()[0],2)
            self.assertEqual(list(db.execute('PRAGMA foreign_key_check')),[])

    def test_local_restore_creates_safety_backup_and_restores_accounts_data(self):
        source=self.manual()
        with updater.database(self.db) as db:
            db.execute("UPDATE streams SET name='Changed after backup'")
            db.execute("UPDATE users SET username='changed-admin'")
        self.supervisor.maintenance({'action':'restore','id':source.name})
        self.assertEqual(updater.status()['state'],'succeeded',updater.status())
        self.assertEqual(self.stream_name(),'Original service')
        with updater.database(self.db) as db:
            self.assertEqual(db.execute('SELECT username FROM users').fetchone()[0],'admin')
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
        safety=Path(updater.status()['backup'])
        target,metadata=updater.unpack_backup(safety)
        with updater.database(target/'database.sqlite3') as db:
            self.assertEqual(db.execute('SELECT name FROM streams').fetchone()[0],'Changed after backup')
        self.assertEqual(metadata['reason'],'pre-restore')
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_failed_restore_rolls_back_code_and_database(self):
        source=self.manual()
        def break_app(files):
            files['app.py']=files['app.py'].replace(b"if __name__ == '__main__':",b"if __name__ == '__main__':\n    with sqlite3.connect(os.environ['VSM_DATABASE']) as db:\n        db.execute(\"UPDATE streams SET name='Bad restore'\")\n    raise RuntimeError('Deliberate backup restore failure')")
        broken=self.modify_bundle(source,break_app)
        self.supervisor.maintenance({'action':'restore','id':broken.name})
        self.assertEqual(updater.status()['state'],'failed')
        self.assertEqual(self.stream_name(),'Original service')
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_update_rollback_uses_archived_code_when_original_directory_is_gone(self):
        previous=self.base/'previous-app'
        for name in updater.FILES:
            target=previous/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(updater.ROOT/name,target)
        self.supervisor.stop()
        self.supervisor.active={'path':str(previous),'revision':'1'*40}
        self.supervisor.start();self.assertTrue(self.supervisor.healthy(timeout=10))
        original=updater.create_backup
        def backup_and_remove(*args):
            result=original(*args);shutil.rmtree(previous);return result
        with self.fake_download(self.archive(broken=True)),mock.patch('updater.create_backup',side_effect=backup_and_remove):
            self.supervisor.update('install')
        self.assertFalse(previous.exists())
        self.assertEqual(updater.status()['state'],'failed')
        self.assertEqual(self.supervisor.active['revision'],'1'*40)
        self.assertTrue((Path(self.supervisor.active['path'])/'app.py').exists())
        self.assertTrue(self.supervisor.healthy(timeout=10))
        self.assertEqual(self.stream_name(),'Original service')

    def test_corrupt_or_traversal_backup_is_rejected_before_shutdown(self):
        source=self.manual();pid=self.supervisor.child.pid
        corrupt=source.parent/'corrupt.vsm-backup.zip';corrupt.write_bytes(b'not a zip')
        self.supervisor.maintenance({'action':'restore','id':corrupt.name})
        self.assertEqual(self.supervisor.child.pid,pid)
        with zipfile.ZipFile(source) as archive: files={n:archive.read(n) for n in archive.namelist()}
        with zipfile.ZipFile(corrupt,'w') as archive:
            for n,data in files.items(): archive.writestr(n,data)
            archive.writestr('../../escape',b'bad')
        with self.assertRaises(ValueError):updater.validate_backup(corrupt)
        with self.assertRaises(ValueError):updater.backup_path('../../anything')
        with zipfile.ZipFile(corrupt,'w') as archive:
            for n,data in files.items(): archive.writestr(n,b'changed' if n=='web/app.js' else data)
        with self.assertRaises(ValueError):updater.validate_backup(corrupt)
        self.assertEqual(self.stream_name(),'Original service')

    def test_interrupted_bundle_restore_recovers_on_supervisor_start(self):
        source=self.manual()
        previous=dict(self.supervisor.active)
        self.supervisor.stop()
        with updater.database(self.db) as db: db.execute("UPDATE streams SET name='Interrupted operation'")
        updater.atomic_json(updater.directory()/'pending.json',{'backup':str(source),'previous':previous,'bundle':True})
        self.supervisor=updater.Supervisor()
        self.supervisor.recover()
        self.assertEqual(self.stream_name(),'Original service')
        self.assertFalse((updater.directory()/'pending.json').exists())
        self.assertNotEqual(self.supervisor.active['path'],previous['path'])
        self.supervisor.start();self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_invalid_manifest_types_are_rejected_without_activation(self):
        source=self.manual()
        def change(files):
            manifest=json.loads(files['update-manifest.json'])
            manifest['python_minimum']=None
            files['update-manifest.json']=json.dumps(manifest).encode()
        invalid=self.modify_bundle(source,change)
        with self.assertRaises(ValueError): updater.validate_backup(invalid)
        with zipfile.ZipFile(source) as archive: files={n:archive.read(n) for n in archive.namelist()}
        manifest=json.loads(files['backup.json']);manifest['sha256']=list(updater.BACKUP_FILES)
        files['backup.json']=json.dumps(manifest).encode()
        with zipfile.ZipFile(invalid,'w') as archive:
            for name,data in files.items():archive.writestr(name,data)
        with self.assertRaises(ValueError):updater.validate_backup(invalid)
        self.assertTrue(self.supervisor.healthy(timeout=10))

    def test_cleanup_only_selected_backups_and_protects_recovery(self):
        first=self.manual();second=self.manual()
        updater.atomic_json(updater.directory()/'pending.json',{'backup':str(second)})
        with self.assertRaises(ValueError):updater.delete_backups([second.name])
        self.assertTrue(second.exists())
        self.assertEqual(updater.delete_backups([first.name]),1)
        self.assertFalse(first.exists());self.assertTrue(second.exists())
        (updater.directory()/'pending.json').unlink()


class BackupApiTest(test_api.ApiTest):
    test_service_delivery_lifecycle_and_persistence=None
    test_validation_and_origin_boundary=None
    test_static_application=None

    def test_permissions_csrf_and_unsupported_supervisor(self):
        self.assertEqual(self.request('GET','/api/backups',client=self.new_client())[0],401)
        for name,role in [('viewer','Viewer'),('editor','Editor')]:
            self.request('POST','/api/users',{'username':name,'password':'viewer-password-123','role':role})
            client=self.new_client();self.request('POST','/api/auth/login',{'username':name,'password':'viewer-password-123'},client=client)
            for method,path,body in [('GET','/api/backups',None),('GET','/api/backups/fake.vsm-backup.zip/download',None),('POST','/api/backups/upload',{}),('POST','/api/backups/create',{}),('POST','/api/backups/restore',{'id':'x'}),('POST','/api/backups/cleanup',{'ids':['x']})]:
                self.assertEqual(self.request(method,path,body,client=client)[0],403)
        self.assertEqual(self.request('POST','/api/backups/create',{},headers={'X-CSRF-Token':''})[0],403)
        with mock.patch.dict(os.environ,{'VSM_BACKUP_PROTOCOL':'0'}):
            self.assertEqual(self.request('POST','/api/updates/install',{})[0],409)
            self.assertEqual(self.request('POST','/api/backups/create',{})[0],409)
        with mock.patch.dict(os.environ,{'VSM_BACKUP_PROTOCOL':'1'}),mock.patch('updater.status',return_value={'available':True,'state':'idle'}),mock.patch('updater.request',return_value={'queued':True}) as queue:
            self.assertEqual(self.request('POST','/api/backups/create',{})[0],202)
            queue.assert_called_once_with('backup')
