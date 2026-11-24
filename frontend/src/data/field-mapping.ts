/** 来源字段字典相关类型，与后端 field_mapping 路由返回结构保持一致。 */

export type DictError = {
  source: string | null
  line: number | null
  message: string
}

export type DictionaryInfo = {
  source: string
  version: number
  columns: string[]
  rule_count: number
  confirmed_count: number
  version_changed?: boolean
}

export type DictRule = {
  source: string
  source_field: string
  target_field: string
  transform: string
  default: string
  depends_on: string[]
  line: number
  confirmed: boolean
}

export type ImportReport = {
  source: string
  status: 'imported' | 'rejected'
  version: number
  version_changed?: boolean
  rule_count: number
  created?: number
  updated?: number
  confirmed_kept?: number
  errors: DictError[]
}

export type ImportResult = {
  ok: boolean
  imported_sources: string[]
  rejected_sources: string[]
  sources: ImportReport[]
  errors: DictError[]
  warnings: DictError[]
}

export type TrialPreview = {
  source: string
  line: number
  source_field: string
  target_field: string
  transform: string
  raw_value: string | number
  converted_value: string | number
  confirmed: boolean
}

export type TrialSource = {
  source: string
  status: 'latest' | 'stale' | 'missing'
  actual_version: number | null
  expected_version: number | null
  message: string
  rule_count: number
  errors: DictError[]
  warnings: DictError[]
  preview: TrialPreview[]
}

export type TrialResult = {
  ok: boolean
  latest_sources: string[]
  waiting_sources: string[]
  sources: TrialSource[]
}

export type MapResult = {
  ok: boolean
  source: string
  version: number | null
  message: string
  target: Record<string, string | number>
  errors: DictError[]
}
