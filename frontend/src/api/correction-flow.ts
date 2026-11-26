import { request } from './client'
import type { EntryRow } from '@/data/types'

export type CorrectionFlowEvent = {
  状态: string
  时间: string
  说明: string
}

export type CorrectionReceipt = EntryRow & {
  回执编号: string
  批次记录编号: string
  受理状态?: string
  放行方向?: string
  重解析编号?: string
  候选批次编号?: string
  契约编号?: string
  投递记录编号?: string
  流转记录?: CorrectionFlowEvent[]
}

export type CorrectionBoard = {
  ok: boolean
  rejected_batches: EntryRow[]
  receipts: CorrectionReceipt[]
  tasks: EntryRow[]
  deliverables: CorrectionReceipt[]
  released?: CorrectionReceipt[]
}

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as T
}

export async function getCorrectionBoard(query: Record<string, string> = {}): Promise<CorrectionBoard> {
  const params = new URLSearchParams(
    Object.entries(query).filter(([, value]) => Boolean(value)),
  ).toString()
  const response = await request(`/api/correction-flow/board?${params}`)
  if (!response.ok) throw new Error('更正协调看板读取失败')
  return (await response.json()) as CorrectionBoard
}

export function claimOriginalScope(body: {
  batch_no: string
  owner?: string
  task_no?: string
}) {
  return postJson<{ ok: boolean; message: string; task?: EntryRow | null }>(
    '/api/correction-flow/claims',
    body,
  )
}

export function submitCorrection(body: Record<string, unknown>) {
  return postJson<{ ok: boolean; message: string; receipt?: CorrectionReceipt | null }>(
    '/api/correction-flow/corrections',
    body,
  )
}

export function verifyCorrection(body: { receipt_no?: string; task_no?: string }) {
  return postJson<{ ok: boolean; message: string; receipt?: CorrectionReceipt | null }>(
    '/api/correction-flow/verify',
    body,
  )
}

export function claimDeliverable(body: Record<string, unknown> = {}) {
  return postJson<{
    ok: boolean
    message: string
    receipt?: CorrectionReceipt
    delivery?: EntryRow | null
    adopted_version?: string
    adopted_version_count?: number
  }>('/api/correction-flow/release-claim', body)
}
