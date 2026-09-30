(() => {
  'use strict';
  const $=id=>document.getElementById(id), channel='l1001-bridge-v1';
  const url=new URL(window.L1001_CONFIG.jupyterURL), frame=$('jupyter');
  let connected=false, active=null, handshake='', ping, slow, recovery, checkpoint=null, htmlURL=null, checkpointURL=null;
  const status=text=>{$('status').textContent=text;};
  const send=m=>frame.contentWindow.postMessage({channel,...m},url.origin);
  const modelDefaults={arc:'gpt-oss-120b',openai:'gpt-5.4-nano'};
  const hasUnfinishedCheckpoint=()=>!!checkpoint && checkpoint.fragments.length<checkpoint.total;
  function controls() {
    $('run').disabled=!connected || !!active;
    $('run').textContent=hasUnfinishedCheckpoint()?'Resume translation':'Translate';
    $('stop').hidden=!active;$('settings').disabled=!!active;$('advanced-settings').disabled=!!active;
    $('checkpoint').disabled=!!active;$('clear-checkpoint').disabled=!!active || !checkpoint;
  }
  function providerChanged(clear=true) {
    const arc=$('provider').value==='arc';
    if(clear){$('api-key').value='';$('model').value=modelDefaults[$('provider').value];}
    $('key-label').textContent=(arc?'ARC':'OpenAI')+' API key';
    $('api-key').placeholder='Enter your '+(arc?'ARC':'OpenAI')+' API key';
    $('key-help-open').hidden=!arc;
    $('provider-help').hidden=arc;
    $('models').replaceChildren(...(arc?['gpt-oss-120b','DeepSeek-V4-Flash','GLM-5.2','Kimi-K3']:['gpt-5.4-nano']).map(value=>{const o=document.createElement('option');o.value=value;return o;}));
  }
  $('provider').addEventListener('change',()=>providerChanged());providerChanged(false);
const keyHelpDialog = document.getElementById('key-help-dialog');
document.getElementById('key-help-open').addEventListener('click', event => {
  event.preventDefault();
  keyHelpDialog.showModal();
  document.body.classList.add('key-help-open');
});
document.getElementById('key-help-close').addEventListener('click', () => keyHelpDialog.close());
keyHelpDialog.addEventListener('click', event => {
  const bounds = keyHelpDialog.getBoundingClientRect();
  if (event.target === keyHelpDialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) keyHelpDialog.close();
});
keyHelpDialog.addEventListener('close', () => {
  document.body.classList.remove('key-help-open');
  document.getElementById('api-key').focus();
});
document.getElementById('key-help-image').addEventListener('error', () => {
  document.getElementById('key-help-error').hidden = false;
});

  const banner=$('banner');
  function showBanner(){if(banner.naturalWidth){banner.hidden=false;$('logo-fallback').hidden=true;}}
  banner.addEventListener('load',showBanner);showBanner();
  // Align the Translation heading with the provider/model labels on desktop.
  const resultsColumn=document.querySelector('.results');
  function alignTranslationPanel() {
    if(window.innerWidth<=750){resultsColumn.style.paddingTop='';return;}
    const current=parseFloat(getComputedStyle(resultsColumn).paddingTop)||0;
    const offset=document.querySelector('label[for="provider"]').getBoundingClientRect().top-$('results-title').getBoundingClientRect().top;
    resultsColumn.style.paddingTop=Math.max(0,current+offset)+'px';
  }
  const panelAlignment=new ResizeObserver(alignTranslationPanel);
  for(const element of [document.querySelector('.site-logo'),$('results-title'),$('result-summary')])panelAlignment.observe(element);
  window.addEventListener('resize',alignTranslationPanel);
  alignTranslationPanel();
  function blobLink(id,text,type,name,old) {
    if(old)URL.revokeObjectURL(old);
    const next=URL.createObjectURL(new Blob([text],{type}));
    $(id).href=next;$(id).download=name;$(id).setAttribute('aria-disabled','false');return next;
  }
  function setCheckpoint(value) {
    checkpoint=value;
    $('translation-progress').max=Math.max(1,value.total);
    $('translation-progress').value=value.fragments.length;
    checkpointURL=blobLink('download-checkpoint',JSON.stringify(value,null,2),'application/json','L1001-checkpoint.json',checkpointURL);controls();
  }
  function clearCheckpoint() {
    checkpoint=null;$('translation-progress').value=0;if(checkpointURL)URL.revokeObjectURL(checkpointURL);checkpointURL=null;
    $('download-checkpoint').removeAttribute('href');$('download-checkpoint').setAttribute('aria-disabled','true');
    $('checkpoint').value='';$('checkpoint-help').textContent='Choose the original source file and enter your API key to resume a checkpoint.';controls();
  }
  $('clear-checkpoint').addEventListener('click',()=>{clearCheckpoint();status('Checkpoint cleared. The next run starts a new translation.');});
  $('source').addEventListener('change',()=>{if(!$('title').value && $('source').files[0])$('title').value=$('source').files[0].name.replace(/\.[^.]+$/,'');});
  $('checkpoint').addEventListener('change',async()=>{
    try {
      const file=$('checkpoint').files[0];if(!file)return;
      if(file.size>30*1024*1024)throw Error('Checkpoint must be 30 MB or smaller.');
      const value=JSON.parse(await file.text()), s=value.settings;
      if(value.version!==1 || !s?.metadata || !Array.isArray(value.fragments) || !['arc','openai'].includes(s.provider) || !Number.isInteger(value.total) || value.fragments.length>value.total)throw Error('This is not an L1001 browser checkpoint.');
      $('provider').value=s.provider;providerChanged();$('model').value=s.model;
      for(const k of ['title','author','source_language','source_details','target_language','target_details']) $(k.replaceAll('_','-')).value=String(s.metadata[k] || '');
      $('style').value=s.style;$('chunk-chars').value=s.chunk_chars;$('annotations').checked=s.annotations;
      $('annotation-scope').value=s.annotation_scope;$('annotation-guidance').value=s.annotation_guidance;
      setCheckpoint(value);$('checkpoint-help').textContent=`Restored ${value.fragments.length} of ${value.total} chunks. Select the original file (${value.source_name}) and enter your API key.`;
      status('Checkpoint loaded. Settings restored; select the original source document.');
    } catch(e){status('Could not load checkpoint: '+e.message);$('checkpoint').value='';}
  });
  function connect() {
    if(active)return;
    clearInterval(ping);clearTimeout(slow);connected=false;controls();
    $('reconnect').hidden=true;handshake=crypto.randomUUID();status('Connecting to JupyterLite… The first load may take a minute.');
    url.searchParams.set('bridge',Date.now());frame.src=url.href;
    ping=setInterval(()=>send({type:'ping',id:handshake}),1500);
    slow=setTimeout(()=>{if(!connected){status('Still connecting. The L1001 GitHub Pages notebook and bridge must be deployed first. You can reconnect below.');$('reconnect').hidden=false;}},45000);
  }
  $('reconnect').addEventListener('click',connect);
  window.addEventListener('message',event=>{
    if(event.origin!==url.origin || event.source!==frame.contentWindow || event.data?.channel!==channel)return;
    const m=event.data;
    if(m.id===handshake && m.type==='connected') {connected=true;clearInterval(ping);clearTimeout(slow);$('reconnect').hidden=true;status('Ready.');controls();return;}
    if(!active || m.id!==active)return;
    if(m.type==='status')status(m.text);
    if(m.type==='stream'){$('log').textContent=($('log').textContent+m.text).slice(-100000);}
    if(m.type==='result') {
      const out=m.outputs;if(out.checkpoint)setCheckpoint(out.checkpoint);
      $('result-summary').textContent=`${out.completed} of ${out.total} chunks completed.`;
      if(out.html) {
        $('preview').srcdoc=out.html;
        const stem=($('title').value || 'translation').replace(/[^\p{L}\p{N}._-]+/gu,'-').slice(0,90);
        htmlURL=blobLink('download-html',out.html,'text/html;charset=utf-8',stem+(out.completed<out.total?'-partial':'')+'.html',htmlURL);
      }
    }
    if(m.type==='done' || m.type==='error') {
      clearTimeout(recovery);active=null;controls();
      status(m.type==='done'?'Ready.':'Error: '+m.error);
      if(m.type==='error'){$('log').textContent+='\n'+m.error;$('reconnect').hidden=false;}
    }
  });
  $('stop').addEventListener('click',()=>{
    if(!active)return;send({type:'stop',id:active});status('Stopping… Completed chunks remain available.');
    recovery=setTimeout(()=>{active=null;connected=false;frame.src='about:blank';controls();$('reconnect').hidden=false;status('Translation stopped. Download your checkpoint, then reconnect to resume.');},12000);
  });
  $('form').addEventListener('submit',async event=>{
    event.preventDefault();if(!connected || active)return;
    const file=$('source').files[0];
    if(!file || !/\.(txt|docx|rtf)$/i.test(file.name)){status('Choose a .txt, .docx, or .rtf file.');return;}
    if(file.size>10*1024*1024){status('Choose a file no larger than 10 MB.');return;}
    const id=crypto.randomUUID();active=id;controls();status('Reading your source document…');
    try {
      let binary='';for(const b of new Uint8Array(await file.arrayBuffer()))binary+=String.fromCharCode(b);
      if(active!==id)return;
      const inputs={file:{name:file.name,data:btoa(binary)},provider:$('provider').value,model:$('model').value.trim(),api_key:$('api-key').value.trim(),style:$('style').value,chunk_chars:Number($('chunk-chars').value),annotations:$('annotations').checked,annotation_scope:$('annotation-scope').value,annotation_guidance:$('annotation-guidance').value,checkpoint:hasUnfinishedCheckpoint()?checkpoint:null};
      for(const k of ['title','author','source_language','source_details','target_language','target_details'])inputs[k]=$(k.replaceAll('_','-')).value.trim();
      $('log').textContent='';
      if(!inputs.checkpoint){$('translation-progress').value=0;if(htmlURL)URL.revokeObjectURL(htmlURL);htmlURL=null;$('download-html').removeAttribute('href');$('download-html').setAttribute('aria-disabled','true');$('preview').srcdoc='';$('result-summary').textContent='Preparing translation…';}
      send({type:'run',id,inputs});inputs.api_key='';
    } catch(e){active=null;controls();status('Could not read the source document: '+e.message);}
  });
  window.addEventListener('beforeunload',e=>{if(active){e.preventDefault();e.returnValue='';}});
  window.addEventListener('pagehide',()=>{$('api-key').value='';if(active)send({type:'stop',id:active});});
  if(location.protocol==='file:'){status('Serve this folder over HTTP or upload it to l1001.vt.domains. Opening the file directly cannot connect to JupyterLite.');}else connect();
})();
