# 智能体数据飞轮 V2.1 使用教程

本版默认分析四份原始 JSON，生成确定性统计、逐条 AI 评审和可追溯看板。每个统计均可查看公式、参与及排除记录，再定位原始文件。Python 3.9+ 即可运行，核心脚本只使用标准库；不需要模型 API Key、网络或全局安装。AI 评审由读取 Skill 的助手完成，脚本不调用外部模型。

当前真实数据的完整分析底表与评审结果位于本目录外的 `../分析结果/`。随包的原字段文档、标识符、`assets/document_snapshot.json` 和旧实例均保留。本版及真实结果仅本地使用，不上传 GitHub。

## 先预览最终界面

直接打开 [examples/dashboard.html](examples/dashboard.html)。这是当前七页模板生成的真实数据冻结看板，含468条对话、本次响应支持率60.90%、项目月度统计及全部统计血缘。无需先运行分析或AI评审即可预览。它保留运营摘要和原标识符，因此压缩包比只含脚本的版本更大。

`assets/dashboard.html` 是渲染模板，含数据占位符，用于脚本生成页面；预览成品使用 `examples/dashboard.html`。完整原文仍需提供匹配的四份原始JSON，并按下文启动来源服务。冻结示例和网页导出不能替代 canonical 底表或作为新一轮评审依据。

旧版四条文档实例已移到 [examples/document_snapshot/](examples/document_snapshot/)，其中的 `dashboard.html` 也使用当前七页界面，并明确保留文档实例范围。其统计不代表本轮真实数据。V2.1同步了示例和模板，默认入口不再使用旧三页界面。

## 让 AI 执行

直接让助手读取本目录 `SKILL.md`，不必安装到全局技能目录：

```text
读取 agent-data-flywheel/SKILL.md，分析四份原始 JSON。
按 compact 批次逐条评审有效性、实际响应支持、价值机会与 BadCase，
生成带全部统计血缘的看板。没有产品能力规则或外部评测标签时保留未知。
所有结果写入 ../分析结果，不上传数据。
```

## 输入与目录

以下命令在本 Skill 目录执行。当前原始文件目录为 `../../真实数据/下载文件/数据飞轮/`，也可替换为自己的目录：

```text
数据飞轮/
  用户反馈.json
  安全护栏.json
  对话详情.json
  推理.json
```

每个文件是 JSON 数组；嵌套 `body/detail/responseData` 可以是 JSON 字符串。缺少来源时只分析已有来源；脚本也支持 `--dialogue 路径`、`--feedback 路径` 等单独指定文件。输入不被修改。

```text
agent-data-flywheel/
  SKILL.md / README.md             入口与教程
  数据字段说明与真实实例.md          保留原文与标识符的旧字段材料
  数据字段字典.md                   保留原文的旧字段材料
  scripts/                        分析、批次、合并、渲染、原文读取
  references/                     字段、评审标准与 JSON 契约
  assets/dashboard.html            七页看板模板
  examples/dashboard.html         当前七页界面的真实数据冻结预览
  examples/document_snapshot/     旧四条文档实例及当前界面的快照预览
../分析结果/
  analysis.json                   规范分析底表，保留供后续校验
  review-batches/                  当前指纹绑定的评审批次
  ai-reviews/                      AI 逐批写出的评审
  reviewed_analysis.json           校验后的完整分析与评审
  dashboard.html                  看板展示投影
```

## 1. 分析原始数据

```bash
python3 scripts/analyze.py --input-dir "../../真实数据/下载文件/数据飞轮" --out "../分析结果"
```

`analysis.json` 包含来源数量、去重结果、质量问题、记录、月度使用及全部统计血缘。默认文本摘录上限为 2,000 字符，可用 `--max-text` 调整。原文件 SHA256、每个原始对象的索引和行指纹保留，截断不会变成失败标签。

## 2. 生成 compact 评审批次

```bash
python3 scripts/prepare_review.py --analysis "../分析结果/analysis.json" --capabilities references/capabilities.empty.json --compact --batch-size 30 --out "../分析结果/review-batches"
```

