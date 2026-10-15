# 数据字段与关联

## 用户反馈

对象数组。外层项目字段为 projectId、projectName、projectProduct、projectVersion、tenantId、tenantName。业务字段为 chatId、feedback、mark、reason、reasonType、createTime、question。

feedback=-1 为差评、0 为未评价、3 为好评。question 可能为包含 scene/question/otherContext 的 JSON 字符串，也可能为“场景：无场景，问题：…”纯文本。chatId 关联对话 detail.id，不是反馈事件唯一键。反馈只按完全相同的整条记录合并重复；同对话的不同时间、标签或原因保留。

## 三类网关日志

共同字段含 requestId、projectId、projectName、projectProduct、projectVersion、tenantId、tenantName、url、requestType、queryParam、header、body、responseData、status、updateTime、totalCost、responseCost、firstTokenLatency、perOutput、isStream、globalRunning、userType。body/header/responseData 可能是 JSON 字符串、字符串 null 或非 JSON 内容。

requestId 只在各自来源内使用。各来源独立生成 ID，不可用于跨来源确定关联。护栏和推理按 requestId 去重，重复保留最大 updateTime；相同时间以整条记录的内容指纹确定保留项，并报告冲突，不把这个取舍解释为业务先后。

## 安全护栏

body 包含 questions[] 和可选 ignore_items[]。responseData.checkResults[] 含 isLegal、question、response、error_code、error_desc。HTTP200 仍可能 isLegal=false。请求拦截率以有效请求数为分母，问题拦截率以有效检查结果数为分母；一请求可能有多个问题。

## 对话详情

body.question 是问题 JSON 字符串；body.detail 是完整链路 JSON 字符串。detail 含 id、conversationId、question、feedback、mark、answers[]、askTime、questionTime、costTime、answerFeedback。

answers[] 含 id、chatId、level、type、status、data、recipeName、nodeName、costTime、answerTime、AIGC。level 为 TITLE/DETAIL/ANSWER/CONCEAL，type 为 TEXT/PIU，status 为 MESSAGE/END。data 可为文本、JSON 字符串或 null；PIU 中可能存在嵌套 answers、steps、diagnosisUnitList。END 之后可能仍有异步答案。

detail.id 是对话记录键；conversationId 是多轮会话键；二者分别计数。对话导出行按 requestId 去重后，同一 detail.id 多份快照再保留最新 updateTime 用于业务统计，重复快照不累计到业务分母。原始网关日志条数和独立对话记录数分开展示。

detail.costTime 用于对话耗时，外壳 totalCost/responseCost 用于上报接口耗时。不能用外壳 updateTime 减 askTime 推导完整对话耗时。

## 推理

body 含 model、messages[]（role/content）、stream、enable_thinking、temperature、max_tokens、tools[]、tool_choice。请求中存在 tools 定义不代表实际调用成功。system 内容是 Prompt 快照，可计算指纹；指纹不是发布版本。

responseData 可以为普通 JSON、SSE 或护栏拒绝对象。先识别 HTTP403/guardrail_message，之后识别 SSE、普通 JSON、其他，互斥计数。普通 JSON 的 choices[].message.content 是回复；SSE 按当前请求内部的生成 id 与 choices.index 拼接 delta.content/reasoning_content，终止标志不当正文。文本片段不能跨不同请求合并。chunk 数不等于 token 数。

request.body.model 和 response.model 可能不同。仅报告路由差异；没有路由配置证据不能认定错误。首 token 延迟、响应耗时、每输出 token 耗时分别统计，0、缺失、非法值应说明是否纳入。

## 文档快照范围

基线来自项目原始两份字段文档：反馈42、护栏3280、对话468、推理16201；对话节点11248；场景 diagnose92、data70、无场景306。反馈原因仅1条，反馈与对话仅匹配1条；对话内嵌护栏468条，内嵌推理输出39条。

已知文档疑点：推理格式4512+11689+6=16207，超出总量6；推理 userType 被描述为1和0；对话示例 updateTime 早于 askTime；护栏字段表标题19但列到22；answers.data 被写作string但可为null。原始 JSON 未接入时只报告疑点，不自行修正文档数字。

推理、护栏的统计来源与对话记录量不同，不能合计成独立问题量或数据加工漏斗。RAG检索日志、金标、修复生命周期、usage、稳定userId未提供时，不计算召回率、准确率、解决率、成本或留存。
