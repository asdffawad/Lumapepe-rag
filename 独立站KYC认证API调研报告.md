# 独立站用户身份认证 API 调研报告

> **项目背景**：独立站面向美国、新加坡等国际市场，用户参加活动需进行护照上传 + 活体人脸比对认证
> **调研日期**：2026年4月

---

## 一、需求分析

| 需求项 | 说明 |
|---|---|
| 证件上传 | 护照（全球护照支持） |
| 活体检测 | 人脸比对，确保真人参与 |
| 目标市场 | 美国、新加坡及全球用户 |
| 合规要求 | GDPR（欧盟）、CCPA（美国加州）、新加坡 PDPA / MAS |
| 业务场景 | 用户参加活动前的身份认证 |

---

## 二、方案对比

### 2.1 国际 KYC 服务商对比

| 指标 | **Jumio** | **Onfido**（Entrust IDV） | **Veriff** |
|---|---|---|---|
| 定位 | 企业级高合规 | 开发者友好 | 高转化率 / UX 优先 |
| 护照支持 | 5,000+ 证件类型 | 2,500+ 证件类型 | 230+ 国家及地区 |
| 国家覆盖 | 200+ | 195+ | 全球 |
| 平均验证耗时 | 10–15 秒 | 6–10 秒 | < 6 秒 |
| 首次通过率 | 高（AI驱动） | 较高 | 95%+ |
| 活体检测方式 | 3D 活体 + AI/ML | 主动 / 被动活体 | 被动活体（视频优先） |
| 欺诈检测 | AI/ML + AML 筛查 | 欺诈信号检测 | 背景视频分析 |
| Web SDK 大小 | ~5–8 MB | ~2–4 MB | ~2–3 MB |
| 集成难度 | 中等 | 中等（Studio 低代码） | 低（2–4 小时） |

### 2.2 价格对比（2026年最新）

#### 一线大厂（Veriff / Onfido / Jumio）

| 套餐 | 单次验证 | 月最低消费 | 说明 |
|---|---|---|---|
| **Veriff Essential** | $0.80 / 次 | $49/月 | 全自动 AI 决策、护照 OCR、生物识别/活体检测 |
| **Veriff Plus** | $1.39 / 次 | $99/月 | 含 Essential + 人工复核 + 视频录制 + 增强欺诈检测 |
| **Veriff Premium** | $1.89 / 次 | $209/月 | 含 Plus + 品牌定制 + Hosted 验证页 + PDF 导出 |
| **Onfido 中小企业** | $1.50–$3.00 / 次 | 需联系销售 | 平台费 + 按次计费 |
| **Onfido 企业版** | 需联系销售 | 年合同 $30,000–$120,000 | — |
| **Jumio 中量级** | $2.00–$4.00 / 次 | 需联系销售 | — |
| **Jumio 企业版** | 需联系销售 | 年合同 $25,000–$1,000,000+ | 按量级阶梯 |

#### 低价平价方案（推荐重点关注）

| 服务商 | 单次价格 | 月费/最低消费 | 护照支持 | 活体检测 | 特点 |
|---|---|---|---|---|---|
| **Shufti Pro** | **$1.00 / 次** | **免费计划每月 10 次** | 230+ 国家，3,000+ 证件 | 3D 活体 + Deepfake 检测 | AML 筛查强大，$1.00 起（年验证量 2,500–20,000 次档） |
| **iDenfy** | **$1.35 / 次起** | 最低月承诺 $135 | 全球护照 | 生物识别 + 活体 | **失败不额外收费**（可选仅收成功 +$0.50），企业版量大可至 $0.55 |
| **Microblink** | **无公开定价** | 需联系销售询价 | 全球护照 | ✅ | 专注文档 OCR/扫描，深度集成，无标准公开价目 |
| **IDWise** | **$1.00 / 次** | 无月费 | 200+ 国家，13,000+ 证件 | 人脸比对 + 活体 | **一口价**，含免费重试，无月费 |
| **Persona** | **$250/月起**（需签 12 个月合同） | **最低 $250/月** | 全球护照 | ✅ | **无按次计费**，按月套餐，Startup 项目有免费额度 |
| **Face++（旷视）** | **¥0.02–0.06 纯API** | 无月费、无最低消费 | 需自行拼装 | 需 FaceID 企业版（需询价） | **成本极低**，但需自己拼装 KYC 流程 |

