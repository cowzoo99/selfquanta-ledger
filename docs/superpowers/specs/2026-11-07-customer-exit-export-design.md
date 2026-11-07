# SelfLedger customer exit export design

**Status:** approved design for implementation planning  
**Date:** 2026-11-07  
**Parent specification:** `2026-11-06-selfledger-public-erp-platform-design.md`  
**Release gate:** G5 customer data exit

## Goal

Give each customer enterprise a complete, readable and verifiable copy of its data. The enterprise owner can generate and download the package without platform staff. Platform staff may act only after the customer grants access, and that action must remain auditable.

The first release exports one Frappe Site at a time. It does not accept a Site name, database name or filesystem path from the browser. The current Site is the isolation boundary.

## User flow

The enterprise owner opens `企业设置 → 数据管理` and selects `导出企业全部数据`.

1. The server creates an export request and returns immediately with status `生成中`.
2. A background job reads only the current Site and builds a private ZIP archive.
3. The page shows the latest status without blocking the browser.
4. A successful request becomes `可下载` and shows package size, data counts and attachment count.
5. A failed request shows a Chinese reason and a `重新生成` action. Retry creates a new request and never overwrites the previous audit record.
6. Before download or deletion, the server checks the user's current permission again. A copied file URL cannot bypass authorization.
7. The enterprise owner may delete the generated ZIP. The request and its audit events remain.

Any failure makes the whole request fail. The server must not expose a partial archive.

## Package format

The ZIP uses UTF-8 filenames and contains:

```text
manifest.json
README.txt
master-data/
transactions/
audit/
attachments/
    index.csv
    files/...
```

### Manifest

`manifest.json` records:

- schema version;
- product and application version;
- current Site identifier represented by a non-secret stable identifier;
- company names in the Site;
- export request, requester and UTC timestamps;
- each exported data set, format, row count and SHA-256 hash;
- attachment count, total bytes and SHA-256 hash per file;
- package completion status.

The manifest never contains database credentials, encryption keys, API secrets or physical server paths.

### Master data

Export supported business masters as one CSV per DocType, including Company, Account, Cost Center, Customer, Supplier, Contact, Address, Item, Item Group, Warehouse, Project, Employee and the related child rows needed to interpret them.

Only DocTypes installed on the current Site are included. An absent optional DocType is recorded as unavailable instead of failing the package.

### Transactions

Export the standard documents used by the approved ERP scope, including sales, purchase, stock, expenses, payroll, payments, journal entries, general ledger entries and the SelfLedger import, classification, draft and posting records. Submitted, cancelled and draft states are retained as separate fields.

Each CSV includes stable document identifiers, company, owner, creation and modification timestamps, document status and the fields needed to trace the business document to its accounting effect. Child tables are exported separately with parent identifiers.

### Audit data

Export Frappe version and activity records that the current Site permits for customer exit, together with Internal Ledger Audit Event and AI Interaction Audit. The package records export request, completion, download, failure and archive deletion as separate immutable audit events.

### Attachments

Export private and public files that belong to an exported document in the current Site. `attachments/index.csv` maps each physical file to its File document, attached DocType, attached document, original filename, privacy flag, size and SHA-256 hash.

The archive uses safe generated paths. Original filenames remain metadata, so duplicate or unsafe filenames cannot overwrite another file or escape the ZIP directory.

## Excluded security data

The export must exclude:

- password hashes and password reset keys;
- API keys and API secrets;
- OTP and two-factor authentication secrets;
- session, cookie and OAuth token data;
- site configuration and encryption keys;
- database names, users and passwords;
- private keys, certificate keys and backup credentials;
- server paths and infrastructure environment values.

User identity data needed for audit readability may include account name, enabled state and role names. It must not include authentication material.

## Permissions and audit

- Only the enterprise owner may request, download or delete a customer exit package.
- Platform support has no default access. A support action requires customer-granted, time-limited access and records the effective user and acting support user.
- Enterprise administrators, accountants, ordinary members, read-only members and guests cannot request or obtain the package unless they also hold the enterprise owner role.
- The generated File is private. Direct File API and URL access use the same permission decision as the export request.
- Every request records requester, actor, timestamp, status, package hash and failure code without recording exported content in logs.

## Data model and interfaces

Add one request DocType named `Customer Exit Export` with the minimum fields needed to track owner, status, timestamps, private File, counts, package hash and a safe failure code. Users cannot edit its state directly.

Expose four whitelisted operations:

- request a new export;
- list the current Site's export requests;
- download a completed package;
- delete a completed package file.

The background builder owns all state changes. It writes to a temporary directory inside the current Site's private area, closes the ZIP, verifies the manifest and hashes, then creates the private File. On failure it removes temporary artifacts before marking the request failed.

## Frontend

Add one section under `企业设置 → 数据管理`. Preserve the existing SelfLedger shell, navigation, Chinese labels and permissions.

The screen contains:

- one primary action, `导出企业全部数据`;
- a short warning that the package contains sensitive business data but no login passwords or system keys;
- a compact request list with status, requester, creation time, row count, attachment count and file size;
- `下载`, `删除导出包` and `重新生成` actions only when valid for the current state;
- explicit loading, empty, failed and unavailable states.

Frontend implementation must use the installed `heydamir/unslop` Skill. It will keep the current product identity, quiet the interface chrome, use one established accent, keep controls aligned to the existing spacing system and avoid decorative gradients, emoji, glowing cards or invented data. It must preserve existing routes and behavior and remain usable at 375 px, 768 px, 1440 px and 200% zoom.

## Failure handling

- A failed data set, attachment read, hash check or ZIP validation fails the whole request.
- The user sees a stable Chinese error category, not a Python traceback or server path.
- Logs keep request identifiers and safe error codes, not record contents or credentials.
- Retrying never mutates the earlier request.
- Download refuses requests that are incomplete, missing their private File or owned by another Site context.
- Deleting the ZIP is idempotent. It does not delete business data or the audit trail.

## Acceptance criteria

The module is complete when one module-level acceptance run proves:

1. An enterprise owner can request, complete, download and delete a private package.
2. The package contains the expected master, transaction, attachment and audit fixtures, with literal row counts and valid hashes.
3. Authentication secrets, Site configuration and server paths are absent.
4. Enterprise administrators, ordinary members, read-only members and guests cannot request or download it.
5. A copied private URL cannot bypass the permission check.
6. Dedicated acceptance Sites A and B produce packages containing only their own fixture identifiers and files.
7. An injected attachment or archive failure produces no downloadable partial package and leaves a failed audit record.
8. Retry creates a new request without changing earlier evidence.
9. The frontend exposes the agreed states and actions in Chinese and passes its focused accessibility and responsive checks.
10. The integrated acceptance checklist changes `客户完整退出导出` from `PENDING` to `PASS` only after the real package is opened and its manifest, CSV files, attachments and audit records are inspected.

## Out of scope

- Direct import into a named competitor's product.
- Full database backup or restoration credentials in the customer package.
- Cross-Site or multi-customer export.
- Tax authority submission.
- Scheduled recurring exports.
- Automatic deletion or retention policy beyond the owner's explicit delete action.
