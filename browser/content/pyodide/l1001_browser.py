"""Browser adapter: files, async provider requests, checkpoints, safe HTML."""
import asyncio, base64, hashlib, io, json, re, zipfile
from html import escape
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from l1001_core import (WorkMetadata, TextChunk, EntityRegistry, SYSTEM_INSTRUCTIONS,
    TRANSLATION_STYLE_OPTIONS, TRANSLATION_SCHEMA, build_chunk_prompt,
    parse_json_output, process_entity_markers, ENTITY_MARKER_RE,
    infer_html_lang, html_direction, localized_endnotes_heading)

MAX_FILE = 10 * 1024 * 1024
MAX_TEXT = 2_000_000

class SafeHTML(HTMLParser):
    tags = set('p h1 h2 h3 h4 h5 h6 blockquote ul ol li em strong br hr pre code table thead tbody tfoot tr th td a sup sub span section div dl dt dd'.split())
    void = {'br','hr'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack = [], []
    def handle_starttag(self, tag, attrs):
        if tag not in self.tags: return
        safe = []
        for name, value in attrs:
            value = value or ''
            if name == 'href' and tag == 'a' and re.match(r'^(https?://|mailto:|#[\w-]+$)',value,re.I):
                safe.append((name,value))
            elif name in {'id','class'} and re.fullmatch(r'[\w -]{1,100}',value): safe.append((name,value))
            elif name in {'aria-label','aria-labelledby','title','lang'}: safe.append((name,value))
            elif name == 'dir' and value in {'ltr','rtl','auto'}: safe.append((name,value))
            elif name in {'colspan','rowspan'} and value.isdigit() and 0<int(value)<100: safe.append((name,value))
        self.out.append('<'+tag+''.join(f' {k}="{escape(v,quote=True)}"' for k,v in safe)+'>')
        if tag not in self.void: self.stack.append(tag)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.void: self.handle_endtag(tag)
    def handle_endtag(self,tag):
        if tag not in self.stack: return
        while self.stack:
            current=self.stack.pop(); self.out.append(f'</{current}>')
            if current==tag: break
    def handle_data(self,data): self.out.append(escape(data))
    def result(self):
        while self.stack: self.out.append(f'</{self.stack.pop()}>')
        return ''.join(self.out)

def sanitize(value):
    parser=SafeHTML(); parser.feed(value); parser.close(); return parser.result()

def read_source(upload):
    try: data=base64.b64decode(upload['data'],validate=True)
    except Exception: raise ValueError('The uploaded file could not be read.') from None
    if len(data)>MAX_FILE: raise ValueError('Files must be 10 MB or smaller.')
    name=upload['name'].lower()
    if name.endswith('.docx'):
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entry=z.getinfo('word/document.xml')
            if entry.file_size>20*1024*1024: raise ValueError('The expanded DOCX document is too large.')
            xml=z.read(entry)
            if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml: raise ValueError('Unsupported DOCX XML.')
            root=ET.fromstring(xml)
        blocks=[]
        for p in root.findall('.//w:body//w:p',ns):
            parts=[]
            for run in p.findall('.//w:r',ns):
                text=''.join(n.text or '' if n.tag.endswith('}t') else '\t' if n.tag.endswith('}tab') else '\n' if n.tag.endswith('}br') else '' for n in run)
                if run.find('w:rPr/w:b',ns) is not None and text.strip(): text='**'+text+'**'
                if run.find('w:rPr/w:i',ns) is not None and text.strip(): text='*'+text+'*'
                parts.append(text)
            text=''.join(parts)
            style=p.find('w:pPr/w:pStyle',ns)
            if style is not None:
                match=re.search(r'heading\s*([1-6])',style.get('{'+ns['w']+'}val',''),re.I)
                if match: text='#'*int(match[1])+' '+text
            if text.strip(): blocks.append(text)
        text='\n\n'.join(blocks)
    elif name.endswith('.rtf'):
        from striprtf.striprtf import rtf_to_text
        if not data.lstrip().startswith(b'{\\rtf'): raise ValueError('This file is not an RTF document.')
        text=rtf_to_text(data.decode('latin-1'),errors='strict')
    elif name.endswith('.txt'):
        if data.startswith((b'\xff\xfe',b'\xfe\xff')): text=data.decode('utf-16')
        else:
            try: text=data.decode('utf-8-sig')
            except UnicodeDecodeError: text=data.decode('cp1252')
    else: raise ValueError('Choose a .txt, .docx, or .rtf file.')
    text=text.replace('\r\n','\n').replace('\r','\n').strip()
    if not text or '\x00' in text: raise ValueError('The file has no readable text or uses an unsupported encoding.')
    if len(text)>MAX_TEXT: raise ValueError('Please split texts longer than 2 million characters into separate files.')
    return text

def split_chunks(text,limit):
    # Character counts are explicit: no native tiktoken dependency in the browser.
    pieces=[]
    for block in re.split(r'\n[ \t]*\n+',text):
        remaining=block.strip()
        while len(remaining)>limit:
            region=remaining[:limit]
            matches=list(re.finditer(r'[.!?。！？]\s+|\n|\s+',region))
            end=matches[-1].end() if matches and matches[-1].end()>limit//2 else limit
            pieces.append(remaining[:end].strip()); remaining=remaining[end:].strip()
        if remaining: pieces.append(remaining)
    chunks=[]; current=''
    for piece in pieces:
        candidate=current+'\n\n'+piece if current else piece
        if len(candidate)>limit:
            chunks.append(current); current=piece
        else: current=candidate
    if current: chunks.append(current)
    return chunks

def settings_from(inputs):
    metadata={k:str(inputs.get(k,'')).strip()[:4000] for k in ('title','author','source_language','source_details','target_language','target_details')}
    if not metadata['source_language'] or not metadata['target_language']: raise ValueError('Enter both languages.')
    provider=inputs.get('provider')
    if provider not in {'arc','openai'}: raise ValueError('Choose ARC or OpenAI.')
    style=str(inputs.get('style','2'))
    if style not in TRANSLATION_STYLE_OPTIONS: raise ValueError('Choose a translation style.')
    size=int(inputs.get('chunk_chars',6000))
    if not 1000<=size<=16000: raise ValueError('Chunk size must be 1,000–16,000 characters.')
    model=str(inputs.get('model','')).strip()
    if not model or len(model)>150: raise ValueError('Enter a model ID.')
    return dict(metadata=metadata,provider=provider,model=model,style=style,chunk_chars=size,
        annotations=bool(inputs.get('annotations')),annotation_scope=str(inputs.get('annotation_scope','Named people, places, organizations, works, events, objects, and concepts'))[:2000],
        annotation_guidance=str(inputs.get('annotation_guidance','Use one to three concise sentences per annotation.'))[:2000])

def document(state):
    settings=state['settings']; meta=settings['metadata']; lang=infer_html_lang(meta['target_language'])
    partial=len(state['fragments'])<state['total']
    body='\n'.join(sanitize(f) for f in state['fragments'])
    notes=EntityRegistry(state['entities']).endnotes_html(localized_endnotes_heading(lang))
    notice=f'<p class="notice">Partial translation: {len(state["fragments"])} of {state["total"]} chunks completed.</p>' if partial else ''
    return f'''<!doctype html>
<html lang="{escape(lang,quote=True)}" dir="{html_direction(lang)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(meta['title'] or 'Translated work')}</title>
<style>body{{font:18px/1.7 Georgia,serif;color:#182a43;background:#fff;margin:0}}main{{max-width:48rem;margin:auto;padding:3rem 1.5rem}}header{{border-bottom:1px solid #ccd3df;padding-bottom:1rem;margin-bottom:2rem}}h1,h2,h3{{line-height:1.2}}.author{{font-style:italic}}.endnotes{{border-top:1px solid #ccd3df;margin-top:3rem;padding-top:1rem;font-size:.9em}}a{{color:#2459c4}}table{{border-collapse:collapse;max-width:100%}}td,th{{border:1px solid #ccd3df;padding:.4rem}}pre{{white-space:pre-wrap}}.notice{{background:#fff3ce;padding:1rem}}@media print{{main{{padding:0}}}}</style></head>
<body><main><header><h1>{escape(meta['title'] or 'Translated work')}</h1><p class="author">{escape(meta['author'])}</p>
<p>{escape(meta['source_language'])} → {escape(meta['target_language'])} · {escape(settings['model'])}</p></header>{notice}<article>{body}</article>{sanitize(notes)}</main></body></html>'''

async def request_translation(settings,key,prompt,fetch=None,sleep=asyncio.sleep):
    if fetch is None:
        from pyodide.http import pyfetch
        fetch=pyfetch
    instruction=SYSTEM_INSTRUCTIONS+'\n\nTRANSLATION STYLE\n'+TRANSLATION_STYLE_OPTIONS[settings['style']][1]
    instruction+= ('\nAnnotate only: '+settings['annotation_scope']+'. '+settings['annotation_guidance'] if settings['annotations'] else '\nAnnotations disabled. Do not use ENTITY markers. Return entities as an empty list.')
    if settings['provider']=='arc':
        url='https://llm-api.arc.vt.edu/api/v1/chat/completions'
        body=dict(model=settings['model'],messages=[dict(role='system',content=instruction),dict(role='user',content=prompt+'\nReturn only JSON conforming to this schema:\n'+json.dumps(TRANSLATION_SCHEMA))],max_tokens=8000)
    else:
        url='https://api.openai.com/v1/responses'
        body=dict(model=settings['model'],instructions=instruction,input=prompt,store=False,max_output_tokens=30000,
            text={'format':{'type':'json_schema','name':'translation_chunk','strict':True,'schema':TRANSLATION_SCHEMA}})
    for attempt in range(4):
        try:
            response=await asyncio.wait_for(fetch(url,method='POST',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},body=json.dumps(body)),timeout=600)
            if not response.ok:
                status=response.status
                if status in {408,409,429} or status>=500: raise RuntimeError(f'Temporary provider error (HTTP {status}).')
                raise PermissionError(f'Provider returned HTTP {status}. Check your key, model access, quota, and request settings.')
            data=await response.json()
            if settings['provider']=='arc':
                choice=data['choices'][0]
                if choice.get('finish_reason')!='stop': raise ValueError('The provider did not finish the translation. Try a smaller chunk size.')
                output=choice['message'].get('content') or ''
            else:
                if data.get('status')!='completed': raise ValueError('The provider did not complete the translation. Try a smaller chunk size.')
                output=''.join(c.get('text','') for item in data.get('output',[]) if item.get('type')=='message' for c in item.get('content',[]) if c.get('type')=='output_text')
            payload=parse_json_output(output)
            if any(not isinstance(e,dict) or not isinstance(e.get('aliases',[]),list) for e in payload['entities']): raise ValueError('Invalid entity annotations returned.')
            return payload
        except PermissionError:
            raise
        except (OSError,asyncio.TimeoutError):
            error='Browser request failed or timed out. Check your connection, ARC VPN, and provider CORS access.'
        except (ValueError,RuntimeError,KeyError,IndexError,TypeError) as exc:
            error=str(exc) if isinstance(exc,RuntimeError) else 'Provider returned incomplete or invalid translation data. Try a smaller chunk size.'
        if attempt<3:
            print(f'Request attempt {attempt+1} failed. Retrying…',flush=True)
            await sleep(2**(attempt+1))
    raise RuntimeError(error)