#### 月度成本估算（以每月 1,000 次验证为例）

| 服务商 | 套餐 | 月费用 | 年费用 |
|---|---|---|---|
| **Shufti Pro** | $1.00/次（2,500–20,000 次/年档） | **$1,000** | **$12,000** |
| **Shufti Pro** | Free 计划（每月 10 次上限） | **$0** | **$0** |
| **iDenfy** | $1.35/次（Basic） | $1,350 | $16,200 |
| **iDenfy** | 企业版量大（$0.55/次参考） | ~$550 | ~$6,600 |
| **IDWise** | $1.00/次（无月费） | **$1,000** | **$12,000** |
| **Veriff** | Essential（$0.80/次 + $49/月） | $800 + $49 | $10,188 |
| **Veriff** | Plus（$1.39/次 + $99/月） | $1,390 + $99 | $17,868 |
| **Veriff** | Premium（$1.89/次 + $209/月） | $1,890 + $209 | $25,188 |
| **Persona** | Essential（$250/月，需签 12 个月） | **$250** | **$3,000**（年付优惠） |
| **Onfido** | 中量级参考（$2.00/次） | ~$2,000 | ~$24,000 |
| **Jumio** | 中量级参考（$2.50/次） | ~$2,500 | ~$30,000 |
| **Face++（旷视）** | ¥0.02–0.06/次 | ¥0（充值余额永不过期） | ¥0 |

> 注：以上价格为官网公开定价（2026年4月核实）。iDenfy 可选"仅收成功验证"模式（基础版 +$0.50/次），企业版量大可至 $0.55/次，需联系销售。Persona 为月套餐模式无按次计费。Veriff Essential/Plus/Premium 附加模块（PEP筛查+$0.64、持续监控+$0.09、数据保留+$0.30）另按次累加。月费按 1,000 次验证计算。

#### 影响成本的关键因素

| 因素 | 说明 |
|---|---|
| 量大折扣 | 年验证量 20万+ 可谈到 $1.00–$2.50/次 |
| 附加模块 | AML 筛查、PEP 检查、持续监控均为额外收费 |
| 实施费 | 企业版通常有一次性实施费 $3,000–$50,000+ |
| 谈判空间 | 对比多家供应商报价，可谈下 20–35% 的折扣 |
| 失败率 | iDenfy 仅收成功——验证失败率高的场景更划算 |

### 2.3 合规认证对比

| 认证 | **Jumio** | **Onfido** | **Veriff** | **Shufti Pro** | **iDenfy** | **IDWise** | **Face++** |
|---|---|---|---|---|---|---|
| SOC 2 Type II | ✅ | ✅ | ✅ | ✅ | ✅ | — | — |
| ISO 27001 | ✅ | ✅ | ✅ | ✅ | ✅ | — | — |
| GDPR | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — |
| CCPA | ✅ | ✅ | — | ✅ | — | — | — |
| PCI DSS | — | — | ✅ | — | — | — | — |
| AML 筛查 | ✅ | ✅ | — | ✅ | ✅ | ✅（可选 $0.25/次） | — |
| 数据驻留 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 新加坡 MAS | ✅ | — | — | ✅ | — | — | — |
| 国际 KYC 合规 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ 需确认 |

### 2.4 集成体验对比

| 项目 | **Jumio** | **Onfido** | **Veriff** | **Shufti Pro** | **iDenfy** | **IDWise** | **Face++** |
|---|---|---|---|---|---|---|---|
| API 文档质量 | 完善，较复杂 | **最好**（Studio 可视化） | 清晰简洁 | 良好 | 一般 | 良好 | 中文文档，较为详细 |
| Webhook 支持 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Web SDK | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ 需自行开发 |
| 移动 SDK | iOS / Android | iOS / Android | iOS / Android / Flutter / RN | iOS / Android / Flutter / RN | iOS / Android | iOS / Android / Flutter | iOS / Android |
| 无代码方案 | — | Studio（低代码） | Hosted Page（无代码） | Journey Builder（拖拽） | — | ✅（SDK） | — |
| 免费额度 | — | — | 15天试用（50次） | **10 次/月 永久免费** | 14天试用（10次） | 30天免费试用 | 人脸检测等部分 API 免费 |
| 部署方式 | 云端 | 云端 | 云端 | 云端 / 本地 / BYOS | 云端 | 云端 | 云端 / 本地 |
| 一站式 KYC | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ 需自行拼装 |

