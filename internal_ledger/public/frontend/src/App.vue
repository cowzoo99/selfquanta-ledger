<script setup>
import {ref, reactive, computed, onMounted} from 'vue';
import Dashboard from './pages/Dashboard.vue';
import ImportPreview from './pages/ImportPreview.vue';
import ReviewCenter from './pages/ReviewCenter.vue';
import EnterpriseMembers from './pages/EnterpriseMembers.vue';
import DuplicateReview from './pages/DuplicateReview.vue';
import AccountingCenter from './pages/AccountingCenter.vue';
import WorkflowNavigation from './components/WorkflowNavigation.vue';
import {call, errorMessage, setLanguage} from './api';
const props = defineProps({view: {type: String, default: 'dashboard'}, filter: {type: String, default: ''}, batch: {type: String, default: ''}, transaction: {type: String, default: ''}});
const month = ref(frappe.datetime.get_today().slice(0, 7));
const counts = reactive({status: 'idle', data: null, error: ''});
const list = reactive({status: 'idle', data: null, error: ''});
const classificationRun = reactive({status: 'idle', summary: null, error: ''});
const loading = computed(() => classificationRun.status === 'loading' || counts.status === 'loading' || list.status === 'loading');
const currentCounts = computed(() => counts.status === 'success' && counts.data?.month === month.value ? counts.data : null);
const language = ref((frappe.boot?.lang || '').startsWith('zh') ? 'zh' : 'en');
const savingLanguage = ref(false), languageError = ref('');
const error = computed(() => languageError.value || counts.error || list.error);
const canWrite = frappe.session.user === 'Administrator' || frappe.user_roles.includes('账套管理员');
const navigation = [['dashboard', '业务总览'], ['import', '文件智能导入'], ['review', '待办中心'], ['accounting', '会计中心'], ...(canWrite ? [['members', '企业成员']] : [])];
const page = computed(() => navigation.some(entry => entry[0] === props.view) || ['duplicates', 'classification'].includes(props.view) ? props.view : 'dashboard');
const taskPage = computed(() => ['duplicates', 'classification'].includes(page.value) ? 'review' : page.value);
const reviewFilter = computed(() => page.value === 'classification' ? '待分类' : (props.filter || '待审核'));
const workflowStage = computed(() => {
  if (page.value === 'import') return 1;
  if (page.value === 'duplicates' || reviewFilter.value === '疑似重复') return 2;
  if (reviewFilter.value === '待分类') return 3;
  if (reviewFilter.value === '待审核') return 4;
  if (['待过账', '已过账'].includes(reviewFilter.value)) return 5;
  return 0;
});
const workflowItems = computed(() => (list.data || []).filter(item => !props.batch || item.transaction.import_batch === props.batch));
const workflowCounts = computed(() => ({
  duplicates: workflowItems.value.filter(item => item.transaction.duplicate_review_status === 'Pending').length,
  classification: workflowItems.value.filter(item => item.transaction.status === 'Imported' && !item.draft).length,
  drafts: workflowItems.value.filter(item => ['Draft', 'Rejected'].includes(item.draft?.status)).length,
  posting: workflowItems.value.filter(item => ['Approved', 'Posted'].includes(item.draft?.status)).length,
}));
let refreshGeneration = 0;
async function load(resource, request, generation) {
  Object.assign(resource, {status: 'loading', data: null, error: '', generation});
  try {
    const data = await request;
    if (generation === refreshGeneration) Object.assign(resource, {data, status: 'success'});
  } catch (e) {
    if (generation === refreshGeneration) Object.assign(resource, {error: errorMessage(e), status: 'error'});
  }
}
function refresh() {
  const generation = ++refreshGeneration;
  languageError.value = '';
  return Promise.all([
    load(counts, call('get_dashboard_counts', {month: month.value}), generation),
    load(list, call('list_review_items'), generation),
  ]);
}
async function refreshPage() {
  if (workflowStage.value === 3 && canWrite) {
    Object.assign(classificationRun, {status: 'loading', summary: null, error: ''});
    try {
      const response = await frappe.call({method: 'internal_ledger.api.review_api.auto_classify_pending', type: 'POST'});
      Object.assign(classificationRun, {status: 'success', summary: response.message.summary, error: ''});
    } catch (e) {
      Object.assign(classificationRun, {status: 'error', summary: null, error: '自动分类失败：' + errorMessage(e)});
    }
  }
  return refresh();
}
async function changeLanguage() {
  savingLanguage.value = true; languageError.value = '';
  try {
    await setLanguage(language.value);
    window.location.reload();
  } catch (e) {
    languageError.value = errorMessage(e);
    savingLanguage.value = false;
  }
}
onMounted(refreshPage);
</script>

