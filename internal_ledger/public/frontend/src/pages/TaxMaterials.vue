<script setup>
import {computed, onMounted, reactive, ref, watch} from 'vue';
import {errorMessage, financeCall} from '../api';

const props = defineProps({canWrite: Boolean});
const company = ref('');
const period = ref(frappe.datetime.get_today().slice(0, 7));
const packageType = ref('monthly_standard');
const material = ref(null);
const state = reactive({status: 'loading', error: '', notice: ''});
const busy = computed(() => state.status === 'loading' || state.status === 'saving');

async function loadLatest() {
  state.status = 'loading'; state.error = ''; state.notice = '';
  try {
    if (!company.value) company.value = (await financeCall('close', 'get_finance_context')).company;
    material.value = await financeCall('tax', 'get_latest_tax_material_package',
      {period: period.value, package_type: packageType.value});
    state.status = 'success';
  } catch (e) {state.status = 'error'; state.error = errorMessage(e);}
}

async function buildPackage() {
  if (!props.canWrite || busy.value || !company.value) return;
  state.status = 'saving'; state.error = ''; state.notice = '';
  try {
    material.value = await financeCall('tax', 'build_tax_material_package',
      {company: company.value, period: period.value, package_type: packageType.value});
    state.status = 'success'; state.notice = '材料包已生成并作为私有文件保存。';
  } catch (e) {state.status = 'error'; state.error = errorMessage(e);}
}

watch(packageType, type => {
  const year = period.value.slice(0, 4);
  period.value = type === 'annual_standard' ? year : type === 'quarterly_standard' ? `${year}-Q1` : `${year}-01`;
  loadLatest();
});
onMounted(loadLatest);
</script>

<template>
  <section aria-labelledby="tax-materials-title">
    <h2 id="tax-materials-title">报税材料</h2>
    <nav class="accounting-tabs" aria-label="会计中心功能" role="tablist">
      <a href="/app/ledger-workbench/review">智能记账</a>
      <a href="/app/ledger-workbench/accounting">凭证与总账</a>
      <a href="/app/ledger-workbench/month-end">月结检查</a>
      <a href="/app/ledger-workbench/tax-materials" aria-current="page">报税材料</a>
    </nav>
    <p>从正式总账、发票和工资单生成可交付会计或税务人员复核的中文材料包。</p>
    <p v-if="!canWrite" class="ledger-readonly">当前是只读权限，可以查看和下载已有材料包，不能生成新版本。</p>
    <div class="finance-toolbar">
      <label>材料类型<select v-model="packageType" :disabled="busy">
        <option value="monthly_standard">月度标准材料包</option>
        <option value="quarterly_standard">季度标准材料包</option>
        <option value="annual_standard">年度标准材料包</option>
      </select></label>
      <label>申报期间<input v-model="period" type="text" :disabled="busy" @change="loadLatest"></label>
      <button type="button" :disabled="!canWrite || busy || !company" @click="buildPackage">生成材料包</button>
    </div>
    <p v-if="state.status === 'loading' || state.status === 'saving'" role="status">正在读取报税材料……</p>
    <p v-if="state.error" role="alert">{{ state.error }}</p>
    <p v-if="state.notice" role="status">{{ state.notice }}</p>
    <p v-if="state.status === 'success' && !material">当前期间还没有材料包。</p>
    <article v-if="material" class="material-card">
      <div><h3>{{ material.company }} · {{ material.period }}</h3><strong>版本 {{ material.version }}</strong></div>
      <dl>
        <dt>来源校验码</dt><dd>{{ material.source_hash.slice(0, 12) }}</dd>
        <dt>生成人</dt><dd>{{ material.generated_by }}</dd>
        <dt>生成时间</dt><dd>{{ material.generated_at }}</dd>
      </dl>
      <p class="material-warning">{{ material.disclaimer }}</p>
      <a class="ledger-primary-action" :href="material.file"><strong>下载私有材料包</strong><span>每次下载都会重新校验当前账号和账套权限</span></a>
    </article>
  </section>
</template>

<style scoped>
.finance-toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; margin: 16px 0; }
.finance-toolbar label { display: grid; gap: 5px; }
.material-card { max-width: 760px; padding: 18px; border: 1px solid #b8c8d2; border-radius: 10px; background: #f8fbfc; }
.material-card > div { display: flex; justify-content: space-between; gap: 12px; }
.material-card dl { display: grid; grid-template-columns: 120px 1fr; gap: 7px 12px; }
.material-card dd { margin: 0; overflow-wrap: anywhere; }
.material-warning { padding: 10px 12px; border-left: 4px solid #b56a00; background: #fff5df; }
.ledger-readonly { padding: 10px; background: #f1f4f6; }
</style>
