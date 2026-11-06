const ledgerRoles = new Set(["账套管理员", "账务查看者", "企业负责人", "财务主管", "会计", "出纳",
  "销售", "采购", "库存", "项目", "人事工资", "只读审计"]);

function redirectSetupHomeToLedger() {
  const route = frappe.get_route();
  if (!Array.isArray(route) || !frappe.app?.sidebar) return false;
  const canUseLedger = frappe.session.user === "Administrator"
    || (frappe.user_roles || []).some((role) => ledgerRoles.has(role));
  const isLegacySetupHome = route[0] === "setup" && route[1] === "home";
  const isCurrentSetupHome = route[0] === "Workspaces" && route[1] === "Home"
    && window.location.pathname === "/desk/setup/home";
  const isDeskLauncher = window.location.pathname === "/desk" && route[0] === "";
  const isJournalEntryList = (route[0] === "List" && route[1] === "Journal Entry")
    || (["/app/journal-entry", "/desk/journal-entry"].includes(window.location.pathname)
      && route[0] !== "Form");
  if (canUseLedger && isJournalEntryList) {
    frappe.set_route("ledger-workbench", "accounting");
    return true;
  }
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
