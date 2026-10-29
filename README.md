# VSM Toolbox

A local web application for mapping IT service value streams and documenting the processes that deliver them. Python 3.11+ and SQLite are the only runtime requirements; there are no third-party Python packages, external databases, or CDNs. Normal application use stays local; optional Admin updates contact GitHub.

## Features

- Create value streams with a customer, desired outcome, and description.
- Explore an interactive diagram with delivery steps, customer outcomes, and all linked processes. Pan, drag cards, zoom with the mouse wheel or touch pinch, and fit the whole map to the screen.
- Use Edit flow to manage delivery steps and reorder their sequence.
- Assign responsible teams and record active work and waiting time in minutes.
- See total lead time, work time, and flow efficiency for each map.
- Maintain a shared process library with owners, descriptions, and work instructions.
- Link multiple processes to each step and reuse processes across streams.
- Read, create, update, and delete data through an authenticated JSON API.
- Create local accounts and assign Admin, Editor, or Viewer access.
- Check and install the latest GitHub main revision from the Administrator menu.

For example, map **Restore customer service**: report incident → triage → investigate → restore service → confirm resolution. Link incident classification, escalation, and service restoration procedures to the appropriate steps. Maps currently represent a linear current-state journey; branching, parallel flows, version history, and collaborative editing are future extensions.

## Development

```sh
python3 updater.py
# In another terminal:
python3 -m unittest discover -s tests -v
```

Open port 8080 in your browser. The default bind address is `127.0.0.1`. The application creates `data/vsm.sqlite3` on first launch. Create the first Admin account in the browser. `python3 app.py` also supports development without the update supervisor; web updates will be unavailable in that mode. No initial example data is inserted. Set `VSM_HOST`, `VSM_PORT`, and `VSM_DATABASE` to override defaults.

## Interactive value stream diagrams

Open a value stream and select **Diagram**. Solid green arrows show the delivery sequence, and dashed blue connectors link steps to their supporting processes. Every process linked to a step in the selected stream is included. A shared process appears once with connectors to all relevant steps.

- Drag empty background to pan; drag a card to arrange the visual. Connectors follow the cards.
- Use **+**, **−**, the mouse wheel, or a two-finger pinch to zoom. Zoom follows the pointer or pinch center.
- Use **Fit to screen** to see the complete map, or **Reset layout** to restore the automatic arrangement.
- Double-click a step or process, or focus it and press Enter, to open its details. Existing Admin/Editor/Viewer permissions still apply.
- With the diagram focused, arrow keys pan, **+ / −** zoom, and **0** fits. With a card focused, arrow keys move it; Shift moves it farther.

Visual layout and zoom are saved per account and stream in the current browser. They do not change shared data, process links, or delivery order. Viewers can explore and arrange their own visual without gaining editing access. Use **Edit flow** to change the actual step sequence and manage process links. Layouts are personal to a browser, rather than shared server layouts.

Optional browser integration checks run against a temporary database and server, never your existing workspace. With Playwright and its Chromium browser installed in your development environment, run `node tests/diagram.cjs`. `VSM_CHROMIUM` can select an existing Chromium binary, and `PYTHON` can select the Python executable. This checks pan, drag, zoom, fit/reset, keyboard access, process connectors, layout persistence, Viewer restrictions, and touch pinch. Playwright is a development check only; the deployed application still has no third-party runtime dependencies.

## Install on Windows

Supports Windows 10/11 and Windows Server with a machine-wide, 64-bit Python 3.11 or newer installation from python.org (not the Microsoft Store alias or a user-only installation). Python must be readable by Windows LocalService. The web application uses only Python's standard library and local SQLite; the Windows service host uses `pywin32==311`. No external database or container runtime is needed.

Download/extract this repository to a local directory. In an elevated Windows PowerShell 5.1 or newer, run from that checkout:

```powershell
.\scripts\install-windows.ps1 -Python 'C:\Program Files\Python313\python.exe'
# To allow other machines on the local subnet (Domain/Private firewall profiles):
.\scripts\install-windows.ps1 -Python 'C:\Program Files\Python313\python.exe' -AllowPrivateNetwork
```

Use one of these installation commands, not both. Supply the actual path to your Python executable. Installation needs Internet access to PyPI for pywin32; normal application startup and use work offline. The installer validates the payload, installs the service host dependency, copies the runtime, registers **VSMToolbox** for automatic startup as the built-in **LocalService** account, configures restart recovery, starts it, and checks `/api/health`. It refuses to overwrite an existing installation or service. On failure it retains staged files for diagnosis and does not delete data. Do not point its directories at existing unrelated folders.

