// ==UserScript==
// @name         StickerForge Collector
// @namespace    https://github.com/hubailmn/stickerforge
// @version      1.0.1
// @description  Click GIFs on GIPHY/Tenor to stack their links. Alt+Shift+C copies the stack to the clipboard and clears it.
// @match        https://giphy.com/*
// @match        https://*.giphy.com/*
// @match        https://tenor.com/*
// @match        https://*.tenor.com/*
// @grant        GM_setClipboard
// @grant        GM_getValue
// @grant        GM_setValue
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const STACK_KEY = "sf_stack";
  const MAX_ITEMS = 120; // matches the web app source cap; packs are split on import
  // Only media from these hosts is collected.
  const MEDIA_HOST = /^(media\d*\.giphy\.com|i\.giphy\.com|media\d*\.tenor\.com|c\.tenor\.com)$/i;


  function loadStack() {
    try {
      const list = JSON.parse(GM_getValue(STACK_KEY, "[]"));
      return Array.isArray(list) ? list : [];
    } catch (_) {
      return [];
    }
  }

  function saveStack(list) {
    GM_setValue(STACK_KEY, JSON.stringify(list));
  }

  // GIPHY serves the same GIF under many renditions (200w.webp, giphy.mp4, ...).
  // Normalise to the full-size giphy.gif in the same folder so the link is stable.
  function normalise(url) {
    const parsed = new URL(url);
    if (!MEDIA_HOST.test(parsed.hostname)) return null;
    if (/giphy\.com$/i.test(parsed.hostname)) {
      const folder = parsed.pathname.replace(/\/[^/]*$/, "");
      if (folder.startsWith("/media")) {
        return `https://media.giphy.com${folder}/giphy.gif`;
      }
    }
    return `${parsed.origin}${parsed.pathname}`;
  }

  function mediaUrlFrom(element) {
    const candidates = [
      element.currentSrc,
      element.src,
      element.getAttribute("data-src"),
      element.querySelector && element.querySelector("source")?.src,
    ];
    for (const candidate of candidates) {
      if (!candidate) continue;
      try {
        const url = normalise(new URL(candidate, location.href).href);
        if (url) return url;
      } catch (_) {
        // not a usable URL; try the next candidate
      }
    }
    return null;
  }

  let toastTimer = null;
  function toast(message) {
    let node = document.getElementById("sf-toast");
    if (!node) {
      node = document.createElement("div");
      node.id = "sf-toast";
      document.body.append(node);
    }
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove("show"), 2200);
  }

  function renderBadge() {
    let badge = document.getElementById("sf-badge");
    if (!badge) {
      badge = document.createElement("div");
      badge.id = "sf-badge";
      badge.title = "Click to copy all links";
      badge.addEventListener("click", copyAndClear);
      document.body.append(badge);
    }
    const count = loadStack().length;
    badge.innerHTML = "";
    badge.append(`StickerForge: ${count}`);
    const hint = document.createElement("small");
    hint.textContent = "Alt+Shift+C copies & clears";
    badge.append(hint);
  }

  function addToStack(url) {
    const list = loadStack();
    if (list.includes(url)) {
      toast("Already in the stack");
      return;
    }
    if (list.length >= MAX_ITEMS) {
      toast(`Stack is full (${MAX_ITEMS}). Copy it first.`);
      return;
    }
    list.push(url);
    saveStack(list);
    renderBadge();
    toast(`Added (${list.length} in stack)`);
  }

  async function copyAndClear() {
    const list = loadStack();
    if (!list.length) {
      toast("Stack is empty");
      return;
    }
    const text = list.join("\n");
    try {
      GM_setClipboard(text, "text");
    } catch (_) {
      try {
        await navigator.clipboard.writeText(text);
      } catch (error) {
        // Keep the stack so nothing is lost when the copy fails.
        toast("Copy failed: " + error.message);
        return;
      }
    }
    saveStack([]);
    renderBadge();
    toast(`Copied ${list.length} link${list.length === 1 ? "" : "s"} - paste into StickerForge`);
  }

  // Capture phase, so the click is handled before the site's own handlers.
  document.addEventListener(
    "click",
    (event) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
        return; // modified clicks keep their normal behaviour (open in tab, etc.)
      }
      const target = event.target && event.target.closest ? event.target.closest("img, video") : null;
      if (!target) return;
      const url = mediaUrlFrom(target);
      if (!url) return;
      event.preventDefault();
      event.stopPropagation();
      addToStack(url);
    },
    true
  );

  // Alt+Shift+C copies & clears. Ignored while typing in a field.
  document.addEventListener("keydown", (event) => {
    const typing = /^(input|textarea|select)$/i.test(event.target.tagName) || event.target.isContentEditable;
    if (typing) return;
    if (event.altKey && event.shiftKey && event.code === "KeyC") {
      event.preventDefault();
      copyAndClear();
    }
    if (event.altKey && event.shiftKey && event.code === "KeyX") {
      event.preventDefault();
      saveStack([]);
      renderBadge();
      toast("Stack cleared");
    }
  });

  function injectStyles() {
    const style = document.createElement("style");
    style.textContent = `
    #sf-badge {
      position: fixed; right: 16px; bottom: 16px; z-index: 2147483647;
      background: #075e54; color: #fff; font: 600 13px/1.3 system-ui, sans-serif;
      padding: 9px 13px; border-radius: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.3);
      cursor: pointer; user-select: none;
    }
    #sf-badge small { display: block; font-weight: 400; opacity: .8; font-size: 11px; }
    #sf-toast {
      position: fixed; right: 16px; bottom: 74px; z-index: 2147483647;
      background: #111827; color: #fff; font: 13px/1.4 system-ui, sans-serif;
      padding: 8px 12px; border-radius: 10px; opacity: 0; transition: opacity .2s; pointer-events: none;
    }
    #sf-toast.show { opacity: 1; }
  `;
    document.head.append(style);
  }

  function start() {
    injectStyles();
    renderBadge();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
