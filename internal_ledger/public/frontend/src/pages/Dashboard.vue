<script setup>
defineProps({items: Array, loading: Boolean});
defineEmits(['navigate']);
</script>

<template>
  <section aria-labelledby="dashboard-title">
    <h2 id="dashboard-title">工作台</h2>
    <p>先处理分类冲突、校验失败和驳回项，再审核正常草稿。</p>
    <button @click="$emit('navigate', 'import')">导入账单</button>
    <button @click="$emit('navigate', 'review')">进入审核中心</button>
    <p v-if="loading" role="status">正在载入流水…</p>
    <p v-else-if="!items.length">尚无流水，请先导入银行卡、微信或支付宝账单。</p>
    <ul v-else class="ledger-items">
      <li v-for="item in items" :key="item.transaction.name">
        <strong>{{ item.error || item.draft?.rejection_reason || item.draft?.status || '待分类' }}</strong>
        <span>{{ item.transaction.occurred_at }} · {{ item.transaction.counterparty }} · {{ item.transaction.amount }}</span>
        <button @click="$emit('navigate', 'review')">查看流水与分录</button>
      </li>
    </ul>
  </section>
</template>