Open **http://localhost:8080** on the Windows machine, or **http://<server-name-or-IP>:8080** from permitted network clients. The service listens on all IPv4 interfaces on HTTP port 8080. Without `-AllowPrivateNetwork`, the installer does not add a firewall exception. Complete first Admin registration locally before permitting remote clients. HTTP does not encrypt passwords or traffic; restrict access to a trusted network. Secure-cookie mode is disabled for this HTTP deployment.

| Local path | Purpose |
|---|---|
| `%ProgramFiles%\VSMToolbox` | Installed application, stable service host, `service.json` |
| `%ProgramData%\VSMToolbox\vsm.sqlite3` | Accounts and all shared application data |
| `%ProgramData%\VSMToolbox\updates` | Downloaded releases, activation records, local backups |
| `%ProgramData%\VSMToolbox\service.log` | Rotating service lifecycle/error log |
| `%ProgramData%\VSMToolbox\application.log` | Web process output; archive periodically while stopped |

The installer restricts the data directory to Administrators, SYSTEM and LocalService, and gives LocalService read/execute access to installed code. `-InstallDirectory` and `-DataDirectory` accept alternate absolute local paths. Keep data on a local fixed disk, outside the application directory and outside cloud-synchronized folders. Nothing uploads application data to an external service. Diagram layout preferences remain in each user's browser, as before. Optional Admin application updates only download code from GitHub.

```powershell
Get-Service VSMToolbox
Stop-Service VSMToolbox
Start-Service VSMToolbox
Restart-Service VSMToolbox
Invoke-RestMethod http://localhost:8080/api/health
```

For an installer/service-engine upgrade: stop the service, keep an external backup of the entire data directory, run `scripts/uninstall-windows.ps1`, archive the old installation directory, then install the new checkout with the same data directory. Existing database contents and backups are retained. A stored `updates/active.json` intentionally keeps the selected web release; archive that file while stopped if deliberately selecting the new bundled application. Never discard data to resolve an upgrade problem. Moving an existing database from another system requires copying it while the old server is stopped (including its SQLite journal files if present), then verifying the new installation before retiring the old one. Existing updates and full-code backups may contain platform-specific older code; keep them for recovery on their original platform.

Uninstall the service and its managed firewall rule from an elevated PowerShell:

```powershell
.\scripts\uninstall-windows.ps1
```

Uninstallation preserves Python, installed application files, all data, and backups. Remove archived program files manually only after checking your backups. Service failures appear in Windows Event Viewer and the local logs. Check Python availability/permissions and whether another process owns port 8080 if startup fails.

## Local accounts and permissions

On an empty accounts database, the sign-in page offers **Create your Admin account**. The first successful registration becomes Admin, and public registration then closes. Registration is serialized so simultaneous requests cannot create multiple first accounts. Existing value streams and processes remain intact when upgrading from a version without accounts. Complete initial registration before opening access to other users.

Open **Administrator → Account administration** to create accounts or use **Account Overview** to edit usernames, reset passwords, assign a role, and remove accounts. Each account has one of these predefined roles:

| Role | Access |
|---|---|
| Admin | All content, local account management, and application updates |
| Editor | Read, create, edit and remove streams, steps and processes |
| Viewer | Read streams and process documentation only |

Usernames are case-insensitive and contain 3–64 letters, numbers, dots, underscores or hyphens. Passwords contain 12–128 characters and are stored as individually salted PBKDF2-SHA256 hashes (600,000 iterations). Passwords are never returned by the API. There are no external identity providers or default credentials. Admins reset passwords locally; automated email recovery is not included. The last Admin cannot be removed or demoted.

Sessions last up to 12 hours and use opaque HttpOnly, SameSite=Strict cookies. Password, username or role changes revoke that account's sessions; removal immediately revokes access. Sign-in attempts are limited to ten failures per client IP per five-minute window. Application mutations require both an authenticated session and its CSRF token; read-only controls are also enforced by the API.

The Windows service provides HTTP on port 8080. Restrict access to trusted clients through Windows Firewall. HTTP carries credentials and content without transport encryption. No external identity provider or data service is required.

## Updates from the Administrator menu

Open **Administrator → Application updates**, select **Check for updates**, then **Update from main**. This targets the latest commit published to the repository's `main` branch, rather than the latest GitHub Release tag. Only Admins can request or view updates; the source repository and branch cannot be overridden through the API.

The supervisor checks the GitHub commit, downloads that exact revision over verified HTTPS, validates the supported update manifest and Python syntax, stops the web process, and creates a self-contained, integrity-checked application-and-database backup before switching code. Failure to create the backup cancels installation. It starts the new application, checks its revision and health, and revokes existing sessions after success. Sign in again after installation. If startup fails, it reconstructs the previous application **and database from the backup archive**, even when the original code directory is missing. A persistent recovery record also allows the supervisor to recover an interrupted installation on its next start. Writes pause during installation; checking and downloading leave the application available.

