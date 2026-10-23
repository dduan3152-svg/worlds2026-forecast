"""Build a self-contained HTML snapshot that works directly from a file."""
from pathlib import Path
import argparse
import base64
import json

ROOT=Path(__file__).resolve().parents[1]


def build(root=ROOT, destination=None):
    site=root/'site'
    data=json.loads((site/'data.json').read_text(encoding='utf-8'))
    embedded=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    html=(site/'index.html').read_text(encoding='utf-8')
    css=(site/'style.css').read_text(encoding='utf-8')
    javascript=(site/'app.js').read_text(encoding='utf-8')
    if '</style' in css.lower() or '</script' in javascript.lower():
        raise ValueError('Unexpected closing tag in bundled assets')
    for required in ['<link rel="stylesheet" href="style.css">','<script src="app.js" defer></script>','</body>']:
        if html.count(required)!=1:raise ValueError('Website template changed; review static export')
    html=html.replace('<link rel="stylesheet" href="style.css">','<style>'+css+'</style>')
    html=html.replace('<script src="app.js" defer></script>','')
    report=base64.b64encode((root/'docs/worlds2026_report.pdf').read_bytes()).decode('ascii')
    html=html.replace('href="report.pdf" target="_blank" rel="noopener"','href="data:application/pdf;base64,'+report+'" download="worlds2026-report.pdf"')
    html=html.replace('阅读冻结版研究报告','下载冻结版研究报告')
    html=html.replace('<main>','<main><aside class="status-panel" style="margin-top:24px;margin-bottom:0">'
        '<strong>独立静态网页 · 离线快照</strong><p class="fine">页面、图表、数据和报告均已包含在本文件中，无需启动服务。'
        '本文件保存导出时的预测；云端有新结果后，请下载新版本。页面中的检查时间为导出时记录。</p></aside>')
    html=html.replace('刷新页面数据 ↻','恢复当前快照 ↻')
    scripts='<script type="application/json" id="forecast-data">'+embedded+'</script>'
    scripts+='<script>window.FORECAST_SNAPSHOT=JSON.parse(document.getElementById("forecast-data").textContent);</script>'
    scripts+='<script>'+javascript+'</script>'
    html=html.replace('</body>',scripts+'</body>')
    destination=destination or site/'forecast.html'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(html,encoding='utf-8')
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    print(build(destination=args.output))
