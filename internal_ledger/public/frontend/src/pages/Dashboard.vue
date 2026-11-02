<script setup>
import {computed} from 'vue';
const props = defineProps({items: Array, counts: Object, month: String, canWrite: Boolean, loading: Boolean});
const monthly = computed(() => props.items.filter(item => String(item.transaction.occurred_at).startsWith(props.month)));
const pendingDuplicates = computed(() => monthly.value.filter(item => item.transaction.status === 'Duplicate' && item.transaction.duplicate_review_status === 'Pending').length);
const pendingPosting = computed(() => monthly.value.filter(item => item.transaction.status === 'Imported' && !['Pending', 'Confirmed'].includes(item.transaction.duplicate_review_status) && item.draft?.status === 'Approved').length);
const erpLinks = [['selling', '销售'], ['buying', '采购'], ['stock', '库存'], ['manufacturing', '制造']];
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
    <p v-else>尚无导入记录，请先导入账单。</p>
    <h3>企业经营管理</h3>
    <div class="ledger-cards"><a v-for="entry in erpLinks" :key="entry[0]" class="ledger-card ledger-module-card" :href="`/app/${entry[0]}`"><strong>{{ entry[1] }}</strong><span>进入{{ entry[1] }}模块</span></a></div>
  </section>
</template>
