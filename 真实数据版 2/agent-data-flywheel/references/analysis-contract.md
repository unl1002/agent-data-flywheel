# 分析与评审产物契约（V2）

脚本使用 Python 3.9+ 标准库，不调用模型 API。流程为 analyze → prepare_review --compact → AI 逐批写评审 → merge → render；原文回查使用 serve_dashboard 或 extract_source。V2 扩展字段保持 `schema_version: "1.0"`，保留旧产物兼容；新分析和评审按下述约定生成。

## canonical analysis.json

顶层字段：`schema_version`、`provenance`、`sources`、`metrics`、`distributions`、`records`、`quality_issues`，原始数据 V2 另有 `source_rows`、`lineage`、`usage`。

| 字段 | 含义 |
|---|---|
| `provenance` | `mode=raw_json/document_snapshot`、generated_at、source_files、notes；原始数据的 `files[source]` 含 path/name/sha256/bytes/record_count，sha256 为完整文件字节指纹 |
| `sources` | feedback/guardrail/dialogue/inference 各含 status、records、raw_records、duplicates、errors、trace_ids；errors 是 severity=error 的质量问题数，不是业务失败数 |
| `metrics` | `{label,value,unit,source,basis,numerator?,denominator?,reason?,trace_id}`；比例 0–1，单位 ratio；计数 count，耗时 ms；未知为 null |
| `distributions` | scenes/request_models/http_status/response_formats/recipe_dialogue_coverage 等，数组项 `{name,value,trace_id,...}` |
| `quality_issues` | `{code,severity,message,source?,record_id?,raw_ref?,raw_locator?}`；没有定位时不猜记录 |
| `lineage` | trace_id 到计算公式、完整成员及排除记录的映射 |
| `usage` | timezone、months、source_months、project_rows、metrics、monthly_metrics、unknown_time、notes；单元格或指标指向 lineage |

原有指标键继续保留：dialogue_records、answer_nodes、conversation_count、scene_missing_rate、end_marker_coverage、feedback_records、rated_feedback、negative_feedback_rate、reason_coverage、feedback_match_count、feedback_link_rate、dialogue_feedback_coverage、guardrail_records、guardrail_block_rate、guardrail_question_block_rate、inference_records、inference_http_200_rate、model_route_difference_rate、dialogue_cost_p95_ms、first_token_p95_ms、accuracy、rag_recall、resolution_rate 等。精确口径以各项 lineage 为准；缺少评测、检索金标或复测闭环时相关指标为 null。

### 原始对象、记录、节点

`source_rows` 覆盖输入数组的每个对象，包括去重淘汰项。每项含 `ref="raw:<source>:<index>"`、source、零基 index、原文件起始 line、sha256（对象规范 JSON 的行指纹）、log_retained、project_id/project、time，可含 retained_record_id、duplicate_of。原始数组索引不同于文件文本行号；记录行指纹不同于完整文件字节指纹。

规范化 `records` 必需 id、source、question、answer、evidence、signals。常见扩展为 chat_id、conversation_id、request_id、project_id、project、product、version、time、time_field、scene、feedback、mark、reason、response_status、cost_ms、requested_model、actual_model、nodes、response_facts、linked_dialogue_id、raw_locator、source_ref、raw_sha256、locator_steps、truncated。id 在底表内唯一；反馈不按 chatId 去重。

`raw_locator={path,line,index}` 定位原始对象；`source_ref` 连接 source_rows。`locator_steps` 是按序执行的 pointer/decode 步骤，跨越 JSON 字符串边界。例如先取 `/71`，再取 `/body` 并 JSON 解码，再取 `/detail` 并 JSON 解码，才能查看 `/answers/31/data`。不能把 `/71/body/detail/answers/31/data` 当作可直接访问原文件的单个 JSONPointer。

`evidence=[{id,path,excerpt,truncated?}]`，ID 为本记录 `:e序号`；path 是解析字段路径，excerpt 是有上限的数据摘录。AI 只引用当前记录已有 ID。`response_facts` 含 kind/path/text/evidence_id，承载 PIU 解析后的回答、错误、结果及状态；compact 批次选取关键事实并保留所需证据，不改 canonical 底表或其指纹。