---

## 三、国内备选方案

> 适用于主业务在国内、偶尔有海外用户的场景

| 服务商 | 护照 OCR | 活体检测 | 合规 | 说明 |
|---|---|---|---|---|
| **阿里云实人认证** | ID_OCR_MAX（全球护照） | 金融级活体 | 支持新加坡 MAS | 全球护照支持好 |
| **腾讯云慧眼** | 综合认证 | 活体检测 + NFC | 99.8% 准确率 | 高安全性 |
| **百度智能云** | 需配合 OCR 接口 | rest/2.0/face/v4/verify | V4 接口 2026年更新 | 偏国内 |
| **Face++（旷视）** | 通用 OCR 需定制模板 | FaceID 企业版（询价） | ⚠️ 需确认国际合规 | 国内口碑极佳，API 成本极低 |

---

## 四、选型建议

### 4.1 决策矩阵

| 优先级 | 推荐方案 | 理由 |
|---|---|---|
| **预算优先 / 起步阶段** | **Shufti Pro** | $1.00/次，10 次/月 永久免费计划，无实施费 |
| **极低成本 / 技术能力强** | **Face++** | ¥0.02–0.06/次，纯 API 成本极低，无月费 |
| 护照覆盖最重要 | **Veriff** | 12,500+ 证件类型，覆盖最广 |
| 最快集成 / 最小技术投入 | **Veriff** | SDK 最小、无代码方案、集成最快 |
| 开发者体验 / 灵活工作流 | **Onfido** | Studio 可视化编排、文档最好 |
| 企业级高合规 / AML | **Jumio** | 最老牌、AML 筛查、CCPA+GDPR 双认证 |
| 综合实力最强 | **Jumio** 或 **Onfido** | 行业头部，按报价方案选择 |

### 4.2 建议方案

```
独立站 + 美国/新加坡/全球用户 + 活动认证

首选：Shufti Pro（预算优先）
  - 价格透明：$1.00/次（年验证量 2,500–20,000 次档）
  - 10 次/月 永久免费计划，可先测试
  - 230+ 国家护照支持，3,000+ 证件类型
  - 3D 活体 + Deepfake 检测
  - AML 筛查（1,700+ 黑名单）——新加坡 MAS 合规
  - API + Web SDK + 拖拽工作流，集成快速
  - 无月费、无实施费

次选：Face++（成本最低，技术能力强团队适用）
  - 纯 API 成本极低：¥0.02–0.06/次
  - 无月费、无最低消费，充值余额永久有效
  - 人脸检测 API 免费可试用
  - 需自行拼装：护照 OCR + 活体检测 + 人脸比对
  - 活体检测需联系 FaceID 企业版询价
  - 国际合规文档需单独确认

次选：Veriff（体验优先）
  - 护照覆盖广（230+ 国家）
  - 集成最快（2–4 小时）
  - SDK 最小，对前端性能友好
  - 首次通过率高（95%+），用户体验最好
  - 价格透明：$0.80–$1.89/次 + 月费，无实施费，resubmission 免费
  - 注意：PEP 筛查等附加功能另计

备选：iDenfy（验证失败率高的场景）
  - $1.35/次起（Basic），企业版量大可至 $0.55/次
  - 可选"仅收成功验证"模式（+$0.50/次）
  - AML 合规，14天试用

备选：Jumio
  - 如果 AML 合规是硬性要求且体量非常大（年 20万+ 次）
  - 新加坡 MAS 合规文档最完善
  - 注意：起步价较高，需年合同

备选：Onfido
  - 如果开发团队重视 API 文档质量
  - 如果需要灵活的工作流编排（Studio）
  - 年合同门槛 $30,000 起
```

