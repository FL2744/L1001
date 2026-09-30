const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 let listener,started=0,stopped=0,sent=[],requests=[];
 const parent={postMessage:m=>sent.push(m)};
 const kernel={info:Promise.resolve(),requestExecute:options=>{requests.push(options);return {done:Promise.resolve({content:{status:'ok'}}),dispose(){}};}};
 const app={restored:Promise.resolve(),serviceManager:{ready:Promise.resolve(),kernelspecs:{specs:{kernelspecs:{python:{language:'python',display_name:'Python (Pyodide)'}}}},sessions:{startNew:async()=>{started++;return {kernel,shutdown:async()=>stopped++,dispose(){}};}}}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/../bridge/bridge.js','utf8'),{window:{parent,jupyterapp:app,addEventListener:(n,f)=>listener=f},location:{origin:'https://fl2744.github.io',href:'https://fl2744.github.io/L1001/lab/index.html'},crypto:require('node:crypto').webcrypto,setTimeout,clearTimeout,TextEncoder,URL,btoa:s=>Buffer.from(s,'binary').toString('base64'),fetch:async()=>({ok:true,json:async()=>({cells:[{cell_type:'code',source:'outputs = {}'}]})})});
 const send=(origin,source=parent)=>listener({origin,source,data:{channel:'l1001-bridge-v1',type:'run',id:'test',inputs:{api_key:'test-key'}}});
 await send('https://evil.example');await send('https://l1001.vt.domains',{});assert.equal(started,0);
 await send('https://l1001.vt.domains');assert.equal(started,1);assert.equal(stopped,1);
 assert.equal(sent.at(-1).type,'done');assert(requests.every(x=>x.store_history===false && x.allow_stdin===false));
 assert(!JSON.stringify(sent).includes('test-key'));console.log('PASS bridge origin/source checks, published notebook, no history, session shutdown');
})().catch(e=>{console.error(e);process.exitCode=1;});
