/* ============================================================
   Claude Code 初学者知识库 — 共用脚本
   1) 主题切换：浅色 / 深色 / 跟随系统，默认深色，存 localStorage
   2) 代码块一键复制
   ============================================================ */
(function () {
  'use strict';

  /* ---------- 1. 主题 ---------- */
  var KEY = 'kb-theme';
  var mq = window.matchMedia('(prefers-color-scheme: dark)');

  // 读取用户偏好；没存过就用 'dark'（默认深色）
  function getPref() {
    try { return localStorage.getItem(KEY) || 'dark'; }
    catch (e) { return 'dark'; }   // 隐私模式下 localStorage 可能抛异常
  }

  // 把偏好换算成真正要用的主题，并写到 <html data-theme="...">
  function apply() {
    var pref = getPref();
    var real = (pref === 'system') ? (mq.matches ? 'dark' : 'light') : pref;
    document.documentElement.dataset.theme = real;
    var btns = document.querySelectorAll('[data-theme-set]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute('aria-pressed', String(btns[i].dataset.themeSet === pref));
    }
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('[data-theme-set]');
    if (!btn) return;
    try { localStorage.setItem(KEY, btn.dataset.themeSet); } catch (err) {}
    apply();
  });

  // 只有选了「跟随系统」时，系统换主题才需要跟着变
  if (mq.addEventListener) mq.addEventListener('change', apply);
  else if (mq.addListener) mq.addListener(apply);

  /* ---------- 2. 代码块复制 ---------- */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.codeblock .copy');
    if (!btn) return;
    var code = btn.closest('.codeblock').querySelector('code');
    if (!code) return;
    var text = code.innerText;
    var done = function () {
      var old = btn.textContent;
      btn.textContent = '已复制 ✓';
      setTimeout(function () { btn.textContent = old; }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else { fallback(); }

    // file:// 下部分浏览器禁用剪贴板 API，退回老办法
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(); }
      catch (err) { btn.textContent = '请手动复制'; }
      document.body.removeChild(ta);
    }
  });

  /* ---------- 3. 给每个代码块自动加复制按钮 ---------- */
  function addCopyButtons() {
    var blocks = document.querySelectorAll('.codeblock');
    for (var i = 0; i < blocks.length; i++) {
      if (blocks[i].querySelector('.copy')) continue;
      var b = document.createElement('button');
      b.className = 'copy';
      b.type = 'button';
      b.textContent = '复制';
      blocks[i].appendChild(b);
    }
  }

  apply();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', addCopyButtons);
  } else {
    addCopyButtons();
  }
})();
