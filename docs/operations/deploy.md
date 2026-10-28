# Internal Ledger deployment

This is the production path for one company and one site. It uses the exact application image built from this repository; it does not use a floating latest tag.

## Prerequisites

- Ubuntu LTS server. Two CPU cores and 4 GB RAM are the minimum trial size; 4 cores and 8 GB are recommended.
- Docker Engine with Compose v2 and BuildKit secret mounts, Git, PowerShell 7, and OpenSSL.
- A DNS name, a real TLS certificate/key, and firewall rules exposing only SSH, TCP 80, and TCP 443.
- A credential-free HTTPS Git origin reachable from the image builder. The exact application commit must be available through a branch or tag named by INTERNAL_LEDGER_APP_REF. SSH and embedded-credential URLs are rejected because the pinned layered build does not mount SSH credentials.
- An official frappe_docker checkout at tag v4.1.1, commit d84154ae23ab7ae756dfe942e3f24dcd70d1fbda.

The build follows the official layered image interface: images/layered/Containerfile, FRAPPE_BRANCH, and the BuildKit apps_json secret. See the [official build setup](https://github.com/frappe/frappe_docker/blob/v4.1.1/docs/02-setup/02-build-setup.md) and [layered Containerfile](https://github.com/frappe/frappe_docker/blob/v4.1.1/images/layered/Containerfile).

## Build the immutable image

Clone both repositories to a data disk. Do not put credentials in either remote URL when a credential helper or SSH key can be used.

    git clone https://github.com/frappe/frappe_docker /srv/frappe_docker
    git -C /srv/frappe_docker checkout v4.1.1
    git -C /srv/frappe_docker rev-parse HEAD

    $env:FRAPPE_DOCKER_DIR = '/srv/frappe_docker'
    $env:INTERNAL_LEDGER_APP_REF = 'v0.1.0'
    $env:INTERNAL_LEDGER_BUILD_TEMP = '/srv/internal-ledger-build'
    pwsh -NoProfile -File infra/build_image.ps1

The script refuses a dirty worktree, a missing/non-network origin, an app ref that does not point to HEAD, or a modified/wrong frappe_docker checkout. It writes a temporary apps.json only in the explicit scratch directory, passes it as a BuildKit secret, removes it, and creates internal-ledger:<40-character-git-sha>.

## Runtime secrets and TLS

Create root-owned files outside the repository and Compose volumes:

    /etc/internal-ledger/db-root-password
    /etc/internal-ledger/site-admin-password
    /etc/internal-ledger/fullchain.pem
    /etc/internal-ledger/privkey.pem

Use long random passwords and mode 0600; use the certificate delivered by the actual DNS/TLS provider. Do not create a fake production domain or certificate.

Set runtime values in the service manager or a root-readable environment file outside Git:

    INTERNAL_LEDGER_IMAGE=internal-ledger:<git-sha>
    SITE_NAME=ledger.example.com
    COMPOSE_PROJECT_NAME=internal-ledger
    DB_ROOT_PASSWORD_FILE=/etc/internal-ledger/db-root-password
    SITE_ADMIN_PASSWORD_FILE=/etc/internal-ledger/site-admin-password
    TLS_CERTIFICATE_FILE=/etc/internal-ledger/fullchain.pem
    TLS_PRIVATE_KEY_FILE=/etc/internal-ledger/privkey.pem

## Initialize and start

Run the checks before touching production:

    pwsh -NoProfile -File infra/test-config.ps1
    docker compose --env-file infra/versions.env -f infra/compose.yaml config --quiet
    docker compose --env-file infra/versions.env -f infra/compose.yaml --profile init run --rm create-site
    docker compose --env-file infra/versions.env -f infra/compose.yaml up -d
    docker compose --env-file infra/versions.env -f infra/compose.yaml ps

The one-shot create-site service refuses to overwrite an existing site. The gateway is the only service with published ports. MariaDB, Redis, backend, workers, scheduler, WebSocket, and the internal Frappe frontend remain on the internal Docker network. HTTP redirects to HTTPS.

The long-running backend never receives the MariaDB root secret. Only MariaDB itself, the one-shot create-site service, and the isolated restore runner can read it.

After first login, set timezone Asia/Shanghai, currency CNY, the accounting year, and strong named user accounts. Disable public registration. Keep SSH key-only where possible and rate-limit login at the host/provider layer.

## Upgrade

1. Run and verify an encrypted database-plus-files backup.
2. Test the new exact image and migration in a non-production project.
3. Change INTERNAL_LEDGER_IMAGE to the new SHA image.
4. Run bench --site <site> migrate through a one-shot backend command, then recreate services.
5. Check health, login, a read-only report, and the audit log.

Never log passwords, repository credentials, full bank numbers, or statement content. Preserve the upstream Frappe/ERPNext and application license/source notices when distributing the service or image.

## Current acceptance boundary

The development checkout had no Git origin, and Docker was not installed on the host used for Task 9. Therefore no image was built and no live Compose stack was started. build_image.ps1 is intentionally required to fail before Docker when origin is absent. Live image, restart, backup, and restore acceptance remains pending until the owner supplies the real remote and a Docker host.