`nodes` 包含 time、level、type、status、recipe、cost_ms、text、truncated、original_index，按 answerTime 排序但保留原始数组索引。节点成员引用为 `<record.id>#node:<original_index>`；护栏检查为 `<record.id>#check:<原始检查索引>`。不能以排序位置替代源位置，不在首次 END 处截断后续异步节点。

### 时间与使用统计

`time` 为毫秒。对话优先 `body.detail.askTime`，其次 questionTime，最后外层 updateTime；每条 `time_field` 保存实际采用字段。反馈为 createTime，护栏与推理为 updateTime。分月使用 Asia/Shanghai（UTC+8），不可分月值单列 unknown，不按导出文件名猜日期。

项目月度以有效业务详情为基础；占位或缺失 project_id 单列，不充作已识别项目。conversation_id 表示会话，project_id/tenant_id/user_type 不是稳定提问人 ID；无法计算真实人数、DAU、MAU 或留存。

## 全部统计的 lineage

统计卡片、来源数量、分布、项目×月份和 AI 指标均指向 trace_id。通用结构为：

```json
{
  "label": "指标名称",
  "value": 0.5,
  "unit": "ratio",
  "formula": "明确的选择条件与计算公式",
  "member_refs": ["记录或原始行引用"],
  "numerator_refs": ["分子记录引用"],
  "denominator_refs": ["分母记录引用"],
  "excluded_refs": ["当前口径排除的记录引用"]
}
```

`reason` 解释不可计算或成员缺口；不得从汇总值反造成员。比例的 member_refs 通常是分母；计数为参与集合。成员可以是记录、raw 行、node 或 check。错误项可含 issue_indexes；同一记录多个错误不能被误读为多个业务失败。

P95 lineage 另有 `sample_values={记录ID:测量值}`、`rank=ceil(0.95*N)`、selected_ref，排除无效测量；首 token P95 只用流式正值。distinct 项目/会话指标另有 `group_values={记录ID:分组值}`；成员条数可以大于 distinct 值。Recipe 覆盖按详情去重，一个详情可属于多个 Recipe，不可相加为问题量。

看板路径为：**统计 → 公式/成员/分子/分母/排除 → 记录、证据、文件与数组索引 → 完整原始对象**。旧文档快照没有全量原始行，保留报告值与缺口说明，不用少量实例补出成员。

## 批次与 ai_reviews.json

prepare_review 输出 manifest 和 batch-0001.json 等。默认问题集为有问题文本的对话及未关联反馈；显式 --sources 可以生成其他来源专项批次。--compact 保留关键响应与被引用证据，缺少上下文仍需回查。--limit 只截取当前顺序前 N 条，不是随机抽样。

评审外壳示例仅演示结构，以下 ID/标签不可直接套入实际批次：

```json
{
  "schema_version": "1.0",
  "scope": "selected_records",
  "analysis_fingerprint": "原样复制当前批次指纹",
  "capabilities_fingerprint": "原样复制当前能力及评测依据指纹",
  "items": [
    {
      "record_id": "dialogue:example",
      "validity": "valid",
      "intent": ["report"],
      "support": "uncertain",
      "response_support": "uncertain",
      "response_evidence_refs": [],
      "unsupported_reason": "",
      "value_level": "high",
      "value_reason": "报告范围明确，可建立报告输出验收样本。",
      "opportunity_type": "report",
      "badcase": "uncertain",
      "correctness": "uncertain",
      "root_cause": "unknown",
      "reason": "问题明确，但现有摘录不足以判断响应覆盖，亦无能力规则和正确性金标。",
      "evidence_refs": ["dialogue:example:e0"],
      "capability_refs": [],
      "evaluation_refs": [],
      "confidence": 0.7,
      "suggested_action": "补齐报告输出，并按请求中的范围核验覆盖。"
    }
  ]
}
```

