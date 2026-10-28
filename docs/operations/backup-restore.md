# Backup and restore verification

The backup contains the MariaDB dump, public files, private files, site configuration, and a count manifest. It is compressed, encrypted with AES-256-CBC/PBKDF2 through OpenSSL, and copied only to an explicit destination outside the repository and Compose volumes.

## Prepare independent storage and key

Mount independent storage, preferably off-host or in a different provider account. Create the key outside Git and outside the backup destination:

    openssl rand -out /etc/internal-ledger/backup.key 64
    $env:BACKUP_KEY_FILE = '/etc/internal-ledger/backup.key'
    $env:INTERNAL_LEDGER_BACKUP_DESTINATION = '/mnt/offsite/internal-ledger'
    $env:SITE_NAME = 'ledger.example.com'
    $env:COMPOSE_PROJECT_NAME = 'internal-ledger'
    pwsh -NoProfile -File infra/backup.ps1 -ValidateOnly

Restrict the key and destination to the backup operator. Copy the key to a separately protected location; losing it makes the archives unrecoverable.

## Daily backup

With the production Compose environment loaded:

    pwsh -NoProfile -File infra/backup.ps1

The script:

- captures database plus public/private files to exact temporary paths inside backend;
- records tabCompany, tabJournal Entry, and tabFile counts;
- encrypts to a unique temporary file in the daily target directory, validates it, then atomically renames it to the final archive;
- writes an unencrypted sidecar containing only site, commit, timestamp, and counts;
- prints only timestamp, archive path, and SHA-256;
- keeps archives for the newest seven daily dates and the first encrypted monthly copy.

Schedule this command daily with the host service manager. Monitor its exit code and copy the printed checksum to the backup monitoring record. The script rejects a drive root, relative path, project-local destination, missing/short key, invalid site, or invalid Compose project. It never runs a Docker-wide prune.

The count manifest is an operational restore check, not a transaction snapshot: on a busy online site, writes can occur between the count query and database dump. Prefer a maintenance window or otherwise quiescent period for acceptance drills. A count mismatch fails the drill and requires investigation; the script does not add a long database lock.

## Isolated restore drill

The image named in INTERNAL_LEDGER_IMAGE must already exist locally and must exactly match the git_sha in the chosen manifest.

    $env:INTERNAL_LEDGER_BACKUP_ARCHIVE = '/mnt/offsite/internal-ledger/daily/internal-ledger-ledger.example.com-20261028-020000.zip.enc'
    $env:INTERNAL_LEDGER_BACKUP_MANIFEST = '/mnt/offsite/internal-ledger/daily/internal-ledger-ledger.example.com-20261028-020000.manifest.json'
    $env:INTERNAL_LEDGER_RESTORE_PROJECT = 'ledger-restore-20261028'
    $env:INTERNAL_LEDGER_RESTORE_REPORT_DIR = '/mnt/offsite/internal-ledger/restore-reports'
    $env:INTERNAL_LEDGER_IMAGE = 'internal-ledger:<manifest-git-sha>'

    pwsh -NoProfile -File infra/restore-check.ps1 -ValidateOnly
    pwsh -NoProfile -File infra/restore-check.ps1

The restore check creates a unique Compose project and fresh named volumes, binds no public service, restores the database and both file archives, migrates the site, and compares Company, Journal Entry, and File counts to the manifest. On success it writes a durable JSON report and removes only that exact project's containers and volumes. On failure it writes a FAIL report and retains the named project for diagnosis; decrypted host-side temporary files and generated secrets are still removed.

Before creating anything, the script checks containers, networks, and volumes carrying the requested Compose project label. If any already exist, it refuses to reuse or delete them. If INTERNAL_LEDGER_RESTORE_PROJECT is omitted, a high-entropy ledger-restore-* name is generated.

The encrypted archive records the actual Frappe v16 site-config backup filename. During restore, the original encryption_key is merged into the fresh site config through files; the fresh db_name and db_password are preserved. The encryption key is never placed in a process argument or log. A backup without encryption_key fails before the database restore because encrypted Frappe fields would otherwise be unreadable.

After diagnosing a failed drill, an operator may remove only the reported project:

    docker compose --env-file infra/versions.env -f infra/compose.yaml --project-name ledger-restore-20261028 down --volumes --remove-orphans

Do not use docker system prune or docker volume prune. Run a full restore drill before launch, after backup-script changes, and before every production upgrade.
