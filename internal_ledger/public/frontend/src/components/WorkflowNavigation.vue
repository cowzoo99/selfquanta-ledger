<script setup>
import {computed} from 'vue';
const props = defineProps({currentStage: Number, batch: String, counts: Object});
const scoped = route => route + (props.batch ? `/${encodeURIComponent(props.batch)}` : '');
const steps = computed(() => [
  {number: 1, label: '导入识别', href: '/app/ledger-workbench/import'},
  {number: 2, label: '重复复核', href: scoped('/app/ledger-workbench/duplicates/疑似重复'), count: props.counts?.duplicates},
  {number: 3, label: '自动分类', href: scoped('/app/ledger-workbench/review/待分类'), count: props.counts?.classification},
  {number: 4, label: '凭证草稿', href: scoped('/app/ledger-workbench/review/待审核'), count: props.counts?.drafts},
  {number: 5, label: '审核过账', href: scoped('/app/ledger-workbench/review/待过账'), count: props.counts?.posting},
]);
const current = computed(() => steps.value.find(step => step.number === props.currentStage));
const previous = computed(() => steps.value[props.currentStage - 2]);
const next = computed(() => steps.value[props.currentStage]);
const currentYear = new Date().getFullYear();
const generalLedgerHref = `/app/query-report/General%20Ledger?from_date=${currentYear}-01-01&to_date=${currentYear}-12-31`;
</script>

<template>
  <section class="ledger-workflow" aria-label="本批智能记账进度">
    <p><strong>本批智能记账进度</strong> · 第 {{ currentStage }} / 5 步 · {{ current?.label }}</p>
    <ol>
      <li v-for="step in steps" :key="step.number">
        <a :href="step.href" :aria-current="step.number === currentStage ? 'step' : undefined">
          <span>{{ step.number }}</span>{{ step.label }}<small v-if="step.count !== undefined">{{ step.count }} 笔</small>
        </a>
      </li>
    </ol>
  </section>
  <nav class="ledger-step-footer" aria-label="智能记账上一步下一步">
    <a v-if="previous" :href="previous.href">← 上一步：{{ previous.label }}</a><span v-else>已在第一步</span>
    <strong>第 {{ currentStage }} / 5 步</strong>
    <a v-if="next" :href="next.href">下一步：{{ next.label }} →</a>
    <a v-else :href="generalLedgerHref">查看标准总账总览 →</a>
  </nav>
</template>
