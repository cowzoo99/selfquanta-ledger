<script setup>
import {computed} from 'vue';
import {MODULES} from '../moduleCatalog';

const props = defineProps({moduleKey: {type: String, required: true}});
const module = computed(() => MODULES.find(entry => entry.key === props.moduleKey));
</script>

<template>
  <section v-if="module" class="ledger-module-hub" :aria-labelledby="`module-${module.key}`">
    <div class="ledger-hero">
      <div>
        <p class="ledger-module-icon" aria-hidden="true">{{ module.icon }}</p>
        <h2 :id="`module-${module.key}`">{{ module.label }}</h2>
        <p>{{ module.description }}</p>
      </div>
      <a class="ledger-primary-action" :href="module.route">
        <strong>进入{{ module.label }}工作区</strong>
        <span>使用标准业务数据和审批流程</span>
      </a>
    </div>
    <h3>常用任务</h3>
    <div class="ledger-cards">
      <a v-for="task in module.tasks" :key="task.route" class="ledger-card ledger-module-card" :href="task.route">
        <strong>{{ task.label }}</strong><span>{{ task.description }}</span><span>打开</span>
      </a>
    </div>
  </section>
</template>
