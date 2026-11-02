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
    <h2 id="dashboard-title">业务总览</h2>
    <p>导入账单 → 重复复核 → 智能分类 → 凭证审核 → 正式过账。</p>
    <a class="ledger-card" href="/app/ledger-workbench/import"><strong>文件智能导入</strong><span>上传银行、微信或支付宝账单，先预览再确认导入。</span>{{ canWrite ? '上传账单' : '查看导入说明（只读）' }}</a>
    <dl v-if="counts" class="ledger-counts"><div><dt>本月收入</dt><dd>{{ counts.income }}</dd></div><div><dt>本月支出</dt><dd>{{ counts.expense }}</dd></div></dl>
    <p>收入、支出和账务统计不包含待确认或已确认的重复流水。</p>
    <h3>智能内账</h3>
    <div class="ledger-cards"><a v-for="card in cards" :key="card[0]" class="ledger-card" :href="`/app/ledger-workbench/${card[0]}`"><strong>{{ card[1] }} · {{ card[2] }}</strong><span>{{ card[3] }}</span>查看{{ card[1] }}</a></div>
    <p><a href="/app/query-report/General%20Ledger">账簿与报表</a></p>
    <h3>经营管理</h3>
    <p>进入 ERPNext 原生业务页面，按现有角色权限操作。</p>
    <div class="ledger-cards"><a v-for="entry in erpLinks" :key="entry[0]" class="ledger-card" :href="`/app/${entry[0]}`">{{ entry[1] }}</a></div>
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
