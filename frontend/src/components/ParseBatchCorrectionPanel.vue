<template>
  <section
    class="flow-panel"
    data-testid="parse-batch-correction"
    data-correction-panel="parse_batch"
  >
    <div class="flow-head">
      <div>
        <h3>旧驳回批次更正与放行冲突协调</h3>
        <p>
          打开旧驳回批次后，服务端同时核实原驳回样本和已领办的重解析任务；同一回执决定继续放行或转交重解析，
          两个入口不会分别把旧批次判成可发。
        </p>
      </div>
      <button class="btn ghost" type="button" data-testid="refresh-correction-board" @click="refresh">
        刷新协调状态
      </button>
    </div>

    <p v-if="message" class="flow-message" data-testid="correction-message" role="status">{{ message }}</p>
    <p v-if="error" class="error-text" data-testid="correction-error" role="alert">{{ error }}</p>

    <div class="rejected-grid">
      <article
        v-for="item in board?.rejected_batches ?? []"
        :key="String(item.id)"
        class="flow-card"
        :data-testid="`rejected-batch-${item.id}`"
        :data-batch-no="item.批次记录编号"
      >
        <header>
          <strong>{{ item.批次记录编号 }}</strong>
          <span class="muted">投递批次 {{ item.关联投递批次 }}</span>
        </header>
        <p><b>原驳回：</b>{{ item.原驳回样本 }} · {{ item.原驳回结论 }}</p>
        <p class="muted">原复核人：{{ item.原复核人 || '—' }}；接手任务：{{ item.接手任务 || '尚未领取' }}</p>
        <div class="button-row">
          <button
            class="btn"
            type="button"
            :data-testid="`claim-original-scope-${item.id}`"
            @click="claimScope(item)"
          >
            领取原范围
          </button>
          <button
            class="btn primary"
            type="button"
            :data-testid="`correct-original-value-${item.id}`"
            @click="openForBatch(item)"
          >
            更正原值
          </button>
          <button
            v-if="receiptForBatch(item)"
            class="btn ghost"
            type="button"
            :data-testid="`open-receipt-${receiptForBatch(item)?.回执编号}`"
            @click="showReceipt(String(receiptForBatch(item)?.回执编号))"
          >
            打开回执
          </button>
        </div>
      </article>
    </div>

    <div v-if="showForm && selectedBatch" class="modal-backdrop" data-testid="correction-modal">
      <form class="modal correction-form" data-testid="correction-form" @submit.prevent="submit">
        <h3>更正原值并提交修订样本</h3>
        <div class="original-rejection" data-testid="original-rejection">
          <strong>原驳回凭据（历史值和来源不变）</strong>
          <p>样本：{{ rejection?.样本编号 || selectedBatch.原驳回样本 }}</p>
          <p>原样本值：{{ rejection?.原样本值 || '见历史抽样明细' }}</p>
          <p>结论：{{ rejection?.复核结论 || selectedBatch.原驳回结论 }}</p>
          <p>来源依据：{{ rejection?.原驳回来源依据 || `原始抽样复核记录 ${rejection?.样本编号 || selectedBatch.原驳回样本}` }}</p>
          <p>复核人：{{ rejection?.复核人标识 || selectedBatch.原复核人 }}</p>
        </div>

        <label class="create-field">
          <span>接手人 / 复核员</span>
          <input v-model="form.reviewer" data-testid="correction-reviewer" autocomplete="off" />
        </label>
        <label class="create-field">
          <span>更正样本编号</span>
          <input v-model="form.corrected_sample_no" data-testid="correction-sample-no" autocomplete="off" />
        </label>
        <label class="create-field">
          <span>样本原值（仅作更正对照，不覆盖历史驳回样本）</span>
          <input v-model="form.original_value" data-testid="correction-original-value" autocomplete="off" />
        </label>
        <label class="create-field">
          <span>更正后值 / 修订值</span>
          <input v-model="form.corrected_value" data-testid="correction-revised-value" autocomplete="off" />
        </label>
        <label class="create-field">
          <span>修订样本（可填写逐字段 JSON 或说明）</span>
          <textarea
            v-model="form.sample_values_text"
            rows="3"
            data-testid="correction-sample-values"
          />
        </label>
        <div class="form-grid">
          <label class="create-field">
            <span>修订结论</span>
            <select v-model="form.revised_conclusion" data-testid="correction-conclusion">
              <option value="通过">通过</option>
              <option value="驳回">驳回</option>
            </select>
          </label>
          <button class="btn" type="button" data-testid="set-conclusion-pass" @click="form.revised_conclusion = '通过'">
            将修订结论改为通过
          </button>
        </div>
        <label class="create-field">
          <span>来源依据</span>
          <input v-model="form.source_basis" data-testid="correction-source-basis" autocomplete="off" />
        </label>

        <div class="version-status" data-testid="version-status" :data-claimed="liveClaim?.接手任务 ? 'true' : 'false'">
          <strong>版本状态：</strong>
          <template v-if="liveClaim?.接手任务">
            原驳回范围已由真实任务 {{ liveClaim.接手任务 }} / {{ liveClaim.接手人 }} 领取；提交后将转交重解析。
          </template>
          <template v-else>暂无重解析接手任务；若修订复验通过，可继续放行。</template>
        </div>

        <div class="status-flow" data-testid="correction-status-flow">
          <span class="flow-step is-done">原驳回</span>
          <span>→</span>
          <span class="flow-step is-done">更正受理</span>
          <span>→</span>
          <span class="flow-step" :class="{ 'is-current': activeReceipt?.放行方向 === '转交重解析' }">转交重解析</span>
          <span>/</span>
          <span class="flow-step" :class="{ 'is-current': activeReceipt?.放行方向 === '继续放行' }">继续核验放行</span>
        </div>

        <p v-if="message" class="flow-message" data-testid="correction-form-message" role="status">{{ message }}</p>
        <p v-if="error" class="error-text">{{ error }}</p>

        <div v-if="activeReceipt" class="button-row form-next-actions" data-testid="correction-form-next-actions">
          <RouterLink
            v-if="activeReceipt.重解析编号"
            class="btn primary link-btn"
            :to="`/reparse_task?receipt=${encodeURIComponent(activeReceipt.回执编号)}&task=${encodeURIComponent(activeReceipt.重解析编号)}`"
            data-testid="open-reparse-from-batch"
          >
            打开重解析单 {{ activeReceipt.重解析编号 }} · 继续核验
          </RouterLink>
          <button
            v-else-if="activeReceipt.放行方向 === '继续放行' && activeReceipt.受理状态 !== '复验通过' && activeReceipt.受理状态 !== '已发布'"
            class="btn primary"
            type="button"
            data-testid="continue-release-verification"
            @click="verifyNow"
          >
            继续放行核验
          </button>
          <RouterLink
            v-if="activeReceipt.候选批次编号"
            class="btn primary link-btn"
            :to="`/result_deliver?receipt=${encodeURIComponent(activeReceipt.回执编号)}`"
            data-testid="go-release-from-batch"
          >
            前往发布入口领取 {{ activeReceipt.候选批次编号 }}
          </RouterLink>
        </div>

        <div class="page-actions">
          <button class="btn" type="button" data-testid="cancel-correction" @click="showForm = false">关闭表单</button>
          <button class="btn primary" type="submit" data-testid="submit-correction">
            {{ pending ? '提交处理中，可安全重复点击' : '提交更正' }}
          </button>
        </div>
      </form>
    </div>

    <article
      v-if="activeReceipt"
      class="receipt-card"
      data-testid="correction-receipt"
      :data-receipt-no="activeReceipt.回执编号"
      :data-state="activeReceipt.受理状态"
      :data-disposition="activeReceipt.放行方向"
    >
      <header>
        <h3>更正受理回执 {{ activeReceipt.回执编号 }}</h3>
        <span class="badge" data-testid="receipt-state">{{ activeReceipt.受理状态 }}</span>
      </header>
      <p>{{ activeReceipt.原样本编号 }} → {{ activeReceipt.更正样本编号 }}；放行方向：<b>{{ activeReceipt.放行方向 }}</b></p>
      <p class="muted" data-testid="confirmed-receipt">
        目标回执编号：{{ activeReceipt.目标回执编号 || activeReceipt.回执编号 }}；
        确认状态：{{ activeReceipt.确认状态 || '已确认' }}；有效结论份数：1
      </p>

      <ol class="timeline-list" data-testid="receipt-timeline">
        <li v-for="(event, index) in activeReceipt.流转记录 ?? []" :key="index">
          <b>{{ event.状态 }}</b> · {{ event.时间 }} · {{ event.说明 }}
        </li>
      </ol>

      <div v-if="!showForm" class="button-row">
        <RouterLink
          v-if="activeReceipt.重解析编号"
          class="btn primary link-btn"
          :to="`/reparse_task?receipt=${encodeURIComponent(activeReceipt.回执编号)}&task=${encodeURIComponent(activeReceipt.重解析编号)}`"
          data-testid="open-reparse-from-batch"
        >
          打开重解析单 {{ activeReceipt.重解析编号 }} · 继续核验
        </RouterLink>
        <button
          v-else-if="activeReceipt.放行方向 === '继续放行' && activeReceipt.受理状态 !== '复验通过' && activeReceipt.受理状态 !== '已发布'"
          class="btn primary"
          type="button"
          data-testid="continue-release-verification"
          @click="verifyNow"
        >
          继续放行核验
        </button>
        <RouterLink
          v-if="activeReceipt.候选批次编号"
          class="btn primary link-btn"
          :to="`/result_deliver?receipt=${encodeURIComponent(activeReceipt.回执编号)}`"
          data-testid="go-release-from-batch"
        >
          前往发布入口领取 {{ activeReceipt.候选批次编号 }}
        </RouterLink>
      </div>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  claimOriginalScope,
  getCorrectionBoard,
  submitCorrection,
  verifyCorrection,
  type CorrectionBoard,
  type CorrectionReceipt,
} from '@/api/correction-flow'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ initialReceipt?: string }>()
const route = useRoute()
const router = useRouter()

