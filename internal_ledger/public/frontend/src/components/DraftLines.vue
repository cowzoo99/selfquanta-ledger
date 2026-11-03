<script setup>
import {ref, watch, computed} from 'vue';
import {directions, sources, transactionTypes, draftStatuses, classificationStatuses} from '../labels';
const props = defineProps({lines: Array, editable: Boolean, company: String, draft: Object, transaction: Object});
const accounts = ref([]);
watch(() => [props.company, props.editable], async ([company, editable]) => {
  if (!company || !editable) return;
  try {
    const response = await frappe.call({method: 'internal_ledger.api.classification_api.list_leaf_accounts'});
    accounts.value = response.message;
  } catch (_) { accounts.value = []; }
}, {immediate: true});
const debitTotal = computed(() => (props.lines || []).reduce((sum, row) => sum + Number(row.debit || 0), 0));
const creditTotal = computed(() => (props.lines || []).reduce((sum, row) => sum + Number(row.credit || 0), 0));
const balanced = computed(() => Math.abs(debitTotal.value - creditTotal.value) < 0.000000001);
const amount = value => Number(value || 0).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 9});
function accountFor(line) {
  return accounts.value.find(account => account.name === line.account) ||
    (props.editable && accounts.value.length ? {} : line);
}
</script>

<template>
  <div class="ledger-voucher">
    <dl v-if="draft" class="voucher-metadata">
      <dt>记账日期</dt><dd>{{ draft.posting_date }}</dd><dt>摘要</dt><dd>{{ draft.summary || transaction?.summary || '—' }}</dd>
      <dt>来源 / 方向</dt><dd>{{ sources[transaction?.source] || '未知来源' }} / {{ directions[transaction?.direction] || '未知方向' }}</dd>
      <dt>业务类型</dt><dd>{{ transactionTypes[draft.transaction_type] || '待核对' }}</dd>
      <dt>分类原因</dt><dd>{{ classificationStatuses[draft.classification_reason] || draft.classification_reason }}</dd><dt>操作人</dt><dd>{{ draft.manual_override_by || draft.modified_by || draft.owner }}</dd>
      <dt>状态</dt><dd>{{ draftStatuses[draft.status] || '待核对' }}</dd>
    </dl>
    <dl class="voucher-accounts">
      <dt>借方科目</dt><dd v-for="(line, index) in (lines || []).filter(row => Number(row.debit) > 0)" :key="'debit-' + index">{{ accountFor(line).account_name || line.account }} · {{ amount(line.debit) }}</dd>
      <dt>贷方科目</dt><dd v-for="(line, index) in (lines || []).filter(row => Number(row.credit) > 0)" :key="'credit-' + index">{{ accountFor(line).account_name || line.account }} · {{ amount(line.credit) }}</dd>
    </dl>
    <div class="ledger-table">
      <table>
        <caption>凭证借贷分录 · {{ draft?.base_currency || 'CNY' }}</caption>
        <thead><tr><th scope="col">科目编码</th><th scope="col">科目名称</th><th scope="col">借方金额</th><th scope="col">贷方金额</th><th v-if="editable" scope="col">操作</th></tr></thead>
        <tbody><tr v-for="(line, index) in lines" :key="index">
          <td>{{ accountFor(line).account_number || '—' }}</td>
          <td><input v-if="editable" v-model="line.account" :aria-label="`第 ${index + 1} 行科目`" list="ledger-accounts" required><span v-else>{{ accountFor(line).account_name || line.account }}</span></td>
          <td><input v-if="editable" v-model="line.debit" :aria-label="`第 ${index + 1} 行借方金额`" type="number" min="0" step="0.000000001"><span v-else>{{ amount(line.debit) }}</span></td>
          <td><input v-if="editable" v-model="line.credit" :aria-label="`第 ${index + 1} 行贷方金额`" type="number" min="0" step="0.000000001"><span v-else>{{ amount(line.credit) }}</span></td>
          <td v-if="editable"><button type="button" :aria-label="`删除第 ${index + 1} 行`" @click="lines.splice(index, 1)">删除</button></td>
        </tr></tbody>
        <tfoot><tr><th colspan="2" scope="row">借贷合计</th><td>{{ amount(debitTotal) }}</td><td>{{ amount(creditTotal) }}</td><td v-if="editable" /></tr></tfoot>
      </table>
      <datalist id="ledger-accounts"><option v-for="account in accounts" :key="account.name" :value="account.name">{{ account.account_number }} {{ account.account_name }}</option></datalist>
      <p role="status">{{ balanced ? '借贷平衡' : '借贷不平，请核对分录' }}</p>
      <button v-if="editable" type="button" @click="lines.push({account: '', debit: '0', credit: '0'})">添加分录</button>
      <p v-if="!lines?.length">暂无分录。</p>
    </div>
    <details v-if="draft"><summary>技术信息</summary>
      <dl><dt>草稿编号</dt><dd>{{ draft.name }}</dd><dt>来源流水</dt><dd>{{ draft.imported_transaction }}</dd>
        <dt>批次编号</dt><dd>{{ transaction?.import_batch }}</dd><dt>规则编号</dt><dd>{{ draft.classification_rule || '无' }}</dd></dl>
    </details>
  </div>
</template>
