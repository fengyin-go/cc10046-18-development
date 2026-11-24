"""字段映射领域服务。

在通用映射方案骨架之上，新增「来源字段字典」能力：
- 按来源（站点）建立来源字段字典，字典规则通过映射字典 CSV 导入；
- 导入时用已采集样本校验列名与转换类型，错误按原文件行号回查；
- 相互引用（依赖字段）的规则会一起做依赖闭环检查；
- 重导按 (来源, 来源字段) 做幂等更新，已确认的来源规则不会写两份；
- 解析试跑消费来源字段字典，并核对实际读到的字典版本；
- 后续映射按来源取出该来源的目标字段。
"""
from __future__ import annotations

import csv
import io
from typing import Any, ClassVar

from app.services.base import BaseModuleService

# 映射字典 CSV 必须给出的列；默认取值、依赖字段允许缺省。
DICT_REQUIRED_COLUMNS = ["来源", "来源字段", "目标字段", "转换类型"]
DICT_OPTIONAL_COLUMNS = ["默认取值", "依赖字段"]
DICT_ALL_COLUMNS = DICT_REQUIRED_COLUMNS + DICT_OPTIONAL_COLUMNS

# 支持的转换类型（含直通：空串/留空表示不做转换）。
SUPPORTED_TRANSFORMS = {"", "string", "int", "decimal", "upper", "lower", "trim"}


