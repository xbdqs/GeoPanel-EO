# 0.1.0a1 → 0.1.0a2 配置迁移

0.1.0a2 有意进行一次不向后兼容的 alpha 配置修复，以关闭任意单位标签转换路径。

旧配置：

```yaml
source_unit: mm_month
output_unit: mm_year
temporal_reducer: sum
```

新配置：

```yaml
unit: mm
temporal_support: monthly_total
temporal_reducer: sum
```

年度输出的数值仍为12个月累计量之和，物理单位仍为 `mm`；输出表和 codebook 中的 `period_support` 为 `annual_total`。软件不再通过改变单位字符串暗示已发生物理换算。

温度月均值示例：

```yaml
semantic_type: intensive_state
unit: degC
temporal_support: monthly_mean
spatial_reducer: coverage_weighted_mean
temporal_reducer: mean
temporal_window:
  frequency: annual
```

若矢量配置指定 `entity_name_field`，该字段现在必须存在、每个实体非空，并输出为 `entity_name`。旧的 `panel.time_label` 没有控制任何行为，已从 schema 删除；目标期格式由 `temporal_window.frequency` 唯一决定。
