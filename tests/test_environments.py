import updater
import sqlite3
import app
import test_api


class EnvironmentTest(test_api.ApiTest):
    test_service_delivery_lifecycle_and_persistence = None
    test_validation_and_origin_boundary = None
    test_static_application = None

    def create(self, name='Second', client=None):
        status, environment = self.request(
            'POST', '/api/environments', {'name': name}, client=client
        )
        self.assertEqual(status, 201, environment)
        return environment

    def switch(self, ident, client=None):
        return self.request('POST', f'/api/environments/{ident}/switch', {}, client=client)

    def account(self, username='viewer', role='Viewer'):
        status, user = self.request(
            'POST',
            '/api/users',
            {'username': username, 'password': 'environment-password-123', 'role': role},
        )
        self.assertEqual(status, 201, user)
        client = self.new_client()
        self.request(
            'POST',
            '/api/auth/login',
            {'username': username, 'password': 'environment-password-123'},
            client=client,
        )
        return user, client

    def test_creator_is_environment_admin_independent_of_account_role(self):
        _, viewer = self.account()
        _, original = self.request('POST', '/api/streams', {'name': 'Existing'})
        environment = self.create('Owned by Viewer', viewer)
        session = self.request('GET', '/api/session', client=viewer)[1]
        self.assertEqual(session['user']['role'], 'Viewer')
        self.assertEqual(session['environment']['role'], 'Admin')
        self.assertEqual(
            self.request('POST', '/api/streams', {'name': 'Owned'}, client=viewer)[0], 201
        )
        self.assertEqual(
            self.request('POST', '/api/tags', {'name': 'Catalog'}, client=viewer)[0], 201
        )
        self.assertEqual(self.request('GET', '/api/users', client=viewer)[0], 403)
        self.assertEqual(
            self.request(
                'DELETE',
                '/api/environments/' + str(environment['id']),
                {'confirmation': environment['name']},
                client=viewer,
            )[0],
            200,
        )
        self.assertEqual(self.request('GET', '/api/streams/' + str(original['id']))[0], 200)
        self.assertEqual(
            self.request('GET', '/api/session', client=viewer)[1]['environment']['id'], 1
        )

    def test_all_content_and_relationships_are_isolated(self):
        _, tag = self.request('POST', '/api/tags', {'name': 'Critical'})
        _, category = self.request('POST', '/api/tag_categories', {'name': 'Services'})
        _, pcf = self.request(
            'POST', '/api/pcf_entries', {'name': 'Category', 'entry_type': 'Category', 'number': 1}
        )
        _, stream = self.request(
            'POST',
            '/api/streams',
            {'name': 'Private A', 'tag_ids': [tag['id']], 'pcf_ids': [pcf['id']]},
        )
        _, process = self.request('POST', '/api/processes', {'name': 'Private procedure'})
        second = self.create()
        data = self.request('GET', '/api/data')[1]
        for key in (
            'streams',
            'steps',
            'processes',
            'pcf_entries',
            'tags',
            'tag_categories',
            'step_processes',
        ):
            self.assertEqual(data[key], [], key)
        self.assertEqual(self.request('GET', '/api/streams/' + str(stream['id']))[0], 404)
        self.assertEqual(
            self.request('PUT', '/api/processes/' + str(process['id']), {'name': 'Denied'})[0], 404
        )
        self.assertEqual(self.request('DELETE', '/api/pcf_entries/' + str(pcf['id']))[0], 404)
        self.assertEqual(
            self.request('POST', '/api/streams', {'name': 'Denied', 'tag_ids': [tag['id']]})[0], 400
        )
        self.assertEqual(
            self.request('POST', '/api/streams', {'name': 'Denied', 'pcf_ids': [pcf['id']]})[0], 400
        )
        self.assertEqual(
            self.request('POST', '/api/steps', {'name': 'Denied', 'stream_id': stream['id']})[0],
            400,
        )
        self.assertEqual(
            self.request('POST', '/api/tags', {'name': 'Denied', 'category_id': category['id']})[0],
            400,
        )
        self.assertEqual(
            self.request(
                'POST',
                '/api/pcf_entries',
                {
                    'name': 'Denied',
                    'entry_type': 'Process Group',
                    'parent_id': pcf['id'],
                    'number': 1,
                },
            )[0],
            400,
        )
        self.assertEqual(
            self.request('POST', '/api/streams', {'name': 'Denied', 'environment_id': 1})[0], 400
        )
        _, own = self.request('POST', '/api/streams', {'name': 'B'})
        _, step = self.request('POST', '/api/steps', {'name': 'B step', 'stream_id': own['id']})
        self.assertEqual(
            self.request(
                'PUT', f"/api/steps/{step['id']}/processes", {'process_ids': [process['id']]}
            )[0],
            400,
        )
        for table, payload in [
            ('tags', {'name': 'Critical'}),
            ('tag_categories', {'name': 'Services'}),
            ('pcf_entries', {'name': 'Category', 'entry_type': 'Category', 'number': 1}),
        ]:
            self.assertEqual(self.request('POST', '/api/' + table, payload)[0], 201)
        self.assertEqual(
            self.request(
                'POST', '/api/streams', {'name': 'Stale'}, headers={'X-Environment-ID': '1'}
            )[0],
            409,
        )
        with app.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('INSERT INTO streams_tags VALUES (?,?)', (own['id'], tag['id']))
        self.assertEqual(self.switch(1)[0], 200)
        self.assertEqual(self.request('GET', '/api/data')[1]['streams'][0]['name'], 'Private A')

    def test_tag_catalog_names_and_mutations_are_environment_scoped(self):
        original = {}
        for table in ('tag_categories', 'tags'):
            status, item = self.request('POST', '/api/' + table, {'name': 'Service'})
            self.assertEqual(status, 201)
            original[table] = item
            self.assertEqual(self.request('POST', '/api/' + table, {'name': ' service '})[0], 400)
        self.create()
        own = {}
        for table in ('tag_categories', 'tags'):
            self.assertEqual(self.request('GET', '/api/' + table)[1], [])
            status, item = self.request('POST', '/api/' + table, {'name': 'Service'})
            self.assertEqual(status, 201)
            own[table] = item
            self.assertNotEqual(item['id'], original[table]['id'])
            foreign = '/api/' + table + '/' + str(original[table]['id'])
            for method, payload in [('GET', None), ('PUT', {'name': 'Changed'}), ('DELETE', None)]:
                self.assertEqual(self.request(method, foreign, payload)[0], 404)
        self.assertEqual(
            self.request(
                'PUT',
                '/api/tags/' + str(own['tags']['id']),
                {'category_id': original['tag_categories']['id']},
            )[0],
            400,
        )
        self.assertEqual(
            self.request(
                'PUT',
                '/api/tags/' + str(own['tags']['id']),
                {'category_id': own['tag_categories']['id']},
            )[0],
            200,
        )
        self.assertEqual(self.request('DELETE', '/api/tags/' + str(own['tags']['id']))[0], 200)
        self.assertEqual(self.switch(1)[0], 200)
        self.assertEqual(self.request('GET', '/api/tags')[1][0]['id'], original['tags']['id'])
        self.assertEqual(self.request('GET', '/api/tag_categories')[1][0]['name'], 'Service')

    def test_membership_roles_switching_and_last_admin_protection(self):
        user, viewer = self.account()
        second = self.create()
        ident = second['id']
        self.assertEqual(self.switch(ident, viewer)[0], 404)
        self.assertEqual(len(self.request('GET', '/api/environments', client=viewer)[1]), 1)
        self.assertEqual(
            self.request(
                'POST',
                f'/api/environments/{ident}/members',
                {'username': 'viewer', 'role': 'Editor'},
            )[0],
            200,
        )
        self.assertEqual(self.switch(ident, viewer)[0], 200)
        self.assertEqual(
            self.request('POST', '/api/streams', {'name': 'Allowed'}, client=viewer)[0], 201
        )
        self.assertEqual(
            self.request(
                'DELETE',
                f'/api/environments/{ident}',
                {'confirmation': second['name']},
                client=viewer,
            )[0],
            403,
        )
        self.assertEqual(
            self.request('GET', f'/api/environments/{ident}/members', client=viewer)[0], 403
        )
        member = f'/api/environments/{ident}/members/{user["id"]}'
        self.assertEqual(self.request('PUT', member, {'role': 'Viewer'})[0], 200)
        self.assertEqual(
            self.request('POST', '/api/streams', {'name': 'Denied'}, client=viewer)[0], 403
        )
        self.assertEqual(
            self.request('PUT', f'/api/environments/{ident}/members/1', {'role': 'Viewer'})[0], 409
        )
        self.assertEqual(self.request('DELETE', f'/api/environments/{ident}/members/1', {})[0], 409)
        self.assertEqual(self.request('DELETE', member, {})[0], 200)
        self.assertEqual(
            self.request(
                'GET', '/api/data', headers={'X-Environment-ID': str(ident)}, client=viewer
            )[0],
            409,
        )

    def test_csv_scope_and_preview_binding(self):
        text = 'PCF ID,Hierarchy ID,Name\nC,1,Category\nG,1.1,Group\n'

        def preview():
            return self.request('POST', '/api/pcf_import/preview', {'csv': text})[1]

        first = preview()
        self.request(
            'POST',
            '/api/pcf_import/apply',
            {'csv': text, 'fingerprint': first['fingerprint'], 'decisions': {}},
        )
        second = self.create()
        other = preview()
        self.assertEqual(other['new'], 2)
        self.assertNotEqual(first['fingerprint'], other['fingerprint'])
        status, result = self.request(
            'POST',
            '/api/pcf_import/apply',
            {'csv': text, 'fingerprint': other['fingerprint'], 'decisions': {}},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(len(self.request('GET', '/api/pcf_entries')[1]), 2)
        self.switch(1)
        self.assertEqual(len(self.request('GET', '/api/pcf_entries')[1]), 2)
        self.assertEqual(
            self.request(
                'POST',
                '/api/pcf_import/apply',
                {'csv': text, 'fingerprint': other['fingerprint'], 'decisions': {}},
            )[0],
            409,
        )

    def test_deletion_removes_all_owned_data_and_keeps_other_environments(self):
        _, retained = self.request('POST', '/api/streams', {'name': 'Retained'})
        second = self.create()
        ident = second['id']
        _, tag = self.request('POST', '/api/tags', {'name': 'Tag'})
        _, cat = self.request('POST', '/api/tag_categories', {'name': 'Category'})
        parent = None
        ids = []
        for kind in app.PCF_TYPES:
            _, entry = self.request(
                'POST',
                '/api/pcf_entries',
                {'name': kind, 'entry_type': kind, 'number': 1, 'parent_id': parent},
            )
            parent = entry['id']
            ids.append(parent)
        _, stream = self.request(
            'POST', '/api/streams', {'name': 'Removed', 'tag_ids': [tag['id']], 'pcf_ids': ids}
        )
        _, step = self.request('POST', '/api/steps', {'name': 'Step', 'stream_id': stream['id']})
        _, process = self.request(
            'POST', '/api/processes', {'name': 'Process', 'tag_ids': [tag['id']]}
        )
        self.request('PUT', f"/api/steps/{step['id']}/processes", {'process_ids': [process['id']]})
        self.assertEqual(
            self.request('DELETE', f'/api/environments/{ident}', {'confirmation': 'wrong'})[0], 400
        )
        self.assertEqual(
            self.request('DELETE', f'/api/environments/{ident}', {'confirmation': second['name']})[
                0
            ],
            200,
        )
        with app.connect() as conn:
            for table in app.RESOURCES:
                self.assertEqual(
                    conn.execute(
                        f'SELECT count(*) FROM {table} WHERE environment_id=?', (ident,)
                    ).fetchone()[0],
                    0,
                    table,
                )
            self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
        self.assertEqual(self.request('GET', '/api/streams/' + str(retained['id']))[0], 200)
        self.assertGreater(self.create('After deletion')['id'], ident)
        app.initialize()
        self.assertEqual(len(self.request('GET', '/api/environments')[1]), 2)

    def test_creation_requires_csrf_and_accounts_cannot_orphan_environments(self):
        self.assertEqual(
            self.request('POST', '/api/environments', {'name': 'Denied'}, client=self.new_client())[
                0
            ],
            401,
        )
        self.assertEqual(
            self.request(
                'POST', '/api/environments', {'name': 'Denied'}, headers={'X-CSRF-Token': ''}
            )[0],
            403,
        )
        user, viewer = self.account()
        self.create('Viewer owned', viewer)
        self.assertEqual(self.request('DELETE', '/api/users/' + str(user['id']), {})[0], 409)

    def test_last_environment_can_be_deleted_without_reappearing(self):
        environment = self.request('GET', '/api/environments')[1][0]
        self.assertEqual(
            self.request('DELETE', '/api/environments/1', {'confirmation': environment['name']})[0],
            200,
        )
        app.initialize()
        self.assertEqual(self.request('GET', '/api/environments')[1], [])
        self.assertIsNone(self.request('GET', '/api/session')[1]['environment'])
        self.assertEqual(self.request('GET', '/api/data')[0], 409)
        created = self.create('Fresh start')
        self.assertGreater(created['id'], 1)
        self.assertEqual(self.request('GET', '/api/session')[1]['environment']['role'], 'Admin')

    def test_legacy_database_migrates_ids_links_catalogs_and_sessions(self):
        from pathlib import Path
        from unittest import mock

        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        with updater.database(legacy) as conn:
            conn.executescript(
                '''
                CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,role TEXT);
                CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,csrf_token TEXT,expires_at REAL);
                CREATE TABLE streams(id INTEGER PRIMARY KEY,name TEXT,customer TEXT DEFAULT '',outcome TEXT DEFAULT '',description TEXT DEFAULT '',voc TEXT DEFAULT '');
                CREATE TABLE steps(id INTEGER PRIMARY KEY,stream_id INTEGER REFERENCES streams(id) ON DELETE CASCADE,name TEXT,owner TEXT DEFAULT '',position INTEGER DEFAULT 0,work_minutes REAL DEFAULT 0,wait_minutes REAL DEFAULT 0);
                CREATE TABLE processes(id INTEGER PRIMARY KEY,name TEXT,owner TEXT DEFAULT '',description TEXT DEFAULT '',procedure TEXT DEFAULT '');
                CREATE TABLE tag_categories(id INTEGER PRIMARY KEY,name TEXT UNIQUE COLLATE NOCASE);
                CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT UNIQUE COLLATE NOCASE,color TEXT,category_id INTEGER REFERENCES tag_categories(id) ON DELETE SET NULL);
                CREATE TABLE pcf_entries(id INTEGER PRIMARY KEY,name TEXT,entry_type TEXT,parent_id INTEGER REFERENCES pcf_entries(id) ON DELETE RESTRICT,number INTEGER,reference TEXT DEFAULT '',description TEXT DEFAULT '',UNIQUE(parent_id,number));
                CREATE UNIQUE INDEX pcf_category_number ON pcf_entries(number) WHERE parent_id IS NULL;
                CREATE TABLE stream_pcf(stream_id INTEGER REFERENCES streams(id) ON DELETE CASCADE,pcf_id INTEGER REFERENCES pcf_entries(id) ON DELETE CASCADE,PRIMARY KEY(stream_id,pcf_id));
                CREATE TABLE step_processes(step_id INTEGER REFERENCES steps(id) ON DELETE CASCADE,process_id INTEGER REFERENCES processes(id) ON DELETE CASCADE,PRIMARY KEY(step_id,process_id));
                CREATE TABLE streams_tags(entity_id INTEGER REFERENCES streams(id) ON DELETE CASCADE,tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,PRIMARY KEY(entity_id,tag_id));
                INSERT INTO users VALUES (1,'legacy-admin','unused','Admin');
                INSERT INTO users VALUES (2,'legacy-viewer','unused','Viewer');
                INSERT INTO sessions VALUES ('legacy-token',1,'csrf',9999999999);
                INSERT INTO streams(id,name) VALUES (7,'Legacy service');
                INSERT INTO steps(id,stream_id,name) VALUES (9,7,'Legacy step');
                INSERT INTO processes(id,name) VALUES (11,'Legacy procedure');
                INSERT INTO tag_categories VALUES (20,'Service type');
                INSERT INTO tags VALUES (21,'Critical','#336699',20);
                INSERT INTO pcf_entries(id,name,entry_type,number) VALUES (13,'IT Management','Category',1);
                INSERT INTO pcf_entries(id,name,entry_type,parent_id,number) VALUES (14,'Group','Process Group',13,1);
                INSERT INTO stream_pcf VALUES (7,14);
                INSERT INTO step_processes VALUES (9,11);
                INSERT INTO streams_tags VALUES (7,21);
            '''
            )
        with mock.patch.object(app.storage, 'DB', legacy):
            app.initialize()
            app.initialize()
            with app.connect() as conn:
                snapshot = app.ContentRepository(conn, 1).snapshot()
                self.assertEqual(snapshot['streams'][0]['id'], 7)
                self.assertEqual(snapshot['streams'][0]['tag_ids'], [21])
                self.assertEqual(snapshot['streams'][0]['pcf_ids'], [14])
                self.assertEqual(snapshot['pcf_entries'][-1]['index'], '1.1')
                self.assertEqual(
                    conn.execute('SELECT count(*) FROM environment_members').fetchone()[0], 2
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT environment_id FROM session_environments WHERE token_hash='legacy-token'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(list(conn.execute('PRAGMA foreign_key_check')), [])
            # Match an older supervisor deleting sessions without PRAGMA foreign_keys.
            with updater.database(legacy) as conn:
                conn.execute('DELETE FROM sessions')
            with app.connect() as conn:
                self.assertEqual(
                    conn.execute('SELECT count(*) FROM session_environments').fetchone()[0], 0
                )
