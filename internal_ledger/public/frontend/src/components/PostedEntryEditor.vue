<script setup>
import {computed, onBeforeUnmount, ref, watch} from 'vue';
import TransactionEvidence from './TransactionEvidence.vue';
import {directions, transactionTypes} from '../labels';
import {errorMessage} from '../api';
const props = defineProps({transaction: {type: String, required: true}, canWrite: Boolean, evidenceOnly: Boolean, disabled: Boolean});
const emit = defineEmits(['saved', 'deleted', 'closed']);
const aggregate = ref(null), form = ref(null), accounts = ref([]);
const busy = ref(false), loading = ref(false), error = ref('');
const canEdit = computed(() => props.canWrite && !props.disabled && !!aggregate.value && !busy.value && !loading.value);
const occurredAt = computed({get: () => form.value.occurred_at.replace(' ', 'T'),
  set: value => {form.value.occurred_at = value.replace('T', ' ');}});
const debitLines = ref([]), creditLines = ref([]);
let generation = 0;
function accountLabel(name) {
  const account = accounts.value.find(row => row.name === name);
  return account ? [account.account_number, account.account_name].filter(Boolean).join(' · ') : name;
}
async function load() {
  const request = ++generation;
  aggregate.value = null; form.value = null; accounts.value = []; error.value = ''; busy.value = false;
  if (props.evidenceOnly) {loading.value = false; return;}
  loading.value = true;
  try {
    const [entry, choices] = await Promise.all([
      frappe.call({method: 'internal_ledger.api.posting_api.get_posted_entry', type: 'GET', args: {transaction: props.transaction}}),
      frappe.call({method: 'internal_ledger.api.classification_api.list_leaf_accounts', type: 'GET'}),
    ]);
    if (request !== generation) return;
    const data = entry.message, tx = data.transaction, draft = data.draft;
    aggregate.value = data; accounts.value = choices.message;
    form.value = {occurred_at: tx.occurred_at, posting_date: draft.posting_date, counterparty: tx.counterparty || '',
      summary: tx.summary || '', direction: tx.direction, transaction_type: draft.transaction_type,
      amount: Number(tx.amount), payment_account: tx.payment_account,
      lines: draft.lines.map(line => ({account: line.account, debit: Number(line.debit || 0), credit: Number(line.credit || 0)}))};
    debitLines.value = form.value.lines.filter(line => line.debit > 0);
    creditLines.value = form.value.lines.filter(line => line.credit > 0);
    if (!debitLines.value.length) addInitialLine(debitLines);
    if (!creditLines.value.length) addInitialLine(creditLines);
  } catch (e) {if (request === generation) error.value = errorMessage(e);}
  finally {if (request === generation) loading.value = false;}
}
watch(() => [props.transaction, props.evidenceOnly], load, {immediate: true});
onBeforeUnmount(() => {generation++;});
function addInitialLine(target) {
  const line = {account: '', debit: 0, credit: 0};
  form.value.lines.push(line); target.value.push(line);
}
function addLine(side) {
  if (!canEdit.value) return;
  addInitialLine(side === 'debit' ? debitLines : creditLines);
}
function removeLine(line, side) {
  if (!canEdit.value || (side === 'debit' ? debitLines.value : creditLines.value).length <= 1) return;
  form.value.lines.splice(form.value.lines.indexOf(line), 1);
  const target = side === 'debit' ? debitLines.value : creditLines.value;
  target.splice(target.indexOf(line), 1);
}
async function save() {
  if (!canEdit.value) return;
  if (!debitLines.value.length || !creditLines.value.length) {error.value = '借方和贷方至少各保留一条分录'; return;}
  const request = generation;
  busy.value = true; error.value = '';
  try {
    await frappe.call({method: 'internal_ledger.api.posting_api.rebuild_posted_entry', type: 'POST',
      args: {transaction: props.transaction, values: JSON.stringify(form.value), expected_version: aggregate.value.version}});
    if (request === generation) emit('saved');
  } catch (e) {if (request === generation) error.value = errorMessage(e);}
  finally {if (request === generation) busy.value = false;}
}
async function remove() {
  if (!canEdit.value) return;
  const reason = window.prompt('请输入永久删除原因');
  if (!reason?.trim()) return;
  if (!window.confirm('此操作将永久删除流水、附件、凭证和总账，且不可恢复。确认继续？')) return;
  const request = generation, transaction = props.transaction;
  busy.value = true; error.value = '';
  try {
    const response = await frappe.call({method: 'internal_ledger.api.posting_api.permanently_delete_transactions', type: 'POST',
      args: {transactions: JSON.stringify([transaction]), reason: reason.trim(),
        expected_versions: JSON.stringify({[transaction]: aggregate.value.version})}});
    if (request !== generation) return;
    const result = response.message.find(row => row.transaction === transaction);
    if (result?.status === 'deleted') emit('deleted');
    else error.value = result?.message || '永久删除失败，请刷新后重试';
  } catch (e) {if (request === generation) error.value = errorMessage(e);}
  finally {if (request === generation) busy.value = false;}
}
</script>

