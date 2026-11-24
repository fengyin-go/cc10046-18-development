"""来源字段字典路由：映射字典 CSV 导入、解析试跑、按来源映射与规则确认。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.field_mapper import FieldMapperService

router = APIRouter(prefix="/api/field-mapping", tags=["来源字段字典"])

_service = FieldMapperService()


class ImportDictPayload(BaseModel):
    csv_text: str = Field(default="", description="映射字典 CSV 原文")
    samples: dict[str, list[dict[str, Any]]] = Field(
        default_factory=dict, description="按来源组织的已采集样本，用于校验列名与转换类型")


class TrialPayload(BaseModel):
    samples: dict[str, list[dict[str, Any]]]
    expected_versions: dict[str, int] | None = None


class MapPayload(BaseModel):
    source: str
    data: dict[str, Any]


class ConfirmRulePayload(BaseModel):
    source: str
    source_field: str


@router.post("/dictionaries/import")
def import_dictionary(payload: ImportDictPayload) -> dict[str, Any]:
    """导入映射字典 CSV：按来源建字典，错误按原文件行号返回。"""
    return _service.domain_import_dict_csv(payload.csv_text, payload.samples)


@router.get("/dictionaries")
def list_dictionaries() -> dict[str, Any]:
    return {"items": _service.domain_list_dictionaries()}


@router.get("/dictionaries/{source}")
def get_dictionary(source: str) -> dict[str, Any]:
    dictionary = _service.domain_get_dictionary(source)
    if dictionary is None:
        return {"ok": False, "message": f"来源「{source}」尚未建立来源字段字典", "rules": []}
    return {"ok": True, **dictionary}


@router.post("/trial")
def trial_parse(payload: TrialPayload) -> dict[str, Any]:
    """解析试跑：消费来源字段字典，核对实际读到的字典版本。"""
    return _service.domain_trial_parse(payload.samples, payload.expected_versions)


@router.post("/map")
def map_by_source(payload: MapPayload) -> dict[str, Any]:
    """按来源映射：交给后续映射得到该来源的目标字段。"""
    return _service.domain_map_by_source(payload.source, payload.data)


@router.post("/rules/confirm")
def confirm_rule(payload: ConfirmRulePayload) -> dict[str, Any]:
    """确认一条来源规则；重导修正后确认状态保留，不会产生第二条。"""
    rule, message = _service.domain_confirm_rule(payload.source, payload.source_field)
    if rule is None:
        return {"ok": False, "message": message}
    return {"ok": True, "message": "来源规则已确认", "rule": rule}
