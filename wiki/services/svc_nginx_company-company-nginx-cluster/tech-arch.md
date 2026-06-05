---
title: company-nginx-cluster 技术架构
doc_type: tech
service_ids:
  - svc_nginx_company
service_name: company-nginx-cluster
related_hosts:
  - host_nginx_01
  - host_nginx_02
  - host_nginx_vip
tags:
  - Nginx
  - 反向代理
  - 集群
  - 公司统一入口
updated_at: 2026-06-01
---

## company-nginx-cluster 技术架构

### 概述

公司统一 Nginx 反向代理集群，承载全公司业务流量的入口路由和负载均衡。部署为双节点主从模式 + VIP。

### 集群节点

| 节点 | IP | 角色 | 端口 |
|------|-----|------|------|
| nginx-node-1 | 10.33.16.42 | 主节点 | 80, 443 |
| nginx-node-2 | 10.33.16.43 | 从节点 | 80, 443 |
| nginx-vip | 10.33.16.244 | VIP 负载均衡 | 80, 443 |

### 技术栈

- **Web Server**: Nginx
- **OS**: CentOS 7
- **协议**: HTTP/HTTPS
- **高可用**: VIP + Keepalived（待确认）

### 依赖关系

- 上游: 外部用户 -> VIP(10.33.16.244) -> nginx-node-1/2
- 下游: -> 后端业务服务（待补充具体服务名）

### 关键配置

```bash
# 检查 Nginx 状态
systemctl status nginx

# 检查监听端口
ss -tlnp | grep -E "80|443"

# 检查 VIP
ip addr show | grep 10.33.16.244

# 检查日志
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log
```

### 运维要点

- 主节点故障时 VIP 自动漂移到从节点（需确认 Keepalived 配置）
- 证书更新影响所有上游业务，变更必须在维护窗口执行