const board = ref<CorrectionBoard | null>(null)
const selectedBatch = ref<EntryRow | null>(null)
const showForm = ref(false)
const activeReceiptNo = ref(props.initialReceipt || String(route.query.receipt || ''))
const message = ref('')
const error = ref('')
const pending = ref(false)
let inflight: Promise<void> | null = null
let timer: ReturnType<typeof setInterval> | undefined

const form = ref({
  reviewer: 'reviewer-duty',
  corrected_sample_no: '',
  original_value: '',
  corrected_value: '',
  sample_values_text: '{\n  "rec_id": "R-CORRECTED-001"\n}',
  revised_conclusion: '通过',
  source_basis: '复核员对照原始行文本确认，原值为录入偏差',
  idempotency_key: '',
})

const rejection = computed<EntryRow | null>(() => {
  if (!selectedBatch.value || !board.value) return null
  const batchNo = String(selectedBatch.value.批次记录编号 || '')
  const item = board.value.rejected_batches.find((row) => row.批次记录编号 === batchNo)
  if (!item) return null
  return {
    ...item,
    样本编号: item.原驳回样本,
    复核结论: item.原驳回结论,
    复核人标识: item.原复核人,
  } as EntryRow
})

const activeReceipt = computed<CorrectionReceipt | null>(() => {
  if (!board.value || !activeReceiptNo.value) return null
  return board.value.receipts.find((item) => item.回执编号 === activeReceiptNo.value) ?? null
})

