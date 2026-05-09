# 商城后端（lumapepe）WebSocket 代理换票

RAG 提供 **内部 HTTP 接口**，用共享密钥签发与 `/ws/chat` 一致的 JWT，避免在 lumapepe 进程或运维主机上配置 `JWT_SECRET`。

## RAG 环境变量

| 变量 | 说明 |
|------|------|
| `RAG_PROXY_INTERNAL_KEY` | **必填**（启用换票）。长随机串，与 lumapepe `token-fetch-api-key` 一致。 |
| `RAG_PROXY_TOKEN_SUBJECT` | 可选，JWT `sub`，默认 `lumapepe-proxy`。 |
| `RAG_PROXY_TOKEN_ROLE` | 可选，JWT `role`，默认 `service`。 |
| `RAG_PROXY_TOKEN_TTL_SECONDS` | 可选，有效期秒数，默认 `604800`（7 天），范围 60～2592000。 |

## 接口

- `POST /internal/v1/rag-proxy/token`  
  - Header: `X-RAG-Proxy-Key: <与 RAG_PROXY_INTERNAL_KEY 相同>`  
  - 响应 JSON：`token`、`expires_in`、`token_type`  

- `GET /internal/v1/rag-proxy/health`  
  - 返回 `internal_proxy_token: enabled|disabled`（是否已配置密钥，不泄露内容）

## 安全

- **仅内网**暴露上述路径（防火墙、Nginx `allow`、K8s NetworkPolicy）。
- 不要将 `RAG_PROXY_INTERNAL_KEY` 提交到仓库。

### Nginx 示例（仅允许商城出口 IP）

```nginx
location /internal/ {
    allow 10.0.0.0/8;
    deny all;
    proxy_pass http://rag_backend;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## lumapepe 配置示例

在 `application.yaml` / `application-local.yaml` 的 `lumapepe.kefu.rag` 下：

```yaml
token-fetch-url: http://<rag内网或公网IP>/internal/v1/rag-proxy/token
token-fetch-method: POST
token-fetch-api-key: <与 RAG_PROXY_INTERNAL_KEY 相同>
token-fetch-api-key-header: X-RAG-Proxy-Key
```

清空或省略 `KEFU_RAG_JWT` / `ws-bearer-token-file` 时，代理会在需要时自动拉取 JWT。