默认集合是有问题文本的对话和未关联反馈；已确定关联的反馈不重复评为另一问题。新数据的默认待评数量以 `manifest.json` 为准。`--compact` 优先提供 PIU 末端响应、拒答、执行错误和诊断结果，减少重复进度快照；证据不足时仍须按定位读取原文。

本轮实例为 468 条对话加 41 条孤立反馈，共 509 条默认问题记录，已全部评审。`coverage.available/ratio` 的分母是全部规范化来源记录（还含独立推理与护栏日志），不能当成默认问题集的评审覆盖率；默认问题覆盖应与 manifest 的待评 ID 集合核对。

`references/capabilities.empty.json` 不含任何能力规则或评测标签。若使用者另行提供真实依据，准备批次与合并时必须使用同一份文件，格式见 [产物契约](references/analysis-contract.md)。缺少规则不代表产品不支持。

## 3. AI 逐批评审

读取 `manifest.json`、各 `batch-*.json` 和 [评审标准](references/review-rubric.md)，逐条阅读问题及关键回答证据，写入 `../分析结果/ai-reviews/review-0001.json` 等文件。脚本不自动判语义，不能用关键词批量替代 AI 判断。

- 每份评审从当前批次原样复制 `analysis_fingerprint`、`capabilities_fingerprint`，包含 `schema_version: "1.0"`、`scope: "selected_records"` 和 `items`。
- 每条填写旧必填字段及 V2 的 `response_support`、`response_evidence_refs`、`unsupported_reason`、`value_level`、`value_reason`、`opportunity_type`。完整示例见 [JSON 契约](references/analysis-contract.md)。所有证据必须来自本记录已有 evidence ID。
- `response_gap_type` 在兼容旧数据的 JSON 契约中可选；新一轮评审应对未支持/部分支持问题逐条填写，用于原因分布，证据不足时用 unknown。它与待核验 root_cause 分开，不是确认根因。
- `support` 是有规则依据的产品能力结论；无规则时为 `uncertain`，无效问题可为 `not_applicable`。`response_support` 是本次实际回答是否覆盖主要诉求；支持不代表正确，也不是产品能力承诺。
- 无回答、截断或上下文不足保留 `uncertain`。HTTP 200、END、finished 不单独证明支持或正确。差评、标记或技术失败可为候选 BadCase；确认错误与 `confirmed` 需要使用者提供的记录级外部评测依据。
- 价值评价问题自身用途：对象与指标/动作明确、对运维决策或故障处理有直接用途为 high；目的可辨但范围或条件待补全为 medium；无业务内容为 none；无法识别为 uncertain。正常完成的明确业务问题也可为 high。写清价值原因、机会类型和可执行建议，不能按“是否值得修复或做回归”决定价值。

各批不能重叠 `record_id`。`--limit 100` 只选择当前顺序的前 100 条，属于有序截取，不是随机抽样；可用 `--sources inference` 做专项分析。结论须注明来源、选择方式及覆盖范围。`all_question_records` 仅能在最终合并集合覆盖当前底表全部默认问题记录时声明，不等于覆盖未导入数据。`completed` 只表示已有评审完成，不保证全量覆盖。

## 4. 校验、合并与渲染

AI 写完全部批次后执行；通配符只匹配本轮生成的评审文件：

```bash
python3 scripts/merge_reviews.py --analysis "../分析结果/analysis.json" --reviews ../分析结果/ai-reviews/review-*.json --capabilities references/capabilities.empty.json --out "../分析结果/reviewed_analysis.json"
python3 scripts/render_dashboard.py --report "../分析结果/reviewed_analysis.json" --out "../分析结果/dashboard.html"
```

合并会检查双指纹、重复 ID、证据、能力规则及评测约束。渲染会再次校验并重算评审指标。原始分析或依据改变后，重新生成批次并评审，不能替换旧指纹冒充重新评审。

大看板会将 JSON 以 gzip 内嵌，现代浏览器通过 DecompressionStream 本地解压，无需联网。旧浏览器可在渲染命令追加 `--uncompressed`，代价是 HTML 文件更大。

无 AI 评审时可先生成统计版，页面会明确显示未评审：

```bash
python3 scripts/render_dashboard.py --analysis "../分析结果/analysis.json" --out "../分析结果/dashboard.html"
```

