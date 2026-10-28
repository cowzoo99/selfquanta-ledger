export async function call(method, args = {}) {
  const response = await frappe.call({method: `internal_ledger.api.review_api.${method}`, args});
  return response.message;
}

export function errorMessage(error) {
  try {
    const messages = JSON.parse(error._server_messages || error.responseJSON?._server_messages || '[]');
    if (messages.length) return messages.map(value => JSON.parse(value).message.replace(/<[^>]+>/g, '')).join('；');
  } catch (_) { /* Use the transport message below. */ }
  return error.message || '请求失败，请刷新后重试。';
}
