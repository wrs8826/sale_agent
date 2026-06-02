/**
 * 共享设置抽屉（chat / cleaner / reranker 三段 API 配置）
 *
 * 用法：在任意页面 <script> 中调用 SettingsDrawer.mount()，
 *      会自动在 header > .topnav 前面插入一个齿轮按钮，
 *      并在 body 末尾注入抽屉 DOM。
 *
 * 与后端 /settings 接口约定：
 *   GET 响应 { settings: { chat: {api_key_mask, api_key_set, base_url, model_name}, ... } }
 *   POST 请求 { chat: {...}, cleaner: {...}, reranker: {...} }，
 *        api_key 为空表示保留原值；非空则视为新明文，后端会加密后存储。
 */
(function () {
  const SECTIONS = [
    {
      key: "chat",
      title: "Chat",
      groups: [
        {
          key: "chat",
          subtitle: "Chat Model",
          hint: "对话主模型。API Key 将以 Fernet 加密形式存储于服务端。",
        },
        {
          key: "cleaner",
          subtitle: "AI 清洗",
          hint: "用于资料入库与对话反馈摘要。留空任一字段则继承 Chat Model 的对应配置。",
        },
      ],
    },
    {
      key: "knowledge",
      title: "Knowledge",
      groups: [
        {
          key: "embedding",
          subtitle: "Embedding Model",
          hint: "向量化模型。默认 text-embedding-v4，留空 API Key / base_url 继承 Chat Model。⚠ 更换后请清空向量库并重新入库，否则维度不匹配会查询失败。",
        },
        {
          key: "reranker",
          subtitle: "Rerank Model",
          hint: "检索重排序模型。默认 gte-rerank-v2，留空 API Key / base_url 继承 Chat Model。",
        },
      ],
    },
  ];

  let openedOnce = false;
  let currentMasked = {};

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const c of children) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function buildField(sectionKey, fieldKey, label, type, placeholder) {
    const inputId = `settings-${sectionKey}-${fieldKey}`;
    return el(
      "div",
      { class: "settings-field" },
      el("label", { for: inputId }, label),
      el("input", {
        id: inputId,
        type,
        placeholder: placeholder || "",
        "data-section": sectionKey,
        "data-field": fieldKey,
      })
    );
  }

  function buildSubsection(group) {
    const dot = el("span", {
      class: "status-dot",
      id: `dot-${group.key}`,
      title: "未测试",
    });
    return el(
      "div",
      { class: "settings-subsection" },
      el("h4", {}, dot, document.createTextNode(group.subtitle)),
      el("div", { class: "hint" }, group.hint),
      buildField(group.key, "api_key", "API Key", "password", "（留空保留原值）"),
      buildField(group.key, "base_url", "Base URL", "text"),
      buildField(group.key, "model_name", "Model Name", "text")
    );
  }

  function buildStorageSection() {
    return el(
      "div",
      { class: "settings-section" },
      el("h3", {}, "Storage"),
      el(
        "div",
        { class: "settings-subsection" },
        el(
          "h4",
          {},
          el("span", { class: "status-dot", id: "dot-storage", title: "无需测试" }),
          document.createTextNode("Wiki 目录")
        ),
        el(
          "div",
          { class: "hint" },
          "反馈 wiki 文件的存储路径。支持绝对路径或相对于 agent_service/ 的相对路径。留空使用默认 wiki/ 目录。修改后旧文件不会自动迁移，需手动移动。"
        ),
        el(
          "div",
          { class: "settings-field" },
          el("label", { for: "settings-storage-wiki_dir" }, "目录路径"),
          el("input", {
            id: "settings-storage-wiki_dir",
            type: "text",
            placeholder: "留空使用默认路径（agent_service/wiki/）",
            "data-section": "storage",
            "data-field": "wiki_dir",
          })
        )
      )
    );
  }

  function buildDrawer() {
    const body = el("div", { class: "settings-body" });
    for (const section of SECTIONS) {
      const sec = el(
        "div",
        { class: "settings-section" },
        el("h3", {}, section.title)
      );
      for (const group of section.groups) sec.appendChild(buildSubsection(group));
      body.appendChild(sec);
    }
    body.appendChild(buildStorageSection());

    const status = el("span", { class: "status", id: "settings-status" });
    const testBtn = el(
      "button",
      { class: "btn secondary", id: "settings-test", onclick: testSettings },
      "测试连通"
    );
    const saveBtn = el(
      "button",
      { class: "btn", id: "settings-save", onclick: saveSettings },
      "保存"
    );

    return el(
      "div",
      { class: "settings-drawer", id: "settings-drawer" },
      el(
        "div",
        { class: "settings-header" },
        el("h2", {}, "⚙ API 设置"),
        el("button", { class: "close-btn", onclick: closeDrawer, title: "关闭" }, "×")
      ),
      body,
      el("div", { class: "settings-footer" }, status, testBtn, saveBtn)
    );
  }

  const SECTION_KEYS = ["chat", "cleaner", "reranker", "embedding"];
  const SECTION_LABEL = {
    chat: "Chat Model",
    cleaner: "AI 清洗",
    reranker: "Rerank Model",
    embedding: "Embedding Model",
  };

  function setDot(key, state, tip) {
    const d = document.getElementById(`dot-${key}`);
    if (!d) return;
    d.classList.remove("ok", "fail", "pending");
    if (state) d.classList.add(state);
    d.title = tip || (state === "ok" ? "连通" : state === "fail" ? "失败" : "未测试");
  }

  function resetDots(state) {
    for (const k of SECTION_KEYS) setDot(k, state || "");
  }

  async function testSettings() {
    const btn = document.getElementById("settings-test");
    const saveBtn = document.getElementById("settings-save");
    btn.disabled = true;
    saveBtn.disabled = true;
    resetDots("pending");
    setStatus("正在测试 4 个模型…", "ing");
    try {
      const r = await fetch("/settings/test", { method: "POST" });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "测试失败");
      const results = data.results || {};
      const okList = [];
      const failList = [];
      for (const k of SECTION_KEYS) {
        const item = results[k] || {};
        if (item.ok) {
          setDot(k, "ok", `连通（${item.latency_ms} ms）`);
          okList.push(SECTION_LABEL[k]);
        } else {
          setDot(k, "fail", `失败：${item.error || "未知错误"}`);
          failList.push(SECTION_LABEL[k]);
        }
      }
      const parts = [];
      if (okList.length) parts.push(`✓ 连通：${okList.join("、")}`);
      if (failList.length) parts.push(`✗ 失败：${failList.join("、")}`);
      setStatus(parts.join("　"), failList.length ? "err" : "ok");
    } catch (e) {
      resetDots("");
      setStatus("测试失败: " + e.message, "err");
    } finally {
      btn.disabled = false;
      saveBtn.disabled = false;
    }
  }

  function setStatus(msg, kind) {
    const s = document.getElementById("settings-status");
    if (!s) return;
    s.textContent = msg || "";
    s.className = "status" + (kind ? " " + kind : "");
  }

  function fillForm(masked) {
    currentMasked = masked || {};
    for (const sectionKey of ["chat", "cleaner", "reranker", "embedding"]) {
      const data = currentMasked[sectionKey] || {};
      const fields = {
        api_key: { placeholder: data.api_key_set ? `已设置（${data.api_key_mask}）— 留空保留` : "未设置，请输入" },
        base_url: { value: data.base_url || "" },
        model_name: { value: data.model_name || "" },
      };
      for (const [field, attrs] of Object.entries(fields)) {
        const input = document.querySelector(
          `input[data-section="${sectionKey}"][data-field="${field}"]`
        );
        if (!input) continue;
        if ("value" in attrs) input.value = attrs.value;
        else input.value = "";
        if ("placeholder" in attrs) input.placeholder = attrs.placeholder;
      }
    }
    // storage 段
    const storageData = currentMasked.storage || {};
    const wikiInput = document.querySelector('input[data-section="storage"][data-field="wiki_dir"]');
    if (wikiInput) wikiInput.value = storageData.wiki_dir || "";
  }

  async function loadSettings() {
    setStatus("加载中…", "ing");
    resetDots("");
    try {
      const r = await fetch("/settings");
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "加载失败");
      fillForm(data.settings || {});
      setStatus("", "");
    } catch (e) {
      setStatus("加载失败: " + e.message, "err");
    }
  }

  async function saveSettings() {
    const btn = document.getElementById("settings-save");
    btn.disabled = true;
    setStatus("保存中…", "ing");

    const payload = {};
    for (const sectionKey of ["chat", "cleaner", "reranker", "embedding"]) {
      payload[sectionKey] = {};
      for (const field of ["api_key", "base_url", "model_name"]) {
        const input = document.querySelector(
          `input[data-section="${sectionKey}"][data-field="${field}"]`
        );
        if (!input) continue;
        payload[sectionKey][field] = input.value;
      }
    }
    // storage 段
    const wikiInput = document.querySelector('input[data-section="storage"][data-field="wiki_dir"]');
    payload.storage = { wiki_dir: wikiInput ? wikiInput.value.trim() : "" };

    try {
      const r = await fetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "保存失败");
      fillForm(data.settings || {});
      resetDots("");                       // 配置变更，旧测试结果失效

      if (data.embedding_changed) {
        // Embedding 模型变更 → 自动重建向量库
        setStatus("Embedding 已变更，正在重建向量库…", "ing");
        try {
          const rb = await fetch("/vectordb/rebuild", { method: "POST" });
          if (rb.ok) {
            const reader  = rb.body.getReader();
            const decoder = new TextDecoder();
            let buf = "";
            outer: while (true) {
              const { done, value } = await reader.read();
              if (done) break;
              buf += decoder.decode(value, { stream: true });
              const lines = buf.split("\n");
              buf = lines.pop();
              for (const line of lines) {
                if (!line.startsWith("data:")) continue;
                let evt;
                try { evt = JSON.parse(line.slice(5).trim()); } catch { continue; }
                if (evt.type === "done") {
                  setStatus(`已保存 ✓ 向量库已重建（${evt.count} 块）`, "ok");
                  setTimeout(() => setStatus("", ""), 4000);
                  break outer;
                } else if (evt.type === "error") {
                  setStatus("向量库重建失败: " + evt.message, "err");
                  break outer;
                }
              }
            }
          } else {
            setStatus("已保存 ✓（向量库重建请求失败，请手动清空重建）", "err");
          }
        } catch (rbErr) {
          setStatus("已保存 ✓（向量库重建异常: " + rbErr.message + "）", "err");
        }
      } else {
        setStatus("已保存 ✓（点击测试连通验证）", "ok");
        setTimeout(() => setStatus("", ""), 3000);
      }
    } catch (e) {
      setStatus("保存失败: " + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  function openDrawer() {
    document.getElementById("settings-drawer").classList.add("open");
    document.getElementById("settings-overlay").classList.add("open");
    if (!openedOnce) {
      openedOnce = true;
      loadSettings();
    }
  }

  function closeDrawer() {
    document.getElementById("settings-drawer").classList.remove("open");
    document.getElementById("settings-overlay").classList.remove("open");
  }

  function mount() {
    // 1) 注入齿轮按钮到 header（紧贴 topnav 右侧；无 topnav 时贴 header 末端）
    const header = document.querySelector("header");
    if (header && !header.querySelector(".settings-btn")) {
      const btn = el(
        "button",
        {
          class: "settings-btn",
          title: "API 设置",
          onclick: openDrawer,
        },
        "⚙"
      );
      const nav = header.querySelector(".topnav");
      if (nav) header.insertBefore(btn, nav.nextSibling);
      else header.appendChild(btn);
    }

    // 2) 注入抽屉 + 遮罩到 body 末
    if (!document.getElementById("settings-drawer")) {
      const overlay = el("div", {
        class: "settings-overlay",
        id: "settings-overlay",
        onclick: closeDrawer,
      });
      document.body.appendChild(overlay);
      document.body.appendChild(buildDrawer());
    }
  }

  window.SettingsDrawer = { mount, open: openDrawer, close: closeDrawer };

  // 自动挂载
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
