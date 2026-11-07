import re


DATASETS = {
    "master-data": (
        "Company", "Account", "Cost Center", "Customer", "Supplier", "Contact", "Address",
        "Item", "Item Group", "Warehouse", "Project", "Employee", "Department", "Designation",
    ),
    "transactions": (
        "Lead", "Opportunity", "Quotation", "Sales Order", "Delivery Note", "Sales Invoice",
        "Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry", "Stock Entry",
        "Expense Claim", "Employee Advance", "Task", "Timesheet", "Attendance", "Leave Application",
        "Payroll Entry", "Salary Slip", "Journal Entry", "GL Entry", "Import Batch",
        "Imported Transaction", "Classification Rule", "Voucher Draft", "Posting Link",
        "Month End Run", "Tax Material Package",
    ),
    "audit": ("Version", "Activity Log", "Internal Ledger Audit Event", "AI Interaction Audit"),
}

DENIED_FIELD_PATTERN = re.compile(
    r"(^|_)(password|passwd|secret|token|otp|private_key|encryption_key|reset_key|api_key)(_|$)",
    re.IGNORECASE,
)
DENIED_FIELDTYPES = {"Password"}
NO_VALUE_FIELDTYPES = {
    "Section Break", "Column Break", "Tab Break", "HTML", "Button", "Heading", "Fold",
    "Table", "Table MultiSelect",
}
STANDARD_FIELDS = ("name", "creation", "modified", "modified_by", "owner", "docstatus", "idx")
USER_FIELDS = (
    "name", "email", "first_name", "last_name", "full_name", "enabled", "user_type",
    "creation", "modified",
)
DENIED_FIELDS_BY_DOCTYPE = {"Version": {"data"}}


def safe_fields(meta):
    if meta.name == "User":
        return list(USER_FIELDS)
    denied = DENIED_FIELDS_BY_DOCTYPE.get(meta.name, set())
    fields = list(STANDARD_FIELDS)
    for field in meta.fields:
        if not field.fieldname or field.fieldname in denied:
            continue
        if field.fieldtype in DENIED_FIELDTYPES | NO_VALUE_FIELDTYPES:
            continue
        if getattr(field, "is_virtual", False) or DENIED_FIELD_PATTERN.search(field.fieldname):
            continue
        if field.fieldname not in fields:
            fields.append(field.fieldname)
    return fields
