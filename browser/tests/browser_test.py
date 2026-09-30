import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).parent / "artifacts"
ROOT.mkdir(exist_ok=True)
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True)
  context=await browser.new_context(viewport={'width':1440,'height':1050},service_workers='block')
  await context.route('**/l1001-config.js',lambda r:r.fulfill(content_type='text/javascript',body="window.L1001_CONFIG={jupyterURL:'http://localhost:8000/lab/index.html'};"))
  calls=[]
  async def api(route):
   calls.append(route.request.post_data_json)
   payload=dict(translated_html='<p>[[ENTITY:E1]]Paris[[/ENTITY]] welcomes you.</p>',entities=[dict(marker_id='E1',canonical_name='Paris',display_text='Paris',category='place',aliases=[],note='A city in France.')],continuity_notes='Keep Paris.')
   if '/responses' in route.request.url:
    response={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(payload)}]}]}
   else:response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload)}}]}
   await route.fulfill(content_type='application/json',body=json.dumps(response),headers={'Access-Control-Allow-Origin':'*'})
  await context.route('https://llm-api.arc.vt.edu/**',api)
  await context.route('https://api.openai.com/**',api)
  page=await context.new_page()
  page.on('pageerror',lambda e:print('PAGE ERROR',e,flush=True))
  page.on('console',lambda m:print('CONSOLE',m.type,m.text[:250],flush=True) if m.type=='error' else None)
  await page.goto('http://localhost:8000/l1001.html')
  await page.screenshot(path=str(ROOT/'desktop-preview.png'),full_page=True)
  await page.wait_for_function("document.getElementById('run').disabled===false",timeout=180000)
  await page.locator('#api-key').fill('test-only-secret')
  await page.locator('#source-language').fill('French')
  await page.locator('#source').set_input_files({'name':'sample.txt','mimeType':'text/plain','buffer':b'Bonjour Paris.'})
  await page.get_by_text('Translation and annotation options',exact=True).click()
  await page.locator('#annotations').check()
  await page.locator('#run').click()
  try:
   await page.wait_for_function("/Translation complete|Error:/.test(document.getElementById('status').textContent)",timeout=240000)
  except Exception:
   print('STATUS',await page.locator('#status').inner_text(),flush=True);print('LOG',await page.locator('#log').inner_text(),flush=True);raise
  print('STATUS',await page.locator('#status').inner_text(),flush=True)
  print('LOG',await page.locator('#log').inner_text(),flush=True)
  assert 'Translation complete' in await page.locator('#status').inner_text()
  assert len(calls)==1
  async with page.expect_download() as result:await page.locator('#download-html').click()
  downloaded=await result.value
  await downloaded.save_as(ROOT/'tested-output.html')
  content=(ROOT/'tested-output.html').read_text()
  assert '<!doctype html>' in content and 'endnote-1' in content and 'test-only-secret' not in content
  await page.get_by_text('Resume a saved translation',exact=True).click()
  await page.locator('#clear-checkpoint').click()
  await page.locator('#provider').select_option('openai')
  assert await page.locator('#api-key').input_value()==''
  await page.locator('#api-key').fill('test-openai-secret')
  await page.locator('#run').click()
  await page.wait_for_function("/Translation complete|Error:/.test(document.getElementById('status').textContent)",timeout=120000)
  assert 'Translation complete' in await page.locator('#status').inner_text(), await page.locator('#status').inner_text()
  assert len(calls)==2 and calls[-1]['store']==False
  await page.frame_locator('#preview').locator('h1').wait_for(state='visible')
  assert 'Paris' in await page.frame_locator('#preview').locator('article').inner_text()
  await page.locator('#preview').scroll_into_view_if_needed()
  await page.frame_locator('#preview').locator('h1').screenshot(path=str(ROOT/'preview-heading.png'))
  await page.screenshot(path=str(ROOT/'desktop-preview.png'),full_page=True)
  await page.set_viewport_size({'width':390,'height':844})
  await page.screenshot(path=str(ROOT/'mobile-preview.png'),full_page=True)
  assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  print('PASS real JupyterLite/Pyodide: ARC and OpenAI mocked requests, HTML download, mobile layout',flush=True)
  await browser.close()
asyncio.run(main())
