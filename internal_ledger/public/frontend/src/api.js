export async function call(method, args = {}) {
  const response = await frappe.call({method: `internal_ledger.api.review_api.${method}`, args});
  return response.message;
}

export async function evidenceCall(method, args = {}) {
  const response = await frappe.call({method: `internal_ledger.api.evidence_api.${method}`, args,
    type: method === 'get_evidence' ? 'GET' : 'POST'});
  return response.message;
}

export async function memberCall(method, args = {}) {
  const response = await frappe.call({method: `internal_ledger.api.member_api.${method}`, args,
    type: ['list_enterprise_members', 'get_my_account'].includes(method) ? 'GET' : 'POST'});
  return response.message;
}

export async function financeCall(domain, method, args = {}) {
  const reads = ['get_finance_context', 'get_latest_month_end_run', 'get_month_end_run',
    'get_latest_tax_material_package', 'get_tax_material_package'];
  const response = await frappe.call({method: `internal_ledger.api.${domain}_api.${method}`, args,
    type: reads.includes(method) ? 'GET' : 'POST'});
  return response.message;
}

export async function businessCall(method, args = {}) {
  const response = await frappe.call({method: `internal_ledger.api.business_api.${method}`, args, type: 'GET'});
  return response.message;
}

export async function aiCall(method, args = {}) {
  const response = await frappe.call({method: `internal_ledger.api.ai_api.${method}`, args,
    type: method === 'get_assistant_status' ? 'GET' : 'POST'});
  return response.message;
}

export async function exportCall(method, args = {}) {
  const response = await frappe.call({method: `internal_ledger.api.export_api.${method}`, args,
    type: method === 'list_customer_exit_exports' ? 'GET' : 'POST'});
  return response.message;
}

export async function setLanguage(language) {
  const response = await frappe.call({method: 'internal_ledger.api.language_api.set_language',
    args: {language}, type: 'POST'});
  return response.message;
}

export function errorMessage(error) {
  try {
    const messages = JSON.parse(error._server_messages || error.responseJSON?._server_messages || '[]');
    if (messages.length) return messages.map(value => JSON.parse(value).message.replace(/<[^>]+>/g, '')).join('；');
  } catch (_) { /* Use the transport message below. */ }
  return error.message || '请求失败，请刷新后重试。';
}