旧字段枚举：validity=valid/invalid/uncertain；intent 为非空数组，值为 data_query/diagnosis/knowledge/report/conversation/other/uncertain；support=supported/unsupported/partial/uncertain/not_applicable；badcase=confirmed/candidate/not_observed/uncertain；correctness=correct/incorrect/uncertain/not_applicable；root_cause=strategy/rag/model/data/product_gap/safety/unknown。reason 非空，confidence 是 0–1 有限数；evidence_refs 必须是本记录非空有效引用。

V2 新评审显式填写全部六字段：

| 字段 | 约束与含义 |
|---|---|
| response_support | supported/unsupported/partial/uncertain/not_applicable；本次实际响应是否执行或回答主要诉求，不是正确性或能力承诺 |
| response_evidence_refs | 本记录已有 evidence ID 数组；supported/unsupported/partial 必须非空，不能只用问题或 finished 证明支持 |
| unsupported_reason | 字符串；unsupported/partial 时具体说明拒答、执行失败或缺失诉求，其余可空 |
| value_level | high/medium/none/uncertain；按问题本身业务用途评价，与回答是否完成、是否需要修复分开 |
| value_reason | 字符串；high/medium 写业务价值依据 |
| opportunity_type | 字符串，说明待跟进方向，不是确认根因；如 intent、query_execution、sql_generation、scope_clarification、knowledge、report、unknown |

完全没有六字段的旧 item 仍兼容；出现任一字段就必须齐全。旧 item 响应支持计入未知，新 V2 不得借兼容省略判断。invalid 不能有确定 response_support 或 support；使用 uncertain/not_applicable。高/中价值还须提供可执行 suggested_action。

high 表示对象、指标/动作明确且有直接运维用途；medium 表示目的可辨但范围/条件需补全；none 表示无业务内容；uncertain 表示无法辨识。正常完成的明确问题也可 high，不能把“可做回归样本”作为 medium 的判定依据。

可选 `response_gap_type` 用于有效但未支持/部分支持响应的缺口分布，枚举为 field_mapping/execution_failure/scope_clarification/intent_deviation/content_uncovered/safety_boundary/semantic_mismatch/unknown。逐条依据实际回答语义标注，不按关键词自动归类；它说明字段映射、执行、范围澄清、意图偏离、内容覆盖、安全边界或语义匹配缺口，不是已确认根因，与 root_cause 分开。

scope：旧快照只用 document_examples；原始数据按批/抽样用 selected_records；只有最终合并记录覆盖当前 canonical 底表全部默认问题集时才可声明 all_question_records。指纹为规范化 JSON SHA256；复制本轮双指纹，不改原始分析或复用旧批次冒充评审。

### 能力与外部评测依据

能力文件为 `{schema_version:"1.0",rules:[],evaluations:[]}`。每条依据有唯一 id 和非空 evidence_source。rules 的 status 为 supported/unsupported/partial，另可含 title、description、版本与范围说明。确定 support 必须引用匹配状态且实际范围适用的规则；没有规则保持 uncertain。

外部 evaluations 的 kind 为 human_label/reference_answer，绑定 record_id，correctness 为 correct/incorrect，保留 evidence_source 与标签或答案内容。确定 correctness 需 evaluation_refs，confirmed BadCase 还必须有 incorrect 外部依据。不能自行造金标来证明自己的判断。无依据时 accuracy=null；差评、mark 或技术失败只能成为待核验候选。

## reviewed_analysis.json

输出为 `{schema_version:"1.0",analysis:完整底表,review:{...}}`，不修改 analysis。review 保留 status、scope、items、双指纹、capabilities、coverage、notes，以及：

