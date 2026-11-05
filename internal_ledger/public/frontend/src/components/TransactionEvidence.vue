<script setup>
import {ref, computed, watch, onBeforeUnmount} from 'vue';
import {evidenceCall, errorMessage} from '../api';
const props = defineProps({transaction: {type: String, required: true}, canWrite: Boolean, disabled: Boolean});
const emit = defineEmits(['saved']);
const editor = ref(null), displayTitle = ref(''), note = ref(''), renderedNote = ref(''), attachments = ref([]);
const noteVersion = ref('');
const busy = ref(false), loading = ref(false), error = ref(''), notice = ref('');
const ready = ref(false);
const canEdit = computed(() => props.canWrite && !props.disabled && ready.value && !busy.value && !loading.value);
const mimeTypes = {png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', pdf: 'application/pdf',
  doc: 'application/msword', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xls: 'application/vnd.ms-excel', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  txt: 'text/plain', csv: 'text/csv'};
let generation = 0;
function apply(data) {
  displayTitle.value = data.display_title || '';
  note.value = data.note_html; renderedNote.value = data.note_html; attachments.value = data.attachments; noteVersion.value = data.note_version;
  if (editor.value) editor.value.innerHTML = data.note_html;
}
async function load() {
  const request = ++generation;
  displayTitle.value = ''; note.value = ''; renderedNote.value = ''; attachments.value = []; noteVersion.value = ''; error.value = ''; notice.value = ''; busy.value = false;
  loading.value = true; ready.value = false;
  try {
    const data = await evidenceCall('get_evidence', {tx_name: props.transaction});
    if (request === generation) {apply(data); ready.value = true;}
  } catch (e) { if (request === generation) error.value = errorMessage(e); }
  finally { if (request === generation) loading.value = false; }
}
watch(() => props.transaction, load, {immediate: true});
onBeforeUnmount(() => {generation++;});
function input(event) { note.value = event.target.innerHTML; notice.value = ''; }
function format(kind) {
  if (!canEdit.value) return;
  const selection = window.getSelection();
  if (!selection.rangeCount || !editor.value.contains(selection.anchorNode) || !editor.value.contains(selection.focusNode)) return;
  const range = selection.getRangeAt(0), content = range.extractContents();
  const wrapper = document.createElement(kind === 'bold' ? 'strong' : 'ul');
  if (kind === 'bold') wrapper.append(content);
  else { const li = document.createElement('li'); li.append(content); wrapper.append(li); }
  range.insertNode(wrapper); range.selectNodeContents(wrapper); selection.removeAllRanges(); selection.addRange(range);
  note.value = editor.value.innerHTML;
}
function table() {
  if (!canEdit.value) return;
  editor.value.focus();
  const selection = window.getSelection();
  const range = selection.rangeCount && editor.value.contains(selection.anchorNode) && editor.value.contains(selection.focusNode)
    ? selection.getRangeAt(0) : document.createRange();
  if (!editor.value.contains(range.commonAncestorContainer)) { range.selectNodeContents(editor.value); range.collapse(false); }
  const element = document.createElement('table'), body = document.createElement('tbody');
  for (let row = 0; row < 2; row++) {
    const tr = document.createElement('tr');
    for (let column = 0; column < 2; column++) {const td = document.createElement('td'); td.append(document.createElement('br')); tr.append(td);}
    body.append(tr);
  }
  element.append(body); range.deleteContents(); range.insertNode(element);
  range.selectNodeContents(body.firstChild.firstChild); range.collapse(true);
  selection.removeAllRanges(); selection.addRange(range); note.value = editor.value.innerHTML;
}
async function write(method, args) {
  if (!canEdit.value) return;
  const request = ++generation;
  busy.value = true; error.value = ''; notice.value = '';
  try {
    const data = await evidenceCall(method, {tx_name: props.transaction, ...args});
    if (request === generation) {
      if (method === 'save_evidence') {apply(data); emit('saved', data);}
      else attachments.value = data.attachments;
      notice.value = '已保存';
    }
  } catch (e) { if (request === generation) error.value = errorMessage(e); }
  finally { if (request === generation) busy.value = false; }
}
function save() { return write('save_evidence', {display_title: displayTitle.value,
  note_html: note.value, expected_version: noteVersion.value}); }
