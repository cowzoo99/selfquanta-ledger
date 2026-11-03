<script setup>
import {ref, computed, onMounted} from 'vue';
import Dashboard from './pages/Dashboard.vue';
import ImportPreview from './pages/ImportPreview.vue';
import ReviewCenter from './pages/ReviewCenter.vue';
import EnterpriseMembers from './pages/EnterpriseMembers.vue';
import DuplicateReview from './pages/DuplicateReview.vue';
import {call, errorMessage, setLanguage} from './api';
const props = defineProps({view: {type: String, default: 'dashboard'}, filter: {type: String, default: ''}, batch: {type: String, default: ''}});
const month = ref(frappe.datetime.get_today().slice(0, 7));
const counts = ref(null), items = ref([]), loading = ref(false), error = ref('');
const language = ref((frappe.boot?.lang || '').startsWith('zh') ? 'zh' : 'en');
const savingLanguage = ref(false);
const canWrite = frappe.session.user === 'Administrator' || frappe.user_roles.includes('账套管理员');
const navigation = [['dashboard', '业务总览'], ['import', '文件智能导入'], ['review', '待办中心'], ...(canWrite ? [['members', '企业成员']] : [])];
const page = computed(() => navigation.some(entry => entry[0] === props.view) || ['duplicates', 'classification'].includes(props.view) ? props.view : 'dashboard');
const taskPage = computed(() => ['duplicates', 'classification'].includes(page.value) ? 'review' : page.value);
const reviewFilter = computed(() => page.value === 'classification' ? '待分类' : (props.filter || '待审核'));
async function refresh() {
  loading.value = true; error.value = '';
  try {
    const result = await Promise.all([call('get_dashboard_counts', {month: month.value}), call('list_review_items')]);
    counts.value = result[0]; items.value = result[1];
  } catch (e) { error.value = errorMessage(e); }
  finally { loading.value = false; }
}
async function changeLanguage() {
  savingLanguage.value = true; error.value = '';
  try {
    await setLanguage(language.value);
    window.location.reload();
  } catch (e) {
    error.value = errorMessage(e);
    savingLanguage.value = false;
  }
}
onMounted(refresh);
</script>

<template>
  <div class="ledger-app">
    <main>
      <header>
      <h2>{{ navigation.find(entry => entry[0] === taskPage)?.[1] }}</h2>
      <div class="ledger-tabs" role="group" aria-label="智能内账视图">
        <a v-for="entry in navigation" :key="entry[0]" :href="`/app/ledger-workbench/${entry[0]}`" :aria-current="taskPage === entry[0] ? 'page' : undefined">{{ entry[1] }}</a>
        <a href="/app/query-report/General%20Ledger">账簿与报表</a>
      </div>
      <label class="language-switch" for="ledger-language">界面语言
        <select id="ledger-language" v-model="language" :disabled="savingLanguage" @change="changeLanguage">
          <option value="zh">简体中文</option><option value="en">English</option>
        </select>
      </label>
      <a v-if="canWrite" href="/app/classification-rule">维护分类规则</a>
      <label for="ledger-month">月度摘要</label><input id="ledger-month" v-model="month" type="month" @change="refresh"><button :disabled="loading" @click="refresh">刷新</button>
      </header>
      <p v-if="error" role="alert">{{ error }}</p>
      <Dashboard v-if="page === 'dashboard'" :items="items" :counts="counts" :month="month" :can-write="canWrite" :loading="loading" />
      <ImportPreview v-else-if="page === 'import'" :can-write="canWrite" @imported="refresh" />
      <DuplicateReview v-else-if="page === 'duplicates'" :can-write="canWrite" @refresh="refresh" />
      <EnterpriseMembers v-else-if="page === 'members' && canWrite" :active="page === 'members'" />
      <ReviewCenter v-else :items="items" :loading="loading" :can-write="canWrite" :initial-filter="reviewFilter" :initial-batch="batch" @refresh="refresh" />
    </main>
  </div>
</template>

<style>
.ledger-app { padding: 20px; color: var(--text-color, #222); }
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
@media (max-width: 900px) { .ledger-hero { grid-template-columns: minmax(0, 1fr); padding: 20px; } }
@media (max-width: 800px) { .ledger-review { grid-template-columns: minmax(0, 1fr); } .ledger-app { padding: 6px; } }
</style>
