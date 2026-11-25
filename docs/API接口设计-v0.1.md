# API接口设计 v0.1：草案

2026-11-19实施增量：新增`POST /api/v1/conversations/{id}/expense-calculations`，office/training/hospitality条件确认后由程序计算；规则接口增加单次上限、比例及数量限制。消息增加expense_proposal及具体error，run和chat/config提供动态limits。详见[本次实现](综合费用与问答稳定性修复记录-2026-11-19.md)。以下旧5/10/90说明为历史草案。

M6实施更新（2026-11-04）：未新增业务路由，API版本0.6.0、/health阶段M6。条件提议增加原输入数量核验，计算回答可附带核验后的办理原文；现有消息/引用接口和权限边界继续适用。真实OpenAPI存于本机.m6/openapi.json，见[M6记录](M6开发与验收记录.md)。

日期：2026-11-03（Asia/Shanghai）

## 状态与通用约定

M5更新（2026-11-04）：新增管理员`GET /admin/audit-events`（对象/动作筛选、before_id游标）、`GET /admin/fee-rules/{id}/source-review`和`POST /admin/fee-rules/{id}/recover`。恢复要求规则及来源版本、当前片段/摘录、单价和显式核对；操作记录随业务事务提交。既有usage返回模型/操作/状态明细及未知预留。接口仅当前管理员可用，不开放员工聊天；实际契约见[M5记录](M5开发与验收记录.md)及OpenAPI，优先于下文旧阶段草案。

M4更新（2026-11-04）：新增`GET/POST /admin/fee-rules`、`PUT /admin/fee-rules/{id}`、`POST /admin/fee-rules/{id}/activate|suspend`、`POST /conversations/{id}/calculations`及`GET /calculations/{id}`。消息响应新增trip_proposal/calculation；预算确认消息须成对提交budget_confirmed_revision和budget_profile_revision，且对应已保存的条件。直接计算用client_request_id/context_revision/profile_revision，金额只能由程序取得规则后计算。实际契约见[M4记录](M4开发与验收记录.md)及OpenAPI；下文早期路径或字段差异以当前实现为准。

M3更新（2026-11-04）：聊天配置、会话CRUD/范围、条件卡、消息、运行/进度/取消与引用接口已实现；详细契约见[M3记录](M3开发与验收记录.md)。M2文档、混合检索和用量接口继续有效。费用规则和计算接口仍为后续草案；以下早期建议与当前实现不同时，以运行服务`/openapi.json`、迁移和阶段记录为准。

认证为JWT访问令牌＋刷新机制。M1—M3已实现身份、账号、员工资料、KB、文档、检索、会话、任务与引用；具体工程参数不自动转为用户已确认需求。消息提交返回202并使用client_request_id UUID幂等，不接受模型、职级或任意工具参数；条件/范围修改检查revision，同会话最多一项活动运行。会话、任务、进度和引用仅限本人，管理员也不能读员工私聊。

M1列表当前返回 `data.items/total`，未实现游标分页。账号创建为username、role、可选password、display_name、department、grade，返回user及当次生成的temporary_password；只有省略password时才返回随机密码。资料与KB修改、成员授权都要求 `expected_revision`。`GET /knowledge-bases/{id}` 已实现；`include_inactive=true` 仅管理员可用。匿名CSRF接口返回200和null，有效刷新上下文返回值；登录/刷新/退出要求允许的Origin。

- 前缀/api/v1，JSON使用snake_case；时间ISO 8601 UTC；UUID为对象标识；金额是十进制字符串。
- 建议成功返回data、request_id；列表额外返回next_cursor；失败返回error.code、error.message、error.details、request_id。
- 未登录401；明确的管理员操作无权限403；不属于当前用户的对象及无权读取的文档/引用/运行返回统一404，避免泄露对象存在性。
- 输入422，状态/版本/重复运行冲突409，请求大小413；预算暂停429并附业务错误码；外部服务失败由任务状态表达或返回502/503。
- 任何来自模型或客户端的user_id、role、grade、kb_ids都不能替代服务端身份授权。
- 不将供应商密钥返回前端。Markdown展示需处理不可信内容，原文件只能经鉴权接口取得。

## 身份与账号