Update code, activation state and backups live in `%ProgramData%\VSMToolbox\updates` beside the database. Retain this directory with the database; activation survives service restarts. Old releases and backups are retained; monitor storage and keep external backups.

GitHub access to `api.github.com` and `codeload.github.com` is required only for checking/downloading updates from `CoxkeLamagra/VSM-Toolbox-Win/main`. Normal local use stays offline. Public repositories use anonymous access. For private repositories, an administrator may configure `VSM_GITHUB_TOKEN` in the service process environment and restart it; never store tokens in the checkout or browser. The token is sent only to GitHub API requests and never forwarded to redirected hosts. Incompatible runtime/dependency changes require a Windows service-engine upgrade.

Service-engine upgrades use the Windows maintenance procedure above. Compatible application updates use the Admin interface.

## API

All requests and responses use JSON. IDs are integers; updates accept a subset of the documented fields. Successful creation returns 201, reads/updates/deletes return 200, update requests return 202, invalid requests return 400, unauthenticated requests return 401, insufficient permissions return 403, missing resources return 404, account/update conflicts return 409, and throttled sign-ins return 429. Send `Content-Type: application/json` for every POST, PUT or DELETE (an empty body can be `{}`).

First sign in with `POST /api/auth/login`, retain the returned session cookie, and pass the response's `csrf_token` in `X-CSRF-Token` on each mutation. `GET /api/session` retrieves the current user and CSRF token. Except for health and session status, all data reads require a session; all content writes require Admin or Editor. Account and update operations require Admin.

| Route | Methods | Fields / purpose |
|---|---|---|
| `/api/health` | GET | Public health, running revision and update protocol |
| `/api/session` | GET | Current account, CSRF token, and whether first registration is required |
| `/api/auth/register` | POST | First account only: `username`, `password`; always creates Admin |
| `/api/auth/login` | POST | `username`, `password`; sets cookie and returns CSRF token |
| `/api/auth/logout` | POST | Revokes current session |
| `/api/users` | GET, POST | Admin account list/create: `username`, `password`, `role` (defaults to Viewer) |
| `/api/users/{id}` | GET, PUT, DELETE | Admin account read/edit/remove; omit password on edit to keep it |
| `/api/roles` | GET | Admin-only list of assignable roles |
| `/api/updates` | GET | Admin-only installed/latest revision, operation status and availability |
| `/api/updates/check` | POST | Admin-only queue a GitHub check; empty object |
| `/api/updates/install` | POST | Admin-only queue update from main; empty object |
| `/api/data` | GET | Complete workspace including links |
| `/api/streams` | GET, POST | `name` (required), `customer`, `outcome`, `description` |
| `/api/streams/{id}` | GET, PUT, DELETE | Read/update/delete a stream; deletion cascades to steps |
| `/api/steps` | GET, POST | `name`, `stream_id` (required), `owner`, `position`, `work_minutes`, `wait_minutes` |
| `/api/steps/{id}` | GET, PUT, DELETE | Read/update/delete a step |
| `/api/processes` | GET, POST | `name` (required), `owner`, `description`, `procedure` |
| `/api/processes/{id}` | GET, PUT, DELETE | Read/update/delete a process |
| `/api/steps/{id}/processes` | PUT | Replace links with `{"process_ids": [1, 2]}`; empty list unlinks all |

```sh
# After logging in and saving its cookie in session.cookies and CSRF token in CSRF_TOKEN:
curl -X POST http://127.0.0.1:8080/api/streams \
  --cookie session.cookies \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Restore customer service","customer":"Managed service customer","outcome":"Service restored"}'
```

Work/wait times must be finite nonnegative numbers in minutes. Flow efficiency is work time divided by work plus waiting time. A process can link to many steps; deleting a stream preserves its processes. Reordering saves positions through step updates. This version assumes one editor at a time; simultaneous edits are not conflict managed.

## Backups and restore from the Admin interface

Open **Administrator → Backups & restore**. Only Admin accounts can list or download backups, create manual backups, upload, restore, or delete backup files. All write actions require the session's CSRF token. Manual backup and restore work offline, without GitHub access.

- **Create manual backup** keeps the web server running and uses SQLite’s online backup API to produce a `.vsm-backup.zip` containing all manifest-declared application runtime files, the SQLite database (including local accounts), revision metadata, and checksums. Writes pause during capture; reads remain available.
- **Download** saves a server backup to your computer. Keep copies on another system to protect against disk failure.
- **Restore** beside a saved backup restores that application's code, accounts, and database. The selected archive is validated before the application stops. A full **pre-restore safety backup** captures the current state before any replacement; failed startup restores that safety snapshot.
- **Upload and restore** accepts a trusted `.vsm-backup.zip` (maximum 128 MiB compressed and unpacked). Archive paths, regular-file types, exact file membership, checksums, Python syntax, runtime compatibility, SQLite integrity/schema and the presence of an Admin account are checked. Arbitrary archive paths and symlinks are never extracted. Invalid uploads are rejected without changing the running workspace.
- **Delete selected backups** permanently removes only explicitly selected files after confirmation. Backups referenced by an active recovery record are protected. Legacy database-only files in the application's backup directory can be downloaded or deleted, but are not offered as full application restore points.

