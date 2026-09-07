// Browser-only camera replay. No robot commands and no additional camera requests.
const LOCAL_REPLAY_FPS = 30;
const LOCAL_REPLAY_BITRATE = 5000000;
function summarizeModelCommand(command) {
  if (!command || typeof command !== 'object') return 'Waiting';
  const label=String(command.type || 'command').replaceAll('_',' ').replace(/^./,char=>char.toUpperCase());
  if (command.type === 'joint_trajectory') {
    const parts=[label];
    if(Number.isFinite(command.waypoint_count)) parts.push(`${command.waypoint_count} waypoints`);
    if(Number.isFinite(command.cadence_hz)) parts.push(`${command.cadence_hz} Hz`);
    if(Number.isFinite(command.first_step_id)&&Number.isFinite(command.last_step_id)) parts.push(`steps ${command.first_step_id}–${command.last_step_id}`);
    return parts.join(' · ');
  }
  const arms=['left','right'].filter(name=>command[name]);
  return arms.length ? `${label} · ${arms.join(' + ')}` : label;
}
class YamLocalReplay {
  constructor(notify = () => {}) {
    this.notify = notify;
    this.active = null;
    this.records = new Map();
    this.seen = new Set();
    this.previous = null;
    this.supported = typeof MediaRecorder !== 'undefined' && typeof HTMLCanvasElement !== 'undefined' && !!HTMLCanvasElement.prototype.captureStream;
    this.db = null;
    try { if (typeof indexedDB !== 'undefined') {
      const request = indexedDB.open('yam-local-replays', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('replays', {keyPath:'id'});
      request.onsuccess = () => { this.db = request.result; };
      request.onerror = () => {}; // Private browsing/storage limits must not affect the runner.
    } } catch (_) {}
  }
  update(state) {
    const id = state.interactions?.run_id;
    const running = state.status === 'running';
    if (this.active && (!running || id !== this.active.id)) this.finish();
    if (running && id && !this.seen.has(id) && this.supported) {
      this.seen.add(id);
      this.start(id, !this.previous || this.previous.id !== id);
    }
    this.previous = {id, status:state.status};
  }
  start(id, late) {
    const canvas = document.createElement('canvas'); canvas.width=1440; canvas.height=294;
    const ctx = canvas.getContext('2d');
    const capture = {id, canvas, ctx, chunks:[], bytes:0, started:performance.now(), last:0, frames:0, partial:late, failed:false};
    capture.done = new Promise(resolve => { capture.resolve = resolve; });
    this.records.set(id, capture); this.active=capture;
    try {
      this.draw(capture);
      capture.stream = canvas.captureStream(LOCAL_REPLAY_FPS);
      const mimeType = ['video/webm;codecs=vp8','video/webm','video/mp4'].find(type => MediaRecorder.isTypeSupported(type));
      capture.recorder = new MediaRecorder(capture.stream, { ...(mimeType ? {mimeType} : {}), videoBitsPerSecond:LOCAL_REPLAY_BITRATE });
      capture.recorder.ondataavailable = event => {
        if (event.data.size) { capture.chunks.push(event.data); capture.bytes+=event.data.size; }
        if (capture.bytes > 128*1024*1024) { capture.partial=true; this.finish(capture); }
      };
      capture.recorder.onerror = () => { capture.failed=true; this.finish(capture); };
      capture.recorder.onstop = () => this.complete(capture);
      capture.recorder.start(1000);
      capture.timer = setInterval(() => {
        try { this.draw(capture); } catch (_) { capture.failed=true; this.finish(capture); }
        if (performance.now()-capture.started > 600000) { capture.partial=true; this.finish(capture); }
      },1000/LOCAL_REPLAY_FPS);
      this.notify('Recording local replay · keep this page open and visible.');
    } catch (_) { capture.failed=true; this.finish(capture); }
  }
  draw(capture) {
    const {ctx,canvas}=capture, now=performance.now();
    if (document.hidden || (capture.last && now-capture.last>1000)) capture.partial=true;
    capture.last=now;
    ctx.fillStyle='#10151b'; ctx.fillRect(0,0,canvas.width,canvas.height);
    ['left','top','right'].forEach((name,i) => {
      const img=document.querySelector(`#cameraGrid img[src="/api/monitor/cameras/${name}"]`);
      ctx.fillStyle='#ffffff'; ctx.font='14px sans-serif';
      ctx.fillText(`${name.toUpperCase()} · ${((now-capture.started)/1000).toFixed(1)}s`,i*480+10,17);
      if (img?.naturalWidth && !img.closest('.failed')) {
        const scale=Math.min(480/img.naturalWidth,270/img.naturalHeight);
        ctx.drawImage(img,i*480+(480-img.naturalWidth*scale)/2,24+(270-img.naturalHeight*scale)/2,img.naturalWidth*scale,img.naturalHeight*scale);
        capture.frames++;
      } else { capture.partial=true; ctx.fillText('Camera unavailable',i*480+10,155); }
    });
  }
  finish(capture=this.active) {
    if (!capture || capture.stopping) return;
    capture.stopping=true; capture.duration=(performance.now()-capture.started)/1000;
    clearInterval(capture.timer);
    if (this.active===capture) this.active=null;
    if (capture.recorder && capture.recorder.state!=='inactive') {
      try { capture.recorder.stop(); } catch (_) { capture.failed=true; this.complete(capture); }
    } else this.complete(capture);
  }
  complete(capture) {
    if(capture.completed) return;
    capture.completed=true;
    capture.stream?.getTracks().forEach(track=>track.stop());
    const blob = new Blob(capture.chunks, {type:capture.recorder?.mimeType || 'video/webm'});
    capture.chunks=[];
    const record = !capture.failed && capture.frames && blob.size ? {id:capture.id,blob,duration:capture.duration,partial:capture.partial,created:Date.now()} : null;
    if (record) {
      this.records.set(capture.id,record);
      // Bound memory and persistent storage to the three most recent local replays.
      const ready=[...this.records.values()].reverse().filter(value=>value.blob).sort((a,b)=>b.created-a.created);
      ready.slice(3).forEach(value=>this.records.delete(value.id));
      this.save(record);
    } else this.records.delete(capture.id);
    capture.resolve(record);
    if (!this.active) this.notify(record ? `Local replay ready${record.partial?' · partial capture':''}. Watch replay or Save video; archive upload continues separately.` : 'Local replay unavailable. The server archive will be used when ready.');
  }
  save(record) {
    try {
      if (!this.db) return;
      const tx=this.db.transaction('replays','readwrite'), store=tx.objectStore('replays');
      store.put(record);
      const request=store.getAll();
      request.onsuccess=()=>request.result.sort((a,b)=>b.created-a.created).slice(3).forEach(item=>store.delete(item.id));
      tx.onerror=()=>{};
    } catch (_) {} // In-memory replay still works when persistent storage is full.
  }
  async get(id) {
    const record=this.records.get(id);
    if (record?.blob) return record;
    if (record?.stopping) return record.done;
    if (record) return null;
    if (!this.db) return null;
    return new Promise(resolve => {
      try {
        const request=this.db.transaction('replays').objectStore('replays').get(id);
        request.onsuccess=()=>resolve(request.result || null); request.onerror=()=>resolve(null);
      } catch (_) { resolve(null); }
    });
  }
}
if (typeof module !== 'undefined') module.exports = {YamLocalReplay,LOCAL_REPLAY_FPS,LOCAL_REPLAY_BITRATE,summarizeModelCommand};

(() => {
  const baseRender = window.render;
  const byId = id => document.getElementById(id);
  const list = byId('interactionList'), selector = byId('interactionRun');
  let current = null, displayed = null, selected = '', lastRefresh = 0, refreshPending = false;
  const nodes = new Map();
  let autoSelected = false, selectionVersion = 0, commandDetailsOpen = false;
  function renderModelCommand(command) {
    const field=byId('command');
    if(!command) { field.textContent='Waiting'; return; }
    const summary=document.createElement('span'); summary.className='command-summary'; summary.textContent=summarizeModelCommand(command);
    const details=document.createElement('details'); details.className='command-details'; details.open=commandDetailsOpen;
    const toggle=document.createElement('summary'); toggle.textContent='Full command';
    const payload=document.createElement('pre'); payload.textContent=JSON.stringify(command,null,2);
    details.append(toggle,payload); details.ontoggle=()=>{commandDetailsOpen=details.open;};
    field.replaceChildren(summary,details);
  }
  const localStatus=document.createElement('p'); localStatus.id='localReplayStatus'; localStatus.setAttribute('role','status');
  localStatus.textContent='Local replay records the camera views during a run. Keep this page open and visible. The latest three local replays are kept in this browser when storage is available.';
  byId('interactionPath').before(localStatus);
  const localReplay=new YamLocalReplay(message=>{localStatus.textContent=message;});
  if(!localReplay.supported) localStatus.textContent='This browser cannot record local replay. Replay will use the uploaded server recording.';
  const help=byId('interactionPath').previousElementSibling?.previousElementSibling;
  if(help?.textContent.startsWith('Save log includes')) help.textContent='Save log includes model inputs, outputs, input images, and execution errors. Watch replay uses the local video as soon as recording stops, with the uploaded archive as a fallback. Save video downloads a copy.';

  async function get(path) {
    const response = await fetch(path, {cache:'no-store', headers:{'X-YAM-Runner-Token':CSRF}});
    if (!response.ok) throw new Error((await response.json()).error || 'Recording unavailable');
    return response;
  }

  function renderLog(log) {
    if ((displayed?.run_id || '') !== (log?.run_id || '')) { closeReplay(); nodes.clear(); list.replaceChildren(); }
    displayed = log;
    window.dispatchEvent(new CustomEvent("runner-recording-selected", {detail: log?.run_id || ""}));
    byId('interactionPath').textContent = log?.path ? `Saved locally: ${log.path}` : 'Interaction history will appear here.';
    if(log?.error) byId('interactionError').textContent = log.error;
    const events = log?.events || [], keys = new Set();
    for (const event of events) {
      const key = event.kind === 'packet_progress' ? `progress:${event.details.trajectory_id}` : String(event.id);
      keys.add(key);
      let row = nodes.get(key);
      if (!row) {
        row = document.createElement('li');
        const title = document.createElement('div'); title.className='interaction-title';
        const time = document.createElement('time'), text = document.createElement('span'); text.className='interaction-message';
        title.append(time,text);
        const details=document.createElement('details'), summary=document.createElement('summary'), pre=document.createElement('pre');
        summary.textContent='Details'; details.append(summary,pre); row.append(title,details); nodes.set(key,row);
      }
      row.className = /error|reject/.test(event.kind) ? 'interaction-failure' : '';
      row.querySelector('time').textContent = new Date(event.timestamp*1000).toLocaleTimeString();
      row.querySelector('.interaction-message').textContent = event.message;
      row.querySelector('pre').textContent = JSON.stringify(event.details || {},null,2);
    }
    for (const [key,row] of nodes) if (!keys.has(key)) { row.remove(); nodes.delete(key); }
    // Newest first. Existing details elements retain their expanded state.
    if (events.length) {
      for (const event of [...events].reverse()) {
        const key=event.kind==='packet_progress'?`progress:${event.details.trajectory_id}`:String(event.id);
        list.append(nodes.get(key));
      }
      for (const node of [...list.children]) if (!node.querySelector('.interaction-title')) node.remove();
    } else if (!nodes.size) {
      const empty=document.createElement('li'); empty.textContent='No interactions yet. Join the queue or choose a previous recording.'; list.replaceChildren(empty);
    }
    byId('wireDownload').disabled = !log?.run_id;
    byId('saveVideoDownload').disabled = !log?.run_id;
    byId('interactionDownload').disabled = !log?.run_id;
  }

  async function refreshRuns() {
    if (refreshPending || Date.now()-lastRefresh < 5000) return;
    refreshPending=true;
    try {
      const data=await (await get('/api/recordings')).json();
      selector.replaceChildren(new Option('Current run',''), ...data.runs.map(run => new Option(
        `${new Date(run.updated_at*1000).toLocaleString()} · ${run.run_id.slice(-8)}`, run.run_id)));
      if (!selected && !current?.interactions?.run_id && data.runs.length) {
        selected=data.runs[0].run_id; autoSelected=true;
        const runId=selected, version=++selectionVersion;
        const log=await (await get(`/api/recordings/${runId}/interactions`)).json();
        if (version===selectionVersion && selected===runId) {
          renderLog(log); byId('interactionStage').textContent='Latest saved run';
        }
      }
      selector.value=selected;
      lastRefresh=Date.now();
    } catch (error) { byId('interactionError').textContent=error.message; }
    finally { refreshPending=false; }
  }

  selector.onchange=async () => {
    selected=selector.value; autoSelected=false; const version=++selectionVersion; closeReplay();
    if (!selected) { if(current) window.render(current); return; }
    try { const log=await (await get(`/api/recordings/${selected}/interactions`)).json(); if(version===selectionVersion) { renderLog(log); byId('interactionStage').textContent='Saved run'; } }
    catch(error) { byId('interactionError').textContent=error.message; }
  };

  let downloading = false;
  async function download(video) {
    if (!displayed?.run_id || downloading) return;
    const runId=displayed.run_id; let name=video?'video.mp4':'log.zip';
    downloading=true;
    byId('interactionError').textContent=video?'Fetching saved left | top | right video…':'Preparing model and interaction log…';
    try {
      const local=video?await localReplay.get(runId):null;
      if(local) name=local.blob.type.includes('mp4')?'video.mp4':'video.webm';
      const blob=local?.blob || await (await get(`/api/recordings/${runId}/${name}`)).blob();
      const url=URL.createObjectURL(blob), link=document.createElement('a');
      link.href=url; link.download=`${runId.replace(/^(robocurve|run)_/, 'yam-run-')}-${name}`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),10000);
      byId('interactionError').textContent='';
    } catch(error) { byId('interactionError').textContent=error.message; }
    finally { downloading=false; }
  }
  const replay=document.createElement('section');
  replay.id='runReplay'; replay.className='run-replay'; replay.hidden=true;
  replay.innerHTML='<div class="replay-heading"><h3>Run replay · left | top | right</h3><button type="button" id="closeReplay">Close</button></div><video id="replayVideo" controls playsinline preload="auto" aria-label="Recorded run, left top and right cameras"></video><div class="replay-load" id="replayLoad" hidden><progress id="replayProgress" max="1"></progress><span id="replayProgressText">Preparing video…</span></div><div class="replay-status"><span id="replayStatus" role="status"></span><button type="button" id="retryReplay" hidden>Retry replay</button></div>';
  byId('interactionList').before(replay);
  const video=byId('replayVideo'), replayStatus=byId('replayStatus'), retry=byId('retryReplay');
  const replayLoad=byId('replayLoad'), replayProgress=byId('replayProgress'), replayProgressText=byId('replayProgressText');
  let replayVersion=0, replayTimer=null, localUrl=null, localPlaying=null;
  function resetReplayProgress(label='Preparing video…') {
    replayLoad.hidden=false; replayProgress.removeAttribute('value'); replayProgressText.textContent=label;
  }
  function updateReplayProgress() {
    if (!Number.isFinite(video.duration) || video.duration <= 0) return;
    let end=0;
    for(let i=0;i<video.buffered.length;i++) end=Math.max(end,video.buffered.end(i));
    const percent=Math.max(0,Math.min(1,end/video.duration));
    replayProgress.value=percent;
    replayProgressText.textContent=percent>=.995?'Video loaded':`Loading video · ${Math.round(percent*100)}% buffered`;
  }
  function closeReplay() {
    replayVersion++; clearTimeout(replayTimer);
    if (!video) return;
    video.pause(); video.removeAttribute('src'); video.load(); replay.hidden=true; replayLoad.hidden=true;
    if(localUrl) URL.revokeObjectURL(localUrl); localUrl=null; localPlaying=null;
  }
  function replayIssue(message) {
    clearTimeout(replayTimer); replayLoad.hidden=true; replayStatus.textContent=message; retry.hidden=false;
  }
  async function watchReplay(archiveOnly=false) {
    if (!displayed?.run_id) return;
    closeReplay(); const version=replayVersion, runId=displayed.run_id;
    replay.hidden=false; retry.hidden=true; replayStatus.textContent='Opening replay…'; resetReplayProgress();
    replay.scrollIntoView({behavior:'smooth', block:'center'});
    replayTimer=setTimeout(()=>replayIssue('Replay is taking longer than expected. Retry when the upload or connection is ready.'),20000);
    try {
      const local=archiveOnly===true?null:await localReplay.get(runId);
      if(version!==replayVersion || displayed?.run_id!==runId) return;
      if(local) {
        clearTimeout(replayTimer); localPlaying=local;
        localUrl=URL.createObjectURL(local.blob); video.src=localUrl; video.load();
        replayStatus.textContent=`Local replay · ${Math.round(local.duration)} seconds${local.partial?' · partial capture':''}`;
        video.play().catch(()=>{if(version===replayVersion) replayStatus.textContent+=' · Press Play';});
        return;
      }
      // Fetch only the small run-to-episode mapping. The video element streams
      // directly with byte ranges; no full-file Blob download before playback.
      const artifacts=await (await get(`/api/recordings/${runId}/artifacts`)).json();
      if(version!==replayVersion || displayed?.run_id!==runId) return;
      if(!artifacts.video_url) throw new Error('No replay was recorded for this older run.');
      const source=new URL(artifacts.video_url);
      if(source.origin!=='https://huggingface.co' || !source.pathname.startsWith('/datasets/andlyu/Public-YAM-runs/resolve/')) throw new Error('Unexpected replay source');
      source.searchParams.delete('download');
      source.searchParams.set('replay',String(Date.now()));
      video.src=source.href; video.load();
      replayStatus.textContent='Loading recorded video…';
      video.play().catch(()=>{if(version===replayVersion && !video.error) replayStatus.textContent='Press Play to watch the replay.';});
    } catch(error) { if(version===replayVersion) replayIssue(error.message); }
  }
  video.onprogress=updateReplayProgress;
  video.onloadedmetadata=()=>{retry.hidden=true;updateReplayProgress();replayStatus.textContent=localPlaying?`Local replay · ${Math.round(localPlaying.duration)} seconds${localPlaying.partial?' · partial capture':''}`:`Recorded run${Number.isFinite(video.duration)?' · '+Math.round(video.duration)+' seconds':''}`;};
  video.oncanplay=()=>{clearTimeout(replayTimer);retry.hidden=true;updateReplayProgress();};
  video.onplaying=()=>{clearTimeout(replayTimer);retry.hidden=true;replayLoad.hidden=true;replayStatus.textContent=localPlaying?`Local replay · ${Math.round(localPlaying.duration)} seconds${localPlaying.partial?' · partial capture':''}`:'Recorded run · left | top | right';};
  video.onwaiting=()=>{if(!replay.hidden){resetReplayProgress('Buffering replay…');updateReplayProgress();replayStatus.textContent='Buffering replay…';}};
  video.onstalled=()=>{if(!replay.hidden){resetReplayProgress('Waiting for video data…');updateReplayProgress();}};
  video.onerror=()=>{replayLoad.hidden=true;if(localPlaying) { watchReplay(true); return; } if(!replay.hidden && video.getAttribute('src')) replayIssue('Replay is not available yet or could not load. Retry after the run finishes uploading.');};
  retry.onclick=watchReplay; byId('closeReplay').onclick=closeReplay;
  byId('interactionDownload').textContent='Save log';
  byId('wireDownload').textContent='Watch replay';
  byId('wireDownload').title='Play the selected run here: left | top | right';
  byId('interactionDownload').onclick=()=>download(false);
  byId('wireDownload').onclick=watchReplay;
  const saveVideo=document.createElement('button'); saveVideo.id='saveVideoDownload';
  saveVideo.className='disconnect'; saveVideo.textContent='Save video'; saveVideo.onclick=()=>download(true);
  byId('wireDownload').after(saveVideo);
  if (!byId('datasetLink')) {
    const link=document.createElement('a');link.id='datasetLink';link.textContent='HF dataset ↗';
    link.href='https://huggingface.co/datasets/andlyu/Public-YAM-runs/tree/main';link.target='_blank';link.rel='noopener';
    byId('wireDownload').after(link);
  }

