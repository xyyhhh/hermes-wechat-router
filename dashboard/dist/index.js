/* Uses the Dashboard's React and authenticated API client. No build step. */
(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  const React = SDK.React;
  const h = React.createElement;
  const categories = { schedule: "时间预约", deadline: "截止日期", todo: "待办事项", important: "重要事件", other: "其他" };
  const base = "/api/plugins/wechat-router";

  function Page() {
    const [config, setConfig] = React.useState(null);
    const [stats, setStats] = React.useState(null);
    const [error, setError] = React.useState("");
    const [notice, setNotice] = React.useState("");
    const [busy, setBusy] = React.useState(false);
    const [keys, setKeys] = React.useState({ WECHAT_ROUTER_SECRET: "", TYPESAFE_API_KEY: "", WECHAT_ROUTER_API_KEY: "" });
    const [statusError, setStatusError] = React.useState("");
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
      refresh();
      const timer = setInterval(refresh, 10000);
      return () => { disposed = true; clearInterval(timer); };
    }, [profile]);

    async function act(action) {
      setBusy(true); setError(""); setNotice("");
      try { await action(); }
      catch (_) { setError("操作失败。请检查 provider 地址、提示词、本人 ID、时区和 profile 名称，然后重试。"); }
      finally { setBusy(false); }
    }
    function change(name, value) {
      setConfig(old => ({ ...old, settings: { ...old.settings, [name]: value } }));
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
        h("textarea", { name, rows: 3, disabled: busy, value: config.settings[name].join("\n"),
          onChange: event => change(name, event.target.value.split("\n")) }), h("small", null, hint));
    }
    function normalized() {
      const settings = { ...config.settings };
      for (const name of ["owner_ids", "interests", "sources"]) {
        settings[name] = settings[name].map(x => x.trim()).filter(Boolean);
      }
      return settings;
    }
    function button(label, onClick, disabled) {
      return h("button", { type: "button", disabled: busy || disabled, onClick }, label);
    }
    if (!config) return h("div", { className: "wr-page" }, h("h1", null, "微信消息分流"),
      h("p", { role: error ? "alert" : "status" }, error || "正在读取配置…"));
    const s = config.settings;
    const usage = stats && stats.usage || {};
    const metric = (label, value) => h("div", { className: "wr-metric", key: label },
      h("strong", null, Number(value || 0).toLocaleString()), h("span", null, label));

    return h("main", { className: "wr-page" },
      h("header", { className: "wr-header" }, h("div", null, h("h1", null, "微信消息分流"),
        h("p", null, "微信消息 → 按会话合并 → Jev 判断 → Kanban 派单")),
        h("span", { className: "wr-pill" }, "Profile: " + profile + " · v" + config.version)),
      error && h("p", { className: "wr-alert", role: "alert" }, error),
      notice && h("p", { className: "wr-notice", role: "status" }, notice),
      config.validation_error && h("p", { className: "wr-alert" }, config.validation_error),
      h("section", { className: "wr-card" }, h("h2", null, "处理状态"),
        h("p", null, !stats ? "正在读取状态…" : !stats.initialized ? "收件箱尚未初始化：配置后启动 Gateway。" :
          stats.paused ? "已暂停处理新批次；接收接口仍可保存消息。" : "处理开关已开启；此状态不代表 Gateway 当前在线。"),
        statusError && h("p", { role: "alert" }, statusError),
        h("div", { className: "wr-metrics" }, metric("已接收消息", stats && stats.messages),
          metric("等待合并", stats && stats.buffered), metric("Jev 调用", usage.calls),
          metric("输入 token", usage.input_tokens), metric("输出 token", usage.output_tokens)),
        h("p", { className: "wr-muted" }, "仅统计插件内的 Jev 消耗，不包含下游 agent。缺少 usage 的调用：" + (usage.calls_without_usage || 0)),
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
              setNotice("已重新排队；可能再次调用 Jev。");
            })))))),
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
                h("option", { value: "observe" }, "观察：调用 Jev，记录判断"), h("option", { value: "active" }, "执行：调用 Jev，并派发任务")),
              h("small", null, "两种模式均产生 Jev 费用；观察模式不创建 Kanban 任务。")),
            field("owner_name", "本人称呼"), field("timezone", "时区"),
            list("owner_ids", "本人的微信 ID", "每行一个；使用消息源实际发送的 sender_id。执行模式必填。"),
            list("interests", "关注事项", "每行一项，帮助判断群聊中与自己相关的信息。"),
            list("sources", "消息源筛选", "每行一个 source；留空接收全部来源。私聊和群聊均处理。"))),
        h("section", { className: "wr-card" }, h("h2", null, "Jev Provider"),
          h("div", { className: "wr-grid" },
            h("label", { className: "wr-field" }, h("span", null, "服务提供商"),
              h("select", { name: "provider", value: s.provider, disabled: busy, onChange: e => change("provider", e.target.value) },
                h("option", { value: "typesafe" }, "TypeSafe 官方"),
                h("option", { value: "custom" }, "第三方 · System One 兼容"))),
            field("model", "模型 ID", "填写服务商实际提供的模型名称，例如 jev-latest。"),
            field("request_timeout", "请求超时（秒）", "按服务商的响应速度调整。", { type: "number", min: 0.1, step: 0.1 }),
            s.provider === "custom" && field("provider_base_url", "API 地址", "填写含版本路径的 Base URL（如 https://provider.example/v1），或完整 /systemone 地址。")),
          h("p", { className: "wr-muted" }, s.provider === "custom"
            ? "请求使用 TypeSafe System One 格式和 Bearer 认证。请在下方保存 WECHAT_ROUTER_API_KEY；不会使用官方 key 或自动回退到官方服务。"
            : "使用 https://api.typesafe.ai/v1/systemone，请在下方保存 TYPESAFE_API_KEY。"),
          h("p", { className: "wr-muted" }, "修改后保存配置并重启 Gateway。保存页面不会向服务商发出请求。")),
        h("section", { className: "wr-card" }, h("h2", null, "Jev 判断提示词"),
          h("label", { className: "wr-field" }, h("span", null, "总体判断规则"),
            h("textarea", { name: "jev_instructions", rows: 7, maxLength: 12000, disabled: busy,
              value: s.jev_instructions, onChange: e => change("jev_instructions", e.target.value) }),
            h("small", null, "作为 state.routing_policy 发送给 Jev，可修改相关性、群聊、预约和忽略规则。分类选项和输出字段由插件维护。最多 12,000 字符；较长提示词会增加每批输入 token。")),
          h("div", { className: "wr-actions" },
            button("恢复默认提示词", () => change("jev_instructions", config.defaults.jev_instructions), !config.defaults),
            h("span", { className: "wr-muted" }, "恢复后需保存；已有批次保留原提示词。"))),
        h("section", { className: "wr-card" }, h("h2", null, "消息合并与判断"),
          h("div", { className: "wr-grid" },
            field("quiet_seconds", "静默合并窗口（秒）", "同一会话停止来信多久后开始判断。", { type: "number", min: 0.1, step: 0.1 }),
            field("max_wait_seconds", "最长等待（秒）", "持续活跃的群聊也会按此窗口切批。", { type: "number", min: 0.1, step: 0.1 }),
            field("max_batch_messages", "每批消息上限", "范围 1–20；达到上限可提前判断。", { type: "number", min: 1, max: 20, step: 1 }),
            field("max_context_chars", "历史上下文字符上限", "限制每次判断携带的历史内容。", { type: "number", min: 1, step: 1 }),
            field("min_confidence", "最低判断置信度", "低于阈值的候选交由复核 profile。", { type: "number", min: 0, max: 1, step: 0.05 }))),
        h("section", { className: "wr-card" }, h("h2", null, "Kanban 路由"),
          h("p", { className: "wr-muted" }, "填写已创建的 Hermes profile 名称。每个分类可复用同一个 agent。"),
          h("div", { className: "wr-grid" }, field("board", "看板名称"), field("triage_profile", "不确定事项复核 profile"),
            ...Object.entries(categories).map(([category, label]) => h("label", { className: "wr-field", key: category },
              h("span", null, label + " → profile"), h("input", { name: "route-" + category, value: s.routes[category], disabled: busy,
                onChange: e => change("routes", { ...s.routes, [category]: e.target.value }) }))))),
        h("section", { className: "wr-card" }, h("h2", null, "消息接入"),
          h("div", { className: "wr-grid" }, field("host", "监听地址"), field("port", "监听端口", null,
            { type: "number", min: 1, max: 65535, step: 1 })),
          h("p", null, "外部监听器提交至 http://<Hermes 主机>:" + s.port + "/wechat-router/v1/events，按接口文档进行 HMAC 签名。"),
          h("a", { href: "https://github.com/xyyhhh/hermes-wechat-router/blob/main/docs/provider-api.md", target: "_blank", rel: "noreferrer" }, "消息提供程序接口文档")),
        h("div", { className: "wr-save" }, h("button", { type: "submit", disabled: busy }, busy ? "处理中…" : "保存配置"),
          h("span", null, "保存后重启 Gateway；不会自动重启。"))),
      h("section", { className: "wr-card" }, h("h2", null, "凭据"),
        h("p", { className: "wr-muted" }, "通过 Hermes 的凭据接口单独保存；已有值不回显。保存后重启 Gateway。"),
        ...["WECHAT_ROUTER_SECRET", s.provider === "custom" ? "WECHAT_ROUTER_API_KEY" : "TYPESAFE_API_KEY"].map(name => h("div", { className: "wr-secret", key: name },
          h("label", { className: "wr-field" }, h("span", null, name + (config.secrets[name] ? " · 已配置" : " · 未配置")),
            h("input", { name, type: "password", autoComplete: "new-password", value: keys[name], disabled: busy,
              onChange: e => setKeys(old => ({ ...old, [name]: e.target.value })) }),
            h("small", null, name === "WECHAT_ROUTER_SECRET" ? "与外部监听器共享，至少 32 字节。" : name === "TYPESAFE_API_KEY" ? "仅用于 TypeSafe 官方服务。" : "仅用于当前配置的第三方 System One 服务。")),
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
