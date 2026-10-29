# SelfQuanta ledger backup and restore verification

The backup contains the MariaDB dump, public files, private files, site configuration, and a count manifest. It is compressed, encrypted with AES-256-CBC/PBKDF2 through OpenSSL, and copied only to an explicit destination outside the repository and Compose volumes.

## Prepare independent storage and key

Mount independent storage, preferably off-host or in a different provider account. Create the key outside Git and outside the backup destination:

```bash
sudo install -d -m 0700 -o root -g root /etc/selfquanta/ledger /var/backups/selfquanta/ledger
sudo openssl rand -out /etc/selfquanta/ledger/backup.key 64
sudo chmod 0600 /etc/selfquanta/ledger/backup.key
```

Configure COSCLI credentials in the root-owned `/etc/selfquanta/ledger/cos.yaml`, referenced by `COSCLI_CONFIG_FILE`. Keep that file and key out of Git and logs. The runtime file `/etc/selfquanta/ledger/runtime.env` supplies:

```dotenv
INTERNAL_LEDGER_BACKUP_DESTINATION=/var/backups/selfquanta/ledger
BACKUP_KEY_FILE=/etc/selfquanta/ledger/backup.key
COSCLI_CONFIG_FILE=/etc/selfquanta/ledger/cos.yaml
COS_BACKUP_URI=cos://selfquanta-ledger-backup/production/ledger/
```

Restrict the key and destination to the backup operator. Copy the key to a separately protected location; losing it makes the archives unrecoverable. Validate the configured inputs without running a backup:

```bash
sudo bash -c 'set -a; . /etc/selfquanta/ledger/runtime.env; set +a; pwsh -NoProfile -File /srv/selfquanta/ledger/infra/backup.ps1 -ValidateOnly'
```

## Scheduled and manual backup

The installed `selfquanta-ledger-backup.timer` runs daily at 02:30 Asia/Shanghai with a randomized delay of up to ten minutes. Inspect its next/last activation and service result:

```bash
sudo systemctl list-timers selfquanta-ledger-backup.timer
sudo systemctl status selfquanta-ledger-backup.timer
sudo systemctl status selfquanta-ledger-backup.service
```

Trigger an encrypted backup and COS upload now:

```bash
sudo systemctl start selfquanta-ledger-backup.service
sudo systemctl status selfquanta-ledger-backup.service
sudo journalctl -u selfquanta-ledger-backup.service -n 100 --no-pager
```

The service invokes `infra/backup-and-upload.ps1`. That wrapper invokes `backup.ps1` first and uploads only after it succeeds; it suppresses child output so COS config and key material do not enter logs. The local script:

- captures database plus public/private files to exact temporary paths inside backend;
- records `tabCompany`, `tabJournal Entry`, and `tabFile` counts;
- encrypts to a unique temporary file in the daily target directory, validates it, then atomically renames it to the final archive;
- writes an unencrypted sidecar containing only site, commit, timestamp, and counts;
- prints only timestamp, archive path, and SHA-256;
- keeps archives for the newest seven daily dates and the first encrypted monthly copy.

The script rejects a drive root, relative path, project-local destination, missing/short key, invalid site, or invalid Compose project. It never runs a Docker-wide prune. The count manifest is an operational restore check, not a transaction snapshot: on a busy online site, writes can occur between the count query and database dump. Prefer a maintenance window or otherwise quiescent period for acceptance drills. A count mismatch fails the drill and requires investigation; the script does not add a long database lock.

## Verify encrypted COS objects

List the configured backup prefix and confirm the daily encrypted archive and its manifest sidecar are present. COSCLI reads credentials from the named config file:

```bash
sudo /usr/local/bin/coscli ls cos://selfquanta-ledger-backup/production/ledger/ --recursive -c /etc/selfquanta/ledger/cos.yaml
```

