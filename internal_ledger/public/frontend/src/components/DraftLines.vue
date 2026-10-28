<script setup>
import {ref, watch} from 'vue';
const props = defineProps({lines: Array, editable: Boolean, company: String});
const accounts = ref([]);
watch(() => props.company, async company => {
  if (!company || !props.editable) return;
  try {
    const response = await frappe.call({method: 'frappe.client.get_list', args: {
      doctype: 'Account', fields: ['name'], filters: {company, is_group: 0, disabled: 0}, limit_page_length: 0,
    }});
    accounts.value = response.message;
  } catch (_) { accounts.value = []; }
}, {immediate: true});
</script>

<template>
  <div class="ledger-table">
    <table>
      <caption>凭证借贷分录（使用科目完整名称）</caption>
      <thead><tr><th>科目</th><th>借方</th><th>贷方</th><th v-if="editable">操作</th></tr></thead>
      <tbody><tr v-for="(line, index) in lines" :key="index">
        <td><input v-if="editable" v-model="line.account" :aria-label="`第 ${index + 1} 行科目`" list="ledger-accounts" required><span v-else>{{ line.account }}</span></td>
        <td><input v-if="editable" v-model="line.debit" :aria-label="`第 ${index + 1} 行借方`" type="number" min="0" step="0.000000001"><span v-else>{{ line.debit }}</span></td>
        <td><input v-if="editable" v-model="line.credit" :aria-label="`第 ${index + 1} 行贷方`" type="number" min="0" step="0.000000001"><span v-else>{{ line.credit }}</span></td>
        <td v-if="editable"><button type="button" :aria-label="`删除第 ${index + 1} 行`" @click="lines.splice(index, 1)">删除</button></td>
      </tr></tbody>
    </table>
    <datalist id="ledger-accounts"><option v-for="account in accounts" :key="account.name" :value="account.name" /></datalist>
    <button v-if="editable" type="button" @click="lines.push({account: '', debit: '0', credit: '0'})">添加分录</button>
    <p v-if="!lines?.length">暂无分录。</p>
  </div>
</template>
