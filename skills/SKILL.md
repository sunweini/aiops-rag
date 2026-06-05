---
name: aiops-rag
description: 当用户使用 /aiops_rag 指令或询问运维相关问题（故障排查、服务拓扑、SOP操作、依赖链路、故障影响、架构查询、主机端口映射）时，必须使用此 skill 查阅知识库。覆盖 ELK/K3s/Nginx/ERP 集群架构、生产故障（502/延时/超时/OOM/磁盘满/端口不可达）、SOP/技术文档/故障记录的编写与更新、知识库增量索引。所有运维问答必须先查此 skill，不可跳过。
---

# AIOps RAG 知识库

通过 `aiops-query` CLI 查询和运维 AIOps 知识库。

## CLI 调用规则（硬规则，违反即为不合格）

**禁止生成脚本或写入文件来查询。** 所有查询操作**必须**通过单次 `./aiops-query <cmd>` 命令完成，禁止生成 `.sh` 脚本文件，禁止多步串联脚本。

- `./aiops-query query '<问题>'` — 一次调用完成
- `./aiops-query topology <svc_id>` — 一次调用完成
- `./aiops-query cluster <svc_id>` — 一次调用完成
- 其他查询命令同理

**错误** ❌：写脚本 `#!/bin/bash ... ./aiops-query topology A ... ./aiops-query topology B` 再执行
**正确** ✅：直接 `./aiops-query cluster svc_nginx_company`

## CLI 安装

```bash
chmod +x ~/.openclaw/skills/aiops-rag/aiops-query
```

## 查询命令

| 命令 | 用途 | 示例 |
|------|------|------|
| `aiops-query query '<问题>'` | 知识库问答（最常用） | `aiops-query query 'nginx 502 排查'` |
| `aiops-query topology <svc_id>` | 服务拓扑 | `aiops-query topology svc_nginx` |
| `aiops-query cluster <svc_id>` | 集群拓扑（VIP+成员） | `aiops-query cluster svc_nginx_company` |
| `aiops-query impact <host_id>` | 主机故障影响分析 | `aiops-query impact host_app_01` |
| `aiops-query path <svc_id> [depth]` | 多跳依赖链 | `aiops-query path svc_nginx 3` |
| `aiops-query circular` | 循环依赖检测 | `aiops-query circular` |
| `aiops-query service-docs <svc_id>` | 服务关联文档列表 | `aiops-query service-docs svc_nginx` |
| `aiops-query host-docs <host_id>` | 主机关联文档列表 | `aiops-query host-docs host_nginx_01` |
| `aiops-query doc-services <doc_id>` | 文档关联服务列表 | `aiops-query doc-services svc_nginx_tech-arch` |
| `aiops-query doc-hosts <doc_id>` | 文档关联主机列表 | `aiops-query doc-hosts svc_nginx_company_tech-arch` |
| `aiops-query health` | 系统健康（含 ES↔Neo4j 同步） | `aiops-query health` |
| `aiops-query sync-health` | ES ↔ Neo4j 一致性详细检查 | `aiops-query sync-health` |
| `aiops-query metrics` | 监控指标 | `aiops-query metrics` |

## 维护命令

| 命令 | 用途 | 示例 |
|------|------|------|
| `aiops-query write-sop <svc_id> <操作名> [--file <path> | '<内容>'] [--tags 'tag1,tag2']` | 写入 SOP | `aiops-query write-sop svc_nginx 重启 --file /tmp/body.md --tags '应急,高危'` |
| `aiops-query write-tech <svc_id> [--file <path> | '<内容>'] [--tags 'tag1,tag2']` | 写入技术文档 | `aiops-query write-tech svc_nginx --file /tmp/body.md --tags 'ELK,架构'` |
| `aiops-query write-incident <svc_id> [--file <path> | '<内容>'] [--tags 'tag1,tag2']` | 写入故障记录 | `aiops-query write-incident svc_nginx --file /tmp/body.md --tags '502,Nginx'` |
| `aiops-query add-service --id <id> --name <名> --host <hid> [--port 8080] [--call svc_x:http:80]` | 新增服务到拓扑 | `aiops-query add-service --id svc_payment --name payment-service --host host_app_02 --port 50051 --call svc_db_mysql:tcp:3306` |
| `aiops-query delete-service <svc_id>` | 删除服务及关联文档 | `aiops-query delete-service svc_xxx` |
| `aiops-query update-node <Label> <node_id> <key> <value>` | 更新任意节点属性 | `aiops-query update-node Host host_nginx_01 os 'Ubuntu 22.04'` |
| `aiops-query add-host --id <id> --name <名> --ip <ip> [--os 'Ubuntu']` | 新增主机 | `aiops-query add-host --id host_db_02 --name prod-db-02 --ip 10.0.2.20 --os 'Ubuntu 22.04'` |
| `aiops-query index` | 增量重建索引 | `aiops-query index` |
| `aiops-query index-file <path>` | 单文件索引重建 | `aiops-query index-file services/svc_k3s-k3s-cluster/tech-arch.md` |