### 4.3 选型决策树

```
Start
 │
 ├─ 预算极度敏感 / 起步阶段
 │   ├─ Shufti Pro（$1.00/次，10次/月免费，先测先用）
 │   └─ Face++（¥0.02–0.06/次，需自行拼装）
 │
 ├─ 月验证量 < 500 次
 │   ├─ 预算优先 → Shufti Pro（$1.00/次）
 │   ├─ 成本极致 → Face++（¥0.02–0.06/次，需技术拼装）
 │   └─ 体验优先 → Veriff Essential（$0.80/次 + $49/月）
 │
 ├─ 月验证量 500–2,000 次
 │   ├─ 预算优先 → Shufti Pro / iDenfy（$1.00–$1.35/次）
 │   └─ 合规优先 → 联系 Onfido / Jumio 报价格
 │
 ├─ 月验证量 > 2,000 次
 │   ├─ 联系 Shufti Pro 企业版（量大折扣）
 │   ├─ 联系 Veriff 企业版（量大折扣）
 │   ├─ 联系 iDenfy 企业版（量大可至 $0.55/次）
 │   ├─ 联系 Onfido 企业版
 │   └─ 联系 Jumio 企业版
 │
 └─ 需要 AML 筛查 / 持续监控
     ├─ 预算优先 → Shufti Pro（AML 包含，$1.00 起）
     └─ 高合规 → Onfido 或 Jumio（更成熟）
```

---

## 五、接入方式详解

> 以下按服务商分别说明接入步骤，包含后端 Token 生成、前端 SDK 初始化、Webhook 接收三大核心环节。

### 5.0 通用接入架构

> 以下按服务商分别说明接入步骤，包含后端 Token 生成、前端 SDK 初始化、Webhook 接收三大核心环节。

### 5.1 Shufti Pro（首选）

**接入方式**：RESTful API + Web SDK（可选）+ Webhook 回调

#### 后端 — 生成 Verification Request

```python
import hashlib, hmac, base64, time, requests, uuid

SECRET_KEY = "your_secret_key"   # 从 Shufti 后台获取
CLIENT_ID  = "your_client_id"   # 从 Shufti 后台获取

def create_shufti_request(user_id: str, callback_url: str):
    # 构造请求体
    payload = {
        "reference_id": str(uuid.uuid4()),        # 唯一请求 ID
        "callback_url": callback_url,             # Webhook 回调地址
        "email": f"{user_id}@example.com",
        "country": "",                             # 空=自动检测护照
        "verification_mode": "image_only",        # image_only / video / audio
        "document": {
            "supported_types": ["passport", "id_card", "driving_license"],
            "name": {"first_name": "", "last_name": ""},
        },
        "face": {},                                # 活体 + 人脸比对
        "address": {},
    }

    # 生成签名：base64(SHA256(secret_key + reference_id + timestamp))
    timestamp = str(int(time.time()))
    string_to_sign = SECRET_KEY + payload["reference_id"] + timestamp
    signature = base64.b64encode(
        hashlib.sha256(string_to_sign.encode()).digest()
    ).decode()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {base64.b64encode(f'{CLIENT_ID}:{SECRET_KEY}'.encode()).decode()}",
        "X-Signature": signature,
        "X-Timestamp": timestamp,
    }

    resp = requests.post(
        "https://api.shuftipro.com/",
        json=payload,
        headers=headers,
        timeout=30,
    )
    return resp.json()  # 包含 verification_url 和 reference_id
```

#### 前端 — 初始化 Web SDK（可选，不用 SDK 也可直接跳转 verification_url）

```html
<!-- 方式一：直接跳转 URL（最简） -->
<a href="https://api.shuftipro.com/?verify_request=VERIFICATION_REQUEST">
  开始身份认证
</a>

<!-- 方式二：嵌入 iframe -->
<iframe src="https://api.shuftipro.com/?verify_request=VERIFICATION_REQUEST"
        width="100%" height="600px" frameborder="0"></iframe>

<!-- 方式三：Web SDK（npm） -->
<!-- npm install shuftipro-web-sdk -->
<script src="https://cdn.jsdelivr.net/npm/shuftipro-web-sdk/dist/sp-sdk.js"></script>
<script>
  const client = new ShuftiPro({ environment: "sandbox" });  // 或 "live"
  client.init({
    verify_request: "VERIFICATION_REQUEST_FROM_BACKEND",
    onComplete: (data) => console.log("验证完成", data),
    onError:   (err)  => console.error("验证失败", err),
  });
</script>
```

