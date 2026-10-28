frappe.pages['ledger-workbench'].on_page_load = function(wrapper) {
  const page = frappe.ui.make_app_page({parent: wrapper, title: '协同内账工作台', single_column: true});
  const root = document.createElement('div');
  page.main[0].appendChild(root);
  frappe.require('ledger_workbench.bundle.js', () => internal_ledger.mount_workbench(root));
};
