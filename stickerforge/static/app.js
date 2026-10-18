const $ = (id) => document.getElementById(id);

const state = {
  sources: [],
  packId: null,
  stickers: [],
  order: [],
  cover: null,
  title: "",
  author: "",
};

const FPS_CHOICES = [5, 8, 10, 12, 15, 20, 24, 30];

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function")
      node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(2) + " MB";
}

function fmtTime(seconds) {
  const value = Math.max(0, seconds || 0);
  const minutes = Math.floor(value / 60);
  return `${minutes}:${(value % 60).toFixed(1).padStart(4, "0")}`;
}

function truncate(text, n) {
  return text.length > n ? text.slice(0, n - 1) + "\u2026" : text;
}

function setStatus(message, kind) {
  const node = $("status");
  node.textContent = message || "";
  node.className = "status" + (kind ? " " + kind : "");
}

async function addSources() {
  const files = Array.from($("files").files);
  const urls = $("urls").value.trim();
  if (!files.length && !urls) {
    setStatus("Paste a URL or choose a file first.", "error");
    return;
  }
  const form = new FormData();
  form.append("urls", urls);
  const local = {};
  for (const file of files) {
    const objectUrl = URL.createObjectURL(file);
    local[file.name] = objectUrl;
    form.append("files", file);
  }

  const button = $("add");
  button.disabled = true;
  setStatus("Reading sources\u2026", "busy");
  try {
    const response = await fetch("/api/analyze", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.error || "Could not read sources.", "error");
      return;
    }
    for (const source of data.sources) {
      const isUrl = /^https?:\/\//i.test(source.label);
      source._src = local[source.label] || (isUrl ? source.label : null);
      source.trim_start = 0;
      source.trim_end = source.kind === "video" ? source.duration : 0;
      source.fps = source.kind === "video" ? 12 : null;
      source.keep_animation = true;
      state.sources.push(source);
    }
    $("urls").value = "";
    $("files").value = "";
    renderSources();
    setStatus(
      `Added ${data.sources.length} source${data.sources.length === 1 ? "" : "s"}.`,
      "ok"
    );
    if (data.warnings && data.warnings.length) {
      setStatus(data.warnings.join(" "), "error");
    }
  } catch (error) {
    setStatus("Network error: " + error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function removeSource(index) {
  const [removed] = state.sources.splice(index, 1);
  if (removed && removed._src && removed._src.startsWith("blob:")) URL.revokeObjectURL(removed._src);
  renderSources();
}

function metaLine(source) {
  if (source.kind === "video") {
    return `${source.width}\u00d7${source.height} \u00b7 ${fmtTime(source.duration)} \u00b7 ${source.fps} fps`;
  }
  const bits = [`${source.width}\u00d7${source.height}`];
  if (source.animated) bits.push(`${source.frames} frames`);
  else bits.push("static");
  return bits.join(" \u00b7 ");
}

function renderSources() {
  const list = $("sources");
  list.innerHTML = "";
  if (!state.sources.length) {
    list.append(h("p", { class: "empty" }, "No sources yet. Add URLs or files above."));
    return;
  }
  state.sources.forEach((source, index) => list.append(sourceCard(source, index)));
}

function sourceCard(source, index) {
  const card = h("div", { class: "src-card" + (source.kind === "video" ? " is-video" : "") });
  card.append(
    h(
      "div",
      { class: "src-head" },
      h("img", { class: "src-thumb", src: source.thumb_url, alt: "" }),
      h(
        "div",
        { class: "src-meta" },
        h("div", { class: "src-name", title: source.label }, truncate(source.label, 44)),
        h("div", { class: "src-sub" }, metaLine(source))
      ),
      h(
        "button",
        { class: "mini", type: "button", title: "Remove", onclick: () => removeSource(index) },
        "\u2715"
      )
    )
  );

  if (source.kind === "video") card.append(videoTimeline(source));

  if (source.kind === "video" || source.animated) {
    card.append(
      h(
        "label",
        { class: "check small" },
        h("input", {
          type: "checkbox",
          checked: source.keep_animation !== false,
          onchange: (event) => {
            source.keep_animation = event.target.checked;
          },
        }),
        h("span", {}, "Keep animation")
      )
    );
  }
  return card;
}

function videoTimeline(source) {
  const duration = Math.max(source.duration || 0, 0.1);
  const video = h("video", {
    class: "src-video",
    src: source._src || "",
    controls: true,
    preload: "metadata",
    playsinline: true,
  });

  const startOut = h("span", { class: "trim-val" });
  const endOut = h("span", { class: "trim-val" });
  const spanOut = h("span", { class: "trim-span" });
  const framesOut = h("span", { class: "trim-frames" });

  const startRange = h("input", {
    type: "range",
    class: "trim",
    min: 0,
    max: duration,
    step: 0.05,
    value: source.trim_start,
  });
  const endRange = h("input", {
    type: "range",
    class: "trim",
    min: 0,
    max: duration,
    step: 0.05,
    value: source.trim_end,
  });

  const sync = () => {
    if (source.trim_end <= source.trim_start) source.trim_end = source.trim_start + 0.1;
    startRange.value = source.trim_start;
    endRange.value = source.trim_end;
    startOut.textContent = fmtTime(source.trim_start);
    endOut.textContent = fmtTime(source.trim_end);
    const span = source.trim_end - source.trim_start;
    spanOut.textContent = `${span.toFixed(1)}s`;
    spanOut.classList.toggle("over", span > 10);
    spanOut.title = span > 10 ? "WhatsApp caps animated stickers at ~10s; it will be trimmed" : "";
    framesOut.textContent = `~${Math.max(2, Math.round(span * source.fps))} frames`;
  };

  startRange.addEventListener("input", () => {
    source.trim_start = Math.min(parseFloat(startRange.value), source.trim_end - 0.1);
    sync();
  });
  endRange.addEventListener("input", () => {
    source.trim_end = Math.max(parseFloat(endRange.value), source.trim_start + 0.1);
    sync();
  });

  video.addEventListener("timeupdate", () => {
    if (video.currentTime > source.trim_end) video.currentTime = source.trim_start;
    if (video.currentTime < source.trim_start - 0.2) video.currentTime = source.trim_start;
  });

  const fpsSelect = h(
    "select",
    {
      class: "fps-select",
      onchange: (event) => {
        source.fps = parseFloat(event.target.value);
        sync();
      },
    },
    FPS_CHOICES.map((value) =>
      h("option", { value, selected: value === source.fps }, value + " fps")
    )
  );

  const timeline = h(
    "div",
    { class: "timeline" },
    video,
    h(
      "div",
      { class: "trim-row" },
      h("span", { class: "trim-label" }, "start"),
      startRange,
      startOut,
      h(
        "button",
        {
          class: "mini",
          type: "button",
          title: "Use playhead as start",
          onclick: () => {
            source.trim_start = Math.min(video.currentTime, source.trim_end - 0.1);
            sync();
          },
        },
        "\u25c0"
      )
    ),
    h(
      "div",
      { class: "trim-row" },
      h("span", { class: "trim-label" }, "end"),
      endRange,
      endOut,
      h(
        "button",
        {
          class: "mini",
          type: "button",
          title: "Use playhead as end",
          onclick: () => {
            source.trim_end = Math.max(video.currentTime, source.trim_start + 0.1);
            sync();
          },
        },
        "\u25b6"
      )
    ),
    h(
      "div",
      { class: "trim-summary" },
      h("span", { class: "trim-label" }, "clip"),
      spanOut,
      h("span", { class: "trim-label" }, "speed"),
      fpsSelect,
      framesOut
    )
  );
  sync();
  return timeline;
}

async function build() {
  if (!state.sources.length) {
    setStatus("Add at least one source.", "error");
    return;
  }
  const payload = {
    title: $("title").value || "My Pack",
    author: $("author").value || "",
    padding: parseInt($("padding").value, 10) || 0,
    max_frames: parseInt($("maxFrames").value, 10) || 90,
    sources: state.sources.map((source) => ({
      sid: source.sid,
      label: source.label,
      trim_start: source.kind === "video" ? source.trim_start : null,
      trim_end: source.kind === "video" ? source.trim_end : null,
      fps: source.kind === "video" ? source.fps : null,
      keep_animation: source.keep_animation !== false,
    })),
  };

  const button = $("build");
  button.disabled = true;
  setStatus("Converting\u2026 this can take a moment.", "busy");
  try {
    const response = await fetch("/api/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      setStatus(data.error || "Build failed.", "error");
      return;
    }
    state.packId = data.pack_id;
    state.stickers = data.stickers;
    state.order = data.stickers.map((s) => s.sid);
    state.cover = (data.stickers.find((s) => s.cover) || data.stickers[0]).sid;
    state.title = data.title;
    state.author = data.author;
    render(data);
    setStatus(`Built ${data.count} sticker${data.count === 1 ? "" : "s"}.`, "ok");
  } catch (error) {
    setStatus("Network error: " + error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function render(data) {
  $("result").hidden = false;
  $("packTitle").textContent = state.title;
  $("packAuthor").textContent = state.author ? "by " + state.author : "no author set";
  $("packStats").textContent = `${data.count}/${data.max_stickers} stickers \u00b7 ${fmtBytes(data.total_bytes)}`;
  $("coverImg").src = data.cover_url;
  $("packIcon").src = data.cover_url;

  const warnings = $("warnings");
  warnings.innerHTML = "";
  (data.warnings || []).forEach((text) => warnings.append(h("div", { class: "warn" }, text)));

  $("chat").innerHTML = '<div class="bubble in">Tap a sticker below to drop it into the chat.</div>';
  renderTray();
  renderList();
  $("result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function bySid(sid) {
  return state.stickers.find((s) => s.sid === sid);
}

function renderTray() {
  const tray = $("tray");
  tray.innerHTML = "";
  state.order.forEach((sid, index) => {
    const sticker = bySid(sid);
    const cell = h("div", {
      class: "tray-cell" + (sticker.animated ? " animated" : ""),
      draggable: true,
      "data-index": index,
    });
    const img = h("img", { src: sticker.preview_url, alt: sticker.source });
    const kind = sticker.animated ? `animated, ${sticker.frames} frames` : "static";
    img.title = `${sticker.width}\u00d7${sticker.height} \u00b7 ${fmtBytes(sticker.size)} \u00b7 ${kind}`;
    cell.append(img);
    if (sticker.cover) {
      cell.append(h("span", { class: "cover-badge", title: "Pack cover" }, "\u2605"));
    }
    cell.addEventListener("click", () => sendSticker(sticker));
    cell.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", String(index));
      event.dataTransfer.effectAllowed = "move";
      cell.classList.add("dragging");
    });
    cell.addEventListener("dragend", () => cell.classList.remove("dragging"));
    cell.addEventListener("dragover", (event) => {
      event.preventDefault();
      cell.classList.add("drop");
    });
    cell.addEventListener("dragleave", () => cell.classList.remove("drop"));
    cell.addEventListener("drop", (event) => {
      event.preventDefault();
      cell.classList.remove("drop");
      move(Number(event.dataTransfer.getData("text/plain")), index);
    });
    tray.append(cell);
  });
}

function renderList() {
  const list = $("list");
  list.innerHTML = "";
  state.order.forEach((sid, index) => {
    const sticker = bySid(sid);
    const li = h("li", { class: "item" + (sticker.fits ? "" : " bad") });
    li.append(h("img", { src: sticker.preview_url, alt: "" }));

    const bits = [
      `${sticker.width}\u00d7${sticker.height}`,
      fmtBytes(sticker.size),
      sticker.animated ? `${sticker.frames} frames` : "static",
      `limit ${fmtBytes(sticker.max_bytes)}`,
    ];
    const name = h("div", { class: "item-name", title: sticker.source }, truncate(sticker.source, 46));
    li.append(h("div", { class: "item-meta" }, name, h("div", { class: "item-sub" }, bits.join(" \u00b7 "))));

    const cover = miniButton("\u2605", sticker.cover ? "Current cover" : "Set as cover", () => setCover(sid));
    cover.classList.toggle("active", sticker.cover);
    const up = miniButton("\u2191", "Move up", () => move(index, index - 1));
    const down = miniButton("\u2193", "Move down", () => move(index, index + 1));
    up.disabled = index === 0;
    down.disabled = index === state.order.length - 1;
    li.append(h("div", { class: "item-actions" }, cover, up, down));
    list.append(li);
  });
}

function miniButton(label, title, onClick) {
  return h("button", { class: "mini", type: "button", title, onclick: onClick }, label);
}

function move(from, to) {
  if (Number.isNaN(from) || from === to || to < 0 || to >= state.order.length) return;
  const order = state.order.slice();
  const [item] = order.splice(from, 1);
  order.splice(to, 0, item);
  state.order = order;
  renderTray();
  renderList();
  persistOrder();
}

function setCover(sid) {
  state.cover = sid;
  state.stickers.forEach((s) => {
    s.cover = s.sid === sid;
  });
  const url = `/api/pack/${state.packId}/cover.png?t=${Date.now()}`;
  $("coverImg").src = url;
  $("packIcon").src = url;
  renderTray();
  renderList();
  persistOrder();
}

async function persistOrder() {
  if (!state.packId) return;
  try {
    await fetch(`/api/pack/${state.packId}/order`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order: state.order, cover: state.cover }),
    });
  } catch (error) {
    setStatus("Could not sync order: " + error.message, "error");
  }
}

function sendSticker(sticker) {
  const chat = $("chat");
  const bubble = h("div", { class: "bubble out sticker-bubble" }, h("img", { src: sticker.preview_url, alt: sticker.source }));
  chat.append(bubble);
  bubble.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

document.addEventListener("DOMContentLoaded", () => {
  $("add").addEventListener("click", addSources);
  $("build").addEventListener("click", build);
  $("download").addEventListener("click", () => {
    if (state.packId) window.location = `/api/pack/${state.packId}/download`;
  });
  renderSources();
});
