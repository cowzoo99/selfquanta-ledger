frappe.pages['ledger-workbench'].on_page_load = function(wrapper) {
  const page = frappe.ui.make_app_page({parent: wrapper, title: 'SelfLedger 企业经营与智能内账', single_column: true});
  const root = document.createElement('div');
  page.main[0].appendChild(root);
  wrapper.ledger_root = root;
  frappe.require('ledger_workbench.bundle.js', () => {
    wrapper.ledger_ready = true;
    frappe.pages['ledger-workbench'].on_page_show(wrapper);
  });
};

frappe.pages['ledger-workbench'].on_page_show = function(wrapper) {
  if (!wrapper.ledger_ready) return;
  const route = frappe.get_route();
  const view = route[1] || 'dashboard', filter = route[2] || '';
  const key = JSON.stringify([view, filter]);
  if (wrapper.ledger_route === key) return;
  wrapper.ledger_app?.unmount();
  wrapper.ledger_app = internal_ledger.mount_workbench(wrapper.ledger_root, {view, filter});
  wrapper.ledger_route = key;
};
