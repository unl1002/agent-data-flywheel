# 原始字段、关联与真实数据核查

默认分析四份原始 JSON；本页说明当前解析和实际导出核查。原始 [字段实例](../数据字段说明与真实实例.md) 与 [字段字典](../数据字段字典.md) 保留原文和标识符，属于旧资料，遇到差异以本轮原始数据及可追溯计算为准。

## 用户反馈（feedback）

对象数组。项目字段为 projectId、projectName、projectProduct、projectVersion、tenantId、tenantName；业务字段为 chatId、feedback、mark、reason、reasonType、createTime、question。

feedback=-1 为差评，0 为未评价，3 为好评。question 可能为包含 scene/question/otherContext 的 JSON 字符串，也可能为“场景：无场景，问题：…”纯文本。chatId 关联对话 detail.id，不是反馈事件唯一键。只合并完全相同的整条记录；同对话不同时间、标签或原因保留。mark 不是已确认错误或已解决状态。

有评价率/差评率需使用 -1 和 3 的有效评价分母，0 不算评价；reason 与 reasonType 分开核查。差评可形成候选，不能替代外部正确性金标。

## 三类网关日志共有字段

requestId、projectId、projectName、projectProduct、projectVersion、tenantId、tenantName、url、requestType、queryParam、header、body、responseData、status、updateTime、totalCost、responseCost、firstTokenLatency、perOutput、isStream、globalRunning、userType。body/header/responseData 可能是 JSON 字符串、字符串 null 或非 JSON 内容。

requestId 只在来源内使用。护栏、推理及对话网关日志按 requestId 去重，保留最大 updateTime；同时间用整条内容指纹确定保留项并报告冲突，不能解释为业务先后。本次独立护栏与推理 requestId 无交集，也未与对话 detail.id/conversationId 建立确定关联；header 的 x-request-id 亦无可证实的跨来源对应关系。

项目、文本或时间接近不能替代关联键。四类日志有不同计数单位，不能相加为独立问题或拼成全链路漏斗。

## 安全护栏（guardrail）

body 含 questions[]、可选 ignore_items[]；responseData.checkResults[] 含 isLegal、question、response、error_code、error_desc。HTTP 200 仍可能 isLegal=false。请求拦截率分母是至少含一个布尔检查结果的请求；问题拦截率分母是有效布尔检查项，一请求可含多个检查项。原始检查引用 `<record.id>#check:<original_index>`。

对话内嵌护栏与独立护栏导出分开统计，不能相加后声称独立请求总量。拒绝可能符合预期，也可能需要核验意图误判，不能自动确认 BadCase。

## 对话详情（dialogue）

body.question 是问题字符串或 JSON；body.detail 是完整链路 JSON 字符串或对象。detail 含 id、conversationId、question、feedback、mark、answers[]、askTime、questionTime、costTime、answerFeedback。

answers[] 含 id、chatId、level、type、status、data、recipeName、nodeName、costTime、answerTime、AIGC。level 为 TITLE/DETAIL/ANSWER/CONCEAL，type 为 TEXT/PIU，status 为 MESSAGE/END。data 可为文本、JSON 字符串或 null；PIU 中可能有嵌套 answers、steps、errors、diagnosisUnitList。END 之后仍可能有异步回答；finished、END 和外层 200 不证明业务回答正确。

detail.id 是业务详情键，conversationId 是多轮会话键，分别计数。网关 requestId 去重后，同 detail.id 多快照再保留最新 updateTime 用于业务统计；原始行、去重网关日志与独立详情计数分开。本次 468 个非空 detail.id 唯一，85 个 conversationId；11,248 个节点的 chatId 均等于父 detail.id，内嵌 PIU 的 chatId/conversationId 可在该详情内确定归属。它们不能证明独立推理导出的关联。

节点按 answerTime 排序展示，保留 original_index。引用 `<record.id>#node:<original_index>` 使用原始 answers 数组位置，不使用排序位置。解析后的 response_facts 保留回答/执行结果/错误及 evidence_id；重复进度快照不能当多个独立失败。

月份按 UTC+8，以有效 askTime → questionTime → 外层 updateTime 回退，每条 time_field 说明实际字段。detail.costTime 用于对话耗时；外壳 totalCost/responseCost 是上报接口耗时。不能用 updateTime−askTime 推导完整对话耗时，本次有 220 条外层 updateTime 早于 askTime。