#### 后端 — 接收 Webhook 回调

```python
from flask import Flask, request, jsonify
import hashlib, base64

app = Flask(__name__)
SECRET_KEY = "your_secret_key"

@app.route("/webhook/shufti", methods=["POST"])
def shufti_webhook():
    payload    = request.get_json()
    signature  = request.headers.get("X-Signature", "")
    timestamp  = request.headers.get("X-Timestamp", "")

    # 验签：确保请求确实来自 Shufti Pro
    expected = base64.b64encode(
        hashlib.sha256(f"{SECRET_KEY}{payload.get('reference_id')}{timestamp}".encode()).digest()
    ).decode()
    if signature != expected:
        return "invalid signature", 401

    event    = payload.get("event")
    ref_id   = payload.get("reference_id")
    status   = payload.get("verification_result", {}).get("result")
    # status: "accepted" | "declined" | "review"

    if event == "verification.completed":
        if status == "accepted":
            # 用户认证通过，更新用户状态
            update_user_kyc_status(ref_id, "approved")
        elif status == "declined":
            update_user_kyc_status(ref_id, "rejected")
        else:
            update_user_kyc_status(ref_id, "manual_review")

    return "", 200
```

#### 接入要点

| 项目 | 说明 |
|---|---|
| 沙箱地址 | `https://api.shuftipro.com/`（测试环境） |
| 正式地址 | `https://api.shuftipro.com/`（生产环境，通过 header 区分） |
| 签名算法 | SHA256 + Base64，拼接方式：`secret_key + reference_id + timestamp` |
| 语言 SDK | PHP / Node.js / Python / Java / .NET（官方封装） |
| 测试数据 | 后台提供 Test Documents，可在沙箱模拟各种验证结果 |

---

### 5.2 Veriff（体验优先备选）

**接入方式**：SDK / Hosted Page + REST API + Webhook

#### 后端 — 创建验证 Session

```python
import hmac, hashlib, requests, uuid

API_KEY    = "your_api_key"       # Veriff 后台获取
SHARED_SECRET = "your_secret"     # 用于签名
BASE_URL   = "https://api.veriff.com"

def create_veriff_session(user_id: str, callback_url: str):
    session_id = str(uuid.uuid4())
    timestamp  = str(int(time.time() * 1000))

    # 构造签名
    sign_string = f"+{API_KEY}\n{session_id}\n{timestamp}"
    signature   = hmac.new(
        SHARED_SECRET.encode(), sign_string.encode(), hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type":      "application/json",
        "X-AUTH-CLIENT":     API_KEY,
        "X-HMAC-SIGNATURE":  signature,
        "X-TIMESTAMP":       timestamp,
    }

    payload = {
        "sessionId": session_id,
        "person": {
            "id": user_id,
        },
        "verification": {
            "callbackUrl": callback_url,
            "languages": ["en", "zh"],
        },
    }

    resp = requests.post(
        f"{BASE_URL}/v1/sessions",
        json=payload,
        headers=headers,
    )
    data = resp.json()
    return {
        "session_id":  session_id,
        "session_token": data.get("sessionToken"),
        "verification_url": data.get("verificationUrl"),
    }
```

#### 前端 — 跳转 Hosted Verification Page

```html
<!-- 最简方式：直接跳转 Hosted Page -->
<a id="veriff-link" href="https://alchemy.veriff.com/v2/verify/TOKEN">
  开始身份认证
</a>

<!-- 或者用 JS SDK -->
<script src="https://cdn.jsdelivr.net/npm/@veriff/js-sdk"></script>
<script>
  const veriff = Veriff({
    apiKey: "your_api_key",
    parentId: "veriff-container",
    sessionToken: "SESSION_TOKEN_FROM_BACKEND",
    onComplete: (response) => {
      console.log("验证完成:", response);
      // 发送 sessionToken 给后端查结果
      fetch("/api/kyc/check", {
        method: "POST",
        body: JSON.stringify({ sessionToken: response.sessionToken }),
      });
    },
  });
  veriff.launch();
</script>
<div id="veriff-container"></div>
```

