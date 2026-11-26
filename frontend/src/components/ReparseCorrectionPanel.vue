<template>
  <section class="flow-panel" data-testid="reparse-correction-panel" data-correction-panel="reparse_task">
    <div class="flow-head">
      <div>
        <h3>重解析接手台 · 更正结论与原驳回凭据</h3>
        <p>这里只展示和更正回执关联的真实接手任务；继续核验会把同一份回执推进到唯一正式候选版本。</p>
      </div>
      <button class="btn ghost" type="button" data-testid="refresh-reparse-board" @click="refresh">刷新</button>
    </div>

    <p v-if="message" class="flow-message" data-testid="reparse-message" role="status">{{ message }}</p>
    <p v-if="error" class="error-text" data-testid="reparse-error" role="alert">{{ error }}</p>

    <div v-if="queryTask && !activeReceipt" class="receipt-card" data-testid="task-context" :data-task-no="queryTask">
      <h3>任务 {{ queryTask }}</h3>
      <p>该入口按真实重解析单读取更正复核结论；若任务尚未领取，请先在下方任务行点击“领取任务”。</p>
      <button class="btn primary" type="button" data-testid="continue-verification-by-task" @click="verifyByTask">
        读取更正结论并继续核验
      </button>
    </div>

    <article
      v-for="receipt in visibleReceipts"
      :key="receipt.回执编号"
      class="receipt-card"
      data-testid="reparse-receipt"
      :data-receipt-no="receipt.回执编号"
      :data-task-no="receipt.重解析编号"
    >
      <header>
        <h3>更正受理回执 {{ receipt.回执编号 }}</h3>
        <span class="badge">{{ receipt.受理状态 }}</span>
      </header>

      <div class="evidence-grid">
        <div data-testid="original-rejection-evidence">
          <strong>原驳回凭据</strong>
          <p>样本：{{ receipt.原样本编号 }}</p>
          <p>{{ receipt.原驳回结论 }}</p>
          <p class="muted">来源依据：{{ sourceBasis(receipt) }}</p>
          <p class="muted">原复核人：{{ receipt.原复核人 }}</p>
        </div>
        <div data-testid="correction-conclusion">
          <strong>更正复核结论</strong>
          <p>新样本：{{ receipt.更正样本编号 }}；结论：{{ receipt.修订后结论 }}</p>
          <p>原值：{{ receipt.样本原值 || '—' }}</p>
          <p>更正后：{{ receipt.更正后值 || '—' }}</p>
        </div>
        <div data-testid="handled-scope">
          <strong>真实接手任务与已处理范围</strong>
          <p>任务：{{ receipt.重解析编号 || '未转交' }}；接手人：{{ receipt.接手人 || '—' }}</p>
          <p>范围：{{ taskOf(receipt)?.已处理批次范围 || taskOf(receipt)?.受影响批次 || receipt.关联投递批次 }}</p>
          <p class="muted">原始重解析原因保持不变：{{ taskOf(receipt)?.重解析原因 || '—' }}</p>
        </div>
      </div>

      <ol class="timeline-list">
        <li v-for="(event, index) in receipt.流转记录 ?? []" :key="index">
          <b>{{ event.状态 }}</b> · {{ event.时间 }} · {{ event.说明 }}
        </li>
      </ol>

      <div class="button-row">
        <RouterLink
          class="btn ghost link-btn"
          :to="`/parse_batch?receipt=${encodeURIComponent(receipt.回执编号)}`"
          :data-testid="`open-batch-receipt-${receipt.回执编号}`"
        >
          打开原批回执 {{ receipt.批次记录编号 }}
        </RouterLink>
        <button
          v-if="receipt.受理状态 !== '复验通过' && receipt.受理状态 !== '已发布'"
          class="btn primary"
          type="button"
          :data-testid="`continue-verification-${receipt.回执编号}`"
          @click="verifyReceipt(receipt)"
        >
          继续核验
        </button>
        <RouterLink
          v-if="receipt.候选批次编号"
          class="btn primary link-btn"
          :to="`/result_deliver?receipt=${encodeURIComponent(receipt.回执编号)}`"
          data-testid="go-release-from-reparse"
        >
          核验完成，去发布入口领取 {{ receipt.候选批次编号 }}
        </RouterLink>
      </div>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  getCorrectionBoard,
  verifyCorrection,
  type CorrectionBoard,
  type CorrectionReceipt,
} from '@/api/correction-flow'

