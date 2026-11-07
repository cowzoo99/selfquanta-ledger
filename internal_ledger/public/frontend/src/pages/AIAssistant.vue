<script setup>
import {computed, ref} from 'vue';
import {aiCall, errorMessage} from '../api';

const props = defineProps({status: {type: Object, required: true}});
const features = [
  {key: 'explain_report', label: '报表解读', description: '解释标准财务报表中的指标和变化', contextType: 'Report'},
  {key: 'suggest_classification', label: '分类建议', description: '根据一笔导入流水给出只读分类建议', contextType: 'Imported Transaction'},
  {key: 'draft_summary', label: '经营摘要', description: '根据企业或业务单据草拟经营摘要', contextType: 'Company'},
  {key: 'answer_help', label: '使用帮助', description: '解答智能导入、业务经营和月结材料用法', contextType: 'Help'},
];
const selectedFeature = ref('answer_help');
const contextName = ref('智能导入');
const question = ref('请说明这个功能的使用步骤和注意事项。');
const requestState = ref('idle');
const result = ref(null);
const error = ref('');
let generation = 0;
const feature = computed(() => features.find(item => item.key === selectedFeature.value));
const allowedFeatures = computed(() => new Set(props.status.data?.allowed_features || []));
const ready = computed(() => props.status.status === 'success' && props.status.data?.available);

function selectFeature(item) {
  selectedFeature.value = item.key;
  result.value = null;
  error.value = '';
  if (item.key === 'explain_report') contextName.value = 'General Ledger';
  else if (item.key === 'suggest_classification') contextName.value = '';
  else if (item.key === 'draft_summary') contextName.value = frappe.boot?.sysdefaults?.company || '';
  else contextName.value = '智能导入';
}

async function submit() {
  if (requestState.value === 'loading' || !ready.value || !allowedFeatures.value.has(selectedFeature.value)) return;
  if (!contextName.value.trim() || !question.value.trim()) {
    error.value = '请先填写要解读的内容和问题。';
    return;
  }
  const request = ++generation;
  requestState.value = 'loading';
  result.value = null;
  error.value = '';
  try {
    const response = await aiCall('ask_assistant', {feature: selectedFeature.value,
      context_type: feature.value.contextType, context_name: contextName.value.trim(), question: question.value.trim()});
    if (request !== generation) return;
    result.value = response;
    requestState.value = 'success';
  } catch (e) {
    if (request !== generation) return;
    error.value = errorMessage(e);
    requestState.value = 'error';
  }
}

function cancel() {
  if (requestState.value !== 'loading') return;
  generation++;
  requestState.value = 'cancelled';
  error.value = '';
  result.value = null;
}
</script>

<template>
  <section aria-labelledby="ai-assistant-title">
    <p class="ledger-eyebrow">可选能力 · 默认关闭</p>
    <h2 id="ai-assistant-title">SelfLedger AI 助手</h2>
    <p>AI 仅提供解释和建议，不能修改账目、过账、付款或提交报税。</p>
    <p v-if="status.status === 'loading'" role="status">正在检查 AI 服务状态…</p>
    <p v-else-if="status.status === 'error'" role="alert">AI 助手暂时不可用：{{ status.error }}。现有 ERP 功能不受影响。</p>
    <p v-else-if="!status.data?.enabled" role="status">AI 助手未启用。现有 ERP 功能不受影响。</p>
    <p v-else-if="!status.data?.available" role="alert">AI 助手尚未完成服务配置。现有 ERP 功能不受影响。</p>
    <template v-else>
      <div class="ledger-cards" aria-label="AI 辅助功能">
        <button v-for="item in features" :key="item.key" type="button" class="ledger-card ledger-ai-feature"
          :aria-current="selectedFeature === item.key ? 'page' : undefined"
          :disabled="!allowedFeatures.has(item.key) || requestState === 'loading'" @click="selectFeature(item)">
          <strong>{{ item.label }}</strong><span>{{ item.description }}</span>
        </button>
      </div>
      <fieldset class="ledger-ai-form" :disabled="requestState === 'loading'">
        <label for="ai-context">要解读的内容</label>
        <select v-if="selectedFeature === 'explain_report'" id="ai-context" v-model="contextName">
          <option>General Ledger</option><option>Profit and Loss Statement</option><option>Balance Sheet</option>
          <option>Accounts Receivable</option><option>Accounts Payable</option><option>Stock Ledger</option>
        </select>
        <select v-else-if="selectedFeature === 'answer_help'" id="ai-context" v-model="contextName">
          <option>智能导入</option><option>业务经营</option><option>月结材料</option>
        </select>
        <input v-else id="ai-context" v-model="contextName" :placeholder="selectedFeature === 'suggest_classification' ? '输入流水编号' : '输入企业或单据编号'">
        <label for="ai-question">问题</label>
        <textarea id="ai-question" v-model="question" maxlength="2000" rows="4"></textarea>
      </fieldset>
      <button type="button" :disabled="requestState === 'loading' || !allowedFeatures.has(selectedFeature)" @click="submit">获取 AI 建议</button>
      <button v-if="requestState === 'loading'" type="button" @click="cancel">取消请求</button>
      <p v-if="requestState === 'loading'" role="status">正在生成建议，请勿重复提交…</p>
      <p v-else-if="requestState === 'cancelled'" role="status">请求已取消。</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <article v-if="result" class="ledger-ai-answer" aria-live="polite">
        <h3>AI 建议，需人工复核</h3>
        <p>{{ result.answer }}</p>
        <h4>引用</h4>
        <ul><li v-for="citation in result.citations" :key="`${citation.doctype}-${citation.name}`">
          <a :href="citation.route">{{ citation.doctype }} · {{ citation.name }}</a>
        </li></ul>
        <h4>注意事项</h4>
        <ul><li v-for="warning in result.warnings" :key="warning">{{ warning }}</li></ul>
      </article>
    </template>
  </section>
</template>

<style scoped>
.ledger-ai-feature { text-align: left; margin: 0; }
.ledger-ai-feature[aria-current="page"] { border-color: #155775; background: #e8f4f8; color: inherit; }
.ledger-ai-form { margin-top: 18px; }
.ledger-ai-form input, .ledger-ai-form select, .ledger-ai-form textarea { width: 100%; }
.ledger-ai-answer { margin-top: 18px; padding: 16px; border-left: 4px solid #155775; background: #f7fbfd; }
</style>
