<script setup>
import {ref, computed} from 'vue';
import DraftLines from '../components/DraftLines.vue';
import {call, errorMessage} from '../api';
const props = defineProps({items: Array, canWrite: Boolean, loading: Boolean});
const emit = defineEmits(['refresh']);
const selected = ref([]), active = ref(null), busy = ref(false), error = ref(''), outcomes = ref([]), reason = ref('');
const savedVersion = ref('');
const editing = computed(() => props.canWrite && active.value?.draft?.status === 'Draft');
const dirty = computed(() => active.value?.draft && JSON.stringify(active.value.draft) !== savedVersion.value);
function open(item) {
  active.value = JSON.parse(JSON.stringify(item)); savedVersion.value = JSON.stringify(active.value.draft);
  reason.value = ''; error.value = '';
}
async function act(method, args) {
  busy.value = true; error.value = '';
  try {
    const doc = await call(method, args);
    active.value = await call('get_review_item', {name: doc.name});
    savedVersion.value = JSON.stringify(active.value.draft);
    emit('refresh');
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
function save() {
  const doc = active.value.draft;
  return act('update_draft', {name: doc.name, lines: JSON.stringify(doc.lines), summary: doc.summary || '', posting_date: doc.posting_date});
}
async function bulk() {
  busy.value = true; outcomes.value = []; error.value = '';
  for (const name of selected.value) {
    try {
      const item = await call('get_review_item', {name});
      if (!item.can_approve) throw new Error(item.error || '当前状态不可审核');
      await call('approve_draft', {name});
      outcomes.value.push({name, message: '审核通过'});
    } catch (e) { outcomes.value.push({name, message: errorMessage(e)}); }
  }
  selected.value = []; active.value = null; busy.value = false; emit('refresh');
}
</script>

<template>
  <section aria-labelledby="review-title">
    <h2 id="review-title">审核中心</h2>
    <p>异常优先。人工修改会保留操作人，并重新验证科目、金额与会计期间。</p>
    <button v-if="canWrite" :disabled="busy || dirty || !selected.length" @click="bulk">审核选中项（{{ selected.length }}）</button>
    <p v-if="loading" role="status">正在载入…</p><p v-else-if="!items.length">没有待处理流水。</p>
    <ul v-if="outcomes.length" aria-live="polite"><li v-for="outcome in outcomes" :key="outcome.name">{{ outcome.name }}：{{ outcome.message }}</li></ul>
    <div class="ledger-review">
      <ul class="ledger-items">
        <li v-for="item in items" :key="item.transaction.name">
          <label v-if="canWrite && item.draft"><input v-model="selected" type="checkbox" :value="item.draft.name" :disabled="busy || !item.can_approve">选择 {{ item.draft.name }}</label>
          <strong>{{ item.error || item.draft?.rejection_reason || item.draft?.status || '待分类' }}</strong>
          <span>{{ item.transaction.counterparty }} · {{ item.transaction.amount }}</span>
          <button :disabled="busy" @click="open(item)">查看 {{ item.transaction.name }}</button>
        </li>
      </ul>
      <div v-if="active" class="ledger-detail">
        <h3>原流水</h3>
        <dl><dt>流水编号 / 批次</dt><dd>{{ active.transaction.name }} / {{ active.transaction.import_batch }}</dd>
          <dt>时间 / 来源</dt><dd>{{ active.transaction.occurred_at }} / {{ active.transaction.source }}</dd>
          <dt>交易对象</dt><dd>{{ active.transaction.counterparty }}</dd><dt>摘要</dt><dd>{{ active.transaction.summary }}</dd>
          <dt>方向 / 金额 / 资金科目</dt><dd>{{ active.transaction.direction }} / {{ active.transaction.amount }} / {{ active.transaction.payment_account }}</dd></dl>
        <p v-if="active.error" role="alert">{{ active.error }}</p><p v-if="error" role="alert">{{ error }}</p>
        <template v-if="active.draft">
          <h3>凭证草稿 · {{ active.draft.status }}</h3>
          <p>{{ active.draft.classification_reason }} · {{ active.draft.classification_rule }}</p>
          <p v-if="active.draft.is_manual_override">人工覆盖：{{ active.draft.manual_override_by }}</p>
          <form @submit.prevent="save">
            <fieldset :disabled="busy || !editing">
              <label for="draft-summary">凭证摘要</label><textarea id="draft-summary" v-model="active.draft.summary" />
              <label for="draft-date">记账日期</label><input id="draft-date" v-model="active.draft.posting_date" type="date" required>
              <DraftLines :lines="active.draft.lines" :editable="editing" :company="active.draft.company" />
              <button v-if="editing" type="submit">保存人工修改</button>
            </fieldset>
          </form>
          <template v-if="canWrite">
            <p v-if="editing">审核以已保存版本为准，请先保存人工修改。</p>
            <button v-if="editing" :disabled="busy || dirty || !active.can_approve" @click="act('approve_draft', {name: active.draft.name})">审核通过</button>
            <form v-if="editing" @submit.prevent="act('reject_draft', {name: active.draft.name, reason})"><label for="draft-reason">驳回原因</label><input id="draft-reason" v-model="reason" required :disabled="busy"><button :disabled="busy || !reason.trim()">驳回</button></form>
            <button v-if="active.draft.status === 'Rejected'" :disabled="busy" @click="act('reopen_draft', {name: active.draft.name})">重开为草稿</button>
          </template>
        </template>
        <button v-else-if="canWrite" :disabled="busy || !!active.error" @click="act('prepare_draft', {tx_name: active.transaction.name})">分类并生成待审核草稿</button>
      </div>
      <p v-else>选择一笔流水，在此核对原流水与借贷分录。</p>
    </div>
  </section>
</template>
