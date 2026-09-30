import asyncio, base64, io, json, sys, unittest, zipfile
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'content/pyodide'))
from l1001_browser import read_source, split_chunks, sanitize, run, request_translation, arc_stream, ProviderFailure, settings_from

def upload(name,data): return {'name':name,'data':base64.b64encode(data).decode()}
def inputs(): return dict(file=upload('sample.txt',('Bonjour le monde. '*90).encode()),api_key='test-only-secret',provider='arc',model='gpt-oss-120b',source_language='French',target_language='English',chunk_chars=1000,annotations=True)
class Response:
    ok=True;status=200
    async def json(self):
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(translated_html='<p>[[ENTITY:E1]]Paris[[/ENTITY]] welcomes you.</p><script>alert(1)</script>',entities=[dict(marker_id='E1',canonical_name='Paris',display_text='Paris',category='place',aliases=[],note='A city in France.')],continuity_notes='Keep Paris.'))}}]}
class Reader:
    def __init__(self,response): self.response=response;self.parts=None
    async def read(self):
        if self.parts is None:
            data=await self.response.json()
            choice=data['choices'][0]
            events=[{'choices':[{'index':0,'delta':{'content':choice['message']['content']},'finish_reason':None}]}, {'choices':[{'index':0,'delta':{},'finish_reason':choice['finish_reason']}]}]
            raw=(''.join('data: '+json.dumps(e,ensure_ascii=False)+'\r\n\r\n' for e in events)+'data: [DONE]\r\n\r\n').encode()
            self.parts=[raw[i:i+7] for i in range(0,len(raw),7)]
        if not self.parts:return SimpleNamespace(done=True)
        value=self.parts.pop(0)
        return SimpleNamespace(done=False,value=SimpleNamespace(to_py=lambda:value))
    async def cancel(self): pass
    def releaseLock(self): pass
Response.js_response=property(lambda self:SimpleNamespace(body=SimpleNamespace(getReader=lambda:Reader(self))))

class Tests(unittest.IsolatedAsyncioTestCase):
    def test_files(self):
        self.assertEqual(read_source(upload('a.txt','مرحبا'.encode('utf-16'))),'مرحبا')
        self.assertEqual(read_source(upload('a.txt',b'Caf\xe9')),'Café')
        self.assertIn('café',read_source(upload('a.rtf',br"{\rtf1\ansi caf\'e9\par two}")))
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello 世界</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(read_source(upload('a.docx',data.getvalue())),'Hello 世界')
        with self.assertRaises(ValueError):read_source(upload('a.exe',b'bad'))
    def test_chunks_and_safety(self):
        source='世界'*3000+'\n\nBonjour. '*200
        chunks=split_chunks(source,1000)
        self.assertTrue(all(0<len(x)<=1000 for x in chunks))
        self.assertEqual(''.join(source.split()),''.join(''.join(chunks).split()))
        result=sanitize('<p onclick="evil()">ok<a href="javascript:evil()">x</a><img src="x" onerror="evil()"><svg><script>evil()</script></svg></p>')
        self.assertNotIn('onclick',result);self.assertNotIn('javascript:',result);self.assertNotIn('<script',result);self.assertNotIn('<img',result)
    async def test_resume_and_providers(self):
        events=[];requests=[]
        async def fetch(url,**kw): requests.append((url,json.loads(kw['body'])));return Response()
        data=inputs()
        await run(data,lambda p:events.append(json.loads(json.dumps(p))) if 'checkpoint' in p else None,fetch)
        self.assertNotIn('api_key',data)
        self.assertEqual(len(requests),2)
        state=events[-1]['checkpoint']
        self.assertEqual(len(state['entities']),1)
        self.assertNotIn('test-only-secret',json.dumps(events))
        self.assertNotIn('<script',events[-1]['html'])
        self.assertIn('endnote-1',events[-1]['html'])
        second=inputs();second['checkpoint']=events[1]['checkpoint'];requests.clear()
        await run(second,lambda p:None,fetch)
        self.assertEqual(len(requests),1)
        mismatch=inputs();mismatch['checkpoint']=state;mismatch['target_language']='German'
        with self.assertRaisesRegex(ValueError,'does not match'):await run(mismatch,lambda p:None,fetch)
        class OpenAIResponse(Response):
            async def json(self):return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(dict(translated_html='<p>Hello.</p>',entities=[],continuity_notes=''))}]}]}
        async def openai(url,**kw):
            self.assertEqual(url,'https://api.openai.com/v1/responses')
            body=json.loads(kw['body']);self.assertFalse(body['store']);self.assertEqual(body['text']['format']['type'],'json_schema');return OpenAIResponse()
        data=inputs();data['provider']='openai'
        await run(data,lambda p:None,openai)
    async def test_failure(self):
        class Unauthorized(Response):ok=False;status=401
        async def fetch(*a,**kw):return Unauthorized()
        data=inputs()
        with self.assertRaisesRegex(PermissionError,'401'):await run(data,lambda p:None,fetch)
        self.assertNotIn('api_key',data)
    async def test_stream_limits_and_interrupted_body(self):
        async def parts(events):
            for event in events: yield event
        with self.assertRaisesRegex(ProviderFailure,'output limit'):
            await arc_stream(None,parts(['data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\n','data: [DONE]\n\n']))
        with self.assertRaisesRegex(ProviderFailure,'ended early'):
            await arc_stream(None,parts(['data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n']))
    async def test_retry_reason_and_delay(self):
        class Busy(Response):
            ok=False;status=429;headers={'Retry-After':'12'}
            async def json(self):return {'error':{'code':'user_concurrent','message':'must not expose raw body'}}
        attempts=[];delays=[];notices=[]
        async def fetch(url,**kw):
            body=json.loads(kw['body']);self.assertTrue(body['stream']);self.assertEqual(body['max_tokens'],30000)
            attempts.append(body)
            return Busy() if len(attempts)==1 else Response()
        async def sleep(delay):delays.append(delay)
        result=await request_translation(settings_from(inputs()),'secret','source',fetch,sleep,notices.append)
        self.assertIn('translated_html',result)
        self.assertEqual(delays,[12]);self.assertIn('user_concurrent',notices[0]);self.assertNotIn('must not expose',notices[0])
    async def test_no_repeat_oversized_request(self):
        class TooLarge(Response):
            ok=False;status=413
            async def json(self):return {'error':{'code':'replica_capacity_exceeded'}}
        attempts=[]
        async def fetch(*a,**kw):attempts.append(1);return TooLarge()
        with self.assertRaisesRegex(ProviderFailure,'413.*replica_capacity_exceeded'):
            await request_translation(settings_from(inputs()),'secret','source',fetch)
        self.assertEqual(len(attempts),1)
    async def test_second_chunk_retry_retains_first(self):
        events=[];calls=[]
        class Busy(Response):
            ok=False;status=429
            async def json(self):return {'error':{'code':'queue_full'}}
        async def fetch(url,**kw):
            calls.append(kw)
            return Busy() if len(calls)==2 else Response()
        real=request_translation
        async def no_sleep(delay):pass
        async def fast(*a,**kw):return await real(*a,**kw,sleep=no_sleep)
        with patch('l1001_browser.request_translation',fast):
            await run(inputs(),lambda p:events.append(json.loads(json.dumps(p))),fetch)
        self.assertEqual(len(calls),3)
        self.assertEqual([e['completed'] for e in events if 'checkpoint' in e],[0,1,2])
        self.assertTrue(any('queue_full' in e.get('status','') for e in events))
if __name__=='__main__':unittest.main()
