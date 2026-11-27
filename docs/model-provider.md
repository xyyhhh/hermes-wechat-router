# 模型 Provider 与判断提示词

插件 0.3.0 起，打开 Dashboard → 选择运行接收平台的 profile（本项目为 `default`）→ **微信消息分流**，即可配置 provider 与提示词。

## 服务提供商

| 选项 | 请求地址 | 使用的凭据 |
|---|---|---|
| Google Gemini Decisions（0.5.0 起） | 固定为 `https://generativelanguage.googleapis.com/v1beta/decisions` | `GEMINI_API_KEY` |
| TypeSafe 官方 | 固定为 `https://api.typesafe.ai/v1/systemone` | `TYPESAFE_API_KEY` |
| 第三方 · System One 兼容 | 页面填写的 API 地址 | `WECHAT_ROUTER_API_KEY` |

第三方 API 地址可以填写包含版本路径的 Base URL，例如 `https://provider.example/v1`，插件会追加 `/systemone`；也可以填写完整接口 URL，例如 `https://provider.example/custom/v1/systemone`，不会重复追加。模型 ID 必须填写服务商实际提供的名字。

在页面下方的「凭据」区域，保存所选 provider 的 API key。凭据通过 Hermes 的 profile secret 接口保存，已有值不回显，也不放在配置 YAML、批次快照或 URL 中。切换为第三方时不会复用官方 key，也没有失败后自动回退到官方的行为。

远程服务必须使用 HTTPS；本机 `localhost` 或回环 IP 的代理允许 HTTP。URL 不接受用户名、密码、query 或 fragment；TypeSafe/第三方 API key 使用 `Authorization: Bearer ...`；Google 使用 `x-goog-api-key`。HTTP 重定向不会被跟随。请求超时可以在页面调整。

兼容意味着服务接受原生 `model`、`state`、`questions` 请求，并返回与问题同名的 `answers`：Noul 返回 `type: noul` 和 0–1 数值 `noul`；Choice 返回 `type: choice`、合法的 `choice` 和 0–1 数值 `confidence`。返回 `usage.input_tokens` 与 `usage.output_tokens` 时纳入统计；没有 usage 的调用无法精确计量。仅仅支持 OpenAI Chat Completions 的服务不满足这个协议。

