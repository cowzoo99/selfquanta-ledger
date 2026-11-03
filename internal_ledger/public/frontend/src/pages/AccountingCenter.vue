<script setup>
import {ref} from 'vue';
import TransactionEvidence from '../components/TransactionEvidence.vue';
defineProps({items: {type: Array, default: () => []}, canWrite: Boolean});
const selected = ref('');
const title = '会计中心';
</script>

<template>
  <section aria-labelledby="accounting-center-title">
    <h2 id="accounting-center-title">{{ title }}</h2>
    <p>从凭证草稿核对借贷分录，管理员确认后生成正式记账凭证，并可继续查看总账。</p>
    <div class="ledger-cards">
      <a class="ledger-card" href="/app/voucher-draft"><strong>凭证草稿</strong><span>查看自动分类和人工补充后形成的借贷草稿</span></a>
      <a class="ledger-card" href="/app/journal-entry"><strong>正式记账凭证</strong><span>查看管理员确认过账后的正式凭证</span></a>
      <a class="ledger-card" href="/app/query-report/General%20Ledger"><strong>总账报表</strong><span>按科目和期间查询正式账簿</span></a>
    </div>
    <h3>逐笔备注与附件</h3>
    <ul class="ledger-items">
      <li v-for="item in items" :key="item.transaction.name">
        <strong>{{ item.transaction.counterparty || '未提供交易对象' }} · {{ item.transaction.amount }} · {{ item.transaction.occurred_at }}</strong>
        <button type="button" @click="selected = item.transaction.name">查看备注与附件</button>
      </li>
    </ul>
    <TransactionEvidence v-if="selected" :transaction="selected" :can-write="canWrite" />
  </section>
</template>
