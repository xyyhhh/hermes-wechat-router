/* Uses the Dashboard's React and authenticated API client. No build step. */
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  const React = SDK.React;
  const h = React.createElement;
  const base = "/api/plugins/wechat-router";

  function Page() {
    const [config, setConfig] = React.useState(null);
    const [stats, setStats] = React.useState(null);
    const [audit, setAudit] = React.useState(null);
    const [error, setError] = React.useState("");
    const [notice, setNotice] = React.useState("");
    const [busy, setBusy] = React.useState(false);
    const [keys, setKeys] = React.useState({ WECHAT_ROUTER_SECRET: "", TYPESAFE_API_KEY: "", WECHAT_ROUTER_API_KEY: "", GEMINI_API_KEY: "" });
    const [statusError, setStatusError] = React.useState("");
    const [preview, setPreview] = React.useState(null);
    const [connectionTest, setConnectionTest] = React.useState(null);
    const profile = config && config.profile;
    const scoped = (path) => path + (profile ? "?profile=" + encodeURIComponent(profile) : "");
    const request = (path, method, body) => SDK.fetchJSON(scoped(base + path), {
      method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });

    React.useEffect(() => {
      let disposed = false;
      SDK.fetchJSON(base + "/config").then(value => { if (!disposed) setConfig(value); })
        .catch(() => { if (!disposed) setError("无法读取插件配置。请确认已启用插件，并重启 Dashboard 后刷新。"); });
      return () => { disposed = true; };
    }, []);

    React.useEffect(() => {
      if (!profile) return;
      let disposed = false;
      const refresh = () => SDK.fetchJSON(scoped(base + "/status")).then(value => {
        if (!disposed) { setStats(value); setStatusError(""); }
      }).catch(() => { if (!disposed) setStatusError("状态刷新失败；下方数据可能已经过期。"); });
      const refreshAudit = () => SDK.fetchJSON(scoped(base + "/audit")).then(value => { if (!disposed) setAudit(value); }).catch(() => { if (!disposed) setAudit(null); });
      refresh(); refreshAudit();
      const timer = setInterval(() => { refresh(); refreshAudit(); }, 10000);
      return () => { disposed = true; clearInterval(timer); };
    }, [profile]);

    async function act(action) {
      setBusy(true); setError(""); setNotice("");
      try { await action(); }
      catch (error) {
        const match = String(error && error.message || "").match(/\b(jev_(?:timeout|transport_error|proxy_unavailable|http_\d{3}|invalid_[a-z_]+|response_too_large)|audit_unavailable)\b/);
        const code = match && match[1];
        const descriptions = {
          audit_unavailable: "调用日志写入失败，请检查服务器磁盘和文件权限；该次请求可能已发出，请先核对日志。",
          jev_proxy_unavailable: "Hermes 已选择代理，但缺少相应连接器。请检查 Hermes 的代理依赖。",
          jev_timeout: "模型请求超时。请检查Hermes 代理和请求超时设置。",
          jev_transport_error: "无法连接模型服务。请检查网络、DNS、TLS 和Hermes 代理。",
          jev_http_400: "模型服务拒绝请求格式或参数，请核对模型 ID 或更新插件。",
          jev_http_401: "模型服务认证失败，请检查已保存的密钥。",
          jev_http_403: "模型服务拒绝访问，请检查密钥权限、项目或服务地区。",
          jev_http_404: "模型或接口不存在，请核对模型 ID 和服务地址。",
          jev_http_429: "模型服务限流或额度不足，请检查配额后重试。"
        };
        setError(code ? (descriptions[code] || "模型返回错误，请检查服务状态或响应格式。") + "（" + code + "）" : "操作失败。请检查已保存的密钥、模型和配置。连接测试只使用已保存设置。");
      }
      finally { setBusy(false); }
    }
    function change(name, value) {
      setPreview(null); setConnectionTest(null);
      setConfig(old => ({ ...old, settings: { ...old.settings, [name]: value } }));
    }
    function textField(name, label, hint, value, onChange) {
      return h("label", { className: "wr-field", key: name }, h("span", null, label),
        h("textarea", { name, rows: 3, maxLength: 4000, disabled: busy,
          value: value === undefined ? config.settings[name] : value,
          onChange: onChange || (e => change(name, e.target.value)) }), h("small", null, hint));
    }
    function field(name, label, hint, options) {
      const opts = options || {};
      const value = config.settings[name];
      return h("label", { className: "wr-field", key: name }, h("span", null, label),
        h("input", { name, value: value == null ? "" : value, disabled: busy,
          type: opts.type || "text", min: opts.min, max: opts.max, step: opts.step,
          onChange: event => change(name, opts.type === "number" ? Number(event.target.value) : event.target.value) }),
        hint && h("small", null, hint));
    }
    function list(name, label, hint) {
      return h("label", { className: "wr-field", key: name }, h("span", null, label),
        h("textarea", { name, rows: 3, disabled: busy, value: (config.settings[name] || []).join("\n"),
          onChange: event => change(name, event.target.value.split("\n")) }), h("small", null, hint));
    }
    function normalized() {
      const settings = { ...config.settings };
      for (const name of ["owner_ids", "owner_aliases", "interests", "sources"]) {
        settings[name] = (settings[name] || []).map(x => x.trim()).filter(Boolean);
      }
      settings.sender_rules = (settings.sender_rules || []).map(rule => ({ ...rule,
        source: rule.source.trim(), sender_id: rule.sender_id.trim(),
        conversation_id: (rule.conversation_id || "").trim(), name: (rule.name || "").trim(),
        aliases: (rule.aliases || []).map(x => x.trim()).filter(Boolean)
      }));
      return settings;
    }
    function changeRule(index, key, value) {
      change("sender_rules", (config.settings.sender_rules || []).map((r, i) => i === index ? { ...r, [key]: value } : r));
    }
    function ruleField(rule, i, key, label) {
      return h("label", { className: "wr-field", key }, h("span", null, label),
        h("input", { name: "sender-" + i + "-" + key, disabled: busy,
          value: key === "aliases" ? (rule.aliases || []).join(", ") : rule[key] || "",
          onChange: e => changeRule(i, key, key === "aliases" ? e.target.value.split(/[,，]/) : e.target.value) }));
    }
    function button(label, onClick, disabled) {
      return h("button", { type: "button", disabled: busy || disabled, onClick }, label);
    }
    if (!config) return h("div", { className: "wr-page" }, h("h1", null, "微信消息分流"),
      h("p", { role: error ? "alert" : "status" }, error || "正在读取配置…"));
    const s = config.settings;
    const usage = stats && stats.usage || {};
    const modelKey = s.provider === "google" ? "GEMINI_API_KEY" : s.provider === "custom" ? "WECHAT_ROUTER_API_KEY" : "TYPESAFE_API_KEY";
    const metric = (label, value) => h("div", { className: "wr-metric", key: label },
      h("strong", null, Number(value || 0).toLocaleString()), h("span", null, label));

    return h("main", { className: "wr-page" },
      h("header", { className: "wr-header" }, h("div", null, h("h1", null, "微信消息分流"),
        h("p", null, "微信消息 → 按会话合并 → 判别模型时间安排初筛 → 主助理处理和委派")),
        h("span", { className: "wr-pill" }, "Profile: " + profile + " · v" + config.version)),
      error && h("p", { className: "wr-alert", role: "alert" }, error),
      notice && h("p", { className: "wr-notice", role: "status" }, notice),
      config.validation_error && h("p", { className: "wr-alert" }, config.validation_error),
      h("section", { className: "wr-card" }, h("h2", null, "处理状态"),
        h("p", null, !stats ? "正在读取状态…" : !stats.initialized ? "收件箱尚未初始化：配置后启动 Gateway。" :
          !config.enabled ? "接收平台已禁用；保存后需重启 Gateway。" : stats.paused ? "已暂停处理新批次；接收接口仍可保存消息。" : "处理开关已开启；此状态不代表 Gateway 当前在线。"),
        statusError && h("p", { role: "alert" }, statusError),
        stats && stats.outcomes && h("p", { className: "wr-muted" }, "仅保存未推理：内容不可见 " + (stats.outcomes.content_unavailable || 0) + " 批；已知助理回执 " + (stats.outcomes.assistant_receipt || 0) + " 批。内容不可见不代表没有安排。"),
        h("div", { className: "wr-metrics" }, metric("已接收消息", stats && stats.messages),
          metric("等待合并", stats && stats.buffered), metric("模型调用", usage.calls),
          metric("输入 token", usage.input_tokens), metric("输出 token", usage.output_tokens)),
        h("p", { className: "wr-muted" }, "仅统计收件箱处理的模型消耗，不包含连接测试和下游 agent。缺少 usage 的调用：" + (usage.calls_without_usage || 0)),
        stats && stats.initialized && h("div", { className: "wr-actions" },
          button(stats.paused ? "恢复处理" : "暂停处理", () => act(async () => {
            await request("/pause", "POST", { paused: !stats.paused });
            setStats(await SDK.fetchJSON(scoped(base + "/status")));
            setNotice("处理开关已更新。正在进行的批次可能继续完成。");
          })), h("span", { className: "wr-muted" }, "状态每 10 秒刷新")),
        stats && stats.failures && stats.failures.length > 0 && h("div", { className: "wr-failures" },
          h("h3", null, "失败批次"), ...stats.failures.map(item => h("div", { className: "wr-failure", key: item.id },
            h("code", null, item.id), h("span", null, item.error || "处理失败"),
            button("重试", () => act(async () => {
              await request("/retry", "POST", { batch_id: item.id });
              setStats(await SDK.fetchJSON(scoped(base + "/status")));
              setNotice("已重新排队；可能再次调用判别模型。");
            })))))),
      h("section", { className: "wr-card" }, h("h2", null, "模型调用日志"),
        h("p", null, "默认保留每次模型请求与原始响应，包括错误、超时和重试；只保存在当前 profile 的本地私有日志库，不记录认证头或密钥，不自动清理。"),
        h("p", { className: "wr-muted" }, "位置：plugin-data/wechat-router/model-audit.sqlite3。原始响应最多保存 2 MiB，超过会标记截断；进程中断可能留下未完成记录。"),
        h("p", null, audit ? "已记录 " + (audit.attempts || 0) + " 次，未完成 " + (audit.pending || 0) + " 次，错误 " + (audit.errors || 0) + " 次。" : "正在读取调用日志状态，或日志暂不可用。")),
      h("form", { onSubmit: event => { event.preventDefault(); act(async () => {
        await request("/config", "PUT", { enabled: config.enabled, settings: normalized() });
        setNotice("配置已保存。请重启当前 profile 的 Gateway 后生效。");
      }); } },
        h("section", { className: "wr-card" }, h("h2", null, "基本配置"),
          h("label", { className: "wr-checkbox" }, h("input", { type: "checkbox", name: "enabled", checked: config.enabled,
            disabled: busy, onChange: e => setConfig(old => ({ ...old, enabled: e.target.checked })) }), "启用微信消息接收平台"),
          h("div", { className: "wr-grid" },
            h("label", { className: "wr-field" }, h("span", null, "处理模式"),
              h("select", { name: "mode", value: s.mode, disabled: busy, onChange: e => change("mode", e.target.value) },
                h("option", { value: "observe" }, "观察：调用判别模型，记录判断"), h("option", { value: "active" }, "执行：调用判别模型，并派发任务")),
              h("small", null, "两种模式均产生 模型费用；观察模式不创建 Kanban 任务。")),
            h("label", { className: "wr-checkbox" }, h("input", { type: "checkbox", name: "receive_only", checked: !!s.receive_only,
              disabled: busy, onChange: e => change("receive_only", e.target.checked) }), "仅接收并暂存，不调用判别模型、不派单（无需模型 key；保存后重启 Gateway）"),
            field("owner_name", "本人姓名或常用称呼", "避免只填写‘我’；用于后续主助理核对身份。"), field("timezone", "时区"),
            list("owner_ids", "本人的微信 ID", "每行一个；使用消息源实际发送的 sender_id。执行模式必填。"),
            list("owner_aliases", "本人群昵称和别名", "每行一个。发信人按稳定 ID 识别，别名只帮助理解称呼。"),
            list("sources", "消息源筛选", "每行一个 source；留空接收全部来源。私聊和群聊均处理。"))),
        h("section", { className: "wr-card" }, h("h2", null, "发信人身份与重要性"),
          h("p", { className: "wr-muted" }, "按消息源和稳定 sender_id 匹配；会话 ID 留空时对该来源全部会话生效，同一会话的专用规则覆盖通用规则。重要性只影响命中后的看板优先级（高 10、普通 0、低 -10），不改变模型问题、阈值或分批窗口。"),
          ...(s.sender_rules || []).map((rule, i) => h("div", { className: "wr-rule", key: i },
            h("div", { className: "wr-grid" },
              ...[["source", "消息源 source"], ["sender_id", "发信人 sender_id"], ["conversation_id", "限定会话 ID（可选）"], ["name", "显示名称（可选）"], ["aliases", "昵称或别名（逗号分隔）"]].map(([key, label]) => ruleField(rule, i, key, label)),
              h("label", { className: "wr-field" }, h("span", null, "重要性"),
                h("select", { name: "sender-" + i + "-importance", value: rule.importance || "normal", disabled: busy, onChange: e => changeRule(i, "importance", e.target.value) },
                  ...[["high", "高"], ["normal", "普通"], ["low", "低"]].map(([value, label]) => h("option", { key: value, value }, label)))),
              h("label", { className: "wr-field" }, h("span", null, "账号类型"),
                h("select", { name: "sender-" + i + "-role", value: rule.role || "person", disabled: busy, onChange: e => changeRule(i, "role", e.target.value) },
                  h("option", { value: "person" }, "普通发信人"), h("option", { value: "assistant" }, "已知助理账号 · 只记录回执")))),
            button("删除发信人规则 " + (i + 1), () => change("sender_rules", s.sender_rules.filter((_, n) => n !== i))))),
          h("p", { className: "wr-muted" }, "助理账号消息不再触发新任务；只有 reply_to 能匹配本插件同会话的原任务时，执行模式才将回执追加原卡。无法关联时保留日志；本人发给助理的消息照常初筛。请核实账号身份后再设置。"),
          button("添加发信人规则", () => change("sender_rules", [...(s.sender_rules || []), { source: "", sender_id: "", conversation_id: "", name: "", aliases: [], importance: "normal", role: "person" }]))),
        h("section", { className: "wr-card" }, h("h2", null, "判别模型 Provider"),
          h("div", { className: "wr-grid" },
            h("label", { className: "wr-field" }, h("span", null, "服务提供商"),
              h("select", { name: "provider", value: s.provider, disabled: busy, onChange: e => {
                const next = e.target.value; setPreview(null); setConnectionTest(null);
                setConfig(old => ({ ...old, settings: { ...old.settings, provider: next,
                  model: next === "google" ? "gemini-decision-1" : old.settings.provider === "google" && old.settings.model === "gemini-decision-1" ? "jev-latest" : old.settings.model } }));
              } },
                h("option", { value: "google" }, "Google Gemini Decisions"),
                h("option", { value: "typesafe" }, "TypeSafe 官方"),
                h("option", { value: "custom" }, "第三方 · System One 兼容"))),
            field("model", "模型 ID", s.provider === "google" ? "Google 判别模型，默认 gemini-decision-1。" : "填写服务商实际提供的模型名称，例如 jev-latest。"),
            field("request_timeout", "请求超时（秒）", "按服务商的响应速度调整。", { type: "number", min: 0.1, step: 0.1 }),
            s.provider === "custom" && field("provider_base_url", "API 地址", "填写含版本路径的 Base URL（如 https://provider.example/v1），或完整 /systemone 地址。")),
          h("p", { className: "wr-muted" }, s.provider === "google"
            ? "使用 Google 官方 /v1beta/decisions 接口。请在下方保存 GEMINI_API_KEY，通过 x-goog-api-key 认证。" : s.provider === "custom"
            ? "请求使用 TypeSafe System One 格式和 Bearer 认证。请在下方保存 WECHAT_ROUTER_API_KEY；不会使用官方 key 或自动回退到官方服务。"
            : "使用 https://api.typesafe.ai/v1/systemone，请在下方保存 TYPESAFE_API_KEY。"),
          h("p", { className: "wr-muted" }, "模型请求沿用 Hermes 的代理设置（含 gateway.trust_env 与 NO_PROXY），无需在本插件另配代理。"),
          h("p", { className: "wr-muted" }, "修改后保存配置并重启 Gateway。保存页面不会向服务商发出请求。"),
          h("div", { className: "wr-actions" }, button("测试已保存的连接", () => act(async () => {
            setConnectionTest(null);
            setConnectionTest(await request("/test-connection", "POST", {}));
          })), h("span", { className: "wr-muted" }, "先保存配置和密钥；点击后发送一条合成预约样例，调用已保存的模型并可能计费。无需启用消息处理，不读取微信消息，不创建任务。")),
          connectionTest && h("p", { role: "status" }, "连接测试成功：" + connectionTest.model + "；合成样例判断：" + JSON.stringify(connectionTest.answer) + "；本次 token：" + JSON.stringify(connectionTest.usage))),
        h("section", { className: "wr-card" }, h("h2", null, "判别模型提示词"),
          h("label", { className: "wr-field" }, h("span", null, "总体判断规则"),
            h("textarea", { name: "jev_instructions", rows: 7, maxLength: 12000, disabled: busy,
              value: s.jev_instructions, onChange: e => change("jev_instructions", e.target.value) }),
            h("small", null, "作为 state.routing_policy 发送给判别模型，只识别预约、邀请、计划、截止及其更新；不判断本人相关性或重要性。主助理负责核对、去重和后续处理。最多 12,000 字符；较长提示词会增加每批输入 token。")),
          h("div", { className: "wr-actions" },
            button("恢复默认提示词", () => change("jev_instructions", config.defaults.jev_instructions), !config.defaults),
            button("使用默认时间安排初筛", () => {
              setPreview(null); setConnectionTest(null);
              const defaults = Object.fromEntries(["jev_instructions", "decision_question", "positive_criteria", "negative_criteria", "choice_criteria"]
                .filter(k => config.defaults[k] !== undefined).map(k => [k, config.defaults[k]]));
              setConfig(old => ({ ...old, settings: { ...old.settings, ...defaults } }));
              setNotice("已填入默认时间安排规则；阈值和模式保持原值，请保存后重启 Gateway。");
            }, !config.defaults),
            h("span", { className: "wr-muted" }, "恢复后需保存；已有批次保留原提示词。"))),
        h("section", { className: "wr-card" }, h("h2", null, "模型请求配置"),
          h("p", { className: "wr-muted" }, "每批只问有无时间安排线索。支持时间未定的邀请和跨批确认；命中后由主助理核对是否与本人有关、是否值得行动。"),
          h("label", { className: "wr-field" }, h("span", null, "判断模式"),
            h("select", { name: "decision_mode", value: s.decision_mode, disabled: busy,
              onChange: e => change("decision_mode", e.target.value) },
              h("option", { value: "bool" }, "布尔 · 是否存在时间安排（Noul）"),
              h("option", { value: "choice" }, "选择 · 忽略 / 观察 / 交主助理（Choice）"))),
          textField("decision_question", "判断问题", "此文本是该批次唯一问题的 instructions。"),
          s.decision_mode === "bool" ? h("div", { className: "wr-grid" },
            field("bool_threshold", "交接分数阈值", "Noul 分数达到阈值时交主助理；不是经过本地校准的准确率。", { type: "number", min: 0.01, max: 0.99, step: 0.01 }),
            textField("positive_criteria", "是：存在安排线索", "TypeSafe 使用 criteria.true；Google 使用 criteria.true_description。"),
            textField("negative_criteria", "否：没有安排线索", "TypeSafe 使用 criteria.false；Google 使用 criteria.false_description。")) :
            h("div", { className: "wr-grid" },
              ...[["ignore", "忽略"], ["watch", "继续观察"], ["act", "交主助理"]].map(([key, label]) =>
                textField("criterion-" + key, label + "的判定说明", "选项键固定为 " + key + "，可编辑说明。",
                  s.choice_criteria[key], e => change("choice_criteria", { ...s.choice_criteria, [key]: e.target.value }))),
              field("min_confidence", "选择模式复核阈值", "act 低于此置信度时，仍交同一主助理并标记为待核对。", { type: "number", min: 0, max: 1, step: 0.05 })),
          h("div", { className: "wr-actions" }, button("预览请求 JSON", () => act(async () => {
            setPreview(await request("/request-preview", "POST", { settings: normalized() }));
          })), h("span", { className: "wr-muted" }, "使用表单草稿和消息占位符；不读取真实消息、不调用模型。")),
          preview && h("pre", { className: "wr-preview", "aria-label": "模型请求 JSON" }, JSON.stringify(preview.request, null, 2))),
        h("section", { className: "wr-card" }, h("h2", null, "消息合并与判断"),
          h("div", { className: "wr-grid" },
            field("quiet_seconds", "静默合并窗口（秒）", "同一会话停止来信多久后开始判断。", { type: "number", min: 0.1, step: 0.1 }),
            field("max_wait_seconds", "最长等待（秒）", "持续活跃的群聊也会按此窗口切批。", { type: "number", min: 0.1, step: 0.1 }),
            field("max_batch_messages", "每批消息上限", "范围 1–20；达到上限可提前判断。", { type: "number", min: 1, max: 20, step: 1 }),
            field("max_context_chars", "历史上下文字符上限", "限制每次判断携带的历史内容。", { type: "number", min: 1, step: 1 }))),
        h("section", { className: "wr-card" }, h("h2", null, "统一交主助理"),
          h("p", { className: "wr-muted" }, "一个命中批次生成一条 Kanban 交接任务，附完整原文、身份信息、引用和上下文。多次交接可能属于同一事项，由主助理关联已有任务并决定后续 agent。"),
          h("div", { className: "wr-grid" }, field("board", "看板名称"), field("assistant_profile", "主助理 profile", "填写已创建的主助理，默认 assistant。"))),
        h("section", { className: "wr-card" }, h("h2", null, "消息接入"),
          h("div", { className: "wr-grid" }, field("host", "监听地址"), field("port", "监听端口", null,
            { type: "number", min: 1, max: 65535, step: 1 })),
          h("p", null, "外部监听器提交至 http://<Hermes 主机>:" + s.port + "/wechat-router/v1/events，按接口文档进行 HMAC 签名。"),
          h("a", { href: "https://github.com/xyyhhh/hermes-wechat-router/blob/main/docs/provider-api.md", target: "_blank", rel: "noreferrer" }, "消息提供程序接口文档")),
        h("div", { className: "wr-save" }, h("button", { type: "submit", disabled: busy }, busy ? "处理中…" : "保存配置"),
          h("span", null, "保存后重启 Gateway；不会自动重启。"))),
      h("section", { className: "wr-card" }, h("h2", null, "凭据"),
        h("p", { className: "wr-muted" }, "通过 Hermes 的凭据接口单独保存；已有值不回显。保存后重启 Gateway。"),
        ...["WECHAT_ROUTER_SECRET", modelKey].map(name => h("div", { className: "wr-secret", key: name },
          h("label", { className: "wr-field" }, h("span", null, name + (config.secrets[name] ? " · 已配置" : " · 未配置")),
            h("input", { name, type: "password", autoComplete: "new-password", value: keys[name], disabled: busy,
              onChange: e => setKeys(old => ({ ...old, [name]: e.target.value })) }),
            h("small", null, name === "WECHAT_ROUTER_SECRET" ? "与外部监听器共享，至少 32 字节。" : name === "GEMINI_API_KEY" ? "用于 Google Gemini Decisions；在 Google AI Studio 创建 API key。" : name === "TYPESAFE_API_KEY" ? "仅用于 TypeSafe 官方服务。" : "仅用于当前配置的第三方 System One 服务。")),
          button("保存凭据", () => act(async () => {
            try {
              await SDK.fetchJSON(scoped("/api/env"), { method: "PUT", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ key: name, value: keys[name] }) });
              setConfig(old => ({ ...old, secrets: { ...old.secrets, [name]: true } }));
              setNotice(name + " 已保存。请重启 Gateway 后使用新凭据。");
            } finally { setKeys(old => ({ ...old, [name]: "" })); }
          }), !keys[name].trim() || (name === "WECHAT_ROUTER_SECRET" && new TextEncoder().encode(keys[name]).length < 32))))));
  }
  window.__HERMES_PLUGINS__.register("wechat-router", Page);
})();
