"""定位 .csvg 曲线容器的 bounding box + 左右等高，输出到 title。"""
import os, shutil, subprocess, sys

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
HERE = os.path.dirname(os.path.abspath(__file__))

JS = (
    '<script>window.addEventListener("load",function(){'
    'var out=[];'
    'document.querySelectorAll(".csvg").forEach(function(el){'
    'var r=el.getBoundingClientRect();'
    'out.push("csvg|x="+Math.round(r.x)+"|y="+Math.round(r.y)+'
    '"|w="+Math.round(r.width)+"|h="+Math.round(r.height));});'
    'var l=document.querySelector(".split .l");var rg=document.querySelector(".split .r");'
    'if(l&&rg){out.push("LH="+Math.round(l.getBoundingClientRect().height)+'
    '"|RH="+Math.round(rg.getBoundingClientRect().height)+'
    '"|LW="+Math.round(l.getBoundingClientRect().width)+'
    '"|GTBLW="+(document.querySelector("table.gtbl")?Math.round(document.querySelector("table.gtbl").getBoundingClientRect().width):"-"));}'
    'var gt=document.querySelector("table.gtbl");'
    'if(gt){var lr=gt.getBoundingClientRect();out.push("gtbl|w="+Math.round(lr.width)+"|h="+Math.round(lr.height));}'
    'document.title="PROBE|"+out.join("|");});</script>'
)


def run(src, w):
    p = os.path.join(HERE, f"_probe_svg_{w}.html")
    shutil.copyfile(src, p)
    t = open(p, encoding="utf-8").read().replace("</body>", JS + "</body>")
    open(p, "w", encoding="utf-8").write(t)
    try:
        r = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox",
                            f"--window-size={w},900", "--dump-dom", p],
                           capture_output=True, text=True, timeout=60)
        for line in r.stdout.splitlines():
            if "<title>" in line:
                print(f"[{w}px] {line.strip()[:300]}")
                break
    finally:
        os.remove(p)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "out/_t_sina.html",
        int(sys.argv[2]) if len(sys.argv) > 2 else 1500)