| 字段 | 统计范围 |
|---|---|
| primary_source | 固定 dialogue |
| primary_metrics | 仅对话，主口径，等于 by_source_metrics.dialogue |
| by_source_metrics | dialogue/feedback/guardrail/inference 分别计算 |
| primary_monthly | 仅对话，按 UTC+8 月份列 `{month,metrics}` |
| primary_distributions | 仅对话的 unsupported_intents、unsupported_reasons、unsupported_causes、opportunity_types，每组带 trace_id |
| metrics / monthly | 全部已评审来源，含孤立反馈；保留用于审计，不替代对话主口径 |
| lineage | 所有评审指标/分布 trace_id 的完整成员依据 |

coverage 的 available 是全部规范化记录，by_source 显示各来源 reviewed/available；不是默认问题集的独立业务问题分母。status=completed 不意味着全量覆盖。

所有范围使用相同 metric 键，比例分母为零返回 null：

| 键 | 口径 |
|---|---|
| reviewed_records / valid_questions | 已评审记录数 / 其中 validity=valid 数 |
| valid_question_rate | valid / 该范围全部已评审记录 |
| response_supported / response_unsupported / response_partial | valid 中对应 response_support 数 |
| response_unknown | valid 中 uncertain、not_applicable 或旧 item 缺字段数 |
| response_support_rate | valid 且 supported / 全部已评审 valid，未知保留在分母；primary_metrics 的该项是主支持率 |
| response_judged_support_rate | 同一分子 / valid 中 supported、unsupported、partial；仅为可判响应样本率，需同时展示样本量 |
| unsupported_data_queries / partial_data_queries | valid 且 intent 含 data_query 且产品 support=unsupported/partial，不等于本次响应不支持 |
| value_questions | valid 且 value_level 为 high 或 medium |
| confirmed_badcases / candidate_badcases | 对应 badcase 标签计数 |
| accuracy | 有记录级外部评测的 correct / 已评测 correct+incorrect，无金标为 null |

unsupported_intents/unsupported_reasons/unsupported_causes 只取有效对话的 response_support=unsupported/partial；分别按 intent、response_gap_type、root_cause 分组，缺少可选缺口字段时归 unknown。opportunity_types 只取有效对话的 high/medium。意图可多选，不同意图数不可相加为独立问题总量。响应缺口不是根因确认，root_cause 仍是待核验归因。

全局 trace_id 为 `review:<key>`，来源为 `review:source:<source>:<key>`，全局月度为 `review:monthly:<month>:<key>`，对话月度为 `review:primary:monthly:<month>:<key>`，主分布为 `review:primary:distribution:<组>:<序号>`。每项 trace 可还原成员，原始分析的 lineage 独立保留。

## HTML 展示投影与原文读取

render_dashboard.py 读取 canonical analysis 或已合并 report；已评审报告必须保留 capabilities 和双指纹，渲染时重新校验引用与约束并重算指标。模板只有一个 `__FLYWHEEL_DATA__` 占位符；外部文本按文本展示，不执行日志内容，无 CDN 或模型 API。较大 payload 可内嵌 gzip，旧浏览器可用 --uncompressed。

为控制页面体积，HTML 内的 analysis 是展示投影：保留全部 records、source_rows、统计成员、lineage 和 AI 引用证据，其余长正文/节点摘录可能截短；provenance.view_projection=true。页面导出的 JSON 沿用此投影，不能拿来 merge 或代替 canonical analysis 校验；后续 CLI 使用原 analysis.json / 完整 reviewed_analysis.json。

离线 HTML 有证据摘录、原文件位置、JSON 解码步骤，没有完整原文读取按钮。serve_dashboard.py 使用完整 report 与 data-dir，绑定 127.0.0.1，限制 Host/Origin，同源读取 /api/source；核对数据指纹并验证完整原文件 SHA256 后返回选定原始数组对象。文件变更拒绝读取，缓存随文件状态变化重新校验。SSH 可把远端本机端口转发到本机。

extract_source.py 使用同一校验，--analysis 可接受底表或完整 report，--ref 可接受 raw 行/记录/节点/检查引用；输出包含完整原始对象、文件哈希、索引及 locator_steps。verify_lineage.py 独立只读核验冻结底表、评审统计和源文件哈希/对象/起始行，不重生成输入，--out 只写审计报告。完整命令见 README。