  byId('datasetLink').href='https://huggingface.co/datasets/andlyu/Public-YAM-runs/tree/main';

  window.render=state => {
    baseRender(state); renderModelCommand(state.last_model_command); current=state; localReplay.update(state);
    if(autoSelected && state.interactions?.run_id) { selected=''; autoSelected=false; selectionVersion++; selector.value=''; }
    if(!selected) {
      renderLog(state.interactions);
      const request=[...(state.interactions?.events||[])].reverse().find(e=>e.kind==='model_request');
      const trajectory=state.trajectory;
      const elapsed=request?Math.max(0,Math.floor(Date.now()/1000-request.timestamp)):0;
      byId('interactionStage').textContent=state.status==='running'&&state.provider?.vision?.state==='requesting'
        ?`Waiting for Astra · call ${state.provider.model_calls} · ${elapsed}s`
        :state.status==='running'&&trajectory&&trajectory.state!=='completed'
          ?`Executing ${trajectory.progress_count}/${trajectory.waypoint_count} at 10 Hz`
          :state.status==='stopped'&&state.error?'Stopped with error'
          :state.provider?.outcome?.status==='give_up'?'Stopped · Astra gave up'
          :state.provider?.outcome?.status==='done'?'Astra reported done'
          :state.status==='running'?'Preparing next motion':state.status;
    }
    refreshRuns();
  };
})();