const liveClaim = computed<EntryRow | null>(() => {
  if (!board.value || !selectedBatch.value) return null
  const batchNo = String(selectedBatch.value.批次记录编号 || '')
  return board.value.rejected_batches.find((item) => item.批次记录编号 === batchNo) ?? null
})

function draftKey(batchNo: string) {
  return `correction-draft:${batchNo}`
}

function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function receiptForBatch(item: EntryRow): CorrectionReceipt | undefined {
  return board.value?.receipts.find((receipt) => receipt.批次记录编号 === item.批次记录编号)
}

async function refresh() {
  try {
    board.value = await getCorrectionBoard()
    error.value = ''
  } catch (err) {
    error.value = err instanceof Error ? err.message : '协调状态读取失败'
  }
}

function defaultForm(row: EntryRow) {
  return {
    reviewer: 'reviewer-duty',
    corrected_sample_no: `SR-CORR-${String(row.id)}`,
    original_value: String(row.原样本值 || row.原驳回结论 || ''),
    corrected_value: '',
    sample_values_text: '{\n  "rec_id": "R-CORRECTED-001"\n}',
    revised_conclusion: '通过',
    source_basis: String(row.原驳回来源依据 || row.来源依据 || '复核员对照原始行文本确认，原值为录入偏差'),
    idempotency_key: newIdempotencyKey(),
  }
}

