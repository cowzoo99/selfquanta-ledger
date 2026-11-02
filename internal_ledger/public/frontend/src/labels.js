export const directions = {expense: '支出', income: '收入'};
export const sources = {bank: '银行卡', wechat: '微信支付', alipay: '支付宝'};
export const transactionTypes = {
  expense: '费用支出', income: '经营收入', transfer: '账户转账',
  refund: '退款', fee: '手续费', capital: '股东投入',
};
export const transactionStatuses = {Imported: '已导入', Duplicate: '重复流水'};
export const draftStatuses = {Draft: '待审核', Approved: '已审核', Posted: '已过账', Rejected: '已驳回'};
export const processingStatuses = {
  created: '已生成草稿', manual: '待人工分类', duplicate: '待重复复核',
  conflict: '规则冲突', skipped: '已跳过', failed: '处理失败',
};
export const classificationStatuses = {matched: '已匹配', unmatched: '待人工分类', conflict: '规则冲突'};
export const ACCOUNT_PRESETS = {
  office: {label: '办公费', transactionType: 'expense', accountNumber: '560201', direction: 'expense'},
  travel: {label: '差旅费', transactionType: 'expense', accountNumber: '560202', direction: 'expense'},
  purchase: {label: '采购支出', transactionType: 'expense', accountNumber: '560201', direction: 'expense'},
  income: {label: '经营收入', transactionType: 'income', accountNumber: '5001', direction: 'income'},
  fee: {label: '手续费', transactionType: 'fee', accountNumber: '5603', direction: 'expense'},
  capital: {label: '股东投入', transactionType: 'capital', accountNumber: '3001', direction: 'income'},
  transfer: {label: '账户转账', transactionType: 'transfer'},
  other: {label: '其他'},
};
