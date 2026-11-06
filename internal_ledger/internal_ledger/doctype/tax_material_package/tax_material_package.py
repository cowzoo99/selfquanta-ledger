import frappe
from frappe.model.document import Document


class TaxMaterialPackage(Document):
    def validate(self):
        old = self.get_doc_before_save()
        if old and old.status == "Ready":
            frappe.throw("已生成的税务材料包不可修改，请生成新版本")

    def on_trash(self):
        frappe.throw("税务材料包是审计记录，不允许删除")