A successful restore revokes sessions. Sign in using an account and password contained in the restored backup; newer accounts and data are replaced by the selected snapshot. Full backups contain executable application code and local account hashes: restore only trusted backups and protect downloaded files. Checksums detect corruption, not maliciously modified archives. The service remains unprivileged throughout.

Archives are retained under `%ProgramData%\VSMToolbox\updates\backups` with the data directory ACL. No automatic retention deletion is applied. Backups exclude Python, service registration/settings, environment secrets, browser-only layouts, logs, and other backup archives.

Every UI update which proceeds to installation first creates a full **pre-update** snapshot. Download/validation failures leave the current application running; backup failures prevent code activation. Failed activation, and recovery after an interrupted activation, restore code and data from the snapshot. For both updates and restores, the recovery record remains until the new application passes its health check. This protects data even if the previous source directory has been removed.

Install the current Windows service engine using the maintenance procedure above. Web-only updates do not replace the running supervisor. Maintenance controls reject engines that lack the required backup capability.

Additional API routes:

| Route | Method | Purpose |
|---|---|---|
| `/api/backups` | GET | Admin-only backup list, supervisor capability and operation status |
| `/api/backups/create` | POST | Queue a full manual snapshot; `{}` |
| `/api/backups/{id}/download` | GET | Download a saved backup |
| `/api/backups/upload` | POST | Validate and retain a raw archive; `Content-Type: application/octet-stream`, returns its `id` |
| `/api/backups/restore` | POST | Queue restoration; `{"id":"...vsm-backup.zip"}` |
| `/api/backups/cleanup` | POST | Queue deletion of selected IDs; `{"ids":["..."]}` |

Queued operations return 202. Concurrent maintenance requests are refused, and mutations pause during capture, installation and restoration. Uploaded archives can be retained even if the subsequent restore request is cancelled or fails; they remain visible for later selection or cleanup.

Browser integration checks for the complete backup workflow run with `node tests/backups.cjs` when Playwright/Chromium are installed (same `VSM_CHROMIUM` and `PYTHON` options as the diagram test). They start their own supervisor, database and backup storage, leaving the development workspace unchanged. Run `node tests/maintenance.cjs` to check HTML gateway error handling and repeated restart polling without browser dependencies.

### Advanced database-only backups

SQLite's backup API remains available for external database-only backup jobs. These files do not contain application code and cannot be uploaded as complete application backups:

```sh
python3 - <<'PY'
import sqlite3
with sqlite3.connect('C:/ProgramData/VSMToolbox/vsm.sqlite3') as source:
    with sqlite3.connect('C:/ProgramData/VSMToolbox/manual-backup.sqlite3') as target:
        source.backup(target)
PY
```

Run with permission to read/write those paths and restrict backup permissions. Restore database-only files while the service is stopped and ensure the application version matches the database schema.


### Branding and theme

Open **Administrator → Branding & theme** to set the application name, workspace tagline, local logo (PNG/JPEG, up to 128 KiB), accent color, navigation background, and page background. Save applies branding across the workspace and sign-in page; other users see it on their next refresh. **Restore default theme** resets all branding fields, including the logo.

The default uses navy navigation, white surfaces, and a coral-red accent, inspired by the requested Cegeka reference. It uses system fonts and makes no external asset requests. Text on accent buttons and navigation automatically selects a contrasting light or dark color. Brand settings and embedded logo are stored in SQLite, included in application backups, and recovered by restoring a backup. Restoring a backup made before this feature returns to default branding.

`GET /api/branding` is public so the sign-in page can display branding; it exposes only public branding and defaults. `PUT /api/branding` requires an Admin session and CSRF token and accepts the complete `name`, `tagline`, `primary`, `sidebar`, `background`, and `logo` object. Colors use `#RRGGBB`; logo is an empty string or a PNG/JPEG base64 data URL. Custom CSS, SVGs, and external logo URLs are not accepted.

Validation: `python3 -m unittest discover -s tests -v` includes branding authorization, input validation, and persistence. Run `node tests/branding.cjs` with Playwright and Chromium (using the same environment options as the existing browser tests) to verify saving branding, logo rendering, theme application, sign-in branding, and mobile layout.


### Voice of the Customer (VOC)

Create or edit a value stream and fill in **Voice of the Customer (VOC)** to record what the customer is actually requesting you to deliver, preferably in their own words. For example: “Give each new starter working access to email, business applications, and secure remote access on their first day.”