class FieldMapperService(BaseModuleService):
    MODULE = "field_mapper"
    ENTITY = "映射方案"
    OPERATION_TERM = "转换"
    LIST_FIELDS = ["方案标识", "来源字段", "目标字段", "转换函数", "默认取值", "映射版本", "适用站点", "依赖字段"]
    REQUIRED_FIELDS = ["方案标识", "来源字段", "目标字段"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "启用映射": "已生效", "停用映射": "已停用"}
    NEGATIVE_ACTIONS = ["停用映射"]

    # 来源字段字典：来源 -> 字典元信息（版本、列名、时间戳等）。
    # 放在类级别，使路由层/链路层按服务注册表拿到的是同一份字典数据。
    _dictionaries: ClassVar[dict[str, dict[str, Any]]] = {}
    # 来源 -> {来源字段 -> 规则}，规则保留导入时的原文件行号。
    _rules: ClassVar[dict[str, dict[str, dict[str, Any]]]] = {}

    # ------------------------------------------------------------------
    # 兼容旧链路的映射方法（data-process 链路与既有测试仍在使用）
    # ------------------------------------------------------------------
    def domain_map_fields(self, source_data: dict, mapping_rules: list[dict]) -> dict:
        """根据映射规则将来源字段转换为目标字段。"""
        result: dict = {}
        for rule in mapping_rules:
            src_field = rule.get("source", "")
            tgt_field = rule.get("target", "")
            default = rule.get("default", "")
            transform = rule.get("transform", "")

            value = source_data.get(src_field, default)
            if transform == "upper":
                value = str(value).upper()
            elif transform == "lower":
                value = str(value).lower()
            elif transform == "int":
                try:
                    value = int(float(value))
                except (ValueError, TypeError):
                    value = 0
            result[tgt_field] = value
        return result

    # ------------------------------------------------------------------
    # 测试/运维辅助：重置来源字段字典
    # ------------------------------------------------------------------
    @classmethod
    def reset_dictionaries(cls) -> None:
        cls._dictionaries = {}
        cls._rules = {}

    # ==================================================================
    # 一、映射字典 CSV 导入
    # ==================================================================
    def domain_import_dict_csv(
        self, csv_text: str, samples: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """导入映射字典 CSV，按来源建立/更新来源字段字典。

        校验规则（任一来源出错都不写入该来源，已有的旧字典原样保留）：
        - 必含列缺失、来源/来源字段/目标字段缺失；
        - 转换类型必须在支持清单内（未知转换直接报错）；
        - 同一来源内来源字段重复；
        - 用已采集样本核对列名是否真实存在、样本值能否按转换类型转换；
        - 依赖字段必须引用同一来源内存在的来源字段，且不能形成循环依赖。
        所有错误都带原文件行号，便于回查修正。
        """
        samples = samples or {}
        parsed, parse_errors, row_errors = self._parse_dict_csv(csv_text)
        if parse_errors:
            return {
                "ok": False,
                "imported_sources": [],
                "rejected_sources": sorted({e["source"] or "(未识别来源)" for e in parse_errors}),
                "sources": [],
                "errors": parse_errors,
                "warnings": [],
            }

        by_source: dict[str, list[dict[str, Any]]] = {}
        for row in parsed:
            by_source.setdefault(row["source"], []).append(row)

        # 行级错误（缺必填、未知转换等）按来源归集，只阻断对应来源，不牵连其他来源
        row_errors_by_source: dict[str, list[dict[str, Any]]] = {}
        orphan_errors: list[dict[str, Any]] = []
        for error in row_errors:
            source = error.get("source")
            if source:
                row_errors_by_source.setdefault(source, []).append(error)
            else:
                orphan_errors.append(error)

        errors: list[dict[str, Any]] = list(orphan_errors)
        warnings: list[dict[str, Any]] = []
        imported_sources: list[str] = []
        rejected_sources: list[str] = []
        source_reports: list[dict[str, Any]] = []

        all_sources = sorted(set(by_source) | set(row_errors_by_source))
        for source in all_sources:
            rows = by_source.get(source, [])
            source_errors = list(row_errors_by_source.get(source, []))
            # 行级错误已定位到具体行号，残缺行不再做样本/依赖校验，避免连带噪声
            if rows and not source_errors:
                source_errors.extend(self._validate_source_rows(source, rows, samples.get(source)))
            if source not in samples:
                warnings.append({
                    "source": source,
                    "line": None,
                    "message": f"来源「{source}」未提供已采集样本，列名与转换类型未做样本校验",
                })
            if source_errors:
                errors.extend(source_errors)
                rejected_sources.append(source)
                source_reports.append({
                    "source": source,
                    "status": "rejected",
                    "rule_count": len(rows),
                    "version": self._dictionaries.get(source, {}).get("version", 0),
                    "errors": source_errors,
                })
                continue
            report = self._commit_source_dict(source, rows)
            imported_sources.append(source)
            source_reports.append(report)

        return {
            "ok": not errors,
            "imported_sources": imported_sources,
            "rejected_sources": rejected_sources,
            "sources": source_reports,
            "errors": errors,
            "warnings": warnings,
        }

    def _parse_dict_csv(
        self, csv_text: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """解析映射字典 CSV，保留每条记录在原文件中的物理行号。

        返回 (记录行, 结构性错误, 行级错误)：结构性错误（空文件、缺列）整体阻断；
        行级错误带行号与来源，由导入流程按来源归集，只阻断出错来源。
        """
        reader = csv.reader(io.StringIO(csv_text or ""))
        try:
            header = next(reader)
        except StopIteration:
            return [], [{"source": None, "line": 0, "message": "映射字典 CSV 内容为空，无法读取表头"}], []
        header = [cell.strip() for cell in header]
        missing_columns = [col for col in DICT_REQUIRED_COLUMNS if col not in header]
        if missing_columns:
            return [], [{
                "source": None,
                "line": 1,
                "message": f"映射字典缺少必含列：{'、'.join(missing_columns)}",
            }], []

        rows: list[dict[str, Any]] = []
        row_errors: list[dict[str, Any]] = []
        for raw in reader:
            if not any(cell.strip() for cell in raw):
                continue  # 跳过空行，行号仍由 csv.reader 的 line_num 真实记录
            line_no = reader.line_num
            if len(raw) < len(header):
                raw = raw + [""] * (len(header) - len(raw))
            record = {header[i]: raw[i].strip() for i in range(len(header))}
            source = record.get("来源", "")
            for column in DICT_REQUIRED_COLUMNS:
                if not record.get(column):
                    row_errors.append({
                        "source": source or None,
                        "line": line_no,
                        "message": f"第 {line_no} 行「{column}」不能为空",
                    })
            transform = record.get("转换类型", "")
            if transform not in SUPPORTED_TRANSFORMS:
                row_errors.append({
                    "source": source or None,
                    "line": line_no,
                    "message": f"第 {line_no} 行转换类型「{transform}」不在支持清单内"
                               f"（支持：{ '、'.join(sorted(t for t in SUPPORTED_TRANSFORMS if t)) }）",
                })
            rows.append({
                "source": source,
                "source_field": record.get("来源字段", ""),
                "target_field": record.get("目标字段", ""),
                "transform": transform,
                "default": record.get("默认取值", ""),
                "depends_on": self._split_dependencies(record.get("依赖字段", "")),
                "line": line_no,
            })
        return rows, [], row_errors

    @staticmethod
    def _split_dependencies(text: str) -> list[str]:
        return [part.strip() for part in str(text or "").replace("，", ",").split(",") if part.strip()]

    def _validate_source_rows(
        self, source: str, rows: list[dict[str, Any]], sample: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """对单个来源的全部规则做集中校验，相互引用的规则在这里一起检查。"""
        errors: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        sample_columns = set(sample[0].keys()) if sample else set()

        for row in rows:
            line = row["line"]
            if row["source_field"] in seen:
                errors.append({
                    "source": source,
                    "line": line,
                    "message": f"第 {line} 行来源字段「{row['source_field']}」与第 "
                               f"{seen[row['source_field']]} 行重复，同一来源的来源字段必须唯一",
                })
            else:
                seen[row["source_field"]] = line

            # 用已采集样本核对列名
            if sample is not None and row["source_field"] not in sample_columns:
                errors.append({
                    "source": source,
                    "line": line,
                    "message": f"第 {line} 行来源字段「{row['source_field']}」在来源「{source}」"
                               f"已采集样本的列名中不存在，样本列：{ '、'.join(sorted(sample_columns)) }",
                })

        # 依赖字段：引用完整性 + 循环依赖，相互引用的规则一起检查
        known_fields = {row["source_field"] for row in rows}
        line_by_field = {row["source_field"]: row["line"] for row in rows}
        for row in rows:
            for dep in row["depends_on"]:
                if dep not in known_fields:
                    errors.append({
                        "source": source,
                        "line": row["line"],
                        "message": f"第 {row['line']} 行依赖字段「{dep}」在来源「{source}」"
                                   f"的字典规则中不存在，无法建立字段引用",
                    })
                if dep == row["source_field"]:
                    errors.append({
                        "source": source,
                        "line": row["line"],
                        "message": f"第 {row['line']} 行字段「{row['source_field']}」依赖自身，构成自引用闭环",
                    })
        cycle = self._detect_dependency_cycle(rows)
        if cycle:
            cycle_lines = " → ".join(
                f"{field}(第 {line_by_field.get(field, '?')} 行)" for field in cycle
            )
            errors.append({
                "source": source,
                "line": line_by_field.get(cycle[0]),
                "message": f"来源「{source}」的依赖字段存在循环引用：{cycle_lines}",
            })

        # 用样本值核对转换类型
        if sample:
            line_by_field = {row["source_field"]: row["line"] for row in rows}
            for row in rows:
                field = row["source_field"]
                if field not in sample_columns:
                    continue
                bad = self._check_sample_transform(
                    source, field, row["transform"], sample, line_by_field[field])
                errors.extend(bad)
        return errors

    @staticmethod
    def _detect_dependency_cycle(rows: list[dict[str, Any]]) -> list[str]:
        graph = {row["source_field"]: [d for d in row["depends_on"] if d != row["source_field"]]
                 for row in rows}
        state: dict[str, int] = {}  # 0=未访问 1=访问中 2=完成
        stack: list[str] = []

        def visit(node: str) -> list[str] | None:
            state[node] = 1
            stack.append(node)
            for nxt in graph.get(node, []):
                if nxt not in graph:
                    continue
                if state.get(nxt) == 1:
                    idx = stack.index(nxt)
                    return stack[idx:] + [nxt]
                if state.get(nxt, 0) == 0:
                    found = visit(nxt)
                    if found:
                        return found
            stack.pop()
            state[node] = 2
            return None

        for node in graph:
            if state.get(node, 0) == 0:
                found = visit(node)
                if found:
                    return found
        return []

    def _check_sample_transform(
        self, source: str, field: str, transform: str,
        sample: list[dict[str, Any]], rule_line: int,
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        for index, record in enumerate(sample, start=1):
            value = record.get(field)
            if value is None or value == "":
                continue
            if transform in ("int", "decimal") and not self._is_convertible(value, transform):
                errors.append({
                    "source": source,
                    "line": rule_line,
                    "message": f"第 {rule_line} 行字段「{field}」的样本第 {index} 行值「{value}」"
                               f"无法按转换类型「{transform}」转换",
                })
        return errors

    @staticmethod
    def _is_convertible(value: Any, transform: str) -> bool:
        try:
            text = str(value).strip()
            if transform == "int":
                int(float(text))
            elif transform == "decimal":
                float(text)
            else:
                return True
        except (ValueError, TypeError):
            return False
        return True

    def _commit_source_dict(self, source: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """幂等提交一个来源的字典：按来源字段更新，已确认规则保留确认状态。"""
        existing = self._rules.setdefault(source, {})
        new_rules: dict[str, dict[str, Any]] = {}
        confirmed_kept = 0
        created = 0
        updated = 0
        for row in rows:
            field = row["source_field"]
            old = existing.get(field)
            confirmed = bool(old and old.get("confirmed"))
            if old:
                updated += 1
                if confirmed:
                    confirmed_kept += 1
            else:
                created += 1
            new_rules[field] = {
                "source": source,
                "source_field": field,
                "target_field": row["target_field"],
                "transform": row["transform"],
                "default": row["default"],
                "depends_on": row["depends_on"],
                "line": row["line"],
                "confirmed": confirmed,
            }

        # 内容指纹：内容没变就不升版本，保证试跑读到的版本仍然有效
        fingerprint = sorted(
            (r["source_field"], r["target_field"], r["transform"], r["default"],
             ",".join(r["depends_on"]))
            for r in new_rules.values()
        )
        old_dict = self._dictionaries.get(source)
        if old_dict and old_dict.get("fingerprint") == fingerprint:
            version = old_dict["version"]
            version_changed = False
        else:
            version = (old_dict or {}).get("version", 0) + 1
            version_changed = True

        self._rules[source] = new_rules
        self._dictionaries[source] = {
            "source": source,
            "version": version,
            "columns": sorted(new_rules.keys()),
            "rule_count": len(new_rules),
            "confirmed_count": sum(1 for r in new_rules.values() if r["confirmed"]),
            "version_changed": version_changed,
            "fingerprint": fingerprint,
        }
        return {
            "source": source,
            "status": "imported",
            "version": version,
            "version_changed": version_changed,
            "rule_count": len(new_rules),
            "created": created,
            "updated": updated,
            "confirmed_kept": confirmed_kept,
            "errors": [],
        }

    # ==================================================================
    # 二、解析试跑：消费来源字段字典，核对实际读到的字典版本
    # ==================================================================
    def domain_trial_parse(
        self, samples: dict[str, list[dict[str, Any]]],
        expected_versions: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """解析试跑：逐来源核对字典版本、样本列名与转换类型。

        状态：
        - latest：试跑基于该来源当前最新版字典（已用新版）；
        - stale：调用方期望的字典版本落后于当前版本（仍按旧理解等待修正）；
        - missing：该来源尚未建立字典，仍在等待导入。
        """
        expected_versions = expected_versions or {}
        reports: list[dict[str, Any]] = []
        latest_sources: list[str] = []
        waiting_sources: list[str] = []

        for source, sample_rows in samples.items():
            dictionary = self._dictionaries.get(source)
            rules = self._rules.get(source)
            if not dictionary or not rules:
                waiting_sources.append(source)
                reports.append({
                    "source": source,
                    "status": "missing",
                    "actual_version": None,
                    "expected_version": expected_versions.get(source),
                    "message": f"来源「{source}」尚未建立来源字段字典，等待导入修正",
                    "rule_count": 0,
                    "errors": [],
                    "warnings": [],
                    "preview": [],
                })
                continue

            expected = expected_versions.get(source)
            if expected is not None and expected != dictionary["version"]:
                status = "stale"
                waiting_sources.append(source)
                version_message = (
                    f"试跑基于字典第 {expected} 版的理解已过期，当前实际为第 "
                    f"{dictionary['version']} 版，等待按新版重新试跑"
                )
            else:
                status = "latest"
                latest_sources.append(source)
                version_message = f"已按来源「{source}」第 {dictionary['version']} 版字段字典试跑"

            errors, warnings, preview = self._trial_run_rules(source, list(rules.values()), sample_rows)
            reports.append({
                "source": source,
                "status": status,
                "actual_version": dictionary["version"],
                "expected_version": expected,
                "message": version_message,
                "rule_count": len(rules),
                "errors": errors,
                "warnings": warnings,
                "preview": preview,
            })

        return {
            "ok": all(report["status"] != "missing" and not report["errors"] for report in reports),
            "latest_sources": latest_sources,
            "waiting_sources": waiting_sources,
            "sources": reports,
        }

    def _trial_run_rules(
        self, source: str, rules: list[dict[str, Any]], sample_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        preview: list[dict[str, Any]] = []
        if not sample_rows:
            warnings.append({"source": source, "line": None,
                             "message": f"来源「{source}」没有可试跑的样本行"})
            return errors, warnings, preview

        sample_columns = set(sample_rows[0].keys())
        ordered = self._order_rules_by_dependency(rules)
        first_row = sample_rows[0]
        for rule in ordered:
            field = rule["source_field"]
            line = rule["line"]
            if field not in sample_columns:
                errors.append({
                    "source": source,
                    "line": line,
                    "message": f"第 {line} 行来源字段「{field}」在实际样本列名中不存在",
                })
                continue
            raw_value = first_row.get(field, rule["default"])
            try:
                converted = self._apply_transform(raw_value, rule["transform"], rule["default"])
                preview.append({
                    "source": source,
                    "line": line,
                    "source_field": field,
                    "target_field": rule["target_field"],
                    "transform": rule["transform"] or "string",
                    "raw_value": raw_value,
                    "converted_value": converted,
                    "confirmed": rule["confirmed"],
                })
            except (ValueError, TypeError) as exc:
                errors.append({
                    "source": source,
                    "line": line,
                    "message": f"第 {line} 行字段「{field}」转换类型「{rule['transform']}」"
                               f"试跑失败：{exc}",
                })
        return errors, warnings, preview

    # ==================================================================
    # 三、按来源映射：交给后续映射得到该来源的目标字段
    # ==================================================================
    def domain_map_by_source(self, source: str, source_data: dict[str, Any]) -> dict[str, Any]:
        dictionary = self._dictionaries.get(source)
        rules = self._rules.get(source)
        if not dictionary or not rules:
            return {
                "ok": False,
                "source": source,
                "version": None,
                "message": f"来源「{source}」尚未建立来源字段字典，无法执行字段映射",
                "target": {},
                "errors": [{"source": source, "line": None,
                            "message": f"来源「{source}」缺少字段字典"}],
            }

        ordered = self._order_rules_by_dependency(list(rules.values()))
        target: dict[str, Any] = {}
        errors: list[dict[str, Any]] = []
        for rule in ordered:
            field = rule["source_field"]
            present = field in source_data and source_data[field] != ""
            value = source_data[field] if present else rule["default"]
            try:
                target[rule["target_field"]] = self._apply_transform(
                    value, rule["transform"], rule["default"])
            except (ValueError, TypeError) as exc:
                errors.append({
                    "source": source,
                    "line": rule["line"],
                    "message": f"第 {rule['line']} 行字段「{field}」按「{rule['transform']}」"
                               f"转换失败：{exc}",
                })
        return {
            "ok": not errors,
            "source": source,
            "version": dictionary["version"],
            "message": f"已按来源「{source}」第 {dictionary['version']} 版字典完成映射",
            "target": target,
            "errors": errors,
        }

    # ==================================================================
    # 四、字典查询与规则确认（重导后已确认规则不写两份）
    # ==================================================================
    def domain_list_dictionaries(self) -> list[dict[str, Any]]:
        return [dict(info) for info in sorted(self._dictionaries.values(), key=lambda item: item["source"])]

    def domain_get_dictionary(self, source: str) -> dict[str, Any] | None:
        dictionary = self._dictionaries.get(source)
        if dictionary is None:
            return None
        info = dict(dictionary)
        info["rules"] = [dict(rule) for rule in sorted(
            self._rules.get(source, {}).values(), key=lambda item: item["line"])]
        return info

    def domain_confirm_rule(self, source: str, source_field: str) -> tuple[dict[str, Any] | None, str | None]:
        rule = self._rules.get(source, {}).get(source_field)
        if rule is None:
            return None, f"来源「{source}」的来源字段「{source_field}」规则不存在"
        rule["confirmed"] = True
        dictionary = self._dictionaries[source]
        dictionary["confirmed_count"] = sum(
            1 for item in self._rules[source].values() if item["confirmed"])
        return dict(rule), None

    # ==================================================================
    # 共享小工具
    # ==================================================================
    def _order_rules_by_dependency(self, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按依赖字段做拓扑排序：被依赖的规则先执行，相互引用可一起落地。"""
        by_field = {rule["source_field"]: rule for rule in rules}
        ordered: list[dict[str, Any]] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(rule: dict[str, Any]) -> None:
            field = rule["source_field"]
            if field in visited:
                return
            if field in visiting:  # 有环时退化为原顺序，不在映射阶段再次阻断
                return
            visiting.add(field)
            for dep in rule["depends_on"]:
                dep_rule = by_field.get(dep)
                if dep_rule is not None:
                    visit(dep_rule)
            visiting.discard(field)
            visited.add(field)
            ordered.append(rule)

        for rule in sorted(rules, key=lambda item: item["line"]):
            visit(rule)
        return ordered

    @staticmethod
    def _apply_transform(value: Any, transform: str, default: Any = "") -> Any:
        if value is None or value == "":
            value = default
        if transform == "" or transform == "string":
            return "" if value is None else str(value)
        text = str(value).strip()
        if transform == "int":
            return int(float(text))
        if transform == "decimal":
            return float(text)
        if transform == "upper":
            return text.upper()
        if transform == "lower":
            return text.lower()
        if transform == "trim":
            return text
        # 未登记的转换不允许静默吞掉，交由导入/试跑阶段按行号报错
        raise ValueError(f"未知转换类型「{transform}」")