| 方法与路径 | 用途/主要输入 | 返回/权限 |
| --- | --- | --- |
| GET /auth/csrf | 刷新/注销端点的CSRF上下文 | 有效刷新上下文返回值，匿名/失效返回null；CORS限制允许来源 |
| POST /auth/login | JSON：username、password | 建议返回access_token、token_type=bearer、expires_in、user，并设置刷新Cookie；校验来源 |
| POST /auth/refresh | 刷新Cookie及CSRF头 | 建议轮换随机刷新令牌，返回新访问JWT；旧令牌重用则撤销族 |
| POST /auth/logout | 刷新上下文/当前令牌族，CSRF校验 | 204；撤销族并清除刷新Cookie，使所属访问JWT后续请求失效 |
| GET /auth/me | 当前身份 | 角色、自己资料、授权KB及版本摘要 |
| POST /auth/password | old_password、new_password | 204；本人；建议撤销旧令牌族并重新登录 |
| GET/POST /admin/users | 账号列表/创建账号 | 管理员建号已确认，不加入自注册审批流程 |
| PATCH /admin/users/{id} | is_active、role等白名单字段 | 管理员；改角色/禁用需更新登录与授权状态 |
| POST /admin/users/{id}/password-reset | 临时密码及首次改密机制建议 | 管理员；撤销目标旧令牌；不在日志或历史中返回密码 |
| GET/PATCH /admin/employees/{user_id} | 姓名、部门、职级、预期revision | 管理员；字段合法、并发版本冲突409 |

## 知识库、文档和规则

| 方法与路径 | 输入/用途 | 权限与状态 |
| --- | --- | --- |
| GET /knowledge-bases | 当前可见列表 | 员工仅获授权库；管理员管理视图 |
| POST /admin/knowledge-bases | 名称、说明 | 管理员；创建知识库 |
| PATCH /admin/knowledge-bases/{id} | 名称、说明、启用状态 | 管理员；更新知识库 |
| GET/PUT /admin/knowledge-bases/{id}/members | 读取/替换成员授权集合 | 管理员；撤权后所有内容入口重新检查 |
| GET /knowledge-bases/{id}/documents | 当前可见文档列表 | 授权用户；不返回不可访问内容摘要 |
| POST /admin/knowledge-bases/{id}/documents | multipart：file、title、effective_from、effective_until | 管理员；202返回document_id、version_id、job_id |
| POST /admin/documents/{id}/versions | multipart新文件、预期current_version_id、有效期 | 管理员；202；仅处理成功后发布 |
| GET /admin/ingestion-jobs/{id} | 处理状态、失败原因 | 管理员 |
| GET /admin/document-versions/{id}/chunks | 解析片段及来源位置，用于核对提取结果和关联规则 | 管理员；不从员工私人会话中挑选引用 |
| POST /admin/ingestion-jobs/{id}/retry | 重试失败任务 | 管理员；状态不允许则409，避免并行重复任务 |
| DELETE /admin/documents/{id} | 删除当前逻辑文档 | 管理员；停止检索、处理关联规则与历史访问 |
| GET /documents/{id}/versions/{version_id}/download | 原文件下载 | 每次验证当前授权及源文档未删除；文件名不直接作为磁盘路径 |
| GET/POST /admin/fee-rules | 列表/录入条件、单价、单位、来源版本/片段 | 管理员；新录入规则先待核对 |
| PATCH /admin/fee-rules/{id} | 条件/来源修订，携带预期revision | 管理员；保留旧计算快照，修订不静默改写历史金额 |
| POST /admin/fee-rules/{id}/activate | 确认当前来源与参数 | 管理员；再次检查版本、有效性与冲突 |
| POST /admin/fee-rules/{id}/suspend | 暂停 | 管理员；不影响其他规则 |