When supplied, VOC appears as the final draggable node after the customer outcome in the diagram. The full request appears below both the diagram and Edit flow, including for streams without steps. Admins and Editors can use **Add customer request / Edit customer request**; Viewers can read it. The optional `voc` string is supported by the stream POST, PUT, and GET APIs. Existing databases migrate automatically with an empty VOC field. VOC is stored in SQLite and included in backups.


### Tags and categories

Open **Administrator → Tags & categories** to create, rename, recolor, categorize, or remove shared tags, and to create, rename, or delete categories. Only Admins can change the catalog. Deleting a category moves its tags to **Uncategorized**; deleting a tag removes its assignments without deleting any streams, steps, or processes. Tag and category names must be unique (case insensitive) and contain 1–80 characters.

Admins and Editors can select existing tags in the create/edit forms for value streams, delivery steps, and processes. Viewers can read assigned tags. Tags are grouped by category in the selector. Stream and process cards, stream details, and Edit flow show colored badges; diagram cards show up to two tag badges, with all names in their tooltip and full assignments in the details dialog. A stream's overview icon uses the first assigned tag alphabetically (case insensitive, using the browser locale); removing its last tag restores the default icon. Badge text selects a contrasting light or dark color.

The catalog, colors, categories, and assignments stay in SQLite and are included in backups. Existing workspaces gain empty tag tables automatically. `GET /api/tags` and `GET /api/tag_categories` require sign-in; their POST/PUT/DELETE operations require Admin and CSRF. Tags accept `name`, `color` (`#RRGGBB`), and optional `category_id` (integer or null). Stream, step, and process POST/PUT requests accept `tag_ids` as an array of existing integer tag IDs. Supplying an empty array clears assignments; omitting it preserves them. The response includes `tag_ids`. Content and assignments save in one transaction.

Tag tests run with the Python regression suite and `node tests/tags.cjs` (using the existing Playwright/Chromium environment options). They cover catalog permissions, all assignment types, validation, cleanup, color updates, and backup restoration.


### Architecture and development

See [Architecture and refactor notes](docs/architecture.md) for the current responsibilities, resource and page registries, transaction boundaries, deployment compatibility constraints, and verification commands.


### Update error: download exceeds the size limit

The updater previously fetched `/commits/main` to identify the newest revision. GitHub includes changed-file patches in that response, so a large commit could exceed the 100,000-byte metadata limit before the application archive was downloaded. The updater now reads the compact `/git/ref/heads/main` reference and validates its commit SHA. Application archive and extracted-size limits remain in place.

Install the current Windows service engine using the maintenance procedure above. Web-only updates do not replace the running supervisor. Maintenance controls reject engines that lack the required backup capability.

### PCF framework

Open **PCF framework** in the workspace menu to maintain a manually entered APQC Process Classification Framework. The screen starts empty; it does not download or ship APQC reference content. Admins and Editors can add, edit, move, renumber, and remove entries; Viewers can browse both views.

The hierarchy is enforced:

| Entry type | Required parent |
| --- | --- |
| Category | None; top level |
| Process Group | Category |
| Process | Process Group |
| Activity | Process |
| Task | Activity |

Each entry has a name, a positive **Index number** unique among siblings, an optional APQC reference ID, and a description. Categories can be numbered explicitly. The displayed index combines each entry's number with its ancestors, for example `1 → 1.1 → 1.1.1 → 1.1.1.1 → 1.1.1.1.1`. Changing a number or moving an entry recalculates its descendants' displayed indexes; stable entry IDs preserve existing stream links. Entries with children must have those children moved or removed before deletion. Removing a leaf entry removes its stream links without deleting streams.

- **Index** displays an expandable hierarchy. Select **Details / drill down** to read an entry and its direct stream links.
- **Tiles** displays the current level. Select a tile to drill down; use the breadcrumb to return to an ancestor or **All categories**.
- **Add PCF entry** lets you choose any of the five types and an existing parent of the required type. **Add Process Group / Process / Activity / Task** in an entry's detail screen preselects its parent.

Create or edit a value stream to select several **Linked PCF entries** at any level. Use the search field to filter by index, name, or reference; filtering does not clear checked entries. The stream's **PCF classifications** panel opens the linked classifications. A PCF detail screen lists value streams linked directly to that entry. Links do not implicitly select ancestors or descendants. PCF classifications are separate from the existing reusable process library and tags.

All hierarchy data and links remain in SQLite and are included in application backups. Existing installations gain the new tables automatically, preserving current content. No additional runtime files or dependencies are required.

API: authenticated `GET /api/pcf_entries` and `GET /api/pcf_entries/{id}` return `id`, `name`, `entry_type`, `parent_id`, `number`, `reference`, `description`, and computed `index`. Admins and Editors can POST, PUT, and DELETE with their CSRF token. Example Category body:

```json
{"name":"IT Management","entry_type":"Category","parent_id":null,"number":1}
```

