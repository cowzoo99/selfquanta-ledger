<script setup>
import {ref, computed, watch} from 'vue';
import DraftLines from '../components/DraftLines.vue';
import ManualClassificationForm from '../components/ManualClassificationForm.vue';
import TransactionEvidence from '../components/TransactionEvidence.vue';
import {call, errorMessage} from '../api';
import {directions, sources, transactionTypes, draftStatuses, processingStatuses, classificationStatuses} from '../labels';
const props = defineProps({list: {type: Object, required: true}, classificationRun: Object, canWrite: Boolean, initialFilter: String, initialBatch: String, initialTransaction: String});
const emit = defineEmits(['refresh']);
const selected = ref([]), active = ref(null), busy = ref(false), error = ref(''), outcomes = ref([]), reason = ref('');
const savedVersion = ref(''), filter = ref('待分类'), confirmation = ref('');
const currentPage = ref(1), pageSize = 10;
let activeTransaction = '';
const filters = ['疑似重复', '待分类', '待审核', '待过账', '已过账'];
watch(() => props.initialFilter, value => { filter.value = filters.includes(value) ? value : '待分类'; }, {immediate: true});
function duplicate(item) {
  return item.transaction.status === 'Duplicate' || ['Pending', 'Confirmed'].includes(item.transaction.duplicate_review_status);
}
function stage(item) {
  if (duplicate(item)) return item.transaction.duplicate_review_status === 'Pending' ? '疑似重复' : '';
  if (!item.draft) return '待分类';
  return item.draft.status === 'Rejected' ? '待审核' : item.draft.status === 'Approved' ? '待过账' : draftStatuses[item.draft.status];
}
const listReady = computed(() => props.list.status === 'success' && props.classificationRun?.status !== 'loading');
const scopedItems = computed(() => listReady.value ? props.list.data.filter(item => (!props.initialBatch || item.transaction.import_batch === props.initialBatch) &&
  (!props.initialTransaction || item.transaction.name === props.initialTransaction)) : []);
