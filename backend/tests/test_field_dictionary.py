"""来源字段字典与映射字典 CSV 导入、解析试跑、按来源映射测试。

直接运行：python -I tests/test_field_dictionary.py
（环境未安装 pytest 时也可用标准库 unittest 风格的 main 跑通）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.field_mapper import FieldMapperService
from app.routers.field_mapping import _service as router_service


# 同一份导入范围含两个来源：甲规则完整；乙销量写未知转换、折扣依赖销量
BROKEN_CSV = """来源,来源字段,目标字段,转换类型,默认取值,依赖字段
甲,商品名,product_name,string,,
乙,销量,sales_qty,bogus,,
乙,折扣,discount_rate,decimal,,销量
"""

# 修正乙：销量转换类型改为整数 int
FIXED_CSV = """来源,来源字段,目标字段,转换类型,默认取值,依赖字段
甲,商品名,product_name,string,,
乙,销量,sales_qty,int,,
乙,折扣,discount_rate,decimal,,销量
"""

SAMPLES = {
    "甲": [{"商品名": "红茶礼盒"}],
    "乙": [{"销量": "7", "折扣": "0.88"}],
}


def setup_function(_):
    FieldMapperService.reset_dictionaries()


def _reset():
    FieldMapperService.reset_dictionaries()


def test_first_import_keeps_good_source_and_rejects_bad_source_by_line():
    _reset()
    svc = FieldMapperService()
    result = svc.domain_import_dict_csv(BROKEN_CSV, SAMPLES)

    assert result["ok"] is False
    # 甲的规则完整，即使同次导入里乙报错，甲也要成功建立字典
    assert result["imported_sources"] == ["甲"]
    assert result["rejected_sources"] == ["乙"]

    # 错误按原文件行号回查：未知转换在第 3 行
    error = result["errors"][0]
    assert error["line"] == 3
    assert error["source"] == "乙"
    assert "未知" in error["message"] or "不在支持清单" in error["message"]

    jia = svc.domain_get_dictionary("甲")
    assert jia["version"] == 1
    assert len(jia["rules"]) == 1
    assert svc.domain_get_dictionary("乙") is None


def test_trial_marks_latest_and_waiting_sources_before_fix():
    _reset()
    svc = FieldMapperService()
    svc.domain_import_dict_csv(BROKEN_CSV, SAMPLES)

    trial = svc.domain_trial_parse(SAMPLES)
    # 修正乙前先试跑：标出哪些来源已用新版、哪些仍等待
    assert trial["latest_sources"] == ["甲"]
    assert trial["waiting_sources"] == ["乙"]
    statuses = {item["source"]: item["status"] for item in trial["sources"]}
    assert statuses["甲"] == "latest"
    assert statuses["乙"] == "missing"
    assert trial["sources"][0]["actual_version"] == 1


def test_reimport_fixed_scope_validates_both_sources_and_maps_outputs():
    _reset()
    svc = FieldMapperService()
    svc.domain_import_dict_csv(BROKEN_CSV, SAMPLES)
    svc.domain_trial_parse(SAMPLES)

    # 修正乙为整数后，重导同一份范围
    result = svc.domain_import_dict_csv(FIXED_CSV, SAMPLES)
    assert result["ok"] is True
    assert sorted(result["imported_sources"]) == ["乙", "甲"]

    # 甲内容未变，版本不升；乙首次建立字典为第 1 版
    reports = {item["source"]: item for item in result["sources"]}
    assert reports["甲"]["version"] == 1
    assert reports["甲"]["version_changed"] is False
    assert reports["乙"]["version"] == 1
    assert reports["乙"]["version_changed"] is True

    # 再用两个来源验证字典与输出
    trial = svc.domain_trial_parse(SAMPLES, expected_versions={"甲": 1, "乙": 1})
    assert trial["latest_sources"] == ["甲", "乙"]
    assert trial["waiting_sources"] == []
    preview = {
        item["source"]: {p["source_field"]: p["converted_value"] for p in item["preview"]}
        for item in trial["sources"]
    }
    assert preview["甲"]["商品名"] == "红茶礼盒"
    assert preview["乙"]["销量"] == 7  # 销量 7 按整数正确取到

    jia_map = svc.domain_map_by_source("甲", {"商品名": "红茶礼盒"})
    yi_map = svc.domain_map_by_source("乙", {"销量": "7", "折扣": "0.88"})
    assert jia_map["ok"] is True
    assert jia_map["target"] == {"product_name": "红茶礼盒"}
    assert yi_map["ok"] is True
    assert yi_map["target"] == {"sales_qty": 7, "discount_rate": 0.88}

    # 甲仍只有一条规则，乙两条
    assert len(svc.domain_get_dictionary("甲")["rules"]) == 1
    assert len(svc.domain_get_dictionary("乙")["rules"]) == 2


def test_confirmed_rule_is_not_duplicated_on_reimport():
    _reset()
    svc = FieldMapperService()
    svc.domain_import_dict_csv(FIXED_CSV, SAMPLES)
    rule, message = svc.domain_confirm_rule("甲", "商品名")
    assert rule is not None and message is None

    # 重导修正后，之前确认过的来源规则不能写两份
    svc.domain_import_dict_csv(FIXED_CSV, SAMPLES)
    jia_rules = svc.domain_get_dictionary("甲")["rules"]
    assert len(jia_rules) == 1
    assert jia_rules[0]["confirmed"] is True

    # 再导一次含改动的内容，确认状态仍随同字段规则保留
    changed = FIXED_CSV.replace(
        "甲,商品名,product_name,string,,", "甲,商品名,product_name,upper,,")
    svc.domain_import_dict_csv(changed, SAMPLES)
    jia_rules = svc.domain_get_dictionary("甲")["rules"]
    assert len(jia_rules) == 1
    assert jia_rules[0]["confirmed"] is True
    assert jia_rules[0]["transform"] == "upper"


def test_trial_detects_stale_dictionary_version():
    _reset()
    svc = FieldMapperService()
    svc.domain_import_dict_csv(FIXED_CSV, SAMPLES)
    # 乙内容变化触发新版本
    changed = FIXED_CSV.replace("乙,销量,sales_qty,int,,", "乙,销量,sales_qty,int,0,")
    svc.domain_import_dict_csv(changed, SAMPLES)

    trial = svc.domain_trial_parse(SAMPLES, expected_versions={"甲": 1, "乙": 1})
    statuses = {item["source"]: item["status"] for item in trial["sources"]}
    assert statuses["甲"] == "latest"
    assert statuses["乙"] == "stale"
    assert trial["waiting_sources"] == ["乙"]


def test_dependency_rules_are_checked_together():
    _reset()
    svc = FieldMapperService()
    # 循环引用
    cycle_csv = """来源,来源字段,目标字段,转换类型,默认取值,依赖字段
