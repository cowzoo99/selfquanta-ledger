<script setup>
import {ref, watch} from 'vue';
import {memberCall, errorMessage} from '../api';
const props = defineProps({active: Boolean});
const data = ref(null), loading = ref(false), saving = ref(false), error = ref(''), result = ref('');
const createDialog = ref(null), resetDialog = ref(null);
const form = ref({first_name: '', email: '', temporary_password: '', confirm_password: ''});
const reset = ref({user: '', temporary_password: '', confirm_password: ''});
function clearPasswords() {
  form.value.temporary_password = ''; form.value.confirm_password = '';
  reset.value.temporary_password = ''; reset.value.confirm_password = '';
}
async function load() {
  if (!props.active) return;
  loading.value = true; error.value = '';
  try { data.value = await memberCall('list_enterprise_members'); }
  catch (e) { error.value = errorMessage(e); }
  finally { loading.value = false; clearPasswords(); }
}
watch(() => props.active, active => { if (active) load(); }, {immediate: true});
function openCreate() {
  form.value = {first_name: '', email: '', temporary_password: '', confirm_password: ''};
  error.value = ''; result.value = ''; createDialog.value.showModal();
}
function openReset(row) {
  reset.value = {user: row.email, temporary_password: '', confirm_password: ''};
  error.value = ''; result.value = ''; resetDialog.value.showModal();
}
async function create() {
  saving.value = true; error.value = ''; result.value = '';
  try {
    await memberCall('create_enterprise_member', form.value);
    createDialog.value.close(); result.value = '成员已创建，请安全交付临时密码；首次登录需修改密码。';
    await load();
  } catch (e) { error.value = errorMessage(e); }
  finally { clearPasswords(); saving.value = false; }
}
async function toggle(row) {
  if (row.enabled && !window.confirm(`确认停用 ${row.email}？该成员的现有会话将失效。`)) return;
  saving.value = true; error.value = ''; result.value = '';
  try {
    await memberCall('set_enterprise_member_enabled', {user: row.email, enabled: row.enabled ? 0 : 1});
    result.value = row.enabled ? '成员已停用。' : '成员已启用。'; await load();
  } catch (e) { error.value = errorMessage(e); }
  finally { clearPasswords(); saving.value = false; }
}
async function resetPassword() {
  saving.value = true; error.value = ''; result.value = '';
  try {
    await memberCall('reset_enterprise_member_password', reset.value);
    resetDialog.value.close(); result.value = '临时密码已重置，原会话已失效；成员下次登录需修改密码。';
  } catch (e) { error.value = errorMessage(e); }
  finally { clearPasswords(); saving.value = false; }
}
</script>

<template>
  <section class="enterprise-members" aria-labelledby="members-title">
    <h2 id="members-title">企业成员</h2>
    <p v-if="data">{{ data.company }} · 已启用 {{ data.enabled_count }} 人 · 已停用 {{ data.disabled_count }} 人</p>
    <button :disabled="saving || loading" @click="openCreate">新增成员</button>
    <button :disabled="saving || loading" @click="load">刷新成员</button>
    <p v-if="loading" role="status">正在加载成员…</p>
    <p v-if="error && !createDialog?.open && !resetDialog?.open" role="alert">{{ error }}</p>
    <p v-if="result" role="status">{{ result }}</p>
    <div v-if="data" class="ledger-table"><table>
      <caption>当前企业只读成员</caption>
      <thead><tr><th scope="col">姓名</th><th scope="col">登录邮箱</th><th scope="col">状态</th><th scope="col" class="member-date">创建时间</th><th scope="col" class="member-date">最后登录时间</th><th scope="col">操作</th></tr></thead>
      <tbody><tr v-for="row in data.members" :key="row.email">
        <td>{{ row.full_name }}</td><td>{{ row.email }}</td><td>{{ row.enabled ? '启用' : '停用' }}</td>
        <td class="member-date">{{ row.creation }}</td><td class="member-date">{{ row.last_login || '尚未登录' }}</td>
        <td><button :disabled="saving || loading" :aria-label="`${row.enabled ? '停用' : '启用'} ${row.email}`" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</button><button :disabled="saving || loading" :aria-label="`重置 ${row.email} 的临时密码`" @click="openReset(row)">重置密码</button></td>
      </tr></tbody>
    </table><p v-if="!data.members.length">当前企业还没有成员。</p></div>
    <dialog ref="createDialog" aria-labelledby="create-member-title" @close="clearPasswords" @cancel="clearPasswords">
      <h3 id="create-member-title">新增成员</h3>
      <form @submit.prevent="create"><fieldset :disabled="saving">
        <label for="member-name">姓名</label><input id="member-name" v-model="form.first_name" required autocomplete="name">
        <label for="member-email">登录邮箱</label><input id="member-email" v-model="form.email" type="email" required autocomplete="off">
        <label for="member-password">临时密码</label><input id="member-password" v-model="form.temporary_password" type="password" required autocomplete="new-password">
        <label for="member-confirm">确认密码</label><input id="member-confirm" v-model="form.confirm_password" type="password" required autocomplete="new-password">
        <p v-if="error" role="alert">{{ error }}</p>
        <button type="submit">{{ saving ? '正在保存…' : '创建成员' }}</button><button type="button" @click="createDialog.close()">取消</button>
      </fieldset></form>
    </dialog>
    <dialog ref="resetDialog" aria-labelledby="reset-member-title" @close="clearPasswords" @cancel="clearPasswords">
      <h3 id="reset-member-title">重置临时密码</h3>
      <form @submit.prevent="resetPassword"><fieldset :disabled="saving">
        <label for="reset-email">登录邮箱</label><input id="reset-email" :value="reset.user" type="email" readonly autocomplete="off">
        <label for="reset-password">临时密码</label><input id="reset-password" v-model="reset.temporary_password" type="password" required autocomplete="new-password">
        <label for="reset-confirm">确认密码</label><input id="reset-confirm" v-model="reset.confirm_password" type="password" required autocomplete="new-password">
        <p v-if="error" role="alert">{{ error }}</p>
        <button type="submit">{{ saving ? '正在保存…' : '重置密码' }}</button><button type="button" @click="resetDialog.close()">取消</button>
      </fieldset></form>
    </dialog>
  </section>
</template>

<style scoped>
.enterprise-members { min-width: 0; }
.enterprise-members button { min-height: 40px; }
.enterprise-members td { overflow-wrap: anywhere; }
.enterprise-members dialog { width: 420px; max-width: calc(100% - 32px); max-height: calc(100% - 32px); overflow: auto; box-sizing: border-box; }
.enterprise-members input { width: 100%; min-width: 0; box-sizing: border-box; }
@media (max-width: 800px) { .enterprise-members .member-date { display: none; } }
</style>
