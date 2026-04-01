# PriceActions Source Import

此目录存放从 `forecho/PriceActions` 导入的原始资料（source-only）。

## 目录
- `docs/`：上游 docs 全量 markdown（原文层）
- `meta/index.json`：文件索引（标题、长度、是否图依赖、哈希）
- `meta/clean_summary.json`：清洗统计摘要
- `meta/term_mapping.md`：术语标准化映射

## 使用边界
- `knowledge/source/` 仅用于参考，不直接用于交易决策。
- 可执行规则必须进入 `knowledge/refined/`。

## 更新方式
```bash
python3 script/import_priceactions_knowledge.py
```
