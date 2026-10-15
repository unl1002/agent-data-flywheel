# 智能体数据飞轮 Skill 使用教程

这个 Skill 让 AI 使用固定脚本处理四类日志，再参与有效问题、能力支持和 BadCase 的语义分析，最后生成可离线打开的 HTML 看板。运行环境只需要 Python 3.9 或以上；无需前端构建、网络连接或模型 API Key。AI 评审由 Codex 等读取 Skill 的助手完成，不是脚本暗中调用模型。

## 给 AI 的使用示例

在支持 Skill 的 AI 工具中调用：

```text
使用 $agent-data-flywheel 分析 ./data 下的四份 JSON。
识别有效问题、不支持的问数问题和 BadCase，说明证据及不确定性，
生成 ./output/dashboard.html 和分析结论。产品能力规则见 ./capabilities.json。
```

当前项目没有完整 JSON 时：

```text
使用 $agent-data-flywheel 的文档快照和真实实例生成数据飞轮看板。
请完成实例的 AI 语义评审，不能把实例结果当作全量统计。
```

## 安装与目录

这是一个可复制的 Skill 文件夹。将整个 `agent-data-flywheel` 文件夹放入 AI 工具支持的技能目录，例如 Codex 的 `~/.codex/skills/`，再新开对话确认可发现 `$agent-data-flywheel`。也可直接让 AI 读取本目录的 `SKILL.md` 执行。

### 将完整文件夹复制到服务器使用

本文件夹是保留原运营摘要、实例标识符及两份原始字段文档的完整本地交付版，未上传到 GitHub。没有四份完整原始 JSON；当前看板使用文档摘要和四份真实实例摘录。

服务器需要 Python 3.9 或以上。核心分析与生成流程只使用 Python 标准库，Node.js 仅用于可选的界面逻辑测试。将整个文件夹复制到服务器，或上传附带 ZIP 后解压：

```bash
unzip agent-data-flywheel-full.zip
cd agent-data-flywheel
python3 -m unittest discover -s tests
```

先重现自带的文档实例看板：

```bash
python3 scripts/merge_reviews.py --analysis examples/analysis.json --reviews examples/ai_reviews.json --capabilities references/capabilities.empty.json --out output/reviewed_analysis.json
python3 scripts/render_dashboard.py --report output/reviewed_analysis.json --out output/dashboard.html
```

将生成的 `output/dashboard.html` 下载到本机后用浏览器打开即可，无需在服务器开放网页端口。要分析新数据，在服务器的 AI 助手中让它先读取仓库根目录的 `SKILL.md`，并提供原始 JSON 目录和能力材料，再执行后续流程。仅运行 Python 脚本不会自动完成 AI 语义评审。

更新时替换 Skill 文件即可。个人原始数据放入 `data/`，运行结果放入 `output/`。随包的两份字段文档保留原文；AI 可按快照的 `raw_locator` 文件名和行号读取完整实例。看板沿用原演示的项目别名，原文档中的名称与标识符均保留。

```text
agent-data-flywheel/
  SKILL.md                         AI 的任务入口
  README.md                        本教程
  数据字段说明与真实实例.md          原始完整文档，保留实例和标识符
  数据字段字典.md                   原始完整字段字典与运营摘要
  agents/openai.yaml               展示名称和默认提示
  scripts/analyze.py               原始数据解析与统计
  scripts/prepare_review.py        生成 AI 评审批次
  scripts/merge_reviews.py         验证证据引用并合并评审
  scripts/render_dashboard.py      生成离线 HTML
  scripts/review_common.py         共享 JSON 契约
  references/                     字段、判定标准、产物契约
  assets/dashboard.html            自包含看板模板
  assets/document_snapshot.json    本项目文档基线与实例
  examples/analysis.json           演示所用的确定性分析底表
  examples/ai_reviews.json         绑定演示数据的实际评审
  examples/dashboard.html         可直接打开的已生成看板
  tests/                          解析和证据约束验证
```

下面的命令均在 Skill 目录下执行。输出目录可以换成任何有权限写入的路径；输入文件不会被修改。

## 直接查看已完成的示例

双击 `examples/dashboard.html` 可查看当前文档基线和四条实例的 AI 评审。示例带有对应的 `analysis.json` 和 `ai_reviews.json`，可重现这次已经完成的合并与渲染：

```bash
python3 scripts/merge_reviews.py --analysis examples/analysis.json --reviews examples/ai_reviews.json --capabilities references/capabilities.empty.json --out output/reviewed_analysis.json
python3 scripts/render_dashboard.py --report output/reviewed_analysis.json --out output/dashboard.html
```

这会重现已保存的实例评审，不会自动评审新数据。新数据需要按下方流程让 AI 重新判定。

## 方式一 使用现有文档快照

```bash
python3 scripts/analyze.py --snapshot --out output
python3 scripts/prepare_review.py --analysis output/analysis.json --out output/review-batches
```

内置快照仅适用于本项目的两份字段文档。42 条反馈、3,280 条护栏、468 条对话和 16,201 条推理是文档报告值；`records` 中只有四份真实实例的摘录。项目名称在演示中使用别名，原始字段文档未被修改。

## 方式二 导入原始 JSON

准备文件：

```text
data/
  用户反馈.json
  安全护栏.json
  对话详情.json
  推理.json
```

