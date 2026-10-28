<script setup>
import {ref, computed} from 'vue';
import {errorMessage} from '../api';
const props = defineProps({canWrite: Boolean});
const emit = defineEmits(['imported']);
const source = ref('bank'), payment = ref(''), file = ref(null), preview = ref(null);
const upload = ref(null), busy = ref(false), error = ref(''), result = ref('');
const canConfirm = computed(() => props.canWrite && preview.value?.rows.length && !preview.value.errors.length && !busy.value);
function invalidate() { preview.value = null; upload.value = null; result.value = ''; }
function choose(event) { file.value = event.target.files[0] || null; invalidate(); }
async function inspect() {
  error.value = ''; result.value = ''; preview.value = null; busy.value = true;
  try {
    if (!file.value || file.value.size > 2 * 1024 * 1024) throw new Error('请选择不超过 2 MiB 的 CSV 文件。');
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = reject;
      reader.readAsDataURL(file.value);
    });
    upload.value = {source: source.value, payment_account: payment.value, content_base64: content};
    preview.value = (await frappe.call({method: 'internal_ledger.api.import_api.preview_import', args: upload.value})).message;
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
async function confirm() {
  busy.value = true; error.value = '';
  try {
    const response = await frappe.call({method: 'internal_ledger.api.import_api.confirm_import', args: {
      ...upload.value, expected_file_hash: preview.value.file_hash,
    }});
    result.value = `导入成功，批次 ${response.message.batch_name}`;
    preview.value = null; upload.value = null; emit('imported');
  } catch (e) { error.value = errorMessage(e); }
  finally { busy.value = false; }
}
</script>

<template>
  <section aria-labelledby="import-title">
    <h2 id="import-title">导入预览</h2>
    <p v-if="!canWrite">当前账号仅可查看流水。</p>
    <form v-else @submit.prevent="inspect">
      <fieldset :disabled="busy">
        <label for="ledger-source">账单来源</label>
        <select id="ledger-source" v-model="source" @change="invalidate"><option value="bank">银行卡</option><option value="wechat">微信支付</option><option value="alipay">支付宝</option></select>
        <label for="ledger-payment">资金科目完整名称</label><input id="ledger-payment" v-model="payment" required @input="invalidate">
        <label for="ledger-file">CSV 账单（最多 2 MiB）</label><input id="ledger-file" type="file" accept=".csv" required @change="choose">
        <button type="submit">预览账单</button>
      </fieldset>
    </form>
    <p v-if="busy" role="status">正在处理，请稍候…</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="result" role="status">{{ result }}</p>
    <template v-if="preview">
      <p>{{ preview.rows.length }} 条有效流水 · {{ preview.errors.length }} 个错误</p>
      <ul v-if="preview.errors.length"><li v-for="(issue, index) in preview.errors" :key="index">{{ issue }}</li></ul>
      <div class="ledger-table"><table><caption>原始流水预览</caption><thead><tr><th>时间</th><th>交易对象</th><th>摘要</th><th>方向</th><th>金额</th></tr></thead>
        <tbody><tr v-for="(row, index) in preview.rows" :key="index"><td>{{ row.occurred_at }}</td><td>{{ row.counterparty }}</td><td>{{ row.summary }}</td><td>{{ row.direction }}</td><td>{{ row.amount }}</td></tr></tbody>
      </table></div>
      <p v-if="!preview.rows.length">账单没有有效流水。</p>
      <button :disabled="!canConfirm" @click="confirm">确认导入</button>
    </template>
  </section>
</template>