Download the matching encrypted archive and manifest to a restricted restore directory, then compare the downloaded archive's SHA-256 with `encrypted_sha256` in the manifest. `restore-check.ps1` repeats this checksum validation before decrypting anything. Do not treat an uploaded object listing alone as verification that the archive is usable.

## Isolated restore drill

The image named in `INTERNAL_LEDGER_IMAGE` must already exist locally and exactly match `git_sha` in the chosen manifest. Set absolute paths to the downloaded archive, its manifest, a unique isolated project name, and a report directory outside the repository:

Replace `<manifest-git-sha>` with the 40-character `git_sha` value from the selected manifest. The image value is quoted so Bash treats the angle-bracket placeholder as text.

```bash
export INTERNAL_LEDGER_BACKUP_ARCHIVE=/mnt/restore/daily/internal-ledger-ledger.selfquanta.com-20261030-023000.zip.enc
export INTERNAL_LEDGER_BACKUP_MANIFEST=/mnt/restore/daily/internal-ledger-ledger.selfquanta.com-20261030-023000.manifest.json
export BACKUP_KEY_FILE=/etc/selfquanta/ledger/backup.key
export INTERNAL_LEDGER_RESTORE_PROJECT=ledger-restore-drill-20261030
export INTERNAL_LEDGER_RESTORE_REPORT_DIR=/mnt/restore/restore-reports
export INTERNAL_LEDGER_IMAGE='internal-ledger:<manifest-git-sha>'

sudo --preserve-env=INTERNAL_LEDGER_BACKUP_ARCHIVE,INTERNAL_LEDGER_BACKUP_MANIFEST,BACKUP_KEY_FILE,INTERNAL_LEDGER_RESTORE_PROJECT,INTERNAL_LEDGER_RESTORE_REPORT_DIR,INTERNAL_LEDGER_IMAGE \
  pwsh -NoProfile -File /srv/selfquanta/ledger/infra/restore-check.ps1 -ValidateOnly
sudo --preserve-env=INTERNAL_LEDGER_BACKUP_ARCHIVE,INTERNAL_LEDGER_BACKUP_MANIFEST,BACKUP_KEY_FILE,INTERNAL_LEDGER_RESTORE_PROJECT,INTERNAL_LEDGER_RESTORE_REPORT_DIR,INTERNAL_LEDGER_IMAGE \
  pwsh -NoProfile -File /srv/selfquanta/ledger/infra/restore-check.ps1
```

The restore check creates a unique Compose project and fresh named volumes, binds no public service, restores the database and both file archives, migrates the site, and compares Company, Journal Entry, and File counts to the manifest. A successful run writes a durable JSON report in `restore-reports` with `status` set to `PASS`, the source manifest, Git SHA, and expected/actual counts. Retain and review that report as restore evidence. On failure it writes a `FAIL` report and retains the named project for diagnosis; decrypted host-side temporary files and generated secrets are still removed.

Before creating anything, the script checks containers, networks, and volumes carrying the requested Compose project label. If any already exist, it refuses to reuse or delete them. If `INTERNAL_LEDGER_RESTORE_PROJECT` is omitted, a high-entropy `ledger-restore-*` name is generated.

The encrypted archive records the actual Frappe v16 site-config backup filename. During restore, the original `encryption_key` is merged into the fresh site config through files; the fresh `db_name` and `db_password` are preserved. The encryption key is never placed in a process argument or log. A backup without `encryption_key` fails before the database restore because encrypted Frappe fields would otherwise be unreadable.

After diagnosing a failed drill, an operator may remove only the reported project:

```bash
docker compose --env-file /srv/selfquanta/ledger/infra/versions.env -f /srv/selfquanta/ledger/infra/compose.yaml --project-name ledger-restore-drill-20261030 down --volumes --remove-orphans
```

Do not use `docker system prune` or `docker volume prune`. Run a full restore drill before launch, after backup-script changes, and before every production upgrade. This runbook update does not claim that a live backup or restore has been verified.
