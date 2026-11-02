<script setup>
import {computed, ref, watch} from 'vue';
import {errorMessage} from '../api';
import {ACCOUNT_PRESETS, transactionTypes} from '../labels';
const props = defineProps({transaction: Object, disabled: Boolean});
const emit = defineEmits(['classified']);
const accounts = ref([]), busy = ref(false), error = ref('');
const kind = ref('expense'), debit = ref(''), credit = ref(''), summary = ref(''), remember = ref(false);
const selectedPreset = ref(''), showAccounts = ref(false), loadingAccounts = ref(false);
const transferAccount = computed({
  get: () => props.transaction.direction === 'income' ? credit.value : debit.value,
  set: value => { if (props.transaction.direction === 'income') credit.value = value; else debit.value = value; },
});
const transferAccounts = computed(() => accounts.value.filter(account =>
  account.root_type === 'Asset' && account.name !== props.transaction.payment_account));
function accountLabel(name) {
  const account = accounts.value.find(account => account.name === name);
  return account ? `${account.account_number} · ${account.account_name}` : name ? '流水资金账户' : '待选择';
}
function selectPreset(key) {
  const preset = ACCOUNT_PRESETS[key];
  if (preset.direction && preset.direction !== props.transaction.direction) return;
  selectedPreset.value = key;
  showAccounts.value = key === 'other';
  if (key === 'other') return;
  kind.value = preset.transactionType;
  const account = accounts.value.find(account => account.account_number === preset.accountNumber)?.name || '';
  debit.value = props.transaction.direction === 'income' ? props.transaction.payment_account : account;
  credit.value = props.transaction.direction === 'expense' ? props.transaction.payment_account : account;
  if (preset.accountNumber && !account) {
    error.value = '建议科目不可用，请选择其他中文科目或检查科目设置。';
  } else error.value = '';
}
watch(() => props.transaction.name, async () => {
  const txName = props.transaction.name;
  selectedPreset.value = ''; showAccounts.value = false; accounts.value = []; loadingAccounts.value = true;
  kind.value = props.transaction.direction === 'income' ? 'income' : 'expense';
  debit.value = props.transaction.direction === 'income' ? props.transaction.payment_account : '';
  credit.value = props.transaction.direction === 'expense' ? props.transaction.payment_account : '';
  summary.value = props.transaction.summary || ''; remember.value = false; error.value = '';
  try {
    const response = await frappe.call({method: 'internal_ledger.api.classification_api.list_leaf_accounts'});
    if (props.transaction.name === txName) accounts.value = response.message;
  } catch (e) { if (props.transaction.name === txName) error.value = errorMessage(e); }
  finally { if (props.transaction.name === txName) loadingAccounts.value = false; }
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
    <fieldset :disabled="disabled || busy || loadingAccounts">
      <legend>快捷分类</legend>
      <button v-for="(preset, key) in ACCOUNT_PRESETS" :key="key" type="button"
        :disabled="!!preset.direction && preset.direction !== transaction.direction"
        :aria-pressed="selectedPreset === key" @click="selectPreset(key)">{{ preset.label }}</button>
      <p>业务类型：{{ transactionTypes[kind] }}；借方：{{ accountLabel(debit) }}；贷方：{{ accountLabel(credit) }}</p>
      <template v-if="selectedPreset === 'transfer' && !showAccounts">
        <label for="manual-transfer">另一资金科目</label>
        <select id="manual-transfer" v-model="transferAccount" required>
          <option value="" disabled>请选择另一资金科目</option>
          <option v-for="account in transferAccounts" :key="account.name" :value="account.name">{{ account.account_number }} · {{ account.account_name }}</option>
        </select>
      </template>
      <button type="button" :aria-expanded="showAccounts" @click="showAccounts = !showAccounts">更多中文科目</button>
      <div v-if="showAccounts">
        <label for="manual-kind">业务类型</label><select id="manual-kind" v-model="kind">
          <option v-for="(label, value) in transactionTypes" :key="value" :value="value">{{ label }}</option>
        </select>
        <label for="manual-debit">借方科目</label><select id="manual-debit" v-model="debit" required><option value="" disabled>请选择借方科目</option><option v-for="account in accounts" :key="account.name" :value="account.name">{{ account.account_number }} · {{ account.account_name }}</option></select>
        <label for="manual-credit">贷方科目</label><select id="manual-credit" v-model="credit" required><option value="" disabled>请选择贷方科目</option><option v-for="account in accounts" :key="account.name" :value="account.name">{{ account.account_number }} · {{ account.account_name }}</option></select>
      </div>
      <label for="manual-summary">凭证摘要</label><textarea id="manual-summary" v-model="summary" />
      <label><input v-model="remember" type="checkbox" :disabled="!transaction.counterparty?.trim()">记住该交易对象的精确分类规则</label>
      <button type="submit" :disabled="!debit || !credit || debit === credit">{{ busy ? '正在生成…' : '人工分类并生成凭证' }}</button>
    </fieldset>
  </form>
</template>
