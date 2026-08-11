# LiveKit Server 部署指南

## Docker Compose（本地与集成测试）

仓库的 `docker-compose.yml` 已包含 LiveKit 服务。配置 `.env.local` 后启动：

```powershell
docker compose --env-file .env.local up --build -d
docker compose ps
docker compose logs -f livekit liverag-interview-agent
```

默认本地配置：

```dotenv
LIVEKIT_URL=ws://127.0.0.1:7880
LIVEKIT_API_KEY=liverag-local
LIVEKIT_API_SECRET=liverag-local-development-secret-0001
INTERVIEW_AGENT_NAME=interview-agent
```

Compose 暴露以下端口：

| 端口 | 协议 | 用途 |
| --- | --- | --- |
| 7880 | TCP | LiveKit HTTP/WebSocket 信令 |
| 7881 | TCP | RTC TCP 回退 |
| 7882 | UDP | WebRTC 音频媒体 |

`liverag-interview-agent` 通过 `INTERVIEW_AGENT_NAME` 接收面试任务。前端签发的 token 只能使用服务端的 API Key/Secret；Secret 不得通过浏览器环境变量暴露。

## 生产部署

生产环境应使用公网可访问的 `wss://` 地址，并配置受信任的 TLS 证书与正确的公网 IP。至少需要：

1. 将 `LIVEKIT_URL` 设为浏览器可访问的 `wss://<domain>`。
2. 用随机高强度值替换本地默认 `LIVEKIT_API_KEY` 和 `LIVEKIT_API_SECRET`，通过密钥管理系统注入。
3. 放通 `7880/TCP`、`7881/TCP` 与 `7882/UDP`，或按 LiveKit 官方生产拓扑配置反向代理和端口范围。
4. 在 NAT、负载均衡或 Kubernetes 环境中正确声明公网地址；不要保留本地 Compose 中的内网 `node_ip` 配置。
5. 从目标公网和真实浏览器完成一次音频收发测试，确认 UDP 不被安全组或企业网络阻断。

## 常见排查

- 能建立页面连接但没有声音：检查浏览器麦克风权限、UDP 7882、防火墙和公网地址配置。
- Agent 未加入房间：确认 token 的 dispatch 名称与 `INTERVIEW_AGENT_NAME` 一致，并检查 Interview Agent 容器日志。
- 本地连接失败：确认 `LIVEKIT_URL` 与浏览器访问地址一致；容器内 Agent 使用 Compose 注入的 `ws://livekit:7880`，不要改成浏览器地址。

LiveKit 的版本特性和生产拓扑以其官方文档为准；本项目只维护自身的 Agent、token 与 Compose 集成配置。
