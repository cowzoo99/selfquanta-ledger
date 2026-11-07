<script setup>
import {computed, onBeforeUnmount, onMounted, ref} from 'vue';
import {errorMessage, exportCall} from '../api';

const exports = ref([]);
const loading = ref(false);
const requesting = ref(false);
const deleting = ref('');
const error = ref('');
let pollTimer = null;
let disposed = false;

const hasGenerating = computed(() => exports.value.some(item => item.status === 'Generating'));
const statusLabels = {Generating: '正在整理', Ready: '准备完成', Failed: '生成失败', Deleted: '已删除'};
const failureLabels = {
  ATTACHMENT_READ_FAILED: '有附件暂时无法读取，请检查文件后重试',
  ARCHIVE_VERIFY_FAILED: '退出包完整性校验失败，请重新生成',
  DATA_EXPORT_FAILED: '退出包生成失败，请稍后重试',
};

function formatSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function shortHash(value) {
  return value ? value.slice(0, 16) : '等待生成';
}

function downloadHref(name) {
  return `/api/method/internal_ledger.api.export_api.download_customer_exit_export?name=${encodeURIComponent(name)}`;
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (!disposed && hasGenerating.value) pollTimer = setTimeout(() => loadExports(true), 3000);
}

async function loadExports(quiet = false) {
  if (!quiet) loading.value = true;
  try {
    exports.value = await exportCall('list_customer_exit_exports');
    error.value = '';
  } catch (exception) {
    error.value = `读取退出包失败：${errorMessage(exception)}`;
  } finally {
    if (!quiet) loading.value = false;
    schedulePoll();
  }
}

async function requestExport() {
  if (requesting.value || hasGenerating.value) return;
  requesting.value = true;
  error.value = '';
  try {
    await exportCall('request_customer_exit_export');
    await loadExports(true);
  } catch (exception) {
    error.value = `生成退出包失败：${errorMessage(exception)}`;
  } finally {
    requesting.value = false;
  }
}

async function deleteExport(name) {
  if (!window.confirm('删除服务器上的退出包文件？企业业务数据不会被删除。')) return;
  deleting.value = name;
  error.value = '';
  try {
    await exportCall('delete_customer_exit_export', {name});
    await loadExports(true);
  } catch (exception) {
    error.value = `删除退出包失败：${errorMessage(exception)}`;
  } finally {
    deleting.value = '';
  }
}

onMounted(loadExports);
onBeforeUnmount(() => {disposed = true; clearTimeout(pollTimer);});
</script>

<template>
  <section class="exit-page" aria-labelledby="exit-title">
    <div class="exit-intro">
      <div>
        <p class="exit-kicker">数据交接</p>
        <h1 id="exit-title">企业数据退出包</h1>
        <p>将当前企业的业务记录、会计数据和附件整理为可核验的 ZIP 文件，便于留档或迁移</p>
      </div>
      <button class="exit-primary" :disabled="requesting || hasGenerating" @click="requestExport">
        {{ hasGenerating ? '正在整理数据' : requesting ? '正在提交' : '生成新的退出包' }}
      </button>
    </div>

    <p v-if="error" class="exit-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="exit-state" aria-live="polite">正在读取退出记录</p>
    <div v-else-if="!exports.length" class="exit-empty">
      <strong>尚未生成过退出包</strong>
      <span>生成后可在这里查看完整性摘要、下载或删除服务器文件</span>
    </div>

    <ol v-else class="exit-list" aria-label="退出包记录">
      <li v-for="item in exports" :key="item.name" class="exit-record">
        <div class="exit-record-main">
          <div class="exit-record-heading">
            <div>
              <span class="exit-status" :data-status="item.status">{{ statusLabels[item.status] || item.status }}</span>
              <strong>{{ item.name }}</strong>
            </div>
            <time>{{ item.completed_at || item.requested_at }}</time>
          </div>

          <dl v-if="item.status === 'Ready'" class="exit-metrics">
            <div><dt>业务数据</dt><dd>{{ item.row_count }} 条</dd></div>
            <div><dt>附件</dt><dd>{{ item.attachment_count }} 个</dd></div>
            <div><dt>文件大小</dt><dd>{{ formatSize(item.package_size) }}</dd></div>
          </dl>
          <p v-else-if="item.status === 'Generating'" class="exit-state">系统正在逐项整理并校验文件，可离开本页稍后查看</p>
          <p v-else-if="item.status === 'Failed'" class="exit-error">{{ failureLabels[item.failure_code] || '生成失败，请重新生成' }}</p>
          <p v-else class="exit-state">服务器上的退出包文件已删除，记录保留用于审计</p>
        </div>

        <aside v-if="item.status === 'Ready'" class="exit-seal" aria-label="文件完整性">
          <span>SHA-256 完整性封印</span>
          <code :title="item.package_sha256">{{ shortHash(item.package_sha256) }}</code>
          <div class="exit-actions">
            <a :href="downloadHref(item.name)">下载退出包</a>
            <button :disabled="deleting === item.name" @click="deleteExport(item.name)">
              {{ deleting === item.name ? '正在删除' : '删除文件' }}
            </button>
          </div>
        </aside>
      </li>
    </ol>

    <p class="exit-note">退出包只包含当前企业站点的数据。下载后请妥善保存，系统不会自动发送给第三方</p>
  </section>
