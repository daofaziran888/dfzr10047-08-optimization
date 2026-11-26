<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <ParseBatchCorrectionPanel v-if="meta.key === 'parse_batch'" ref="correctionPanel" />
    <ReparseCorrectionPanel v-if="meta.key === 'reparse_task'" />
    <ReleaseClaimPanel v-if="meta.key === 'result_deliver'" />

    <p v-if="noticeMessage" class="flow-message" data-testid="action-message" role="status">{{ noticeMessage }}</p>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              v-show="showRowAction(action, row)"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import {
  createEntry,
  downloadEntries,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'
import ParseBatchCorrectionPanel from '@/components/ParseBatchCorrectionPanel.vue'
import ReparseCorrectionPanel from '@/components/ReparseCorrectionPanel.vue'
import ReleaseClaimPanel from '@/components/ReleaseClaimPanel.vue'
import { getCorrectionBoard } from '@/api/correction-flow'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const correctionPanel = ref<InstanceType<typeof ParseBatchCorrectionPanel> | null>(null)
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const rejectedBatchNos = ref<Set<string>>(new Set())
const correctionTaskNos = ref<Set<string>>(new Set())
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function showRowAction(action: string, row: EntryRow): boolean {
  if (meta.key === 'parse_batch' && (action === '领取原范围' || action === '更正原值')) {
    return rejectedBatchNos.value.has(String(row.批次记录编号))
  }
  if (meta.key === 'reparse_task' && action === '继续核验') {
    // 有回执时才把通用行内入口收敛为“真实关联任务”；未受理时仍允许点击并得到服务端提示。
    return correctionTaskNos.value.size === 0 || correctionTaskNos.value.has(String(row.重解析编号))
  }
  return true
}

async function loadFlowHints() {
  if (!['parse_batch', 'reparse_task', 'result_deliver'].includes(meta.key)) return
  try {
    const board = await getCorrectionBoard()
    rejectedBatchNos.value = new Set(
      board.rejected_batches.map((row) => String(row.批次记录编号)),
    )
    correctionTaskNos.value = new Set(
      board.receipts
        .map((receipt) => String(receipt.重解析编号 || ''))
        .filter(Boolean),
    )
  } catch {
    // 协调面板自身会展示读取错误；通用表格不应因此阻断。
  }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  noticeMessage.value = ''

  // “更正原值”是打开保留草稿的更正表单，不能走通用状态覆盖。
  if (meta.key === 'parse_batch' && action === '更正原值') {
    correctionPanel.value?.openForBatch(row)
    return
  }

  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    noticeMessage.value = result.message
    window.dispatchEvent(new CustomEvent('correction-flow:changed'))
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void loadFlowHints()
  window.addEventListener('correction-flow:changed', loadFlowHints)
})

onBeforeUnmount(() => {
  window.removeEventListener('correction-flow:changed', loadFlowHints)
})
</script>
