#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""页面横向溢出探针：复制目标 HTML 为 _probeN.html，注入 JS 读 scrollWidth，headless chrome dump-dom 取出。
⚠️ 探针 HTML 写 %TEMP%，不删除（os.remove 在项目目录会撞批量删除防护）。
⚠️ 必须覆盖到 600px 一档（svg min-content 坑）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    "out", "etf_rotation_2026-09-28.html")
WIDTHS = [1500, 1366, 1100, 800, 600]
PROBE = os.path.join(tempfile.gettempdir(), "_probe_overflow.html")

JS = """
<script>
function run(){
  var d = document.documentElement;
  var out = ["PAGE=" + (d.scrollWidth - d.clientWidth)];
  var els = document.querySelectorAll("*");
  for (var i = 0; i < els.length && i < 400; i++) {
    var r = els[i].getBoundingClientRect();
    if (r.right > d.clientWidth + 1) {
      var id = els[i].id ? ("#" + els[i].id) : "";
      var cls = (els[i].className && els[i].className.baseVal === undefined)
        ? ("." + String(els[i].className).split(" ")[0]) : "";
      out.push("OVF " + els[i].tagName + id + cls + " right=" + Math.round(r.right));
    }
  }
  document.title = out.join(" ｜ ");
}
window.addEventListener("load", function(){ setTimeout(run, 250); });
</script>
"""

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
if not os.path.exists(CHROME):
    CHROME = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"


def probe(width):
    with open(SRC, "r", encoding="utf-8") as f:
        html_doc = f.read()
    html_doc = html_doc.replace("</body>", JS + "</body>")
    with open(PROBE, "w", encoding="utf-8") as f:
        f.write(html_doc)
    user_dir = os.path.join(tempfile.gettempdir(), f"_chrome_probe_{width}")
    cmd = [CHROME, "--headless", "--disable-gpu", "--no-sandbox",
           f"--user-data-dir={user_dir}", f"--window-size={width},1000",
           "--dump-dom", f"file:///{PROBE}"]
    for attempt in range(4):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=90,
                               encoding="utf-8", errors="replace")
            for line in r.stdout.split("\n"):
                if "<title>" in line:
                    return line.split("<title>")[1].split("</title>")[0].strip()
            if r.stdout:
                return "(no title)"
        except Exception:
            continue
    return "(chrome failed)"


def main():
    if not os.path.exists(SRC):
        print("missing", SRC)
        return 1
    bad = 0
    for w in WIDTHS:
        title = probe(w)
        # ⚠️ 2026-10-25 修：Chrome 起不来时 probe() 返回 "(chrome failed)"，
        # 而旧逻辑「title 里没有 PAGE= 就当 0」会把**失败静默报成 PASS**。
        # 现在必须先确认真拿到了 PAGE=，否则直接判 FAIL 并打红。
        if "PAGE=" not in (title or ""):
            bad += 1
            print(f"[{w:5d}px] PROBE_ERROR  FAIL   ← 浏览器没返回数据，本次结果不可用")
            print("    ", (title or "")[:200])
            continue
        page_ovf = title.split("PAGE=")[1].split(" ")[0].split("｜")[0].strip()
        ok = (page_ovf == "0")
        if not ok:
            bad += 1
        print(f"[{w:5d}px] PAGE_OVERFLOW={page_ovf:>6s} {'OK' if ok else 'FAIL'}")
        if not ok:
            print("   ", title[:400])
    print("RESULT:", "ALL PASS" if bad == 0 else f"{bad} WIDTHS FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
