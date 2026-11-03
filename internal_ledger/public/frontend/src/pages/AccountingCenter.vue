<script setup>
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
  </section>
</template>