For a Process Group, use `"entry_type":"Process Group"` and the Category's ID as `parent_id`. The same rule applies at each lower level. Stream POST/PUT requests accept `pcf_ids`, an array of existing PCF entry IDs. Omitting it preserves links; `[]` clears them. Responses include `pcf_ids`; `/api/data` also includes `pcf_entries`. Content, tags, and PCF links save in one transaction.

Run the Python suite for hierarchy, numbering, migration, permission, linking, and restore checks. `node tests/pcf.cjs` exercises the complete five-level hierarchy, both views, stream links, search, renumbering, and Viewer/mobile access using the existing Playwright/Chromium environment options.

### Import PCF entries from CSV

Select **Import from CSV** next to **Add PCF entry** on the PCF framework screen, choose a file, and select **Upload and preview**. Import is available to Admins and Editors; Viewers cannot preview or apply uploads. Required column names (surrounding spaces are ignored):

| CSV column | Application field |
| --- | --- |
| `PCF ID` | APQC reference ID |
| `Hierarchy ID` | Full hierarchy index; its last component becomes the entry's sibling number |
| `Name` | Entry name |
| `Description` (optional) | Entry description; quoted multiline text is supported |

```csv
PCF ID,Hierarchy ID,Name,Description
10001,1.0,IT Management,Manage IT services and capabilities.
10002,1.1,Fault to Restoration,Restore service after a fault.
10003,1.1.1,Incident Management,Coordinate incident resolution.
10004,1.1.1.1,Register Incident,Record the reported incident.
10005,1.1.1.1.1,Create ticket,Create a service desk ticket.
```

One through five numeric components identify Category, Process Group, Process, Activity, and Task respectively. A top-level APQC index such as `1.0` is accepted as category `1`; ordinary `1` is also accepted. Parents must be included in the CSV or already exist at the requested index. Rows can be in any order. The importer supports UTF-8 (with or without BOM), quoted fields and multiline names, and comma/semicolon/tab separators. Files are limited to 256 KiB and 5000 entries. PCF ID and Name are required and limited to 200 characters; numbers must be positive integers up to 999999. Description is optional and limited to 20000 characters. Other extra columns are ignored.

The preview makes no changes. APQC reference IDs are compared after trimming whitespace, without case sensitivity. Each duplicate shows the existing entry and the incoming name/index with a **Yes / No** overwrite selector. **Overwrite ALL** sets every duplicate to Yes; **Skip ALL** sets every duplicate to No. Individual choices can still be changed afterward. Select **Import entries** to save new entries and apply your choices.

- **Yes** updates the existing name, reference, hierarchy number, and parent. Its ID and value stream links are preserved. When the optional Description column is present, it replaces the description; a blank value clears it. If the column is absent, the existing description is preserved.
- **No** retains the existing entry. Imported children referencing that CSV parent attach to the retained entry, so their resulting index follows its actual location.
- New reference IDs are created. A different reference already occupying the requested index is never silently overwritten; the whole import stops with an error.
- Repeated references or normalized indexes within the file, ambiguous existing reference IDs, missing parents, and incompatible final hierarchies are rejected. Resolve those ambiguities before importing.
- A successful import is atomic. If validation or writing fails, all its changes roll back. If any PCF entry changed after preview, preview again before applying. CSV text is processed locally and is not retained as an uploaded file; the resulting entries remain in SQLite and normal application backups.

API endpoints: `POST /api/pcf_import/preview` accepts `{"csv":"..."}` and returns totals, duplicate details, and a framework `fingerprint`. `POST /api/pcf_import/apply` accepts the same CSV, that fingerprint, and a `decisions` object mapping every duplicate's incoming reference ID to `"yes"` or `"no"`. Both endpoints require an Admin/Editor session and CSRF token. The apply response contains `created`, `updated`, and `skipped` counts.

Tests: `python3 -m unittest discover -s tests -v` and `node tests/pcf-import.cjs` using the existing browser-test environment options.

### Clear all business data

Admins can open **Administrator → Data management** to clear all value streams, their delivery steps / process flows, process-library entries, PCF classifications, and their links. **Accounts, sessions, branding, tag definitions/categories, and existing backup files are retained.** Editors and Viewers cannot access or execute the action.

The action requires typing **DELETE ALL BUSINESS DATA** and accepting an additional final warning. The supervisor stops the web process, creates a full local application-and-database backup with reason `pre-data-clear`, records recovery information, and only then deletes business content in a database transaction. If backup creation fails, deletion is not attempted. Failed deletion rolls back; failed startup restores the safety backup. An interrupted operation with a pending recovery record is recovered on supervisor startup. After a successful clear, the saved backup can be restored through **Backups & restore**; restoring also restores its accounts/data and revokes sessions as usual.

