<script setup>
import {ref, watch} from 'vue';
import {errorMessage} from '../api';
const props = defineProps({transaction: Object, disabled: Boolean});
const emit = defineEmits(['classified']);
const accounts = ref([]), busy = ref(false), error = ref('');
const kind = ref('expense'), debit = ref(''), credit = ref(''), summary = ref(''), remember = ref(false);
watch(() => props.transaction.name, async () => {
  kind.value = props.transaction.direction === 'income' ? 'income' : 'expense';
  debit.value = props.transaction.direction === 'income' ? props.transaction.payment_account : '';
  credit.value = props.transaction.direction === 'expense' ? props.transaction.payment_account : '';
  summary.value = props.transaction.summary || ''; remember.value = false; error.value = '';
  try {
    const response = await frappe.call({method: 'internal_ledger.api.classification_api.list_leaf_accounts'});
    accounts.value = response.message;
  } catch (e) { error.value = errorMessage(e); }
}, {immediate: true});
async function submit() {
  busy.value = true; error.value = '';
  try {
    const response = await frappe.call({method: 'internal_ledger.api.classification_api.classify_manually', type: 'POST', args: {
      tx_name: props.transaction.name, transaction_type: kind.value, debit_account: debit.value,
      credit_account: credit.value, summary: summary.value, remember_counterparty: remember.value ? 1 : 0,
    }});
    emit('classified', response.message);
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
</script>

<template>
  <form @submit.prevent="submit">
    <h3>人工分类</h3>
    <p>核对业务类型和借贷科目后生成待审核草稿。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <fieldset :disabled="disabled || busy">
      <label for="manual-kind">业务类型</label><select id="manual-kind" v-model="kind">
        <option value="expense">费用支出</option><option value="income">经营收入</option><option value="transfer">账户转账</option>
        <option value="refund">退款</option><option value="fee">手续费</option><option value="capital">股东投入</option>
      </select>
      <label for="manual-debit">借方科目</label><select id="manual-debit" v-model="debit" required><option value="" disabled>请选择借方科目</option><option v-for="account in accounts" :key="account.name" :value="account.name">{{ account.account_number }} {{ account.account_name }}</option></select>
      <label for="manual-credit">贷方科目</label><select id="manual-credit" v-model="credit" required><option value="" disabled>请选择贷方科目</option><option v-for="account in accounts" :key="account.name" :value="account.name">{{ account.account_number }} {{ account.account_name }}</option></select>
      <label for="manual-summary">凭证摘要</label><textarea id="manual-summary" v-model="summary" />
      <label><input v-model="remember" type="checkbox" :disabled="!transaction.counterparty?.trim()">记住该交易对象的精确分类规则</label>
      <button type="submit">{{ busy ? '正在生成…' : '人工分类并生成凭证' }}</button>
    </fieldset>
  </form>
</template>
