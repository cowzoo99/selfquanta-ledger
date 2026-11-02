<script setup>
import {computed} from 'vue';
const props = defineProps({items: Array, counts: Object, month: String, canWrite: Boolean, loading: Boolean});
const monthly = computed(() => props.items.filter(item => String(item.transaction.occurred_at).startsWith(props.month)));
const pendingDuplicates = computed(() => monthly.value.filter(item => item.transaction.status === 'Duplicate' && item.transaction.duplicate_review_status === 'Pending').length);
const pendingPosting = computed(() => monthly.value.filter(item => item.transaction.status === 'Imported' && !['Pending', 'Confirmed'].includes(item.transaction.duplicate_review_status) && item.draft?.status === 'Approved').length);
const exceptions = computed(() => props.items.filter(item => item.error || item.draft?.status === 'Rejected'));
const erpLinks = [['selling', '销售'], ['buying', '采购'], ['stock', '库存'], ['manufacturing', '制造']];
function exceptionRoute(item) {
  if (item.transaction.status === 'Duplicate' || ['Pending', 'Confirmed'].includes(item.transaction.duplicate_review_status)) return '/app/ledger-workbench/duplicates';
  const stages = {Draft: '待审核', Rejected: '待审核', Approved: '待过账', Posted: '已过账'};
  return `/app/ledger-workbench/review/${item.draft ? stages[item.draft.status] : '待分类'}`;
}
const cards = computed(() => [
  ['duplicates', '疑似重复', pendingDuplicates.value, '核对重复候选与原流水'],
  ['classification', '待分类', props.counts?.pending_classification ?? '—', '补充科目并学习交易对象规则'],
  ['review/待审核', '待审核', props.counts?.pending_review ?? '—', '核对标准借贷凭证并审核'],
  ['review/待过账', '待过账', pendingPosting.value, '确认后生成 ERPNext 正式凭证'],
  ['review/已过账', '已过账', props.counts?.posted ?? '—', '查看已完成的凭证'],
]);
</script>

<template>
  <section aria-labelledby="dashboard-title">
    <div class="ledger-hero">
      <div>
        <p class="ledger-eyebrow">SelfLedger 智能内账</p>
        <h2 id="dashboard-title">从一张流水开始，完成专业借贷记账</h2>
        <p>银行回单、银行流水、微信或支付宝截图，都在本网站完成识别、复核、分类、制证和过账。</p>
      </div>
      <a class="ledger-primary-action" href="/app/ledger-workbench/import">
        <strong>{{ canWrite ? '开始导入并自动识别' : '查看文件智能导入' }}</strong>
        <span>支持 PDF、JPG、PNG、CSV · 先预览，确认后才入账</span>
      </a>
    </div>
    <ol class="ledger-flow" aria-label="智能内账处理流程">
      <li><strong>1 导入识别</strong><span>提取时间、交易对象、摘要和金额</span></li>
      <li><strong>2 重复复核</strong><span>疑似重复不参与统计，等待人工确认</span></li>
      <li><strong>3 分类学习</strong><span>人工选科目后，可保存为后续分类规则</span></li>
      <li><strong>4 凭证审核</strong><span>检查专业借方、贷方和金额平衡</span></li>
      <li><strong>5 正式过账</strong><span>批量确认后生成 ERPNext 会计凭证</span></li>
    </ol>
    <dl v-if="counts" class="ledger-counts"><div><dt>本月收入</dt><dd>{{ counts.income }}</dd></div><div><dt>本月支出</dt><dd>{{ counts.expense }}</dd></div></dl>
    <p>收入、支出和账务统计不包含待确认或已确认的重复流水。</p>
    <h3>内账处理中心</h3>
    <div class="ledger-cards"><a v-for="card in cards" :key="card[0]" class="ledger-card" :href="`/app/ledger-workbench/${card[0]}`"><strong>{{ card[1] }} · {{ card[2] }}</strong><span>{{ card[3] }}</span>查看{{ card[1] }}</a></div>
    <p><a href="/app/query-report/General%20Ledger">账簿与报表</a></p>
    <h3>企业经营管理</h3>
    <p>销售、采购、库存和制造保留为同一网站内的功能分支，继续使用同一账号和权限。</p>
    <div class="ledger-cards"><a v-for="entry in erpLinks" :key="entry[0]" class="ledger-card ledger-module-card" :href="`/app/${entry[0]}`"><strong>{{ entry[1] }}</strong><span>进入{{ entry[1] }}模块</span></a></div>
    <h3>异常优先</h3>
    <p v-if="loading" role="status">正在载入流水…</p>
    <p v-else-if="!items.length">尚无流水，请先导入银行卡、微信或支付宝账单。</p>
    <p v-else-if="!exceptions.length">当前没有待处理异常。</p>
    <ul v-else class="ledger-items">
      <li v-for="item in exceptions" :key="item.transaction.name">
        <strong>{{ item.error || item.draft?.rejection_reason || '已驳回' }}</strong>
        <span>{{ item.transaction.occurred_at }} · {{ item.transaction.counterparty }} · {{ item.transaction.amount }}</span>
        <a :href="exceptionRoute(item)">查看并处理</a>
      </li>
    </ul>
  </section>
</template>