function openForBatch(row: EntryRow) {
  const batchNo = String(row.批次记录编号 || row.batch_no || row.id)
  // 同一批在轮询/版本状态刷新后重复打开时，绝不覆盖用户已经录入的草稿。
  if (selectedBatch.value && String(selectedBatch.value.批次记录编号) === batchNo) {
    showForm.value = true
    return
  }
  const rejected = board.value?.rejected_batches.find(
    (item) => String(item.批次记录编号) === batchNo,
  )
  const sourceRow = { ...row, ...(rejected || {}) }
  selectedBatch.value = sourceRow
  const saved = sessionStorage.getItem(draftKey(batchNo))
  if (saved) {
    try {
      form.value = { ...defaultForm(sourceRow), ...JSON.parse(saved) }
    } catch {
      form.value = defaultForm(sourceRow)
    }
  } else {
    form.value = defaultForm(sourceRow)
  }
  if (!form.value.idempotency_key) form.value.idempotency_key = newIdempotencyKey()
  showForm.value = true
  message.value = ''
  error.value = ''
}

watch(form, (value) => {
  if (selectedBatch.value) {
    sessionStorage.setItem(draftKey(String(selectedBatch.value.批次记录编号)), JSON.stringify(value))
  }
}, { deep: true })

watch(() => route.query.receipt, (value) => {
  if (typeof value === 'string') activeReceiptNo.value = value
})

async function claimScope(row: EntryRow) {
  error.value = ''
  message.value = ''
  const result = await claimOriginalScope({
    batch_no: String(row.批次记录编号),
    owner: 'reparse-owner',
  })
  if (!result.ok) {
    error.value = result.message
    return
  }
  message.value = result.message
  window.dispatchEvent(new CustomEvent('correction-flow:changed'))
  await refresh()
}

async function runSubmit() {
  if (!selectedBatch.value) return
  pending.value = true
  error.value = ''
  message.value = ''
  let sampleValues: unknown = form.value.sample_values_text
  try {
    sampleValues = JSON.parse(form.value.sample_values_text)
  } catch {
    // 非 JSON 时按普通修订样本文本提交，服务端会包装成对象。
  }
  const result = await submitCorrection({
    batch_no: String(selectedBatch.value.批次记录编号),
    reviewer: form.value.reviewer,
    corrected_sample_no: form.value.corrected_sample_no,
    original_value: form.value.original_value,
    corrected_value: form.value.corrected_value,
    sample_values: sampleValues,
    revised_conclusion: form.value.revised_conclusion,
    source_basis: form.value.source_basis,
    idempotency_key: form.value.idempotency_key,
  })
  pending.value = false
  if (!result.ok || !result.receipt) {
    error.value = result.message
    return
  }
  activeReceiptNo.value = result.receipt.回执编号
  message.value = result.message
  await router.replace({ query: { ...route.query, receipt: result.receipt.回执编号 } })
  window.dispatchEvent(new CustomEvent('correction-flow:changed'))
  await refresh()
}

