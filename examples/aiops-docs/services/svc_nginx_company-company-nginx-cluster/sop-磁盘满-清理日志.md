---
title: company-nginx-cluster 磁盘满-清理日志 SOP
doc_type: sop
service_ids:
  - svc_nginx_company
service_name: company-nginx-cluster
tags:
  - 磁盘满-清理日志
  - 应急
  - Nginx
  - 日志清理
  - 常规
updated_at: 2026-06-01
---

## company-nginx-cluster 磁盘满-清理日志 SOP

### 前置条件

- 需要哪些权限（如 root、sudo）
- 影响范围（会影响哪些服务/用户）
- 需要提前检查什么

### 操作步骤

### 告警识别

收到 Nginx 磁盘空间告警后，先确认目标节点是否为 **nginx-node-1（10.33.16.42 / ops-pangu，主节点）**。
本 SOP 仅适用于主节点 `/home/logs` 磁盘满问题，其他节点或路径不适用。

### 前置条件

- **权限**：aiagent 用户，通过跳板机（10.33.16.184:9166，root 登录）SSH 登录目标节点
- **登录方式**：SSH 到跳板机后，使用预设别名直接登录
  ```bash
  # Step 1: 登录跳板机
  ssh root@10.33.16.184 -p 9166
  # Step 2: 从跳板机直接登录（已配置 SSH alias + key）
  ssh ops-pangu
  ```

### 操作步骤

**Step 1 — 登录目标节点**

```bash
# Step 1: 登录跳板机
ssh root@10.33.16.184 -p 9166
# Step 2: SSH 到 nginx 主节点
ssh ops-pangu
```

**Step 2 — 确认磁盘使用情况**

```bash
df -h | grep -E 'Filesystem|/home'
```

确认 `/home` 分区使用率是否接近 100%。

**Step 3 — 定位大文件**

```bash
cd /home/logs
du -sh * | sort -rh | head -10
```

按大小排序，取最大的文件。输出示例：

```
2.3G    access.log
856M    error.log
120M    access.log.1
```

**Step 4 — 清空目标文件**

```bash
# 清空最大的日志文件（用实际文件名替换 {FILENAME}）
echo '' > /home/logs/{FILENAME}.log
```

> ⚠️ 注意：
> - 文件名必须准确，避免清错文件
> - 仅清空单个最大文件，如果仍需释放空间，重复 Step 3-4 清空次大文件
> - `echo '' >` 会立即截断文件，正在写入的日志可能丢失数秒内容，属可接受范围

**Step 5 — 验证释放效果**

```bash
# 等待 20 秒让文件系统刷新
sleep 20
df -h | grep -E 'Filesystem|/home'
```

对比操作前后 `/home` 使用率，确认已回落至正常水平。

### 验证

- 检查 `/home` 使用率：`df -h | grep /home`
- 检查 Nginx 进程正常：`systemctl status nginx`
- 检查 VIP 在主节点：`ip addr show | grep 10.33.16.244`（应有该 IP）

### 回滚

此操作为不可逆（日志已截断）。如需保留日志排查历史问题，操作前先备份：

```bash
cp /home/logs/{FILENAME}.log /home/logs/{FILENAME}.log.bak.$(date +%Y%m%d_%H%M%S)
```

### 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| 清空后磁盘未见明显下降 | 有进程仍持有文件句柄，空间未释放 | 重启 Nginx 进程：`systemctl reload nginx` |
| `/home/logs` 目录不存在 | 告警来源非 nginx-node-1，或日志路径配置不同 | 确认告警节点 IP，如为从节点（10.33.16.43）则排查从节点日志路径 |
| `du -sh *` 无大文件但磁盘仍满 | 可能是已被删除但未释放的文件（orphaned inode） | `lsof \| grep deleted \| grep /home` 查找并重启对应进程 |
| 告警反复出现 | 日志轮转配置不合理，日志增长速度 > 清理频率 | 检查 logrotate 配置，缩短轮转周期或调小保留份数 |


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
