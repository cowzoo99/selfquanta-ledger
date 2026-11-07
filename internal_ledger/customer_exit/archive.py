import csv
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import frappe

from internal_ledger.customer_exit.catalog import DATASETS, USER_FIELDS, safe_fields


SCHEMA = "selfledger.customer-exit.v1"
README = (
    "SelfLedger 企业数据退出包\r\n"
    "本压缩包包含当前企业站点的可读业务数据、附件和审计记录。\r\n"
    "本压缩包不包含登录密码、系统密钥，也不是数据库恢复备份。\r\n"
).encode("utf-8")


class AttachmentReadError(OSError):
    failure_code = "ATTACHMENT_READ_FAILED"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _csv_bytes(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(headers)
    writer.writerows([[_text(value) for value in row] for row in rows])
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def _installed(doctype: str) -> bool:
    return bool(frappe.db.exists("DocType", doctype) and frappe.db.table_exists(doctype))


def _rows(doctype: str, fields, filters=None, child=False):
    order_by = "parent, parentfield, idx, name" if child else "creation, name"
    return frappe.get_all(doctype, filters=filters, fields=list(fields), order_by=order_by)


def _collect_csv_members():
    members = {}
    documents = set()
    unavailable = []
    row_count = 0
    audit_count = 0
    child_rows = {}
    child_fields = {}

    for group, doctypes in DATASETS.items():
        for doctype in doctypes:
            if not _installed(doctype):
                unavailable.append(doctype)
                continue
            meta = frappe.get_meta(doctype)
            fields = safe_fields(meta)
            rows = _rows(doctype, fields)
            members[f"{group}/{_slug(doctype)}.csv"] = _csv_bytes(
                fields, [[row.get(field) for field in fields] for row in rows],
            )
            row_count += len(rows)
            if group == "audit":
                audit_count += len(rows)
            documents.update((doctype, row.name) for row in rows)

            parent_names = [row.name for row in rows]
            if not parent_names:
                continue
            for table in (field for field in meta.fields if field.fieldtype in ("Table", "Table MultiSelect")):
                child = table.options
                if not child or not _installed(child):
                    if child:
                        unavailable.append(child)
                    continue
                fields_for_child = safe_fields(frappe.get_meta(child))
                rows_for_child = _rows(child, fields_for_child, {
                    "parenttype": doctype, "parent": ["in", parent_names],
                }, child=True)
                child_fields[child] = fields_for_child
                child_rows.setdefault(child, {}).update({row.name: row for row in rows_for_child})
                documents.update((child, row.name) for row in rows_for_child)

    for doctype in sorted(child_rows):
        fields = child_fields[doctype]
        rows = list(child_rows[doctype].values())
        rows.sort(key=lambda row: (
            _text(row.get("parent")), _text(row.get("parentfield")),
            int(row.get("idx") or 0), _text(row.get("name")),
        ))
        members[f"children/{_slug(doctype)}.csv"] = _csv_bytes(
            fields, [[row.get(field) for field in fields] for row in rows],
        )
        row_count += len(rows)

    user_rows = _rows("User", USER_FIELDS)
    members["identities/user.csv"] = _csv_bytes(
        USER_FIELDS, [[row.get(field) for field in USER_FIELDS] for row in user_rows],
    )
    row_count += len(user_rows)
    documents.update(("User", row.name) for row in user_rows)
    role_fields = ("parent", "role", "idx")
    role_rows = _rows("Has Role", role_fields, {"parenttype": "User"}, child=True)
    members["identities/roles.csv"] = _csv_bytes(
        role_fields, [[row.get(field) for field in role_fields] for row in role_rows],
    )
    row_count += len(role_rows)
    return members, documents, sorted(set(unavailable)), row_count, audit_count


def attachment_archive_path(file_name: str, original_name: str) -> str:
    suffix = Path(original_name or "").suffix.lower().lstrip(".")
    extension = f".{suffix}" if re.fullmatch(r"[a-z0-9]{1,10}", suffix) else ""
    digest = hashlib.sha256(file_name.encode("utf-8")).hexdigest()[:16]
    return f"attachments/files/{digest}{extension}"


def _attachment_content(row):
    try:
        document = frappe.get_doc("File", row.name)
        if document.is_remote_file:
            raise AttachmentReadError("remote attachment")
        content = document.get_content()
        return content.encode("utf-8") if isinstance(content, str) else bytes(content)
    except AttachmentReadError:
        raise
    except Exception as exc:
        raise AttachmentReadError("attachment unavailable") from exc


def _collect_attachment_members(documents):
    members = {}
    index_rows = []
    if documents:
        attached_types = sorted({doctype for doctype, _ in documents})
        rows = frappe.get_all("File", filters={
            "attached_to_doctype": ["in", attached_types],
        }, fields=["name", "file_name", "file_url", "is_private", "attached_to_doctype", "attached_to_name"],
            order_by="creation, name")
        for row in rows:
            if (row.attached_to_doctype, row.attached_to_name) not in documents:
                continue
            content = _attachment_content(row)
            path = attachment_archive_path(row.name, row.file_name)
            members[path] = content
            index_rows.append([
                row.name, row.attached_to_doctype, row.attached_to_name, row.file_name,
                int(row.is_private or 0), len(content), hashlib.sha256(content).hexdigest(), path,
            ])
    headers = (
        "file_id", "attached_to_doctype", "attached_to_name", "original_filename",
        "is_private", "size", "sha256", "archive_path",
    )
    members["attachments/index.csv"] = _csv_bytes(headers, index_rows)
    return members, len(index_rows)


def _manifest(request, members, unavailable, row_count, attachment_count, audit_count):
    return {
        "schema": SCHEMA,
        "request": request.name,
        "requested_at": _text(request.requested_at),
        "row_count": row_count,
        "attachment_count": attachment_count,
        "audit_count": audit_count,
        "unavailable": unavailable,
        "members": [
            {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in members.items()
        ],
    }


def _zip_info(name: str):
    info = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def _write_zip(target: Path, manifest, members):
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        archive.writestr(_zip_info("manifest.json"), encoded)
        for name, content in members.items():
            archive.writestr(_zip_info(name), content)


def _verify_zip(target: Path, expected):
    with ZipFile(target) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or names[0] != "manifest.json":
            raise ValueError("invalid archive members")
        stored = json.loads(archive.read("manifest.json"))
        if stored.get("schema") != SCHEMA or stored != expected:
            raise ValueError("invalid archive manifest")
        expected_names = ["manifest.json"] + [item["path"] for item in stored["members"]]
        if names != expected_names:
            raise ValueError("archive member order mismatch")
        for item in stored["members"]:
            content = archive.read(item["path"])
            if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("archive member hash mismatch")


def build_customer_exit_archive(request_name: str, target: Path) -> dict:
    request = frappe.get_doc("Customer Exit Export", request_name)
    if request.status != "Generating":
        frappe.throw("只有生成中的企业数据导出可以构建")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    members, documents, unavailable, row_count, audit_count = _collect_csv_members()
    attachment_members, attachment_count = _collect_attachment_members(documents)
    members["README.txt"] = README
    ordered = {"README.txt": members.pop("README.txt")}
    ordered.update(members)
    ordered.update(attachment_members)
    manifest = _manifest(
        request, ordered, unavailable, row_count, attachment_count, audit_count,
    )
    _write_zip(target, manifest, ordered)
    _verify_zip(target, manifest)
    return manifest
