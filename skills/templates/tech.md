---
title: {title}
doc_type: tech
service_ids:
  - {service_id}
service_name: {service_name}
tags: {tags}
updated_at: {date}
---

## {title}

### 概述

（服务的业务定位、核心职责、在系统中的角色。2-4句话。）

### 集群/节点

| 节点名 | IP | 角色 | 端口 |
|--------|-----|------|------|
| | | | |

### 关键配置

```bash
# 检查服务状态
systemctl status xxx

# 检查端口
ss -tlnp | grep <port>

# 检查日志
tail -f /var/log/xxx/xxx.log
```

### 运维要点

- 故障切换说明
- 证书/密钥更新注意事项
- 监控告警阈值
