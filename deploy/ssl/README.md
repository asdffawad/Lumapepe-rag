# SSL 证书目录

此目录用于存放 SSL 证书文件。

## 所需文件

- `fullchain.pem` - 完整证书链
- `privkey.pem` - 私钥

## 获取免费证书

使用 Let's Encrypt:

```bash
certbot --nginx -d your-domain.com
```

或手动配置:

```bash
# 创建目录
mkdir -p deploy/ssl

# 使用 certonly 获取证书
certbot certonly --manual -d your-domain.com

# 复制证书
cp /etc/letsencrypt/live/your-domain.com/fullchain.pem deploy/ssl/
cp /etc/letsencrypt/live/your-domain.com/privkey.pem deploy/ssl/
```
