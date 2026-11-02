<script setup>
import {ref, onMounted} from 'vue';
import {errorMessage} from '../api';
import {directions, sources, processingStatuses} from '../labels';
const props = defineProps({canWrite: Boolean});
const emit = defineEmits(['refresh']);
const items = ref([]), notes = ref({}), loading = ref(false), busy = ref(false), error = ref(''), result = ref(null);
const statuses = {Pending: '待复核', Confirmed: '已确认重复，已忽略', Released: '已保留，进入正常入账流程'};
async function refresh() {
  loading.value = true;
  try {
    const response = await frappe.call({method: 'internal_ledger.api.duplicate_api.list_duplicates', type: 'GET'});
    items.value = response.message;
  } finally { loading.value = false; }
}
async function resolve(item, decision) {
  if (!props.canWrite || busy.value || item.candidate.duplicate_review_status !== 'Pending') return;
  const note = notes.value[item.candidate.name] || '';
  if (decision === 'release' && !note.trim()) { error.value = '不是重复时，请填写保留并入账的原因。'; return; }
  busy.value = true; error.value = ''; result.value = null;
  try {
    const response = await frappe.call({method: 'internal_ledger.api.duplicate_api.resolve_duplicate', type: 'POST',
      args: {name: item.candidate.name, decision, note: note.trim()}});
    result.value = response.message.processing;
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
    <a href="/app/ledger-workbench/review/疑似重复">返回待办中心</a>
    <p v-if="loading" role="status">正在载入重复证据…</p>
    <p v-else-if="!items.length">当前没有重复复核记录。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="result" role="status" aria-label="重复复核处理结果">
      <p v-if="result.status === 'created'">已自动分类并生成凭证草稿。</p>
      <p v-else-if="result.status === 'duplicate'">已确认重复并忽略，该流水不参与统计与过账。</p>
      <p v-else>{{ processingStatuses[result.status] || '处理结果待核对' }}：{{ result.message }}</p>
      <a v-if="result.status === 'created'" :href="`/app/voucher-draft/${encodeURIComponent(result.draft)}`">查看草稿</a>
      <a v-else-if="['manual', 'conflict'].includes(result.status)" href="/app/ledger-workbench/review/待分类">前往待人工分类</a>
      <template v-else-if="result.status === 'failed'">
        <p>流水已保留，自动处理失败；可在待办中心重新生成草稿或人工分类后重试。</p>
        <a href="/app/ledger-workbench/review/待分类">前往待办中心重试</a>
      </template>
    </div>
    <article v-for="item in items" :key="item.candidate.name" class="ledger-card">
      <h3>{{ item.candidate.name }} · {{ statuses[item.candidate.duplicate_review_status] }}</h3>
      <p>匹配依据：{{ item.candidate.duplicate_match_reason || '来源、时间、金额、方向、交易对象、摘要与资金账户的稳定指纹相同' }}</p>
      <div class="duplicate-evidence">
        <div v-for="side in ['candidate', 'original']" :key="side" class="ledger-detail">
          <h4>{{ side === 'candidate' ? '重复候选' : '原流水' }}</h4>
          <dl><dt>流水编号</dt><dd>{{ item[side].name }}</dd><dt>导入批次</dt><dd>{{ item[side].import_batch }}</dd>
            <dt>来源 / 时间</dt><dd>{{ sources[item[side].source] || '未知来源' }} / {{ item[side].occurred_at }}</dd>
            <template v-if="canWrite && item[side].source_id"><dt>来源单号</dt><dd>{{ item[side].source_id }}</dd></template>
            <dt>交易对象</dt><dd>{{ item[side].counterparty }}</dd><dt>摘要</dt><dd>{{ item[side].summary }}</dd>
            <dt>方向 / 金额</dt><dd>{{ directions[item[side].direction] || '未知方向' }} / {{ item[side].amount }}</dd>
            <dt>资金科目</dt><dd>{{ item[side].payment_account }}</dd></dl>
        </div>
      </div>
      <p>关联原流水：<a :href="`/app/imported-transaction/${encodeURIComponent(item.original.name)}`">{{ item.original.name }}</a></p>
      <dl v-if="item.candidate.duplicate_reviewed_at" class="ledger-detail"><dt>复核人 / 时间</dt><dd>{{ item.candidate.duplicate_reviewed_by }} / {{ item.candidate.duplicate_reviewed_at }}</dd><dt>复核备注</dt><dd>{{ item.candidate.duplicate_review_note }}</dd></dl>
      <template v-if="canWrite && item.candidate.duplicate_review_status === 'Pending'">
        <label :for="`note-${item.candidate.name}`">复核备注（保留并入账时必填）</label>
        <textarea :id="`note-${item.candidate.name}`" v-model="notes[item.candidate.name]" :disabled="busy" />
        <div><button :disabled="busy" @click="resolve(item, 'confirm')">确认重复，忽略</button>
          <button :disabled="busy || !(notes[item.candidate.name] || '').trim()" @click="resolve(item, 'release')">不是重复，保留并入账</button></div>
      </template>
    </article>
  </section>
</template>