接口依据：[TypeSafe System One](https://docs.typesafe.ai/api/system-one)、[Noul](https://docs.typesafe.ai/primitives/noul)、[Choice](https://docs.typesafe.ai/primitives/choice)。

## Google Gemini Decisions（0.5.0 起）

在 Dashboard 中选择 **Google Gemini Decisions**，模型默认填入 `gemini-decision-1`，在凭据区保存 Google AI Studio 创建的 `GEMINI_API_KEY`，然后保存配置。Google 使用固定的官方地址，不填写第三方 API 地址，也不会使用 TypeSafe 或第三方的密钥。此处接入的是 Gemini Developer API 的 Decisions 接口，不是 Vertex AI 或普通聊天生成接口。

两种判断模式都可使用：bool 对应原生 Noul（Google 使用 `criteria.true_description` / `false_description`，TypeSafe 使用 `true` / `false`），choice 保留 ignore/watch/act。请求以 `model`、`input`、`questions` 发送；`input` 是包含规则、时区、人员短代号、消息、历史与引用原文的 JSON 字符串。响应按问题 ID 校验，缺失、被过滤或无效的答案会失败，不会默认派单。Google 的 `usage.total_input_tokens` / `total_output_tokens` 转换为插件统计字段；缺少用量时保持未知，不冒充零消耗。

先保存配置和凭据，再点击 **测试已保存的连接**。每次点击对已保存 provider 发出一个真实请求，可能计费；使用固定的合成预约、测试身份和测试总体规则，不读取真实微信消息、不创建任务、不更改处理开关。问题和选项使用默认时间安排规则以隔离个人配置；请求模式采用已保存配置；返回判断及本次用量。连接测试不计入收件箱调用统计。成功表示协议和认证可用，不能替代中文群聊判断质量评估；切换模型后需重新校验阈值。

只查看页面、保存配置和预览请求不调用模型。连接测试无须启动 Gateway，正常消息处理在保存后重启 Gateway 生效。可以保持 `receive_only: true` 完成配置和连接测试，之后再明确解除仅接收并恢复处理。

```yaml
platforms:
  wechat_router:
    enabled: true
    extra:
      provider: google
      model: gemini-decision-1
      receive_only: true
      mode: observe
      assistant_profile: assistant
      decision_mode: bool
      bool_threshold: 0.6
```

密钥通过 Hermes 凭据页面保存，不写入上述 YAML。现有自定义提示词、合并规则和主助理交接行为仍然适用。

接口依据：[Google 官方判别模型教程](https://codelabs.developers.google.com/codelabs/discriminative-models)、[Google Gen AI SDK Noul 定义](https://github.com/googleapis/python-genai/blob/main/google/genai/_gaos/types/decisions/noulquestion.py)、[Noul criteria 定义](https://github.com/googleapis/python-genai/blob/main/google/genai/_gaos/types/decisions/noulcriteria.py)、[响应结构](https://github.com/googleapis/python-genai/blob/main/google/genai/_gaos/types/decisions/createdecisionresponse.py)。实现直接使用已有 aiohttp，无需额外 SDK 依赖。

## Hermes 代理与连接故障（0.5.2 起）

收件箱推理与网页连接测试均调用 Hermes 的 `resolve_proxy_url` / `proxy_kwargs_for_aiohttp`，沿用 `gateway.trust_env`、进程环境中的 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` 及 `NO_PROXY`；由 Hermes 处理大小写、优先级和系统代理。无需在插件里保存另一份代理地址。禁用 `gateway.trust_env` 或目标匹配 `NO_PROXY` 时遵循 Hermes 的直连决定。代理环境变更后需重启 Gateway 和独立 Dashboard，使两者加载一致的环境。

选中 SOCKS 但 Hermes 没有相应连接器时，明确报错，不静默改为直连。模型 API 地址和认证仍指向原 provider；不跟随重定向、不改 TLS 校验、不跨 provider 回退，也不自动读取 `.netrc`。

页面区分网络超时、连接错误、HTTP 400/401/403/404/429 等稳定错误码，给出相应排查方向，不回显密钥或服务商原始错误正文。`jev_timeout` 表示网络或响应超时，不代表密钥错误；代理修通后仍需检查真实接口返回，不能把连通等同于推理成功。

Google 的 bool criteria 字段与 TypeSafe 不同；0.5.0–0.5.1 错用了 `true` / `false`，会被 Google 以 HTTP 400 拒绝。0.5.2 保留自定义说明并正确转换为 `true_description` / `false_description`。请求预览与实际发送使用同一构造函数。

## 自定义提示词与分组边界

总体规则作为 `routing_policy` 发送，与 `decision_question` 和是/否或 Choice 判定说明一起决定模型问题。主助理自己的 SOUL 和聊天模型提示词独立配置。总体规则最多 12,000 字符，问题与各选项说明最多 4,000 字符。

默认只判断当前 messages 有无时间安排线索，不要求本人相关或重要，不要求已确认或有完整日期。`history`、`quotes` 只辅助理解当前消息，不能仅因历史有安排触发。相邻消息不一定同一话题；一次邀请可以跨批确认，一批也可以包含多个话题。

| 历史 | 当前消息 | 默认预期 |
|---|---|---|
| 空 | 今晚要不要一起吃饭？夹杂闲聊 | 交主助理 |
| 空 | 改天一起喝咖啡 | 交主助理，时间待定也可 |
| 已有会议邀请 | 改到四点／可以／不去了 | 明确承接时交主助理 |
| 已有截止通知 | 这个表情包真搞笑 | 不因历史触发 |
| 空 | 明天降温 | 忽略，天气不是安排 |
| 空 | 其他两人约定周五见面 | 可通过初筛，由主助理判断本人相关性 |
| 无可见原文 | 图片或语音占位 | 保存为内容不可见，不调用模型 |

初筛不读取原始任务 ID、完成状态或处理结果。重复投递按 source/event_id 幂等处理，语义去重、是否过期、日期推算以及与本人的关系由后续主助理核对。

## 判断问题与模式

每个可判断批次只问一个 `needs_followup` 问题。bool 使用 Noul 分数，达到 `bool_threshold` 时交主助理；这个分数不是经本地标注校准的准确率。Choice 使用固定 ignore/watch/act，选项文字可配置。日期未定、本人关系不明不是默认 watch 的依据。

```json
{
  "needs_followup": {
    "type": "noul",
    "instructions": "当前 messages 是否至少包含一条时间安排或其更新线索？",
    "criteria": {
      "true": "具体预约、邀请、计划、截止及其确认、补充、改期、取消。",
      "false": "当前没有时间安排或更新线索，仅有历史提及安排也为否。"
    }
  }
}
```

上例为 TypeSafe；Google 对应 `true_description` / `false_description`。网页可以预览草稿请求，不读取真实消息、不发起推理。

## 生效与重试

升级不覆盖已有自定义规则。旧用户可点“使用默认时间安排初筛”，同时恢复总体规则、问题和选项；阈值和模式不改变。保存后重启 Gateway，新批次才采用新规则。首次尝试前会保存完整批次快照；失败重试保留原格式、规则与候选数据。成功判断后的看板重试复用已保存决定，不额外推理。切换 provider 或端点后，旧的未判断批次会阻止跨 provider 重放。

未知或缺失答案不会自动派单，失败会按现有退避策略重试；达到上限后需手动处理。观察模式调用模型但不建卡或追加回执；仅接收模式无需模型 key。插件不会自动启用接收或恢复暂停状态。

## YAML 示例

```yaml
platforms:
  wechat_router:
    enabled: false
    extra:
      provider: google
      model: gemini-decision-1
      mode: observe
      owner_name: 你的姓名
      owner_aliases: [你的群昵称]
      owner_ids: [YOUR_WECHAT_ID]
      assistant_profile: assistant
      decision_mode: bool
      bool_threshold: 0.6
      sender_rules: []
```

省略提示词字段即使用新默认值。第三方服务改为 `provider: custom` 并配置 `provider_base_url` 和实际模型 ID；凭据单独保存。

## 0.7.0 的输入与语义检查

新批次只初筛时间安排及其更新，不要求本人相关或重要，也不要求日期完整。请求状态包含 `routing_policy`、`timezone`、`conversation`、`people`、`history`、`messages` 和可选 `quotes`；消息的 `ref` 为 m/h/q 短代号，`sender` 指向人员表，`at` 为带时区的原始发送时间。缺失引用为 `reply_to: unavailable`；无正文媒体保留内容不可见标记。原始传输编号、重要性和已有任务状态不发给初筛。

纯占位、已知助理回执仍保存在收件箱，但不作为新安排调用模型。旧批次沿用旧格式，不能用新版请求解释旧日志。自定义问题应使用实际 `messages` / `history` / `quotes` 边界，避免重新要求模型核对任务是否已完成。

`evals/temporal.json` 提供 16 个合成标注案例，包括主动邀请、日期未定、他人安排、跨批确认、改期、取消、历史无关聊天及天气。自动化 HTTP fixture 测试验证协议和分组，不能证明模型准确率。可在配置好相应凭据的环境中显式运行：

```sh
python scripts/evaluate_temporal.py --provider google --model gemini-decision-1 --output /private/path/temporal-eval.jsonl
```

这会产生实际模型消费，但不读取微信收件箱、不创建看板任务；遇到首次服务错误即停止，不重试。Hermes 内运行时可加 `--hermes-proxy` 复用框架代理。JSONL 输出记录合成案例 ID、期望、分数和用量，旁边的 `.model-audit.sqlite3` 保留每次完整请求与原始响应；文件权限 0600 且拒绝覆盖。语义测试通过也不等于真实聊天的准确率。