#### 后端 — 接收 Webhook

```python
import hmac, hashlib, json
from flask import Flask, request

SHARED_SECRET = "your_secret"

@app.route("/webhook/veriff", methods=["POST"])
def veriff_webhook():
    body       = request.get_data()
    signature  = request.headers.get("X-HMAC-SIGNATURE", "")
    timestamp  = request.headers.get("X-TIMESTAMP", "")

    # 验签
    expected = hmac.new(
        SHARED_SECRET.encode(), f"{timestamp}.{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    if signature != expected:
        return "invalid", 401

    payload = json.loads(body)
    code    = payload.get("decision", {}).get("status")  # "approved" | "declined" | "referral"
    veriff_id = payload.get("verification", {}).get("id")

    if code == "approved":
        update_user_kyc_status(veriff_id, "approved")
    elif code == "declined":
        update_user_kyc_status(veriff_id, "rejected")
    else:
        update_user_kyc_status(veriff_id, "review")

    return "", 200
```

#### 接入要点

| 项目 | 说明 |
|---|---|
| 测试环境 | `https://alchemy.veriff.com/` |
| 正式环境 | `https://station.veriff.com/` |
| 签名算法 | HMAC-SHA256，格式：`timestamp.body`，hex 编码 |
| 建议 | 优先使用 Hosted Page（无代码），2–4 小时可完成集成 |

---

### 5.3 Face++（旷视，极低成本方案）

**接入方式**：纯 REST API 拼装（无 SDK）+ Webhook / 轮询

> Face++ 无一站式 KYC SDK，需自行串联：通用 OCR → 人脸检测 → 人脸比对 → FaceID 活体。

#### FaceID 活体检测接入（企业版，需联系销售开通）

```python
import hmac, hashlib, time, base64, requests

API_KEY    = "your_faceid_api_key"
API_SECRET = "your_faceid_secret"
BIZ_TOKEN  = "从 FaceID 控制台预生成的 biz_token"

def generate_faceid_sign(params: dict) -> str:
    """生成 FaceID 请求签名（HMAC-SHA1）"""
    sorted_keys = sorted(params.keys())
    pairs = [f"{k}={params[k]}" for k in sorted_keys if params[k]]
    sign_str = "&".join(pairs)
    return hmac.new(
        API_SECRET.encode(), sign_str.encode(), hashlib.sha1
    ).hexdigest()

def faceid_liveness_check(image_base64: str):
    url = "https://api-sgp.megvii.com/faceid/v5/sdk/liveness"  # 新加坡节点

    params = {
        "api_key":         API_KEY,
        "biz_token":       BIZ_TOKEN,
        "data_type":       "0",       # 0=SDK采集, 1=自传图片
        "sign":            "",       # 稍后填充
        "sign_version":    "hmac_sha1",
        "face_risk":       "1",       # 返回欺诈风险信息
        "device_risk":     "1",       # 返回设备风险信息
    }
    params["sign"] = generate_faceid_sign(params)

    files = {"image": ("face.jpg", base64.b64decode(image_base64), "image/jpeg")}

    resp = requests.post(url, data=params, files=files, timeout=30)
    result = resp.json()

    # result_code: 1000=活体通过, 4100=攻击检测（假脸/3D面具）
    if result.get("result_code") == 1000:
        return {"passed": True, "confidence": result.get("confidence")}
    else:
        return {"passed": False, "reason": result.get("reason")}
```

#### 护照 OCR（通用文字识别 V2，需定制模板）

