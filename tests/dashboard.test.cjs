const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const { JSDOM } = require("jsdom");
const React = require("react");

test("Dashboard renders, saves scoped config and secrets, pauses, and retries", async () => {
  const dom = new JSDOM('<div id="root"></div>', { runScripts: "outside-only", url: "http://localhost" });
  global.window = dom.window;
  global.document = dom.window.document;
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const { createRoot } = require("react-dom/client");
  const requests = [];
  let paused = false;
  let failure = true;
  const config = {
    profile: "work", version: "0.3.0", enabled: true, secrets: { TYPESAFE_API_KEY: true },
    defaults: { jev_instructions: "默认判断规则" },
    settings: { mode: "observe", model: "jev-latest", provider: "typesafe", provider_base_url: "", jev_instructions: "默认判断规则", owner_name: "我", owner_ids: ["me"], interests: [], sources: [],
      timezone: "Asia/Shanghai", quiet_seconds: 8, max_wait_seconds: 60, max_batch_messages: 12, request_timeout: 15,
      max_context_chars: 6000, min_confidence: 0.6, board: "default", triage_profile: "inbox", host: "127.0.0.1", port: 8768,
      routes: Object.fromEntries(["schedule", "deadline", "todo", "important", "other"].map(x => [x, "inbox"])) }
  };
  let Component;
  dom.window.__HERMES_PLUGINS__ = { register(name, value) { assert.equal(name, "wechat-router"); Component = value; } };
  dom.window.TextEncoder = TextEncoder;
  dom.window.__HERMES_PLUGIN_SDK__ = { React, async fetchJSON(url, options) {
    const body = options && options.body && JSON.parse(options.body);
    requests.push({ url, options, body });
    if (url.endsWith("/config")) return structuredClone(config);
    if (url.includes("/status")) return { initialized: true, paused, messages: 42, buffered: 3,
      usage: { calls: 4, input_tokens: 200, output_tokens: 80 }, failures: failure ? [{ id: "batch-1", error: "fixture_error" }] : [] };
    if (url.includes("/pause")) { paused = body.paused; return { paused }; }
    if (url.includes("/retry")) { failure = false; return { queued: true }; }
    return { ok: true };
  } };
  dom.window.eval(fs.readFileSync("dashboard/dist/index.js", "utf8"));
  const root = createRoot(document.getElementById("root"));
  async function click(label) {
    const button = [...document.querySelectorAll("button")].find(b => b.textContent === label && !b.disabled);
    assert.ok(button, label);
    await React.act(async () => { button.click(); });
  }
  async function input(name, value) {
    const element = document.querySelector(`[name="${name}"]`);
    const prototype = element.tagName === "TEXTAREA" ? dom.window.HTMLTextAreaElement.prototype : dom.window.HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
    await React.act(async () => {
      descriptor.set.call(element, value);
      element.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    });
  }
  try {
    await React.act(async () => { root.render(React.createElement(Component)); });
    assert.match(document.body.textContent, /Profile: work/);
    assert.match(document.body.textContent, /42/);
    assert.equal(document.querySelector('[name="TYPESAFE_API_KEY"]').value, "");
    await React.act(async () => {
      const select = document.querySelector('[name="provider"]');
      select.value = "custom";
      select.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
    });
    assert.ok(document.querySelector('[name="WECHAT_ROUTER_API_KEY"]'));
    assert.equal(document.querySelector('[name="TYPESAFE_API_KEY"]'), null);
    await input("provider_base_url", "https://provider.example/v1");
    await input("jev_instructions", "只关注本人已确认的预约");
    await click("暂停处理");
    assert.equal(paused, true);
    await click("恢复处理");
    assert.equal(paused, false);
    await click("重试");
    assert.equal(failure, false);
    await input("owner_name", "本人改名");
    await React.act(async () => {
      document.querySelector("form").dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    });
    const saved = requests.find(x => x.url.includes("/config?") && x.options.method === "PUT");
    assert.equal(saved.body.settings.owner_name, "本人改名");
    assert.equal(saved.body.settings.provider_base_url, "https://provider.example/v1");
    assert.equal(saved.body.settings.jev_instructions, "只关注本人已确认的预约");
    assert.match(saved.url, /profile=work/);
    await input("WECHAT_ROUTER_SECRET", "synthetic-new-secret-at-least-32-bytes");
    await click("保存凭据");
    const secret = requests.find(x => x.url.includes("/api/env"));
    assert.equal(secret.body.value, "synthetic-new-secret-at-least-32-bytes");
    assert.match(secret.url, /profile=work/);
    assert.equal(document.querySelector('[name="WECHAT_ROUTER_SECRET"]').value, "");
    await input("WECHAT_ROUTER_API_KEY", "synthetic-custom-key");
    await click("保存凭据");
    assert.ok(requests.find(x => x.body && x.body.key === "WECHAT_ROUTER_API_KEY" && x.body.value === "synthetic-custom-key"));
    assert.equal(document.querySelector('[name="WECHAT_ROUTER_API_KEY"]').value, "");
    await click("恢复默认提示词");
    assert.equal(document.querySelector('[name="jev_instructions"]').value, "默认判断规则");
    assert.ok(requests.every(x => !x.url.includes("jev") && !x.url.includes("typesafe")));
  } finally {
    await React.act(async () => { root.unmount(); });
    dom.window.close();
    delete global.window;
    delete global.document;
  }
});