## 路由决策

根据用户问题选择命令：

- **SOP 操作**: "怎么重启" "如何扩容" → `query`
- **故障排查**: "502" "延时" "连不上" "异常" → `query`（自动触发 incident+architecture）
- **服务拓扑**: "部署在哪" "端口" "主机" → `topology <svc_id>`
- **集群拓扑**: "集群成员" "VIP" "主/从节点" → `cluster <svc_id>`
- **依赖分析**: "依赖哪些" "调用链" "影响什么" → `query` 或 `path <svc_id>`
- **主机故障影响**: "xx挂了" "xx宕机" → `impact <host_id>`
- **文档查询**: "这个服务有哪些文档" → `service-docs <svc_id>`
- **主机文档**: "这个主机有哪些文档" → `host-docs <host_id>`
- **反向查询**: "这个文档涉及哪些服务" → `doc-services <doc_id>`
- **文档关联主机**: "这个文档涉及哪些主机" → `doc-hosts <doc_id>`
- **更新节点**: "改下这个主机的OS" "更新服务状态" "改端口协议" → `update-node <Label> <node_id> <key> <value>`
- **同步检查**: "ES和Neo4j一致吗" → `sync-health`
- **写入文档**: "帮我写个SOP" "记录这个故障" "更新技术文档" → `write-sop` / `write-tech` / `write-incident`
- **新增服务**: "加个新服务" "注册服务到知识库" → `add-service`
- **系统状态**: "服务还在跑吗" "知识库正常吗" → `health` / `metrics`
- **重建索引**: "更新文档后重建索引" "只改了一个文档" → `index` / `index-file <path>`

## 服务 ID 对照表

| 服务 | service_id | 关键主机 |
|------|-----------|--------|
| company-nginx-cluster | svc_nginx_company | host_nginx_01 (10.33.16.42) 主节点, host_nginx_02 (10.33.16.43) 从节点, host_nginx_vip (10.33.16.244) VIP |
| elasticsearch | svc_es | host_es_master_01 (10.33.17.100), host_es_master_02 (10.33.17.101), host_es_master_03 (10.33.17.102), host_es_data_01/02 (10.33.17.103/104) |
| kibana | svc_kibana | host_es_master_01 (10.33.17.100) |
| logstash | svc_logstash | host_logstash_01 (10.33.17.105), host_logstash_02 (10.33.17.106), host_logstash_vip (10.33.17.107) VIP |
| K3Cloud-01 | svc_app01 | host_app01 (10.33.17.120) |
| K3Cloud-02 | svc_app02 | host_app02 (10.33.17.121) |
| SQLServer AlwaysOn | svc_db_alwayson | host_db_master (10.33.17.124) 主节点, host_db_slave (10.33.17.125), host_db_backup (10.33.17.126) |
| ERP中转 | svc_erp_relay_main | host_erp_relay_main (10.33.16.58), host_erp_relay_backup (10.33.16.63) |
| 契约锁中转 | svc_qys_relay_main | host_qys_relay_main (10.33.16.70), host_qys_relay_backup (10.33.16.71) |
| 天枢中转 | svc_tianqu_relay_main | host_tianqu_relay_main (10.33.16.127), host_tianqu_relay_backup (10.33.16.26) |
| 文件服务器 | svc_file_server | host_file (10.33.17.122) |
| 管理中心 | svc_mgmt_center | host_mgmt (10.33.17.123) |
| k3s-cluster | svc_k3s | host_k3s_master (10.33.16.202), host_k3s_node01 (10.33.16.203), host_k3s_node02 (10.33.16.204), host_k3s_gpu_node01 (10.33.17.234) GPU |
| logstash-vip | svc_logstash_vip | host_logstash_vip (10.33.17.107) |

> 服务名可变，service_id 不变——始终用 service_id。
> 不知道 service_id 时先用 `aiops-query query '<服务名>'` 从返回的 sources 中获取。

## 文档写入流程

> ★ 核心：Agent 由 LLM 生成完整文档内容，再通过 CLI 写入知识库

### 写 SOP

