import { fetchJson, request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---------------------------------------------------------------------------
// 站点归属清单：跳转采集登记/重送、入口回查、去重分组与复采来源。
// ---------------------------------------------------------------------------

export type AttributionRow = {
  id: number
  attribution_id: string
  run_id: string
  entry_site: string
  entry_url: string
  landing_site: string
  landing_url: string
  primary_site: string
  source_site: string
  role: string
  summary: string
  content_hash: string
  crawl_count: number
  processing_count: number
  status: string
  [field: string]: string | number | boolean
}

export type CaptureInput = {
  run_id: string
  entry_url: string
  landing_url: string
  summary: string
  entry_site?: string
  landing_site?: string
  attribution_site?: string
  role?: string
}

export type IngestResult = {
  ok: boolean
  duplicate: boolean
  counted: boolean
  message: string
  capture?: AttributionRow
  processing_record?: Record<string, string | number>
  unique_captures?: number
}

export type GroupMember = {
  attribution_id: string
  source_site: string
  entry_site: string
  entry_url: string
  landing_site: string
  landing_url: string
  summary: string
  role: string
  processing_count: number
}

export type DedupGroup = {
  group_id: string
  run_id: string
  content_hash: string
  member_count: number
  total_members: number
  visible_members: number
  members: GroupMember[]
  representative: GroupMember
  representative_source_site: string
  entry_sites: string[]
  landing_sites: string[]
}

export type LookupResult = {
  ok: boolean
  found: boolean
  visible: boolean
  message: string
  attribution_id?: string
  run_id?: string
  entry_site?: string
  entry_url?: string
  landing_site?: string
  landing_url?: string
  summary?: string
  role?: string
}

export async function ingestCapture(input: CaptureInput): Promise<IngestResult> {
  const response = await request('/api/site_attribution/captures', {
    method: 'POST',
    body: JSON.stringify(input),
  })
  const payload = (await response.json()) as { results?: IngestResult[] }
  const result = payload.results?.[0]
  if (!result) throw new Error('采集登记失败')
  return result
}

export async function listManifest(
  filters: Record<string, string> = {},
): Promise<{ total: number; items: AttributionRow[] }> {
  const query = new URLSearchParams(
    Object.fromEntries(Object.entries(filters).filter(([, value]) => value)),
  ).toString()
  return await fetchJson(`/api/site_attribution/manifest?${query}`)
}

export async function lookupAttribution(
  attributionId: string,
  site: string,
): Promise<LookupResult> {
  const query = new URLSearchParams({ attribution_id: attributionId, site }).toString()
  const response = await request(`/api/site_attribution/lookup?${query}`)
  return (await response.json()) as LookupResult
}

export async function runAttributionDedup(runId: string): Promise<{
  ok: boolean
  captures: number
  groups: number
  duplicates: number
  group_ids: string[]
  message?: string
}> {
  const response = await request('/api/site_attribution/dedup', {
    method: 'POST',
    body: JSON.stringify({ run_id: runId, trigger: 'manual' }),
  })
  return await response.json()
}

export async function listDedupGroups(
  runId: string,
  site = '',
): Promise<{ total: number; items: DedupGroup[] }> {
  const query = new URLSearchParams({ run_id: runId, ...(site ? { site } : {}) }).toString()
  return await fetchJson(`/api/site_attribution/groups?${query}`)
}

export type RecrawlTarget = {
  ok: boolean
  message: string
  group_id?: string
  attribution_id?: string
  source_site?: string
  entry_site?: string
  entry_url?: string
  landing_site?: string
  landing_url?: string
  summary?: string
  role?: string
}

export async function loadRecrawlTarget(
  groupId: string,
  site = '',
): Promise<RecrawlTarget> {
  const query = site ? `?${new URLSearchParams({ site }).toString()}` : ''
  return await fetchJson(`/api/site_attribution/groups/${groupId}/recrawl${query}`)
}

export async function listRepresentatives(
  runId: string,
  site = '',
): Promise<{ total: number; items: RecrawlTarget[] }> {
  const query = new URLSearchParams({ run_id: runId, ...(site ? { site } : {}) }).toString()
  return await fetchJson(`/api/site_attribution/representatives?${query}`)
}
