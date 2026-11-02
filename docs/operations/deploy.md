# SelfQuanta ledger deployment

This is the production path for one company and one site. It uses the exact application image built from this repository; it does not use a floating latest tag.

## Prerequisites

- Ubuntu 24.04 server. Two CPU cores and 4 GB RAM are the minimum trial size; 4 cores and 8 GB are recommended.
- Docker Engine with Compose v2 and BuildKit secret mounts, Git, PowerShell 7, OpenSSL, and WireGuard.
- A credential-free HTTPS Git origin reachable from the image builder. The exact application commit must be available through a branch or tag named by `INTERNAL_LEDGER_APP_REF`. SSH and embedded-credential URLs are rejected because the pinned layered build does not mount SSH credentials.
- An official `frappe_docker` checkout at tag `v4.1.1`, commit `d84154ae23ab7ae756dfe942e3f24dcd70d1fbda`.

The build follows the official layered image interface: `images/layered/Containerfile`, `FRAPPE_BRANCH`, and the BuildKit `apps_json` secret. See the [official build setup](https://github.com/frappe/frappe_docker/blob/v4.1.1/docs/02-setup/02-build-setup.md) and [layered Containerfile](https://github.com/frappe/frappe_docker/blob/v4.1.1/images/layered/Containerfile).

## Network state before and after ICP approval

**Pre-ICP:** Keep the service WireGuard-only. Set `HTTP_BIND=HTTPS_BIND=10.77.0.1`; firewall rules deny public TCP 80/443 and allow those ports only on `wg0`. Do not publish public DNS or expose public 80/443. The private-access setup creates an internal CA and an internal certificate for `ledger.selfquanta.com`; distribute its CA certificate to authorized clients. To provision a WireGuard client, replace the endpoint placeholder with the reachable server public IPv4 address or hostname only; use no scheme, port, or credentials. Do not run this placeholder verbatim:

```bash
sudo env WIREGUARD_ENDPOINT='SERVER_PUBLIC_IP_OR_HOSTNAME' bash /srv/selfquanta/ledger/infra/host/configure-private-access.sh laptop-cao
```

**Post-ICP:** Only after filing approval, provision public DNS and a valid public TLS certificate, then deliberately change the HTTP/HTTPS bindings and firewall rules for public access. Public bindings, public DNS, and public 80/443 remain disabled until that gate is met. Re-run the configuration checks and review the changed firewall rules before cutover.

## Build the immutable image

Place the application checkout at `/srv/selfquanta/ledger` and the pinned upstream checkout at `/srv/selfquanta/frappe_docker`. Do not put credentials in either remote URL. Set `RELEASE_COMMIT` to the reviewed full 40-character commit from the release report and `INTERNAL_LEDGER_APP_REF` to its published release tag. Retain that tag; never build a release by following a moving `main` head. The build script also verifies that the remote ref points to the exact checkout:

```powershell
$releaseCommit = $env:RELEASE_COMMIT
if ($releaseCommit -notmatch '^[0-9a-f]{40}$' -or $releaseCommit -eq ('0' * 40)) { throw 'Set the reviewed RELEASE_COMMIT' }
if (-not $env:INTERNAL_LEDGER_APP_REF) { throw 'Set the published release tag' }
git -C /srv/selfquanta/ledger checkout --detach $releaseCommit
if ($LASTEXITCODE -ne 0) { throw 'Release checkout failed' }
git -C /srv/selfquanta/frappe_docker checkout v4.1.1
if ($LASTEXITCODE -ne 0) { throw 'Upstream checkout failed' }
git -C /srv/selfquanta/frappe_docker rev-parse HEAD

$env:FRAPPE_DOCKER_DIR = '/srv/selfquanta/frappe_docker'
$env:INTERNAL_LEDGER_BUILD_TEMP = '/srv/selfquanta/build'
pwsh -NoProfile -File /srv/selfquanta/ledger/infra/build_image.ps1
if ($LASTEXITCODE -ne 0) { throw 'Application image build failed' }
docker build --tag "internal-ledger-ocr:$releaseCommit" /srv/selfquanta/ledger/ocr_service
if ($LASTEXITCODE -ne 0) { throw 'OCR image build failed' }
```

The script refuses a dirty worktree, a missing/non-network origin, an app ref that does not point to `HEAD`, or a modified/wrong `frappe_docker` checkout. It writes a temporary `apps.json` only in the explicit scratch directory, passes it as a BuildKit secret, removes it, and creates `internal-ledger:<40-character-git-sha>`.

Do not overwrite either SHA tag. Record the commit, both image IDs/digests and the prior release's image IDs in the restricted release record. For an incremental build, use `infra/Dockerfile.app-layer` with `APP_COMMIT` equal to this exact commit and `BASE_IMAGE` equal to a previously verified image digest; verify the resulting revision label before promotion. A successful Docker build alone is insufficient: run this read-only gate against the candidate app image before attaching any site volumes:

```bash
set -euo pipefail
: "${RELEASE_COMMIT:?Set the reviewed full commit}"
candidate_image="internal-ledger:${RELEASE_COMMIT}"
docker image inspect "$candidate_image" --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker image inspect "internal-ledger-ocr:${RELEASE_COMMIT}" --format '{{.Id}}'
docker run --rm --entrypoint python "$candidate_image" -c '
import json
from pathlib import Path
root = Path("/home/frappe/frappe-bench")
entries = json.loads((root / "assets/assets.json").read_text())
assert isinstance(entries, dict) and len(entries) >= 57, "at least 57 runtime asset entries required"
for bundle in ("desk.bundle.js", "ledger_workbench.bundle.js"):
    assert bundle in entries and entries[bundle].endswith(".js"), bundle
for value in entries.values():
    assert isinstance(value, str) and value.startswith("/assets/"), "invalid asset path"
    path = (root / value.lstrip("/")).resolve()
    assert path.is_relative_to(root) and path.is_file(), "missing or unsafe runtime asset"
print("PASS: runtime assets", len(entries), "entries; zero missing; Desk and ledger present")'
```

Use the runtime `assets/assets.json`, not only the app's generated `sites/assets/assets.json`: a ledger-only manifest can hide missing Desk bundles. The deterministic PowerShell equivalent accepts an exported image filesystem: `pwsh -NoProfile -File infra/test-config.ps1 -StaticOnly -AssetManifestPath <exported-bench>/assets/assets.json -AssetRoot <exported-bench>`. Both arguments are required together. Run document-parser tests in the candidate image on an isolated test site before approval; keep the full test log, manifest gate output and image IDs with the release record. Never run synthetic acceptance against the production site.

## Runtime secrets and configuration

Create root-owned files outside the repository and Compose volumes:

```text
/etc/selfquanta/ledger/db-root-password
/etc/selfquanta/ledger/site-admin-password
/etc/selfquanta/ledger/internal-fullchain.pem
/etc/selfquanta/ledger/internal-privkey.pem
/etc/selfquanta/ledger/backup.key
/etc/selfquanta/ledger/cos.yaml
```

Use long random passwords. Keep `db-root-password` and `site-admin-password` owned by `root:root` with mode 0640 so the image's `frappe` user (group 0) can read the Compose secret mounts; keep the remaining secret files at mode 0600. Before ICP approval, use the internal certificate generated by `infra/host/configure-private-access.sh`; do not create a fake public production certificate or domain. Install the runtime configuration at `/etc/selfquanta/ledger/runtime.env` with the actual built image SHA:

```dotenv
INTERNAL_LEDGER_IMAGE=internal-ledger:<git-sha>
OCR_IMAGE=internal-ledger-ocr:<same-git-sha>
SITE_NAME=ledger.selfquanta.com
COMPOSE_PROJECT_NAME=selfquanta-ledger
DB_ROOT_PASSWORD_FILE=/etc/selfquanta/ledger/db-root-password
SITE_ADMIN_PASSWORD_FILE=/etc/selfquanta/ledger/site-admin-password
TLS_CERTIFICATE_FILE=/etc/selfquanta/ledger/internal-fullchain.pem
TLS_PRIVATE_KEY_FILE=/etc/selfquanta/ledger/internal-privkey.pem
HTTP_BIND=10.77.0.1
HTTPS_BIND=10.77.0.1
HTTP_PORT=80
HTTPS_PORT=443
INTERNAL_LEDGER_BACKUP_DESTINATION=/var/backups/selfquanta/ledger
BACKUP_KEY_FILE=/etc/selfquanta/ledger/backup.key
COSCLI_CONFIG_FILE=/etc/selfquanta/ledger/cos.yaml
COS_BACKUP_URI=cos://selfquanta-ledger-backup/production/ledger/
```

Replace both placeholders with the same reviewed full 40-character commit. Keep the runtime file root-readable and outside Git. The runtime example contains all-zero app and OCR image sentinels and must not be deployed unchanged. The static gate rejects configured mutable/sentinel images and requires both exact image template values. Compose rejects a missing or empty `OCR_IMAGE`; there is no development image fallback. Run `docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml config --quiet` with the protected runtime environment loaded before startup. Bare `infra/test-config.ps1 -StaticOnly` remains usable for repository checks without a production environment.

## Install and operate the systemd services

Copy the repository to `/srv/selfquanta/ledger`, set `/etc/selfquanta/ledger/runtime.env`, and install the named units:

```bash
sudo bash /srv/selfquanta/ledger/infra/host/install-systemd-units.sh
sudo systemctl daemon-reload
sudo systemctl enable --now selfquanta-ledger-backup.timer
```

On the **first install only**, create the site once before starting the normal systemd service. This loads the root-owned runtime environment and supplies the pinned Compose versions file:

```bash
sudo bash -c 'set -a; . /etc/selfquanta/ledger/runtime.env; set +a; docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml --profile init run --rm create-site'
```

Do not repeat the one-time create-site step for an existing site. Start and inspect the regular service for first startup after creation and for subsequent boots:

```bash
sudo systemctl start selfquanta-ledger
sudo systemctl status selfquanta-ledger
sudo journalctl -u selfquanta-ledger --since today
```

The unit runs `infra/test-config.ps1 -StaticOnly` before Compose startup. For a deliberate runtime or image change, first complete the backup/migrate/switch sequence below; then restart and inspect the service:

```bash
sudo systemctl restart selfquanta-ledger
sudo systemctl status selfquanta-ledger
sudo journalctl -u selfquanta-ledger -n 100 --no-pager
```

The one-shot `create-site` service refuses to overwrite an existing site. The gateway is the only service with published container ports. MariaDB, Redis, backend, workers, scheduler, WebSocket, and the internal Frappe frontend remain on the internal Docker network. HTTP redirects to HTTPS. The long-running backend never receives the MariaDB root secret; only MariaDB itself, the one-shot create-site service, and the isolated restore runner can read it.

After first login, set timezone Asia/Shanghai, currency CNY, the accounting year, and strong named user accounts. Disable public registration. Keep SSH key-only where possible and rate-limit login at the host/provider layer.

## Upgrade and rollback

Use a maintenance window. Before stopping any service, test the exact candidate images and migration in an isolated project, complete a restore drill, and retain the protected current runtime configuration and previous SHA image IDs. Keep the existing private bindings, TLS/secret paths and Compose project name. These operator commands require the normal authorized production change approval; this document does not execute them.

1. Stop new writes and background jobs for a consistent backup. Load the existing runtime and run `infra/backup-and-upload.ps1`; require process exit 0 and verify the archive checksum/manifest and COS copy. A backup/upload failure ends the release before migration. Never print or copy the runtime secrets into the release log.
2. Stop the old app services while keeping the database, Redis and volumes. Use the candidate image only for a one-shot migration; leave the persistent `INTERNAL_LEDGER_IMAGE` and `OCR_IMAGE` settings unchanged until this exits 0.
3. On migration failure, stop here. Do not start the candidate services or edit the persistent image values. Diagnose against the retained backup; partially applied schema/data changes may require restore rather than restarting the old code.
4. Only after migration exit 0, set the two reviewed SHA image values in the protected runtime file and restart the systemd service. Keep maintenance enabled for the container/static/bench checks below; then disable maintenance and restart while access remains WireGuard-only, and perform HTTP/authenticated read-only smoke before admitting users or restarting the backup timer.

Example maintenance sequence (run as root with the approved release commit already exported; the helper is local to this shell):

```bash
set -euo pipefail
set -a; . /etc/selfquanta/ledger/runtime.env; set +a
: "${RELEASE_COMMIT:?Set the reviewed full commit}"
[[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ && ! "$RELEASE_COMMIT" =~ ^0{40}$ ]]
candidate_image="internal-ledger:${RELEASE_COMMIT}"
dc() { docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml "$@"; }
sudo systemctl stop selfquanta-ledger-backup.timer
dc exec -T backend bench --site "$SITE_NAME" set-maintenance-mode on
dc exec -T backend bench --site "$SITE_NAME" disable-scheduler
dc stop scheduler queue-short queue-long
pwsh -NoProfile -File /srv/selfquanta/ledger/infra/backup-and-upload.ps1
# set -e stops here unless backup and upload returned 0. Verify its matching manifest/checksum.
dc stop gateway frontend websocket backend
INTERNAL_LEDGER_IMAGE="$candidate_image" dc run --rm --no-deps --entrypoint bench \
  backend --site "$SITE_NAME" migrate
# Stop on nonzero migration; do not switch the persistent runtime before this point.
```

The final `migrate` command runs the candidate image through a temporary environment override, preserving the current runtime file for rollback. Keep maintenance enabled on failure. Save the current root-owned runtime to a unique restricted backup path before editing it; record that path in the release record. After successful migration, edit only `INTERNAL_LEDGER_IMAGE` and `OCR_IMAGE` to the reviewed SHA values, reload the environment, validate Compose configuration and run `sudo systemctl restart selfquanta-ledger`. Complete phase 1 below with maintenance still enabled. Complete phase 2 only through the existing WireGuard/private firewall access. After both phases succeed, enable the site's scheduler and restart `selfquanta-ledger-backup.timer`.

For rollback, restore the previous SHA image values and the saved configuration, then run `sudo systemctl restart selfquanta-ledger` and inspect `sudo systemctl status selfquanta-ledger` and its journal. Keep maintenance enabled until checks pass. A code-image rollback does not reverse database migrations. If schema/data compatibility is uncertain, keep services stopped and restore the selected encrypted database-plus-files backup using the verified [isolated restore procedure](backup-restore.md#isolated-restore-drill), including the original encryption key. Do not use `down --volumes`, remove production volumes, or run Docker-wide prune during rollback.

Run a restore drill before promotion using the backup manifest's exact image and a unique `ledger-restore-*` project. `infra/restore-check.ps1 -ValidateOnly` must succeed before the actual drill; then require a durable `status: PASS` report matching Company, Journal Entry and File counts. A failed drill retains its isolated project for diagnosis and blocks promotion. The drill verifies recovery in separate fresh volumes; production restoration remains a deliberate operator action using the selected verified backup, never an automatic rollback script.

## Read-only release smoke and real-file acceptance

Phase 1, maintenance enabled: verify container health, the deployed runtime asset manifest and the exact static Desk/ledger bundle URLs (HTTP 200 and JavaScript content). Check Compose's published ports and ensure `ocr` has none; only the private gateway may publish ports. OCR health is internal (`http://ocr:8765/health`), with no public OCR route. Use container-local `bench execute` for database connectivity, the unified Workspace, the configured company and enabled leaf accounts; these reads bypass the HTTP request maintenance gate without changing its configuration. For example, using the `dc` helper above:

```bash
dc ps
dc exec -T backend bench --site "$SITE_NAME" execute 'frappe.db.sql("select 1")'
dc exec -T backend bench --site "$SITE_NAME" execute 'frappe.db.exists("Workspace", "SelfLedger 企业经营与智能内账")'
dc exec -T backend bench --site "$SITE_NAME" execute 'frappe.db.exists("Company", frappe.db.get_single_value("Internal Ledger Settings", "company"))'
dc exec -T backend bench --site "$SITE_NAME" execute internal_ledger.api.classification_api.list_leaf_accounts
```

Require a successful database read, the named Workspace, the configured Company and the expected enabled leaf accounts before proceeding. Do not attempt `/api/method/ping`, login or authenticated HTTP API checks in this phase: normal Frappe requests are stopped by maintenance mode.

Phase 2, WireGuard/private firewall access retained: disable maintenance through `dc exec -T backend bench --site "$SITE_NAME" set-maintenance-mode off`, then run `sudo systemctl restart selfquanta-ledger` to refresh web-process configuration. Now verify `/api/method/ping`, login, the authenticated unified Workspace, company/accounts, workbench counts, a read-only report and audit history. Do not admit ordinary users during this check. If any check fails, immediately run `dc exec -T backend bench --site "$SITE_NAME" set-maintenance-mode on` and restart the service to close HTTP access, then restore the previous image/configuration using the rollback procedure above. If backend cannot execute the maintenance command, stop the gateway before diagnosis and rollback. If the failed release changed database state incompatibly, keep maintenance enabled and use the selected backup plus the verified restore-drill procedure to recover database and files before retrying. Keep the scheduler disabled and backup timer stopped until smoke succeeds; add no maintenance read-bypass setting and open no public access.

Do not print source IDs, bank details or statement contents in smoke logs. Keep fixture imports and automated posting on `test.localhost`, where tests roll back database and source-file fixtures.

Real accounting acceptance remains an administrator decision: the administrator selects an approved real statement file, uploads it through the UI, reviews Pending/Confirmed duplicate exclusions, supplies a release note where appropriate, manually classifies an unmatched row and checks each debit/credit line and company. The administrator must explicitly click the UI approval/posting confirmation before any real Journal Entry is created. Record only the resulting real Journal Entry reference and acceptance result in restricted evidence. Automated smoke must never upload a fabricated statement or approve/post entries in the production company. Public access without WireGuard is separately gated Task 7 and is not part of this release-readiness task.

Never log passwords, repository credentials, full bank numbers, or statement content. Preserve the upstream Frappe/ERPNext and application license/source notices when distributing the service or image.

## Current acceptance boundary

No live production deployment, public cutover, or production backup/restore is verified by this readiness task. Record live image, restart, backup/restore and administrator real-file acceptance separately from local automated evidence; public cutover remains Task 7.
