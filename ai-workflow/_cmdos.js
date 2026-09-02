/* ============================================================
   命令样式开关 —— 一键把可复制的命令改写成 Windows 形式。

   为什么需要它:
     全书 23 页共 164 个可复制命令块,里面 72 处是 macOS/Linux 专用写法
     (`.venv/bin/python`、`&&`、`touch`、`which`…)。Windows 读者要么
     一路手动换算,要么回第 2 页翻对照表。第 2 页那张表仍是权威说明,
     这个开关只是省掉来回翻的功夫。

   为什么不是在页面里并排写两份:
     并排会让正文体量接近翻倍,违背「读起来不累」这条底线。

   为什么是外置文件而不是每页内联:
     同一段脚本内联 23 遍会让全书涨 10%;外置只需每页加一行 <script>。
     本目录已有 _depmap.js 这个先例。

   边界(重要):
     · 只改 <pre><code> —— 那是给你复制的命令。
     · 不碰 <pre> 纯文本块 —— 那些是「真实输出」,是 macOS 上的实测记录,
       改写它就成了伪造。全书 63 个纯文本块一个都不动。
     · 在**文本节点**上替换,所以块内的 <b> 高亮不会被破坏。
     · 原文存在内存里,切回 macOS/Linux 时逐字还原。
   ============================================================ */
(function () {
  var CSS =
    '.cmdos{display:flex;gap:2px;margin-left:8px}' +
    '.cmdos button{font:inherit;font-size:11.5px;padding:3px 9px;border:1px solid var(--rule);' +
    'background:var(--raised);color:var(--faint);cursor:pointer;border-radius:7px}' +
    '.cmdos button[aria-pressed="true"]{background:var(--accent);color:var(--paper);border-color:var(--accent)}' +
    '.oswarn{border:1px solid var(--warn);background:var(--raised);border-radius:11px;' +
    'padding:12px 16px;margin:0 0 22px;font-size:13.5px;color:var(--soft);line-height:1.7}' +
    '.oswarn b{color:var(--warn)}' +
    '@media(max-width:720px){.cmdos{margin-left:0}}';

  var WARN =
    '<b>已切到 Windows 写法。</b>本页可复制的命令已按第 ' +
    '<a class="pg" href="02-python-setup.html#venv">2</a> 页第二节那张对照表改写:' +
    '<code>.venv/bin/python</code>→<code>.venv\\Scripts\\python.exe</code>、' +
    '<code>python3</code>→<code>python</code>、<code>which</code>→<code>Get-Command</code>、' +
    '<code>touch</code>→<code>New-Item</code>、<code>mkdir -p</code>→<code>mkdir</code>、' +
    '<code>&amp;&amp;</code> 拆成两行。' +
    '<br><b>注意拆行后语义变了</b>:<code>&amp;&amp;</code> 是「前一条成功才跑下一条」,' +
    '而分两行是「不管成不成都跑」—— 想保留原语义,用 PowerShell 7 或 Git Bash。' +
    '<br>两类内容<b>不改写</b>:终端框里的「真实输出」(那是 macOS 上的实测记录);' +
    '以及写进文件的 shell 脚本(git 钩子、heredoc)—— Windows 上 Git 会用 Git Bash 跑它们,原样就是对的。';

  /* && 拆行,但要避开字符串里的 &&。
     例如 reason="pip install pytest-playwright && playwright install chromium"
     是一段 Python 字符串,拆了就把代码改坏了。 */
  function splitAnd(line) {
    var out = '', dq = 0, sq = 0;
    for (var k = 0; k < line.length; k++) {
      var c = line[k];
      if (c === '"') dq ^= 1;
      else if (c === "'") sq ^= 1;
      if (!dq && !sq && c === '&' && line[k + 1] === '&') {
        out = out.replace(/\s+$/, '') + '\n';
        k++;
        while (line[k + 1] === ' ') k++;
        continue;
      }
      out += c;
    }
    return out;
  }

  function toWin(t) {
    return t.split('\n').map(function (L) {
      /* bash 的 `VAR=值 命令` 前缀,PowerShell 要写成 `$env:VAR='值'; 命令` */
      L = L.replace(/^(\s*)([A-Z_][A-Z0-9_]*)=(\S+)(\s+)(?=[^\s=])/, "$1$env:$2='$3';$4")
           .replace(/\.venv\/bin\/python/g, '.venv\\Scripts\\python.exe')
           .replace(/(^|[\s(])python3(?=[\s)]|$)/g, '$1python')
           .replace(/(^|[\s(])which(?=\s)/g, '$1Get-Command')
           .replace(/(^|\s)touch(\s+)/g, '$1New-Item -ItemType File$2')
           .replace(/mkdir -p /g, 'mkdir ')
           .replace(/(^|\s)ls -a(?=\s|$)/g, '$1ls -Force');
      return splitAnd(L);
    }).join('\n');
  }

  function boot() {
    var items = [];
    [].forEach.call(document.querySelectorAll('pre > code'), function (c) {
      var w = document.createTreeWalker(c, NodeFilter.SHOW_TEXT, null), n, tn = [];
      while ((n = w.nextNode())) tn.push({ n: n, o: n.nodeValue });
      /* 写进文件的 shell 脚本(heredoc、shebang)整块不动:
         Windows 上 git 钩子由 Git Bash 执行,原样就是对的,
         改成 PowerShell 写法反而把能跑的东西改坏。 */
      if (/<<|#!\//.test(tn.map(function (x) { return x.o; }).join(''))) return;
      if (tn.some(function (x) { return toWin(x.o) !== x.o; })) items.push(tn);
    });
    if (!items.length) return;          /* 本页没有需要改写的命令,开关不出现 */

    var st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);

    var g = document.createElement('div');
    g.className = 'cmdos';
    g.setAttribute('role', 'group');
    g.setAttribute('aria-label', '命令样式');
    g.innerHTML = '<button type="button" data-os="nix" aria-pressed="true">macOS/Linux</button>' +
                  '<button type="button" data-os="win" aria-pressed="false">Windows</button>';
    var themer = document.querySelector('.themer');
    if (themer && themer.parentNode) themer.parentNode.insertBefore(g, themer.nextSibling);
    else return;

    var warn = document.createElement('p');
    warn.className = 'oswarn';
    warn.hidden = true;
    warn.innerHTML = WARN;
    var wrap = document.querySelector('.wrap');
    if (wrap) wrap.insertBefore(warn, wrap.firstChild);

    var bs = g.querySelectorAll('button');
    function set(os) {
      items.forEach(function (tn) {
        tn.forEach(function (x) { x.n.nodeValue = (os === 'win') ? toWin(x.o) : x.o; });
      });
      [].forEach.call(bs, function (b) {
        b.setAttribute('aria-pressed', String(b.dataset.os === os));
      });
      warn.hidden = (os !== 'win');
      try { localStorage.setItem('kb2-cmdos', os); } catch (e) {}
    }
    var s = null;
    try { s = localStorage.getItem('kb2-cmdos'); } catch (e) {}
    set(s === 'win' ? 'win' : 'nix');
    [].forEach.call(bs, function (b) {
      b.addEventListener('click', function () { set(b.dataset.os); });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
