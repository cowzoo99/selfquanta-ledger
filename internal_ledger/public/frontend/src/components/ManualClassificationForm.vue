<script setup>
import {computed, ref, watch} from 'vue';
import {errorMessage} from '../api';
import {ACCOUNT_PRESETS, transactionTypes} from '../labels';
const props = defineProps({transaction: Object, disabled: Boolean});
const emit = defineEmits(['classified']);
const accounts = ref([]), busy = ref(false), error = ref('');
const kind = ref('expense'), debit = ref(''), credit = ref(''), summary = ref(''), remember = ref(false);
const kindText = ref(''), debitText = ref(''), creditText = ref(''), ruleConflict = ref(false);
const selectedPreset = ref(''), showAccounts = ref(false), loadingAccounts = ref(false);
const kindOptions = computed(() => Object.entries(transactionTypes).map(([value, label]) => ({value, label})));
const transferAccount = computed({
  get: () => props.transaction.direction === 'income' ? credit.value : debit.value,
  set: value => { if (props.transaction.direction === 'income') credit.value = value; else debit.value = value; syncChoiceText(); },
});
const transferAccounts = computed(() => accounts.value.filter(account =>
  account.root_type === 'Asset' && account.name !== props.transaction.payment_account));
function accountLabel(name) {
  const account = accounts.value.find(account => account.name === name);
  return account ? `${account.account_number} · ${account.account_name}` : name ? '流水资金账户' : '待选择';
}
const accountChoice = account => `${account.account_number} · ${account.account_name}`;
function syncChoiceText() {
  kindText.value = transactionTypes[kind.value] || '';
  debitText.value = debit.value ? accountLabel(debit.value) : '';
  creditText.value = credit.value ? accountLabel(credit.value) : '';
}
function chooseKind(value) {
  kindText.value = value;
  const option = kindOptions.value.find(option => option.value === value || option.label === value);
  kind.value = option?.value || '';
}
function chooseAccount(side, value) {
  const text = side === 'debit' ? debitText : creditText;
  const target = side === 'debit' ? debit : credit;
  text.value = value;
  const account = accounts.value.find(account => account.name === value || accountChoice(account) === value);
  target.value = account?.name || '';
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
  syncChoiceText();
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
  ruleConflict.value = false; syncChoiceText();
  try {
    const response = await frappe.call({method: 'internal_ledger.api.classification_api.list_leaf_accounts'});
    if (props.transaction.name === txName) { accounts.value = response.message; syncChoiceText(); }
  } catch (e) { if (props.transaction.name === txName) error.value = errorMessage(e); }
  finally { if (props.transaction.name === txName) loadingAccounts.value = false; }
}, {immediate: true});
async function submit() {
  busy.value = true; error.value = ''; ruleConflict.value = false;
  try {
    const response = await frappe.call({method: 'internal_ledger.api.classification_api.classify_manually', type: 'POST', args: {
      tx_name: props.transaction.name, transaction_type: kind.value, debit_account: debit.value,
      credit_account: credit.value, summary: summary.value, remember_counterparty: remember.value ? 1 : 0,
    }});
    emit('classified', response.message);
  } catch (e) {
    const message = errorMessage(e);
    ruleConflict.value = message.includes('交易对象精确规则冲突');
    error.value = ruleConflict.value
      ? '该交易对象已有另一套精确分类规则。可仅处理本笔，或先核对已有规则。' : message;
  }
  finally { busy.value = false; }
}
function submitWithoutRule() { remember.value = false; return submit(); }
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
      <div v-if="!showAccounts" class="ledger-choice-tags" aria-label="当前分类选择">
        <span class="ledger-choice-tag">业务类型：{{ transactionTypes[kind] || '待选择' }}</span>
        <span class="ledger-choice-tag">借方：{{ accountLabel(debit) }}</span>
        <span class="ledger-choice-tag">贷方：{{ accountLabel(credit) }}</span>
      </div>
      <template v-if="selectedPreset === 'transfer' && !showAccounts">
        <label for="manual-transfer">另一资金科目</label>
        <select id="manual-transfer" v-model="transferAccount" required>
          <option value="" disabled>请选择另一资金科目</option>
          <option v-for="account in transferAccounts" :key="account.name" :value="account.name">{{ account.account_number }} · {{ account.account_name }}</option>
        </select>
      </template>
      <button type="button" :aria-expanded="showAccounts" @click="showAccounts = !showAccounts">更多中文科目</button>
      <div v-if="showAccounts">
        <div class="ledger-choice-row"><div><label for="manual-kind">业务类型（可输入或选择）</label>
          <input id="manual-kind" :value="kindText" list="manual-business-types" autocomplete="off" required @input="chooseKind($event.target.value)"></div>
          <span class="ledger-choice-tag">{{ transactionTypes[kind] || '待选择' }}</span></div>
        <datalist id="manual-business-types"><option v-for="option in kindOptions" :key="option.value" :value="option.label" /></datalist>
        <div class="ledger-choice-row"><div><label for="manual-debit">借方科目（可输入或选择）</label>
          <input id="manual-debit" :value="debitText" list="manual-accounts" autocomplete="off" required @input="chooseAccount('debit', $event.target.value)"></div>
          <span class="ledger-choice-tag">借方：{{ accountLabel(debit) }}</span></div>
        <div class="ledger-choice-row"><div><label for="manual-credit">贷方科目（可输入或选择）</label>
          <input id="manual-credit" :value="creditText" list="manual-accounts" autocomplete="off" required @input="chooseAccount('credit', $event.target.value)"></div>
          <span class="ledger-choice-tag">贷方：{{ accountLabel(credit) }}</span></div>
        <datalist id="manual-accounts"><option v-for="account in accounts" :key="account.name" :value="accountChoice(account)" /></datalist>
      </div>
      <label for="manual-summary">凭证摘要</label><textarea id="manual-summary" v-model="summary" />
      <label><input v-model="remember" type="checkbox" :disabled="!transaction.counterparty?.trim()">记住该交易对象的精确分类规则</label>
      <div v-if="ruleConflict" role="group" aria-label="分类规则冲突处理">
        <button type="button" @click="submitWithoutRule">仅处理本笔（不保存规则）</button>
        <a href="/app/classification-rule">前往核对已有分类规则</a>
      </div>
      <button type="submit" :disabled="!kind || !debit || !credit || debit === credit">{{ busy ? '正在生成…' : '人工分类并生成凭证' }}</button>
    </fieldset>
  </form>
</template>
