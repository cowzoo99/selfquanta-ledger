const ledgerRoles = new Set(["账套管理员", "账务查看者"]);

function redirectSetupHomeToLedger() {
  const route = frappe.get_route();
  const canUseLedger = frappe.session.user === "Administrator"
    || frappe.user_roles.some((role) => ledgerRoles.has(role));
  if (canUseLedger && route[0] === "setup" && route[1] === "home") {
    frappe.set_route("ledger-workbench", "dashboard");
  }
}

$(document).on("app_ready", redirectSetupHomeToLedger);
frappe.router.on("change", redirectSetupHomeToLedger);
setTimeout(redirectSetupHomeToLedger, 0);
