<script setup>
import {computed, onMounted, reactive, ref} from 'vue';
import {errorMessage, financeCall} from '../api';

const props = defineProps({canWrite: Boolean, canClose: Boolean});
const month = ref(frappe.datetime.get_today().slice(0, 7));
const company = ref('');
const result = ref(null);
const state = reactive({status: 'loading', error: '', notice: ''});
const busy = computed(() => state.status === 'loading' || state.status === 'saving');
const blocking = computed(() => (result.value?.checks || []).filter(row => row.severity === 'Blocking' && row.status === 'Fail'));
const statusLabel = status => status === 'Pass' ? '通过' : '需处理';
const severityLabel = severity => severity === 'Blocking' ? '阻断项' : '提醒项';

async function loadLatest() {
  state.status = 'loading'; state.error = ''; state.notice = '';
  try {
    if (!company.value) company.value = (await financeCall('close', 'get_finance_context')).company;
    result.value = await financeCall('close', 'get_latest_month_end_run', {period: month.value});
    state.status = 'success';
  } catch (e) {state.status = 'error'; state.error = errorMessage(e);}
}

async function runChecks() {
  if (!props.canWrite || busy.value || !company.value) return;
  state.status = 'saving'; state.error = ''; state.notice = '';
  try {
    result.value = await financeCall('close', 'run_month_end_checks', {company: company.value, period: month.value});
    state.status = 'success'; state.notice = '月结检查已完成，请处理所有阻断项后再关账。';
  } catch (e) {state.status = 'error'; state.error = errorMessage(e);}
}

async function confirmClose() {
  if (!props.canClose || busy.value || !result.value || blocking.value.length || result.value.status === 'Closed') return;
  if (!window.confirm(`确认关闭 ${company.value} 的 ${month.value} 会计期间？关账后该期间不能继续过账。`)) return;
  state.status = 'saving'; state.error = ''; state.notice = '';
  try {
    const closed = await financeCall('close', 'confirm_period_close', {run_name: result.value.name});
    result.value = {...result.value, status: closed.status, accounting_period: closed.accounting_period};
    state.status = 'success'; state.notice = `期间已关闭：${closed.accounting_period}`;
  } catch (e) {state.status = 'error'; state.error = errorMessage(e);}
}

onMounted(loadLatest);
</script>

<template>
  <section aria-labelledby="month-end-title">
    <h2 id="month-end-title">月结检查</h2>
    <nav class="accounting-tabs" aria-label="会计中心功能" role="tablist">
      <a href="/app/ledger-workbench/review">智能记账</a>
      <a href="/app/ledger-workbench/accounting">凭证与总账</a>
      <a href="/app/ledger-workbench/month-end" aria-current="page">月结检查</a>
      <a href="/app/ledger-workbench/tax-materials">报税材料</a>
    </nav>
    <p>先检查未过账草稿、疑似重复、对账、库存、发票、借贷平衡、工资和税务资料，再由授权人员确认关账。</p>
    <p v-if="!canWrite" class="ledger-readonly">当前是只读权限，可以查看检查结果，不能运行检查或确认关账。</p>
    <div class="finance-toolbar">
      <label>会计月份<input v-model="month" type="month" :disabled="busy" @change="loadLatest"></label>
      <button type="button" :disabled="!canWrite || busy || !company" @click="runChecks">运行月结检查</button>
      <button type="button" :disabled="!canClose || busy || !result || blocking.length > 0 || result?.status === 'Closed'" @click="confirmClose">确认关账</button>
    </div>
    <p v-if="state.status === 'loading' || state.status === 'saving'" role="status">正在读取月结检查……</p>
    <p v-if="state.error" role="alert">{{ state.error }}</p>
    <p v-if="state.notice" role="status">{{ state.notice }}</p>
    <div v-if="result" class="finance-summary">
      <strong>{{ result.company }} · {{ result.period_start }} 至 {{ result.period_end }}</strong>
      <span>{{ result.status === 'Closed' ? '已关账' : `检查完成，${blocking.length} 个阻断项` }}</span>
    </div>
    <h3>检查结果</h3>
    <p v-if="state.status === 'success' && !result">本月还没有检查记录，请运行月结检查。</p>
    <ul v-if="result" class="finance-checks">
      <li v-for="row in result.checks" :key="row.code" :class="row.status === 'Pass' ? 'check-pass' : 'check-fail'">
        <div><strong>{{ row.message }}</strong><span>{{ severityLabel(row.severity) }} · {{ statusLabel(row.status) }} · {{ row.count }} 项</span></div>
        <a v-if="row.status !== 'Pass'" :href="row.target_route">前往处理</a>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.finance-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin: 16px 0; }
.finance-toolbar label { display: grid; gap: 5px; }
.finance-summary, .finance-checks li { display: flex; justify-content: space-between; gap: 12px; padding: 12px; border: 1px solid #cbd5dc; border-radius: 8px; }
.finance-summary span, .finance-checks span { color: #596873; }
.finance-checks { list-style: none; padding: 0; display: grid; gap: 9px; }
.finance-checks li > div { display: grid; gap: 4px; }
.check-fail { border-left: 5px solid #b23a2d !important; background: #fff5f3; }
.check-pass { border-left: 5px solid #277a45 !important; }
.ledger-readonly { padding: 10px; background: #f1f4f6; }
</style>