丙,a,ta,string,,b
丙,b,tb,string,,a
"""
    result = svc.domain_import_dict_csv(cycle_csv, {"丙": [{"a": "1", "b": "2"}]})
    assert result["ok"] is False
    assert result["rejected_sources"] == ["丙"]
    assert "循环引用" in result["errors"][0]["message"]
    assert result["errors"][0]["line"] == 2

    # 依赖字段不存在
    missing_csv = """来源,来源字段,目标字段,转换类型,默认取值,依赖字段
丁,a,ta,string,,ghost
"""
    result = svc.domain_import_dict_csv(missing_csv, {"丁": [{"a": "1"}]})
    assert result["ok"] is False
    assert result["errors"][0]["line"] == 2
    assert "ghost" in result["errors"][0]["message"]

    # 相互引用合法时，被依赖规则先参与映射
    ok_csv = """来源,来源字段,目标字段,转换类型,默认取值,依赖字段
戊,base,base_v,int,,
戊,ratio,ratio_v,decimal,,base
"""
    result = svc.domain_import_dict_csv(ok_csv, {"戊": [{"base": "3", "ratio": "1.5"}]})
    assert result["ok"] is True
    mapped = svc.domain_map_by_source("戊", {"base": "3", "ratio": "1.5"})
    assert mapped["target"] == {"base_v": 3, "ratio_v": 1.5}


def test_sample_column_and_transform_validation_reports_line_numbers():
    _reset()
    svc = FieldMapperService()
    # 样本里没有该列名
    bad_column = """来源,来源字段,目标字段,转换类型,默认取值,依赖字段
己,名称,tname,string,,
"""
    result = svc.domain_import_dict_csv(bad_column, {"己": [{"商品名": "x"}]})
    assert result["ok"] is False
    assert result["errors"][0]["line"] == 2

    # 样本值无法按 int 转换
    bad_value = """来源,来源字段,目标字段,转换类型,默认取值,依赖字段
庚,销量,sales_qty,int,,
"""
    result = svc.domain_import_dict_csv(bad_value, {"庚": [{"销量": "abc"}]})
    assert result["ok"] is False
    assert result["errors"][0]["line"] == 2
    assert "无法按转换类型" in result["errors"][0]["message"]


def test_structural_csv_errors_block_everything():
    _reset()
    svc = FieldMapperService()
    result = svc.domain_import_dict_csv("来源,来源字段\n辛,x\n", {"辛": [{"x": "1"}]})
    assert result["ok"] is False
    assert result["imported_sources"] == []
    assert "缺少必含列" in result["errors"][0]["message"]

    empty = svc.domain_import_dict_csv("", {})
    assert empty["ok"] is False


def test_map_unknown_source_fails_cleanly():
    _reset()
    svc = FieldMapperService()
    result = svc.domain_map_by_source("不存在的来源", {"x": "1"})
    assert result["ok"] is False
    assert result["target"] == {}


def test_router_service_shares_dictionary_state():
    _reset()
    # 路由层实例与新实例共享类级别字典状态
    FieldMapperService.reset_dictionaries()
    router_service.domain_import_dict_csv(FIXED_CSV, SAMPLES)
    another = FieldMapperService()
    assert another.domain_get_dictionary("甲")["version"] == 1


if __name__ == "__main__":
    import traceback

    failures = 0
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for name, func in tests:
        try:
            func()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001 - 独立运行器需要兜底打印
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