<template>
  <div class="ledger-app">
    <main>
      <header>
      <h2>{{ navigation.find(entry => entry[0] === taskPage)?.[1] }}</h2>
      <div class="ledger-tabs" role="group" aria-label="智能内账视图">
        <a v-for="entry in navigation" :key="entry[0]" :href="`/app/ledger-workbench/${entry[0]}`" :aria-current="taskPage === entry[0] ? 'page' : undefined">{{ entry[1] }}</a>
      </div>
      <label class="language-switch" for="ledger-language">界面语言
        <select id="ledger-language" v-model="language" :disabled="savingLanguage" @change="changeLanguage">
          <option value="zh">简体中文</option><option value="en">English</option>
        </select>
      </label>
      <a v-if="canWrite" href="/app/classification-rule">维护分类规则</a>
      <label for="ledger-month">月度摘要</label><input id="ledger-month" v-model="month" type="month" @change="refresh"><button :disabled="loading" @click="refreshPage">刷新</button>
      </header>
      <p v-if="error" role="alert">{{ error }}</p>
      <WorkflowNavigation v-if="workflowStage" :current-stage="workflowStage" :batch="batch" :counts="workflowCounts" />
      <Dashboard v-if="page === 'dashboard'" :items="list.data" :counts="currentCounts" :month="month" :can-write="canWrite" :loading="counts.status === 'loading'" />
      <ImportPreview v-else-if="page === 'import'" :can-write="canWrite" @imported="refresh" />
      <DuplicateReview v-else-if="page === 'duplicates'" :can-write="canWrite" :batch="batch" :transaction="transaction" @refresh="refresh" />
      <EnterpriseMembers v-else-if="page === 'members' && canWrite" :active="page === 'members'" />
      <AccountingCenter v-else-if="page === 'accounting'" />
      <ReviewCenter v-else :list="list" :classification-run="classificationRun" :can-write="canWrite" :initial-filter="reviewFilter" :initial-batch="batch" :initial-transaction="transaction" @refresh="refresh" />
    </main>
  </div>
</template>

