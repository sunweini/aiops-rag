---
title: {title}
doc_type: sop
service_ids:
  - {service_id}
service_name: {service_name}
related_hosts:
  - {host_id}
tags:
  - {sop_type}
updated_at: {date}
---

## {title}

### 前置条件

- 需要哪些权限（如 root、sudo）
- 影响范围（会影响哪些服务/用户）
- 需要提前检查什么

### 操作步骤

{body}

### 验证

- 检查进程: `systemctl status xxx`
- 检查端口: `ss -tlnp | grep <port>`
- 检查日志: `tail -20 /var/log/xxx/xxx.log`

### 回滚

- 操作失败时如何恢复到操作前状态

### 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| | | |

> **分块说明**
> - 按 ### section 父子切分，长 section 自动段落合并/句子级兜底
> - 步骤较多时可拆子 section（### 操作步骤-诊断 / ### 操作步骤-修复）
> - 表格/代码块不会被截断，可放心使用
