<script setup>
import {ref, computed, onMounted} from 'vue';
import TransactionEvidence from '../components/TransactionEvidence.vue';
import {errorMessage} from '../api';
import {directions, sources, processingStatuses} from '../labels';
const props = defineProps({canWrite: Boolean, batch: String, transaction: String});
const emit = defineEmits(['refresh']);
const items = ref([]), notes = ref({}), loading = ref(true), busy = ref(false), error = ref(''), result = ref(null);
const selected = ref([]), batchNote = ref(''), batchOutcomes = ref([]);
const currentPage = ref(1), pageSize = 1;
const inScope = item => (!props.batch || item.candidate.import_batch === props.batch) &&
  (!props.transaction || item.candidate.name === props.transaction);
const scopeBatch = computed(() => props.batch || (props.transaction
  ? items.value.find(item => item.candidate.name === props.transaction)?.candidate.import_batch : ''));
const taskRoute = status => `/app/ledger-workbench/review/${status}` + (scopeBatch.value
  ? `/${encodeURIComponent(scopeBatch.value)}` + (props.transaction ? `/${encodeURIComponent(props.transaction)}` : '') : '');
const statuses = {Pending: '待复核', Confirmed: '已确认重复，已忽略', Released: '已保留，进入正常入账流程'};
const pageCount = computed(() => Math.max(1, Math.ceil(items.value.length / pageSize)));
const pagedItems = computed(() => items.value.slice((currentPage.value - 1) * pageSize, currentPage.value * pageSize));
const pendingItems = computed(() => items.value.filter(item => inScope(item) && item.candidate.duplicate_review_status === 'Pending'));
const allSelected = computed(() => pendingItems.value.length > 0 && pendingItems.value.every(item => selected.value.includes(item.candidate.name)));
function toggleSelectAll() { selected.value = allSelected.value ? [] : pendingItems.value.map(item => item.candidate.name); }
function previousPage() { currentPage.value = Math.max(1, currentPage.value - 1); result.value = null; }
function nextPage() { currentPage.value = Math.min(pageCount.value, currentPage.value + 1); result.value = null; }
async function refresh() {
  loading.value = true;
  try {
    const response = await frappe.call({method: 'internal_ledger.api.duplicate_api.list_duplicates', type: 'GET',
      args: {batch: props.batch || '', transaction: props.transaction || ''}});
    items.value = response.message.filter(inScope);
    const pending = pendingItems.value.map(item => item.candidate.name);
    selected.value = selected.value.filter(name => pending.includes(name));
    currentPage.value = Math.min(currentPage.value, Math.max(1, Math.ceil(items.value.length / pageSize)));
  } finally { loading.value = false; }
}
async function resolve(item, decision) {
  if (!props.canWrite || loading.value || busy.value || !inScope(item) || item.candidate.duplicate_review_status !== 'Pending') return;
  const note = notes.value[item.candidate.name] || '';
  if (decision === 'release' && !note.trim()) { error.value = '不是重复时，请填写保留并入账的原因。'; return; }
  busy.value = true; error.value = ''; result.value = null;
  try {
    const response = await frappe.call({method: 'internal_ledger.api.duplicate_api.resolve_duplicate', type: 'POST',
      args: {name: item.candidate.name, decision, note: note.trim(),
        batch: props.batch || '', transaction: props.transaction || ''}});
    result.value = response.message.processing;
    await refresh(); emit('refresh');
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
async function resolveSelected(decision) {
  if (!props.canWrite || loading.value || busy.value || !selected.value.length) return;
  if (decision === 'release' && !batchNote.value.trim()) { error.value = '一键保留入账必须填写统一复核备注。'; return; }
  busy.value = true; error.value = ''; result.value = null; batchOutcomes.value = [];
  try {
    const response = await frappe.call({method: 'internal_ledger.api.duplicate_api.resolve_duplicates', type: 'POST',
      args: {names: JSON.stringify(selected.value), decision, note: batchNote.value.trim(),
        batch: props.batch || '', transaction: props.transaction || ''}});
    batchOutcomes.value = response.message; selected.value = []; batchNote.value = '';
    await refresh(); emit('refresh');
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
onMounted(async () => { try { await refresh(); } catch (e) { error.value = errorMessage(e); } });
</script>

<template>
  <section aria-labelledby="duplicates-title">
    <h2 id="duplicates-title">重复流水复核</h2>
    <p>重复候选在确认前不会参与分类、统计或过账。确认重复后继续忽略；保留后自动分类并生成凭证草稿。</p>
    <a v-if="!loading" :href="taskRoute('疑似重复')">返回待办中心</a>
    <p v-if="loading" role="status">正在载入重复证据…</p>
    <p v-else-if="!items.length">当前没有重复复核记录。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="canWrite && pendingItems.length" class="ledger-batch-toolbar" role="group" aria-label="重复流水批量复核">
      <button :disabled="loading || busy" @click="toggleSelectAll">{{ allSelected ? '取消全选' : '全选全部' }}（{{ pendingItems.length }}）</button>
      <strong>已选择 {{ selected.length }} 笔</strong>
      <button :disabled="loading || busy || !selected.length" @click="resolveSelected('confirm')">一键确认重复并忽略</button>
      <label for="duplicate-batch-note">统一复核备注</label><input id="duplicate-batch-note" v-model="batchNote" :disabled="loading || busy" placeholder="保留入账时必填">
      <button :disabled="loading || busy || !selected.length || !batchNote.trim()" @click="resolveSelected('release')">一键保留并入账</button>
    </div>
    <table v-if="batchOutcomes.length" aria-live="polite"><caption>批量复核结果</caption>
      <thead><tr><th scope="col">流水</th><th scope="col">结果</th><th scope="col">说明</th></tr></thead>
      <tbody><tr v-for="outcome in batchOutcomes" :key="outcome.name"><td>{{ outcome.name }}</td>
        <td>{{ outcome.status === 'duplicate' ? '已确认重复' : processingStatuses[outcome.status] || '处理完成' }}</td><td>{{ outcome.message }}</td></tr></tbody>
    </table>
    <div v-if="result" role="status" aria-label="重复复核处理结果">
      <p v-if="result.status === 'created'">已自动分类并生成凭证草稿。</p>
      <p v-else-if="result.status === 'duplicate'">已确认重复并忽略，该流水不参与统计与过账。</p>
      <p v-else>{{ processingStatuses[result.status] || '处理结果待核对' }}：{{ result.message }}</p>
      <a v-if="result.status === 'created'" :href="`/app/voucher-draft/${encodeURIComponent(result.draft)}`">查看草稿</a>
      <a v-else-if="['manual', 'conflict'].includes(result.status)" :href="taskRoute('待分类')">前往待人工分类</a>
      <template v-else-if="result.status === 'failed'">
        <p>流水已保留，自动处理失败；可在待办中心重新生成草稿或人工分类后重试。</p>
        <a :href="taskRoute('待分类')">前往待办中心重试</a>
      </template>
    </div>
    <article v-for="item in pagedItems" :key="item.candidate.name" class="ledger-card">
      <label v-if="canWrite && item.candidate.duplicate_review_status === 'Pending'"><input v-model="selected" type="checkbox" :value="item.candidate.name" :disabled="loading || busy">选择批量复核</label>
      <h3>{{ item.candidate.counterparty || '未提供交易对象' }} · {{ item.candidate.amount }} · {{ item.candidate.occurred_at }} · {{ statuses[item.candidate.duplicate_review_status] }}</h3>
      <p>匹配依据：{{ item.candidate.duplicate_match_reason || '来源、时间、金额、方向、交易对象、摘要与资金账户的稳定指纹相同' }}</p>
      <div class="duplicate-evidence">
        <div v-for="side in ['candidate', 'original']" :key="side" class="ledger-detail">
          <h4>{{ side === 'candidate' ? '重复候选' : '原流水' }}</h4>
          <dl><dt>来源 / 时间</dt><dd>{{ sources[item[side].source] || '未知来源' }} / {{ item[side].occurred_at }}</dd>
            <dt>交易对象</dt><dd>{{ item[side].counterparty }}</dd><dt>摘要</dt><dd>{{ item[side].summary }}</dd>
            <dt>方向 / 金额</dt><dd>{{ directions[item[side].direction] || '未知方向' }} / {{ item[side].amount }}</dd>
            <dt>资金科目</dt><dd>{{ item[side].payment_account }}</dd></dl>
          <details><summary>技术信息</summary><p>流水编号：{{ item[side].name }}</p><p>导入批次：{{ item[side].import_batch }}</p><p v-if="canWrite && item[side].source_id">来源单号：{{ item[side].source_id }}</p></details>
          <TransactionEvidence :transaction="item[side].name" :can-write="canWrite" />
        </div>
      </div>
      <p>关联原流水：<a :href="`/app/imported-transaction/${encodeURIComponent(item.original.name)}`">查看原流水</a></p>
      <dl v-if="item.candidate.duplicate_reviewed_at" class="ledger-detail"><dt>复核人 / 时间</dt><dd>{{ item.candidate.duplicate_reviewed_by }} / {{ item.candidate.duplicate_reviewed_at }}</dd><dt>复核备注</dt><dd>{{ item.candidate.duplicate_review_note }}</dd></dl>
      <template v-if="canWrite && item.candidate.duplicate_review_status === 'Pending'">
        <label :for="`note-${item.candidate.name}`">复核备注（保留并入账时必填）</label>
        <textarea :id="`note-${item.candidate.name}`" v-model="notes[item.candidate.name]" :disabled="loading || busy" />
        <div><button :disabled="loading || busy" @click="resolve(item, 'confirm')">确认重复，忽略</button>
          <button :disabled="loading || busy || !(notes[item.candidate.name] || '').trim()" @click="resolve(item, 'release')">不是重复，保留并入账</button></div>
      </template>
    </article>
    <nav v-if="items.length" class="ledger-pagination" aria-label="重复流水逐笔分页">
      <button :disabled="currentPage === 1 || loading || busy" @click="previousPage">上一笔</button>
      <span>第 {{ currentPage }} / {{ pageCount }} 笔</span>
      <button :disabled="currentPage === pageCount || loading || busy" @click="nextPage">下一笔</button>
    </nav>
  </section>
</template>