function remove(attachment) { return write('delete_evidence_file', {attachment_name: attachment.name}); }
async function upload(event) {
  const file = event.target.files?.[0];
  if (!file || !canEdit.value) return;
  const request = generation;
  const warnings = [];
  if (!/\.(png|jpe?g|webp|pdf|docx?|xlsx?|txt|csv)$/i.test(file.name)) warnings.push('不支持的附件扩展名');
  if (file.size > 10 * 1024 * 1024) warnings.push('附件超过 10 MiB');
  if (attachments.value.length >= 10) warnings.push('每笔流水最多 10 个附件');
  notice.value = warnings.length ? warnings.join('；') + '。服务端将最终校验。' : '';
  // Browser checks are advisory; the evidence API validates content, type, size and count.
  busy.value = true; error.value = '';
  try {
    const content = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split('base64,')[1]);
      reader.onerror = () => reject(new Error('读取附件失败'));
      reader.readAsDataURL(file);
    });
    if (request !== generation) return;
    const data = await evidenceCall('upload_evidence', {tx_name: props.transaction, filename: file.name,
      content_base64: content, mime_type: mimeTypes[file.name.split('.').pop().toLowerCase()] || file.type});
    if (request === generation) {attachments.value = data.attachments; notice.value = '附件已上传';}
  } catch (e) { if (request === generation) error.value = errorMessage(e); }
  finally { if (request === generation) busy.value = false; event.target.value = ''; }
}
</script>

<template>
  <section class="ledger-evidence" aria-label="备注与附件">
    <h3>备注与附件</h3>
    <p v-if="loading" role="status">正在载入备注与附件…</p>
    <p v-if="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <button v-if="!ready && !loading && error" type="button" @click="load">重试</button>
    <label v-if="ready && canWrite">展示标题
      <input v-model="displayTitle" type="text" maxlength="140" :readonly="!canEdit" placeholder="可修改列表中显示的标题">
    </label>
    <p v-if="ready && !canWrite">展示标题：{{ displayTitle }}</p>
    <div v-if="canWrite && ready" role="toolbar" aria-label="备注格式">
      <button type="button" :disabled="!canEdit" @mousedown.prevent @click="format('bold')">加粗</button>
      <button type="button" :disabled="!canEdit" @mousedown.prevent @click="format('list')">项目列表</button>
      <button type="button" :disabled="!canEdit" @mousedown.prevent @click="table">插入 2×2 表格</button>
    </div>
    <div v-if="ready && !canWrite" class="ledger-evidence-editor" v-html="renderedNote" />
    <div v-if="ready && canWrite" ref="editor" class="ledger-evidence-editor" role="textbox" aria-label="流水备注" aria-multiline="true"
      :aria-readonly="!canEdit" :contenteditable="canEdit" v-html="renderedNote" @input="input" />
    <template v-if="canWrite">
      <button type="button" :disabled="!canEdit" @click="save">保存标题和备注</button>
      <label>上传附件<input type="file" accept=".png,.jpg,.jpeg,.webp,.pdf,.doc,.docx,.xls,.xlsx,.txt,.csv" :disabled="!canEdit" @change="upload"></label>
      <p>每笔最多 10 个附件，每个不超过 10 MiB；支持图片、PDF、Word、Excel、TXT、CSV。服务端最终校验文件。</p>
    </template>
    <ul class="ledger-evidence-attachments">
      <li v-for="attachment in attachments" :key="attachment.name">
        <template v-if="attachment.is_image"><p>图片预览</p><img :src="attachment.file_url" :alt="attachment.file_name" class="ledger-evidence-preview"></template>
        <a :href="attachment.file_url" download>{{ attachment.file_name }} · 下载</a>
        <button v-if="canWrite" type="button" :disabled="!canEdit" @click="remove(attachment)">删除附件</button>
      </li>
    </ul>
  </section>
</template>
