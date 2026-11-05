<script setup>
import {computed, ref} from 'vue';
import TransactionEvidence from '../components/TransactionEvidence.vue';
import {errorMessage} from '../api';
const props = defineProps({items: {type: Array, default: () => []}, canWrite: Boolean});
const emit = defineEmits(['refresh']);
const selected = ref('');
const reversalFor = ref(''), reversalReason = ref(''), busy = ref(false), error = ref(''), notice = ref('');
const ledgerView = ref('entries');
const title = '会计中心';
const currentYear = new Date().getFullYear();
const generalLedgerHref = `/app/query-report/General%20Ledger?from_date=${currentYear}-01-01&to_date=${currentYear}-12-31`;
const amount = value => `CNY ${Number(value || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
const account = line => [line.account_number, line.account_name].filter(Boolean).join(' - ') || line.account;
const entries = computed(() => props.items.filter(item => item.draft?.status === 'Posted').map(item => {
  const lines = item.draft.lines || [];
  const debit = lines.filter(line => Number(line.debit) > 0);
  const credit = lines.filter(line => Number(line.credit) > 0);
  const incoming = item.transaction.direction === 'income';
  return {name: item.draft.name, transaction: item.transaction.name,
    date: item.draft.posting_date || item.transaction.occurred_at?.slice(0, 10),
    summary: item.display_title || item.draft.summary || item.transaction.counterparty || '未提供摘要', debit, credit,
    posting: item.posting,
    movement: {incoming, line: (incoming ? debit : credit)[0], amount: item.transaction.amount}};
}));
async function reversePosting(entry) {
  if (!reversalReason.value.trim()) {error.value = '请填写冲销原因'; return;}
  busy.value = true; error.value = ''; notice.value = '';
  try {
    await frappe.call({method: 'internal_ledger.api.posting_api.reverse_posting', type: 'POST',
      args: {posting_link: entry.posting.name, reason: reversalReason.value.trim()}});
    reversalFor.value = ''; reversalReason.value = ''; notice.value = '已作废并生成冲销凭证';
    emit('refresh');
  } catch (e) {error.value = errorMessage(e);}
  finally {busy.value = false;}
}
</script>

<template>
  <section aria-labelledby="accounting-center-title">
    <h2 id="accounting-center-title">{{ title }}</h2>
    <p>从凭证草稿核对借贷分录，管理员确认后生成正式记账凭证，并可继续查看总账。</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <a class="ledger-primary-action" href="#ledger-overview">
      <strong>标准总账总览</strong>
      <span>按日期和凭证分开查看借方、贷方，并可切换收支变动明细</span>
    </a>
    <div class="ledger-cards">
      <a class="ledger-card" href="/app/voucher-draft"><strong>凭证草稿</strong><span>查看自动分类和人工补充后形成的借贷草稿</span></a>
      <a class="ledger-card" href="/app/journal-entry"><strong>正式记账凭证</strong><span>查看管理员确认过账后的正式凭证</span></a>
      <a class="ledger-card" :href="generalLedgerHref"><strong>总账报表</strong><span>按科目和期间查询正式账簿</span></a>
    </div>
    <div id="ledger-overview" class="ledger-overview">
      <div class="ledger-view-tabs" role="tablist" aria-label="总账展示方式">
        <button type="button" :aria-current="ledgerView === 'entries' ? 'page' : undefined" @click="ledgerView = 'entries'">借贷分录</button>
        <button type="button" :aria-current="ledgerView === 'movements' ? 'page' : undefined" @click="ledgerView = 'movements'">收支变动明细</button>
      </div>
      <p v-if="!entries.length">暂无已过账凭证。</p>
      <div v-else-if="ledgerView === 'entries'" class="ledger-voucher-list">
        <article v-for="entry in entries" :key="entry.name" class="ledger-overview-voucher">
          <div class="ledger-voucher-heading">
            <div><strong>{{ entry.summary }}</strong><span>{{ entry.date }} · {{ entry.name }}</span></div>
            <div v-if="entry.posting && entry.posting.status === 'Reversed'">
              <strong>已作废（已生成冲销凭证）</strong>
              <span>{{ entry.posting.reversal_journal_entry }}</span>
            </div>
            <button v-else-if="canWrite && entry.posting && entry.posting.status === 'Posted'" type="button"
              @click="reversalFor = entry.name; reversalReason = ''">作废并冲销</button>
          </div>
          <div v-if="reversalFor === entry.name" class="ledger-reversal">
            <label>冲销原因<input v-model="reversalReason" type="text" maxlength="500"></label>
            <button type="button" :disabled="busy" @click="reversePosting(entry)">确认作废并生成冲销凭证</button>
            <button type="button" :disabled="busy" @click="reversalFor = ''; reversalReason = ''">取消</button>
          </div>
          <div class="ledger-entry-columns">
            <div class="ledger-debit-column">
              <h4>借方</h4>
              <div v-for="line in entry.debit" :key="line.name || line.account" class="ledger-entry-line">
                <span>{{ account(line) }}</span><strong>{{ amount(line.debit) }}</strong>
              </div>
            </div>
            <div class="ledger-credit-column">
              <h4>贷方</h4>
              <div v-for="line in entry.credit" :key="line.name || line.account" class="ledger-entry-line">
                <span>{{ account(line) }}</span><strong>{{ amount(line.credit) }}</strong>
              </div>
            </div>
          </div>
        </article>
      </div>
      <div v-else class="ledger-movement-list">
        <article v-for="entry in entries" :key="entry.name" class="ledger-movement-row">
          <div><strong>{{ entry.summary }}</strong><span>{{ entry.date }} · {{ account(entry.movement.line || {}) }}</span></div>
          <strong :class="entry.movement.incoming ? 'movement-in' : 'movement-out'">
            {{ entry.movement.incoming ? '转入 · 增加' : '转出 · 减少' }}
            {{ entry.movement.incoming ? '+' : '-' }}{{ amount(entry.movement.amount) }}
          </strong>
        </article>
      </div>
    </div>
    <h3>逐笔备注与附件</h3>
    <ul class="ledger-items">
      <li v-for="item in items" :key="item.transaction.name">
        <strong>{{ item.display_title || item.transaction.counterparty || '未提供交易对象' }} · {{ item.transaction.amount }} · {{ item.transaction.occurred_at }}</strong>
        <button type="button" @click="selected = item.transaction.name">查看备注与附件</button>
      </li>
    </ul>
    <TransactionEvidence v-if="selected" :transaction="selected" :can-write="canWrite" @saved="emit('refresh')" />
  </section>
</template>

<style scoped>
.ledger-overview { margin-top: 20px; scroll-margin-top: 20px; }
.ledger-view-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.ledger-voucher-list, .ledger-movement-list { display: grid; gap: 14px; }
.ledger-overview-voucher, .ledger-movement-row { border: 1px solid var(--border-color, #d9dee3); border-radius: 10px; overflow: hidden; background: var(--card-bg, #fff); }
.ledger-voucher-heading { display: flex; justify-content: space-between; gap: 12px; padding: 12px 16px; background: #f5f7f9; }
.ledger-voucher-heading > div { display: grid; gap: 4px; }
.ledger-reversal { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; padding: 10px 16px; background: #fff4ef; }
.ledger-reversal label { display: grid; gap: 4px; flex: 1 1 280px; }
.ledger-voucher-heading span, .ledger-movement-row span { color: #606b75; }
.ledger-entry-columns { display: grid; grid-template-columns: 1fr 1fr; }
.ledger-debit-column, .ledger-credit-column { padding: 14px 16px; min-width: 0; }
.ledger-debit-column { border-right: 1px solid var(--border-color, #d9dee3); background: #f5fbff; }
.ledger-credit-column { background: #fff8f5; }
.ledger-debit-column h4, .ledger-credit-column h4 { margin: 0 0 10px; }
.ledger-entry-line { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; padding: 7px 0; }
.ledger-entry-line span { overflow-wrap: anywhere; }
.ledger-movement-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 16px; padding: 14px 16px; }
.ledger-movement-row div { display: grid; gap: 4px; }
.movement-in { color: #19733b; }
.movement-out { color: #b33a2b; }
@media (max-width: 720px) {
  .ledger-entry-columns { grid-template-columns: 1fr; }
  .ledger-debit-column { border-right: 0; border-bottom: 1px solid var(--border-color, #d9dee3); }
  .ledger-voucher-heading, .ledger-movement-row { grid-template-columns: 1fr; flex-direction: column; }
}
</style>