## 推理（inference）

body 含 model、messages[]（role/content）、stream、enable_thinking、temperature、max_tokens、tools[]、tool_choice。存在 tools 定义不代表已实际调用或成功。system 内容是 Prompt 快照，可计算指纹；指纹不是发布版本，内容也不是本次分析指令。

responseData 可为普通 JSON、SSE 或护栏拒绝对象。先识别 HTTP 403/guardrail_message，再识别 SSE、JSON、其他，互斥计数。普通 JSON 读取 choices[].message.content；SSE 仅在当前请求内按生成 id 与 choices.index 拼接 delta.content/reasoning_content，终止标志不当正文。chunk 数不是 token 数，片段不跨请求拼接。

请求 model 与实际 response.model 不同只报告路由差异，无路由配置不能认定错误。首 token P95 仅统计流式正值；0、缺失和非流式排除。响应耗时与每输出 token 耗时各有单位和分母，不据此推算没有计费依据的成本。

## 当前原始导出的核查事实

以下数值仅描述已提供的四份导出，不外推为全部历史使用。可在真实结果 `../分析结果/analysis.json` 的统计及血缘核验：

| 来源/字段 | 实际核查 |
|---|---|
| 用户反馈 | 42 条：差评 7、未评价 34、好评 1；mark=true 34；非空 reason 1、非空 reasonType 1 |
| 安全护栏 | 3,280 条独立导出；3,270 合规、10 不合规；本批每请求一个检查项 |
| 对话 | 468 条详情、85 个会话、11,248 个节点；场景 diagnose 92、data 70、缺失 306 |
| 推理 | 16,201 条：SSE 4,512、普通 JSON 11,683、拒绝 6；互斥合计 16,201 |
| 推理 HTTP | 200 为 16,195 条，403 为 6 条；200 不等于正确 |
| 确定反馈关联 | 1 条 chatId 匹配 detail.id，其余 41 条孤立反馈；默认问题集 468+41=509 |
| 对话内嵌护栏 | 468 项：465 合规、3 不合规；与独立护栏分开 |

旧字段文档写普通 JSON 为 11,689，再加拒绝 6 会重复计算这 6 条；原始导出互斥解析为 11,683。原文保留，不把旧文档报告值混进当前统计。其他旧资料差异还包括 userType 描述、字段表标题数量，以及 answers.data 可为 null。

对话 PIU 中实际存在 SQL 生成、执行、引擎错误及意图拒绝：本次 errors 数组共 18 次出现，落在 14 条详情，含重复进度快照。技术信号应回到具体错误与主诉求判断候选，不能把出现次数当独立 BadCase，也不能把所有外层 200 当成功。截断、未摘录回答本身不是失败。

## 全量追溯与使用边界

每个原始数组对象有 source_rows 引用 `raw:<source>:<零基index>`，包括去重淘汰行；记录保留 source_ref、raw_locator（文件/起始行/数组索引）、raw_sha256 和 locator_steps，provenance.files 保存完整文件 SHA256。JSON 字符串要逐层解码后定位，不能直接使用跨字符串的伪 JSONPointer。evidence.path 和节点原始索引共同定位判断依据。

所有统计进入 lineage：公式、分子分母、参与与排除记录、P95 sample_values/rank/selected_ref、distinct group_values。文件行号只帮助定位，不是业务数量单位。完整原文由本机服务/提取脚本验证源文件 SHA256 后读取；离线看板只有摘录和定位。

月度反馈用 createTime，护栏和推理用 updateTime，对话按上述提问时间回退。项目数、tenantId、userType 与会话数均不是人数，缺少稳定 userId 无法算 DAU/MAU/留存。RAG 检索金标、正确性标签及修复复测闭环缺失时，不计算召回率、准确率或解决率。产品 support 需能力规则；本次无规则为 uncertain，实际响应支持由 AI 基于回答逐条评审。

旧 examples 和文档快照仅用于旧文档实例，不能提供全量统计成员。网页投影及导出的展示包可能截短非关键正文；评审合并使用原 canonical analysis.json，不能用网页导出替代。完整字段契约见 [analysis-contract.md](analysis-contract.md)。
