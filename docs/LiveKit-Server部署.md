# 🚀 LiveKit Server 完整部署指南 (自定义密钥 + Nginx 反向代理)

本指南用于在 Linux 服务器上部署生产级 LiveKit 服务。
- **核心服务**：通过 Docker 运行，监听本地 7880 端口。
- **密钥管理**：支持**自定义 API Key 和 Secret**，无需使用生成工具。
- **反向代理**：通过 Nginx 处理外部请求，支持 WebSocket 升级。
- **扩展性**：配置已预留域名和证书位置，待审批通过后直接填入即可。

---

## 1. 前置要求

- **操作系统**: CentOS 7+ / Ubuntu 20.04+ / Debian 11+
- **已安装软件**: Docker, Docker Compose, Nginx
- **防火墙/安全组策略** (必须放行):
  - **TCP**: `80`, `443` (Nginx 对外端口)
  - **UDP**: `50000-60000` (LiveKit 媒体流端口，**至关重要**)

---

## 2. 目录规划

建议在 `/opt` 目录下创建标准工作区：

```bash
mkdir -p /opt/livekit-server/{data,config,ssl}
cd /opt/livekit-server
```

---

## 3. 第一步：准备自定义密钥

既然你已经有了自己定义的密钥，请直接准备好以下两个值：
- **API Key**: 你自定义的标识符 (例如: `my-app-key`)
- **Secret**: 你设定的高复杂度密码 (例如: `SuperSecretString123!@#`)

> **⚠️ 安全提示**：
> - **Key** 可以简短易读。
> - **Secret** 必须足够长且随机（建议 32 位以上），它是真正的凭证，**严禁泄露**。
> - 如果 Secret 中包含特殊字符（如 `:`, `#`），在后续配置文件中需用双引号包裹。

---

## 4. 第二步：配置 LiveKit 核心服务

### 4.1 创建配置文件 `config/livekit.yaml`

使用 `vim` 或 `nano` 创建文件：
```bash
vim config/livekit.yaml
```

填入以下内容（**请替换 `<你的自定义Key>` 和 `<你的自定义Secret>`**）：

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

# 认证密钥配置
keys:
  # 格式: 你的Key: "你的Secret"
  # 注意：如果 Secret 包含特殊字符，务必加上双引号
  <你的自定义Key>: "<你的自定义Secret>"
```

**示例：**
```yaml
keys:
  my-dev-key: "MyVeryLongAndSecureSecretString2024"
```

### 4.2 创建 `docker-compose.yml`

在项目根目录创建文件：
```bash
vim docker-compose.yml
```

填入以下内容：

```yaml
version: '3'

services:
  livekit:
    image: livekit/livekit-server:latest
    container_name: livekit-server
    restart: always
    
    # 【重要】必须使用 host 网络模式，以便直接暴露 UDP 端口给媒体流
    network_mode: host
    
    volumes:
      # 挂载配置文件
      - ./config/livekit.yaml:/etc/livekit.yaml
      # 挂载数据目录
      - ./data:/data
    
    command:
      - --config=/etc/livekit.yaml
```

### 4.3 启动服务

```bash
# 启动容器
docker compose up -d

# 查看日志确认启动成功 (按 Ctrl+C 退出日志视图)
docker compose logs -f
```
*看到 `Starting server` 且无 `panic` 报错即表示成功。*

---

## 5. 第三步：配置 Nginx 反向代理

这是连接外部用户与内部服务的桥梁。

### 5.1 创建 Nginx 配置文件

创建文件 `/etc/nginx/conf.d/livekit.conf`：

```nginx
server {
    # 监听 443 端口 (HTTPS)
    listen 443 ssl;
    
    # [待填写] 域名审批下来后，将 IP 改为域名
    # 当前测试阶段可先填服务器公网 IP
    server_name <你的域名或当前IP>;

    # [待填写] 证书路径
    # 域名下来后，将此处指向正式证书文件
    # 当前测试阶段，可先指向自签名证书 (需提前生成并放置)
    ssl_certificate     /etc/nginx/ssl/<证书文件名>.crt;
    ssl_certificate_key /etc/nginx/ssl/<密钥文件名>.key;

    # SSL 基础优化配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # 【核心】LiveKit 代理配置 (根路径模式)
    location / {
        proxy_pass http://127.0.0.1:7880;
        
        proxy_http_version 1.1;
        # 必须设置以下头以支持 WebSocket 升级
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # 传递真实客户端信息
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # 增加超时时间，防止长连接断开
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}

# 可选：强制 HTTP 跳转 HTTPS
server {
    listen 80;
    server_name <你的域名或当前IP>;
    return 301 https://$host$request_uri;
}
```

### 5.2 临时证书处理 (可选)

如果此时还没有正式证书，为了能让 Nginx 启动，你可以先生成一个临时的自签名证书：

```bash
# 创建目录
mkdir -p /etc/nginx/ssl

# 生成临时自签名证书 (有效期365天)
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/temp.key \
  -out /etc/nginx/ssl/temp.crt \
  -subj "/C=CN/ST=State/L=City/O=Org/CN=temp"
```
*然后在 Nginx 配置中将证书路径指向 `temp.crt` 和 `temp.key`。等正式证书下来后，直接覆盖这两个文件即可。*

### 5.3 检查并重启 Nginx

```bash
# 检查配置语法
nginx -t

# 重载配置
nginx -s reload
```

---

## 6. 第四步：防火墙与安全组设置

确保流量能穿透服务器。

### 6.1 操作系统防火墙

**CentOS (firewalld):**
```bash
firewall-cmd --permanent --add-port=80/tcp
firewall-cmd --permanent --add-port=443/tcp
firewall-cmd --permanent --add-port=50000-60000/udp
firewall-cmd --reload
```

**Ubuntu (ufw):**
```bash
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 50000:60000/udp
```

### 6.2 云服务器安全组 (关键)

登录你的云厂商控制台（阿里云/腾讯云/AWS等），在**安全组**规则中添加入站规则：
1.  **协议**: TCP, 端口: 80, 443 (来源: 0.0.0.0/0)
2.  **协议**: UDP, 端口: 50000-60000 (来源: 0.0.0.0/0)
    *   *注意：UDP 范围必须与 `livekit.yaml` 中的配置一致，否则音视频流无法传输。*

---

## 7. ✅ 部署完成与后续操作

### 当前状态
- LiveKit 服务已使用**自定义密钥**启动。
- Nginx 已配置好反向代理逻辑。
- **等待项**：域名解析生效、正式证书下发。

### 📅 域名审批通过后的操作清单

一旦你拿到域名（例如 `livekit.example.com`）和正式证书文件：

1.  **上传证书**：将正式 `.crt` 和 `.key` 文件上传到 `/etc/nginx/ssl/`。
2.  **修改 Nginx 配置** (`/etc/nginx/conf.d/livekit.conf`):
    - `server_name`: 改为 `livekit.example.com`
    - `ssl_certificate`: 指向正式证书路径
    - `ssl_certificate_key`: 指向正式密钥路径
3.  **重载 Nginx**:
    ```bash
    nginx -t && nginx -s reload
    ```
4.  **更新客户端代码**:
    - 连接地址：`wss://livekit.example.com`
    - API Key: 使用你自定义的那个 Key。
    - Token 生成：使用你自定义的那个 Secret。

---

## 🔧 常用维护命令

```bash
# 查看 LiveKit 实时日志
docker compose logs -f

# 重启 LiveKit 服务 (修改配置后)
docker compose restart

# 停止服务
docker compose down

# 查看 Nginx 错误日志
tail -f /var/log/nginx/error.log
```