# L1001 browser edition

This adapts the MIT-licensed FL2744/L1001 application to the same architecture as
ARC Chat: the HTML on l1001.vt.domains embeds a GitHub Pages JupyterLite instance,
sends inputs through a checked postMessage bridge, and receives JSON results.
The published notebook runs in a temporary Pyodide kernel. No server-side Python,
PHP API proxy, API credentials in GitHub, or API credentials on the VT host are needed.

## Publish (when ready)

Merge `browser/` and `.github/workflows/l1001-browser.yml` into FL2744/L1001.
The existing desktop application stays in the repository root.
In repository Settings → Pages, choose **GitHub Actions** as the source.
Run **Deploy L1001 browser** or push these files to main.
The workflow builds https://fl2744.github.io/L1001/lab/index.html and serves the
notebook at /L1001/files/pyodide/L1001-browser.ipynb.

Upload the contents of `web/` to the public directory of l1001.vt.domains.
Open https://l1001.vt.domains/l1001.html. Your existing Desktop `L1001-banner.png` is included in the same directory.
A text logo is displayed if the image cannot be loaded.

The upload page will connect only after the GitHub Pages build is deployed.
No changes to the existing ARC Chat deployment or bridge are required.
The app executes the published notebook, ignoring stale browser-saved copies.
Python modules are fetched from that same deployment. Editing `L1001.py` alone
does not change the browser adapter; update the browser files as well.

## Files

- `web/l1001.html`, `l1001.css`, `l1001.js`, `l1001-config.js`: VT upload files.
- `content/pyodide/L1001-browser.ipynb`: browser entry notebook.
- `content/pyodide/l1001_core.py`: original styles, prompts, entity registry.
- `content/pyodide/l1001_browser.py`: browser file readers, async requests,
  chunking, checkpoints, and safe HTML assembly.
- `bridge/bridge.js`, `bridge/install.py`: post-build JupyterLite bridge.
- `requirements.txt`: JupyterLite build dependencies.
- `tests/test_translation.py`: offline adapter tests using simulated responses.

## Behavior and differences from the desktop version

- TXT: UTF-8, UTF-16 BOM, or Windows-1252. DOCX: main-body paragraphs, headings,
  bold, italic; tables flatten to paragraphs. RTF: text extraction via striprtf.
  Images, footnotes, comments, and complex document layout are not imported.
- 10 MB upload limit, 2 million extracted characters, 20 MB expanded DOCX XML.
- Paragraph-aware chunks use a 6,000-character default (configurable 1,000–16,000),
  rather than the desktop application's tiktoken count. Character counts do not
  predict a model's exact token budget. Reduce chunk size if output truncates.
- Original seven styles, context, continuity, optional global entity endnotes.
- ARC: https://llm-api.arc.vt.edu/api/v1/chat/completions, default gpt-oss-120b.
  The portal/key-management address is https://llm.arc.vt.edu.
- OpenAI: https://api.openai.com/v1/responses, default gpt-5.4-nano retained from
  L1001.py, JSON schema output and store:false. Model IDs are editable; access
  depends on the account. No reasoning-effort option is forced on custom models.
- Up to four request attempts on transient failures or malformed output. No retry
  on authentication/authorization errors. Each attempt can take up to ten minutes.
- Every completed chunk updates the preview, HTML download, and checkpoint.
  Download checkpoints explicitly before closing the tab. They contain translated
  text, settings, and continuity notes, but no API keys or original source file.
  Resume requires the original source and identical settings, checked by SHA-256.
  Checkpoints are specific to the browser version, not the desktop CLI.
- Stop shuts down this temporary kernel; completed chunks stay in the parent page.
  A request already received by a provider may still incur usage charges.
- Model HTML and imported checkpoint fragments use an allowlist sanitizer;
  preview is sandboxed, downloads include a restrictive content security policy.
- The interface does not persist inputs in localStorage, notebook cells, or browser
  databases. JupyterLite may cache runtime assets; input data lives in memory.
  The selected provider receives source chunks and context to translate them.

## Local build and verification

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt striprtf==0.0.29
.venv/bin/python tests/test_translation.py
.venv/bin/jupyter lite build --contents content --output-dir dist
.venv/bin/python bridge/install.py dist
cp web/* dist/
```

For a local integration test, edit only the generated `dist/l1001-config.js` to
point to `http://localhost:8000/lab/index.html`, serve `dist` on localhost:8000,
and open http://localhost:8000/l1001.html. Do not upload the test configuration.
Opening the HTML via file:// cannot connect through the origin-checked bridge.

The bridge accepts only its own origin, https://l1001.vt.domains, and
http://localhost:8000. A different production origin needs an explicit code edit.
It verifies the parent window and run ID and accepts only the fixed notebook.
GitHub Pages/JupyterLite and both APIs must be reachable from the browser.
API requests remain subject to provider CORS and ARC's network/VPN requirements.

## References

- Original source: https://github.com/FL2744/L1001
- Layout/framework: https://l1001.vt.domains/arc-chat.html
- JupyterLite embedding: https://jupyterlite.readthedocs.io/en/stable/howto/configure/advanced/iframe.html
- Browser HTTP: https://pyodide.org/en/stable/usage/api/python-api/http.html
- OpenAI request format: https://developers.openai.com/api/docs/guides/structured-outputs
- OpenAI Responses: https://developers.openai.com/api/docs/guides/migrate-to-responses

The shared ChatGPT conversation link supplied with the request could not be read;
the live ARC Chat page and its existing local bridge supplied the framework.

For the real Pyodide browser test, install playwright, run playwright install chromium,
serve dist on localhost:8000, then run tests/browser_test.py. It intercepts only
the config URL and provider requests; no real API key or paid request is used.
