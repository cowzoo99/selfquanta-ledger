<script setup>
import {ref, onMounted} from 'vue';
import Dashboard from './pages/Dashboard.vue';
import ImportPreview from './pages/ImportPreview.vue';
import ReviewCenter from './pages/ReviewCenter.vue';
import {call, errorMessage} from './api';
const page = ref('dashboard'), month = ref(frappe.datetime.get_today().slice(0, 7));
const counts = ref(null), items = ref([]), loading = ref(false), error = ref('');
const canWrite = frappe.session.user === 'Administrator' || frappe.user_roles.includes('账套管理员');
async function refresh() {
  loading.value = true; error.value = '';
  try {
    const result = await Promise.all([call('get_dashboard_counts', {month: month.value}), call('list_review_items')]);
    counts.value = result[0]; items.value = result[1];
  } catch (e) { error.value = errorMessage(e); }
  finally { loading.value = false; }
}
onMounted(refresh);
</script>

<template>
  <div class="ledger-app">
    <nav aria-label="内账工作台导航"><h2>协同内账</h2>
      <button v-for="entry in [['dashboard', '工作台'], ['import', '导入预览'], ['review', '审核中心']]" :key="entry[0]" :aria-current="page === entry[0] ? 'page' : undefined" @click="page = entry[0]">{{ entry[1] }}</button>
      <a v-if="canWrite" href="/app/classification-rule">维护分类规则</a>
    </nav>
    <main>
      <header><label for="ledger-month">月度摘要</label><input id="ledger-month" v-model="month" type="month" @change="refresh"><button :disabled="loading" @click="refresh">刷新</button>
        <dl v-if="counts" class="ledger-counts"><div><dt>收入</dt><dd>{{ counts.income }}</dd></div><div><dt>支出</dt><dd>{{ counts.expense }}</dd></div><div><dt>待分类</dt><dd>{{ counts.pending_classification }}</dd></div><div><dt>待审核</dt><dd>{{ counts.pending_review }}</dd></div><div><dt>已过账</dt><dd>{{ counts.posted }}</dd></div></dl>
      </header>
      <p v-if="error" role="alert">{{ error }}</p>
      <Dashboard v-if="page === 'dashboard'" :items="items" :loading="loading" @navigate="page = $event" />
      <ImportPreview v-else-if="page === 'import'" :can-write="canWrite" @imported="refresh" />
      <ReviewCenter v-else :items="items" :loading="loading" :can-write="canWrite" @refresh="refresh" />
    </main>
  </div>
</template>

<style>
.ledger-app { display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 24px; padding: 20px; color: var(--text-color, #222); }
.ledger-app nav { display: flex; flex-direction: column; gap: 12px; }
.ledger-app main, .ledger-detail { min-width: 0; }
.ledger-app header, .ledger-app section { padding: 16px; border: 1px solid var(--border-color, #ddd); border-radius: 8px; margin-bottom: 20px; }
.ledger-app button { padding: 8px 12px; margin: 4px; border: 1px solid #66788a; border-radius: 5px; background: var(--card-bg, white); color: inherit; }
.ledger-app button[aria-current="page"] { background: #155775; color: white; }
.ledger-app button:disabled { opacity: .55; cursor: default; }
.ledger-app :focus-visible { outline: 3px solid #2877a4; outline-offset: 2px; }
.ledger-app input:not([type="checkbox"]), .ledger-app select, .ledger-app textarea { max-width: 100%; padding: 7px; border: 1px solid #8b959e; border-radius: 4px; color: inherit; background: var(--card-bg, white); }
.ledger-app fieldset { border: 0; padding: 0; display: grid; gap: 8px; }
.ledger-app label { margin-top: 8px; }
.ledger-counts { display: flex; flex-wrap: wrap; gap: 24px; margin: 16px 0 0; }
.ledger-counts dd { font-size: 20px; margin: 0; }
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
@media (max-width: 800px) { .ledger-app, .ledger-review { grid-template-columns: minmax(0, 1fr); } .ledger-app { padding: 6px; gap: 10px; } .ledger-app nav { flex-direction: row; flex-wrap: wrap; align-items: center; } }
</style>
