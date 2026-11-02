const ledgerRoles = new Set(["账套管理员", "账务查看者"]);

function redirectSetupHomeToLedger() {
  const route = frappe.get_route();
  const canUseLedger = frappe.session.user === "Administrator"
    || (frappe.user_roles || []).some((role) => ledgerRoles.has(role));
  const isLegacySetupHome = route[0] === "setup" && route[1] === "home";
  const isCurrentSetupHome = route[0] === "Workspaces" && route[1] === "Home"
    && window.location.pathname === "/desk/setup/home";
  const isDeskLauncher = window.location.pathname === "/desk" && route[0] === "";
  if (canUseLedger && isDeskLauncher) {
    window.location.assign("/desk/ledger-workbench/dashboard");
    return true;
  }
  if (canUseLedger && (isLegacySetupHome || isCurrentSetupHome)) {
    frappe.set_route("ledger-workbench", "dashboard");
    return true;
  }
  return false;
}

$(document).on("app_ready", redirectSetupHomeToLedger);
frappe.router.on("change", redirectSetupHomeToLedger);
let redirectAttempts = 0;
const redirectTimer = setInterval(() => {
  redirectAttempts += 1;
  if (redirectSetupHomeToLedger() || redirectAttempts >= 200) {
    clearInterval(redirectTimer);
  }
}, 100);
