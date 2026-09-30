import asyncio, base64, io, json, sys, unittest, zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'content/pyodide'))
from l1001_browser import read_source, split_chunks, sanitize, run, request_translation

def upload(name,data): return {'name':name,'data':base64.b64encode(data).decode()}
def inputs(): return dict(file=upload('sample.txt',('Bonjour le monde. '*90).encode()),api_key='test-only-secret',provider='arc',model='gpt-oss-120b',source_language='French',target_language='English',chunk_chars=1000,annotations=True)
class Response:
    ok=True;status=200
    async def json(self):
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(translated_html='<p>[[ENTITY:E1]]Paris[[/ENTITY]] welcomes you.</p><script>alert(1)</script>',entities=[dict(marker_id='E1',canonical_name='Paris',display_text='Paris',category='place',aliases=[],note='A city in France.')],continuity_notes='Keep Paris.'))}}]}
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
if __name__=='__main__':unittest.main()
