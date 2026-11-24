import { request } from './client'
import type {
  DictionaryInfo,
  DictRule,
  ImportResult,
  MapResult,
  TrialResult,
} from '@/data/field-mapping'

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(`接口返回 ${response.status}，请求未生效`)
  return (await response.json()) as T
}

export async function importDictCsv(
  csvText: string,
  samples: Record<string, Record<string, string>[]>,
): Promise<ImportResult> {
  return postJson('/api/field-mapping/dictionaries/import', {
    csv_text: csvText,
    samples,
  })
}

export async function listDictionaries(): Promise<{ items: DictionaryInfo[] }> {
  const response = await request('/api/field-mapping/dictionaries')
  if (!response.ok) throw new Error('来源字段字典读取失败')
  return await response.json()
}

export async function getDictionary(
  source: string,
): Promise<{ ok: boolean; message?: string; rules: DictRule[] } & DictionaryInfo> {
  const response = await request(
    `/api/field-mapping/dictionaries/${encodeURIComponent(source)}`,
  )
  if (!response.ok) throw new Error(`来源「${source}」字典读取失败`)
  return await response.json()
}

export async function trialParse(
  samples: Record<string, Record<string, string>[]>,
  expectedVersions?: Record<string, number>,
): Promise<TrialResult> {
  return postJson('/api/field-mapping/trial', {
    samples,
    expected_versions: expectedVersions,
  })
}

export async function mapBySource(
  source: string,
  data: Record<string, string>,
): Promise<MapResult> {
  return postJson('/api/field-mapping/map', { source, data })
}

export async function confirmRule(
  source: string,
  sourceField: string,
): Promise<{ ok: boolean; message: string }> {
  return postJson('/api/field-mapping/rules/confirm', {
    source,
    source_field: sourceField,
  })
}