已确认单文件10MB、PDF100页、提取正文10万字符、演示资料30份当前文档，支持正文和简单表格；具体字节换算在配置中统一。上传采用multipart，按实际读取字节检查大小。[FastAPI文件接口说明](https://fastapi.tiangolo.com/tutorial/request-files/)

## 会话、提问与运行

| 方法与路径 | 输入/用途 | 返回/约束 |
| --- | --- | --- |
| GET/POST /conversations | 本人列表/新建：title、knowledge_base_ids | 仅本人；范围必须当前获授权 |
| PATCH /conversations/{id} | title、knowledge_base_ids、预期context_revision | 仅本人；范围变更重新鉴权 |
| DELETE /conversations/{id} | 删除会话 | 仅本人；后续不再返回，停止未完成运行的新增调用 |
| GET /conversations/{id}/messages | 历史分页 | 仅本人；受影响回答返回占位，不含隐藏正文或引用快照 |
| PATCH /conversations/{id}/trip-context | destination、travel_days、lodging_nights、预期revision | 仅本人；不接收grade/role；校验1—30及晚数范围 |
| POST /conversations/{id}/messages | content、client_request_id、可选trip_context、context_revision | 202返回message_id、run_id、status；建议同会话只有一项活动运行 |
| GET /runs/{id} | 查询状态 | 仅运行所有者；计数、停止原因、结果消息标识；不暴露绕过历史鉴权的原始结果 |
| GET /runs/{id}/events?after_seq=n | 有序操作进度 | 仅所有者，返回events、next_seq；同样检查可见性 |
| POST /runs/{id}/cancel | 取消候选 | 仅所有者；停止新增调用，在途调用是否已收费独立记录 |
| GET /citations/{id} | 引文与来源位置 | 先验证所属会话所有权，再验证来源当前可访问；撤权或源文档删除则404 |
| GET /calculations/{id} | 明细、标准、公式和来源 | 仅所属会话用户并验证当前来源权限；无法访问时不返回快照 |

建议首次用轮询进度和一次性最终回答实现；逐字输出或SSE作为后续优化评估。最终内容通过当前权限和引用检查后才提供给前端。

## 消息提交示例

ID为占位符，实施时替换为有效UUID。

```json
{
  "client_request_id": "<本次请求UUID>",
  "content": "这次出差怎么报销，大概预算多少？",
  "context_revision": 2,
  "trip_context": {
    "destination": "北京",
    "travel_days": 3,
    "lodging_nights": 2
  }
}
```

202结果为run_id等标识；重发相同client_request_id和相同请求内容复用原运行。相同标识但不同内容返回409 REQUEST_KEY_REUSED。客户端不能通过更换标识绕过同会话活动运行限制。

## 最终结果结构建议

```json
{
  "answer": "<办理指南与有依据的回答>",
  "employee_context": {"grade": "S1", "source": "employee_profile"},
  "trip_context": {"destination": "北京", "travel_days": 3, "lodging_nights": 2},
  "estimate": {
    "status": "complete",
    "basis": "policy_standard_budget",
    "currency": "CNY",
    "items": [
      {"kind": "lodging", "quantity": 2, "unit_rate": "300.00", "amount": "600.00"},
      {"kind": "daily", "quantity": 3, "unit_rate": "80.00", "amount": "240.00"}
    ],
    "total_amount": "840.00"
  },
  "citations": [{"id": "<引用UUID>", "document_title": "<制度名称>", "quote": "<真实引文>", "anchor": {"type": "pdf", "page": 1}}],
  "unresolved_items": []
}
```

单价和金额由程序取得并计算；模型不能把自由生成数字覆盖此结构。依据缺失或规则暂停时，estimate.status为needs_information/blocked，total_amount为null；允许展示已确定项目但明确完整合计不可用。员工资料标记来源为资料记录，不能伪装成文档页码引文。

## 关键业务错误与状态

| 错误/状态 | 含义与前端建议 |
| --- | --- |
| SCOPE_CHANGED | 原选择范围已有撤权/停用；确认新范围后再提交，避免先发生付费调用 |
| CONTEXT_VERSION_CONFLICT | 条件或员工资料已更新；刷新并确认新条件 |
| ACTIVE_RUN_EXISTS | 同会话已有任务，定位到现有进度 |
| RULE_SOURCE_CHANGED / RULE_SUSPENDED | 来源更新，受影响估算暂不可用；保留可回答部分 |
| CITY_STANDARD_MISSING | 城市不在已确认名单，说明缺标准，不自动归类 |
| SOURCE_UNAVAILABLE | 历史占位，不含旧正文、快照或下载地址 |
| SEARCH_LIMIT / MODEL_LIMIT / DEADLINE | 达到5/10/90任一边界，结束本轮并返回证实部分 |
| BUDGET_PAUSED | 达到已确认应用估算暂停线时阻断新增付费请求，管理员查看用量 |
| PROVIDER_UNAVAILABLE / INGESTION_FAILED | 返回可操作原因，保留重试入口；次数与费用如实记录 |

已确认应用估算80元提醒、100元暂停新增付费请求。GET /admin/usage用于摘要设计；所有角色仅查看本人会话，不提供管理员浏览员工会话的接口。

M1已采用：访问令牌放内存，通过Authorization: Bearer传输；刷新令牌为随机值、数据库仅存哈希，放HttpOnly Cookie并轮换，刷新/注销校验CSRF与来源。访问JWT含sub、sid、jti、ver、iat、exp、iss、aud，不携带可当作权限真值的KB列表；每次检查用户、令牌族及当前授权。15分钟/7天已作为可配置工程默认值落地，未改写为用户单独确认的业务要求。

## 验证与下一步

M1身份与权限已有独立集成测试及浏览器测试；固定40例仍用于后续完整业务验收。金额字符串、幂等、文档撤权/替换与停止条件随对应阶段接入；本文件的未实现接口不代表当前服务可调用。