```bash
python3 scripts/analyze.py --input-dir data --out output
```

也可指定部分来源或不同文件名：

```bash
python3 scripts/analyze.py --dialogue data/chat.json --feedback data/feedback.json --out output
```

每份原始文件是 JSON 对象数组；单条嵌套 body/detail/responseData 可以为 JSON 字符串。缺少来源时只计算已有来源，不补造其记录。默认每段文本摘录上限 2,000 字符，可以使用 `--max-text` 调整；分析底表保留原文件位置。AI 需要更多证据时读取原始记录，不凭被截断的摘录判失败。

## 提供真实能力范围

`references/capabilities.empty.json` 是空模板，默认没有任何已确认能力。不支持的问题分析需要使用者提供真实能力范围，不能因列表里没写就判“不支持”。例如使用者确认某项限制后，可以建立：

```json
{
  "schema_version": "1.0",
  "rules": [
    {
      "id": "rule-cross-project",
      "title": "跨项目聚合限制",
      "status": "unsupported",
      "description": "当前版本明确不支持一次聚合多个项目的统计数据。",
      "evidence_source": "请替换为实际已确认的产品说明或负责人结论"
    }
  ],
  "evaluations": []
}
```

上例只示范格式，不代表本项目真实能力，不要直接作为评审依据。若没有能力材料，仍可分析有效问题、意图和问题线索，但支持性保持 uncertain。

```bash
python3 scripts/prepare_review.py --analysis output/analysis.json --capabilities capabilities.json --out output/review-batches --batch-size 30
```

## AI 如何参与评审

AI 读取 `manifest.json` 和各批次 JSON，再按 [判定标准](references/review-rubric.md) 逐条填写 `ai_reviews.json`。批次已含问题、回答摘录、节点、信号、证据 ID 和能力材料，**脚本不会替 AI 给出语义标签**。

评审的完整格式见 [产物约定](references/analysis-contract.md)。每个文件包含当前批次的 `analysis_fingerprint`、`capabilities_fingerprint`、scope 和 items；每条包含 record_id、validity、intent、support、badcase、correctness、root_cause、reason、evidence_refs、capability_refs、evaluation_refs、confidence 和 suggested_action。原始数据或能力材料改变后，旧评审会被拒绝；AI 不得把旧指纹改成新指纹来冒充重新评审。

文档快照使用 `scope=document_examples`；抽样或选择记录使用 `selected_records`。默认“问题记录”指对话记录和未关联反馈，不重复计算已关联反馈。只有覆盖当前分析文件的全部这些问题记录后才能使用 `all_question_records`，这仍不代表覆盖未导入来源或历史全部请求。显式选择推理或护栏分析时，按来源单列结论。

可通过 `--limit 100` 先抽样，也可用 `--sources inference` 生成推理专项批次。分批并行时每个 AI 输出自己的评审 JSON，保持同一数据指纹。不要改动批次记录 ID，不复制前一轮结果到新数据中。

## 合并与生成可视化

```bash
python3 scripts/merge_reviews.py --analysis output/analysis.json --reviews output/ai_reviews.json --capabilities references/capabilities.empty.json --out output/reviewed_analysis.json
python3 scripts/render_dashboard.py --report output/reviewed_analysis.json --out output/dashboard.html
```

有多份评审时，在 `--reviews` 后依次列出文件；不要重复评审同一个 record_id。用自有能力文件生成批次时，合并也传入同一份能力文件。`examples/ai_reviews.json` 是已交付演示的实际标签，绑定当时的分析指纹；重新分析后应让 AI 根据新批次重新评审，不能直接套用旧文件。

没有 AI 评审也可以生成统计版：

```bash
python3 scripts/render_dashboard.py --analysis output/analysis.json --out output/dashboard.html
```

统计版明确显示“AI 尚未评审”。零条已确认不支持只表示当前评审范围没有足够依据确认，不等于产品全部支持；没有正确性依据时准确率为空，不能显示 0% 或 100%。

双击 `dashboard.html` 即可查看。页面含总览、问题分析、数据健康、记录详情、搜索和过滤，可导入新的分析 JSON；原始四类日志由 Python 脚本处理。导入纯分析文件会清空旧 AI 标签，避免不同批次混用。导出功能保存当前分析包，不写回原始数据。

## 交付分析结论

AI 最终应给出：数据来源和覆盖范围、有效问题与不支持问数的判定口径、代表性 BadCase 及证据、策略/RAG/模型的待验证改进方向、无法判断的内容和待补数据，以及生成的看板路径。低置信度或无答案的记录留待核验，不为了图表好看补造类别数量。

若要统计真实准确率，在能力文件的 `evaluations` 中加入使用者提供的人工标签或标准答案，并绑定 record_id。若要分析 RAG 效果，需要检索 query、文档、得分、引用和评测信息。修复率需要独立案例生命周期及复测记录；当前 Skill 不会把原始 mark 当作已解决。

## 验证

```bash
python3 -m unittest discover -s tests
```

安装了 Node.js 时，还可以运行 `node tests/test_dashboard.cjs` 验证未评审空态、数据包导入和记录绑定逻辑。Python 分析脚本及生成后的静态看板不依赖 Node.js。

更多参数用各脚本的 `--help` 查看。脚本解析失败或评审校验失败时，先处理错误再生成报告，不将未完成的评审标成已完成。