function submit() {
  // 双击时复用同一个在途 Promise；请求结束后再次点击仍带同一个幂等键。
  if (!inflight) inflight = runSubmit().finally(() => { inflight = null })
  return inflight
}

async function verifyNow() {
  if (!activeReceipt.value) return
  const result = await verifyCorrection({ receipt_no: activeReceipt.value.回执编号 })
  if (!result.ok) {
    error.value = result.message
    return
  }
  message.value = result.message
  window.dispatchEvent(new CustomEvent('correction-flow:changed'))
  await refresh()
}

function showReceipt(no: string) {
  activeReceiptNo.value = no
  void router.replace({ query: { ...route.query, receipt: no } })
}

function handleChanged() {
  void refresh()
}

onMounted(() => {
  void refresh()
  window.addEventListener('correction-flow:changed', handleChanged)
  timer = setInterval(refresh, 1500)
})

onBeforeUnmount(() => {
  window.removeEventListener('correction-flow:changed', handleChanged)
  if (timer) clearInterval(timer)
})

defineExpose({ openForBatch, refresh })
</script>

<style scoped>
.flow-panel { margin: 12px 0; padding: 14px; background: #fff; border: 1px solid var(--border); border-radius: 10px; }
.flow-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.flow-head h3, .receipt-card h3 { margin: 0 0 4px; font-size: 16px; }
.flow-head p { margin: 0; color: var(--muted); font-size: 13px; }
.rejected-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; margin-top: 12px; }
.flow-card, .receipt-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: #fbfdff; }
.flow-card header { display: flex; justify-content: space-between; gap: 8px; }
.flow-card p { font-size: 13px; margin: 8px 0; }
.muted { color: var(--muted); font-size: 12px; }
.button-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.button-row .btn, .link-btn { text-decoration: none; font-size: 13px; }
.flow-message { color: #067647; background: #ecfdf3; border: 1px solid #abefc6; padding: 8px 10px; border-radius: 6px; font-size: 13px; }
.original-rejection { background: #fff1f0; border: 1px solid #ffccc7; border-radius: 6px; padding: 10px; margin-bottom: 12px; font-size: 13px; }
.original-rejection p { margin: 4px 0; }
.form-grid { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: end; }
.form-grid .create-field { margin: 0; }
.version-status { background: #eff6ff; border: 1px solid #bfdbfe; color: #1e40af; border-radius: 6px; padding: 8px 10px; font-size: 12px; margin: 8px 0; }
.form-next-actions { margin: 10px 0; padding: 10px; background: #f8fafc; border-radius: 6px; }
.status-flow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 12px; margin: 10px 0; }
.flow-step { padding: 3px 8px; border-radius: 999px; background: #eef2f7; color: var(--muted); }
.flow-step.is-done { background: #dcfce7; color: #166534; }
.flow-step.is-current { background: #dbeafe; color: #1d4ed8; font-weight: 700; }
.receipt-card { margin-top: 12px; background: #f8fafc; }
.receipt-card header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.badge { background: #dbeafe; color: #1d4ed8; border-radius: 999px; padding: 3px 10px; font-size: 12px; }
.timeline-list { margin: 10px 0; padding-left: 18px; font-size: 12px; color: #334155; }
.timeline-list li { margin-bottom: 4px; }
textarea { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px; font: inherit; }
</style>