```python
import base64, hashlib, time, requests

API_KEY    = "your_ocr_api_key"
API_SECRET = "your_ocr_secret"

def passport_ocr(image_base64: str):
    # Face++ 通用 OCR（非护照专用，需自行解析字段）
    url = "https://api-cn.faceplusplus.com/v2/vision/ocridcard"

    params = {
        "api_key":      API_KEY,
        "api_secret":   API_SECRET,
        "image_base64": image_base64,
        "legality":     "1",        # 返回证件合规性分析
    }

    resp = requests.post(url, data=params, timeout=30)
    result = resp.json()

    # 返回结构化信息（需自己映射护照字段）
    return {
        "name":        result.get("name"),
        "id_number":   result.get("id_number"),
        "gender":      result.get("gender"),
        "birthday":    result.get("birthday"),
        "nationality": result.get("nationality"),
        "address":     result.get("address"),
        "legality":    result.get("legality"),  # 证件真伪评分
    }
```

#### 接入要点

| 项目 | 说明 |
|---|---|
| FaceID 企业版申请 | 联系 business@megvii.com 或 400-6700-866 |
| 新加坡节点 | `https://api-sgp.megvii.com/faceid/v5/` |
| 国内节点 | `https://api-cn.faceplusplus.com/` |
| 签名算法 | HMAC-SHA1，参数按字母排序拼接 |
| 最大问题 | 护照 OCR 无现成模板，需自行解析 + 活体需企业版授权 |
| 建议 | FaceID 企业版开通后再评估接入复杂度 |

---

### 5.4 iDenfy（仅收成功，备选）

```python
import base64, requests

CLIENT_ID  = "your_client_id"
CLIENT_SECRET = "your_client_secret"
CALLBACK_URL = "https://your-domain.com/webhook/idenfy"

# 1. 创建验证 Session
resp = requests.post(
    "https://ivs.idenfy.com/api/v2/token",
    auth=(CLIENT_ID, CLIENT_SECRET),
    json={
        "callbackUrl": CALLBACK_URL,
        "language": "EN",
        "docType": "PASSPORT",
    },
)
data = resp.json()
# 返回 { authToken, redirectUrl }

# 2. 前端跳转到 redirectUrl

# 3. Webhook 接收结果
# POST /webhook/idenfy
# payload: { verificationId, status, final, result }
# status: "APPROVED" | "DENIED" | "EXPIRED"
@app.route("/webhook/idenfy", methods=["POST"])
def idenfy_webhook():
    payload = request.get_json()
    if payload.get("final"):
        status = payload.get("result", {}).get("status")
        update_user_kyc_status(payload["verificationId"], status.lower())
    return "", 200
```

---

### 5.5 接入方式对比总览

| 项目 | Shufti Pro | Veriff | Face++ | iDenfy | IDWise | Persona |
|---|---|---|---|---|---|---|
| **后端语言示例** | Python | Python | Python | Python | — | — |
| **SDK 类型** | JS Web SDK | JS SDK / Hosted | 无（纯 API） | JS SDK | JS SDK | JS SDK |
| **Token 生成** | 后端签名 | 后端签名 | FaceID 控制台预生成 | 后端请求 | 后端请求 | 后端请求 |
| **签名算法** | SHA256+Base64 | HMAC-SHA256 | HMAC-SHA1 | Basic Auth | — | Bearer Token |
| **Webhook 验签** | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| **SDK vs API** | 两者皆可 | SDK / Hosted Page | 仅 API | 两者皆可 | SDK | 两者皆可 |
| **官方 SDK 包** | PHP/Node/Py/Java/.NET | Node.js（社区）/ 无官方 | 无 | — | — | — |
| **沙箱/测试环境** | ✅ | ✅ | 部分 API 免费 | ✅ | ✅ | ✅ |
| **预计接入工时** | 4–8 小时 | 2–4 小时 | 20–40 小时 | 6–10 小时 | 6–12 小时 | 8–16 小时 |

> 注：Face++ 接入工时最长，因为需要自行串联多个 API、解析护照 OCR 字段、实现活体检测逻辑、错误处理等。

---

### 5.6 统一 Webhook 签名验签模板

