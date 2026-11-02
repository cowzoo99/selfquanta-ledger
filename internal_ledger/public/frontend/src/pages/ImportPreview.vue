<script setup>
import {ref, computed, onMounted} from 'vue';
import {errorMessage} from '../api';
import {sources} from '../labels';
const props = defineProps({canWrite: Boolean});
const emit = defineEmits(['imported']);
const source = ref('auto'), payment = ref(''), file = ref(null), preview = ref(null);
const upload = ref(null), busy = ref(false), error = ref(''), result = ref(null);
const paymentAccounts = ref([]);
const canConfirm = computed(() => props.canWrite && preview.value?.rows.length && !preview.value.errors.length && !busy.value);
const remainingRoute = computed(() => {
  const summary = result.value?.summary;
  if (summary?.duplicate) return '/app/ledger-workbench/review/疑似重复';
  return summary && (summary.manual || summary.conflict || summary.failed) ? '/app/ledger-workbench/review/待分类' : '';
});
function invalidate() { preview.value = null; upload.value = null; result.value = null; }
function choose(event) { file.value = event.target.files[0] || null; invalidate(); }
async function inspect() {
  if (!props.canWrite || busy.value) return;
  error.value = ''; result.value = null; preview.value = null; busy.value = true;
  try {
    if (!file.value || file.value.size > 10 * 1024 * 1024) throw new Error('请选择不超过 10 MiB 的 PDF、图片或 CSV 文件。');
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = reject;
      reader.readAsDataURL(file.value);
    });
    upload.value = {source: source.value, payment_account: payment.value, content_base64: content, filename: file.value.name};
    preview.value = (await frappe.call({method: 'internal_ledger.api.import_api.preview_import', args: upload.value})).message;
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
async function confirm() {
  if (!canConfirm.value) return;
  busy.value = true; error.value = '';
  try {
    const response = await frappe.call({method: 'internal_ledger.api.import_api.confirm_import', args: {
      ...upload.value, expected_file_hash: preview.value.file_hash,
      corrections_json: JSON.stringify(preview.value.rows),
    }});
    result.value = response.message;
    preview.value = null; upload.value = null; emit('imported');
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
onMounted(async () => {
  if (!props.canWrite) return;
  try {
    paymentAccounts.value = (await frappe.call({method: 'internal_ledger.api.import_api.list_payment_accounts'})).message || [];
    if (!payment.value && paymentAccounts.value.length === 1) payment.value = paymentAccounts.value[0].name;
  } catch (e) { error.value = errorMessage(e); }
});
</script>

<template>
  <section aria-labelledby="import-title">
    <h2 id="import-title">文件智能导入</h2>
    <p>上传银行回单、微信或支付宝截图，确认导入后自动分类并生成凭证草稿，核对后再审核过账。</p>
    <p v-if="!canWrite">当前账号仅可查看流水。</p>
    <form v-else @submit.prevent="inspect">
      <fieldset :disabled="busy">
        <label for="ledger-source">账单来源</label>
        <select id="ledger-source" v-model="source" @change="invalidate"><option value="auto">自动识别（推荐）</option><option value="bank">银行卡</option><option value="wechat">微信支付</option><option value="alipay">支付宝</option></select>
        <label for="ledger-payment">从哪个资金账户收款或付款</label>
        <select id="ledger-payment" v-model="payment" required @change="invalidate"><option value="" disabled>请选择资金账户</option><option v-for="account in paymentAccounts" :key="account.name" :value="account.name">{{ account.account_number }} · {{ account.account_name }}</option></select>
        <label for="ledger-file">PDF、JPG、PNG 或 CSV（最多 10 MiB）</label><input id="ledger-file" type="file" accept=".pdf,.jpg,.jpeg,.png,.csv" required @change="choose">
        <button type="submit">识别并预览</button>
      </fieldset>
    </form>
    <p v-if="busy" role="status">正在处理，请稍候…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="result" role="status" aria-label="导入处理结果">
      <h3>导入完成 · {{ result.batch_name }}</h3>
      <p>识别来源：{{ sources[result.detected_source] || '未知来源' }}</p>
      <dl class="ledger-counts">
        <div><dt>自动生成凭证草稿</dt><dd>{{ result.summary.created }}</dd></div>
        <div><dt>疑似重复</dt><dd>{{ result.summary.duplicate }}</dd></div>
        <div><dt>待人工分类</dt><dd>{{ result.summary.manual }}</dd></div>
        <div><dt>规则冲突</dt><dd>{{ result.summary.conflict }}</dd></div>
        <div><dt>处理失败</dt><dd>{{ result.summary.failed }}</dd></div>
      </dl>
      <a v-if="result.summary.created" href="/app/ledger-workbench/review/待审核">查看自动生成的凭证</a>
      <a v-if="remainingRoute" :href="remainingRoute">处理剩余事项</a>
    </div>
    <template v-if="canWrite && preview">
      <p>识别来源：{{ sources[preview.detected_source] || '未知来源' }}</p>
      <p>{{ preview.rows.length }} 条有效流水 · {{ preview.errors.length }} 个错误</p>
      <ul v-if="preview.errors.length"><li v-for="(issue, index) in preview.errors" :key="index">第 {{ issue.raw_row }} 页/行：{{ issue.reason }}</li></ul>
      <div class="ledger-table"><table><caption>识别结果（可修改后确认）</caption><thead><tr><th>时间</th><th>交易对象</th><th>摘要</th><th>方向</th><th>金额</th></tr></thead>
        <tbody><tr v-for="(row, index) in preview.rows" :key="index"><td><input v-model="row.occurred_at" aria-label="交易时间"></td><td><input v-model="row.counterparty" aria-label="交易对象"></td><td><input v-model="row.summary" aria-label="摘要"></td><td><select v-model="row.direction" aria-label="收支方向"><option value="expense">支出</option><option value="income">收入</option></select></td><td><input v-model="row.amount" inputmode="decimal" aria-label="金额"></td></tr></tbody>
      </table></div>
      <p v-if="!preview.rows.length">账单没有有效流水。</p>
      <button :disabled="!canConfirm" @click="confirm">确认导入</button>
    </template>
  </section>
</template>
