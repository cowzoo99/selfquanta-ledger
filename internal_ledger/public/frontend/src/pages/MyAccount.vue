<script setup>
import {ref, watch} from 'vue';
import {memberCall, errorMessage} from '../api';

const props = defineProps({active: Boolean});
const account = ref(null), loading = ref(false), saving = ref(false), error = ref(''), result = ref('');
const form = ref({email: '', mobile_no: '', current_password: ''});

async function load() {
  if (!props.active) return;
  loading.value = true; error.value = ''; result.value = '';
  try {
    account.value = await memberCall('get_my_account');
    form.value = {email: account.value.email, mobile_no: account.value.mobile_no, current_password: ''};
  } catch (e) { error.value = errorMessage(e); }
  finally { loading.value = false; }
}

async function save() {
  saving.value = true; error.value = ''; result.value = '';
  try {
    account.value = await memberCall('update_my_account', form.value);
    form.value.current_password = '';
    result.value = '邮箱和手机号已保存。为保护账号安全，请重新登录。';
    window.setTimeout(() => window.location.assign('/login'), 1200);
  } catch (e) { error.value = errorMessage(e); form.value.current_password = ''; }
  finally { saving.value = false; }
}

watch(() => props.active, active => { if (active) load(); }, {immediate: true});
</script>

<template>
  <section class="my-account" aria-labelledby="my-account-title">
    <h2 id="my-account-title">我的账号</h2>
    <p>绑定后，可使用登录账号、邮箱或手机号配合同一个密码登录。</p>
    <p v-if="loading" role="status">正在加载账号…</p>
    <form v-else-if="account" @submit.prevent="save"><fieldset :disabled="saving">
      <label for="my-login-account">登录账号</label>
      <input id="my-login-account" :value="account.login_account || '旧邮箱账号'" readonly autocomplete="username">
      <label for="my-email">绑定邮箱</label>
      <input id="my-email" v-model="form.email" type="email" autocomplete="email" placeholder="例如 name@example.com">
      <label for="my-mobile">绑定手机号</label>
      <input id="my-mobile" v-model="form.mobile_no" type="tel" inputmode="tel" autocomplete="tel" placeholder="例如 13800138000">
      <small>内部使用版本暂不发送验证码；系统会检查邮箱和手机号是否已被其他账号绑定。</small>
      <label for="my-current-password">当前密码</label>
      <input id="my-current-password" v-model="form.current_password" type="password" required autocomplete="current-password">
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="result" role="status">{{ result }}</p>
      <button type="submit">{{ saving ? '正在保存…' : '保存并重新登录' }}</button>
    </fieldset></form>
  </section>
</template>

<style scoped>
.my-account form { max-width: 520px; }
.my-account input { width: 100%; box-sizing: border-box; }
.my-account small { color: #56636d; line-height: 1.5; }
</style>
