<template>
  <section class="release-panel" data-testid="release-board" data-release-entry="result_deliver">
    <div class="release-head">
      <div>
        <h3>发布入口 · 领取可交付批次</h3>
        <p>只有同一个更正回执下已闭合且契约校验通过的唯一候选版本能被领取；未受理完的修复产物不会进入投递。</p>
      </div>
      <button class="btn ghost" type="button" data-testid="refresh-release-board" @click="refresh">刷新</button>
    </div>

    <p v-if="message" class="flow-message" data-testid="release-message" role="status">{{ message }}</p>
    <p v-if="error" class="error-text" data-testid="release-error" role="alert">{{ error }}</p>

    <div v-if="claimableDeliverables.length" class="claim-bar">
      <label>
        <span>可交付回执 / 批次版本</span>
        <select v-model="selectedReceiptNo" data-testid="deliverable-select">
          <option v-for="item in claimableDeliverables" :key="item.回执编号" :value="item.回执编号">
            {{ item.回执编号 }} · {{ item.批次记录编号 }} → {{ item.候选批次编号 }} · {{ item.校验状态 }}
          </option>
        </select>
      </label>
      <button class="btn primary" type="button" data-testid="claim-deliverable" @click="claimSelected">
        领取可交付批次
      </button>
    </div>
    <p v-else-if="releasedDeliverables.length" class="muted" data-testid="all-deliverables-claimed">
      当前复验通过的候选均已由发布入口领取；可在下方或“更正回执”中查询已确认目标回执。
    </p>
    <p v-else class="muted" data-testid="no-deliverable">暂无完成校验的可交付批次。</p>

    <article
      v-for="item in visibleCards"
      :key="item.回执编号"
      class="deliverable-card"
      :data-testid="`deliverable-${item.回执编号}`"
      :data-receipt-no="item.回执编号"
      :data-adopted-version="item.候选批次编号"
    >
      <header>
        <strong>{{ item.回执编号 }} · {{ item.候选批次编号 }}</strong>
        <span class="badge">{{ item.受理状态 }}</span>
      </header>
      <p>原批：{{ item.批次记录编号 }}；契约：{{ item.契约编号 }}（{{ item.校验状态 }}）</p>
      <p :data-testid="testIdFor('formal-candidate', item.回执编号)">已通过复验的正式候选：<b>{{ item.候选批次编号 }}</b></p>
      <p :data-testid="testIdFor('adopted-version', item.回执编号)">本次只采用这一个完成校验的版本；采用版本数：{{ item.采用版本数 ?? 1 }}</p>
      <p v-if="item.投递记录编号" :data-testid="testIdFor('confirmed-delivery-receipt', item.回执编号)">
        已确认目标回执：{{ item.投递记录编号 }}；目标：{{ item.目标位置 }}；领取人：{{ item.发布领取人 }}
      </p>
      <div class="button-row">
        <button
          v-if="item.受理状态 === '复验通过'"
          class="btn primary"
          type="button"
          :data-testid="`claim-deliverable-${item.回执编号}`"
          @click="claim(item.回执编号)"
        >
          领取此交付版本
        </button>
        <span v-else class="badge" data-testid="already-claimed">已领取并确认</span>
        <RouterLink
          class="btn ghost link-btn"
          :to="`/parse_batch?receipt=${encodeURIComponent(item.回执编号)}`"
          :data-testid="`open-receipt-from-release-${item.回执编号}`"
        >
          查确认回执
        </RouterLink>
      </div>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  claimDeliverable,
  getCorrectionBoard,
  type CorrectionBoard,
} from '@/api/correction-flow'

const route = useRoute()
const board = ref<CorrectionBoard | null>(null)
const selectedReceiptNo = ref(String(route.query.receipt || ''))
const message = ref('')
const error = ref('')
let timer: ReturnType<typeof setInterval> | undefined

const claimableDeliverables = computed(() => board.value?.deliverables ?? [])
const releasedDeliverables = computed(() => board.value?.released ?? [])
const visibleCards = computed(() => {
  const selected = selectedReceiptNo.value || String(route.query.receipt || '')
  return [
    ...claimableDeliverables.value,
    ...releasedDeliverables.value.filter((item) => item.回执编号 === selected),
  ]
})

function testIdFor(base: string, receiptNo: string): string {
  return receiptNo === selectedReceiptNo.value ? base : `${base}-${receiptNo}`
}

async function refresh() {
  try {
    board.value = await getCorrectionBoard()
    if (!selectedReceiptNo.value && claimableDeliverables.value.length === 1) {
      selectedReceiptNo.value = claimableDeliverables.value[0].回执编号
    }
    if (route.query.receipt) selectedReceiptNo.value = String(route.query.receipt)
    error.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : '发布看板读取失败'
  }
}

async function claim(receiptNo: string) {
  error.value = ''
  message.value = ''
  const result = await claimDeliverable({ receipt_no: receiptNo, owner: 'release-duty' })
  if (!result.ok) {
    error.value = result.message
    return
  }
  message.value = result.message
  selectedReceiptNo.value = receiptNo
  window.dispatchEvent(new CustomEvent('correction-flow:changed'))
  await refresh()
}

function claimSelected() {
  if (!selectedReceiptNo.value) {
    error.value = '请先选择一个已完成校验的可交付批次'
    return
  }
  void claim(selectedReceiptNo.value)
}

watch(() => route.query.receipt, (value) => {
  if (typeof value === 'string') selectedReceiptNo.value = value
})

onMounted(() => {
  void refresh()
  window.addEventListener('correction-flow:changed', refresh)
  timer = setInterval(refresh, 1500)
})

onBeforeUnmount(() => {
  window.removeEventListener('correction-flow:changed', refresh)
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.release-panel { margin: 12px 0; padding: 14px; background: #fff; border: 1px solid var(--border); border-radius: 10px; }
.release-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.release-head h3 { margin: 0 0 4px; font-size: 16px; }
.release-head p { margin: 0; color: var(--muted); font-size: 13px; }
.claim-bar { display: flex; gap: 10px; align-items: flex-end; margin: 12px 0; padding: 12px; background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; }
.claim-bar label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); flex: 1; }
.claim-bar select { padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; }
.deliverable-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-top: 10px; background: #fbfdff; }
.deliverable-card header { display: flex; justify-content: space-between; gap: 10px; }
.deliverable-card p { font-size: 13px; margin: 8px 0; }
.badge { background: #dcfce7; color: #166534; border-radius: 999px; padding: 3px 10px; font-size: 12px; }
.button-row { display: flex; gap: 8px; flex-wrap: wrap; }
.link-btn { text-decoration: none; font-size: 13px; }
.muted { color: var(--muted); font-size: 13px; }
.flow-message { color: #067647; background: #ecfdf3; border: 1px solid #abefc6; padding: 8px 10px; border-radius: 6px; font-size: 13px; }
</style>