async def run(inputs,emit,fetch=None):
    key=inputs.pop('api_key','').strip()
    try:
        if not key: raise ValueError('Enter your API key.')
        settings=settings_from(inputs)
        text=read_source(inputs['file'])
        chunks=split_chunks(text,settings['chunk_chars'])
        fingerprint=hashlib.sha256((text+json.dumps(settings,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
        state=inputs.get('checkpoint')
        if state:
            if state.get('version')!=1 or state.get('fingerprint')!=fingerprint or state.get('settings')!=settings or state.get('total')!=len(chunks): raise ValueError('Checkpoint does not match this file and settings. Restore its settings or start a new translation.')
            if not isinstance(state.get('fragments'),list) or not all(isinstance(x,str) for x in state['fragments']) or len(state['fragments'])>len(chunks): raise ValueError('Invalid checkpoint fragments.')
            if not isinstance(state.get('entities'),list) or not isinstance(state.get('continuity_notes'),str): raise ValueError('Invalid checkpoint annotations.')
            for i,e in enumerate(state['entities']):
                if not isinstance(e,dict) or e.get('number')!=i+1 or not all(isinstance(e.get(k),str) for k in ('canonical_name','display_text','category','note')) or not isinstance(e.get('aliases'),list): raise ValueError('Invalid checkpoint entity.')
        else:
            state=dict(version=1,fingerprint=fingerprint,settings=settings,source_name=inputs['file']['name'],total=len(chunks),fragments=[],entities=[],continuity_notes='')
        registry=EntityRegistry(state['entities'])
        emit({'checkpoint':state,'html':document(state) if state['fragments'] else '', 'completed':len(state['fragments']),'total':len(chunks)})
        for i in range(len(state['fragments']),len(chunks)):
            emit({'status':f'Translating chunk {i+1} of {len(chunks)}…'})
            prompt=build_chunk_prompt(chunk=TextChunk(i,chunks[i],len(chunks[i])),chunk_count=len(chunks),metadata=WorkMetadata(**settings['metadata']),registry=registry,
                previous_source_tail=chunks[i-1][-2800:] if i else '',previous_target_tail=state['fragments'][-1][-2800:] if i else '',previous_continuity_notes=state['continuity_notes'])
            payload=await request_translation(settings,key,prompt,fetch=fetch)
            if settings['annotations']:
                fragment,warnings=process_entity_markers(payload['translated_html'],payload['entities'],registry)
                for warning in warnings: print(warning,flush=True)
            else: fragment=ENTITY_MARKER_RE.sub(r'\2',payload['translated_html'])
            fragment=sanitize(fragment)
            if not re.sub('<[^>]+>','',fragment).strip(): raise ValueError('The provider returned no readable translated text.')
            state['fragments'].append(fragment); state['entities']=registry.entries; state['continuity_notes']=payload['continuity_notes']
            emit({'checkpoint':state,'html':document(state),'completed':i+1,'total':len(chunks)})
        return {'completed':len(chunks),'total':len(chunks)}
    finally:
        key=''
        inputs.pop('api_key',None)