1. 读取模板 `~/.openclaw/skills/aiops-rag/templates/sop.md` — 了解 frontmatter 格式和文档结构
2. **调 LLM** 生成完整 Markdown body（包含操作步骤、命令、验证、回滚），格式对齐模板
3. 调 `aiops-query write-sop <svc_id> <操作类型> --file <LLM生成md文件路径>` 写入并入库（推荐用 --file 避免 shell 引号截断）
4. **tags 规范**：默认带操作类型标签；额外 tags 用逗号分隔，如 `--tags '应急,高危,必须备份'`

### 写技术文档

1. 读取 `~/.openclaw/skills/aiops-rag/templates/tech.md`
2. **调 LLM** 生成内容（功能概述、技术栈、依赖关系图、部署信息、关键配置）
3. 调 `aiops-query write-tech <svc_id> --file <LLM生成md文件路径>` 写入并入库（推荐用 --file 避免 shell 引号截断）
4. **tags 规范**：默认 `技术文档`；强烈建议加业务域标签，如 `--tags 'ELK,中间件,日志平台'`

### 写故障记录

1. 读取 `~/.openclaw/skills/aiops-rag/templates/incident.md`
2. **调 LLM** 生成内容（故障时间线、影响范围、根因分析、处理过程、改进措施）
3. 调 `aiops-query write-incident <svc_id> --file <LLM生成md文件路径>` 写入并入库（推荐用 --file 避免 shell 引号截断）
4. **tags 规范**：默认 `故障, 复盘`；强烈建议加故障类型标签，如 `--tags '502,Nginx,配置错误'`

### 写文档原则

- **不要猜测** — 端口、IP、配置参数须用户确认，未知的标注 `（待确认）`
- **命令可执行** — bash 代码块中的命令应可在目标主机上直接运行
- **标注来源** — 如果信息来自已有文档或拓扑，注明出处
- **必须有 tags** — 每个文档必须带标签，至少含默认标签；Agent 应根据文档内容自动补充有意义的业务域标签
- **标签命名规范**：用中文或标准技术缩写（ELK、ES、K8s、Nginx、502、OOM 等），逗号分隔，不用空格花括号等特殊字符
- **禁止占位符** — 文档写入后必须校验，不可出现 `（待填写）`、`{placeholder}`、`{xxx}` 等模板残留
- **必须有 ## 标题 + ### 章节层次** — 每个文档以 `## 标题` 开头，按 `### section` 组织结构（见模板），保证索引时层次切分效果。无结构文档检索质量差
- **每个 ### section 内容建议 500~1500 字符** — 引擎自动处理超长 section 的段落合并和句子级回退，无需手动控制长度
- **related_hosts 必填** — 文档涉及的每个主机 host_id 必须写入 frontmatter。host_id 可从拓扑 YAML 中查询，如不确定则标注 `（待确认 host_xxx）`
- **禁止手动编辑 YAML/文件** — Agent 在任何情况下都不允许手动编写脚本或直接编辑 `call-graph.yml`、`tech-arch.md`、`sop-*.md` 等文件。所有操作必须通过 `aiops-query` CLI 命令完成。如果缺少某个批量操作功能，应要求完善 skill 自带脚本，而非 Agent 自己绕过 CLI

### Agent 查询回答规范（硬规则）

Agent 在回答用户查询时必须遵守以下两条硬性约束，违反即为不合格：

1. **禁止省略性复述** — 知识库原文中的节点表、IP、端口、配置参数、命令等内容必须完整呈现，不得做省略、摘要或"详见文档"式跳过。可以补充额外说明，但不能删减原文已有信息。
2. **低置信度不编造** — 无论置信度高低，都不允许编造内容或自由发挥。知识库没有的信息标注 `（待确认）`，不确定的结论标注置信度。置信度低时给出多种可能性，但每种可能性必须基于已有数据推理，不能凭空构造。

## 标签体系规范

Agent 在写入文档时，必须遵循标签规范以保证知识库检索质量：

| 文档类型 | 强制默认标签 | 建议补充标签（按内容自动选择） |
|---------|-------------|--------------------------|
| tech（技术文档） | `技术文档` | 业务域标签（ELK/K8s/数据库/中间件）+ 组件标签（ES/Logstash/Kibana） |
| sop（操作手册） | 操作类型（重启/扩容/迁移/备份等） | 风险等级（应急/高危/常规）+ 关联场景（OOM/502/磁盘满） |
| incident（故障记录） | `故障` `复盘` | 故障类型（502/OOM/延时/连接泄漏）+ 根因组件（Nginx/MySQL/Redis） |

**错误示例** ❌：
- `tags:` 留空 — 检索不到
- `tags: 运维` — 太泛，没有信息量
- `tags: ELK,elasticsearch` — 中英混合不一致