<template>
  <section aria-label="整笔账目">
    <div class="ledger-edit-heading">
      <h3>{{ evidenceOnly ? '备注与附件' : canWrite ? '编辑整笔账目' : '查看整笔账目' }}</h3>
      <button type="button" :disabled="busy || disabled" @click="emit('closed')">关闭</button>
    </div>
    <p v-if="loading" role="status">正在载入账目…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <button v-if="!loading && !form && !evidenceOnly" type="button" @click="load">重试</button>
    <form v-if="form && canWrite" @submit.prevent="save">
      <fieldset :disabled="!canEdit">
        <div class="ledger-edit-fields">
          <label>交易日期<input v-model="occurredAt" aria-label="交易日期" type="datetime-local" step="1" required></label>
          <label>过账日期<input v-model="form.posting_date" aria-label="过账日期" type="date" required></label>
          <label>交易对象<input v-model="form.counterparty" aria-label="交易对象" type="text"></label>
          <label>摘要<input v-model="form.summary" aria-label="摘要" type="text"></label>
          <label>收支方向<select v-model="form.direction" aria-label="收支方向"><option v-for="(label, value) in directions" :key="value" :value="value">{{ label }}</option></select></label>
          <label>业务分类<select v-model="form.transaction_type" aria-label="业务分类"><option v-for="(label, value) in transactionTypes" :key="value" :value="value">{{ label }}</option></select></label>
          <label>金额<input v-model.number="form.amount" aria-label="金额" type="number" min="0.01" step="0.01" required></label>
          <label>支付账户<input v-model="form.payment_account" aria-label="支付账户" list="posted-entry-accounts" required></label>
        </div>
        <datalist id="posted-entry-accounts"><option v-for="account in accounts" :key="account.name" :value="account.name">{{ accountLabel(account.name) }}</option></datalist>
        <section v-for="side in ['debit', 'credit']" :key="side">
          <h4>{{ side === 'debit' ? '借方分录' : '贷方分录' }}</h4>
          <div v-for="(line, index) in (side === 'debit' ? debitLines : creditLines)" :key="index" class="ledger-edit-line">
            <label>科目<input v-model="line.account" :aria-label="`${side === 'debit' ? '借方' : '贷方'}科目 ${index + 1}`" list="posted-entry-accounts" required></label>
            <label>金额<input v-model.number="line[side]" :aria-label="`${side === 'debit' ? '借方' : '贷方'}金额 ${index + 1}`" type="number" min="0.01" step="0.01" required></label>
            <button type="button" :disabled="(side === 'debit' ? debitLines : creditLines).length <= 1" @click="removeLine(line, side)">删除{{ side === 'debit' ? '借方' : '贷方' }}分录</button>
          </div>
          <button type="button" @click="addLine(side)">添加{{ side === 'debit' ? '借方' : '贷方' }}分录</button>
        </section>
      </fieldset>
      <button type="submit" :disabled="!canEdit">保存整笔账目</button>
      <button type="button" :disabled="!canEdit" @click="remove">永久删除</button>
    </form>
    <template v-else-if="form">
      <dl>
        <dt>交易日期</dt><dd>{{ form.occurred_at }}</dd><dt>过账日期</dt><dd>{{ form.posting_date }}</dd>
        <dt>交易对象</dt><dd>{{ form.counterparty }}</dd><dt>摘要</dt><dd>{{ form.summary }}</dd>
        <dt>收支方向</dt><dd>{{ directions[form.direction] }}</dd><dt>业务分类</dt><dd>{{ transactionTypes[form.transaction_type] }}</dd>
        <dt>金额</dt><dd>{{ form.amount }}</dd><dt>支付账户</dt><dd>{{ accountLabel(form.payment_account) }}</dd>
      </dl>
      <section v-for="side in ['debit', 'credit']" :key="side">
        <h4>{{ side === 'debit' ? '借方分录' : '贷方分录' }}</h4>
        <p v-for="(line, index) in (side === 'debit' ? debitLines : creditLines)" :key="index">{{ accountLabel(line.account) }} · {{ line[side] }}</p>
      </section>
    </template>
    <TransactionEvidence v-if="form || evidenceOnly" :transaction="transaction" :can-write="canWrite" :disabled="disabled || busy || loading" @saved="emit('saved')" />
  </section>
</template>

<style scoped>
.ledger-edit-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.ledger-edit-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
label { display: grid; gap: 4px; }
.ledger-edit-line { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; margin-bottom: 8px; }
</style>