<style>
.ledger-app { padding: 20px; color: var(--text-color, #222); }
.ledger-app:has(.ledger-step-footer) { padding-bottom: 92px; }
.ledger-tabs { display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 12px; }
.ledger-tabs [aria-current="page"] { font-weight: bold; text-decoration: underline; }
.language-switch { display: inline-flex; gap: 8px; align-items: center; margin-right: 16px; font-size: 13px; }
.ledger-app main, .ledger-detail { min-width: 0; }
.ledger-app header, .ledger-app section { padding: 16px; border: 1px solid var(--border-color, #ddd); border-radius: 8px; margin-bottom: 20px; }
.ledger-hero { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(260px, .8fr); gap: 24px; align-items: center; padding: 28px; margin: -4px -4px 20px; border-radius: 12px; background: linear-gradient(135deg, #eef7fb, #f6f2ff); border: 1px solid #c7dbe7; }
.ledger-eyebrow { color: #155775; font-weight: 700; letter-spacing: .04em; margin: 0 0 6px; }
.ledger-hero h2 { margin: 0 0 10px; font-size: clamp(24px, 3vw, 36px); line-height: 1.25; }
.ledger-primary-action { display: flex; flex-direction: column; gap: 8px; padding: 22px; border-radius: 10px; background: #155775; color: #fff !important; text-decoration: none !important; box-shadow: 0 8px 20px rgba(21, 87, 117, .22); }
.ledger-primary-action strong { font-size: 20px; }
.ledger-primary-action span { color: #e5f4fa; line-height: 1.5; }
.ledger-primary-action:hover { background: #0f4863; }
.ledger-app button { padding: 8px 12px; margin: 4px; border: 1px solid #66788a; border-radius: 5px; background: var(--card-bg, white); color: inherit; }
.ledger-app button[aria-current="page"] { background: #155775; color: white; }
.ledger-app button:disabled { opacity: .55; cursor: default; }
.ledger-app :focus-visible { outline: 3px solid #2877a4; outline-offset: 2px; }
.ledger-app input:not([type="checkbox"]), .ledger-app select, .ledger-app textarea { max-width: 100%; padding: 7px; border: 1px solid #8b959e; border-radius: 4px; color: inherit; background: var(--card-bg, white); }
.ledger-app fieldset { border: 0; padding: 0; display: grid; gap: 8px; }
.ledger-app label { margin-top: 8px; }
.ledger-counts { display: flex; flex-wrap: wrap; gap: 24px; margin: 16px 0 0; }
.ledger-counts dd { font-size: 20px; margin: 0; }
.ledger-cards, .duplicate-evidence { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr)); gap: 16px; }
.ledger-card { display: block; padding: 16px; border: 1px solid var(--border-color, #ddd); border-radius: 8px; }
.ledger-module-card { border-top: 4px solid #7a6c9d; }
.ledger-card strong, .ledger-card span { display: block; margin-bottom: 8px; }
.ledger-review { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(0, 2fr); gap: 20px; }
.ledger-items { list-style: none; padding: 0; max-height: 650px; overflow-y: auto; }
.ledger-items li { border-bottom: 1px solid #ddd; padding: 12px 0; display: grid; gap: 6px; overflow-wrap: anywhere; }
.ledger-table { overflow-x: auto; max-width: 100%; }
.ledger-app table { width: 100%; border-collapse: collapse; }
.ledger-app th, .ledger-app td { padding: 8px; border-bottom: 1px solid #ddd; text-align: left; }
.ledger-app td input { min-width: 90px; width: 100%; }
.ledger-detail dl { display: grid; grid-template-columns: minmax(90px, 120px) minmax(0, 1fr); gap: 6px 10px; }
.ledger-detail dd { margin: 0; overflow-wrap: anywhere; }
.ledger-app [role="alert"] { color: #a12621; }
.ledger-workflow { background: #f7fbfd; }
.ledger-workflow ol { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 8px; list-style: none; padding: 0; margin: 12px 0 0; }
.ledger-workflow a { display: grid; grid-template-columns: auto 1fr; gap: 3px 8px; align-items: center; padding: 12px; border: 1px solid #b8c8d2; border-radius: 8px; color: inherit; text-decoration: none; }
.ledger-workflow a > span { display: grid; place-items: center; grid-row: 1 / 3; width: 28px; height: 28px; border-radius: 50%; background: #dfeaf0; font-weight: 700; }
.ledger-workflow a[aria-current="step"] { border-color: #155775; background: #e8f4f8; font-weight: 700; }
.ledger-workflow a[aria-current="step"] > span { background: #155775; color: white; }
.ledger-workflow small { color: #56636d; }
.ledger-step-footer { position: fixed; z-index: 30; left: max(300px, 18vw); right: 24px; bottom: 16px; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 12px; padding: 12px 16px; border: 1px solid #9fb6c3; border-radius: 10px; background: rgba(255,255,255,.97); box-shadow: 0 6px 24px rgba(24,55,72,.2); }
.ledger-step-footer > :last-child { justify-self: end; }
.ledger-pagination { display: flex; align-items: center; justify-content: center; gap: 10px; margin: 12px 0; }
.ledger-evidence-editor { min-height: 100px; padding: 10px; border: 1px solid #8b959e; border-radius: 4px; overflow-wrap: anywhere; overflow-x: auto; }
.ledger-evidence-editor:focus { outline: 3px solid #2877a4; outline-offset: 2px; }
.ledger-evidence-preview { display: block; max-width: 100%; max-height: 280px; object-fit: contain; }
.ledger-evidence-attachments { display: grid; gap: 12px; padding-left: 20px; overflow-wrap: anywhere; }
@media (max-width: 800px) { .ledger-evidence-attachments { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 900px) { .ledger-hero { grid-template-columns: minmax(0, 1fr); padding: 20px; } }
@media (max-width: 800px) { .ledger-review { grid-template-columns: minmax(0, 1fr); } .ledger-app { padding: 6px 6px 110px; } .ledger-workflow ol { grid-template-columns: 1fr; } .ledger-step-footer { left: 8px; right: 8px; bottom: 8px; font-size: 12px; } }
</style>
