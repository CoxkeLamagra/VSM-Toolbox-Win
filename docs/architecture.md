# Application architecture

VSM Toolbox uses Python 3.11+ standard-library modules, SQLite, and browser ES modules. The application needs no database service or frontend build. Windows deployment adds pywin32 for the Windows Service Control Manager host, running as LocalService with local ProgramData storage and HTTP on port 8080.

## Backend

`app.py` is the executable entry point and a small compatibility facade for Python consumers/tests. The implementation lives in `vsm/`:

| Module | Responsibility |
|---|---|
| `config.py` | Version, application root, role/session/password constants |
| `storage.py` | Database configuration, transaction ownership, schema and migrations |
| `auth.py` | Account validation, password hashing/checking, public user projection, API errors |
| `branding.py` | Branding validation, colors, generated theme stylesheet |
| `resources.py` | Resource fields, write roles, nullable fields, text limits, PCF types |
| `repository.py` | Environment-scoped content reads/writes, relationship validation and serialization |
| `bpmn.py` | Bounded XML parsing into normalized diagram data |
| `pcf.py` | CSV parsing, preview/fingerprint, duplicate decisions and atomic import |
| `environments.py` | Membership, environment lifecycle, session selection and last-Admin rules |
| `http.py` | Bounded HTTP serving, routes, sessions/CSRF, transport and maintenance authorization |
| `runtime.py` | Delivery profiles, manifest validation and static asset allowlist |

Storage owns the `DB` setting and `connect()` transaction context. Repository/domain operations receive a caller-owned connection and do not commit independently. Environment relationships are validated both in repositories and SQLite triggers. Application Admin privileges remain separate from environment roles.

`updater.py` remains the portable application supervisor. It owns application processes, pinned-source GitHub updates, backup archives, recovery records, health checks, and maintenance serialization. It uses the same delivery-profile validator as installation and static serving.

## Frontend

`web/app.js` owns the application session, page registry/navigation, request/polling lifecycle, forms and administration orchestration. It imports:

- `lib/core.js`: escaping, form serialization, service timing, ratings, and workspace indexes.
- `features/bpmn.js`: measured wrapping, route construction, collision-aware labels and SVG rendering.
- `features/stream-diagram.js`: interactive diagram controller and private layout cache, with explicit callbacks for session/content access and editing.
- `features/pcf.js`: PCF views, CSV preview/apply, forms and feature-local navigation/import state. The controller resets that state through the feature API when sessions/environments change.

Feature factories receive explicit dependencies; they do not depend on undeclared page globals. ES module imports are served from a manifest-derived allowlist with JavaScript MIME types and the existing same-origin CSP. There is no bundle-generation step. `package.json` marks development JavaScript as modules; Node is only needed for checks, not installation.

`web/style.css` remains the shared theme/layout stylesheet. Its visual refinements and current screen coverage are described in the dated visual-review document; historical references there to the six-file runtime apply to the earlier delivery protocol.

## Delivery protocol 2 and reinstall transition

This refactor deliberately replaces the six-file delivery restriction with a bounded manifest. Install through the Windows service installer. The stable `windows_service.py` host is outside the application update inventory; it imports the installed supervisor and preserves maintenance capabilities across application restarts. Existing database migrations remain additive; this refactor adds no data/schema changes.

`update-manifest.json` is the authoritative runtime inventory. Protocol 2 requires the bootstrap files, accepts at most 128 unique files, and constrains paths to root entry points, Python modules under `vsm/`, and JS/CSS/HTML assets under `web/`. Traversal, absolute paths, duplicate names, and other extensions are rejected. Archives copy only declared regular files into a release directory; links are never extracted. All declared Python files are compiled before activation. Static asset routes use the same declared web inventory.

The new supervisor can install later protocol-2 releases that add valid declared modules; no hardcoded per-release file tuple limits expansion. Application health must report the protocol and revision of the active release. The old exact protocol-1 profile remains accepted **for backup restoration**, including code/data recovery and safety backups of restored legacy releases. Legacy and modular archive contents must match their embedded profile and hashes exactly. Trusted full-code backup restoration retains its existing Admin/service-code trust boundary.

`scripts/install-windows.ps1` validates and stages the manifest payload, installs pywin32, sets local ACLs, registers automatic startup and recovery, and verifies HTTP health. `windows_service.py` configures local paths and runs the supervisor inside the Windows service process. Stop requests set the supervisor event; it terminates/waits for the web child before exiting. Supervisor locks use msvcrt byte locks on Windows and flock in Linux development. File writes are flushed before atomic replacement; directory fsync is used only on platforms supporting it.

## Verification

Run backend, module, and helper checks:

```sh
python3 -m unittest discover -s tests -v
node tests/frontend.cjs
node tests/maintenance.cjs
node --check web/app.js
python3 -m py_compile windows_service.py
git diff --check
```

Browser checks are in `tests/*.cjs`, including the visual review, diagrams, PCF/CSV, tags, environments, ratings/service times, branding, backups and data management. They use disposable databases/servers. Set `NODE_PATH`/`VSM_CHROMIUM` for Playwright/Chromium when installed outside the checkout. Windows service lifecycle integration runs in the Windows CI job.

Delivery tests cover inventory completeness, unsafe paths, staged installation without source-tree dependencies, future module extraction, and actual supervisor transitions from modular to legacy backups and back. Existing tests cover rollback, migrations, role isolation, request bounds, service configuration and portability.
