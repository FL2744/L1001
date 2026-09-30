"""Run after `jupyter lite build`: python bridge/install.py _output"""
import json
import re
import shutil
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else '_output')
index = root / 'lab/index.html'
html = index.read_text()
pattern = r'(<script\b[^>]*\bid="jupyter-config-data"[^>]*>)(.*?)(</script>)'
def configure(match):
    config = json.loads(match[2])
    config['exposeAppInBrowser'] = True
    return match[1] + json.dumps(config) + match[3]
html, count = re.subn(pattern, configure, html, count=1, flags=re.S)
if count != 1:
    raise SystemExit('Cannot locate JupyterLite page configuration; no changes written.')
tag = '<script src="./notebook-bridge.js"></script>'
if tag not in html:
    if '</body>' not in html:
        raise SystemExit('Cannot locate closing body; no changes written.')
    html = html.replace('</body>', tag + '\n</body>')
shutil.copyfile(Path(__file__).with_name('bridge.js'), root / 'lab/notebook-bridge.js')
index.write_text(html)
print(f'Installed notebook bridge in {index}')