**正确示例** ✅：
- 技术文档: `tags: 技术文档, ELK, Elasticsearch, 日志平台`
- SOP: `tags: 重启, 应急, Nginx`
- 故障记录: `tags: 故障, 复盘, 502, Nginx, 配置错误`

## 文档校验规范

Agent 写入文档后，必须校验以下内容，不合格的文档会降低知识库检索质量：

| 校验项 | 检查方式 | 不合格的表现 |
|--------|---------|-------------|
| **tags 不为空** | 检查 frontmatter 中 tags 字段 | 空列表或仅有默认值如 `技术文档` |
| **body 无占位符** | grep 模板占位符关键词 | 出现 `（服务负责什么，在系统中的角色）`、`（待填写）`、`{placeholder}` 等 |
| **body 有实质内容** | 检查 body 行数 ≥ 10 且包含代码块或表格 | body 仅 2-3 行描述性文字 |
| **目录命名规范** | 检查目录名格式 | 出现 `${svc_id}-${name}` 中 id 和 name 语义重复（如 `svc_es-es`） |
| **有 ## 标题 + ### 章节层次** | 检查 body 是否有 ≥ 1 个 ## 标题和 ≥ 2 个 ### section | body 无标题或无 section 划分 |
| **related_hosts 非空** | SOP/故障文档必须填写 related_hosts | 仅技术文档可为空，SOP/故障不可空 |
| **section 长度 ≤ 800 字符** | 每个 ### section 内容不超过 800 字符 | section 超长导致切分截断

## 主机注册复核规范

Agent 注册主机后必须复核以下内容，否则数据不完整：

| 复核项 | 检查方式 | 不合格的表现 |
|--------|---------|-------------|
| **os 非空** | `aiops-query topology <svc_id>` 或直接查 YAML 中 host 的 os 字段 | os 字段缺失或为空字符串 |
| **ip 准确** | 对比实际采集的 IP 与拓扑中注册的 IP | IP 不一致或为空 |

**发现不合格主机的处理**：
1. os 缺失 → 立即调用 `aiops-query update-node Host <host_id> os '<os_value>'` 补全
2. ip 错误 → 如果主机刚注册，直接修改 YAML 中正确 IP 后 reload-topology；如果已上线很久，用 `update-node` 更新

**发现不合格文档时的处理**：
1. 如果是刚写入的 → 立即重新生成，补充缺失内容
2. 如果是历史遗留 → 标注并修复

## 分块策略（文档结构为何重要）

文档入库后按标题层次切分，结构越好检索越准。三种策略：

| 文档类型 | 切分策略 | 层级效果 |
|---------|---------|---------|
| **SOP** | 父子块：`### section` 整体作为 parent（≤800c），section 内拆 200-300c child。检索命中 child → 返回完整 section | child 精准命中 + LLM 看到完整步骤 |
| **tech/incident** | 层次切分：按 `## → ### → 段落组` 递归，优先保持标题边界。最长段落组 5 段，超过则句子级 fallback | 每个 chunk 自带标题路径，检索可溯源 |
| **全类型** | 占位符过滤 + 标题继承。`（待填写）`、`{xxx}` 模板残留直接拒绝索引 | 无垃圾 chunk，每个 chunk 知道属于哪个文档 |

→ **文档必须有以下结构才能被正确检索：**
- `## 标题` + 2+ 个 `### section`
- section ≤ 800 字符
- 无 `（待填写）` / `{placeholder}` 残留

---

## 拓扑新增规范

Agent 新增服务须先确认：
1. service_id 以 `svc_` 开头，host_id 以 `host_` 开头
2. **先注册 Host 再注册 Service** — 调用 `add-host` 后再 `add-service`，禁止创建引用不存在 host 的 service
3. host 在拓扑中已存在，若不存在先 `add-host`
4. **`--call` 参数必填** — 如果有依赖关系必须传 `--call`，禁止创建无任何调用关系的孤立服务
5. calls 中的 target service_id 在拓扑中已存在
6. 端口编号准确（不要猜测）
7. **集群服务** — 对于多节点同构部署（如 ES 集群、Logstash 集群），应在 YAML 中使用 `cluster_nodes` 字段列出所有节点，而非拆分为多个独立 service

## 模板文件

| 文件 | 用途 |
|------|------|
| `templates/sop.md` | SOP 文档模板 |
| `templates/tech.md` | 技术架构文档模板 |
| `templates/incident.md` | 故障记录文档模板 |

Agent 在写文档前需读取对应模板以了解结构和字段要求。
