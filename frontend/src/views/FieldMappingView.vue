<template>
  <section class="page field-mapping" data-module="field_mapper">
    <header class="page-head">
      <div>
        <h2>字段映射 · 来源字段字典</h2>
        <p class="page-desc">
          按来源建立来源字段字典：导入映射字典 CSV，用已采集样本校验列名与转换类型；
          解析试跑核对实际读到的字典版本，再按来源映射得到目标字段。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="load-dicts" type="button" @click="refreshDictionaries">
          刷新字典
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">已建字典来源</span>
        <strong class="stat-value">{{ dictionaries.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">试跑已用新版</span>
        <strong class="stat-value" data-testid="latest-count">{{ trial?.latest_sources.length ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">仍在等待修正</span>
        <strong class="stat-value" data-testid="waiting-count">{{ trial?.waiting_sources.length ?? 0 }}</strong>
      </article>
    </div>

    <!-- 第一步：已采集样本 -->
    <article class="fm-card">
      <h3>① 已采集样本（用于校验列名与转换类型）</h3>
      <p class="fm-hint">
        每个来源一段 JSON 对象数组，键为实际读到的列名。示例已按「甲 / 乙」两个来源预填。
      </p>
      <textarea
        v-model="sampleText"
        class="fm-code"
        data-testid="sample-input"
        rows="6"
        spellcheck="false"
      ></textarea>
    </article>

    <!-- 第二步：映射字典 CSV 导入 -->
    <article class="fm-card">
      <h3>② 映射字典 CSV 导入</h3>
      <p class="fm-hint">
        必含列：来源、来源字段、目标字段、转换类型；可选列：默认取值、依赖字段。
        支持的转换类型：string、int、decimal、upper、lower、trim（留空按 string 处理）。
      </p>
      <textarea
        v-model="csvText"
        class="fm-code"
        data-testid="csv-input"
        rows="8"
        spellcheck="false"
      ></textarea>
      <div class="page-actions fm-actions">
        <button class="btn primary" data-testid="import-csv" type="button" :disabled="busy" @click="onImport">
          {{ busy ? '导入中…' : '导入映射字典' }}
        </button>
        <button class="btn" data-testid="fill-broken" type="button" @click="fillBroken">
          填入含错样例
        </button>
        <button class="btn" data-testid="fill-fixed" type="button" @click="fillFixed">
          填入修正样例
        </button>
      </div>
    </article>

    <!-- 导入结果 -->
    <article v-if="importResult" class="fm-card" data-testid="import-result">
      <h3>导入结果</h3>
      <p :class="importResult.ok ? 'fm-ok' : 'fm-bad'">
        {{ importResult.ok ? '本次导入范围全部来源校验通过' : '存在被拒绝的来源，已通过来源照常建立字典' }}
      </p>
      <table class="data-table">
        <thead>
          <tr>
            <th>来源</th>
            <th>结果</th>
            <th>字典版本</th>
            <th>规则数</th>
            <th>新增/更新</th>
            <th>已确认保留</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="report in importResult.sources" :key="report.source" :data-testid="`import-row-${report.source}`">
            <td>{{ report.source }}</td>
            <td>
              <span :class="report.status === 'imported' ? 'tag tag-ok' : 'tag tag-bad'">
                {{ report.status === 'imported' ? '已建立新版' : '已拒绝（保留旧版）' }}
              </span>
            </td>
            <td>v{{ report.version }}</td>
            <td>{{ report.rule_count }}</td>
            <td>{{ report.created ?? 0 }} / {{ report.updated ?? 0 }}</td>
            <td>{{ report.confirmed_kept ?? 0 }}</td>
          </tr>
        </tbody>
      </table>
      <ul v-if="importResult.errors.length" class="fm-errors" data-testid="import-errors">
        <li v-for="(error, index) in importResult.errors" :key="index" class="fm-error">
          <strong v-if="error.line">第 {{ error.line }} 行</strong>
          <strong v-else>结构错误</strong>
          <template v-if="error.source">（来源：{{ error.source }}）</template>
          ：{{ error.message }}
        </li>
      </ul>
      <ul v-if="importResult.warnings.length" class="fm-warnings">
        <li v-for="(warning, index) in importResult.warnings" :key="index">{{ warning.message }}</li>
      </ul>
    </article>

    <!-- 第三步：解析试跑 -->
    <article class="fm-card">
      <h3>③ 解析试跑（消费来源字段字典，核对实际读到的字典版本）</h3>
      <div class="page-actions fm-actions">
        <button class="btn primary" data-testid="run-trial" type="button" :disabled="busy" @click="onTrial">
          打开解析试跑
        </button>
        <label class="fm-inline">
          携带期望版本
          <input v-model="expectVersionText" data-testid="expect-versions" placeholder='如 {"乙": 1}' />
        </label>
      </div>
    </article>

    <article v-if="trial" class="fm-card" data-testid="trial-result">
      <h3>试跑结果</h3>
      <div class="fm-source-badges">
        <span class="tag tag-ok" data-testid="latest-badge">已用新版：{{ trial.latest_sources.join('、') || '无' }}</span>
        <span class="tag tag-wait" data-testid="waiting-badge">仍等待：{{ trial.waiting_sources.join('、') || '无' }}</span>
      </div>
      <div v-for="source in trial.sources" :key="source.source" class="fm-source-block" :data-testid="`trial-source-${source.source}`">
        <header>
          <strong>来源「{{ source.source }}」</strong>
          <span :class="statusTagClass(source.status)">{{ statusLabel(source.status) }}</span>
          <span class="fm-muted">实际读到字典版本：{{ source.actual_version ?? '无' }}</span>
        </header>
        <p class="fm-muted">{{ source.message }}</p>
        <ul v-if="source.errors.length" class="fm-errors">
          <li v-for="(error, index) in source.errors" :key="index" class="fm-error">
            <strong v-if="error.line">第 {{ error.line }} 行</strong>：{{ error.message }}
          </li>
        </ul>
        <table v-if="source.preview.length" class="data-table">
          <thead>
            <tr>
              <th>原文件行号</th>
              <th>来源字段</th>
              <th>目标字段</th>
              <th>转换类型</th>
              <th>样本原值</th>
              <th>试跑取值</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in source.preview" :key="`${row.source}-${row.line}`">
              <td>{{ row.line }}</td>
              <td>{{ row.source_field }}</td>
              <td>{{ row.target_field }}</td>
              <td>{{ row.transform }}</td>
              <td>{{ row.raw_value }}</td>
              <td data-testid="preview-converted">{{ row.converted_value }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <!-- 第四步：按来源映射验证 -->
    <article class="fm-card">
      <h3>④ 按来源映射验证</h3>
      <div class="fm-actions">
        <label class="fm-inline">
          来源
          <input v-model="mapSource" data-testid="map-source" placeholder="如 乙" />
        </label>
        <label class="fm-inline fm-grow">
          待映射数据（JSON）
          <input v-model="mapDataText" data-testid="map-data" placeholder='如 {"销量": "7", "折扣": "0.88"}' />
        </label>
        <button class="btn primary" data-testid="run-map" type="button" :disabled="busy" @click="onMap">
          执行映射
        </button>
      </div>
      <div v-if="mapResult" class="fm-map-output" data-testid="map-result">
        <p :class="mapResult.ok ? 'fm-ok' : 'fm-bad'">
          {{ mapResult.message }}
        </p>
        <table v-if="Object.keys(mapResult.target).length" class="data-table">
          <thead>
            <tr><th>目标字段</th><th>取值</th></tr>
          </thead>
          <tbody>
            <tr v-for="(value, key) in mapResult.target" :key="key">
              <td>{{ key }}</td>
              <td>{{ value }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </article>

    <!-- 字典清单与规则确认 -->
    <article class="fm-card">
      <h3>来源字段字典清单（重导修正后，已确认规则不会写两份）</h3>
      <table v-if="dictionaries.length" class="data-table" data-testid="dict-table">
        <thead>
          <tr>
            <th>来源</th>
            <th>当前版本</th>
            <th>来源字段列</th>
            <th>规则数</th>
            <th>已确认</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="dict in dictionaries" :key="dict.source">
            <td>{{ dict.source }}</td>
            <td>v{{ dict.version }}</td>
            <td>{{ dict.columns.join('、') }}</td>
            <td>{{ dict.rule_count }}</td>
            <td>{{ dict.confirmed_count }} / {{ dict.rule_count }}</td>
            <td>
              <button class="link" type="button" @click="openDictionary(dict.source)">查看规则</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="fm-muted">尚未建立任何来源字段字典。</p>
    </article>

    <article v-if="activeDictionary" class="fm-card" data-testid="dict-detail">
      <h3>来源「{{ activeDictionary.source }}」字典规则（v{{ activeDictionary.version }}）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>原文件行号</th>
            <th>来源字段</th>
            <th>目标字段</th>
            <th>转换类型</th>
            <th>依赖字段</th>
            <th>确认状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in activeDictionary.rules" :key="rule.source_field">
            <td>{{ rule.line }}</td>
            <td>{{ rule.source_field }}</td>
            <td>{{ rule.target_field }}</td>
            <td>{{ rule.transform || 'string' }}</td>
            <td>{{ rule.depends_on.join('、') || '—' }}</td>
            <td>
              <span :class="rule.confirmed ? 'tag tag-ok' : 'tag tag-wait'">
                {{ rule.confirmed ? '已确认' : '待确认' }}
              </span>
            </td>
            <td>
              <button
                v-if="!rule.confirmed"
                class="link"
                type="button"
                :data-testid="`confirm-${rule.source_field}`"
                @click="onConfirm(rule.source_field)"
              >
                确认规则
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </article>

    <footer class="page-foot">
      <span v-if="errorMessage" class="error-text" data-testid="fm-error">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  confirmRule,
  getDictionary,
  importDictCsv,
  listDictionaries,
  mapBySource,
  trialParse,
} from '@/api/field-mapping'
import type {
  DictionaryInfo,
  ImportResult,
  MapResult,
  TrialResult,
} from '@/data/field-mapping'

const BROKEN_CSV = [
  '来源,来源字段,目标字段,转换类型,默认取值,依赖字段',
  '甲,商品名,product_name,string,,',
  '乙,销量,sales_qty,bogus,,',
  '乙,折扣,discount_rate,decimal,,销量',
  '',
].join('\n')

const FIXED_CSV = [
  '来源,来源字段,目标字段,转换类型,默认取值,依赖字段',
  '甲,商品名,product_name,string,,',
  '乙,销量,sales_qty,int,,',
  '乙,折扣,discount_rate,decimal,,销量',
  '',
].join('\n')

const SAMPLE_JSON = JSON.stringify(
  {
    甲: [{ 商品名: '红茶礼盒' }],
    乙: [{ 销量: '7', 折扣: '0.88' }],
  },
  null,
  2,
)

const csvText = ref(BROKEN_CSV)
const sampleText = ref(SAMPLE_JSON)
const expectVersionText = ref('')
const mapSource = ref('乙')
const mapDataText = ref('{"销量": "7", "折扣": "0.88"}')

const busy = ref(false)
const errorMessage = ref('')
const dictionaries = ref<DictionaryInfo[]>([])
const importResult = ref<ImportResult | null>(null)
const trial = ref<TrialResult | null>(null)
const mapResult = ref<MapResult | null>(null)
const activeDictionary = ref<Awaited<ReturnType<typeof getDictionary>> | null>(null)

function fillBroken() {
  csvText.value = BROKEN_CSV
}

function fillFixed() {
  csvText.value = FIXED_CSV
}

function parseSamples(): Record<string, Record<string, string>[]> {
  try {
    const parsed = JSON.parse(sampleText.value) as Record<string, Record<string, string>[]>
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('样本需是以来源为键的 JSON 对象')
    }
    return parsed
  } catch (error) {
    throw new Error(`已采集样本 JSON 解析失败：${error instanceof Error ? error.message : '格式错误'}`)
  }
}

function parseExpectVersions(): Record<string, number> | undefined {
  const text = expectVersionText.value.trim()
  if (!text) return undefined
  const parsed = JSON.parse(text) as Record<string, number>
  return parsed
}

async function onImport() {
  errorMessage.value = ''
  busy.value = true
  try {
    const samples = parseSamples()
    importResult.value = await importDictCsv(csvText.value, samples)
    await refreshDictionaries()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '映射字典导入失败'
  } finally {
    busy.value = false
  }
}

async function onTrial() {
  errorMessage.value = ''
  busy.value = true
  try {
    const samples = parseSamples()
    trial.value = await trialParse(samples, parseExpectVersions())
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '解析试跑失败'
  } finally {
    busy.value = false
  }
}

async function onMap() {
  errorMessage.value = ''
  busy.value = true
  try {
    const data = JSON.parse(mapDataText.value) as Record<string, string>
    mapResult.value = await mapBySource(mapSource.value.trim(), data)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '映射执行失败'
  } finally {
    busy.value = false
  }
}

async function onConfirm(sourceField: string) {
  if (!activeDictionary.value) return
  errorMessage.value = ''
  try {
    const result = await confirmRule(activeDictionary.value.source, sourceField)
    if (!result.ok) throw new Error(result.message)
    await openDictionary(activeDictionary.value.source)
    await refreshDictionaries()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '规则确认失败'
  }
}

async function openDictionary(source: string) {
  activeDictionary.value = await getDictionary(source)
}

async function refreshDictionaries() {
  const payload = await listDictionaries()
  dictionaries.value = payload.items
}

function statusLabel(status: TrialResult['sources'][number]['status']): string {
  if (status === 'latest') return '已用新版'
  if (status === 'stale') return '版本过期 · 等待'
  return '字典缺失 · 等待'
}

function statusTagClass(status: TrialResult['sources'][number]['status']): string {
  if (status === 'latest') return 'tag tag-ok'
  return 'tag tag-wait'
}

onMounted(refreshDictionaries)
</script>

<style scoped>
.fm-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 14px;
}

.fm-card h3 {
  margin: 0 0 8px;
  font-size: 15px;
}

.fm-hint {
  color: var(--muted);
  font-size: 12px;
  margin: 0 0 8px;
}

.fm-code {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  font-family: monospace;
  font-size: 12px;
  line-height: 1.5;
  resize: vertical;
}

.fm-actions {
  margin-top: 10px;
  gap: 12px;
  align-items: center;
}

.fm-inline {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}

.fm-inline input {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  min-width: 180px;
}

.fm-grow {
  flex: 1;
}

.fm-grow input {
  width: 100%;
}

.fm-ok {
  color: #027a48;
  font-size: 13px;
}

.fm-bad {
  color: #b42318;
  font-size: 13px;
}

.fm-muted {
  color: var(--muted);
  font-size: 12px;
}

.fm-errors {
  margin: 8px 0 0;
  padding-left: 18px;
}

.fm-error {
  color: #b42318;
  font-size: 12px;
  margin-bottom: 4px;
}

.fm-warnings {
  margin: 8px 0 0;
  padding-left: 18px;
  color: #b54708;
  font-size: 12px;
}

.tag {
  display: inline-block;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
}

.tag-ok {
  background: #ecfdf3;
  color: #027a48;
  border: 1px solid #abefc6;
}

.tag-bad {
  background: #fef3f2;
  color: #b42318;
  border: 1px solid #fecdca;
}

.tag-wait {
  background: #fffaeb;
  color: #b54708;
  border: 1px solid #fedf89;
}

.fm-source-badges {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
}

.fm-source-block {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.fm-source-block header {
  display: flex;
  gap: 10px;
  align-items: center;
}

.fm-map-output {
  margin-top: 10px;
}
</style>
