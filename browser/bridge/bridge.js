/* L1001 bridge: fixed published notebook, private transient kernel, no saved inputs. */
(() => {
  'use strict';
  const CHANNEL = 'l1001-bridge-v1';
  const ORIGINS = new Set([location.origin, 'https://l1001.vt.domains', 'http://localhost:8000']);
  let active = null;
  const bounded = (promise, ms, message) => {
    let timer;
    return Promise.race([promise, new Promise((_,reject) => {timer=setTimeout(()=>reject(Error(message)),ms);})]).finally(()=>clearTimeout(timer));
  };
  async function appReady() {
    for(let i=0;i<180;i++) {
      if(window.jupyterapp) {
        await bounded(window.jupyterapp.restored,90000,'JupyterLite startup timed out.');
        await bounded(window.jupyterapp.serviceManager.ready,90000,'JupyterLite services timed out.');
        return window.jupyterapp;
      }
      await new Promise(r=>setTimeout(r,500));
    }
    throw Error('JupyterLite is unavailable. Reconnect and try again.');
  }
  window.addEventListener('message', async event => {
    if(event.source!==window.parent || window.parent===window || !ORIGINS.has(event.origin)) return;
    const m=event.data;
    if(m?.channel!==CHANNEL || typeof m.id!=='string') return;
    const send=(type,extra={})=>event.source.postMessage({channel:CHANNEL,id:m.id,type,...extra},event.origin);
    if(m.type==='ping') {
      try {await appReady();send('connected');} catch(e) {send('error',{error:e.message});}
      return;
    }
    if(m.type==='stop' && active?.id===m.id) {active.cancel();return;}
    if(m.type!=='run') return;
    if(active) {send('error',{error:'A translation is already running.'});return;}
    if(!m.inputs || typeof m.inputs!=='object' || JSON.stringify(m.inputs).length>45_000_000) {send('error',{error:'Invalid inputs or file too large.'});return;}
    let rejectCancel;
    const cancelled=new Promise((_,reject)=>{rejectCancel=reject;});cancelled.catch(()=>{});
    const run={id:m.id,session:null,stopped:false,cancel(){this.stopped=true;rejectCancel(Error('Stopped. Completed chunks are available to download or resume.'));}};
    active=run;
    const wait=(p,ms=120000)=>bounded(Promise.race([p,cancelled]),ms,'Python timed out. Download your checkpoint, then reconnect.');
    let kernel, failure=null;
    try {
      send('status',{text:'Loading the published L1001 notebook…'});
      const app=await wait(appReady());
      const response=await wait(fetch(new URL('../files/pyodide/L1001-browser.ipynb',location.href),{cache:'no-store'}));
      if(!response.ok) throw Error('Published notebook could not be loaded (HTTP '+response.status+').');
      const notebook=await response.json();
      if(!Array.isArray(notebook.cells)) throw Error('Published notebook is invalid.');
      const entries=Object.entries(app.serviceManager.kernelspecs.specs?.kernelspecs || {});
      const selected=entries.find(([name,s])=>s.language==='python' && /pyodide/i.test(name+' '+s.display_name));
      if(!selected) throw Error('The Pyodide kernel is not installed.');
      const starting=app.serviceManager.sessions.startNew({path:'l1001-session-'+crypto.randomUUID()+'.ipynb',name:'L1001 translation',type:'notebook',kernel:{name:selected[0]}});
      starting.then(s=>{if(run.stopped) s.shutdown().finally(()=>s.dispose());else run.session=s;},()=>{});
      const session=await wait(starting);kernel=session.kernel;
      if(!kernel) throw Error('Could not start Python.');
      await wait(kernel.info);
      async function execute(code,ms=120000) {
        const future=kernel.requestExecute({code,store_history:false,allow_stdin:false,stop_on_error:true});
        future.onIOPub=msg=>{
          if(run.stopped) return;
          const c=msg.content;
          if(msg.header.msg_type==='stream') send('stream',{text:c.text});
          const output=c.data?.['application/vnd.l1001+json'];
          if(output?.status) {send('status',{text:output.status});send('stream',{text:output.status+'\n'});}
          else if(output) send('result',{outputs:output});
        };
        try {const reply=await wait(future.done,ms);if(reply.content.status!=='ok') throw Error(reply.content.evalue || 'Notebook execution failed.');}
        finally {future.dispose();}
      }
      // Inputs only enter an unrecorded execution request, never notebook source or storage.
      let binary='';for(const b of new TextEncoder().encode(JSON.stringify(m.inputs))) binary+=String.fromCharCode(b);
      const encoded=btoa(binary);m.inputs.api_key='';binary='';
      const assetBase=new URL('../files/pyodide/',location.href).href;
      await execute(`import base64, json\ninputs=json.loads(base64.b64decode('${encoded}'))\nL1001_ASSET_BASE=${JSON.stringify(assetBase)}`);
      for(const cell of notebook.cells) {
        if(cell.cell_type!=='code') continue;
        const source=Array.isArray(cell.source)?cell.source.join(''):cell.source;
        if(source?.trim()) await execute(source,24*60*60*1000);
      }

    } catch(error) {
      failure=error.message || 'Translation failed.';
    } finally {
      run.stopped=true;
      if(run.session) {
        try {await bounded(run.session.shutdown(),10000,'Shutdown timed out.');} catch(_) {} finally {run.session.dispose();}
      }
      if(active===run) active=null;
      send(failure?'error':'done',failure?{error:failure}:{});
    }
  });
})();
