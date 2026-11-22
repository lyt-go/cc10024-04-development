<template>
  <section class="page" data-module="site_attribution">
    <header class="page-head">
      <div>
        <h2>站点归属</h2>
        <p class="page-desc">
          跳转采集的正式站点归属清单：一次抓取只记一个主站点，保留入口、落地地址与运行身份；
          重送只追加处理记录，不重复计算抓取次数。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-capture" type="button" @click="openCapture">
          登记一次抓取
        </button>
        <button class="btn" data-testid="run-dedup" type="button" @click="submitDedup">
          按运行执行去重
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>运行身份</span>
        <input v-model="filters.run_id" data-testid="filter-run" placeholder="按运行身份检索" />
      </label>
      <label class="filter-item">
        <span>主站点</span>
        <input v-model="filters.site" data-testid="filter-site" placeholder="按主站点检索" />
      </label>
      <label class="filter-item">
        <span>入口站点</span>
        <input v-model="filters.entry_site" data-testid="filter-entry-site" placeholder="按入口核对照表" />
      </label>
      <label class="filter-item">
        <span>落地站点</span>
        <input v-model="filters.landing_site" data-testid="filter-landing-site" placeholder="按落地站点检索" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>归属编号</th>
          <th>采集归属</th>
          <th>主站点</th>
          <th>入口站点</th>
          <th>入口地址</th>
          <th>落地站点</th>
          <th>落地地址</th>
          <th>运行身份</th>
          <th>采集角色</th>
          <th>抓取 / 处理</th>
          <th>页面摘要</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.attribution_id" :data-testid="`attr-${row.attribution_id}`">
          <td>{{ row.attribution_id }}</td>
          <td>{{ row.source_site }}</td>
          <td>{{ row.primary_site }}</td>
          <td>{{ row.entry_site }}</td>
          <td class="url-cell">{{ row.entry_url }}</td>
          <td>{{ row.landing_site }}</td>
          <td class="url-cell">{{ row.landing_url }}</td>
          <td>{{ row.run_id }}</td>
          <td>{{ row.role }}</td>
          <td>{{ row.crawl_count }} / {{ row.processing_count }}</td>
          <td class="summary-cell">{{ row.summary }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`lookup-${row.attribution_id}`" @click="openLookup(row)">
              入口回查
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="12" class="empty-state">暂无归属清单数据，可先登记一次抓取</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条站点归属记录；同一次抓取重送只增加处理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <section v-if="notice" class="notice" data-testid="notice">{{ notice }}</section>

    <section class="panel" data-testid="dedup-panel">
      <h3>运行确认后的去重分组</h3>
      <p class="page-desc">
        分组只在同一次运行内进行，另一个运行的相同页面不会计入；组内每个成员保留自己的入口与落地来源。
      </p>
      <div class="panel-tools">
        <label class="filter-item">
          <span>运行身份</span>
          <input v-model="dedupRunId" data-testid="dedup-run" placeholder="输入运行身份后执行去重" />
        </label>
        <label class="filter-item">
          <span>站点视角</span>
          <input v-model="viewSite" data-testid="dedup-site" placeholder="留空表示不裁剪明细" />
        </label>
        <button class="btn" data-testid="load-groups" type="button" @click="loadGroups">查询分组</button>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th>分组编号</th>
            <th>运行身份</th>
            <th>成员数</th>
            <th>入口站点</th>
            <th>代表页站点</th>
            <th>可用成员</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="group in groups" :key="group.group_id" :data-testid="`group-${group.group_id}`">
            <td>{{ group.group_id }}</td>
            <td>{{ group.run_id }}</td>
            <td>{{ group.visible_members }} / {{ group.total_members }}</td>
            <td>{{ group.entry_sites.join('、') }}</td>
            <td>{{ group.representative_source_site }}</td>
            <td>{{ group.members.map((member) => member.source_site).join('、') || '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`recrawl-${group.group_id}`" @click="showRecrawl(group)">
                复采来源
              </button>
            </td>
          </tr>
          <tr v-if="!groups.length">
            <td colspan="7" class="empty-state">当前运行暂无去重分组</td>
          </tr>
        </tbody>
      </table>
    </section>

    <div v-if="recrawl" class="modal-backdrop" data-testid="recrawl-modal">
      <div class="modal">
        <h3>复采来源记录</h3>
        <dl class="kv">
          <dt>分组编号</dt><dd>{{ recrawl.group_id }}</dd>
          <dt>采集归属</dt><dd>{{ recrawl.source_site }}</dd>
          <dt>入口地址</dt><dd>{{ recrawl.entry_url }}</dd>
          <dt>落地地址</dt><dd>{{ recrawl.landing_url }}</dd>
          <dt>原摘要</dt><dd>{{ recrawl.summary }}</dd>
        </dl>
        <div class="page-actions">
          <button class="btn" type="button" @click="recrawl = null">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="lookup" class="modal-backdrop" data-testid="lookup-modal">
      <div class="modal">
        <h3>入口回查</h3>
        <p class="page-desc">{{ lookup.message }}</p>
        <dl v-if="lookup.visible" class="kv">
          <dt>归属编号</dt><dd>{{ lookup.attribution_id }}</dd>
          <dt>入口地址</dt><dd>{{ lookup.entry_url }}</dd>
          <dt>落地地址</dt><dd>{{ lookup.landing_url }}</dd>
          <dt>原摘要</dt><dd>{{ lookup.summary }}</dd>
        </dl>
        <div class="page-actions">
          <button class="btn" type="button" @click="lookup = null">关闭</button>
        </div>
      </div>
    </div>

    <div v-if="showCapture" class="modal-backdrop" data-testid="capture-modal">
      <form class="modal" @submit.prevent="submitCapture">
        <h3>登记一次抓取</h3>
        <label v-for="field in captureFields" :key="field.key" class="create-field">
          <span>{{ field.label }}</span>
          <input v-model="captureValues[field.key]" :data-testid="`capture-${field.key}`" :required="field.required" />
        </label>
        <p class="page-desc">重送同一次抓取（运行身份、入口地址、落地地址相同）只追加处理记录。</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCapture = false">取消</button>
          <button class="btn primary" data-testid="submit-capture" type="submit" :disabled="saving">
            {{ saving ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  ingestCapture,
  listDedupGroups,
  listManifest,
  loadRecrawlTarget,
  lookupAttribution,
  runAttributionDedup,
} from '@/api/backend-service'
import type {
  AttributionRow,
  CaptureInput,
  DedupGroup,
  LookupResult,
  RecrawlTarget,
} from '@/api/backend-service'

const rows = ref<AttributionRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const notice = ref('')
const filters = ref({ run_id: '', site: '', entry_site: '', landing_site: '' })
const dedupRunId = ref('')
const viewSite = ref('')
const groups = ref<DedupGroup[]>([])
const recrawl = ref<RecrawlTarget | null>(null)
const lookup = ref<LookupResult | null>(null)
const showCapture = ref(false)
const saving = ref(false)
const captureValues = ref<Record<string, string>>({})

const captureFields = [
  { key: 'run_id', label: '运行身份', required: true },
  { key: 'entry_url', label: '入口地址', required: true },
  { key: 'landing_url', label: '落地地址', required: true },
  { key: 'summary', label: '页面摘要', required: true },
  { key: 'entry_site', label: '入口站点（留空按地址推断）', required: false },
  { key: 'landing_site', label: '落地站点（留空按地址推断）', required: false },
  { key: 'attribution_site', label: '采集归属（留空取入口站点）', required: false },
  { key: 'role', label: '采集角色（直接采集 / 跳转采集）', required: false },
]

const stats = computed(() => [
  { label: '归属清单总数', value: total.value },
  { label: '已分组归属', value: rows.value.filter((row) => row.status === '已分组').length },
  {
    label: '重送处理记录',
    value: rows.value.reduce((sum, row) => sum + Number(row.processing_count || 0), 0),
  },
])

function resetFilters() {
  filters.value = { run_id: '', site: '', entry_site: '', landing_site: '' }
  void reload()
}

function openCapture() {
  captureValues.value = Object.fromEntries(captureFields.map((field) => [field.key, '']))
  showCapture.value = true
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listManifest(filters.value)
    rows.value = payload.items
    total.value = payload.total
    if (!dedupRunId.value && filters.value.run_id) dedupRunId.value = filters.value.run_id
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '归属清单读取失败'
  }
}

async function submitCapture() {
  saving.value = true
  errorMessage.value = ''
  try {
    const result = await ingestCapture(captureValues.value as unknown as CaptureInput)
    if (!result.ok) throw new Error(result.message)
    notice.value = result.duplicate
      ? `同一次抓取已存在，仅追加处理记录；该运行真实抓取 ${result.unique_captures} 次`
      : result.message
    showCapture.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集登记失败'
  } finally {
    saving.value = false
  }
}

async function submitDedup() {
  errorMessage.value = ''
  if (!dedupRunId.value) {
    errorMessage.value = '请先填写运行身份'
    return
  }
  try {
    const result = await runAttributionDedup(dedupRunId.value)
    if (!result.ok) throw new Error(result.message || '去重失败')
    notice.value = `运行 ${dedupRunId.value} 实际抓取 ${result.captures} 次，去重后 ${result.groups} 组，重复 ${result.duplicates} 条`
    await loadGroups()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '去重失败'
  }
}

async function loadGroups() {
  errorMessage.value = ''
  if (!dedupRunId.value) return
  try {
    const payload = await listDedupGroups(dedupRunId.value, viewSite.value)
    groups.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '去重分组读取失败'
  }
}

async function showRecrawl(group: DedupGroup) {
  errorMessage.value = ''
  try {
    recrawl.value = await loadRecrawlTarget(group.group_id, viewSite.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复采来源读取失败'
  }
}

async function openLookup(row: AttributionRow) {
  errorMessage.value = ''
  const site = viewSite.value || row.source_site
  try {
    lookup.value = await lookupAttribution(row.attribution_id, site)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '入口回查失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.url-cell,
.summary-cell {
  max-width: 220px;
  word-break: break-all;
}
.notice {
  margin: 10px 0;
  padding: 8px 12px;
  background: #eff8ff;
  border: 1px solid #b2ddff;
  border-radius: 8px;
  font-size: 13px;
}
.panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}
.panel-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin: 10px 0;
}
.kv {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 6px 10px;
  font-size: 13px;
}
.kv dt {
  color: var(--muted);
}
.kv dd {
  margin: 0;
  word-break: break-all;
}
</style>