The application restarts briefly during clearing, and the UI polls until it returns. Retained records are not deleted and database identifiers are not deliberately reset. This is a logical content reset, not secure erasure; backup archives still contain the previous business data.

Install the current Windows service engine using the maintenance procedure above. Web-only updates do not replace the running supervisor. Maintenance controls reject engines that lack the required backup capability.

API: Admin-only `GET /api/maintenance` reports operation status and capability. `POST /api/maintenance/clear-data` requires an Admin session, CSRF token, and exactly `{"confirmation":"DELETE ALL BUSINESS DATA"}`; it queues the backup-first supervisor operation and returns HTTP 202. Normal writes pause while the clear runs. The shared maintenance queue prevents simultaneous update, backup, restore, cleanup, or clear operations.

Verification: the Python regression suite includes backup failure, transaction rollback, failed-startup recovery, retained configuration, restoration, role, CSRF, and exact-confirmation checks. `node tests/data-management.cjs` uses a disposable local instance to verify warning cancellation, backup creation, clearing, full restoration, and Viewer restrictions.

### Environments

Open **Environments** to manage separate local workspaces. Each environment contains its own value streams, delivery steps / process flows, process library, PCF hierarchy, tags, categories, and all related assignments. The header's **Environment** selector switches the current session; the dashboard's environment cards offer the same action. The dashboard shows counts for value streams, steps, processes, PCF entries, tags, and members.

Any signed-in local account can create an environment and immediately becomes its **environment Admin**, even if its application role is Viewer. New environments are private to their creator until members are added. In the active environment's dashboard, an environment Admin can add existing local usernames as **Admin**, **Editor**, or **Viewer**, change those roles, and remove members. The final environment Admin cannot be removed or demoted. Application account deletion is also blocked when it would orphan an environment. Ask an application Admin to create a local account before inviting a new person.

Environment permissions:

| Role | Capabilities within that environment |
| --- | --- |
| Admin | Manage content, tags, categories, membership, environment details, and delete the environment |
| Editor | Create/edit/delete streams, steps, processes, PCF entries, CSV imports, and assignments |
| Viewer | Browse the dashboard and read assigned content |

**Application roles remain separate.** Application Admins retain account management, branding, full backups/restores, updates, and the global clear-business-data action. Environment Admin status alone does not grant those application-wide privileges. Only environments where the user is a member appear in the overview or switcher. The header's `App` badge describes the account role; the dashboard and environment cards describe the environment role.

Upgrading automatically assigns existing data to **Default environment**, preserving record IDs, links, tags, descriptions, and existing default-environment diagram layouts. Existing users receive membership there according to their account roles. These legacy memberships, and initial access given when an application Admin creates an account while administering the selected environment, follow the account role until an environment Admin explicitly sets a membership role. Explicit membership roles and creator Admin status are independent of account role changes. New environments can reuse tag/category names, APQC reference IDs, and PCF category numbers independently.

Only an environment Admin sees **Delete environment**. Deletion requires the confirmation warning and removes that environment's streams, steps, processes, PCF entries, tags, categories, memberships, and assignments in one transaction. It preserves local accounts, branding, backups, and other environments. The final environment can be deleted; the empty dashboard then allows creating another. Deleting environments does not recreate Default environment on restart. Environment IDs are not reused.

CSV preview/import, tag assignment, process linking, dashboards, and content APIs are scoped to the active environment. Cross-environment IDs and relationships are rejected. The frontend sends `X-Environment-ID` to prevent stale tabs from silently writing into a newly selected environment. Diagram layouts and pending CSV previews are separated/reset when switching.

**Backup and maintenance scope:** full backups and restores cover the entire application database, including every environment and its members. Restoring is not limited to the selected environment. The application-wide **Data management** clear action still deletes business content across **all** environments, with its required backup and warnings; environment definitions, memberships, accounts, settings, and tag catalogs remain. Environment deletion is separate from that global action.

Environment API (sign-in required; mutations also require CSRF):

| Endpoint | Behavior |
| --- | --- |
| `GET /api/environments` | Membership-filtered overview with counts |
| `POST /api/environments` | Create with `name` and optional `description`; creator becomes Admin and switches into it |
| `GET /api/environments/{id}` | Read a joined environment |
| `POST /api/environments/{id}/switch` | Switch current session; send `{}` |
| `PUT /api/environments/{id}` | Environment Admin edits name/description |
| `DELETE /api/environments/{id}` | Environment Admin deletes it; requires `{"confirmation":"exact environment name"}` |
| `GET /api/environments/{id}/members` | Environment Admin lists members |
| `POST /api/environments/{id}/members` | Environment Admin adds an existing `username` and `role` |
| `PUT /api/environments/{id}/members/{user_id}` | Environment Admin sets `role` |
| `DELETE /api/environments/{id}/members/{user_id}` | Environment Admin removes membership |