```python
# 统一 Webhook 验签中间件（适配 Shufti / Veriff / iDenfy）
from functools import wraps
import hmac, hashlib, base64

SIGNATURE_HEADER = {
    "shufti": "X-Signature",
    "veriff":  "X-HMAC-SIGNATURE",
    "idenfy":  "x-idenfy-signature",
}

def verify_webhook(provider: str, secret: str):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            from flask import request
            body      = request.get_data()
            header_key = SIGNATURE_HEADER.get(provider, "")
            signature = request.headers.get(header_key, "")

            if provider == "shufti":
                ts   = request.headers.get("X-Timestamp", "")
                exp  = base64.b64encode(hashlib.sha256(
                    f"{secret}{request.get_json().get('reference_id')}{ts}".encode()
                ).digest()).decode()
                ok = signature == exp

            elif provider == "veriff":
                ts   = request.headers.get("X-TIMESTAMP", "")
                exp  = hmac.new(secret.encode(),
                    f"{ts}.{body.decode()}".encode(),
                    hashlib.sha256).hexdigest()
                ok = signature == exp

            else:
                ok = True  # 其余按实际情况添加

            if not ok:
                return "invalid signature", 401
            return f(*args, **kwargs)
        return wrapper
    return decorator

# 使用示例
@app.route("/webhook/kyc", methods=["POST"])
@verify_webhook("shufti", "your_shufti_secret")
def kyc_webhook():
    payload = request.get_json()
    # 业务逻辑...
    return "", 200
```

---

## 七、下一步行动计划

| 步骤 | 行动项 | 负责 |
|---|---|---|
| 1 | 注册 Shufti Pro + Veriff 沙箱账号，获取测试凭证 | 产品 / 运营 |
| 2 | 对比测试：护照识别准确率、活体通过率（优先测 Shufti Pro） | 技术 |
| 3 | 联系销售获取正式报价 | 商务 |
| 4 | 确定服务商，评估接入工时（前端 + 后端） | 技术 |
| 5 | 开发联调，测试环境验证 | 技术 |
| 6 | 生产环境部署 + 合规审查 | 技术 + 法务 |

---

## 八、参考链接

| 服务商 | 官网 | 文档 |
|---|---|---|
| Jumio | https://www.jumio.com | https://documentation.jumio.ai |
| Onfido（Entrust IDV） | https://www.onfido.com | https://developer.onfido.com |
| Veriff | https://www.veriff.com | https://docs.veriff.com |
| **Shufti Pro** | https://shuftipro.com | https://shuftipro.com/plans/ |
| **iDenfy** | https://idenfy.com | https://idenfy.com/blog/ |
| **IDWise** | https://www.idwise.com | https://www.idwise.com/pricing/ |
| **Persona** | https://withpersona.com | https://docs.withpersona.com |
| **IDWise** | https://www.idwise.com | https://docs.idwise.com |
| **iDenfy** | https://idenfy.com | https://documentation.idenfy.com |
| **Face++（旷视）** | https://www.faceplusplus.com.cn | https://www.faceplusplus.com.cn/document/guide_docs/api_pricing |
| 阿里云实人认证 | https://www.aliyun.com | https://help.aliyun.com |
| 腾讯云慧眼 | https://cloud.tencent.com | https://console.cloud.tencent.com |

---

## 九、风险提示

1. **价格波动**：Shufti Pro 官网 Essentials 档为 $1.00/次（非 $0.20），iDenfy 为 $1.35/次起（非 $0.55），Persona 为月套餐 $250/月起（非按次计费）；Veriff 附加模块（PEP筛查+$0.64、持续监控+$0.09）另按次累加；Onfido 和 Jumio 仍需商务谈判
2. **Face++ 合规风险**：Face++ 基础 API 成本极低，但护照 OCR 需自行拼装，FaceID 活体检测需企业询价，国际合规文档需单独确认
3. **数据合规**：护照信息属于敏感个人数据，需在隐私政策中明确告知用户，并在用户协议中获取同意
4. **验证失败率**：建议设计友好的重试流程，避免用户流失；Veriff 的 resubmission 免费，设计空间更大
5. **Deepfake 威胁**：随着 AI 生成技术发展，活体检测方案需定期更新，优先选择 AI 能力强的厂商（如 Veriff）
6. **合同条款**：企业版通常要求年合同，提前评估业务增长曲线，避免合同量级与实际不符

---

*本报告基于 2026 年 4 月公开信息整理，实际方案以各家官方最新文档为准。*
