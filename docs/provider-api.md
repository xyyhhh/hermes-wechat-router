# 消息提供程序接入说明

适用于插件 0.1.0 及 0.2.0；消息接入协议保持 v1。提供程序只需逐条提交微信新消息，Hermes 负责合并、调用 Jev 和派单。

## 1. 需要配置的内容

- **服务地址**：默认 `http://127.0.0.1:8768`；跨机器使用部署方配置的 HTTPS 地址。
- **共享密钥**：与 Hermes profile 的 `WECHAT_ROUTER_SECRET` 相同，至少 32 字节。
- **来源标识**：例如 `wechat-listener`，必须在插件的 `sources` 列表中（列表为空时不限来源）。

提供程序不需要 Jev key。每条业务请求都需要下文的 HMAC 签名。

## 2. 提交一条消息

`POST /wechat-router/v1/events`，`Content-Type: application/json`，请求体为 **UTF-8 JSON 对象**，一次一条。

```json
{
  "source": "wechat-listener",
  "event_id": "wx-message-123",
  "conversation_id": "wx-group-456",
  "chat_type": "group",
  "sender_id": "wx-user-789",
  "sender_name": "示例联系人",
  "occurred_at": "2026-11-05T10:30:00+08:00",
  "message_type": "text",
  "text": "周五下午三点开项目会，可以吗？",
  "mentions": ["MY_WECHAT_ID"],
  "is_self": false
}
```

| 字段 | 类型 / 必填 | 含义与约束 |
|---|---|---|
| `source` | string / 是 | 稳定的提供程序标识 |
| `event_id` | string / 是 | 在该 `source` 内唯一；重试必须保持不变 |
| `conversation_id` | string / 是 | 稳定聊天 ID：私聊用对话 ID，群聊用群 ID，不能用发送者 ID 代替群 ID |
| `sender_id` | string / 是 | 实际发送者的稳定 ID；自己发出的消息也需要提交 |
| `occurred_at` | string / 是 | 消息原始发送时间，ISO 8601，必须包含 `+08:00` 或 `Z` 等时区 |
| `text` | string / 文本消息必填 | 原文；默认最多 8,000 个字符 |
| `chat_type` | string / 否 | `dm` 或 `group`，默认 `dm`；群聊应明确设为 `group` |
| `conversation_name` | string / 否 | 聊天显示名称，不用于识别聊天 |
| `sender_name` | string / 否 | 发送者显示名称，不用于识别发送者 |
| `is_self` | boolean / 否 | 是否账号本人发出；省略时根据 `owner_ids` 判断，不能传字符串 `"false"` |
| `mentions` | string[] / 否 | 被 @ 的人的稳定 ID；默认空列表，最多 100 项，每项最多 256 字符 |
| `reply_to` | string / 否 | 被引用消息的标识，辅助判断上下文 |
| `message_type` | string / 否 | `text`、`image`、`voice`、`video`、`file`、`link`、`system` 或 `other`，默认 `text` |

ID 字段和 `occurred_at` 长度不超过 256 字符且不能含控制字符；显示名称和 `reply_to` 不超过 256 字符。请求体默认不超过 **128 KiB**（按字节计算）。未列出的字段会返回 400，请先将提供程序原始字段映射为上述字段。

非文本消息可以把已有转写、图片说明或文件说明放进 `text`；未提供文本时插件保存占位说明。插件不会下载媒体、识别图片或转写语音。

同一聊天中双方消息必须使用同一个 `conversation_id`。建议按聊天内的消息顺序提交；插件按接收顺序合并，`occurred_at` 用于理解“明天”等相对时间，不会把延迟到达的消息自动插回旧批次。如果原始消息 ID 只在单个聊天中唯一，可以用 `聊天ID:消息ID` 组成 `event_id`。

## 3. 签名方法

请求头：

```text
X-Wechat-Router-Timestamp: <本次请求的 Unix 秒数>
X-Wechat-Router-Signature: sha256=<小写十六进制 HMAC-SHA256>
```

签名输入为以下前缀的 UTF-8 字节，随后直接拼接原始 body 字节：

```text
v1\n<TIMESTAMP>\n<METHOD大写>\n<PATH_AND_QUERY>\n<BODY_BYTES>
```

Python 核心示例：

```python
body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
timestamp = str(int(time.time()))
path = "/wechat-router/v1/events"
prefix = f"v1\n{timestamp}\nPOST\n{path}\n".encode("utf-8")
signature = "sha256=" + hmac.new(
    secret.encode("utf-8"), prefix + body, hashlib.sha256
).hexdigest()
# 将同一个 body 字节串发送出去，签名后不要再重新序列化 JSON。
```

完整可复用实现见 [examples/send_event.py](../examples/send_event.py)，其中 `submit(base_url, secret, event)` 可复制到提供程序中使用，只依赖 Python 标准库。环境变量运行示例见 [README](../README.md#接收-api)。

签名时间默认允许前后 300 秒。它与 `occurred_at` 不同：前者是本次发送时间，后者始终是消息原始时间。使用反向代理时，签名路径应与 Hermes 实际收到的路径一致，通常是上面的路径，不能把域名加入签名。

## 4. 成功、失败与重试

首次接收返回 **202**，表示消息已经落盘；重复提交相同事件返回 **200**、`duplicate: true`。这两种情况都可以将提供程序本地的投递记录标为成功，不需要等待 Jev 或 Agent 完成。

响应示意（实际还会包含其他字段）：

```json
{"event_id":"wx-message-123","status":"buffered","duplicate":false}
```

| 情况 | 提供程序应如何处理 |
|---|---|
| 超时、断线、429、503 或代理 5xx | 保留事件，使用相同 ID 和相同内容退避重试；例如 1、2、4 秒递增至最多 60 秒，加入少量随机延迟。429 优先遵守 `Retry-After` |
| 400 / 413 | 修正字段或大小；不要原样无限重试 |
| 401 | 检查共享密钥、系统时间、路径和实际发送字节 |
| 409 | 同一 `source`/`event_id` 已有不同内容，检查 ID 生成和重试逻辑 |

每次重试重新生成签名时间和签名，保持消息内容不变。收到 202 后即使响应丢失，重复投递也可以安全查回已接收状态。建议提供程序保留自己的持久化待发送队列，Hermes 暂停或重启时继续重试。

修改、撤回等后续事件应使用新的 `event_id`，可通过 `reply_to` 指向原消息，并在 `text` 中说明变化；原事件不能原地覆盖。`reply_to` 是上下文线索，不是强制修改或取消任务的命令。

## 5. 查询处理状态（可选）

- `GET /wechat-router/v1/health`：无需签名，只检查服务是否在线。
- `GET /wechat-router/v1/events/status?source=wechat-listener&event_id=wx-message-123`：查询消息所在批次、判断和关联任务，未找到时返回 404。
- `GET /wechat-router/v1/status`：查询队列与 token 统计。

后两个 GET 需要签名，METHOD 为 `GET`、body 为空；**查询字符串必须按实际编码和顺序包含在签名路径中**。

消息状态为 `buffered` → `queued` → `processing` → `done`；失败退避时回到 `queued`，超过重试上限后为 `failed`。批次完成的 `outcome` 为 `observed`、`ignored`、`watch` 或 `task_created`。`task_created` 表示任务已交给 Kanban，不代表后续 Agent 已完成。

默认 `observe` 模式会调用 Jev但不建卡。已落盘事件后来进入 `failed` 时，应在 Hermes 端修复并重试批次，提供程序不需要换新消息 ID 再发一遍。