`GET /api/session` includes the active `environment` (`id`, `name`, `description`, effective `role`), or null. Business resource responses include `environment_id`; that field cannot be supplied to reassign resources. API clients should switch explicitly and send `X-Environment-ID` with business requests. A stale environment header returns HTTP 409. Accounts without memberships can list/create environments but must join/select one before calling business-data APIs.

Tests: the Python suite covers populated legacy migration, scope boundaries, cross-environment relationship guards, roles, membership, CSV import scoping, deletion cleanup, and multi-environment backup/restore. `node tests/environments.cjs` exercises the dashboard, switching, creator privileges, invitations, isolated content, deletion, empty-state recovery, and mobile layout.

### BPMN process diagrams

In **Process library**, select **Import BPMN XML** to create a process from a local `.bpmn` or `.xml` file. Existing process cards also provide an import/replace button; replacement preserves their value-stream links, tags, and documentation. Use **View BPMN diagram** to inspect the saved diagram, adjust zoom, and scroll or drag to pan. Environment Admins and Editors can import; Viewers can inspect saved diagrams.

Task labels wrap to the measured box width; gateway/event names sit outside their shapes, and flow labels are placed beside routed segments with collision checks. Long labels show an ellipsis when necessary, with their full text available on hover. Diagram bounds include external labels so they are not clipped.

The local viewer supports BPMN 2.0 tasks, events, gateways, subprocess nodes, sequence flows, and message flows. It preserves available BPMN DI node positions and flow waypoints; missing node positions use a basic grid layout. Pools, lanes, extension graphics, and event-specific symbols are not drawn, and nested subprocess contents appear together. Files are limited to 256 KiB, 500 supported nodes, and 2,000 flows. DTDs and entity declarations are rejected. Imported XML is stored in the environment's local database and included in backups; it is never executed as a workflow.


### Value-add ratings

Value streams and individual delivery steps each have independent **Customer Value Add (CVA)** and **Business Value Add (BVA)** classifications: **3 - High**, **2 - Medium**, **1 - Low**, or **Not rated**. Set or clear them in the stream/step edit forms. Ratings appear on stream cards, stream details, delivery-flow cards, and diagram step cards. Stream ratings are entered directly, rather than calculated from step ratings. Existing records remain unrated. Environment Admins/Editors can edit ratings; Viewers can inspect them.

The stream and step API resources accept optional `cva` and `bva` fields with integer values 1–3 or `null` to clear a rating. Omitting either field on update preserves its current value. The database migration is additive, and local backups retain ratings with their stream/step records.

### Service cycle, lead, and takt time

The value stream uses an explicit Lean service-mapping convention, with all step times entered **per customer request**:

| Metric | Calculation | Meaning |
|---|---|---|
| Cycle time (processing) | Step active work time; stream = sum of step active work | Hands-on processing effort per request, excluding waiting |
| Lead time | Work + wait per step; stream = sum across sequential steps | Mapped elapsed time from request to delivery, including queues/delays |
| Takt time | Net available working minutes ÷ customer requests in the same period | Required delivery pace to match customer demand |

For example, 15 minutes of active work and 30 minutes of waiting produce a processing cycle time of 15 minutes and a lead time of 45 minutes. With 420 net available minutes and demand for 35 requests per day, takt is 12 minutes per request. Takt is a demand target, not the actual cycle or lead time. Available time excludes breaks/planned downtime; use a consistent shift, day, week, or month for both inputs.

Set the optional takt inputs under **Edit details → Takt time — customer demand**. Zero/negative demand or available time is rejected, and takt remains **Not set** when either input is blank. Existing streams stay unconfigured. Step forms preview cycle/lead times as you edit work/wait values. Flow cards and diagram step cards show both; the stream header shows totals, takt, and flow efficiency.

These are estimates from the mapped sequential steps, not timestamp measurements or a working-calendar simulation. Include all relevant waiting in the steps. Parallel work, staffing, request mix, throughput, and bottlenecks require separate analysis; summed processing time alone does not establish delivery capacity. The term cycle time can also mean observed completion intervals in other Lean contexts; this UI labels it **processing** to state its convention clearly.

API stream fields: `takt_available_minutes` and `takt_demand` accept finite positive numbers or `null`; `takt_period` accepts `shift`, `day`, `week`, or `month` (default `day`). Omitted inputs preserve existing values. The calculated cycle/lead values derive from the existing step work/wait times rather than being stored as independent, potentially stale values.

## Runtime and service updates

The modular backend (`vsm/`) and native browser modules (`web/`) retain delivery protocol 2. `update-manifest.json` declares the application payload. The Windows service host is installed separately and remains stable across web updates. Web updates do not replace the running supervisor or service registration; use the Windows maintenance procedure above when those components change. Old exact-profile backups remain readable, but restoring code from before Windows support may require recovery on its original platform.
