# 🚀 LiveKit Server 完整部署指南

本指南涵盖两种部署方式：
1. **Docker Compose 集成部署**（推荐）：LiveKit 与 LiveRAG 全套服务一键启动
2. **独立部署**：在 Linux 服务器上单独部署 LiveKit + Nginx 反向代理

---

## 方式一：Docker Compose 集成部署（推荐）

项目根目录的 `docker-compose.yml` 已包含 LiveKit 服务，无需额外配置：

```yaml
# docker-compose.yml 中的 livekit 服务
livekit:
  image: livekit/livekit-server:latest
  container_name: liverag-livekit
  restart: unless-stopped
  ports:
    - "7880:7880/tcp"
    - "7881:7881/tcp"
    - "7882:7882/udp"
  environment:
    LIVEKIT_KEYS: "${LIVEKIT_API_KEY:-liverag-local}: ${LIVEKIT_API_SECRET:-liverag-local-development-secret-0001}"
    LIVEKIT_CONFIG: |
      port: 7880
      bind_addresses:
        - 0.0.0.0
      rtc:
        tcp_port: 7881
        udp_port: 7882
        use_external_ip: false
        node_ip: 192.168.3.121   # ← 改为你的实际 IP
```

### 启动全部服务

```bash
# 在项目根目录
docker compose up -d

# 查看所有服务状态
docker compose ps

# 查看 LiveKit 日志
docker compose logs -f livekit
```

### 关键环境变量

在 `.env.local` 中配置：

```bash
# LiveKit 密钥（生产环境务必修改）
LIVEKIT_API_KEY=your-custom-key
LIVEKIT_API_SECRET=your-secure-secret-at-least-32-chars

# 前端使用的 LiveKit 公网地址
LIVEKIT_PUBLIC_URL=ws://your-server-ip:7880
```

### Docker Compose 中的 LiveKit Agent Workers

项目包含两个 LiveKit Agent Worker：

| Worker | agent_name | 用途 |
|--------|-----------|------|
| `liverag-agent` | `my-agent` | 通用语音 RAG 助手 |
| `liverag-interview-agent` | `interview-agent` | 面试教练 |

两者都依赖 LiveKit 做 room dispatch。前端通过 `/api/connection-details` 指定 agent name 来调度对应的 Worker。

---

## 方式二：独立部署（自定义密钥 + Nginx 反向代理）

适用于需要在独立服务器上运行 LiveKit，或需要自定义网络拓扑的场景。

### 1. 前置要求

- **操作系统**: CentOS 7+ / Ubuntu 20.04+ / Debian 11+
- **已安装软件**: Docker, Docker Compose, Nginx
- **防火墙/安全组策略** (必须放行):
  - **TCP**: `80`, `443` (Nginx 对外端口)
  - **UDP**: `50000-60000` (LiveKit 媒体流端口，**至关重要**)

### 2. 目录规划

```bash
mkdir -p /opt/livekit-server/{data,config,ssl}
cd /opt/livekit-server
```

### 3. 准备自定义密钥

- **API Key**: 自定义标识符 (例如: `my-app-key`)
- **Secret**: 高复杂度密码（建议 32 位以上），**严禁泄露**

> 如果 Secret 中包含特殊字符（如 `:`, `#`），在配置文件中需用双引号包裹。

### 4. 配置 LiveKit

创建 `config/livekit.yaml`：

```yaml
port: 7880
bind_addresses:
  - 0.0.0.0

logging:
  level: info

rtc:
  udp_port: 50000
  port_range_start: 50000
  port_range_end: 60000

keys:
  <你的自定义Key>: "<你的自定义Secret>"
```

创建 `docker-compose.yml`：

```yaml
services:
  livekit:
    image: livekit/livekit-server:latest
    container_name: livekit-server
    restart: always
    network_mode: host
    volumes:
      - ./config/livekit.yaml:/etc/livekit.yaml
      - ./data:/data
    command:
      - --config=/etc/livekit.yaml
```

启动：

```bash
docker compose up -d
docker compose logs -f
```

### 5. 配置 Nginx 反向代理

创建 `/etc/nginx/conf.d/livekit.conf`：

```nginx
server {
    listen 443 ssl;
    server_name <你的域名或当前IP>;

    ssl_certificate     /etc/nginx/ssl/<证书文件名>.crt;
    ssl_certificate_key /etc/nginx/ssl/<密钥文件名>.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://127.0.0.1:7880;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}

server {
    listen 80;
    server_name <你的域名或当前IP>;
    return 301 https://$host$request_uri;
}
```

### 6. 临时自签名证书（可选）

```bash
mkdir -p /etc/nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/temp.key \
  -out /etc/nginx/ssl/temp.crt \
  -subj "/C=CN/ST=State/L=City/O=Org/CN=temp"
```

正式证书下来后直接覆盖这两个文件。

### 7. 检查并重启 Nginx

```bash
nginx -t
nginx -s reload
```

### 8. 防火墙设置

**firewalld:**
```bash
firewall-cmd --permanent --add-port=80/tcp
firewall-cmd --permanent --add-port=443/tcp
firewall-cmd --permanent --add-port=50000-60000/udp
firewall-cmd --reload
```

**ufw:**
```bash
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 50000:60000/udp
```

**云服务器安全组：** 在云厂商控制台添加入站规则：
1. TCP: 80, 443 (来源: 0.0.0.0/0)
2. UDP: 50000-60000 (来源: 0.0.0.0/0)

---

## 与 LiveRAG 后端联调

如果 LiveKit 独立部署，需要更新 LiveRAG 的环境变量指向该 LiveKit 实例：

```bash
# .env.local
LIVEKIT_URL=ws://your-livekit-server:7880
LIVEKIT_API_KEY=your-custom-key
LIVEKIT_API_SECRET=your-secure-secret
```

LiveRAG 的 `liverag-api`、`liverag-agent`、`liverag-interview-agent` 都会使用这些凭证连接 LiveKit。

---

## 常用维护命令

```bash
# Docker Compose 集成模式
docker compose ps                    # 查看所有服务状态
docker compose logs -f livekit       # 查看 LiveKit 日志
docker compose restart livekit       # 重启 LiveKit
docker compose down                  # 停止全部服务

# 独立部署模式
docker compose logs -f               # 查看 LiveKit 实时日志
docker compose restart               # 重启 LiveKit（修改配置后）
docker compose down                  # 停止服务

# Nginx
tail -f /var/log/nginx/error.log     # 查看 Nginx 错误日志
nginx -t && nginx -s reload          # 检查配置并重载
```