const visibleItems = computed(() => scopedItems.value.filter(item => stage(item) === filter.value));
const pageCount = computed(() => Math.max(1, Math.ceil(visibleItems.value.length / pageSize)));
const pagedItems = computed(() => visibleItems.value.slice((currentPage.value - 1) * pageSize, currentPage.value * pageSize));
function previousPage() { activeTransaction = ''; currentPage.value = Math.max(1, currentPage.value - 1); active.value = null; }
function nextPage() { activeTransaction = ''; currentPage.value = Math.min(pageCount.value, currentPage.value + 1); active.value = null; }
watch(() => [filter.value, props.initialBatch, props.initialTransaction], () => {
  activeTransaction = ''; active.value = null; currentPage.value = 1;
});
watch(() => [pageCount.value, listReady.value], ([value, ready]) => {
  if (ready) currentPage.value = Math.min(currentPage.value, value);
});
const editing = computed(() => props.canWrite && active.value?.draft?.status === 'Draft');
const dirty = computed(() => active.value?.draft && JSON.stringify(active.value.draft) !== savedVersion.value);
function open(item) {
  activeTransaction = item.transaction.name;
  active.value = JSON.parse(JSON.stringify(item)); savedVersion.value = JSON.stringify(active.value.draft);
  reason.value = ''; error.value = '';
}
async function classified(doc) {
  active.value = await call('get_review_item', {name: doc.name});
  savedVersion.value = JSON.stringify(active.value.draft); emit('refresh');
}
async function act(method, args) {
  busy.value = true; error.value = '';
  try { await classified(await call(method, args)); }
  catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
function save() {
  const doc = active.value.draft;
  return act('update_draft', {name: doc.name, lines: JSON.stringify(doc.lines), summary: doc.summary || '', posting_date: doc.posting_date});
}
const postEligible = item => !duplicate(item) && item.transaction.status === 'Imported' && !item.error &&
  (item.draft?.status === 'Approved' || (item.draft?.status === 'Draft' && item.can_approve));
function postingTotals(item) {
  const amount = Number(item.transaction.amount), lines = item.draft?.lines || [];
  let debit = 0, credit = 0;
  let anomaly = !postEligible(item) || !Number.isFinite(amount) || amount <= 0 || !lines.length;
  for (const line of lines) {
    const dr = Number(line.debit || 0), cr = Number(line.credit || 0);
    if (!line.account || !Number.isFinite(dr) || !Number.isFinite(cr) || dr < 0 || cr < 0 || (dr > 0) === (cr > 0)) anomaly = true;
    if (Number.isFinite(dr)) debit += dr;
    if (Number.isFinite(cr)) credit += cr;
  }
  return {debit, credit, anomaly: anomaly || debit <= 0 || Math.abs(debit - credit) >= 1e-9};
}
const postingSummary = computed(() => {
  const summary = {count: selected.value.length, income: 0, expense: 0, debit: 0, credit: 0, anomalies: 0};
  for (const name of selected.value) {
    const item = visibleItems.value.find(row => row.draft?.name === name);
    if (!item) { summary.anomalies++; continue; }
    const amount = Number(item.transaction.amount);
    if (Number.isFinite(amount) && ['income', 'expense'].includes(item.transaction.direction)) summary[item.transaction.direction] += amount;
    const {debit, credit, anomaly} = postingTotals(item);
    summary.debit += debit; summary.credit += credit;
    if (anomaly) summary.anomalies++;
  }
  return summary;
});
let initializedScope = '';
watch(() => [filter.value, props.initialBatch, props.initialTransaction, props.list, props.canWrite], () => {
  confirmation.value = '';
  if (filter.value !== '待分类') active.value = null;
  savedVersion.value = '';
  const scope = JSON.stringify([filter.value, props.initialBatch || '', props.initialTransaction || '']);
  const autoSelect = props.canWrite && filter.value === '待审核';
  if (!autoSelect || initializedScope !== scope) {
    selected.value = []; initializedScope = '';
  }
  if (!autoSelect || !listReady.value) return;
  const names = visibleItems.value.filter(item => postEligible(item) && !postingTotals(item).anomaly).map(item => item.draft.name);
  selected.value = initializedScope === scope ? selected.value.filter(name => names.includes(name)) : names;
  initializedScope = scope;
}, {deep: true, immediate: true});
watch(() => [pagedItems.value, props.classificationRun?.status], () => {
  if (filter.value !== '待分类') return;
  if (!listReady.value) { active.value = null; return; }
  const actionable = pagedItems.value.filter(item => !item.draft &&
    (['unmatched', 'conflict'].includes(item.classification?.status) || item.can_classify));
  const next = actionable.find(item => item.transaction.name === activeTransaction) || actionable[0];
  if (next) open(next);
  else { activeTransaction = ''; active.value = null; }
}, {immediate: true});
const confirmationVersion = () => JSON.stringify([props.list.generation, props.initialBatch || '', props.initialTransaction || '', filter.value,
  selected.value.map(name => visibleItems.value.find(item => item.draft?.name === name))]);
const confirmationValid = computed(() => confirmation.value === confirmationVersion());
const money = value => Number(value).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const canPreview = computed(() => listReady.value && props.canWrite && !busy.value && !dirty.value && selected.value.length > 0);
const canPost = computed(() => canPreview.value && confirmationValid.value && !postingSummary.value.anomalies);
function confirmPosting() {
  if (!canPreview.value) return;
  confirmation.value = confirmationVersion(); error.value = '';
}
async function postBatch() {
  if (!canPost.value) return;
  busy.value = true; outcomes.value = []; error.value = '';
  try {
    const response = await frappe.call({method: 'internal_ledger.api.posting_api.approve_and_post',
      args: {draft_names: JSON.stringify(selected.value)}, type: 'POST'});
    outcomes.value = response.message;
    selected.value = []; confirmation.value = ''; active.value = null; emit('refresh');
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
</script>

<template>
  <section aria-labelledby="review-title">
    <h2 id="review-title">待办中心</h2>
    <p v-if="initialBatch">当前范围：本次导入批次</p>
    <p>异常优先。人工修改会保留操作人，并重新验证科目、金额与会计期间。</p>
    <p v-if="classificationRun?.status === 'loading'" role="status">正在自动分类并生成草稿…</p>
    <dl v-else-if="classificationRun?.summary" class="ledger-counts" aria-label="自动分类结果" aria-live="polite">
      <div><dt>已自动分类并生成草稿</dt><dd>{{ classificationRun.summary.created }}</dd></div>
      <div><dt>需要人工分类</dt><dd>{{ classificationRun.summary.manual }}</dd></div>
      <div><dt>规则冲突</dt><dd>{{ classificationRun.summary.conflict }}</dd></div>
      <div><dt>失败</dt><dd>{{ classificationRun.summary.failed }}</dd></div>
    </dl>
    <p v-if="classificationRun?.error" role="alert">{{ classificationRun.error }}</p>
    <div role="group" aria-label="审核状态筛选"><button v-for="label in filters" :key="label" :aria-pressed="filter === label" :disabled="busy" @click="filter = label">{{ label }}（{{ scopedItems.filter(item => stage(item) === label).length }}）</button></div>
    <button v-if="canWrite" :disabled="!canPreview" @click="confirmPosting">审核并过账选中项（{{ listReady ? selected.length : 0 }}）</button>
    <p v-if="list.status === 'loading'" role="status">正在载入…</p><p v-else-if="listReady && !visibleItems.length">当前筛选没有流水。</p>
    <p v-if="error && !active" role="alert">{{ error }}</p>
    <div v-if="canWrite && confirmation" role="region" aria-label="审核过账确认">
      <h3>审核过账确认</h3>
      <p>审核范围：{{ initialBatch ? '本次导入批次' : '全部批次' }} · {{ filter }}</p>
      <details v-if="initialBatch"><summary>技术信息</summary><p>批次编号：{{ initialBatch }}</p></details>
      <dl><dt>选中数量</dt><dd>{{ postingSummary.count }}</dd>
        <dt>收入合计</dt><dd>{{ money(postingSummary.income) }}</dd><dt>支出合计</dt><dd>{{ money(postingSummary.expense) }}</dd>
        <dt>借方合计</dt><dd>{{ money(postingSummary.debit) }}</dd><dt>贷方合计</dt><dd>{{ money(postingSummary.credit) }}</dd>
        <dt>异常数量</dt><dd>{{ postingSummary.anomalies }}</dd></dl>
      <p>确认后将逐笔审核并生成正式记账凭证；失败项保留原状态，可修复后重试。</p>
      <p v-if="!confirmationValid" role="alert">选中项或汇总已变化，请重新预览确认。</p>
      <button :disabled="!canPost" @click="postBatch">确认审核并过账</button>
      <button :disabled="busy" @click="confirmation = ''">取消</button>
    </div>
    <table v-if="outcomes.length" aria-live="polite">
      <caption>逐项处理结果</caption><thead><tr><th scope="col">处理项</th><th scope="col">状态</th><th scope="col">处理说明</th><th scope="col">凭证</th></tr></thead>
      <tbody><tr v-for="(outcome, index) in outcomes" :key="index">
        <td>第 {{ index + 1 }} 项<details><summary>技术信息</summary><p>草稿 / 流水编号：{{ outcome.name }}</p><p v-if="outcome.draft">草稿编号：{{ outcome.draft }}</p></details></td><td>{{ outcome.status === 'created' && outcome.journal_entry ? '已过账' : processingStatuses[outcome.status] || '处理结果待核对' }}</td><td>{{ outcome.message }}</td>
        <td><a v-if="outcome.journal_entry" :href="`/app/journal-entry/${encodeURIComponent(outcome.journal_entry)}`">正式凭证 {{ outcome.journal_entry }}</a>
          <a v-else-if="outcome.draft" :href="`/app/voucher-draft/${encodeURIComponent(outcome.draft)}`">查看草稿</a></td>
      </tr></tbody>
    </table>
    <div class="ledger-review">
      <div>
      <ul class="ledger-items">
        <li v-for="item in pagedItems" :key="item.transaction.name">
          <label v-if="canWrite && postEligible(item)"><input v-model="selected" type="checkbox" :value="item.draft.name" :disabled="busy">选择审核过账</label>
          <strong>{{ item.transaction.counterparty || '未提供交易对象' }} · {{ money(item.transaction.amount) }} · {{ item.transaction.occurred_at }}</strong>
          <span>{{ item.error || item.draft?.rejection_reason || draftStatuses[item.draft?.status] || stage(item) }}</span>
          <button :disabled="busy" @click="open(item)">查看明细</button>
        </li>
      </ul>
      <nav v-if="visibleItems.length" class="ledger-pagination" aria-label="待办分页">
        <button :disabled="currentPage === 1" @click="previousPage">上一页</button>
        <span>第 {{ currentPage }} / {{ pageCount }} 页 · 共 {{ visibleItems.length }} 笔</span>
        <button :disabled="currentPage === pageCount" @click="nextPage">下一页</button>
      </nav>
      </div>
      <div v-if="active" class="ledger-detail">
        <h3>{{ active.transaction.counterparty || '未提供交易对象' }} · {{ money(active.transaction.amount) }} · {{ active.transaction.occurred_at }}</h3>
        <dl>
          <dt>时间 / 来源</dt><dd>{{ active.transaction.occurred_at }} / {{ sources[active.transaction.source] || '未知来源' }}</dd>
          <dt>交易对象</dt><dd>{{ active.transaction.counterparty }}</dd><dt>摘要</dt><dd>{{ active.transaction.summary }}</dd>
          <dt>方向 / 金额 / 资金科目</dt><dd>{{ directions[active.transaction.direction] || '未知方向' }} / {{ active.transaction.amount }} / {{ active.transaction.payment_account }}</dd></dl>
        <h4>业务分类</h4>
        <p v-if="active.classification">分类结果：{{ classificationStatuses[active.classification.status] || '待核对' }}</p>
        <p>业务类型：{{ transactionTypes[active.draft?.transaction_type] || '待人工确认' }}</p>
        <p>分类依据：{{ active.draft?.classification_reason || active.classification?.reason || '尚未确认业务分类' }}</p>
        <p v-if="active.error" role="alert">{{ active.error }}</p><p v-if="error" role="alert">{{ error }}</p>
        <template v-if="active.draft">
          <h3>凭证草稿 · {{ draftStatuses[active.draft.status] }}</h3>
          <p v-if="active.draft.is_manual_override">人工覆盖：{{ active.draft.manual_override_by }}</p>
          <form @submit.prevent="save">
            <fieldset :disabled="busy || !editing">
              <label for="draft-summary">凭证摘要</label><textarea id="draft-summary" v-model="active.draft.summary" />
              <label for="draft-date">记账日期</label><input id="draft-date" v-model="active.draft.posting_date" type="date" required>
              <DraftLines :lines="active.draft.lines" :editable="editing" :company="active.draft.company" :draft="active.draft" :transaction="active.transaction" />
              <button v-if="editing" type="submit">保存人工修改</button>
            </fieldset>
          </form>
          <template v-if="canWrite">
            <p v-if="editing">审核以已保存版本为准，请先保存人工修改。</p>
            <button v-if="editing" :disabled="busy || dirty || !active.can_approve" @click="act('approve_draft', {name: active.draft.name})">审核通过</button>
            <form v-if="editing" @submit.prevent="act('reject_draft', {name: active.draft.name, reason})"><label for="draft-reason">驳回原因</label><input id="draft-reason" v-model="reason" required :disabled="busy"><button :disabled="busy || !reason.trim()">驳回</button></form>
            <button v-if="active.draft.status === 'Rejected'" :disabled="busy" @click="act('reopen_draft', {name: active.draft.name})">重开为草稿</button>
          </template>
        </template>
        <a v-else-if="duplicate(active)" :href="`/app/ledger-workbench/duplicates/疑似重复/${encodeURIComponent(active.transaction.import_batch)}/${encodeURIComponent(active.transaction.name)}`">前往重复流水复核</a>
        <ManualClassificationForm v-else-if="canWrite && active.can_classify" :transaction="active.transaction" :disabled="busy" @classified="classified" />
        <button v-else-if="canWrite" :disabled="busy || !!active.error" @click="act('prepare_draft', {tx_name: active.transaction.name})">分类并生成待审核草稿</button>
        <details v-if="!active.draft"><summary>技术信息</summary>
          <dl><dt>流水编号</dt><dd>{{ active.transaction.name }}</dd><dt>批次编号</dt><dd>{{ active.transaction.import_batch }}</dd>
            <dt>草稿编号</dt><dd>{{ active.draft?.name || '尚未生成' }}</dd>
            <dt>规则编号</dt><dd>{{ active.draft?.classification_rule || active.classification?.rule_name || '无' }}</dd></dl>
        </details>
        <TransactionEvidence :transaction="active.transaction.name" :can-write="canWrite" />
      </div>
      <p v-else>选择一笔流水，在此核对原流水与借贷分录。</p>
    </div>
  </section>
</template>
