const ledgerRoles = new Set(["账套管理员", "账务查看者"]);

function redirectSetupHomeToLedger() {
  const route = frappe.get_route();
  const canUseLedger = frappe.session.user === "Administrator"
    || (frappe.user_roles || []).some((role) => ledgerRoles.has(role));
  const isLegacySetupHome = route[0] === "setup" && route[1] === "home";
  const isCurrentSetupHome = route[0] === "Workspaces" && route[1] === "Home"
    && window.location.pathname === "/desk/setup/home";
  if (canUseLedger && (isLegacySetupHome || isCurrentSetupHome)) {
    frappe.set_route("ledger-workbench", "dashboard");
  }
}

$(document).on("app_ready", redirectSetupHomeToLedger);
frappe.router.on("change", redirectSetupHomeToLedger);
setTimeout(redirectSetupHomeToLedger, 0);
