---
title: k3s-cluster 技术架构
doc_type: tech
service_ids:
  - svc_k3s
service_name: k3s-cluster
tags:
  - 技术文档
  - k3s
  - Kubernetes
  - 容器平台
  - 集群
updated_at: 2026-06-04
---

## k3s-cluster 技术架构

### 概述

K3s 轻量级 Kubernetes 集群，版本 v1.34.3+k3s1。1 个 control-plane 节点 + 2 个 worker 节点 + 1 个 GPU worker 节点（NVIDIA x4）。运行 AI 推理网关、LLM 服务、模型训练等负载。

### 集群节点

| 节点 | IP | 角色 | CPU | 内存 | GPU |
|------|-----|------|-----|------|-----|
| k8s-master | 10.33.16.202 | control-plane + etcd | 4 | 8GB | — |
| k8s-node01 | 10.33.16.203 | worker | 4 | 8GB | — |
| k8s-node02 | 10.33.16.204 | worker | 4 | 8GB | — |
| k8s-gpu-node01 | 10.33.17.234 | GPU worker | 48 | 257GB | NVIDIA x4 |

### 核心组件

| 组件 | 版本 | 用途 |
|------|------|------|
| K3s | v1.34.3 | 轻量级 Kubernetes |
| Traefik | v3.6.12 | Ingress 控制器，暴露 80/443 |
| Calico | — | 网络插件（替代默认 flannel） |
| Containerd | 2.1.5-k3s1 | 容器运行时（master） |
| Docker | 29.1.3 | 容器运行时（worker nodes） |
| NVIDIA GPU Operator | — | GPU 驱动和管理 |
| Prometheus + Grafana | — | 监控栈（monitoring 命名空间） |

### 命名空间

| 命名空间 | 用途 | 关键负载 |
|---------|------|---------|
| 1-pool-10-inference-gateway | AI 推理网关 | gateway-1 ~ gateway-91，端口 9080/9443 |
| 1-pool-10-llm | LLM 推理服务 | ms-4 ~ ms-105，端口 8080 |
| 1-pool-10-sft | 模型微调训练 | training-job-24/26 |
| serving-test | 模型推理测试 | qwen3-6-27b-awq |
| trainning-test | 训练测试 | whisper-stt |
| monitoring | 集群监控 | Prometheus + Grafana + Alertmanager |
| default | 遗留测试服务 | gateway-23/24/25, ms-38~43 |

### 关键端口

| 端口 | 服务 | 说明 |
|------|------|------|
| 6443 | K3s API Server | Kubernetes 管控入口 |
| 80 | Traefik Ingress | HTTP 入口 |
| 443 | Traefik Ingress | HTTPS 入口 |
| 9080 | 推理网关 | AI 推理 HTTP API |
| 9443 | 推理网关 | AI 推理 HTTPS API |
| 8080 | LLM 服务 | 模型推理内部端口 |
| 9090 | Prometheus | 监控指标 |
| 3000 | Grafana | 监控面板 (grafana.k3s.local) |

### 运维要点

- K3s API Server 仅运行在 master 节点，master 故障将导致整个集群不可管理
- GPU 节点 k8s-gpu-node01 为推理/训练专用，资源 48C/257G，4 张 NVIDIA GPU
- Traefik Ingress 已配置 Dashboard，域名包括 grafana.k3s.local、qwen3-27b.k3s.local、whisper.k3s.local
- 监控告警通过 Prometheus + Alertmanager 配置
- worker 节点使用 Docker 而非 containerd，注意 Docker 版本兼容性
