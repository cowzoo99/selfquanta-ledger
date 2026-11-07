<script setup>
import {computed} from 'vue';
const props = defineProps({items: Array, counts: Object, business: Object, aiStatus: Object, month: String, canWrite: Boolean, loading: Boolean});
const monthly = computed(() => props.items?.filter(item => String(item.transaction.occurred_at).startsWith(props.month)));
const pendingDuplicates = computed(() => monthly.value?.filter(item => item.transaction.status === 'Duplicate' && item.transaction.duplicate_review_status === 'Pending').length ?? '—');
const pendingPosting = computed(() => monthly.value?.filter(item => item.transaction.status === 'Imported' && !['Pending', 'Confirmed'].includes(item.transaction.duplicate_review_status) && item.draft?.status === 'Approved').length ?? '—');
const todayTodo = computed(() => (Number(props.counts?.pending_classification || 0)
  + Number(props.counts?.pending_review || 0) + Number(pendingDuplicates.value === '—' ? 0 : pendingDuplicates.value)
  + Number(pendingPosting.value === '—' ? 0 : pendingPosting.value)));
const businessCards = computed(() => Object.values(props.business?.data?.sections || {}).flatMap(section => section.cards || []));
const unavailableSections = computed(() => Object.entries(props.business?.data?.sections || {})
  .filter(([, section]) => !section.available && !section.restricted));
const cards = computed(() => [
  ['review/疑似重复', '疑似重复', pendingDuplicates.value, '核对是否为同一笔交易'],
  ['review/待分类', '待分类', props.counts?.pending_classification ?? '—', '补充科目，可记住交易对象'],
  ['review/待审核', '待审核', props.counts?.pending_review ?? '—', '核对草稿，确认后正式入账'],
  ['review/待过账', '待过账', pendingPosting.value, '处理已审核的凭证'],
]);
</script>

<template>
  <section aria-labelledby="dashboard-title">
    <div class="ledger-hero">
      <div>
        <p class="ledger-eyebrow">SelfLedger 智能内账</p>
        <h2 id="dashboard-title">导入流水，自动整理待审核凭证</h2>
        <p>上传银行、微信或支付宝账单，处理待办，确认后正式入账。</p>
      </div>
      <a class="ledger-primary-action" href="/app/ledger-workbench/import">
        <strong>{{ canWrite ? '开始导入并自动识别' : '查看文件智能导入' }}</strong>
        <span>支持 PDF、JPG、PNG、CSV · 先预览，再确认导入</span>
      </a>
    </div>
    <dl v-if="counts" class="ledger-counts"><div><dt>本月收入</dt><dd>{{ counts.income }}</dd></div><div><dt>本月支出</dt><dd>{{ counts.expense }}</dd></div></dl>
    <p>收支不包含待确认或已确认的重复流水。</p>
    <h3>智能内账待办</h3>
    <div class="ledger-cards"><a v-for="card in cards" :key="card[0]" class="ledger-card" :href="`/app/ledger-workbench/${card[0]}`"><strong>{{ card[1] }} · {{ card[2] }}</strong><span>{{ card[3] }}</span>查看{{ card[1] }}</a></div>
    <h3>最近处理</h3>
    <p v-if="loading" role="status">正在载入处理结果…</p>
    <div v-else-if="counts?.latest_batch" class="ledger-latest-batch">
      <p>批次 {{ counts.latest_batch.batch_name }} · 共 {{ counts.latest_batch.total }} 笔</p>
      <p>自动生成凭证草稿 {{ counts.latest_batch.created }} 笔 · 疑似重复 {{ counts.latest_batch.duplicate }} 笔 · 待人工分类 {{ counts.latest_batch.manual }} 笔</p>
      <a href="/app/ledger-workbench/review/待审核">前往待办中心</a>
    </div>
    <p v-else-if="counts">尚无导入记录，请先导入账单。</p>
    <p v-else>本月摘要尚未载入。</p>
    <h3>企业经营总览</h3>
    <p v-if="business?.status === 'loading'" role="status">正在读取经营数据…</p>
    <p v-else-if="business?.status === 'error'" role="alert">经营数据读取失败：{{ business.error }}</p>
    <template v-else-if="business?.status === 'success'">
      <p v-if="business.data?.partial" role="status">部分经营数据暂时无法读取，其他区块仍可使用。</p>
      <div v-if="businessCards.length" class="ledger-cards">
        <a v-for="entry in businessCards" :key="`${entry.label}-${entry.target_route}`" class="ledger-card ledger-module-card"
          :data-severity="entry.severity" :href="entry.target_route" :title="entry.tooltip">
          <strong>{{ entry.label }} · {{ entry.value }} {{ entry.unit }}</strong>
          <span>{{ entry.tooltip }}</span>
        </a>
      </div>
      <p v-else>当前期间暂无可展示的经营指标。</p>
      <details v-if="unavailableSections.length"><summary>尚未启用的模块</summary>
        <ul><li v-for="entry in unavailableSections" :key="entry[0]">{{ entry[0] }}：{{ entry[1].message }}</li></ul>
      </details>
    </template>
    <p v-else>经营数据尚未载入。</p>
    <h3>AI 辅助</h3>
    <a v-if="aiStatus?.status === 'success' && aiStatus.data?.available" class="ledger-card ledger-module-card" href="/app/ledger-workbench/assistant">
      <strong>打开 AI 助手</strong><span>报表解读、分类建议、经营摘要和使用帮助</span>
    </a>
    <p v-else-if="aiStatus?.status === 'error'" role="status">AI 助手暂时不可用，现有 ERP 功能不受影响。</p>
    <p v-else-if="aiStatus?.status === 'success'">AI 助手未启用，现有 ERP 功能不受影响。</p>
  </section>
</template>