</template>

<style scoped>
.exit-page { --exit-ink: #182329; --exit-muted: #607078; --exit-line: #d9e0e3; --exit-paper: #fbfcfc; --exit-wash: #f1f5f5; --exit-accent: #155775; display: grid; gap: 24px; color: var(--exit-ink); background: var(--exit-paper); }
.exit-intro { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 32px; align-items: end; padding-bottom: 24px; border-bottom: 1px solid var(--exit-line); }
.exit-kicker { margin: 0 0 8px; color: var(--exit-accent); font-size: 13px; font-weight: 500; }
.exit-intro h1 { margin: 0 0 8px; font-size: 28px; line-height: 1.3; font-weight: 500; }
.exit-intro p:not(.exit-kicker) { max-width: 680px; margin: 0; color: var(--exit-muted); line-height: 1.7; }
.exit-primary, .exit-actions a, .exit-actions button { min-height: 44px; margin: 0; padding: 10px 16px; border: 1px solid var(--exit-accent); border-radius: 8px; font-weight: 500; text-decoration: none; }
.exit-primary, .exit-actions a { color: #fff; background: var(--exit-accent); }
.exit-primary:hover, .exit-actions a:hover { color: #fff; background: #0f4863; }
.exit-primary:disabled, .exit-actions button:disabled { opacity: .55; cursor: default; }
.exit-empty { display: grid; gap: 8px; min-height: 144px; place-content: center; padding: 24px; border: 1px dashed #aab8be; border-radius: 12px; text-align: center; }
.exit-empty strong { font-size: 18px; font-weight: 500; }
.exit-empty span, .exit-state, .exit-note { color: var(--exit-muted); }
.exit-list { display: grid; gap: 16px; padding: 0; margin: 0; list-style: none; }
.exit-record { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 24px; padding: 24px; border: 1px solid var(--exit-line); border-radius: 12px; background: #fff; }
.exit-record-main { display: grid; gap: 20px; min-width: 0; }
.exit-record-heading { display: flex; gap: 16px; align-items: start; justify-content: space-between; }
.exit-record-heading > div { display: grid; gap: 8px; }
.exit-record-heading strong { font-size: 18px; font-weight: 500; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.exit-record-heading time { color: var(--exit-muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.exit-status { width: fit-content; color: var(--exit-accent); font-size: 13px; font-weight: 500; }
.exit-status[data-status="Failed"] { color: #9b2c27; }
.exit-status[data-status="Deleted"] { color: var(--exit-muted); }
.exit-metrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 0; }
.exit-metrics div { display: grid; gap: 4px; }
.exit-metrics dt { color: var(--exit-muted); font-size: 13px; }
.exit-metrics dd { margin: 0; font-size: 20px; font-weight: 500; font-variant-numeric: tabular-nums; }
.exit-seal { display: grid; align-content: center; gap: 12px; padding: 20px; border-radius: 8px; background: var(--exit-wash); }
.exit-seal > span { color: var(--exit-muted); font-size: 13px; }
.exit-seal code { color: var(--exit-accent); font-size: 18px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.exit-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
.exit-actions button { margin: 0; color: var(--exit-accent); background: transparent; }
.exit-error { margin: 0; color: #9b2c27; }
.exit-note { margin: 0; font-size: 13px; line-height: 1.6; }
@media (max-width: 768px) {
  .exit-page { gap: 20px; }
  .exit-intro, .exit-record { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  .exit-primary { width: 100%; }
  .exit-record { padding: 20px; }
  .exit-record-heading { display: grid; }
  .exit-metrics { grid-template-columns: minmax(0, 1fr); }
  .exit-actions { display: grid; }
  .exit-actions a, .exit-actions button { display: grid; place-items: center; width: 100%; }
}
</style>