const route = useRoute()
const board = ref<CorrectionBoard | null>(null)
const message = ref('')
const error = ref('')
let timer: ReturnType<typeof setInterval> | undefined

const queryReceipt = computed(() => String(route.query.receipt || ''))
const queryTask = computed(() => String(route.query.task || ''))

const visibleReceipts = computed(() => {
  const receipts = board.value?.receipts ?? []
  if (queryReceipt.value) return receipts.filter((item) => item.回执编号 === queryReceipt.value)
  if (queryTask.value) return receipts.filter((item) => item.重解析编号 === queryTask.value)
  return receipts.filter((item) => item.重解析编号)
})

const activeReceipt = computed(() => visibleReceipts.value[0] ?? null)

function taskOf(receipt: CorrectionReceipt) {
  return board.value?.tasks.find((task) => task.重解析编号 === receipt.重解析编号) ?? null
}

function sourceBasis(receipt: CorrectionReceipt): string {
  const snapshot = receipt.原驳回快照 as { 来源依据?: string } | undefined
  return snapshot?.来源依据 || `原始抽样复核记录 ${receipt.原样本编号} / ${receipt.原复核人}`
}

async function refresh() {
  try {
    board.value = await getCorrectionBoard({
      receipt_no: queryReceipt.value,
      task_no: queryTask.value,
    })
    // 带筛选时 board 只返回筛选回执；为了展示关联任务，再拉一次全量任务由后端 board 已尽量返回。
    if ((queryReceipt.value || queryTask.value) && board.value.receipts.length) {
      const full = await getCorrectionBoard()
      board.value.tasks = full.tasks
    }
    error.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : '重解析协调状态读取失败'
  }
}

async function verifyReceipt(receipt: CorrectionReceipt) {
  error.value = ''
  message.value = ''
  const result = await verifyCorrection({
    receipt_no: receipt.回执编号,
    task_no: receipt.重解析编号 || queryTask.value,
  })
  if (!result.ok) {
    error.value = result.message
    return
  }
  message.value = result.message
  window.dispatchEvent(new CustomEvent('correction-flow:changed'))
  await refresh()
}

async function verifyByTask() {
  error.value = ''
  const result = await verifyCorrection({ task_no: queryTask.value })
  if (!result.ok) {
    error.value = result.message
    return
  }
  message.value = result.message
  window.dispatchEvent(new CustomEvent('correction-flow:changed'))
  await refresh()
}

function handleChanged() {
  void refresh()
}

watch([queryReceipt, queryTask], refresh)

onMounted(() => {
  void refresh()
  window.addEventListener('correction-flow:changed', handleChanged)
  timer = setInterval(refresh, 1500)
})

onBeforeUnmount(() => {
  window.removeEventListener('correction-flow:changed', handleChanged)
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.flow-panel { margin: 12px 0; padding: 14px; background: #fff; border: 1px solid var(--border); border-radius: 10px; }
.flow-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.flow-head h3 { margin: 0 0 4px; font-size: 16px; }
.flow-head p { margin: 0; color: var(--muted); font-size: 13px; }
.receipt-card { margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: #f8fafc; }
.receipt-card header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.badge { background: #dbeafe; color: #1d4ed8; border-radius: 999px; padding: 3px 10px; font-size: 12px; }
.evidence-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin: 10px 0; }
.evidence-grid > div { background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 10px; font-size: 13px; }
.evidence-grid p { margin: 5px 0; }
.muted { color: var(--muted); font-size: 12px; }
.timeline-list { margin: 10px 0; padding-left: 18px; font-size: 12px; color: #334155; }
.button-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.link-btn { text-decoration: none; font-size: 13px; }
.flow-message { color: #067647; background: #ecfdf3; border: 1px solid #abefc6; padding: 8px 10px; border-radius: 6px; font-size: 13px; }
</style>
