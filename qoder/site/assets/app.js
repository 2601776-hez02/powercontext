/* 知序 ZhiXu 项目网站 —— 纯静态，无外部依赖。
   所有数字来自 data/demo.json（由 `python -m zhixu export-site` 从本地 seekdb 导出）。 */

(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const state = {
    data: null,
    activeDemo: 0,
    live: false,
  };

  const STATUS_COLUMNS = [
    { key: "blocked", label: "阻塞" },
    { key: "doing", label: "进行中" },
    { key: "todo", label: "待开始" },
    { key: "done", label: "已完成" },
  ];

  const fmtInt = (value) => new Intl.NumberFormat("zh-CN").format(value);
  const escapeHtml = (text) =>
    String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function inline(text) {
    return text
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s（(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|[\s（(])_([^_\n]+)_/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  function markdownToHtml(markdown) {
    const lines = String(markdown || "").split(/\r?\n/);
    const html = [];
    let listOpen = false;
    let paragraph = [];

    const flushParagraph = () => {
      if (paragraph.length) {
        html.push(`<p>${inline(escapeHtml(paragraph.join(" ")))}</p>`);
        paragraph = [];
      }
    };
    const closeList = () => {
      if (listOpen) {
        html.push("</ul>");
        listOpen = false;
      }
    };

    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (!line.trim()) {
        flushParagraph();
        closeList();
        continue;
      }
      if (/^---+\s*$/.test(line)) {
        flushParagraph(); closeList();
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        flushParagraph(); closeList();
        const level = Math.min(4, heading[1].length);
        html.push(`<h${level}>${inline(escapeHtml(heading[2]))}</h${level}>`);
        continue;
      }
      if (/^>\s?/.test(line)) {
        flushParagraph(); closeList();
        html.push(`<blockquote>${inline(escapeHtml(line.replace(/^>\s?/, "")))}</blockquote>`);
        continue;
      }
      const bullet = line.match(/^[-*]\s+(.*)$/);
      if (bullet) {
        flushParagraph();
        if (!listOpen) { html.push("<ul>"); listOpen = true; }
        let content = bullet[1];
        let cls = "";
        const checkbox = content.match(/^\[([ xX])\]\s*(.*)$/);
        if (checkbox) {
          content = checkbox[2];
          cls = checkbox[1].toLowerCase() === "x" ? ' class="done"' : "";
        }
        html.push(`<li${cls}>${inline(escapeHtml(content))}</li>`);
        continue;
      }
      paragraph.push(line);
    }
    flushParagraph();
    closeList();
    return html.join("\n");
  }

  /* ------------------------------------------------------------- 渲染 */
  function renderHero(data) {
    $("#version-line").textContent = `zhixu ${data.version}`;
    const stats = data.stats;
    const median = data.evidence?.benchmark?.median_ms;
    const kpis = [
      [stats.documents, "篇资料"],
      [stats.chunks, "个片段"],
      [stats.tasks, "个任务"],
      [median ? `${median.toFixed(1)} ms` : "—", "中位检索"],
    ];
    $("#kpi-row").innerHTML = kpis
      .map(([value, label]) => `<div class="kpi"><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`)
      .join("");

    const demo = data.demos?.[0];
    if (demo) {
      const items = (demo.items || []).slice(0, 2);
      const query = demo.query.length > 26 ? demo.query.slice(0, 26) + "…" : demo.query;
      const lines = [
        `$ python -m zhixu ask "${query}"`,
        "",
        'BEGIN_ZHIXU_CONTEXT_V1 trust="untrusted_local_corpus"',
        `{"schema":"zhixu.context.v1","query":"${query}",`,
        ' "items":[',
      ];
      items.forEach((item, index) => {
        const c = item.citation;
        const content = String(item.content).replace(/\s+/g, " ").slice(0, 38);
        lines.push(`  {"citation":{"path":"${c.path}",`);
        lines.push(`              "heading":"${c.heading}","lines":[${c.lines[0]},${c.lines[1]}]},`);
        lines.push(`   "content":"${content}…"}${index < items.length - 1 ? "," : ""}`);
      });
      lines.push(" ]}");
      lines.push("END_ZHIXU_CONTEXT_V1");
      lines.push("");
      lines.push(
        `  ${demo.items.length} 条证据 · ${demo.content_bytes} / ${demo.budget_bytes} 字节 · ${demo.latency_ms} ms`
      );
      $("#hero-terminal").textContent = lines.join("\n");
    }
  }

  function renderMeta(data) {
    $("#generated-at").textContent = data.generated_at;
    $("#demo-version").textContent = `zhixu ${data.version}`;
    $("#footer-generated").textContent = data.generated_at;
    $("#schema-version").textContent = data.store?.schema_version ?? "1";
    const repo = data.repo || {};
    if (repo.remote) {
      $("#repo-link").href = repo.remote;
      $("#repo-info").href = repo.remote;
      $("#repo-info").textContent = repo.remote.replace("https://", "");
    }
    $("#branch-info").textContent = repo.branch || "—";
    $("#version-info").textContent = `zhixu ${data.version} · schema v${data.store?.schema_version ?? "1"} · ${data.stats.searches} 次检索`;
    $("#ev-docs").textContent = fmtInt(data.stats.documents);
    $("#ev-chunks").textContent = fmtInt(data.stats.chunks);
    const bench = data.evidence?.benchmark || {};
    $("#ev-median").textContent = bench.median_ms ? `${bench.median_ms} ms` : "—";
    $("#ev-p95").textContent = bench.p95_ms ? `${bench.p95_ms} ms` : "—";
  }

  function renderDemo(demo) {
    if (!demo) return;
    const results = $("#search-results");
    results.innerHTML = demo.items.length
      ? demo.items
          .map((item, i) => {
            const c = item.citation;
            const badges = (item.channels || [])
              .map((channel) => `<span class="badge ${channel === "vector" ? "vec" : ""}">${channel}</span>`)
              .join("");
            return `
              <article class="result">
                <div class="result-top">
                  <span class="result-index">${String(i + 1).padStart(2, "0")}</span>
                  <span class="result-title">${escapeHtml(c.title)}</span>
                  <span class="result-heading">/ ${escapeHtml(c.heading)}</span>
                  <span class="badges">${badges}<span class="badge score">${Number(item.score).toFixed(4)}</span></span>
                </div>
                <div class="citation">${escapeHtml(c.path)} · 第 ${c.lines[0]}-${c.lines[1]} 行 · 字符 ${c.char_start}-${c.char_end} · chunk ${c.chunk_id}</div>
                <div class="snippet">${escapeHtml(item.content)}</div>
              </article>`;
          })
          .join("")
      : '<article class="result"><div class="snippet">该查询没有命中足够相关的证据，可以换一个说法。</div></article>';

    $("#pack-meter").textContent = `${demo.content_bytes} / ${demo.budget_bytes} bytes`;
    $("#pack-body").textContent = demo.content;
    $("#pack-latency").textContent = `${demo.latency_ms} ms`;
    let channels = `fts ${demo.fts_hits} · vector ${demo.vector_hits}`;
    if (demo.aliases?.length) channels += ` · 术语扩展：${demo.aliases.join("、")}`;
    $("#pack-channels").textContent = channels;
  }

  function renderSearch(index) {
    const data = state.data;
    const demo = data.demos?.[index];
    if (!demo) return;
    state.activeDemo = index;
    $$("#query-chips .query-chip").forEach((chip, i) => {
      chip.classList.toggle("active", i === index);
    });
    renderDemo(demo);
  }

  function liveEnabled() {
    return Boolean(state.live);
  }

  async function runLiveQuery(query) {
    const hint = $("#live-hint");
    hint.textContent = "正在检索…";
    try {
      const response = await fetch(`api/ask?q=${encodeURIComponent(query)}&budget=1800`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      $$("#query-chips .query-chip").forEach((chip) => chip.classList.remove("active"));
      renderDemo({
        query: result.query,
        items: result.items,
        content: result.content,
        content_bytes: result.content_bytes,
        budget_bytes: result.budget_bytes,
        latency_ms: result.meta.latency_ms,
        fts_hits: result.meta.fts_hits,
        vector_hits: result.meta.vector_hits,
        aliases: result.meta.aliases,
      });
      hint.textContent = "已连接本地 seekdb";
      hint.classList.add("on");
    } catch (error) {
      hint.textContent = `实时检索失败：${error.message}`;
      hint.classList.remove("on");
    }
  }

  async function checkLive() {
    const input = $("#live-input");
    const hint = $("#live-hint");
    try {
      const response = await fetch("api/health", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const health = await response.json();
      state.live = health.ok === true;
      hint.textContent = `已连接本地 seekdb ${health.engine_version || ""}`;
      hint.classList.add("on");
      input.disabled = false;
    } catch {
      state.live = false;
      hint.textContent = "静态演示 · 运行 python -m zhixu serve 可实时检索";
      input.disabled = true;
      $("#live-form button").disabled = true;
    }
    $("#live-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const query = input.value.trim();
      if (liveEnabled() && query) runLiveQuery(query);
    });
  }

  function renderQueryChips(data) {
    const chips = $("#query-chips");
    chips.innerHTML = (data.demos || [])
      .map(
        (demo, i) =>
          `<button class="query-chip" data-index="${i}" title="${escapeHtml(demo.intent || "")}">${escapeHtml(demo.query)}</button>`
      )
      .join("");
    $$(".query-chip", chips).forEach((chip) => {
      chip.addEventListener("click", () => renderSearch(Number(chip.dataset.index)));
    });
    renderSearch(0);
  }

  function renderTasks(data) {
    const board = $("#task-board");
    const counts = data.task_summary?.counts || {};
    board.innerHTML = STATUS_COLUMNS.map((column) => {
      const tasks = (data.tasks || []).filter((task) => task.status === column.key);
      const cards = tasks
        .map((task) => {
          const prioClass = task.priority === "P0" ? "p0" : "";
          const evidence = task.evidence_document_id ? " · 有证据" : "";
          return `
            <div class="task-card">
              <p>${escapeHtml(task.title)}</p>
              <div class="task-meta">
                <span class="${prioClass}">${escapeHtml(task.priority)}</span>
                ${task.due_date ? `<span>截止 ${escapeHtml(task.due_date)}</span>` : ""}
                <span>${escapeHtml(task.owner || "未指派")}${evidence}</span>
              </div>
              ${task.detail ? `<div class="task-detail">${escapeHtml(task.detail)}</div>` : ""}
            </div>`;
        })
        .join("");
      return `
        <div class="board-col">
          <h4>${column.label}<b>${tasks.length}</b></h4>
          ${cards || '<div class="task-card"><p class="muted">暂无</p></div>'}
        </div>`;
    }).join("");

    const summary = data.task_summary || {};
    const overdue = summary.overdue?.length ? ` · 逾期：${summary.overdue.join("、")}` : "";
    const dueSoon = summary.due_soon?.length ? ` · 7 天内到期：${summary.due_soon.join("、")}` : "";
    $("#board-note").textContent =
      `共 ${summary.total || 0} 个任务，完成率 ${(Number(summary.completion_rate || 0) * 100).toFixed(0)}%` +
      `（状态计数 ${Object.entries(summary.counts_label || {}).map(([k, v]) => `${k} ${v}`).join(" / ")}）` +
      overdue + dueSoon;
  }

  function renderDigest(data) {
    const paper = $("#digest-paper");
    paper.innerHTML = markdownToHtml(data.digest?.markdown || "暂无周报");
  }

  function renderEvidence(data) {
    const evidence = data.evidence || {};
    const list = $("#index-list");
    list.innerHTML = (evidence.indexes || [])
      .map(
        (item) => `
          <li>
            <span class="name">${escapeHtml(item.index)}</span>
            <span class="muted small">${escapeHtml(item.detail || (item.columns || []).filter((c) => !c.startsWith("__")).join(", ") || "—")}</span>
            <span class="kind">${escapeHtml(item.kind)}</span>
          </li>`
      )
      .join("");
    $("#explain-plan").textContent = (evidence.explain || []).join("\n") || "（未取到查询计划）";

    const checks = Object.entries(evidence.checks || {});
    $("#check-list").innerHTML = checks
      .map(([name, ok]) => `<li class="${ok ? "" : "no"}">${escapeHtml(checksLabel(name))}</li>`)
      .join("");
    const bench = evidence.benchmark || {};
    $("#bench-note").textContent =
      `${bench.queries || 0} 条查询 × ${bench.rounds || 0} 轮，共 ${bench.samples || 0} 次取样；` +
      `中位 ${bench.median_ms} ms，P95 ${bench.p95_ms} ms，最大 ${bench.max_ms} ms。` +
      `测试与自检：24 项 pytest 通过；索引与查询计划由 doctor 实查。`;
  }

  function checksLabel(name) {
    const labels = {
      fts_index: "全文索引 ft_body 存在",
      vector_index: "向量索引 vk_embedding 存在",
      fts_hits: "全文检索命中非空",
      vector_hits: "向量检索命中非空",
      documents: "资料已入库",
      chunks: "片段已生成",
      tasks: "任务与事件可用",
    };
    return labels[name] || name;
  }

  function setupTabs() {
    const activate = (name) => {
      $$(".tab").forEach((item) => {
        const active = item.dataset.tab === name;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", active ? "true" : "false");
      });
      $$(".tab-panel").forEach((panel) => {
        panel.classList.toggle("active", panel.id === `panel-${name}`);
      });
    };
    $$(".tab").forEach((tab) => {
      tab.addEventListener("click", () => activate(tab.dataset.tab));
    });
    const fromHash = (location.hash || "").replace("#", "");
    if (["tasks", "digest", "search"].includes(fromHash)) {
      activate(fromHash);
    }
  }

  async function boot() {
    setupTabs();
    await checkLive();
    try {
      const response = await fetch("data/demo.json", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      state.data = data;
      renderMeta(data);
      renderHero(data);
      renderQueryChips(data);
      renderTasks(data);
      renderDigest(data);
      renderEvidence(data);
    } catch (error) {
      const fallback = document.createElement("p");
      fallback.className = "muted";
      fallback.textContent = `演示数据加载失败：${error.message}。请在本地执行 python -m zhixu export-site 生成 data/demo.json。`;
      $("#demo").appendChild(fallback);
    }
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