看板共七页，顺序为总览、项目月度使用、指标字典与计算依据、问题分析、不支持问题、价值问题、数据健康。“指标字典与计算依据”是位于“项目月度使用”之后的独立页面。价值表保留项目、问题、请求时间、价值机会、原因、建议与来源；问题详情展示实际回答和 AI 证据。缺少评测金标时准确率为 `null`，不显示成 0% 或 100%。

## 5. 查看完整原文

双击 HTML 可离线查看摘录、证据、公式和来源定位；完整原文入口会提示启动本机服务。需要逐行回查原始文件时启动：

```bash
python3 scripts/serve_dashboard.py --report "../分析结果/reviewed_analysis.json" --html "../分析结果/dashboard.html" --data-dir "../../真实数据/下载文件/数据飞轮" --port 8765
```

然后访问 `http://127.0.0.1:8765`。服务只绑定 `127.0.0.1`，限制 Host/Origin；核对看板数据指纹并校验完整原文件 SHA256 后才返回选定原始对象。文件改变会拒绝读取。Ctrl+C 停止。服务器运行时可在本机另开终端通过 SSH 转发：

```bash
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器
```

也可用 CLI 提取；把 `--ref` 换为血缘面板里的原始行、记录或节点引用：

```bash
python3 scripts/extract_source.py --analysis "../分析结果/reviewed_analysis.json" --data-dir "../../真实数据/下载文件/数据飞轮" --ref "raw:dialogue:0" --out "../分析结果/source-dialogue-0.json"
```

## 指标怎样解释

主指标、主月度趋势和不支持/价值分布只含对话；孤立反馈按来源单列。全局 `review.metrics` 仍包含评审过的孤立反馈等记录，不能替代 `review.primary_metrics` 作为对话主结论。

主响应支持率 = 已评审有效对话中 `response_support=supported` 的数量 / 全部已评审有效对话数量，未知也在分母。另有“可判响应样本中的支持率”，其分母仅 supported/unsupported/partial，必须带样本量，不替代主支持率。产品能力不支持与本次响应不支持分开呈现。

月份按北京时间 UTC+8：对话依次使用 `askTime → questionTime → updateTime`，具体字段保存在每条 `time_field`；反馈用 `createTime`，其他来源用 `updateTime`。项目数、会话数不是人数；没有稳定提问人 ID，不能计算用户数、DAU、MAU 或留存。

点击任意统计、分布或月份单元格：**公式 → 成员/分子/分母/排除记录 → 记录、证据和文件索引 → 原文**。P95 含测量样本、rank 与选中记录；去重计数含 `group_values`，成员数可以大于去重值。四类来源不能拼成未经验证的业务漏斗。

HTML 会保留全部统计成员及 AI 引用证据，其他长正文只保留展示摘录。页面导出的 JSON 是展示投影，不是 canonical analysis，不能用来合并评审；始终保留并使用原 `analysis.json` 和完整 `reviewed_analysis.json`。

## 旧文档示例与验证

仅在明确复现旧文档示例时使用 `analyze.py --snapshot`；其少量实例及文档报告值不能冒充当前四份原始 JSON 的全量成员。旧分析与评审位于 `examples/document_snapshot/`，原始字段文档仍保留，不将旧评审套入真实数据。默认 `examples/dashboard.html` 是本轮真实数据的展示投影，与该旧快照分开。

可独立只读核验冻结底表、报告的所有 trace，以及原始文件哈希、对象与起始行号；以下命令只另写审计结果：

```bash
python3 scripts/verify_lineage.py --analysis "../分析结果/analysis.json" --report "../分析结果/reviewed_analysis.json" --out "../分析结果/lineage-audit.json"
```

需要原始文件位于底表记录的路径；`--skip-source-files` 只跳过源文件核验，不代表已验证源哈希或行号。

```bash
python3 -m unittest discover -s tests
```

Node.js 18+ 仅用于可选的 `node --test tests/*.cjs` 界面测试，分析和看板生成不依赖 Node。各脚本用 `--help` 查看参数。交付时报告数据与评审覆盖、主指标口径、代表性证据、未知与待补依据，以及 `analysis.json`、评审文件和看板路径。
