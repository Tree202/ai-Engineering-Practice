/*!
 * 依赖关系地图 · 单源运行时生成 · 按需弹出
 * 用法:每页在 </body> 前加一行 <script src="_depmap.js" defer></script>
 * 当前页从文件名自动识别,不需要任何 per-page 配置。
 * 这里的依赖数据是全站唯一事实源(经人工裁定,见 references/deps.md)。
 */
(function () {
  "use strict";
  var RAW = "__DATA__";
  var PRE = {__PRES__};

  /* ---------- 解析节点表 ---------- */
  var N = {}, ORDER = [];
  RAW.split("\n").forEach(function (line) {
    var c = line.split("|");
    N[c[0]] = { p: c[0], file: c[1], label: c[2], x: +c[3], y: +c[4], w: +c[5],
                fill: c[6], stroke: c[7], sw: c[8], mod: c[9] };
    ORDER.push(c[0]);
  });
  Object.keys(PRE).forEach(function (k) { PRE[k] = PRE[k].match(/../g) || []; });
  ORDER.forEach(function (p) { if (!PRE[p]) PRE[p] = []; });

  /* ---------- 传递约简:算出主干边 ---------- */
  /* 一条边 p→c,若 p 已经是 c 的另一个前置的祖先,就说明有更长的路径能推出它,不必画 */
  var anc = {};
  function ancestors(p) {
    if (anc[p]) return anc[p];
    var s = {};
    anc[p] = s;
    PRE[p].forEach(function (q) {
      s[q] = 1;
      var qa = ancestors(q);
      for (var k in qa) { s[k] = 1; }
    });
    return s;
  }
  var BONE = {}, SUC = {};
  ORDER.forEach(function (c) {
    PRE[c].forEach(function (p) {
      var redundant = PRE[c].some(function (o) { return o !== p && ancestors(o)[p]; });
      if (!redundant) { BONE[p + ">" + c] = 1; }
      (SUC[p] = SUC[p] || []).push(c);
    });
  });

  /* ---------- 当前页:从文件名推断 ---------- */
  var cur = (location.pathname.match(/(\d{2})-[a-z0-9-]+\.html$/i) || [])[1] || "";
  if (!N[cur]) { cur = ""; }          /* 目录页等:没有当前页,只做总览 */

  /* ---------- 连线:父底 → 子顶;同一行则父右 → 子左 ---------- */
  function edge(a, b) {
    var A = N[a], B = N[b], ax = A.x + A.w / 2, bx = B.x + B.w / 2, m = 12;
    if (A.y === B.y) {                       /* 同一行:按左右关系决定从哪条边出发 */
      return A.x < B.x ? [A.x + A.w, A.y + 16, B.x, B.y + 16]
                       : [A.x, A.y + 16, B.x + B.w, B.y + 16];
    }
    function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }
    return [clamp(bx, A.x + m, A.x + A.w - m), A.y + 32,
            clamp(ax, B.x + m, B.x + B.w - m), B.y];
  }

  function svg() {
    var s = '<svg viewBox="0 0 900 528" role="list" aria-label="' +
      (cur ? "依赖关系图,当前是第 " + cur + " 页"
           : "全部 22 页的依赖关系图") + '">' +
      '<defs>' +
      '<marker id="dmA" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">' +
      '<polygon points="0 0, 8 3, 0 6" fill="var(--faint)"/></marker>' +
      '<marker id="dmB" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">' +
      '<polygon points="0 0, 9 3.5, 0 7" fill="var(--accent)"/></marker>' +
      '</defs>';
    var hot = [];      /* 与当前页直接相关的边,后画,压在上层 */

    /* 1) 主干边(常显,淡) */
    s += '<g fill="none" stroke="var(--faint)" stroke-width="1.2" opacity=".6" marker-end="url(#dmA)">';
    Object.keys(BONE).forEach(function (k) {
      var pc = k.split(">");
      if (cur && (pc[0] === cur || pc[1] === cur)) { hot.push([pc[0], pc[1], 1]); return; }
      var e = edge(pc[0], pc[1]);
      s += '<path d="M ' + e[0] + ' ' + e[1] + ' L ' + e[2] + ' ' + e[3] + '"/>';
    });
    s += "</g>";

    /* 2) 当前页的边:主干的画实线,跨层的画虚线;入边深、出边淡 */
    if (cur) {
      PRE[cur].forEach(function (p) { if (!BONE[p + ">" + cur]) { hot.push([p, cur, 0]); } });
      (SUC[cur] || []).forEach(function (c) { if (!BONE[cur + ">" + c]) { hot.push([cur, c, 0]); } });
      s += '<g fill="none" stroke="var(--accent)" stroke-width="2.2" marker-end="url(#dmB)">';
      hot.forEach(function (h) {
        var e = edge(h[0], h[1]), into = h[1] === cur;
        s += '<path d="M ' + e[0] + ' ' + e[1] + ' L ' + e[2] + ' ' + e[3] + '"' +
             (h[2] ? "" : ' stroke-dasharray="5 4"') + (into ? "" : ' opacity=".45"') + '/>';
      });
      s += "</g>";
    }

    /* 3) 节点:当前页不可点并标「你在这里」,其余都是链接 */
    s += '<g font-size="12" text-anchor="middle">';
    ORDER.forEach(function (p) {
      var n = N[p], on = p === cur, cx = n.x + n.w / 2;
      var fill = on ? "var(--accent)" : "var(--" + n.fill + ")",
          strk = on ? "var(--accent)" : "var(--" + n.stroke + ")",
          tcol = on ? "var(--paper)" : "var(--" + n.stroke + ")";
      s += (on ? '<g role="listitem" aria-current="page">'
               : '<a role="listitem" href="' + n.file + '.html" aria-label="第 ' + p +
                 ' 页 ' + n.label.slice(3) + '">') +
        '<rect x="' + n.x + '" y="' + n.y + '" width="' + n.w + '" height="32" rx="9" fill="' + fill +
        '" stroke="' + strk + '" stroke-width="' + (on ? 2.8 : n.sw) + '"/>' +
        '<text x="' + cx + '" y="' + (n.y + 21) + '" font-weight="' + (on ? 800 : 700) +
        '" font-size="' + (on ? 14 : 12) + '" fill="' + tcol + '">' + n.label + '</text>' +
        (on ? '<text x="' + cx + '" y="' + (n.y - 6) + '" font-size="10.5" font-weight="700" ' +
              'fill="var(--accent)">▼ 你在这里</text></g>' : '</a>');
    });
    return s + "</g></svg>";
  }

  var CSS = '#dm-ov{position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.62);display:none;' +
    'align-items:center;justify-content:center;padding:24px}#dm-ov.on{display:flex}' +
    '#dm-bx{background:var(--paper);border:1px solid var(--rule2);border-radius:14px;' +
    'padding:13px 18px 10px;max-width:980px;width:100%;max-height:92vh;overflow:auto;' +
    'box-shadow:0 18px 60px rgba(0,0,0,.45)}' +
    '#dm-hd{display:flex;align-items:baseline;gap:10px;margin-bottom:8px;flex-wrap:wrap}' +
    '#dm-hd b{font-size:15px}' +
    '#dm-hd i{font-style:normal;font-size:12.5px;color:var(--accent);font-weight:600}' +
    '#dm-hd s{text-decoration:none;font-size:12.5px;color:var(--faint);margin-right:auto}' +
    '#dm-x{cursor:pointer;background:transparent;border:1px solid var(--rule);color:var(--soft);' +
    'border-radius:7px;padding:3px 10px;font:inherit;font-size:12.5px}' +
    '#dm-x:hover{color:var(--ink);border-color:var(--rule2)}' +
    '#dm-sc{overflow-x:auto}#dm-bx svg{width:100%;height:auto;display:block}' +
    '#dm-bx a{cursor:pointer}#dm-bx a:hover rect{stroke-width:2.8}' +
    '#dm-bx a:focus-visible rect{stroke:var(--accent);stroke-width:2.8}' +
    '#dm-lg{font-size:12px;color:var(--faint);text-align:center;margin:9px 0 2px;line-height:1.7}' +
    '@media(max-width:700px){#dm-ov{padding:10px}#dm-bx{padding:10px 12px}#dm-bx svg{min-width:900px}}';

  function build() {
    var st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);

    var n = N[cur];
    var where = n
      ? '<i>你在这里:第 ' + cur + ' 页 / 共 23</i><s>' + n.mod + '</s>'
      : '<s>点任意方块跳过去</s>';
    var legend = n
      ? "蓝线 = 与本页直接相关(深 = 本页前置,淡 = 本页解锁) · " +
        "虚线 = 跨层依赖,不在主干上 · 灰线 = 主干"
      : "箭头方向 = 「先读这个」";

    var ov = document.createElement("div");
    ov.id = "dm-ov";
    ov.setAttribute("role", "dialog");
    ov.setAttribute("aria-modal", "true");
    ov.setAttribute("aria-label", "依赖关系图");
    ov.innerHTML = '<div id="dm-bx"><div id="dm-hd"><b>依赖关系图</b>' + where +
      '<button id="dm-x" type="button">关闭 Esc</button></div>' +
      '<div id="dm-sc">' + svg() + '</div><div id="dm-lg">' + legend + '</div></div>';
    document.body.appendChild(ov);

    var last = null;
    function open() {
      last = document.activeElement;
      ov.classList.add("on");
      document.getElementById("dm-x").focus();
    }
    function close() {
      ov.classList.remove("on");
      if (last && last.focus) { last.focus(); }
    }
    ov.addEventListener("click", function (e) { if (e.target === ov) { close(); } });
    document.getElementById("dm-x").onclick = close;
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && ov.classList.contains("on")) { close(); }
    });

    /* 修正 scroll-padding-top —— 导航栏是 sticky 的,点页内目录跳转时
       必须留出它的高度,否则标题会被压在导航栏底下。
       模板里写死的是一个固定值,但导航栏实测桌面 54.3px、375px 手机 100.9px
       (窄屏会折行),固定值两档都对不上。这里按实测高度动态设,任何宽度都准。 */
    var bar = document.querySelector(".nav");
    if (bar) {
      var fit = function () {
        document.documentElement.style.scrollPaddingTop = (bar.offsetHeight + 10) + "px";
      };
      fit();
      var timer;
      window.addEventListener("resize", function () {
        clearTimeout(timer);
        timer = setTimeout(fit, 120);
      });
    }

    /* 在导航栏注入入口 —— 不改任何页面的 HTML 结构 */
    var nav = document.querySelector(".nav-in");
    if (nav) {
      var b = document.createElement("a");
      b.textContent = "地图";
      b.href = "#";
      b.setAttribute("aria-haspopup", "dialog");
      b.title = "看你在全书结构中的位置";
      b.onclick = function (e) { e.preventDefault(); open(); };
      var t = nav.querySelector(".themer");
      if (t) { nav.insertBefore(b, t); } else { nav.appendChild(b); }
    }
    window.__depmap = { page: cur, nodes: ORDER.length, backbone: Object.keys(BONE).length, open: open };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
