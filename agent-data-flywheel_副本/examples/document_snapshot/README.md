# 旧文档四条实例

本目录保留旧字段文档中的四条实例底表与已绑定评审，含原始标识符。`dashboard.html` 已使用当前七页模板重新生成，但其数据仍仅代表旧文档实例和文档报告值，不代表本轮真实数据全量分析。

- 查看本轮真实数据的最终界面：打开 [上级默认看板](../dashboard.html)。
- 字段背景：[字段说明与实例](../../数据字段说明与真实实例.md)、[字段字典](../../数据字段字典.md)。
- 本目录的 `analysis.json` 与 `ai_reviews.json` 保留原内容及指纹，不用于评审新数据。

在 Skill 根目录可重新生成这个旧实例页面：

```bash
python3 scripts/merge_reviews.py --analysis examples/document_snapshot/analysis.json --reviews examples/document_snapshot/ai_reviews.json --capabilities references/capabilities.empty.json --out examples/document_snapshot/reviewed_analysis.json
python3 scripts/render_dashboard.py --report examples/document_snapshot/reviewed_analysis.json --out examples/document_snapshot/dashboard.html
```
