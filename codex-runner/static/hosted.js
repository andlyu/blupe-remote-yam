function runComparisonBaseMetrics(run) {
  if (run.run_metrics) {
    const m = run.run_metrics;
    return {thinking: m.model_calls ? m.model_s : null, execution: m.execution_packets ? m.execution_s : null,
      distance: m.distance_waypoints ? (m.left_path_m + m.right_path_m) * 100 : null,
      legacy: false, partial: m.execution_packets !== m.accepted_packets || m.distance_waypoints !== m.confirmed_waypoints};
  }
  const rows = run.step_timings || [];
  const sum = key => rows.some(r => Number.isFinite(r[key])) ? rows.reduce((n,r) => n + (Number.isFinite(r[key]) ? r[key] : 0), 0) : null;
  const left = sum('left_displacement_m'), right = sum('right_displacement_m');
  return {thinking: sum('model_s'), execution: sum('arm_motion_s'), distance: left === null && right === null ? null : ((left || 0) + (right || 0))*100, legacy: true, partial: true};
}

function runComparisonMetrics(run) {
  const metrics = runComparisonBaseMetrics(run);
  const pairedLegacy = (run.step_timings || []).every(row => Number.isFinite(row.arm_motion_s) &&
    (Number.isFinite(row.left_displacement_m) || Number.isFinite(row.right_displacement_m)));
  metrics.speed = Number.isFinite(metrics.distance) && Number.isFinite(metrics.execution) && metrics.execution > 0 &&
    (metrics.legacy ? pairedLegacy : !metrics.partial) ? metrics.distance / metrics.execution : null;
  const rows = run.step_timings || [];
  const valid = value => Number.isFinite(value) && value >= 0;
  const mean = values => values.length ? values.reduce((a,b) => a+b,0)/values.length : null;
  const pathRecorded = rows.length > 0 && rows.every(row => valid(row.path_m));
  const robotTiming = rows.length > 0 && rows.every(row => valid(row.execution_robot_s));
  const distances = rows.map(row => pathRecorded ? row.path_m :
    (valid(row.left_displacement_m) || valid(row.right_displacement_m) ? (row.left_displacement_m || 0) + (row.right_displacement_m || 0) : null)).filter(valid);
  metrics.distance = distances.length ? mean(distances)*100 : null;
  metrics.thinking = mean(rows.map(row => row.model_s).filter(valid));
  metrics.execution = mean(rows.map(row => robotTiming ? row.execution_robot_s : row.arm_motion_s).filter(valid));
  metrics.steps = rows.length;
  metrics.legacy = !pathRecorded || !robotTiming;
  metrics.partial = rows.some(row => !valid(row.model_s)) || distances.length !== rows.length;
  metrics.samples = {distance:distances.length, thinking:rows.filter(row => valid(row.model_s)).length,
    execution:rows.filter(row => valid(robotTiming ? row.execution_robot_s : row.arm_motion_s)).length};
  return metrics;
}

function renderRunComparison(container, runs) {
  if (!container) return;
  const entries = [...new Map(runs.map(run => [run.episode_id, run])).values()]
    .sort((a,b) => (a.started_at || 0) - (b.started_at || 0)).slice(-20);
  container.replaceChildren();
  const note = document.createElement('p'); note.className = 'help';
  note.textContent = 'One column per run, oldest to newest (latest 20 locally recorded runs). Bars show averages per recorded completed step, excluding final-only model calls. Solid bars: trajectory and robot-clock measurements. Faded bars: older step measurements; distance is endpoint displacement and execution includes feedback. Compare matching measurement types and tasks. Movement speed = combined gripper distance / execution time, including settling and excluding thinking. Incomplete or zero-duration measurements have no speed value. Missing metrics are blank.';
  container.appendChild(note);
  if (!entries.length) return;
  const w = Math.max(650, entries.length * 65 + 80), slot = (w-80)/entries.length;
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="645" role="img" aria-label="Run-to-run comparison of trajectory distance, thinking time, execution time and movement speed"><style>text{font:12px system-ui;fill:currentColor}</style>`;
  const metrics = entries.map(runComparisonMetrics);
  for (const [index, key, label, color] of [[0,'distance','Average distance per step (cm)','#e0a126'],[1,'thinking','Average thinking per step (seconds)','#818cf8'],[2,'execution','Average execution per step (seconds)','#34d399'],[3,'speed','Movement speed (cm/s)','#38bdf8']]) {
    const top = index*155+25, base = top+105, max = Math.max(1,...metrics.map(m => Number.isFinite(m[key]) ? m[key] : 0));
    svg += `<text x="55" y="${top-8}">${label}</text>`;
    for (let tick=0; tick<=2; tick++) {
      const y=base-tick*45;
      svg += `<line x1="55" y1="${y}" x2="${w-15}" y2="${y}" stroke="currentColor" opacity=".15"/><text x="3" y="${y+4}">${(max*tick/2).toFixed(1)}</text>`;
    }
    metrics.forEach((m,i) => {
      const x=55+slot*(i+.5);
      if (Number.isFinite(m[key])) {
        const h=m[key]/max*90;
        svg += `<rect x="${x-15}" y="${base-h}" width="30" height="${h}" fill="${color}" opacity="${m.legacy ? .4 : 1}"><title>Run ${i+1}: ${m[key].toFixed(2)}${m.legacy ? ' (legacy)' : ''}${m.partial ? ' (partial)' : ''}${m.samples[key] !== undefined ? ' · '+m.samples[key]+' samples' : ''}</title></rect>`;
      }
      svg += `<text x="${x}" y="${base+17}" text-anchor="middle">${i+1}${m.legacy ? '*' : ''}</text>`;
    });
  }
  const plot=document.createElement('div'); plot.style.overflowX='auto'; plot.innerHTML=svg+'</svg>'; container.appendChild(plot);
  const list=document.createElement('ol');
  entries.forEach((run,i) => {
    const row=document.createElement('li'), m=metrics[i];
    const val=(v,unit)=>Number.isFinite(v)?v.toFixed(1)+unit:'unavailable';
    row.textContent = `${run.episode_index == null ? run.episode_id : 'Run #'+run.episode_index} · ${run.started_at ? new Date(run.started_at*1000).toLocaleString() : 'Time not recorded'} · ${run.result || 'Unknown'} · ${run.prompt || ''} — ${m.steps} recorded steps; average distance ${val(m.distance,' cm')}, average thinking ${val(m.thinking,' s')}, average execution ${val(m.execution,' s')}, movement speed ${val(m.speed,' cm/s')}${m.legacy ? ' [older step measurements]' : m.partial ? ' [partial]' : ''}`;
    const notes = document.createElement('p');
    notes.style.whiteSpace = 'pre-line';
    notes.textContent = 'Changes since previous run:\n' + (run.change_notes || ['Change notes were not recorded for this older run.']).join('\n');
    row.appendChild(notes);
    list.appendChild(row);
  });
  container.appendChild(list);
}

function renderRunMetrics(container, metrics, status) {
  if (!container) return;
  if (!metrics) { container.textContent = 'Run totals were not recorded.'; return; }
  const seconds = value => Number.isFinite(value) ? value.toFixed(2) + ' s' : 'Unavailable';
  const distanceKnown = metrics.distance_waypoints > 0;
  const distance = distanceKnown ? ((metrics.left_path_m + metrics.right_path_m) * 100).toFixed(1) + ' cm' : 'Unavailable';
  const partial = metrics.execution_packets !== metrics.accepted_packets || metrics.distance_waypoints !== metrics.confirmed_waypoints;
  container.textContent = `${['running', 'preparing'].includes(status) ? 'Run so far' : 'Run summary'}${partial ? ' (partial)' : ''}\nTrajectory distance: ${distance} (confirmed waypoint estimate, both arms combined)\nThinking: ${metrics.model_calls ? seconds(metrics.model_s) : 'No model requests recorded'}\nExecution: ${metrics.execution_packets ? seconds(metrics.execution_s) : 'No execution finish recorded'}\nThinking includes request overhead and retries. Execution uses robot timestamps and includes settling. Missing confirmations are excluded.`;
  container.style.whiteSpace = 'pre-line';
}

// Numeric-only chart shared by the current run and saved run history.
function renderStepMetricsChart(container, timings) {
  if (!container) return;
  const rows = (timings || []).filter(row => Number.isFinite(row.step)).slice(-200);
  if (!rows.length) {
    container.textContent = 'No step metrics recorded for this run yet.';
    return;
  }
  const valid = value => Number.isFinite(value) && value >= 0;
  const timeMax = Math.max(1, ...rows.flatMap(row => [row.model_s, row.arm_motion_s].filter(valid)));
  const distanceMax = Math.max(1, ...rows.flatMap(row => [row.left_displacement_m, row.right_displacement_m].filter(valid).map(v => v * 100)));
  const width = Math.max(640, rows.length * 36 + 70), left = 48, plotWidth = width - 64;
  const slot = plotWidth / rows.length, x = i => left + slot * (i + .5);
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="360" role="img" aria-label="Per-step model thinking time, arm-motion time and gripper displacement"><style>text{font:12px system-ui;fill:currentColor}</style>`;
  svg += '<text x="48" y="18">Time (seconds): model thinking / arm motion</text><text x="48" y="193">Gripper displacement (cm): left / right</text>';
  for (const [base, max] of [[145, timeMax], [320, distanceMax]]) {
    for (let i = 0; i <= 4; i++) {
      const y = base - i * 27;
      svg += `<line x1="${left}" y1="${y}" x2="${width-16}" y2="${y}" stroke="currentColor" opacity=".15"/><text x="4" y="${y+4}">${(max*i/4).toFixed(1)}</text>`;
    }
  }
  rows.forEach((row, i) => {
    for (const [key, offset, color, label] of [['model_s', -10, '#818cf8', 'Model thinking'], ['arm_motion_s', 1, '#34d399', 'Arm motion']]) {
      if (!valid(row[key])) continue;
      const height = row[key] / timeMax * 108;
      svg += `<rect x="${x(i)+offset}" y="${145-height}" width="9" height="${height}" fill="${color}"><title>Step ${row.step}: ${label} ${row[key].toFixed(2)} s</title></rect>`;
    }
    svg += `<text x="${x(i)}" y="163" text-anchor="middle">${row.step}</text><text x="${x(i)}" y="340" text-anchor="middle">${row.step}</text>`;
  });
  for (const [key, color, label] of [['left_displacement_m', '#fbbf24', 'Left'], ['right_displacement_m', '#38bdf8', 'Right']]) {
    let previous = null;
    rows.forEach((row, i) => {
      if (!valid(row[key])) { previous = null; return; }
      const point = [x(i), 320 - row[key] * 100 / distanceMax * 108];
      if (previous) svg += `<line x1="${previous[0]}" y1="${previous[1]}" x2="${point[0]}" y2="${point[1]}" stroke="${color}" stroke-width="2"/>`;
      svg += `<circle cx="${point[0]}" cy="${point[1]}" r="4" fill="${color}"><title>Step ${row.step}: ${label} ${(row[key]*100).toFixed(1)} cm</title></circle>`;
      previous = point;
    });
  }
  svg += '</svg>';
  container.style.overflowX = 'auto';
  container.innerHTML = '<p class="help"><span style="color:#818cf8">■ Model thinking</span> · <span style="color:#34d399">■ Arm motion</span> · <span style="color:#fbbf24">● Left distance</span> · <span style="color:#38bdf8">● Right distance</span><br>Horizontal axis: step number. Hover for values. Missing data is left blank.</p>' + svg;
}

function createRunLaunchGuard() {
  let version = 0, phase = 'idle', attemptId = null, conversation = null;
  return {
    get version() {return version;},
    start(run = null) {phase = 'pending';attemptId = null;conversation = run;return ++version;},
    accept(request, id, run = {}) {
      if(request !== version) return false;
      phase = 'accepted';attemptId = id;
      if(conversation) conversation = {...conversation,...run,attempt_id:id,reviewing_prompt:false};
      return true;
    },
    reject(request, error) {
      if(request !== version) return false;
      phase = 'rejected';
      if(conversation) conversation = {...conversation,status:'failed',error,reviewing_prompt:false};
      return true;
    },
    reset() {++version;phase = 'idle';attemptId = null;conversation = null;},
    conversation(run, state = {}) {
      if(!conversation) return run;
      if(phase !== 'accepted') return conversation;
      // A fresh owner status can precede its shared public_run. Never let the
      // previous run fill that gap, even when both prompts are identical.
      if(attemptId && run?.attempt_id === attemptId) {
        const route = state.provider?.launch_route || run.launch_route || conversation.launch_route;
        return {...conversation,...run,launch_route:route,
          model_name:route?.actual_policy === 'astra' ? 'Astra' : run.model_name || conversation.model_name,
          reviewing_prompt:false};
      }
      if(!attemptId || state.attempt_id !== attemptId) return conversation;
      const route = state.provider?.launch_route || conversation.launch_route;
      return {...conversation,attempt_id:attemptId,status:state.status,error:state.error,
        events:state.interactions?.events || [],task_progress:state.provider?.task_progress,
        launch_route:route,model_name:route?.actual_policy === 'astra' ? 'Astra' : conversation.model_name,
        reviewing_prompt:false};
    },
    allows(request, state) {
      return request === version && phase !== 'pending' &&
        !(phase === 'accepted' && attemptId && state.attempt_id !== attemptId);
    },
    project(state) {
      // A rejected POST has no new backend attempt. Keep its displayed error,
      // while continuing station/queue polling without resurrecting the old one.
      if(phase !== 'rejected' || ['queued','preparing','running'].includes(state.status)) return state;
      const ownPublic = state.public_run?.attempt_id && state.public_run.attempt_id === state.attempt_id;
      return {...state,error:null,safety_error:null,execution_blocked_reason:null,feedback_warning:null,
        subscription_setup:null,provider:null,public_run:ownPublic ? null : state.public_run};
    }
  };
}

(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('pastRunsList')) return;
  let next = 0, loading = false, historyRobot = null, historyGeneration = 0;
  const dialog = $('pastRunDialog'), player = $('pastRunVideo');
  const zeroSecondVideo = duration => typeof duration === 'number' && Number.isFinite(duration) && duration >= 0 && duration < 1;
  let selectedRun = null;
  let preview = null;
  const previewViews = [];
  function stopPreview() {
    if (preview) preview.pause();
    preview = null;
  }
  const speedButton = document.createElement('button');
  speedButton.type = 'button'; speedButton.textContent = 'Live speed';
  $('pastRunDetails').after(speedButton);
  const playbackSource = (run, live) => ({
    url: live ? (run.original_video_url || run.video_url.replace('/previews/', '/videos/').replace('/viewing/', '/videos/')) : run.video_url,
    rate: live || run.compressed ? 1 : 10
  });
  speedButton.addEventListener('click', () => {
    if (selectedRun) openRun(selectedRun, speedButton.dataset.live !== 'true');
  });
  function openRun(run, live = false) {
    if (zeroSecondVideo(run.video_duration_s) || (live && run.original_available === false)) return;
    stopPreview();
    $('pastRunTitle').textContent = `${run.runner_name || 'Name not recorded'} · ${run.episode_index == null ? 'Recent run' : 'Run #'+run.episode_index}`;
    $('pastRunPrompt').textContent = run.prompt;
    document.getElementById('aspirePastLineage')?.remove?.();
    const lineageBox = document.createElement('div'); lineageBox.id = 'aspirePastLineage';
    window.yamAspireLineage?.history(lineageBox, run); $('pastRunPrompt').after(lineageBox);
    selectedRun = run;
    speedButton.hidden = run.original_available === false;
    speedButton.dataset.live = String(live);
    speedButton.textContent = live ? '10× speed' : 'Live speed';
    const cameras = live ? 'Top, left and right cameras' : run.camera_order.includes('observer') ? 'Top, side, left and right cameras' : 'Three cameras · side view was not recorded';
    $('pastRunDetails').textContent = (live ? 'Live speed · 1× · all pauses retained' : run.compressed ? '10× speed · idle pauses removed' : '10× playback · original recording, pauses retained') + ' · ' + cameras + ' · ' + (run.result || 'Unknown') + ': ' + ((run.result === 'Failure' && run.errors) || run.result_reason || 'Result was not recorded');
    $('pastRunError').textContent = '';
    renderStepMetricsChart($('pastRunMetricsChart'), run.step_timings);
    renderRunMetrics($('pastRunMetricsSummary'), run.run_metrics, 'stopped');
    const source = playbackSource(run, live);
    player.pause();
    player.yamRecordingView?.();
    if (!live) player.yamRecordingView = window.yamRecordingView?.(player, run.camera_order);
    player.src = source.url;
    player.defaultPlaybackRate = player.playbackRate = source.rate;
    if (!dialog.open) dialog.showModal();
    player.play().catch(() => {});
  }
  $('closePastRun').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    player.pause(); player.yamRecordingView?.(); player.yamRecordingView = null;
    player.removeAttribute('src'); player.load();
  });
  dialog.addEventListener('click', event => { if (event.target === dialog) {
    const r = dialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
  }});
  player.addEventListener('error', () => { if (player.hasAttribute('src')) $('pastRunError').textContent = 'Video unavailable. Close this player and try again.'; });
  async function load(reset = false) {
    if (loading || !historyRobot) return;
    const generation = historyGeneration;
    loading = true; $('morePastRuns').disabled = $('refreshPastRuns').disabled = true;
    $('pastRunsStatus').textContent = 'Loading past runs…';
    try {
      const response = await fetch(`/api/past-runs?offset=${reset ? 0 : next}&robot_id=${encodeURIComponent(historyRobot)}`, {cache:'no-store', headers:{'X-Blupe-Robot':historyRobot}});
      if (!response.ok) throw new Error('Past runs are temporarily unavailable. Select Refresh to retry.');
      const data = await response.json();
      if (generation !== historyGeneration) return;
      if (reset) { stopPreview(); previewViews.splice(0).forEach(dispose => dispose()); $('pastRunsList').replaceChildren(); }
      for (const run of data.runs) {
        const li = document.createElement('li'), button = document.createElement('button');
        button.type = 'button'; button.className = 'pastRunCard';
        button.setAttribute('aria-label', `Watch run ${run.episode_index}: ${run.prompt}`);
        const video = document.createElement('video');
        video.preload = 'metadata'; video.muted = true; video.playsInline = true; video.loop = true;
        function startPreview() {
          if (dialog.open || zeroSecondVideo(run.video_duration_s) || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
          stopPreview();
          preview = video;
          video.defaultPlaybackRate = video.playbackRate = playbackSource(run, false).rate;
          video.play().catch(() => {});
        }
        button.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') startPreview(); });
        button.addEventListener('focus', () => { if (button.matches(':focus-visible')) startPreview(); });
        button.addEventListener('blur', () => { if (preview === video) stopPreview(); });
        button.addEventListener('pointerleave', () => { if (preview === video) stopPreview(); });
        video.addEventListener('loadedmetadata', () => {
          if (zeroSecondVideo(video.duration)) { showEmptyRun(); return; }
          if (Number.isFinite(video.duration) && video.duration > .2) video.currentTime = Math.min(1, video.duration/2);
        }, {once:true});
        video.tabIndex = -1; video.setAttribute('aria-hidden', 'true');
        const title = document.createElement('span'); title.className = 'pastRunLabel';
        const robotName = {'yam-1':'YAM', 'robot-3652c537a175cbae':'MakerMods SO101', 'robot-abecb4cd868ab24b':'SO101'}[run.robot_id || 'yam-1'] || run.robot_id;
        title.textContent = `${robotName} · ${run.runner_name || 'Name not recorded'} · ${run.episode_index == null ? 'Recent run' : '#'+run.episode_index} · ${new Date(run.started_at*1000).toLocaleString([], {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'})}`;
        const prompt = document.createElement('span'); prompt.className = 'pastRunPrompt'; prompt.textContent = run.prompt;
        const watch = document.createElement('span'); watch.className = 'pastRunLabel'; watch.textContent = '▶ Watch video';
        const outcome = document.createElement('span'); outcome.className = 'pastRunLabel';
        outcome.textContent = run.result || 'Unknown';
        const errors = document.createElement('span'); errors.className = 'pastRunLabel';
        errors.textContent = 'Errors: ' + (run.errors || 'None recorded');
        const ending = document.createElement('span'); ending.className = 'pastRunLabel';
        ending.textContent = 'Ending reason: ' + (run.ending_reason || run.result_reason || 'Not recorded');
        const reasonDetails = document.createElement('details');
        const reasonSummary = document.createElement('summary');
        reasonSummary.className = 'pastRunLabel';
        outcome.style.display = 'inline';
        outcome.style.padding = '0';
        const reasonLabel = document.createElement('span');
        reasonLabel.textContent = 'Show reason';
        reasonLabel.style.marginLeft = '12px';
        reasonLabel.style.textDecoration = 'underline';
        reasonSummary.append(outcome, reasonLabel);
        reasonSummary.style.cursor = 'pointer';
        reasonDetails.append(reasonSummary, ending);
        if (run.errors) reasonDetails.append(errors);
        button.append(title, prompt, video, watch); button.addEventListener('click', () => openRun(run)); li.append(button); window.yamAspireLineage?.history(li, run);
        const disposeView = window.yamRecordingView?.(video, run.camera_order);
        if (disposeView) previewViews.push(disposeView);
        const liveButton = document.createElement('button');
        liveButton.type = 'button';
        liveButton.disabled = run.original_available === false;
        liveButton.textContent = liveButton.disabled ? 'Live speed processing…' : 'Live speed';
        liveButton.setAttribute('aria-label', `Watch run ${run.episode_index} at live speed with pauses`);
        liveButton.addEventListener('click', () => openRun(run, true));
        li.append(reasonDetails, liveButton);
        $('pastRunsList').append(li);
        function showEmptyRun() {
          if (preview === video) stopPreview();
          run.video_duration_s = 0;
          liveButton.remove();
          const card = document.createElement('div'); card.className = 'pastRunCard';
          watch.textContent = 'Run lasted 0 time';
          card.append(title, prompt, watch);
          button.replaceWith(card);
          video.removeAttribute('src'); video.load();
        }
        if (zeroSecondVideo(run.video_duration_s)) showEmptyRun();
        else video.src = run.video_url;
      }
      next = data.next_offset;
      $('morePastRuns').hidden = next === null;
      $('pastRunsStatus').textContent = data.stale ? 'Showing saved results; the archive is temporarily unavailable.' : $('pastRunsList').children.length ? '' : 'No published runs yet.';
    } catch (error) { if (generation === historyGeneration) $('pastRunsStatus').textContent = error.message; }
    finally { if (generation === historyGeneration) { loading = false; $('morePastRuns').disabled = $('refreshPastRuns').disabled = false; } }
  }
  $('refreshPastRuns').addEventListener('click', () => load(true));
  $('morePastRuns').addEventListener('click', () => load());
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopPreview(); });
  window.addEventListener('blupe-robot-selected', event => {
    stopPreview();
    historyRobot = event.detail; historyGeneration++; loading = false; next = 0;
    const selectedName = $('robotSelector')?.selectedOptions?.[0]?.textContent ||
      {'yam-1':'YAM', 'robot-3652c537a175cbae':'MakerMods Bimanual SO101', 'robot-abecb4cd868ab24b':'SO101'}[historyRobot] || historyRobot;
    $('allEpisodesTitle').textContent = `All ${selectedName} episodes`;
    const visualizerRepo = {
      'yam-1': 'andlyu/Public-YAM-runs',
      'robot-3652c537a175cbae': 'andlyu/Public-MakerMods-SO101-runs'
    }[historyRobot];
    const viewer = $('datasetViewer'), link = $('datasetLink');
    viewer.hidden = !visualizerRepo;
    if (visualizerRepo) {
      viewer.src = `https://lerobot-visualize-dataset.hf.space/${visualizerRepo}/episode_0`;
      viewer.title = 'Recorded robot episodes on LeRobot visualizer';
      link.href = 'https://huggingface.co/spaces/lerobot/visualize_dataset?path=' + encodeURIComponent('/' + visualizerRepo + '/episode_0');
      link.textContent = 'Open episode visualizer';
    } else {
      viewer.removeAttribute('src');
      link.href = 'https://huggingface.co/datasets/andlyu/Public-YAM-runs';
      link.textContent = 'Episode visualizer not published yet · open raw archive';
    }

    if (dialog.open) dialog.close();
    $('pastRunsList').replaceChildren();
    load(true);
  });
})();
(() => {
  const container = document.getElementById('runComparison');
  if (!container) return;
  let robot = null, generation = 0;
  async function refresh() {
    if (!robot) return;
    const requestGeneration = ++generation;
    const status = document.getElementById('runComparisonStatus');
    try {
      const response = await fetch('/api/run-comparisons?robot_id=' + encodeURIComponent(robot), {cache:'no-store', headers:{'X-Blupe-Robot':robot}});
      if (!response.ok) throw new Error('Could not load local run metrics. Try Refresh plots.');
      const data = await response.json();
      if (requestGeneration !== generation) return;
      renderRunComparison(container, data.runs);
      status.textContent = data.runs.length ? `${data.runs.length} recorded run(s). Updated automatically after runs finish. Older runs without robot metadata are explicitly labelled.` : 'No completed run metrics recorded locally yet.';
    } catch (error) { if (requestGeneration === generation) status.textContent = error.message; }
  }
  document.getElementById('refreshRunComparison').addEventListener('click', refresh);
  window.addEventListener('blupe-robot-selected', event => { robot = event.detail; generation++; container.replaceChildren(); refresh(); });
  setInterval(() => { if (!document.hidden) refresh(); }, 15000);
})();


(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let claudeModel = 'claude-opus-5-5';
  let aspireRunAvailable = false;
  const originalProviderMarkup = $('provider').innerHTML;
  let selectedRobot = window.yamApplication?.defaultRobot || new URLSearchParams(location.search).get('robot_id') || 'yam-1', robotGeneration = 0, robotCatalog = null, pollingStarted = false;
  let ownRunLive = false, conversationSharingAllowed = true;
  let csrf = '', ended = false, submitting = false, active = false, lastHistory = 0, contactRequested = false;
  const runLaunchGuard = createRunLaunchGuard();
  window.addEventListener('blupe-robot-selected', () => runLaunchGuard.reset());
  function message(text, error = false) {
    for (const id of ['message', 'localRunMessage']) { $(id).textContent = text; $(id).classList.toggle('error', error); }
  }
  let contactDismissed = false;
  function operatorContact() {
    contactRequested = true;
    $('operator').setAttribute('aria-expanded', 'true');
    if ($('message').querySelector('a[href="tel:+17033443837"]')) return;
    const email = document.createElement('a');
    email.href = 'mailto:a.l.andlyu@gmail.com'; email.textContent = 'a.l.andlyu@gmail.com';
    const phone = document.createElement('a');
    phone.href = 'tel:+17033443837'; phone.textContent = '(703) 344-3837';
    $('message').classList.remove('error');
    $('message').replaceChildren(document.createTextNode('Feel free to reach out here: '), email, document.createTextNode(', '), phone);
  }
  function applicationContext() {
    return {$, api, message, buttons, updateSavedKey, providerChanged,
      setSubmitting(value) { submitting = value; }};
  }
  window.yamRecoverTask = async run_id => {
    if(active || submitting || ended || !csrf) throw new Error('Wait for the current task to finish.');
    submitting=true;buttons();
    try {
      await api('/api/aspire-recover',{run_id,automatic_retry_limit:aspireRetryLimit($('automaticRetryLimit').value)});
      runLaunchGuard.reset();window.yamAspireLineage?.reset();
      render(await api('/api/status'));
    } finally {submitting=false;buttons();}
  };
  async function api(path, data) {
    const generation = robotGeneration;
    const launchVersion = runLaunchGuard.version;
    const sessionToken = csrf;
    const headers = path === '/api/robots' ? {} : {'X-Blupe-Robot': selectedRobot};
    if (data !== undefined) { headers['Content-Type'] = 'application/json'; headers['X-YAM-Runner-Token'] = csrf; }
    const response = await fetch(path, {method: data === undefined ? 'GET' : 'POST', headers,
      body: data === undefined ? undefined : JSON.stringify(data), credentials: 'same-origin', cache: 'no-store'});
    const result = await response.json();
    if (generation !== robotGeneration ||
        (!['/api/session', '/api/robots'].includes(path) && sessionToken !== csrf)) {
      throw Object.assign(new Error('Robot session changed'), {stale:true});
    }
    if ((path === '/api/run' && launchVersion !== runLaunchGuard.version) ||
        (path === '/api/status' && (launchVersion !== runLaunchGuard.version ||
          (response.ok && !runLaunchGuard.allows(launchVersion, result))))) {
      throw Object.assign(new Error('Run attempt changed'), {stale:true});
    }
    if (!response.ok) {
      if (response.status === 401 && path !== '/api/chat') { ended = true; buttons(); $('apiKey').value = ''; }
      const error = new Error(result.error || 'Request failed');
      error.subscriptionSetup = result.subscription_setup;
      error.paymentConfirmed = result.payment_confirmed === true;
      throw error;
    }
    return path === '/api/status' ? runLaunchGuard.project(result) : result;
  }
  function buttons() {
    $('run').disabled = !csrf || ended || active || submitting;
    $('openLocalRun').disabled = !csrf || ended || active || submitting;
    $('runGroot').disabled = !csrf || ended || active || submitting;
    $('runAstra').disabled = $('runClaude').disabled = !csrf || ended || active || submitting;
    ['runDuration', 'runnerName', 'email', 'provider', 'model', 'prompt', 'apiKey'].forEach(id => { $(id).disabled = active || submitting || ended; });
    $('shareConversation').disabled = !conversationSharingAllowed || active || submitting || ended;
    $('useApiDepth').disabled = active || submitting || ended;
    $('automaticRetryLimit').disabled = active || submitting || ended;
    $('stop').disabled = !csrf || ended || !active;
    $('liveRunControls').hidden = !csrf || ended || !active || !ownRunLive;
    $('leaveQueue').disabled = !csrf || ended || !active;
    $('operator').disabled = false;
    $('forget').disabled = !csrf || ended;
    $('disconnect').disabled = !csrf || ended;
    updateRunLabel();
  }
  let savedKeyProviders = [];
  function runSetupNeeded() {
    return !$('runnerName').value.trim() ||
      (['openai', 'astra', 'anthropic'].includes($('provider').value) && !$('providerFields').hidden &&
       !savedKeyProviders.includes($('provider').value) && !$('apiKey').value.trim());
  }
  $('openCodexInstructions').onclick = () => {
    $('localRunDialog').close();
    $('copyCodexPrompt').dataset.copied = 'false';
    $('codexCopyStatus').textContent = '';
    $('codexInstructions').showModal();
  };
  // Tolerate a page loaded just before the new button was deployed.
  if ($('openSubscriptionInstructions')) $('openSubscriptionInstructions').onclick = $('openCodexInstructions').onclick;
  function runWithProvider(value, label) {
    if (active || submitting || ended) return;
    $('provider').value = value;
    providerChanged();
    applyModelName(lastLive);
    if (!$('runnerName').value.trim()) {
      $('runSettings').open = true; updateRunLabel(); $('runnerName').focus();
      message(`Enter your name, then press ${label} to join the queue.`); return;
    }
    window.yamAnalytics?.selected(value, $('model').value.trim());
    $('runForm').requestSubmit();
  }
  $('runGroot').onclick = () => runWithProvider('groot', 'Run GR00T');
  $('runAstra').onclick = () => runWithProvider('codex', 'Run with Astra');
  $('runClaude').onclick = () => runWithProvider('claude', 'Run with Opus');
  function openAspireTaskPanel(preview = true) {
    document.querySelector('[data-conversation-mode="reasoning"]')?.click();
    if(preview) window.yamAspireLineage?.preview();
  }
  let setupProvider = null;
  let shownSubscriptionFailure = '';
  function showSubscriptionSetup(setup) {
    setupProvider = setup.provider;
    $('subscriptionHelpTitle').textContent = setup.provider === 'claude' ? 'Caude is not setup' : `${setup.label} is not setup`;
    $('subscriptionHelpReason').textContent = ['missing', 'login_required'].includes(setup.state)
      ? `We couldn't find a ready ${setup.label} subscription connection on this computer. ${setup.message}`
      : setup.message;
    $('subscriptionSetupPrompt').value = setup.setup_prompt;
    $('subscriptionSetupStatus').textContent = setup.run_started
      ? 'This run has stopped. Reconnect your subscription before starting another.'
      : 'No robot run has been started.';
    if (!$('subscriptionHelp').open) $('subscriptionHelp').showModal();
  }
  $('closeSubscriptionHelp').addEventListener('click', () => $('subscriptionHelp').close());
  $('subscriptionHelp').addEventListener('close', () => { setupProvider = null; });
  $('copySubscriptionPrompt').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('subscriptionSetupPrompt').value);
      $('subscriptionSetupStatus').textContent = 'Copied. Paste the prompt into your coding assistant.';
    } catch (_) {
      $('subscriptionSetupPrompt').focus(); $('subscriptionSetupPrompt').select();
      $('subscriptionSetupStatus').textContent = 'Select and copy the setup prompt above.';
    }
  });
  $('recheckSubscription').addEventListener('click', async () => {
    if (!setupProvider) return;
    const provider = setupProvider;
    $('recheckSubscription').disabled = true;
    $('subscriptionSetupStatus').textContent = 'Verifying a small request through your subscription…';
    try {
      const setup = await api(`/api/${provider}/check`, {});
      if (provider !== setupProvider) return;
      if (setup.ready) {
        $('subscriptionHelp').close();
        $(`${provider}Status`).textContent = setup.message;
        message(`${setup.label} is connected. Click its Run button when you are ready. ${setup.availability_note}`);
      } else showSubscriptionSetup(setup);
    } catch (error) { if (!error.stale) $('subscriptionSetupStatus').textContent = error.message; }
    finally { $('recheckSubscription').disabled = false; }
  });
  async function copyCodexPrompt() {
    const prompt = document.querySelector('.codexPrompt');
    try {
      await navigator.clipboard.writeText(prompt.textContent.trim());
      $('copyCodexPrompt').dataset.copied = 'true';
      $('codexCopyStatus').textContent = 'Prompt copied.';
    } catch {
      const range = document.createRange();
      range.selectNodeContents(prompt);
      const selection = window.getSelection();
      selection.removeAllRanges(); selection.addRange(range);
      $('codexCopyStatus').textContent = 'Select the highlighted prompt and copy it manually.';
    }
  }
  $('copyCodexPrompt').onclick = copyCodexPrompt;
  function configureLocalRunDialog(local) {
    $('localRunDialog').dataset.local = String(local);
    $('runForm').classList.toggle('localRunMode', local);
    $('runForm').classList.add('runDialogMode');
    $('openLocalRun').hidden = false;
    if ($('openSubscriptionInstructions')) $('openSubscriptionInstructions').hidden = local;
    $('runnerIdentity').hidden = false;
    $('localRunDialog').close();
    $('localRunDialogBody').append($('runSettings'));
    $('runSettings').open = true;
    updateAspireSetupGuidance();
    updateRunLabel();
  }
  function updateAspireSetupGuidance() {
    const guidance = $('aspireSetupGuidance');
    if (guidance) guidance.hidden = !['aspire', 'hosted_aspire'].includes($('provider').value);
  }
  $('openLocalRun').onclick = () => {
    if (active || submitting || ended || !csrf) return;
    $('runSettings').open = true;
    updateAspireSetupGuidance();
    updateRunLabel();
    $('localRunMessage').textContent = '';
    $('localRunDialog').showModal();
    $('provider').focus();
  };
  $('closeLocalRun').onclick = () => $('localRunDialog').close();
  function updateRunLabel() {
    const runParent = $('runSettings').open ? $('setupRunActions') : document.querySelector('.promptRow');
    if ($('runButtons').parentElement !== runParent) runParent.append($('runButtons'));
    const firstAction = $('run');
    if ($('runButtons').firstElementChild !== firstAction) $('runButtons').prepend(firstAction);
    const modelAnchor = $('runSettings').open ? $('run') : $('openCodexInstructions');
    modelAnchor.after($('runAstra'), $('runClaude'), $('runGroot'));
    $('openCodexInstructions').textContent = $('runSettings').open
      ? 'Run locally through a subscription' : 'Run Locally through Subscription';
    $('runForm').classList.toggle('setupReady', !runSetupNeeded());
    $('runForm').classList.toggle('runActive', active || submitting);
    $('run').textContent = runSetupNeeded() && !$('runSettings').open
      ? 'Run online'
      : window.yamApplication?.runLabel?.() || 'Run';
  }
  $('run').addEventListener('click', event => {
    if (runSetupNeeded() && !$('runSettings').open) {
      event.preventDefault();
      $('runSettings').open = true;
      updateRunLabel();
      (!$('runnerName').value.trim() ? $('runnerName') : $('apiKey')).focus();
    }
  });
  $('runForm').addEventListener('invalid', () => {
    $('runSettings').open = true;
    updateRunLabel();
    if (!$('localRunDialog').open) $('localRunDialog').showModal();
  }, true);
  $('runSettings').addEventListener('toggle', updateRunLabel);
  $('runForm').addEventListener('input', updateRunLabel);
  $('provider').addEventListener('change', () => queueMicrotask(updateRunLabel));

  function updateSavedKey(providers) {
    if (Array.isArray(providers)) savedKeyProviders = providers;
    const saved = savedKeyProviders.includes($('provider').value);
    const keyed = ['openai', 'astra', 'anthropic'].includes($('provider').value);
    $('apiKey').required = keyed && !$('providerFields').hidden && !saved;
    $('apiKey').placeholder = saved ? 'Key saved for this session · enter a new key to replace it' : 'Paste a dedicated API key';
    updateRunLabel();
  }
  function providerChanged() {
    if ($('provider').value !== 'local_raise_lower' && $('prompt').value === 'Raise and lower both arms.') {
      $('prompt').value = $('prompt').dataset?.defaultPrompt || 'place green block on plate';
    }
    const aspireChoice = $('provider').value === 'aspire';
    $('aspireRetrySettings').hidden=!aspireChoice;
    const retry=$('automaticRetryLimit');
    if(retry.dataset.robot !== selectedRobot) {
      let saved='1';try {saved=localStorage.getItem('yam-aspire-retry-limit:'+selectedRobot) || '1';} catch (_) {}
      try {retry.value=String(aspireRetryLimit(saved));} catch (_) {retry.value='1';}
      retry.dataset.robot=selectedRobot;
    }
    $('apiDepthSettings').hidden = !$('useApiDepth').dataset?.available || $('provider').value !== 'codex' || aspireRunAvailable;
    if(aspireRunAvailable) $('useApiDepth').checked = aspireChoice;
    // Keep other providers' choices separate from ASPIRE's fixed inference settings.
    const inference = $('provider').dataset;
    if (aspireChoice) {
      if (inference.aspirePreviousEffort === undefined) {
        inference.aspirePreviousEffort = $('reasoningEffort').value;
        inference.aspirePreviousSpeed = $('responseSpeed').value;
      }
      $('reasoningEffort').value = 'high';
      $('responseSpeed').value = 'standard';
    } else if (inference?.aspirePreviousEffort !== undefined) {
      $('reasoningEffort').value = inference.aspirePreviousEffort;
      $('responseSpeed').value = inference.aspirePreviousSpeed;
      delete inference.aspirePreviousEffort;
      delete inference.aspirePreviousSpeed;
    }
    $('speedField').hidden = !['openai', 'codex', 'anthropic', 'claude'].includes($('provider').value);
    $('effortField').hidden = !['codex', 'claude'].includes($('provider').value);
    $('conversationSharingSettings').hidden = aspireChoice || $('localRunDialog').dataset?.local !== 'true';
    $('subscriptionHelp').close();
    setupProvider = null;
    if(aspireChoice) {
      $('providerFields').hidden = true;$('apiKey').required = false;$('apiKey').value = '';
      $('prompt').readOnly = false;$('model').value = 'gpt-6-astra';
      $('codexSetup').hidden = true;$('claudeSetup').hidden = true;$('forget').hidden = true;
      $('policyHelp').textContent = aspireRunAvailable
        ? 'ASPIRE reuses compatible saved programs without a coding request. Otherwise it generates Python and validates a complete offline native plan before queue/Home. Every run captures fresh cameras and fully plans before task motion.'
        : 'ASPIRE is unavailable for this robot. Choose a configured model or policy.';
      if(aspireRunAvailable) openAspireTaskPanel();
      updateRunLabel();return;
    }
    if ($('provider').value === 'groot') {
      $('providerFields').hidden = true; $('apiKey').required = false;
      $('apiKey').value = ''; $('model').value = 'groot-reviewed-step10000';
      $('policyHelp').textContent = 'GR00T warms up as soon as you join the queue. It runs when your turn and the GPU are ready.';
      updateRunLabel(); return;
    }
    $('prompt').readOnly = $('provider').value === 'local_raise_lower';
    const codex = $('provider').value === 'codex';
    const claude = $('provider').value === 'claude';
    $('codexSetup').hidden = !codex;
    $('claudeSetup').hidden = !claude;
    if (codex || claude) {
      $('providerFields').hidden = true;
      $('apiKey').value = ''; $('apiKey').required = false;
      $('forget').hidden = true;
      $('model').value = claude ? claudeModel : 'gpt-6-astra';
      $('policyHelp').textContent = claude
        ? `Opus runs through Claude Code on this computer using your Claude subscription (${claudeModel}). Join the same robot queue; no model API key is needed.`
        : 'Astra runs through Codex on this computer using your ChatGPT subscription. Join the same robot queue; no model API key is needed.';
      updateRunLabel();
      return;
    }
    $('forget').hidden = false;
    const anthropic = $('provider').value === 'anthropic';
    $('model').value = anthropic ? 'claude-opus-5-5' : $('provider').value === 'astra' ? 'astra-default' : 'gpt-6-astra';
    $('apiKeyLabel').textContent = anthropic ? 'Claude API key' : 'OpenAI API key';
    $('apiKeyCreditProvider').textContent = anthropic ? 'Anthropic' : 'OpenAI';
    $('apiKeyCreate').href = anthropic ? 'https://platform.claude.com/settings/keys' : 'https://platform.openai.com/api-keys';
    $('apiKeyBilling').href = anthropic ? 'https://platform.claude.com/settings/billing' : 'https://platform.openai.com/account/billing/overview';
    if (window.yamApplication?.providerChanged?.(applicationContext())) return;
    const builtIn = $('provider').value === 'local_raise_lower';
    $('providerFields').hidden = builtIn;
    $('apiKey').required = !builtIn; updateSavedKey();
    $('apiKey').value = '';
    if (builtIn) $('prompt').value = 'Raise and lower both arms.';
    else if (!$('prompt').value.trim()) $('prompt').value = $('prompt').dataset?.defaultPrompt || 'place green block on plate';
    $('policyHelp').textContent = builtIn ? 'The built-in policy performs three raise/lower cycles. No model calls or API key are needed.' : 'The model observes the cameras, chooses a move, and waits for robot feedback before deciding again.';
  }
  $('provider').addEventListener('change', () => { providerChanged(); updateAspireSetupGuidance(); applyModelName(lastLive); window.yamAnalytics?.selected($('provider').value, $('model').value.trim()); });
  $('automaticRetryLimit').addEventListener('change',()=>{
    try {const limit=aspireRetryLimit($('automaticRetryLimit').value);localStorage.setItem('yam-aspire-retry-limit:'+selectedRobot,String(limit));}
    catch(error) {if(error instanceof RangeError) message(error.message,true);}
  });
  $('codexCheck').addEventListener('click', async () => {
    $('codexCheck').disabled = true;
    try {
      const setup = await api('/api/codex/check', {});
      $('codexStatus').textContent = setup.message;
      if (!setup.ready) showSubscriptionSetup(setup);
    }
    catch (error) { $('codexStatus').textContent = error.message; }
    finally { $('codexCheck').disabled = false; }
  });
  $('claudeCheck').addEventListener('click', async () => {
    $('claudeCheck').disabled = true;
    try {
      const setup = await api('/api/claude/check', {});
      $('claudeStatus').textContent = setup.message;
      if (!setup.ready) showSubscriptionSetup(setup);
    }
    catch (error) { $('claudeStatus').textContent = error.message; }
    finally { $('claudeCheck').disabled = false; }
  });
  $('runForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (submitting || active || ended) return;
    if($('provider').value === 'aspire' && !aspireRunAvailable) {message('ASPIRE is unavailable for this robot.',true);return;}
    contactRequested = false;
    window.yamPolicyRoute?.(null);
    const aspireRequest = aspireRunAvailable && $('provider').value === 'aspire';
    let launchVersion = null, payload;
    try {
    const provisional = {task:$('prompt').value,runner_name:$('runnerName').value.trim(),
      model_name:selectedModelName(),status:'preparing',events:[],reviewing_prompt:true};
    launchVersion = runLaunchGuard.start(provisional);
    submitting = true;buttons();
    // Every actual submit switches the complete view before hooks or I/O.
    $('localRunDialog').close();$('subscriptionHelp').close();
    $('publicRunError').hidden = true;$('publicRunError').textContent = '';
    window.yamAspireLineage?.requested(provisional.task,launchVersion,aspireRequest);
    renderCurrentConversation(provisional,{});
    openAspireTaskPanel(false);
    guideRunAttention({status:'preparing',aspire:aspireRequest,new_submission:true});
    message('Reviewing your prompt…');
    // Give the browser its first paint before potentially expensive hooks.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    if(launchVersion !== runLaunchGuard.version) return;
    if (await window.yamApplication?.submit?.(applicationContext())) {
      if(launchVersion !== runLaunchGuard.version) return;
      runLaunchGuard.reset();window.yamAspireLineage?.reset();guideRunAttention({status:'idle'});
      submitting=false;buttons();
      return;
    }
    if(launchVersion !== runLaunchGuard.version) return;
    window.yamAnalytics?.requested($('provider').value, $('model').value.trim());
    payload = {runner_name: $('runnerName').value.trim(), provider: $('provider').value === 'aspire' ? 'codex' : $('provider').value, model: $('model').value.trim(),
      email: $('email').value.trim(), share_conversation: $('shareConversation').checked,
      prompt: $('prompt').value, run_duration_s:Number($('runDuration').value)*60, api_key: $('apiKey').value.trim()};
    if ($('provider').value === 'aspire') {payload.use_api_depth = true;payload.automatic_retry_limit=aspireRetryLimit($('automaticRetryLimit').value);}
    else if(aspireRunAvailable && payload.provider === 'codex') payload.use_api_depth = false;
    else if (!$('apiDepthSettings').hidden) payload.use_api_depth = $('useApiDepth').checked;
    if (['codex', 'claude'].includes(payload.provider)) payload.reasoning_effort = $('provider').value === 'aspire' ? 'high' : $('reasoningEffort').value;
    if (['openai', 'codex', 'anthropic', 'claude'].includes(payload.provider)) payload.response_speed = $('provider').value === 'aspire' ? 'standard' : $('responseSpeed').value;
    $('subscriptionHelp').close();
    submitting = true; buttons(); if(!aspireRequest) message(['codex', 'claude'].includes(payload.provider) ? 'Checking your subscription connection…' : 'Joining the robot queue…');
    const state = await api('/api/run', payload);
    if(!runLaunchGuard.accept(launchVersion,state.attempt_id,{status:state.status || 'queued',
      launch_route:state.launch_route,model_name:state.launch_route?.actual_policy === 'astra' ? 'Astra' : provisional.model_name})) return;
    window.yamAspireLineage?.accepted(state.attempt_id,launchVersion,state.launch_route);
    renderCurrentConversation(runLaunchGuard.conversation(null),state);
    window.yamPolicyRoute?.(state.launch_route);
    if(state.launch_route?.actual_policy === 'astra') {
      applyModelName({model_name:'Astra'});
      for(const id of ['conversationState','sideConversationState']) $(id).textContent = (state.status || 'queued')+' · Astra';
    }
    updateSavedKey(state.saved_key_providers);window.yamAnalytics?.observe(state);
    $('apiKey').value = '';$('runSettings').open = true;$('localRunDialog').close();
    active = ['queued','preparing','running'].includes(state.status || 'queued');
    const actualAspire = aspireRequest && state.launch_route?.actual_policy !== 'astra';
    guideRunAttention(actualAspire ? {status:state.status || 'preparing',aspire:true} : {status:state.status || 'queued'});
    message(state.launch_route?.message || (aspireRequest ? 'ASPIRE request accepted. Follow task progress on the left.' : payload.provider === 'groot' ? 'You’re in the queue. GR00T GPU warmup has started.' : 'You’re in the queue. Your position is highlighted above.'));
    }
    catch (error) {
      if (error.stale) return;
      if(launchVersion !== null && !runLaunchGuard.reject(launchVersion,error.message)) return;
      window.yamAnalytics?.rejected(payload?.provider || (aspireRequest ? 'codex' : $('provider').value),payload?.model || $('model').value);
      window.yamAspireLineage?.rejected(error.message,launchVersion);
      if(launchVersion !== null) renderCurrentConversation(runLaunchGuard.conversation(null),{});
      message(launchVersion === null ? error.message : '', launchVersion === null);
      if (error.subscriptionSetup) showSubscriptionSetup(error.subscriptionSetup);
    }
    finally { if(payload) delete payload.api_key; if(launchVersion === null || launchVersion === runLaunchGuard.version) {submitting = false; buttons();} }
  });
  for (const [id, path, text] of [
    ['stop', '/api/stop', 'Stop requested. Your key is saved for your next run.'],
    ['leaveQueue', '/api/stop', 'You left the queue. Your key is saved for your next run.'],
    ['forget', '/api/credentials/clear', 'Key forgotten. Any active run has been asked to stop.'],
    ['operator', '/api/operator', 'Operator requested. Your key is saved for your next run.'],
    ['disconnect', '/api/disconnect', 'Browser session ended. Reload to start a new one.']
  ]) $(id).addEventListener('click', async () => {
    if (id === 'operator') {
      if (contactRequested) {
        contactRequested = false;
        contactDismissed = true;
        $('operator').setAttribute('aria-expanded', 'false');
        message('');
        return;
      }
      contactDismissed = false;
      operatorContact();
      if (!active || ended) return;
      $('apiKey').value = '';
      try { await api(path, {}); } catch (_) { /* Contact details remain available if handoff fails. */ }
      if (contactRequested) operatorContact(); buttons(); return;
    }
    $('apiKey').value = '';
    try { const result = await api(path, {}); updateSavedKey(result.saved_key_providers); if (id === 'disconnect') ended = true; message(text); }
    catch (error) { message(error.message, true); }
    buttons();
  });
  // Shared spectator stopwatch, anchored to server elapsed time rather than the viewer's clock.
  let stopwatch = null;
  const stopwatchTime = seconds => `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  function tickStopwatch() {
    if (!stopwatch) {
      $('runTimer').textContent = '—:—';
      $('ownRunTimer').textContent = '—:—';
      $('runTimerDetail').textContent = 'Waiting for a run';
      return;
    }
    const running = ['preparing', 'running'].includes(stopwatch.status);
    const age = Math.max(0, (performance.now() - stopwatch.receivedAt) / 1000);
    const elapsed = stopwatch.elapsed + (running ? Math.min(age, 5) : 0);
    $('runTimer').textContent = stopwatch.duration === null ? '—:—' : stopwatchTime(Math.ceil(Math.max(0, stopwatch.duration - elapsed)));
    $('ownRunTimer').textContent = $('runTimer').textContent;
    $('runTimerDetail').textContent = running && age > 5 ? 'Reconnecting · timer paused'
      : !running ? 'Run ended'
      : `${stopwatch.status === 'preparing' ? 'Preparing · ' : ''}${stopwatch.duration === null ? 'Run in progress' : stopwatchTime(Math.ceil(Math.max(0, stopwatch.duration - elapsed))) + ' remaining'}`;
  }
  function syncStopwatch(run, generatedAt) {
    const finite = value => typeof value === 'number' && Number.isFinite(value);
    let elapsed = run?.run_elapsed_s;
    if (run && !finite(elapsed) && finite(run.run_started_at)) {
      // API-backed local runs supply server timestamps instead of elapsed time.
      // Never subtract the viewer's wall clock: it may differ from the API clock.
      const end = ['preparing', 'running'].includes(run.status) ? generatedAt : run.run_ended_at;
      if (finite(end)) elapsed = end - run.run_started_at;
      else if (run.run_id && stopwatch?.runId === run.run_id) elapsed = stopwatch.elapsed;
    }
    stopwatch = run && finite(elapsed)
      ? {elapsed: Math.max(0, elapsed), status: run.status, runId: run.run_id,
         duration: typeof run.run_duration_s === 'number' && Number.isFinite(run.run_duration_s) ? run.run_duration_s : null,
         receivedAt: performance.now()} : null;
    tickStopwatch();
  }
  setInterval(tickStopwatch, 250);
  let attentionSession = null;
  let attentionPhase = null;
  function guideRunAttention(state) {
    const phase = state.status;
    const session = state.session_id || (state.new_submission ? null : attentionSession);
    const queued = phase === 'queued';
    const preparing = phase === 'preparing';
    const live = phase === 'running';
    const aspire = state.aspire === true || !!state.provider?.task_progress;
    const warmingUp = !aspire && live && state.first_call_wander_enabled === true && state.run_metrics?.model_calls === 0;
    const vision = typeof aspireSam3Progress === 'function'
      ? aspireSam3Progress({status:phase,task_progress:state.provider?.task_progress}) : null;
    const warmingVision = vision?.state === 'starting';
    const reviewing = !!((aspire || state.new_submission) && preparing && !state.session_id && !state.episode_id);
    const attention = reviewing ? 'reviewing' : aspire && preparing ? 'initializing' : phase;
    const changed = state.new_submission || session !== attentionSession || attention !== attentionPhase;
    const waiting = queued || preparing && !aspire && !reviewing;
    $('yourQueue').classList.toggle('yourTurnWaiting', waiting);
    $('liveConversationPanel').classList.toggle('yourTaskReview', reviewing);
    $('position').closest('.queuePlace').hidden = !(waiting || live);
    $('liveViewer').classList.toggle('yourRunLive', live);
    $('viewerCue').hidden = warmingVision || !(preparing || live || aspire && queued);
    $('viewerCueTitle').textContent = warmingVision && !queued ? vision.title
      : reviewing ? 'Reviewing your prompt…'
      : aspire && queued ? 'Waiting in the robot queue'
      : aspire && live ? 'Your run is active'
      : preparing ? 'Your run is preparing' : warmingUp ? 'Your robot is warming up' : 'Your run is live';
    $('viewerCueDetail').textContent = warmingVision
      ? [vision.timer,queued ? 'The vision worker is waking up while you wait for your turn.'
        : preparing && state.session_id ? 'The vision worker is waking up while the robot initializes.'
        : vision.detail].filter(Boolean).join(' · ')
      : reviewing
      ? 'Working through your prompt and preparing the next steps.'
      : aspire && queued ? 'Your prompt is prepared. Waiting for your turn.'
      : preparing
      ? 'The robot is getting ready. You can stop your run here.'
      : warmingUp ? `The arms do a short dance while ${liveModelName} plans the first move. Your task begins after they return.`
      : 'Watch your robot here. The video may follow with a short delay.';
    $('position').textContent = queued ? (state.queue_position ? `#${state.queue_position}` : 'Joining…') : preparing ? (aspire ? '—' : 'Up next') : live ? 'Your turn' : '—';
    $('queueGuidance').textContent = queued
      ? (state.queue_position === 1 ? "You're next. We'll bring you to the viewer when your run starts." : "You're in the queue. We'll bring you to the viewer when it's your turn.")
      : warmingVision ? [vision.title,vision.timer].filter(Boolean).join(' · ')
      : reviewing ? 'Reviewing your prompt and preparing the next steps.'
      : preparing ? 'The robot is getting ready for your run.'
      : warmingUp ? `Startup dance — waiting for ${liveModelName}’s first decision.`
      : live ? (aspire ? 'Your run is active — watch the highlighted viewer.' : 'Your run is live — watch the highlighted viewer.')
      : 'Join the queue to reserve your turn.';
    if (changed && (reviewing || live)) {
      const target = $(reviewing ? 'liveConversationPanel' : 'liveViewer');
      // Guide once per transition; polling must never pull someone away from reading.
      if (aspire || live || !['queued', 'preparing'].includes(attentionPhase)) {
        target.focus({preventScroll: true});
        target.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center'});
      }
    }
    attentionSession = session;
    attentionPhase = attention;
  }
  function modelResponseTools(event) {
    if (event?.kind !== 'model_response') return [];
    const tools = event.details?.tools;
    const calls = Array.isArray(tools) ? tools : [...String(event.details?.response || event.message || '')
      .matchAll(/^(move_to|done|give_up):[ \t]*(\{[^\n]*\})[ \t]*$/gm)]
      .map(match => ({name: match[1], arguments: match[2]}));
    return calls.flatMap(call => {
      try {
        const args = typeof call.arguments === 'string' ? JSON.parse(call.arguments) : call.arguments;
        return args && typeof args === 'object' && !Array.isArray(args) ? [{name: call.name, args}] : [];
      } catch (_) { return []; }
    });
  }
  function modelRunResult(live) {
    if (!live || live.error || !['stopped', 'completed'].includes(live.status)) return null;
    const latest = [...(live.events || [])].reverse()
      .find(event => ['model_request', 'model_response', 'model_error'].includes(event.kind));
    const decision = modelResponseTools(latest).find(call => ['done', 'give_up'].includes(call.name));
    if (!decision) return null;
    const completed = decision.name === 'done';
    const reason = decision.args[completed ? 'summary' : 'reason'];
    if (typeof reason !== 'string' || !reason.trim()) return null;
    const metrics = live.run_metrics;
    const facts = [];
    if (Number.isInteger(metrics?.model_calls) && metrics.model_calls > 0) {
      facts.push(`${metrics.model_calls} model call${metrics.model_calls === 1 ? '' : 's'}`);
    }
    if (metrics?.accepted_packets === 0) facts.push('No task movements sent');
    return {label: completed ? 'Completed' : 'Could not complete', tone: completed ? 'success' : 'warning',
      attribution: `${live.model_name || liveModelName} ${completed ? 'reported task complete' : 'ended the task'}`,
      summary: reason.trim(), detail: facts.join(' · ')};
  }
  function renderModelRunResult(live) {
    const result = modelRunResult(live);
    const panel = $('runOutcome');
    if (!panel) return result; // A previously loaded page may still have older markup.
    panel.hidden = !result;
    panel.dataset.tone = result?.tone || '';
    $('runOutcomeTitle').textContent = result?.label || '';
    $('runOutcomeAttribution').textContent = result?.attribution || '';
    $('runOutcomeSummary').textContent = result?.summary || '';
    $('runOutcomeDetail').textContent = result?.detail || '';
    $('runOutcomeDetail').hidden = !result?.detail;
    return result;
  }
  function astraStreamNote(event, notesOnly = false) {
    if(event.kind === 'model_progress') return ['summary','status'].includes(event.progress_type || event.details?.progress_type)
      ? String(event.message || '').slice(0,4000) : '';
    if (event.kind !== 'model_response') return '';
    const final = modelResponseTools(event).find(call => ['done', 'give_up'].includes(call.name));
    const reason = final?.args[final.name === 'done' ? 'summary' : 'reason'];
    if (typeof reason === 'string' && reason.trim()) {
      return `${final.name === 'done' ? 'Task complete' : 'Could not complete'}: ${reason.trim()}`;
    }
    const response = event.details?.response || event.message || '';
    const notes = [];
    // Public conversation output contains tool names followed by JSON arguments.
    // Decode JSON strings so escaped quotes and newlines remain readable.
    for (const match of response.matchAll(/"note"\s*:\s*("(?:\\.|[^"\\])*")/g)) {
      try {
        const note = JSON.parse(match[1]).trim();
        if (note) notes.push(note);
      } catch (_) { /* An incomplete note can arrive in a later update. */ }
    }
    if(notes.length) return notes.join('\n\n');
    const stopped=response.match(/(?:^|\n\n)give_up:\s*(\{[\s\S]*\})\s*$/);
    if(stopped) {
      try {const result=JSON.parse(stopped[1]);if(typeof result.reason === 'string') return 'Run stopped: '+result.reason;} catch(_) {}
    }
    if(event.kind === 'model_response' && typeof response === 'string' && !/^\w+:\s*\{/.test(response))
      return response.slice(0,4000);
    return !notesOnly && event.speaker && typeof event.message === 'string' ? event.message : '';
  }
  let liveModelName = 'Astra', lastLive = null;
  function selectedModelName() {
    const applicationName = window.yamApplication?.modelDisplayName?.();
    if (applicationName) return applicationName;
    const provider = $('provider').value;
    if(provider === 'aspire') return 'ASPIRE · Codex/Astra';
    if (provider === 'claude' || provider === 'anthropic') {
      const m = /^claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d+))?$/.exec(claudeModel);
      return m ? m[1][0].toUpperCase() + m[1].slice(1) + ' ' + m[2] + (m[3] ? '.' + m[3] : '') : 'Claude';
    }
    return provider === 'groot' ? 'GR00T' : provider === 'local_raise_lower' ? 'Built-in policy' : 'Astra';
  }
  function applyModelName(live) {
    lastLive = live;
    // A run names its own model; with none showing, name the one this runner will use.
    liveModelName = live?.model_name || (live ? 'Astra' : selectedModelName());
    const reasoningButton = document.querySelector('[data-conversation-mode="reasoning"]');
    const label = $('aspireTaskPanel')?.dataset?.enabled === 'true' ? 'Chat' : liveModelName + ' decision notes';
    if (reasoningButton && reasoningButton.textContent !== label) reasoningButton.textContent = label;
    if ($('liveConversationPanel').dataset.mode !== 'conversation' && $('conversationModeTitle').textContent !== label)
      $('conversationModeTitle').textContent = label;
  }
  function modelRequestProgress(live, modelName, now = Date.now()) {
    if (!['preparing', 'running'].includes(live?.status)) return '';
    const events = live.events || [];
    const last = [...events].reverse().find(event => ['model_request', 'model_response', 'model_error'].includes(event.kind));
    if (last?.kind !== 'model_request') return '';
    const elapsed = Number.isFinite(last.timestamp) ? Math.max(0, Math.floor(now / 1000 - last.timestamp)) : null;
    if (events.some(event => event.kind === 'model_response')) return '';
    return `${modelName}: waiting for the first decision${elapsed === null ? '' : ` · ${elapsed}s elapsed`}. Task, current robot state, and camera images supplied. This includes model startup and image processing; Public summaries appear below when the model emits them; quiet periods are normal.`;
  }
  function renderAstraStream(live) {
    const events = live?.events || [];
    const latest = [...events].reverse().find(event => event.kind === 'model_response' && astraStreamNote(event));
    const running = ['preparing', 'running'].includes(live?.status);
    const progress = modelRequestProgress(live, liveModelName);
    const summary = [...events].reverse().find(event => event.kind === 'model_progress' && (event.progress_type || event.details?.progress_type) === 'summary');
    const publicUpdate = summary ? 'First-call summary: ' + summary.message + '\n\n' : '';
    const note = latest && astraStreamNote(latest);
    let output = live?.reviewing_prompt ? 'Reviewing your prompt…' : progress ? publicUpdate + progress + (note ? '\n\nPrevious decision: ' + note : '') : note || (summary ? 'Public summary: '+summary.message : '') || (live
      ? (running ? `Waiting for ${liveModelName}’s first note…` : `No ${liveModelName} note was recorded for this run.`)
      : `${liveModelName}’s next note will appear here.`);
    if (live?.error) output += '\n\nRun error: ' + live.error;
    if (live?.display_error) output += '\n\nPlayground error: ' + live.display_error;
    const body = $('astraStreamOutput');
    if (body.textContent !== output) {
      body.textContent = output;
      body.scrollTop = 0;
    }
    $('astraStreamState').textContent = live?.reviewing_prompt ? 'Reviewing your prompt…' : running ? 'Live' : modelRunResult(live)?.label || (live ? 'Run ended' : 'Waiting for a run');
    const time = $('astraStreamTime');
    const date = latest?.timestamp != null ? new Date(latest.timestamp * 1000) : null;
    const validDate = date && Number.isFinite(date.getTime());
    const label = validDate ? 'Last note · ' + date.toLocaleTimeString() : '';
    time.hidden = !validDate;
    if (time.textContent !== label) time.textContent = label;
    time.dateTime = validDate ? date.toISOString() : '';
  }
  const ROBOT_STATUS = {
    ready: ['Ready for the next run', 'ready', 'The robot is home with auto-queue enabled.'],
    queued: ['Queued — waiting for your turn', 'waiting', 'The robot is home with auto-queue enabled; your prompt is waiting for assignment.'],
    readiness: ['Queued — waiting for robot readiness', 'waiting', 'The robot isn’t ready yet.'],
    preparing: ['Preparing', 'active', 'Getting ready for your task.'],
    running: ['Running', 'active', 'Your task is active.'],
    home: ['Moving home', 'active', 'Moving to its starting pose.'],
    parking: ['Parking', 'active', 'Moving to its resting pose before disabling.'],
    stoppedReady: ['Stopped but ready', 'ready', 'Stopped with auto-queue enabled. The robot will move home when work arrives.'],
    stopped: ['Stopped', 'waiting', 'Waiting for operator readiness.'],
    offline: ['Offline', 'error', 'Disconnected.'],
    fault: ['Fault', 'error', 'A problem needs attention.'],
    unknown: ['Checking / unavailable', 'unknown', 'Current status is unknown.'],
  };
  function robotStatus(state, robotId, now = Date.now() / 1000) {
    const queue = state.queue_snapshot;
    const station = queue?.stations?.find(item => item.jetson_id === robotId);
    const fresh = timestamp => typeof timestamp === 'number' && now - timestamp >= -5 && now - timestamp <= 10;
    if (!station || !fresh(queue.generated_at)) return 'unknown';
    if (station.connected === false) return 'offline';
    if (station.connected !== true || !fresh(station.observed_at)) return 'unknown';
    const mode = String(station.mode || '').toUpperCase();
    if (mode === 'FAULT') return 'fault';
    const observation = state.last_observation;
    const home = fresh(observation?.observed_at) ? observation.homed : null;
    // YAM also reports queue_ready while verified parked with automatic queue on.
    const isYam = robotId === 'yam-1' || robotCatalog?.robots?.some(robot => robot.id === robotId && robot.hardware === 'yam');
    const parkedYam = isYam && mode === 'DISABLED' && station.queue_ready === true;
    // READY/STOPPED + queue_ready is the controllers' automatic admission signal.
    // Legacy SO101 "active" and transport "available" alone do not establish it.
    const automatic = state.robot_auto_queue_enabled ??
      (parkedYam || mode === 'STOPPED_READY' || station.queue_ready === true && ['READY', 'STOPPED'].includes(mode) ? true : null);
    const ready = home === true && observation.settled === true && automatic === true && station.queue_ready === true;
    if (state.status === 'queued') return ready ? 'queued' : 'readiness';
    if (state.status === 'preparing') return 'preparing';
    if (state.status === 'running') return 'running';
    if (['MOVING_HOME', 'HOMING'].includes(mode)) return 'home';
    if (['PARKING_ZERO', 'PARKING'].includes(mode)) return 'parking';
    if (['INITIALIZING', 'PREPARING'].includes(mode)) return 'preparing';
    if (['EXECUTING', 'API_ACTIVE', 'WAYPATH_EXECUTING', 'WRIST_TEST'].includes(mode)) return 'running';
    if (state.public_run?.status === 'preparing') return 'preparing';
    if (state.public_run?.status === 'running') return 'running';
    if (!['READY', 'STOPPED', 'STOPPED_READY', 'READONLY', 'DISABLED', 'ACTIVE'].includes(mode)) return 'unknown';
    if (ready) return 'ready';
    if (automatic === true && (parkedYam || mode === 'STOPPED_READY' || home != null && observation.settled === true
        && ['STOPPED', 'READONLY', 'DISABLED'].includes(mode))) return 'stoppedReady';
    if (station.queue_ready === true && (home == null || automatic == null)) return 'unknown';
    return 'stopped';
  }
  function renderRobotStatus(key) {
    const [label, tone, meaning] = ROBOT_STATUS[key];
    $('station').textContent = $('status').textContent = label;
    $('station').dataset.tone = tone;
    $('station').title = meaning;
  }
  function renderCurrentConversation(live, state) {
    live = aspireSharedRun(live);
    const previous = renderCurrentConversation.lastRun;
    const identity = run => run?.attempt_id || run?.run_id || run?.task;
    if (live && !live.reviewing_prompt && identity(live) === identity(previous) && previous?.display_error)
      live = {...live, display_error:live.display_error || previous.display_error};
    renderCurrentConversation.lastState = state;
    // Resolve the displayed ASPIRE view before the generic conversation renderer writes shared labels.
    window.yamAspireLineage?.live(live, state);
    const failure = window.yamTaskFailure?.(live, state);
    if(live && failure) live={...live,error:failure.reason};
    renderCurrentConversation.lastRun = live;
    const text = (id,value) => {if($(id).textContent !== value) $(id).textContent=value;};
    text('currentRunner','Runner: ' + (live?.runner_name || '—'));
    text('currentPrompt',live?.task || 'Waiting for someone to run a policy.');
    const header = $('taskHeaderPrompt');
    if (header) {
      const prompt = ['queued', 'preparing', 'running'].includes(live?.status) ? live.task || '' : '';
      header.textContent = prompt ? ': ' + prompt : '';
      if (header.parentElement) header.parentElement.title = prompt;
    }
    applyModelName(live || (state.provider?.launch_route ? {
      model_name:state.provider.launch_route.actual_policy === 'astra' ? 'Astra' : 'ASPIRE · Codex/Astra'} : null));
    const result = renderModelRunResult(live);
    text('conversationState',failure ? failure.label+' · '+failure.reason : live?.reviewing_prompt ? 'Reviewing your prompt…' :
      (result?.label || live?.status || 'Waiting for a run').replaceAll('_', ' ') + (live?.error ? ' · ' + live.error : ''));
    if(identity(live) !== identity(previous) || live?.reviewing_prompt) renderCurrentConversation.errorTimes=new Map();
    const errorTimes=renderCurrentConversation.errorTimes ||= new Map();
    const events = [...(live?.events || [])];
    for (const [speaker, reason] of [['Run error', live?.error], ['Playground error', live?.display_error]]) {
      if (reason && !events.some(event => event.kind === 'model_error' && event.message === reason)) {
        const key=JSON.stringify([speaker,reason]);
        const timestamp=live.ended_at || errorTimes.get(key) || events.at(-1)?.timestamp || Date.now()/1000;
        errorTimes.set(key,timestamp);
        events.push({kind:'model_error',speaker,message:reason,timestamp});
      }
    }
    renderConversation(events, 'liveConversationMessages');
    renderAstraStream(live);
    text('sideRunner',$('currentRunner').textContent);
    const aspirePrompt = $('aspireTaskPanel')?.dataset?.enabled === 'true' && !$('aspireTaskPanel').hidden;
    if (!aspirePrompt && $('sidePrompt').textContent !== $('currentPrompt').textContent)
      $('sidePrompt').textContent = $('currentPrompt').textContent;
    text('sideConversationState',$('conversationState').textContent);
    renderConversation(events, 'sideConversationMessages');
    text('sideConversationTitle',$('liveConversationPanel').dataset.mode === 'reasoning' ? 'Response notes' : 'Conversation with ' + liveModelName);
    return failure;
  }
  function renderConversationError(reason) {
    const live = renderCurrentConversation.lastRun;
    if (live) renderCurrentConversation({...live,display_error:reason},renderCurrentConversation.lastState || {});
  }
  function renderPublicRunNotice(state, taskFailure = null) {
    const run = state.public_run;
    const error = taskFailure?.reason || run?.error || state.robot_fault;
    const warning = ['preparing', 'running'].includes(run?.status) ? run?.warning : null;
    const notice = error || warning;
    const element = $('publicRunError');
    element.hidden = !notice;
    element.dataset.tone = error ? 'error' : 'warning';
    element.textContent = notice ? (taskFailure || run?.error || warning
      ? `${run?.runner_name || state.runner_name || 'Anonymous'} — ${error ? (error === 'Run reached time limit' ? 'Failure' : 'run error') : 'connection warning'}: ${notice}`
      : `Robot error: ${notice}`) : '';
  }
  function render(state) {
    if (state.subscription_setup?.setup_prompt) {
      const failure = `${state.session_id}:${state.error}`;
      if (failure !== shownSubscriptionFailure) {
        shownSubscriptionFailure = failure;
        showSubscriptionSetup(state.subscription_setup);
      }
    }
    updateSavedKey(state.saved_key_providers);
    window.yamAnalytics?.observe(state);
    $('whatsRunning').textContent = state.whats_running?.length
      ? state.whats_running.map(run => `${run.runner_name || 'Anonymous'} · ${run.task || 'Task unavailable'}${run.status === 'preparing' ? ' (preparing)' : ''}`).join('\n')
      : 'No policy is running.';
    const live = runLaunchGuard.conversation(routedConversationRun(state.public_run,state),state);
    const route = state.provider?.launch_route || live?.launch_route;
    const routingActive = ['queued','preparing','running'].includes(state.status) || ['queued','preparing','running'].includes(live?.status);
    window.yamPolicyRoute?.(routingActive ? route : null);
    syncStopwatch(state.public_run, state.queue_snapshot?.generated_at);
    const taskFailure = renderCurrentConversation(live,state);
    const chat=$('aspireTaskPanel');
    const failureInChat=taskFailure && chat?.dataset.enabled === 'true' && !chat.hidden &&
      chat.dataset.failureKey === JSON.stringify(taskFailure);
    active = ['queued', 'preparing', 'running'].includes(state.status);
    ownRunLive = ['preparing', 'running'].includes(state.status);
    $('status').textContent = state.status.replaceAll('_', ' ');
    guideRunAttention(state);
    if (contactRequested || (!contactDismissed && state.error?.startsWith('Operator request failed:'))) operatorContact();
    else if (contactDismissed && state.error?.startsWith('Operator request failed:')) { /* Keep dismissed contact information closed. */ }
    else if (state.error || state.execution_blocked_reason)
      message(failureInChat && state.error === taskFailure.reason ? '' : state.error || state.execution_blocked_reason, true);
    else if (state.feedback_warning) message(state.feedback_warning);
    else if ($('message').textContent.startsWith('Motion paused while refreshing robot status:')) message('Robot status confirmed. Continuing the run.');
    else if (state.provider?.provider === 'groot' && state.provider.warmup === 'warming') message('In the robot queue · warming GR00T GPU…');
    else if (state.provider?.provider === 'groot' && state.provider.warmup === 'failed') message('GR00T GPU startup failed. Leave the queue and try again.', true);
    else if (state.provider?.vision?.retry?.state === 'retrying') message(state.provider.vision.retry.message, state.provider.vision.retry.cause === 'No image');
    else if (state.provider?.vision?.retry?.state === 'failed') message(state.provider.vision.retry.message, true);
    else if (state.provider?.vision?.retry?.state === 'recovered' && /^(Waiting for next |No image)/.test($('message').textContent)) message('Camera feeds recovered. Continuing the run.');
    const queue = state.queue_snapshot;
    const sharedError = state.public_run?.error || state.robot_fault;
    const sameAttempt = live?.attempt_id && live.attempt_id === state.public_run?.attempt_id;
    $('publicRunError').hidden = !sharedError || !!(failureInChat && !state.robot_fault &&
      (sharedError === taskFailure.reason || (sameAttempt && sharedError === 'The run encountered a model or runner error.')));
    $('publicRunError').textContent = sharedError
      ? `${state.public_run?.error ? (state.public_run.runner_name || 'Anonymous') + (sharedError === 'Run reached time limit' ? ' — Failure: ' : ' — run error: ') : 'Robot error: '}${sharedError}` : '';
    const station = queue?.stations?.find(item => item.jetson_id === selectedRobot);
    if (station) updateCameraAvailability(station.connected === true);
    const faultNotice = {pending:'Notifying the operator…', submitted:'The operator has been notified.', failed:'Could not notify the operator.', unavailable:'Operator attention needed.'}[station?.fault_notification] || 'Operator attention needed.';
    const statusKey = robotStatus(state, selectedRobot);
    renderRobotStatus(statusKey);
    if (statusKey === 'fault') $('station').title += ' ' + faultNotice;
    const entries = queue?.entries || [];
    const waiting = entries.filter(item => !['running', 'preparing'].includes(item.status)).length;
    const place = $('position').textContent;
    const queueLabel = (queue ? `Queue · ${waiting} waiting` : 'Queue · unavailable') +
      (place === 'Your turn' ? ' · Your turn' : ' · Your place: ' + place);
    if ($('queueSummary').textContent !== queueLabel) $('queueSummary').textContent = queueLabel;
    $('leaveQueue').hidden = state.status !== 'queued';
    $('queue').replaceChildren(...(entries.length ? entries.map(item => {
      const li = document.createElement('li'); li.textContent = `#${item.position} · ${item.runner_name || 'Anonymous'} · ${item.status}${item.is_mine ? ' · YOU' : ''}`; return li;
    }) : [Object.assign(document.createElement('li'), {textContent: queue ? 'No one is waiting' : 'Queue unavailable'})]));
    buttons();
  }
  async function history() {
    const {runs} = await api('/api/recordings');
    const selected = $('runs').value;
    $('runs').replaceChildren(...(runs.length ? runs.map(run => Object.assign(document.createElement('option'), {
      value: run.run_id, textContent: new Date(run.updated_at * 1000).toLocaleString() + ' · ' + run.run_id.slice(-6)
    })) : [Object.assign(document.createElement('option'), {value: '', textContent: 'No runs yet'})]));
    if (runs.some(run => run.run_id === selected)) $('runs').value = selected;
    $('conversationToggle').disabled = $('saveLog').disabled = $('replay').disabled = !$('runs').value;
    await events();
  }
  async function events() {
    const run = $('runs').value;
    if (!run) return;
    const data = await api(`/api/recordings/${encodeURIComponent(run)}/interactions`);
    if ($('runs').value !== run) return;
    renderConversation(data.events || []);
    $('events').replaceChildren(...(data.events || []).slice(-100).map(event => {
      const li = document.createElement('li'), time = document.createElement('time');
      time.textContent = new Date(event.timestamp * 1000).toLocaleTimeString();
      li.append(time, document.createTextNode(event.message)); return li;
    }));
  }
  const conversationSnapshots = {};
  let sideConversationEvents = [];
  document.querySelectorAll('[data-conversation-mode]').forEach(button => {
    button.addEventListener('click', () => {
      const mode = button.dataset.conversationMode;
      const panel = $('liveConversationPanel');
      panel.dataset.mode = mode;
      panel.hidden = mode === 'hide';
      panel.closest('.watchLayout').classList.toggle('conversationClosed', mode === 'hide');
      $('conversationModeTitle').textContent = mode === 'reasoning' ? liveModelName + ' decision notes' : 'Convo mode';
      $('sideConversationTitle').textContent = mode === 'reasoning' ? 'Response notes' : 'Conversation with ' + liveModelName;
      document.querySelectorAll('[data-conversation-mode]').forEach(item => {
        item.setAttribute('aria-pressed', String(item === button));
      });
      renderConversation(sideConversationEvents, 'sideConversationMessages');
      window.yamAspireLineage?.refresh();
    });
  });
  function renderConversation(events, target = 'conversationMessages') {
    if (target === 'sideConversationMessages') sideConversationEvents = events;
    const reasoning = target === 'sideConversationMessages' && $('liveConversationPanel').dataset.mode === 'reasoning';
    const messages = events.filter(event => ['model_request', 'model_response', 'model_error'].includes(event.kind) ||
      event.kind === 'model_progress' && ['summary','status'].includes(event.progress_type || event.details?.progress_type));
    const snapshot = JSON.stringify([reasoning, liveModelName, messages]);
    if (snapshot === conversationSnapshots[target]) return;
    conversationSnapshots[target] = snapshot;
    const list = $(target), follow = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    const scroll = list.scrollTop;
    const definitions = new Map();
    let previousInput = [];
    const textContent = item => typeof item.content === 'string' ? item.content
      : (item.content || []).filter(part => part.type === 'input_text').map(part => part.text).join('\n');
    const rows = messages.map(event => {
      const request = event.request || event.details?.request_display;
      const input = request?.input;
      const refs = [];
      let freshText = null;
      if (event.kind === 'model_request' && Array.isArray(input)) {
        for (const item of input.filter(item => ['system', 'developer'].includes(item.role))) {
          const content = textContent(item), key = item.role + ':' + content;
          if (!definitions.has(key)) {
            const number = definitions.size + 1;
            const definition = document.createElement('li'), details = document.createElement('details');
            const summary = document.createElement('summary'), pre = document.createElement('pre');
            details.id = `${target}-system-${number}`;
            summary.textContent = number === 1 ? 'System prompt' : `System prompt ${number}`;
            pre.textContent = content;
            details.append(summary, pre); definition.append(details);
            definitions.set(key, {definition, details, number});
          }
          refs.push(definitions.get(key));
        }
        let common = 0;
        while (common < previousInput.length && common < input.length && JSON.stringify(previousInput[common]) === JSON.stringify(input[common])) common++;
        freshText = input.slice(common).filter(item => item.role === 'user').map(textContent).join('\n\n');
        previousInput = input;
      }

      if (reasoning && event.kind !== 'model_error' && !astraStreamNote(event, true)) return null;
      const li = document.createElement('li'), heading = document.createElement('strong'), body = document.createElement('p');
      li.classList.add('conversationTurn');
      li.classList.add(event.kind === 'model_request' ? 'conversationOutgoing' : 'conversationIncoming');
      heading.className = 'conversationHeading';
      const speaker = event.speaker || (event.kind === 'model_progress' ? liveModelName + ((event.progress_type || event.details?.progress_type) === 'summary' ? ' · public summary' : ' · status') : event.kind === 'model_request' ? 'To ' + liveModelName : event.kind === 'model_response' ? liveModelName : 'Run error');
      const speakerLabel = document.createElement('span'), timestamp = document.createElement('time');
      speakerLabel.textContent = speaker;
      timestamp.textContent = new Date(event.timestamp * 1000).toLocaleTimeString([], {hour:'numeric', minute:'2-digit', second:'2-digit'});
      heading.append(speakerLabel, timestamp);
      body.textContent = event.kind === 'model_request' ? event.details?.request_text || event.details?.observation || event.message : event.details?.response || event.message;
      if (freshText !== null) body.textContent = freshText;
      if (reasoning && event.kind !== 'model_error') body.textContent = astraStreamNote(event, true);
      li.append(heading);
      for (const ref of refs) {
        const marker = document.createElement('button'); marker.type = 'button';
        marker.textContent = ref.number === 1 ? 'System instructions' : `System instructions ${ref.number}`;
        marker.className = 'systemPromptReference';
        marker.setAttribute('aria-controls', ref.details.id);
        marker.addEventListener('click', () => { ref.details.open = true; ref.details.scrollIntoView({block:'nearest'}); });
        li.append(marker);
      }
      if (event.kind === 'model_request') {
        const content = document.createElement('div'); content.className = 'requestContent';
        const robotState = document.createElement('details'), stateSummary = document.createElement('summary');
        stateSummary.textContent = 'Robot state'; robotState.append(stateSummary);
        const lines = body.textContent.replace(/\s+(?=Instruction:|state\[|Gateway waypoints remaining)/g, '\n').split('\n');
        for (const line of lines) {
          if (!line.trim()) continue;
          const match = line.match(/^(Instruction|state\[joint_pos\]|state\[eef_state\]):\s*(.*)$/);
          if (match) {
            if (match[1] === 'Instruction') {
              const paragraph = document.createElement('p'); paragraph.textContent = match[2]; content.append(paragraph);
            } else {
              const block = document.createElement('section'), label = document.createElement('h4'), value = document.createElement('pre');
              label.textContent = match[1] === 'state[joint_pos]' ? 'Joint positions' : 'End-effector state';
              value.textContent = match[1] === 'state[eef_state]' ? match[2].replace(/\s+(?=[a-z_]+=)/g, '\n') : match[2];
              block.append(label, value); robotState.append(block);
            }
          } else {
            const paragraph = document.createElement('p'); paragraph.textContent = line; content.append(paragraph);
          }
        }
        if (robotState.children.length > 1) content.append(robotState);
        li.append(content);
      } else { body.className = 'astraResponse'; li.append(body); }
      if (event.kind === 'model_request' && event.images?.length) {
        const gallery = document.createElement('div'); gallery.className = 'messageImages';
        for (const item of event.images) {
          if (!/^\/api\/public-images\/(?:robocurve|run)_[a-f0-9]{32}\/[a-f0-9]{64}$/.test(item.url)) continue;
          const figure = document.createElement('figure'), image = document.createElement('img'), caption = document.createElement('figcaption');
          image.src = item.url + (item.url.includes('?') ? '&' : '?') + 'robot_id=' + encodeURIComponent(selectedRobot); image.alt = item.name + ' image sent to ' + liveModelName; image.loading = 'lazy';
          caption.textContent = item.name; figure.append(image, caption); gallery.append(figure);
        }
        li.append(gallery);
      }
      if (event.request && !reasoning) {
        const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
        summary.textContent = 'Request JSON · full input history and tool definitions';
        pre.textContent = JSON.stringify(event.request, null, 2);
        details.append(summary, pre); li.append(details);
      }
      return li;
    });
    list.replaceChildren(...[...definitions.values()].map(ref => ref.definition),
      ...rows.filter(Boolean));
    if (!list.children.length) list.append(Object.assign(document.createElement('li'), {textContent: reasoning ? liveModelName + '’s response notes will appear here.' : 'No conversation yet.'}));
    list.scrollTop = follow ? list.scrollHeight : scroll;
  }
  const sampleDialog = document.createElement('dialog');
  sampleDialog.id = 'sampleConversationDialog';
  sampleDialog.setAttribute('aria-labelledby', 'sampleConversationTitle');
  const sampleTitle = document.createElement('h2'); sampleTitle.id = 'sampleConversationTitle';
  sampleTitle.textContent = 'Sample conversation';
  const sampleNote = document.createElement('p');
  sampleNote.textContent = 'Illustrative example only. This does not start a robot run.';
  const sampleClose = document.createElement('button'); sampleClose.type = 'button'; sampleClose.textContent = 'Close sample';
  sampleClose.addEventListener('click', () => sampleDialog.close());
  const sampleList = document.createElement('ol'); sampleList.id = 'sampleConversationMessages';
  sampleDialog.append(sampleTitle, sampleNote, sampleClose, sampleList); document.body.append(sampleDialog);
  const sampleButton = document.createElement('button'); sampleButton.type = 'button';
  sampleButton.textContent = 'View sample conversation';
  $('liveConversationMessages').before(sampleButton);
  function showSampleConversation() {
    const turns = [
      ['model_request', 'Instruction: Pick up the apple and place it on the plate.\nstate[joint_pos]: [0, 0.2, 0.1, 0, 0, 0]'],
      ['model_response', 'I can see the apple beside the plate. I’ll move the left gripper above the apple, then lower it to grasp.'],
      ['model_request', 'The move is complete. The gripper is above the apple.\nstate[eef_state]: x=0.30 y=0.20 z=0.15'],
      ['model_response', 'The apple is centered between the fingers. I’m lowering straight down, then closing the gripper gently.'],
      ['model_request', 'The gripper has closed and lifted the apple. The plate is clear.'],
      ['model_response', 'I’ll move over the plate, lower the apple, and open the gripper to release it.'],
      ['model_request', 'The apple has been released onto the plate.'],
      ['model_response', 'The apple is on the plate and both grippers are clear. The task is complete.'],
    ];
    renderConversation(turns.map(([kind, message], i) => ({id:i+1, timestamp:1700000000+i*8, kind, message})), 'sampleConversationMessages');
    sampleList.scrollTop = 0;
    if (!sampleDialog.open) sampleDialog.showModal();
  }
  sampleButton.addEventListener('click', showSampleConversation);
  if (new URLSearchParams(location.search).get('preview') === 'conversation') showSampleConversation();
  $('conversationToggle').addEventListener('click', () => {
    const opening = $('conversation').hidden;
    $('conversation').hidden = !opening;
    $('conversationToggle').setAttribute('aria-expanded', String(opening));
    if (opening) events().catch(error => message(error.message, true));
  });
  $('runs').addEventListener('change', () => { $('video').pause(); $('video').hidden = true; events().catch(e => message(e.message, true)); });
  $('saveLog').addEventListener('click', () => {
    if ($('runs').value) window.location.assign(`/api/recordings/${encodeURIComponent($('runs').value)}/log.zip`);
  });
  $('replay').addEventListener('click', async () => {
    try {
      const data = await api(`/api/recordings/${encodeURIComponent($('runs').value)}/artifacts`);
      if (!data.video_url) throw new Error('This run does not have a published video yet.');
      const url = new URL(data.video_url);
      if (url.origin !== 'https://huggingface.co') throw new Error('Video source unavailable.');
      $('video').src = url.href; $('video').hidden = false;
      $('replayHelp').textContent = 'Loading the selected run’s published video…';
      $('video').play().catch(() => {});
    } catch (error) { $('replayHelp').textContent = error.message; }
  });
  $('video').addEventListener('error', () => { $('replayHelp').textContent = 'Video is not available yet. Try again after the robot finishes uploading.'; });
  $('video').addEventListener('loadeddata', () => { $('replayHelp').textContent = 'Published robot recording'; });
  let videoStream = null;
  function synchronizedPanels(video, sourceLabel) {
    const tiles = [...document.querySelectorAll('canvas[data-sync-tile]')];
    if (!tiles.length) return;
    const atlas = document.createElement('canvas');
    atlas.width = 1280; atlas.height = 720;
    const context = atlas.getContext('2d', {alpha:false});
    const contexts = tiles.map(tile => tile.getContext('2d', {alpha:false}));
    const labels = [...document.querySelectorAll('[data-sync-status]')];
    tiles.forEach(tile => {
      tile.tabIndex = 0;
      tile.addEventListener('click', () => video.play().catch(() => {}));
      tile.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); video.play().catch(() => {}); } });
    });
    let callback, lastMediaTime = -1, stopped = false, frame = 0;
    const stopSnapshots = [];
    const latency = document.querySelector?.('#streamLatency'), latencySamples = [];
    function measureLatency(hd) {
      if (!latency || !hd || typeof context.getImageData !== 'function') return;
      try {
        const pixels = context.getImageData(0, 6, 512, 1).data, bytes = new Uint8Array(8);
        for (let bit = 0; bit < 64; bit++) {
          const pixel = (bit*8+4)*4;
          bytes[Math.floor(bit/8)] |= (pixels[pixel]+pixels[pixel+1]+pixels[pixel+2] > 384 ? 1 : 0) << (7-bit%8);
        }
        let crc = 0;
        for (const byte of bytes.slice(0,7)) {
          crc ^= byte;
          for (let bit = 0; bit < 8; bit++) crc = crc & 128 ? ((crc << 1)^7)&255 : (crc << 1)&255;
        }
        if (bytes[0] !== 0xD5 || crc !== bytes[7]) return;
        const captured = bytes.slice(1,7).reduce((stamp,byte) => stamp*256+byte,0);
        if (latencySamples[latencySamples.length-1]?.captured === captured) return;
        const rendered = Date.now(), age = rendered-captured;
        if (age < -1000 || age > 60000) return;
        latencySamples.push({captured,rendered,age});
        if (latencySamples.length > 300) latencySamples.shift();
        latency.dataset.samples = JSON.stringify(latencySamples);
        latency.textContent = ' · ~' + Math.round(Math.max(0,age)) + ' ms video delay';
        latency.hidden = false;
      } catch { /* Cross-origin or invalid diagnostics do not interrupt video. */ }
    }

    // A bounded still-image request gives each panel a real picture while the
    // WebRTC/HLS handshake runs. It is a preview, never synchronized live video.
    const snapshotRobot = selectedRobot;
    function loadSnapshots() {
      tiles.forEach((tile, i) => {
        const role = tile.dataset.cameraRole;
        if (!role) return;
        const snapshot = new Image();
        let pending = true;
        const stop = () => {
          pending = false; clearTimeout(timer);
          snapshot.onload = snapshot.onerror = null;
          snapshot.removeAttribute('src');
        };
        const timer = setTimeout(stop, 5000);
        stopSnapshots.push(stop);
        snapshot.onerror = stop;
        snapshot.onload = async () => {
          try {
            await snapshot.decode();
            if (!pending || stopped || frame || document.hidden || selectedRobot !== snapshotRobot) return;
            const width = snapshot.naturalWidth || 640, height = snapshot.naturalHeight || 360;
            tile.width = width; tile.height = height;
            contexts[i].drawImage(snapshot, 0, 0, width, height);
            tile.dataset.frameSource = 'snapshot';
          } catch { /* Let the live stream finish connecting if the still fails. */ }
          finally { stop(); }
        };
        snapshot.src = '/api/monitor/cameras/' + encodeURIComponent(role)
          + '?robot_id=' + encodeURIComponent(snapshotRobot) + '&t=' + Date.now();
      });
    }
    function paint() {
      if (stopped) return;
      const stale = video.yamHealth?.stale() === true;
      if (!document.hidden && !stale && video.readyState >= 2 && (frame === 0 || (!video.paused && video.currentTime !== lastMediaTime))) {
        // Snapshot ONCE, then crop all panels from that immutable canvas frame.
        // MDN drawImage nine-argument contract: docs/refs/canvas.
        const hd = video.videoWidth === 2560 && video.videoHeight === 1080;
        const width = video.videoWidth || 1280, height = video.videoHeight || 720;
        if (atlas.width !== width) atlas.width = width;
        if (atlas.height !== height) atlas.height = height;
        context.drawImage(video, 0, 0, width, height);
        measureLatency(hd);
        frame++;
        if (frame === 1) stopSnapshots.forEach(stop => stop());
        tiles.forEach((tile, i) => {
          const role = tile.dataset.cameraRole;
          const rect = hd ? ({observer:[0,0,1920,1080],left:[1920,0,640,360],top:[1920,360,640,360],right:[1920,720,640,360]})[role]
            : [(i%2)*width/2, Math.floor(i/2)*height/2, width/2, height/2];
          if (tile.width !== rect[2]) tile.width = rect[2];
          if (tile.height !== rect[3]) tile.height = rect[3];
          contexts[i].drawImage(atlas, ...rect, 0, 0, rect[2], rect[3]);
          tile.dataset.presentedFrame = String(frame);
          tile.dataset.frameSource = 'video';
        });
        lastMediaTime = video.currentTime;
      }
      labels.forEach((label, i) => {
        const status = stale ? 'Stream stalled' : sourceLabel.textContent;
        label.textContent = tiles[i]?.dataset.frameSource === 'snapshot' ? 'Still image · ' + status : status;
      });
      if (latency && stale) latency.hidden = true;
      callback = requestAnimationFrame(paint);
    }
    document.querySelectorAll('[data-expand-camera]').forEach(button => {
      button.addEventListener('click', () => {
        const panel = button.closest('figure');
        if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
        else panel.requestFullscreen?.().catch(() => {});
      });
    });
    video.yamStopPainting = () => {
      stopped = true; cancelAnimationFrame(callback);
      stopSnapshots.forEach(stop => stop());
      if (latency) { latency.hidden = true; delete latency.dataset.samples; }
    };
    window.addEventListener('pagehide', video.yamStopPainting);
    paint();
    if (!frame) loadSnapshots();
  }
  function camera(image) {
    const label = image.parentElement.querySelector('figcaption span');
    const cameraName = image.dataset.camera, video = document.createElement('video');
    const streamPath = image.dataset.streamPath || cameraName;
    video.muted = true; video.autoplay = true; video.playsInline = true; video.controls = true;
    video.setAttribute('aria-label', image.alt); image.replaceWith(video);
    video.dataset.camera = cameraName;
    video.dataset.streamPath = streamPath;
    if (cameraName === 'synchronized') { video.controls = false; synchronizedPanels(video, label); }
    let hls = null, reader = null, retry, rtcTimeout, generation = 0, lastTime = -1, lastAdvance = 0;
    let disposed = false;
    video.yamStop = () => { disposed = true; video.yamHealth?.close(); video.yamStopPainting?.(); cleanup(); };
    let transport = 'hls', attemptStarted = 0;
    if (cameraName === 'synchronized' && window.YamStreamHealth) {
      video.yamHealth = new window.YamStreamHealth(video, {
        badge: $('streamHealth'), detail: $('streamHealthDetail'), recover: () => recoverStream(),
        report: data => { if (csrf && !ended) api('/api/stream-health', data).catch(() => {}); }
      });
    }
    function cleanup() {
      generation++; clearTimeout(retry); clearTimeout(rtcTimeout); retry = null;
      if (reader) { reader.close(); reader = null; }
      if (hls) { hls.destroy(); hls = null; }
      video.pause(); video.srcObject = null; video.removeAttribute('src'); video.load(); lastTime = -1;
    }
    function recoverStream() {
      if (disposed || ended || document.hidden || retry) return;
      // Give each transport a full startup window; health remains visibly stalled.
      if (performance.now() - attemptStarted < (transport === 'hls' ? 20000 : 8000)) return;
      if (transport === 'webrtc') connect(true); else offline();
    }
    function offline() {
      if (retry) return;
      cleanup(); label.textContent = ended ? 'Session ended' : 'Reconnecting';
      if (!disposed && !ended && !document.hidden) retry = setTimeout(connect, 5000);
    }
    function connect(fallback = false) {
      cleanup(); if (disposed || ended || document.hidden) return;
      const attempt = generation;
      label.textContent = 'Loading video'; lastAdvance = attemptStarted = performance.now();
      if (['observer', 'synchronized'].includes(cameraName) && !fallback && window.MediaMTXWebRTCReader && window.RTCPeerConnection) {
        transport = video.dataset.transport = 'webrtc';
        const useFallback = () => { if (attempt === generation && !ended && !document.hidden) connect(true); };
        rtcTimeout = setTimeout(useFallback, 8000);
        try {
          // Pinned MediaMTX v1.21.0 reader; same-origin WHEP proxy exposes read only.
          reader = new MediaMTXWebRTCReader({url: new URL(`/${streamPath}/whep`, location.href).href,
            onError: useFallback,
            onTrack: event => {
              if (attempt !== generation) return;
              video.srcObject = event.streams[0];
              video.play().catch(() => { if (attempt === generation) label.textContent = 'Press play'; });
            }
          });
        } catch { useFallback(); }
        return;
      }
      transport = video.dataset.transport = 'hls';
      const url = cameraName === 'synchronized' ? `/${streamPath}/index.m3u8` : `/live-video/hls/${cameraName}/index.m3u8`;
      if (window.Hls?.isSupported()) {
        hls = new Hls({enableWorker:false, lowLatencyMode:false, maxBufferLength:30,
          backBufferLength:10, liveSyncDurationCount:4, liveMaxLatencyDurationCount:6,
          liveSyncOnStallIncrease:0, maxLiveSyncPlaybackRate:1.1});
        hls.on(Hls.Events.ERROR, (_, data) => { if (attempt === generation && data.fatal) offline(); });
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          if (attempt === generation) video.play().catch(() => { label.textContent = 'Press play'; });
        });
        hls.loadSource(url); hls.attachMedia(video);
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        // Native HLS (Safari): target eight seconds behind the available edge.
        const catchUp = () => {
          if (attempt !== generation || !video.seekable.length) return;
          const i = video.seekable.length - 1, edge = video.seekable.end(i);
          if (video.currentTime === 0 || edge - video.currentTime > 12) {
            video.currentTime = Math.max(video.seekable.start(i), edge - 8);
          }
        };
        video.onloadedmetadata = catchUp; video.onprogress = catchUp;
        video.src = url; video.play().catch(() => { label.textContent = 'Press play'; });
      } else { label.textContent = 'Video unsupported'; }
    }
    video.addEventListener('timeupdate', () => {
      if (ended || video.paused || video.readyState < 2 || video.currentTime <= 0 || video.currentTime === lastTime) return;
      clearTimeout(rtcTimeout);
      lastTime = video.currentTime; lastAdvance = performance.now(); label.textContent = transport === 'webrtc' ? (cameraName === 'synchronized' ? 'Live · synchronized' : 'Live') : 'Live · delayed';
      window.yamAnalytics?.playback(cameraName);
    });
    video.addEventListener('waiting', () => { if (!ended) label.textContent = 'Buffering'; });
    video.addEventListener('error', () => { if (video.getAttribute('src') && !ended) offline(); });
    const watchdog = setInterval(async () => {
      if (ended) { cleanup(); label.textContent = 'Session ended'; clearInterval(watchdog); return; }
      if (document.hidden || retry) return;
      if (video.yamHealth) return; // Frame monitor owns synchronized-stream recovery.
      if (transport === 'webrtc') {
        if (performance.now() - lastAdvance > 8000) connect(true);
        return; // HLS health is independent of the WebRTC stream.
      }
      if (performance.now() - lastAdvance > 20000) { offline(); return; }
      if (cameraName === 'synchronized') return; // Same composite HLS: independent Lightsail health does not apply.
      const attempt = generation;
      try {
        const r = await fetch('/live-video/health.json', {cache:'no-store', signal:AbortSignal.timeout(5000)});
        const health = r.ok ? await r.json() : null;
        if (attempt === generation && !health?.cameras?.[cameraName]?.live) offline();
      } catch { if (attempt === generation) offline(); }
    }, 5000);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { cleanup(); label.textContent = 'Paused'; } else connect();
    });
    window.addEventListener('pagehide', cleanup);
    connect();
  }
  let statusPollError = '';
  async function poll() {
    const launchVersion = runLaunchGuard.version;

    if (ended || !csrf) { setTimeout(poll, 1000); return; }
    try {
      const state = await api('/api/status');
      if (statusPollError && $('message').textContent === statusPollError) message('');
      statusPollError = '';
      render(state);
      if (Date.now() - lastHistory > 4000) {
        lastHistory = Date.now();
        try { await history(); } catch (error) { if (!error.stale) message(error.message, true); }
      }
    } catch (error) { if (!error.stale) { renderRobotStatus('unknown'); $('astraStreamState').textContent = 'Reconnecting'; statusPollError = error.message; message(error.message, true); } }
    setTimeout(poll, 1000);
  }
  window.addEventListener('pagehide', () => { $('apiKey').value = ''; });
  const originalCameraMarkup = $('liveViewer').innerHTML;
  let modelCameraNames = new Set();
  let cameraNames = ['left', 'top', 'right'], camerasDisconnected = false, cameraEpoch = 0;
  function updateCameraAvailability(connected) {
    if (connected === !camerasDisconnected) return;
    camerasDisconnected = !connected;
    selectedCameras(cameraNames);
  }

  function addModelBadge(picture, name) {
    if (!modelCameraNames.has(name)) return;
    const badge = document.createElement('span');
    badge.className = 'modelCameraBadge';
    badge.title = 'Model input: this camera supplies images to the model. The displayed frame may differ from the submitted frame.';
    badge.setAttribute('aria-label', name + ' camera is a model input');
    badge.innerHTML = '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><rect x="4" y="4" width="8" height="8" rx="2"/><path d="M6 1v3m4-3v3M6 12v3m4-3v3M1 6h3m-3 4h3m8-4h3m-3 4h3"/></svg><span>Model</span>';
    picture.append(badge);
  }

  function selectedCameras(names) {
    cameraNames = names;
    const epoch = ++cameraEpoch;
    document.querySelectorAll('[data-camera]').forEach(video => video.yamStop?.());
    const runControls = $('liveRunControls');
    $('liveViewer').innerHTML = originalCameraMarkup;
    // Preserve the stop listener when robot selection rebuilds the camera tiles.
    $('liveRunControls').replaceWith(runControls);
    $('videoDelayNotice').hidden = !videoStream;
    const roles = videoStream?.cameras || names;
    const topWithGrippers = selectedRobot === 'robot-ba8413962083809c'
      && roles.length === 3 && ['top', 'left', 'right'].every(role => roles.includes(role));
    $('liveViewer').dataset.layout = roles.includes('observer') && roles.length === 4 ? 'observer-focus' : topWithGrippers ? 'top-with-grippers' : names.length === 1 ? 'single' : 'multi';
    const cameraLabel = role => topWithGrippers && role !== 'top'
      ? `${role[0].toUpperCase() + role.slice(1)} wrist` : role[0].toUpperCase() + role.slice(1);
    if (camerasDisconnected) {
      $('liveViewer').querySelectorAll('figure, video, canvas').forEach(node => node.remove());
      $('videoDelayNotice').hidden = true;
      const notice = document.createElement('p');
      notice.id = 'armsDisconnected'; notice.setAttribute('role', 'status');
      notice.textContent = 'Arms are disconnected';
      notice.style.cssText = 'grid-column:1/-1;text-align:center;padding:72px 20px;font-size:24px';
      $('liveViewer').prepend(notice);
      return;
    }
    if (videoStream) {
      const roles = videoStream.cameras;
      $('liveViewer').querySelectorAll('[data-sync-tile]').forEach(tile => {
        const role = roles[Number(tile.dataset.syncTile)], figure = tile.closest('figure');
        if (!role) { figure.remove(); return; }
        figure.dataset.cameraRole = role;
        tile.dataset.cameraRole = role;
        tile.setAttribute('aria-label', role + ' robot camera');
        figure.querySelector('figcaption').firstChild.textContent = cameraLabel(role) + ' ';
        figure.querySelector('[data-expand-camera]').setAttribute('aria-label', 'Expand ' + role + ' camera');
        addModelBadge(tile.parentElement, role);
      });
      document.querySelectorAll('[data-camera]').forEach(image => {
        image.dataset.streamPath = videoStream.path;
        camera(image);
      });
      return;
    }
    $('liveViewer').querySelectorAll('figure').forEach(node => node.remove());
    const generation = robotGeneration;
    for (const name of names) {
      const figure = document.createElement('figure'), image = document.createElement('img'), caption = document.createElement('figcaption');
      figure.dataset.cameraRole = name;
      const label = topWithGrippers ? cameraLabel(name) : name;
      image.alt = label + ' robot camera'; caption.textContent = label + ' · Connecting';
      const picture = document.createElement('div');
      picture.className = 'modelCameraPicture'; picture.append(image);
      addModelBadge(picture, name);
      figure.append(picture, caption); $('liveViewer').prepend(figure);
      const update = async () => {
        if (generation !== robotGeneration || epoch !== cameraEpoch || ended) return;
        const pending = new Image();
        const url = '/api/monitor/cameras/' + encodeURIComponent(name) + '?robot_id=' + encodeURIComponent(selectedRobot) + '&t=' + Date.now();
        let delay = 200, timer;
        try {
          await new Promise((resolve, reject) => {
            timer = setTimeout(() => reject(new Error('Camera timeout')), 5000);
            pending.onload = resolve; pending.onerror = reject; pending.src = url;
          });
          await pending.decode();
          if (generation !== robotGeneration || epoch !== cameraEpoch || ended) return;
          image.src = pending.src;
          caption.textContent = label + ' · Live';
        } catch {
          // Keep the last successful frame visible while reconnecting.
          if (generation === robotGeneration && epoch === cameraEpoch) caption.textContent = label + (image.hasAttribute('src') ? ' · Reconnecting (last frame)' : ' · Connecting');
          delay = 1000;
        } finally {
          clearTimeout(timer); pending.onload = pending.onerror = null;
        }
        if (generation === robotGeneration && epoch === cameraEpoch && !ended) setTimeout(update, delay);
      };
      update();
    }
  }
  async function refreshRobotAvailability() {
    try {
      const catalog = await api('/api/robots');
      robotCatalog = catalog;
      const selector = $('robotSelector');
      catalog.robots.sort((a,b) => Number(b.connected === true) - Number(a.connected === true));
      for (const robot of catalog.robots) {
        const option = [...selector.options].find(option => option.value === robot.id);
        if (option) selector.append(option);
      }
      for (const option of [...selector.options]) if (!option.value) selector.append(option);
      selector.value = selectedRobot;
      const current = catalog.robots.find(robot => robot.id === selectedRobot);
      if (typeof current?.connected === 'boolean') updateCameraAvailability(current.connected);
    } catch { /* Keep current selection and imagery when availability is unknown. */ }
  }
  setInterval(refreshRobotAvailability, 5000);
  async function loadRobotSelector() {
    const selector = $('robotSelector');
    try {
      robotCatalog = await api('/api/robots');
      if (!robotCatalog.robots.some(robot => robot.id === selectedRobot)) {
        if (window.yamApplication?.defaultRobot) throw new Error('Selected robot unavailable');
        selectedRobot = robotCatalog.selected || robotCatalog.robots[0]?.id || '';
      }
      robotCatalog.robots.sort((a,b) => Number(b.connected === true) - Number(a.connected === true));
      if (!window.yamApplication?.defaultRobot && !new URLSearchParams(location.search).get('robot_id')) {
        selectedRobot = robotCatalog.robots.find(robot => robot.id === 'yam-1' && robot.connected === true)?.id
          || robotCatalog.robots.find(robot => robot.connected === true)?.id || selectedRobot;
      }
      selector.replaceChildren(...robotCatalog.robots.map(robot =>
        Object.assign(document.createElement('option'), {value: robot.id, textContent: robot.id === 'robot-abecb4cd868ab24b' ? 'SO101' : robot.name})));
      if (!robotCatalog.robots.some(robot => robot.hardware === 'makerarm')) {
        selector.append(Object.assign(document.createElement('option'), {value: '', textContent: 'MakerMods MakerArm — setup pending', disabled: true}));
      }
      selector.value = selectedRobot;
      window.dispatchEvent(new CustomEvent('blupe-robot-selected', {detail:selectedRobot}));
      selector.disabled = robotCatalog.robots.length < 2;
      selector.onchange = async () => {
        if (submitting) { selector.value = selectedRobot; return; }
        if (window.yamApplication?.selectRobot?.(selector.value)) return;
        selectedRobot = selector.value; robotGeneration++;
        camerasDisconnected = robotCatalog.robots.find(robot => robot.id === selectedRobot)?.connected === false;
        const robotUrl = new URL(location.href); robotUrl.searchParams.set('robot_id', selectedRobot);
        window.history.replaceState(null, '', robotUrl);
        window.dispatchEvent(new CustomEvent('blupe-robot-selected', {detail:selectedRobot}));
        csrf = ''; active = false; ended = false; lastChatSnapshot = '';
        $('apiKey').value = ''; $('robotSelectorStatus').textContent = 'Connecting…';
        renderRobotStatus('unknown');
        $('queue').replaceChildren();
        $('astraStreamOutput').replaceChildren();
        $('astraStreamState').textContent = 'Connecting';
        document.querySelectorAll('[data-camera]').forEach(video => video.yamStop?.());
        buttons();
        await start();
      };
    } catch { $('robotSelectorStatus').textContent = 'Robot list unavailable'; }
  }
  async function start() {
    if (!robotCatalog) await loadRobotSelector();
    try {
      const session = await api('/api/session', {}); csrf = session.csrf; ended = false;
      $('openCodexInstructions').hidden = !!session.local_runner;
      $('conversationSharingSettings').hidden = !session.local_runner;
      $('shareConversation').checked = session.share_conversation !== false;
      conversationSharingAllowed = session.share_conversation !== false;
      $('shareConversation').disabled = !conversationSharingAllowed;
      $('useApiDepth').dataset.available = session.local_runner && session.api_depth?.available ? 'true' : '';
      $('useApiDepth').checked = session.api_depth?.enabled === true;
      aspireRunAvailable = aspireConfiguredRun(session);
      $('apiDepthSettings').hidden = !$('useApiDepth').dataset.available;
      configureLocalRunDialog(!!session.local_runner);
      $('provider').innerHTML = originalProviderMarkup;
      window.yamAnalytics?.init(session.simulation, session.paid_runs);
      $('simulationNotice').hidden = !session.simulation;
      const astra = $('provider').querySelector('[value="astra"]');
      astra.hidden = astra.disabled = !session.astra_enabled;
      if (session.local_runner) {
        if (session.codex) {
          const option = document.createElement('option');
          option.value = 'codex'; option.textContent = 'Codex subscription · Astra';
          if (!$('provider').querySelector('[value=codex]')) $('provider').prepend(option);
          $('codexStatus').textContent = session.codex.message;
        }
        if (session.claude) {
          claudeModel = session.claude_model || claudeModel;
          const option = document.createElement('option');
          option.value = 'claude'; option.textContent = 'Claude subscription · Opus';
          if (!$('provider').querySelector('[value=claude]')) $('provider').prepend(option);
          $('claudeStatus').textContent = session.claude.message;
        }
        // The hosted "run it locally" walkthrough is redundant inside the local runner.
        $('openCodexInstructions').hidden = true;
        $('runAstra').hidden = !session.codex;
        $('runClaude').hidden = !session.claude;
        if(aspireRunAvailable) $('provider').prepend(Object.assign(document.createElement('option'), {value:'aspire',textContent:'ASPIRE · Codex/Astra'}));
        $('provider').value = aspireRunAvailable && (!session.default_provider || session.default_provider === 'codex')
          ? 'aspire' : session.default_provider || (session.codex ? 'codex' : 'claude');
        $('runnerName').value ||= aspireRunAvailable ? 'Aspire Code integration Map' : 'Local runner';
        document.title = 'BluPe · Local runner';
        providerChanged();
      }
      await window.yamApplication?.sessionReady?.(session, applicationContext());
      if (session.joint_policy) {
        const selected = $('provider').value;
        const allowed = new Set(['openai', ...(session.codex ? ['codex'] : []),
          ...(session.claude_supported ? ['anthropic', ...(session.claude ? ['claude'] : [])] : [])]);
        $('provider').replaceChildren(...Array.from($('provider').options).filter(option => allowed.has(option.value)));
        if (allowed.has(selected)) $('provider').value = selected;
        $('runClaude').hidden = !session.claude_supported; $('runAstra').hidden = true;
        providerChanged();
      }
      $('runGroot').hidden = !session.groot_enabled;
      if (session.groot_enabled) $('provider').append(Object.assign(document.createElement('option'), {value:'groot',textContent:'GR00T'}));
      if (session.local_runner) { $('runAstra').hidden = true; $('runClaude').hidden = true; $('runGroot').hidden = true; }
      buttons(); if (!new URLSearchParams(location.search).has('purchase') && !$('message').textContent.startsWith('Payment')) message('');
      camerasDisconnected = robotCatalog?.robots.find(robot => robot.id === selectedRobot)?.connected === false;
      modelCameraNames = new Set(session.model_cameras || []);
      videoStream = session.video_stream === undefined
        ? (selectedRobot === 'yam-1' ? {path:'synchronized', cameras:['top','observer','left','right']} : null)
        : session.video_stream;
      selectedCameras(session.cameras || ['left','top','right']);
      render(await api('/api/status'));
      try { await refreshChat(); } catch (error) { $('chatStatus').textContent = 'Chat unavailable'; }
      $('robotSelectorStatus').textContent = '';
      if (!pollingStarted) { pollingStarted = true; poll(); }
    } catch (error) { message(error.message, true); }
  }
  let chatSending = false, lastChatSnapshot = '';
  async function refreshChat() {
    if (!csrf || ended) return;
    const data = await api('/api/chat');
    const list = $('chatMessages');
    const snapshot = JSON.stringify([data.visitor, data.messages]);
    if (snapshot === lastChatSnapshot) {
      if (!chatSending) $('chatStatus').textContent = 'Shared with all visitors';
      return;
    }
    const bottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    const scrollTop = list.scrollTop;
    list.replaceChildren();
    for (const item of data.messages) {
      const li = document.createElement('li'), name = document.createElement('strong');
      name.textContent = item.name + (item.visitor === data.visitor ? ' (you)' : '');
      const timestamp = document.createElement('time'), sent = new Date(item.at * 1000);
      timestamp.dateTime = sent.toISOString();
      timestamp.textContent = sent.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
      timestamp.title = sent.toLocaleString();
      name.append(document.createTextNode(' · '), timestamp);
      li.append(name, document.createTextNode(item.text)); list.append(li);
    }
    while (list.children.length > 100) list.firstElementChild.remove();
    list.scrollTop = bottom ? list.scrollHeight : scrollTop;
    lastChatSnapshot = snapshot;
    if (!chatSending) $('chatStatus').textContent = 'Shared with all visitors';
  }
  async function pollChat() {
    if (ended) { $('chatSend').disabled = true; $('chatStatus').textContent = 'Reload to reconnect to chat.'; return; }
    if (csrf && !document.hidden) {
      try { await refreshChat(); } catch (error) { $('chatStatus').textContent = error.message; }
    }
    $('chatSend').disabled = !csrf || ended || chatSending;
    setTimeout(pollChat, 2000);
  }
  $('chatForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!csrf || ended || chatSending) return;
    chatSending = true; $('chatSend').disabled = true;
    try {
      await api('/api/chat', {name: $('chatName').value.trim(), text: $('chatText').value.trim()});
      $('chatText').value = ''; $('chatStatus').textContent = 'Sent'; await refreshChat();
    } catch (error) { $('chatStatus').textContent = error.message; }
    finally { chatSending = false; $('chatSend').disabled = ended; }
  });
  pollChat();
  start();
})();

(() => {
  const panel = document.getElementById('visitorChatPanel');
  const open = document.getElementById('openVisitorChat');
  const close = document.getElementById('closeVisitorChat');
  if (!panel || !open || !close) return;
  function toggle(visible, userAction = true) {
    panel.hidden = !visible;
    open.hidden = false;
    panel.closest('main').classList.toggle('chatClosed', !visible);
    open.setAttribute('aria-expanded', String(visible));
    close.setAttribute('aria-expanded', String(visible));
    if (userAction) {
      try { localStorage.setItem("yam-chat-open", String(visible)); } catch (_) {}
      (visible ? close : open).focus();
    }
  }
  try { toggle(localStorage.getItem("yam-chat-open") === "true", false); } catch (_) { toggle(false, false); }
  close.addEventListener('click', () => toggle(false));
  open.addEventListener('click', () => toggle(panel.hidden));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) toggle(false);
  });
})();

/* ASPIRE LOCAL SNAPSHOT START */
window.yamAspireLineageSnapshot = null;
/* ASPIRE LOCAL SNAPSHOT END */

// Display archived four-view recordings in the same camera hierarchy as live.
window.yamRecordingView = function(video, roles) {
  if (!Array.isArray(roles) || roles.length !== 4 || !['observer','left','top','right'].every(role => roles.includes(role))) return;
  const canvas = document.createElement('canvas');
  canvas.className = 'pastRunComposite'; canvas.width = 960; canvas.height = 654;
  canvas.setAttribute('aria-hidden', 'true'); canvas.hidden = true;
  video.after(canvas);
  const context = canvas.getContext('2d', {alpha:false});
  let callback, stopped = false;
  function paint() {
    if (stopped || video.readyState < 2 || !video.videoWidth) return;
    const w = video.videoWidth/2, h = video.videoHeight/2;
    const draw = (role, x, y, width, height) => {
      const index = roles.indexOf(role);
      // Viewing exports add a 24px header above each 360px camera image.
      context.drawImage(video, (index%2)*w, Math.floor(index/2)*h + h*60/384,
        w, h*324/384, x,y,width,height);
    };
    draw('observer',0,0,960,486);
    ['left','top','right'].forEach((role,index) => draw(role,index*323,495,314,159));
    canvas.hidden = false;
    video.classList.add('recordingSource');
    if (!video.paused && !document.hidden) callback = video.requestVideoFrameCallback
      ? video.requestVideoFrameCallback(paint) : requestAnimationFrame(paint);
  }
  function cancel() {
    if (video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(callback);
    else cancelAnimationFrame(callback);
    callback = undefined;
  }
  function start() { cancel(); paint(); }
  for (const event of ['loadeddata','seeked','play']) video.addEventListener(event, start);
  video.addEventListener('pause', cancel);
  const visibility = () => { if (document.hidden) video.pause(); };
  document.addEventListener('visibilitychange', visibility);
  const observer = new IntersectionObserver(entries => { if (!entries[0].isIntersecting && !video.controls) video.pause(); });
  observer.observe(canvas);
  return () => {
    stopped = true; cancel(); observer.disconnect(); canvas.remove();
    video.classList.remove('recordingSource');
    for (const event of ['loadeddata','seeked','play']) video.removeEventListener(event, start);
    video.removeEventListener('pause', cancel);
    document.removeEventListener('visibilitychange', visibility);
  };
};

// Compact controls delegate to the existing run lifecycle and settings dialog.
(() => {
  const $ = id => document.getElementById(id);
  if (!$('composerSend')) return;
  const chat = $('liveConversationPanel'), heading = document.querySelector('.taskChatHeading');
  const toolbar = document.querySelector('.watchToolbar');
  heading.after(toolbar);
  chat.prepend(document.querySelector('.videoOverlays'));
  document.querySelector('.cameraStatusLine').append($('videoDelayNotice'));
  const archive = $('past-runs'), dataset = $('dataset');
  if (archive && dataset) dataset.before(archive);
  const provider = $('provider'), model = $('composerModel'), send = $('composerSend');
  function updateComposer() {
    const options = [...provider.options].filter(option => !option.hidden && !option.disabled);
    const signature = options.map(option => option.value + ':' + option.textContent).join('|');
    if (model.dataset.options !== signature) {
      model.replaceChildren(...options.map(option => new Option(option.textContent, option.value)));
      model.dataset.options = signature;
    }
    model.value = provider.value;
    if (model.disabled !== provider.disabled) model.disabled = provider.disabled;
    const running = $('runForm').classList.contains('runActive');
    const queued = !$('leaveQueue').hidden && !$('leaveQueue').disabled;
    send.dataset.running = String(running);
    const disabled = running ? (queued ? $('leaveQueue').disabled : $('stop').disabled) : $('run').disabled;
    if (send.disabled !== disabled) send.disabled = disabled;
    send.setAttribute('aria-label', running ? (queued ? 'Leave queue' : 'Stop run') : 'Send task');
    send.title = send.getAttribute('aria-label');
    const icon = running ? '■' : '↑';
    if (send.firstElementChild.textContent !== icon) send.firstElementChild.textContent = icon;
    const settingsDisabled = running || $('openLocalRun').disabled;
    if ($('composerSettings').disabled !== settingsDisabled) $('composerSettings').disabled = settingsDisabled;
  }
  model.addEventListener('change', () => {
    provider.value = model.value;
    provider.dispatchEvent(new Event('change', {bubbles:true}));
  });
  function openComposerSettings() {
    const launch = $('openLocalRun'), dialog = $('localRunDialog');
    if (launch.disabled) return;
    launch.click();
    // Hosted model hooks can replace the launch button's original handler.
    // Keep the shared settings accessible without submitting the form.
    if (!dialog.open) {
      const settings = $('runSettings');
      if (settings.parentElement !== $('localRunDialogBody')) $('localRunDialogBody').append(settings);
      settings.open = true;
      dialog.showModal(); provider.focus();
    }
  }
  $('composerSettings').addEventListener('click', openComposerSettings);
  send.addEventListener('click', () => {
    if ($('runForm').classList.contains('runActive')) {
      (!$('leaveQueue').hidden && !$('leaveQueue').disabled ? $('leaveQueue') : $('stop')).click();
    } else if (!$('runForm').checkValidity()) openComposerSettings();
    else $('run').click();
  });
  const prompt = $('prompt');
  function sizePrompt() { prompt.style.height = 'auto'; prompt.style.height = Math.min(120, Math.max(24, prompt.scrollHeight)) + 'px'; }
  prompt.addEventListener('input', sizePrompt);
  prompt.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); if (!send.disabled) send.click();
    }
  });
  new MutationObserver(updateComposer).observe($('runForm'), {subtree:true, childList:true, attributes:true, attributeFilter:['disabled','hidden','class']});
  new MutationObserver(updateComposer).observe($('liveRunControls'), {subtree:true, attributes:true, attributeFilter:['disabled','hidden']});
  new MutationObserver(updateComposer).observe($('leaveQueue'), {attributes:true, attributeFilter:['disabled','hidden']});
  const workspace = document.querySelector('.watchLayout'), visuals = document.querySelector('.watchVisuals');
  function fitCameras() {
    const viewer = $('liveViewer'), four = viewer.dataset.layout === 'observer-focus';
    const aspect = four ? 640/324 : 16/9;
    if (window.innerWidth <= 740) {
      const width = visuals.clientWidth;
      workspace.style.removeProperty('height');
      workspace.style.removeProperty('--console-camera-width');
      workspace.style.setProperty('--console-camera-height', (four ? width/aspect + (width-12)/aspect/3 + 6 : width/aspect) + 'px');
      viewer.dataset.detailLayout = 'below'; return;
    }
    const documentTop = workspace.getBoundingClientRect().top + window.scrollY;
    workspace.style.height = Math.max(160, window.innerHeight - documentTop - 18) + 'px';
    const height = Math.max(100, workspace.clientHeight - document.querySelector('.cameraStatusLine').offsetHeight - $('yourQueue').offsetHeight - 12);
    const available = Math.max(100, workspace.clientWidth - Math.max(320, workspace.clientWidth*.25) - 16);
    const side = four && available/height > 2.35;
    const ideal = four ? side ? (height*4/3 - 4)*aspect + 6 : (height-6+4/aspect)*aspect*3/4 : height*aspect;
    const width = Math.min(available, ideal);
    const cameraHeight = four ? side ? ((width-6)/aspect + 4)*3/4 : width/aspect + (width-12)/aspect/3 + 6 : width/aspect;
    workspace.style.setProperty('--console-camera-width', width + 'px');
    workspace.style.setProperty('--console-camera-height', cameraHeight + 'px');
    viewer.dataset.detailLayout = side ? 'side' : 'below';
  }
  const sizing = new ResizeObserver(fitCameras);
  sizing.observe(workspace); sizing.observe($('yourQueue')); sizing.observe(document.querySelector('.cameraStatusLine'));
  if ($('collectionProgress')) sizing.observe($('collectionProgress'));
  new MutationObserver(fitCameras).observe($('liveViewer'), {attributes:true,attributeFilter:['data-layout']});
  provider.addEventListener('change', updateComposer);
  window.addEventListener('resize', fitCameras);
  updateComposer(); sizePrompt(); fitCameras();
})();

// Keep routing feedback visible alongside the actual provider's normal notes.
window.yamPolicyRoute = function(route) {
  const box = document.getElementById('policyRouteNotice');if(!box) return;
  const message = route?.message || '';
  const development = route?.development_url === 'https://github.com/andlyu/blupe-remote-yam' ? route.development_url : '';
  const key = JSON.stringify([message,development]);
  if(window.yamPolicyRoute.lastBox === box && window.yamPolicyRoute.lastKey === key) return;
  window.yamPolicyRoute.lastBox=box;window.yamPolicyRoute.lastKey=key;
  box.hidden = !message;box.replaceChildren();
  if(!route?.message) return;
  box.append(document.createTextNode(route.message));
  if(route.development_url === 'https://github.com/andlyu/blupe-remote-yam') {
    const link=document.createElement('a');link.href=route.development_url;
    link.target='_blank';link.rel='noopener';link.textContent=' Open repository';box.append(link);
  }
};

function routedConversationRun(run,state) {
  const route=state?.provider?.launch_route;
  if(!route || !['queued','preparing','running'].includes(state.status)) return run;
  return {...(run?.attempt_id === state.attempt_id ? run : {}),
    task:route.prompt,runner_name:state.runner_name,status:state.status,attempt_id:state.attempt_id,
    model_name:route.actual_policy === 'astra' ? 'Astra' : state.provider.display_name || 'ASPIRE · Codex/Astra',
    events:state.interactions?.events || [],launch_route:route};
}

function aspireConfiguredRun(session) {
  // Only the configured ASPIRE station publishes this segmentation metadata.
  return !!(session?.local_runner && session.codex && session.api_depth?.available && session.segmentation?.backend);
}

function aspireLineageGroups(trace = {}) {
  const used = trace.used || [], created = (trace.new_skills || []).filter(item => item.creation === 'recorded_code');
  return {used, retrievedOnly: (trace.retrieved || []).filter(item => !used.some(use => use.id === item.id && use.version === item.version)),
    created, newTitle: 'New skills added', pending: created.length === 0, unknown: trace.usage_recorded !== true};
}
function publicModelRun(run) {
  if(!run || !Array.isArray(run.events)) return run;
  const prefix='PUBLIC_MODEL_EVENT_V1\n';
  return {...run,events:run.events.flatMap(event => {
    if(event.speaker !== 'Tool' || event.kind !== 'model_request' ||
        typeof event.message !== 'string' || !event.message.startsWith(prefix)) return [event];
    try {
      const value=JSON.parse(event.message.slice(prefix.length));
      if(event.message.length > 8000 || value.task !== run.task ||
          !['model_progress','model_error'].includes(value.kind) ||
          value.kind === 'model_progress' && !['summary','status'].includes(value.progress_type) ||
          typeof value.message !== 'string' || value.message.length > 4000 ||
          !Number.isFinite(value.timestamp)) return [];
      return [{id:event.id,kind:value.kind,progress_type:value.progress_type,message:value.message,timestamp:value.timestamp}];
    } catch(_) {return [];}
  })};
}
function aspireSharedRun(run) {
  run=publicModelRun(run);
  if(!run || run.task_progress || !Array.isArray(run.events)) return run;
  const prefix='ASPIRE_TASK_PROGRESS_V1\n';
  let progress=null;
  const events=run.events.filter(event => {
    // Only a publisher's tool-role message can carry native status. A model
    // reply or user prompt containing JSON must never become task evidence.
    if(event.speaker !== 'Tool' || event.kind !== 'model_request' ||
        typeof event.message !== 'string' || !event.message.startsWith(prefix)) return true;
    try {
      const value=JSON.parse(event.message.slice(prefix.length)).task_progress;
      const object=item=>item && typeof item === 'object' && !Array.isArray(item);
      if(event.message.length > 8000 || !object(value) || value.task !== run.task ||
          !Array.isArray(value.updates) || !value.updates.every(row=>object(row) &&
            ['happened','changed','next_action'].every(key=>typeof row[key] === 'string')) ||
          value.attempts && (!Array.isArray(value.attempts) || !value.attempts.every(object)) ||
          value.lineage && (!object(value.lineage) ||
            !['used','retrieved','new_skills'].every(key=>Array.isArray(value.lineage[key]) && value.lineage[key].every(object))) ||
          ['resolution','outcome','stage','vision'].some(key=>value[key] && !object(value[key]))) return true;
      progress=value;
      return false;
    } catch(_) {return true;}
  });
  return progress ? {...run,task_progress:progress,events} : run;
}
function aspireSam3Progress(run, now = Date.now()) {
  if(!['queued','preparing','running'].includes(run?.status)) return null;
  const vision = run.task_progress?.vision;
  if(vision?.model !== 'facebook/sam3' || !['starting','ready','failed'].includes(vision.state)) return null;
  const end = vision.state === 'starting' ? now/1000 : vision.state === 'ready' ? vision.ready_at : vision.ended_at;
  const elapsed = Number.isFinite(vision.started_at) && Number.isFinite(end)
    ? Math.max(0,Math.floor(end-vision.started_at)) : null;
  if(vision.state === 'starting') return {state:'starting',title:'Starting SAM 3 vision',
    timer:elapsed === null ? '' : `${elapsed}s elapsed`,
    detail:'The vision worker is waking up. Task motion waits for vision and the complete plan.'};
  if(vision.state === 'ready') return {state:'ready',title:'SAM 3 vision ready',
    timer:elapsed === null ? '' : `Startup took ${elapsed}s`,detail:'Vision startup is complete.'};
  return {state:'failed',title:'SAM 3 vision unavailable',
    timer:elapsed === null ? '' : `Stopped after ${elapsed}s`,
    detail:'The vision worker reported an error. See the task error for details.'};
}
function aspireStageProgress(run) {
  const stage=run?.task_progress?.stage;
  if(!stage || (!['queued','preparing','running'].includes(run.status) && !stage.active)) return null;
  const elapsed=Number.isFinite(stage.elapsed_s) ? Math.floor(stage.elapsed_s) : 0;
  return {title:stage.title,timer:`${elapsed}s in this stage`,
    detail:stage.detail+(stage.last_failure ? ' Latest candidate rejection: '+stage.last_failure : '')};
}
function aspireAttemptUpdates(attempts = []) {
  return attempts.map(attempt => ({happened:(attempt.mode === 'offline_rerun_diagnosis' ? 'Justified unchanged-code rerun' : attempt.mode === 'offline_code_repair' ? 'Astra repair' : attempt.policy === 'astra' ? 'Astra recovery' : attempt.policy === 'codex_local' ? 'Local Codex repair' : 'Saved program')+
      ' · '+attempt.id+' · '+attempt.status,
    changed:attempt.parent_attempt_id ? 'Follows '+attempt.parent_attempt_id+'. '+
      ((attempt.reason || attempt.result?.reason) ?
        (attempt.reason || attempt.result.reason)+(attempt.diagnosis && attempt.diagnosis !== (attempt.reason || attempt.result.reason) ? ' Astra diagnosis: '+attempt.diagnosis : '') :
        attempt.diagnosis || attempt.outcome?.summary || attempt.outcome?.reason ||
        (attempt.mode === 'offline_rerun_diagnosis' ? 'The unchanged effective program is a justified retry, not a new code fix.' : attempt.mode === 'offline_code_repair' ? 'Revised code is retained separately from the original outcome.' : 'Fresh observations; saved source is unchanged.')) :
      (attempt.result?.reason || 'Source SHA256 '+(attempt.source_sha256 || 'unrecorded')),
    next_action:attempt.policy === 'astra' ? 'Astra outcome is separate; physical success requires observed verification.' :
      'This attempt and its exact source remain in history, including any later recovery.'}));
}
function aspireModelUpdates(events = []) {
  return events.filter(event => event.kind === 'model_progress' &&
    ['summary','status'].includes(event.progress_type || event.details?.progress_type) ||
    event.kind === 'model_response' && typeof (event.details?.summary || event.message) === 'string' &&
      !/^\w+:\s*\{/.test(event.details?.summary || event.message))
    .map(event => ({timestamp:event.timestamp,
      happened:event.kind === 'model_response' ? 'Model response summary.' :
        (event.progress_type || event.details?.progress_type) === 'summary' ? 'Public model summary.' : 'Model status.',
      changed:String(event.details?.summary || event.message || '').slice(0,4000),
      next_action:'Native planning, execution and observed outcomes are recorded separately.'}));
}
function aspireAttemptTrace(attempt,events = []) {
  if(Array.isArray(attempt.trace)) return attempt.trace.filter(e=>e.attempt_id === attempt.id);
  if(!Number.isFinite(attempt.started_at)) return [];
  // Compatibility with an already-running backend: only the existing public
  // summary projection, bounded to this repair's own request window.
  const rows=events.filter(e=>e.timestamp>=attempt.started_at && e.timestamp<=(attempt.ended_at || Infinity));
  const requests=rows.filter(e=>e.kind === 'model_request');
  if(!requests.length || requests.some(e=>e.message !== 'Generate/revise an ASPIRE program')) return [];
  return rows.filter(e=>e.kind === 'model_progress' &&
    ['summary','status'].includes(e.progress_type || e.details?.progress_type) &&
    (!e.details?.attempt_id || e.details.attempt_id === attempt.id))
    .map(e=>({id:e.id,timestamp:e.timestamp,kind:e.kind,message:String(e.message || '').slice(0,4000),
      progress_type:e.progress_type || e.details.progress_type,attempt_id:attempt.id}));
}
function aspireTaskAnswer(run = {},item = {}) {
  const progress=run.task_progress || {};
  if(progress.resolution || item.resolution) return progress.resolution || item.resolution;
  if(progress.outcome?.success === true || item.after_parking_success === true)
    return {state:'verified',title:'Task success verified',detail:'Fresh after-parking checks confirmed the task.',next_action:'No action needed.'};
  if(['preparing','queued','running'].includes(run.status) || progress.stage?.active)
    return {state:'recovering',title:progress.stage?.stage === 'offline_repair' ? 'Diagnosing and repairing the task' : 'Task in progress',
      detail:'Physical task success has not been verified.',next_action:'Current work and elapsed time appear below.'};
  if(progress.outcome || item.review_status || item.native_status)
    return {state:'unresolved',title:'Task remains unverified',detail:progress.outcome?.reason || 'Planning and repair status do not confirm physical success.',
      next_action:'Review the failed or unknown checks. A scene reset requirement has not been established.'};
  return null;
}
function aspireRetryLimit(value) {
  if(!/^[0-3]$/.test(String(value))) throw new RangeError('Automatic retry limit must be a whole number from 0 to 3.');
  return Number(value);
}
function aspirePromptRoute(prompt, catalog) {
  const color = '(?:red|green|blue|black|white|yellow|orange|purple)';
  const match = String(prompt).trim().toLowerCase().match(new RegExp('^(?:pick\\s+up|pick|move|place|put)\\s+(?:the\\s+|a\\s+)?('+color+')\\s+(?:(?:rectangular|cuboid)\\s+)?block\\s+(?:(?:and\\s+)?(?:place|put|move)\\s+it\\s+)?(?:onto|on|to)\\s+(?:a\\s+clear\\s+patch\\s+of\\s+)?(?:the\\s+|a\\s+)?('+color+')?\\s*((?:round\\s+)?(?:poker\\s+)?chip|towel)\\s*\\.?$'));
  const exact=(catalog?.saved_program_tasks || []).find(p=>p.task.trim().toLowerCase() === String(prompt).trim().toLowerCase());
  const compatible = !!exact || !!(match && catalog?.executable?.source_integrity &&
    (match[3].includes('chip') ? match[2] : !match[2] || match[2] === 'green'));
  const generate=catalog?.execution_environment === 'local' && !compatible;
  return {compatible,generate,exact,title: compatible ? 'Saved program candidate' : generate ? 'Generate an ASPIRE program' : 'Run with Astra instead',
    reason: exact ? 'An exact-task saved program matches the complete request. Fresh live capture and full planning follow Home.' : compatible ? 'The supported block-placement request can bind fresh source and destination inputs to '+catalog.executable.skill+'. The full live plan still checks scene compatibility.' : generate ?
      'No usable saved program is identified. ASPIRE will generate Python and validate a complete native plan after Run, before joining the queue.' :
      'No saved ASPIRE program is identified by this preview. The full prompt will run through normal Astra. Reusable ASPIRE code can be developed locally using your own Astra subscription.'};
}
function aspireProgramAttribution(trace = {}) {
  const author = trace.authorship || {};
  if (author.generated_by_codex === true) return author.mode === 'reuse' ? 'Reusing code generated by Codex' : 'Generated by Codex';
  return 'Program authorship not recorded';
}
function aspireRecordedUpdates(item) {
  if (item.task_updates?.length) {
    if(item.verification_error && !item.task_updates.some(update => update.changed?.includes(item.verification_error)))
      return [...item.task_updates,{happened:'Post-parking verification failed.',changed:item.verification_error,
        next_action:'Review the recorded detection failure; it does not establish that placement failed or a reset is needed.'}];
    return item.task_updates;
  }
  const updates = [];
  for (const attempt of item.attempts || []) {
    const trace = attempt.lineage || {}, used = trace.used || [];
    const revision = used.find(skill => skill.core_revision)?.core_revision;
    if(revision) updates.push({happened:'Saved executable repaired before this reuse.',
      changed:(revision.reason || 'Reason unrecorded')+' Core '+revision.old_core_sha256?.slice(0,12)+' → '+revision.new_core_sha256?.slice(0,12)+'.',
      next_action:'This run reused the repaired body; inspect the recorded repair for its planning evidence.'});
    if (used.some(skill => skill.verification === 'exact_source_match')) updates.push({
      happened:'Saved code reused for this task.', changed:used.map(skill => skill.changed).filter(Boolean).join(' ') || 'Exact saved source matched.',
      next_action:'Recorded program SHA256 '+(attempt.source_sha256?.slice(0,12) || 'unknown')+'; exact code is available below.'});
    else updates.push({happened:'Program '+attempt.attempt+' is recorded.',
      changed:attempt.code_revision_reason?.reason || 'Earlier use and code changes were not explicitly recorded.',
      next_action:'Inspect this attempt’s saved code and scoped evidence.'});
    if (attempt.plan?.status || attempt.plan?.planning_success != null) updates.push({
      happened:attempt.plan.planning_success === true ? 'Complete native plan passed.' : 'Native planning result: '+(attempt.plan.status || 'failed')+'.',
      changed:attempt.plan.reason || 'No further explanation recorded in this result.',
      next_action:attempt.plan.planning_success === true ? 'Planning establishes a plan; physical outcome is a separate record.' : 'Any later revision is shown in the next recorded attempt.'});
    if (attempt.execution?.status) updates.push({happened:'Live harness: '+attempt.execution.status+'.',
      changed:attempt.execution.reason || 'Recorded task-motion calls: '+(attempt.execution.physical_motion_calls ?? 'unknown')+'.',
      next_action:'Inspect the after-parking evidence separately.'});
  }
  if (item.review_status) updates.push({happened:item.visual_review_success === true ? 'Visual placement retained after parking.' : 'After-parking review: '+item.review_status.replaceAll('_',' ')+'.',
    changed:item.after_parking_success === true ? 'Automated after-parking checks passed for this episode.' : item.verification_error || 'Automated after-parking outcome remains unverified. The review and images are available below.',
    next_action:item.postpark_checks && Object.values(item.postpark_checks).some(value => value === false) ?
      'Failed automated checks remain unresolved: '+Object.entries(item.postpark_checks).filter(([,v]) => v === false).map(([k]) => k).join(', ')+'.' :
      'This evidence applies to the recorded episode; future tasks need fresh measurements.'});
  return updates;
}
function aspireLiveTask(run,state) {
  run=aspireSharedRun(run);
  const route = state?.provider?.launch_route;
  if(route?.actual_policy === 'astra') return {...run,task:route.prompt,status:state.status,
    attempt_id:state.attempt_id,error:state.error ?? run?.error,launch_route:route,task_progress:null};
  const own = state?.provider?.task_progress;
  if(own) return {
    task:own.task || run?.task,status:state.status,task_progress:own,error:state.error,display_error:run?.display_error,
    run_id:state.interactions?.run_id,attempt_id:state.attempt_id,
    events:run?.events || state?.interactions?.events || []};
  if(state?.error && state.attempt_id && state.attempt_id === run?.attempt_id)
    run={...run,error:state.error};
  const preparing = (state?.whats_running || []).filter(item => item.status === 'preparing');
  if(preparing.length === 1 && !['preparing','running'].includes(run?.status)) {
    const current = preparing[0];
    return {...current,task_progress:{task:current.task,updates:[{
      happened:'Current task is preparing.',
      changed:'Detailed preparation updates are available in the submitting client.',
      next_action:'Waiting for shared progress for this request.'}]}};
  }
  return run;
}

function taskRunFailure(run) {
  if(!run) return null;
  const updates = run.task_progress?.updates || [];
  const isBlocked = update => /^Task blocked\.?$/.test(update?.happened || '');
  const blocked = [...updates].reverse().find(isBlocked)?.changed;
  if(!run.error && !['failed','timed_out','disconnected'].includes(run.status) &&
      !(run.status === 'stopped' && isBlocked(updates.at(-1)))) return null;
  const generic = 'The run encountered a model or runner error.';
  const reason = (run.error && run.error !== generic ? run.error : blocked || run.error) ||
    (run.status === 'timed_out' ? 'Run reached time limit' : 'The task ended without a recorded reason.');
  return {label:run.status === 'timed_out' || reason === 'Run reached time limit' ? 'Task timed out' : 'Task failed',
    reason:String(reason),task:run.task || run.task_progress?.task};
}


function aspireElapsed(seconds) {
  const value=Math.max(0,Math.floor(seconds));
  return value < 60 ? value+'s' : Math.floor(value/60)+'m '+value%60+'s';
}
function aspireLocalSetupUrl(run = {}) {
  const route = run.launch_route || {};
  const aspire = ['aspire', 'hosted_aspire'].includes(run.provider) || /^ASPIRE\b/i.test(run.model_name || '') ||
    route.requested_policy === 'aspire' || route.actual_policy === 'aspire' || !!run.task_progress?.lineage;
  return aspire ? 'https://github.com/andlyu/blupe-remote-yam/blob/main/codex-runner/docs/aspire/README.md' : '';
}
function aspireActivityEntries(run = {}) {
  const progress=run.task_progress || {},messages=[];
  const attempts=progress.attempts || [];
  const traces=new Map(attempts.map(attempt=>[attempt,aspireAttemptTrace(attempt,run.events)]));
  const progressType=event=>event.progress_type || event.details?.progress_type;
  const publicMessage=event=>String(event.details?.summary || event.message || '').slice(0,4000);
  const add=(id,role,title,message,extra={})=>messages.push({id,role,title,message:message || '',...extra});
  add('prompt','You','Task',progress.task || run.task || 'Preparing your task.');
  for(const [index,update] of (progress.updates || []).entries())
    add('update-'+index,'Runner',update.happened,update.changed,{timestamp:update.timestamp,next:update.next_action});
  // Public summaries arrive before planning and during hosted worker execution.
  const coding=run.launch_route?.code_generation_requested === true ||
    (progress.updates || []).some(u=>/generating task code|writing.*code/i.test(u.happened || ''));
  for(const [index,event] of (run.events || []).entries()) {
    if(coding && event.kind !== 'model_progress') continue;
    const update=aspireModelUpdates([event])[0];if(!update) continue;
    // Hosted snapshots omit attempt traces. Keep their separately published
    // public summaries even when they fall inside a recovery's time window.
    // Local traces already include these messages, so suppress only an actual
    // matching public row rather than every event during the attempt.
    if(attempts.some(attempt=>traces.get(attempt).some(row=>row.kind === event.kind &&
        progressType(row) === progressType(event) && publicMessage(row) === update.changed &&
        Number.isFinite(row.timestamp) && Number.isFinite(event.timestamp) &&
        Math.abs(row.timestamp-event.timestamp)<1))) continue;
    const recovering=attempts.some(attempt=>attempt.parent_attempt_id &&
      Number.isFinite(attempt.started_at) && event.timestamp>=attempt.started_at && event.timestamp<=(attempt.ended_at || Infinity));
    add('model-'+(event.id ?? index),'Astra',recovering ? 'Recovery update' : coding ? 'Code-writing update' : update.happened,
      update.changed.replace(/\*\*([^*]+)\*\*/g,'$1'),{timestamp:update.timestamp});
  }
  for(const attempt of attempts) {
    const repair=attempt.mode === 'offline_code_repair' || attempt.policy === 'codex_local';
    const rerun=attempt.mode === 'offline_rerun_diagnosis';
    const actor=attempt.policy === 'codex_local' ? 'Codex' : attempt.policy === 'astra' ? 'Astra' : 'Runner';
    const trace=traces.get(attempt);
    if(attempt.parent_attempt_id) add(attempt.id+'-start',actor,rerun ? 'Checking whether to rerun' : repair ? 'Repair started' : 'Recovery started',
      'Follows '+attempt.parent_attempt_id+'. '+(attempt.previous_result?.reason || attempt.code_revision_reason?.reason || ''),
      {timestamp:attempt.started_at || trace[0]?.timestamp,attempt:attempt.id});
    for(const event of trace) {
      const category=progressType(event);
      if(event.kind === 'model_progress' && !['summary','status'].includes(category)) continue;
      if(!['activity','model_progress','model_request','model_response','model_error','tool_request','tool_result','tool_error'].includes(event.kind)) continue;
      const model=event.kind.startsWith('model_'),error=['model_error','tool_error'].includes(event.kind);
      const title=event.kind === 'model_progress' ? category === 'summary' ? 'Recovery update' : 'Model status' :
        event.kind === 'model_response' ? 'Diagnosis and code response' : event.kind === 'model_request' ? 'Writing task code' :
        error ? 'Reported failure' : 'Activity';
      add(attempt.id+'-trace-'+event.id,model ? actor : 'Runner',title,
        publicMessage(event).replace(/\*\*([^*]+)\*\*/g,'$1'),
        {timestamp:event.timestamp,attempt:attempt.id,revision:event.revision,detail:event.changed,
          lesson:event.lesson,next:event.next_action,tone:error ? 'error' : ''});
    }
    const result=attempt.result || attempt.execution || attempt.plan || {};
    const status=attempt.status || result.status;
    if(status && !['running','starting','preparing','queued'].includes(status)) {
      const validated=['PLAN_VALIDATED','RERUN_VALIDATED'].includes(status),hash=attempt.source_sha256;
      const title=status === 'RERUN_VALIDATED' ? 'Unchanged-code rerun validated' : status === 'PLAN_VALIDATED' ?
        hash ? 'Revised code saved · plan passed' : 'Repair plan passed' : status === 'UNVERIFIED' ?
        'Execution finished · placement unverified' : status.replaceAll('_',' ');
      add(attempt.id+'-result',actor,title,attempt.reason || result.reason || attempt.diagnosis ||
        (validated ? 'Planning passed. Physical success still needs observed verification.' : 'This outcome remains in the task history.'),
        {timestamp:attempt.ended_at || trace.at(-1)?.timestamp,attempt:attempt.id,hash,
          detail:attempt.diagnosis && attempt.diagnosis !== (attempt.reason || result.reason) ? attempt.diagnosis : '',
          next:validated ? 'A fresh run and after-parking checks are still required.' : '',
          tone:status === 'FAILED' || status === 'PROGRAM_ERROR' || status === 'PLAN_FAILED' ? 'error' : ''});
    }
  }
  const outcome=progress.outcome,failure=taskRunFailure(run);
  if(outcome) add('parked-outcome','Runner',outcome.success === true ? 'Placement verified after parking' : 'After-parking result · '+outcome.status,
    outcome.reason,{timestamp:outcome.checked_at,tone:outcome.success === true ? 'success' : 'warning',
      localSetup:!failure && outcome.success !== true && !['queued','preparing','running'].includes(run.status) ? aspireLocalSetupUrl(run) : ''});
  if(failure) add('task-failure','Runner',failure.label,failure.task || 'Submitted task',
    {detail:failure.reason,next:'Review the error before starting another attempt.',tone:'error',timestamp:run.ended_at,localSetup:aspireLocalSetupUrl(run)});
  // Unknown timestamps retain source order; never invent a recorded time.
  const prompt=messages.shift();
  const sorted=messages.map((message,index)=>({...message,order:index}));
  let previous=0;
  for(const message of sorted) {if(Number.isFinite(message.timestamp)) previous=message.timestamp;message.sortAt=previous;}
  sorted.sort((a,b)=>a.sortAt-b.sortAt || a.order-b.order);
  return [prompt,...sorted];
}
function aspireRecordedActivity(item) {
  return {task:item.task,status:'stopped',task_progress:{resolution:item.resolution,updates:aspireRecordedUpdates(item),
    attempts:(item.attempts || []).map(attempt=>({...attempt,
      status:attempt.status || attempt.execution?.status || attempt.plan?.status,
      result:attempt.execution?.status ? attempt.execution : attempt.plan})),
    outcome:item.after_parking_success == null ? null : {success:item.after_parking_success,
      status:item.after_parking_success ? 'SUCCESS' : 'UNVERIFIED',reason:item.verification_error ||
        (item.after_parking_success ? 'Recorded after-parking checks passed.' : 'Recorded after-parking checks did not confirm placement.')}}};
}

function aspireCurrentSource(run = {}) {
  const progress=run.task_progress || {};
  const source=[...(progress.attempts || [])].reverse().find(attempt=>attempt.source_sha256 || attempt.previous_source_sha256);
  return source?.source_sha256 || source?.previous_source_sha256 || progress.lineage?.program?.source_sha256 || '';
}
function aspirePhasePath(run = {},active = false) {
  const progress=run.task_progress || {},stage=progress.stage?.stage || '',attempts=progress.attempts || [];
  const repair=attempts.some(a=>['offline_code_repair','offline_rerun_diagnosis'].includes(a.mode) || a.policy === 'codex_local') || stage === 'offline_repair';
  const labels=repair ? ['Run','Verify','Repair','Plan','Next'] : ['Prepare','Plan','Run','Verify','Next'];
  const trace=aspireAttemptTrace(attempts.at(-1) || {},run.events);
  const planning=stage === 'native_planning' || stage === 'offline_repair' && trace.at(-1)?.kind === 'tool_request' && /plan/i.test(trace.at(-1).message || '');
  let current=stage === 'execution' ? labels.indexOf('Run') : planning ? labels.indexOf('Plan') :
    stage === 'offline_repair' ? labels.indexOf('Repair') : /review|verif|parking/.test(stage) ? labels.indexOf('Verify') :
    run.status === 'running' ? labels.indexOf('Run') : repair ? labels.indexOf('Repair') : 0;
  if(!active) current=progress.resolution?.state === 'action_needed' || progress.outcome?.success === true ? 4 :
    ['PLAN_VALIDATED','RERUN_VALIDATED'].includes(attempts.at(-1)?.status) ? labels.indexOf('Plan') :
    progress.outcome ? labels.indexOf('Verify') : current;
  return labels.map((label,index)=>({label,state:index === current ? progress.resolution?.state === 'action_needed' ? 'hold' : active ? 'current' : 'stopped' : ''}));
}

function aspireWorkStatus(run = {},now = Date.now()/1000,lastPollAt = now) {
  const progress=run.task_progress || {},stage=progress.stage,attempts=progress.attempts || [];
  const current=attempts.at(-1),trace=current ? aspireAttemptTrace(current,run.events) : [];
  const active=progress.resolution?.state !== 'cancelled' &&
    (stage?.active === true || ['queued','preparing','running'].includes(run.status));
  const stamps=[stage?.event_at,stage?.started_at,...aspireActivityEntries(run).map(message=>message.timestamp),
    ...trace.map(e=>e.timestamp)].filter(Number.isFinite);
  const age=stamps.length ? Math.max(0,now-Math.max(...stamps)) : null;
  const disconnected=active && now-lastPollAt > 15;
  const waiting=age === null || age > 45;
  const verified=progress.outcome?.success === true || progress.resolution?.state === 'verified';
  const needsInput=!active && progress.resolution?.state === 'action_needed';
  const state=needsInput ? 'action' : disconnected ? 'connection' : active ? waiting ? 'waiting' : 'active' : verified ? 'verified' : 'stopped';
  const status=needsInput ? 'Needs your input' : disconnected ? 'Connection delayed' : active ? waiting ? 'Waiting for update' : 'Working' :
    verified ? 'Complete · verified' : 'Not working';
  const writing=stage?.stage === 'offline_repair' && ['model_request','model_progress'].includes(trace.at(-1)?.kind);
  const title=needsInput ? progress.resolution.title : active ? (writing ? 'Writing repair code' : stage?.title) || ({queued:'Waiting for the robot',preparing:'Preparing the task',running:'Running the task'}[run.status]) || 'Repairing the task' :
    verified ? 'Task complete' : progress.outcome ? 'Placement remains unverified' : taskRunFailure(run) ? 'Task failed' : 'Task stopped';
  const revision=[...trace].reverse().find(e=>e.revision)?.revision;
  const timer=active && stage ? `${Math.floor((stage.elapsed_s || 0)+Math.max(0,now-lastPollAt))}s in this stage` : '';
  const freshness=age === null ? 'No update time recorded' : 'Last update '+aspireElapsed(age)+' ago';
  return {state,status,title,timer,path:aspirePhasePath(run,active),source:aspireCurrentSource(run),meta:[current?.id,revision ? 'revision '+revision : '',freshness].filter(Boolean).join(' · '),
    detail:needsInput ? progress.resolution.detail : disconnected ? 'Live status has not refreshed for '+aspireElapsed(now-lastPollAt)+'. Current activity is unknown.' :
      active && waiting ? 'The runner reports this phase is active; no new progress is confirmed. '+(stage?.detail || '') :
      (stage?.detail || (verified ? 'Fresh after-parking checks passed.' : active ? 'No task work is currently reported.' : 'Physical success has not been verified.'))+
        (stage?.last_failure ? ' Latest candidate rejection: '+stage.last_failure : '')};
}

function aspireTaskWorking(run) {
  return ['queued','preparing','running'].includes(run?.status) || run?.task_progress?.stage?.active === true;
}

// ASPIRE task progress is shared; station library and recovery stay local.
(() => {
  const localAspire = ['127.0.0.1', 'localhost'].includes(location.hostname);
  const $ = id => document.getElementById(id);
  let lastPollAt=Date.now()/1000;
  let catalog, robot, liveRun, liveState, pendingAttempt, signature = '', selected = 'preview', fetchGeneration = 0, draftPreview = false;
  const node = (tag, text, className) => Object.assign(document.createElement(tag), {textContent:text || '', className:className || ''});
  const details = title => {const box = node('details', '', 'aspireDisclosure');box.append(node('summary',title));return box;};
  const setHidden = (element,value) => {if(element && element.hidden !== value) element.hidden=value;};
  const builtFromState = new Map();
  function builtFrom(key) {
    const box = details('Built from');box.dataset.aspireBuiltFrom = JSON.stringify([robot,key]);
    box.open = builtFromState.get(box.dataset.aspireBuiltFrom) === true;
    box.addEventListener('toggle',() => {if(box.isConnected) builtFromState.set(box.dataset.aspireBuiltFrom,box.open);});
    return box;
  }
  function rememberBuiltFrom(box) {
    // Read native state before replacement, even if its toggle event is still queued.
    for(const disclosure of box.querySelectorAll('details[data-aspire-built-from]'))
      builtFromState.set(disclosure.dataset.aspireBuiltFrom,disclosure.open);
  }
  const paragraph = (box,text,cls='') => box.append(node('p',text,cls));
  const code = (box,title,value) => {const d = details(title);d.append(node('pre',typeof value === 'string' ? value : JSON.stringify(value,null,2)));box.append(d);};
  const visionViews = new WeakMap();
  function renderVision(box,progress) {
    setHidden(box,!progress);
    const key=JSON.stringify(progress);
    if(box.dataset.visionKey === key) return;
    box.dataset.visionKey=key;
    if(!progress) return;
    let view=visionViews.get(box);
    if(!view) {
      const title=node('strong'),timer=node('p'),detail=node('p');
      title.setAttribute('role','status');title.setAttribute('aria-live','polite');
      timer.setAttribute('role','timer');timer.setAttribute('aria-live','off');
      box.append(title,timer,detail);view={title,timer,detail};visionViews.set(box,view);
    }
    for(const field of ['title','timer','detail'])
      if(view[field].textContent !== progress[field]) view[field].textContent=progress[field];
  }
  function renderFailure(box,failure,includeTask=false) {
    if(!box) return;
    setHidden(box,!failure);
    const key=JSON.stringify(failure);
    if(box.dataset.failureKey === key) return;
    box.dataset.failureKey=key;box.replaceChildren();
    if(!failure) return;
    box.append(node('strong',failure.label));
    if(includeTask && failure.task) paragraph(box,failure.task,'taskFailurePrompt');
    paragraph(box,failure.reason.length > 300 ? failure.reason.slice(0,297)+'…' : failure.reason);
    if(failure.reason.length > 300) code(box,'Exact error details',failure.reason);
    paragraph(box,'Review the error before starting another attempt.','help');
  }
  window.yamTaskFailure = (run,state={}) => {
    const task=aspireLiveTask(run,state),failure=taskRunFailure(task);
    const chat=$('aspireTaskPanel');
    const failureInChat=chat?.dataset.enabled === 'true' && !chat.hidden &&
      chat.dataset.failureKey === JSON.stringify(failure);
    renderFailure($('taskRunFailure'),failureInChat ? null : failure,true);
    // Routing describes the next live step only while a task is active.
    if(task && !['queued','preparing','running'].includes(task.status) && $('policyRouteNotice'))
      setHidden($('policyRouteNotice'),true);
    return failure;
  };
  const badge = (box,text) => box.append(node('span',text,'aspireBadge'));
  const url = value => value+(value.includes('?') ? '&' : '?')+'robot_id='+encodeURIComponent(robot);
  const link = (box,label,item) => {
    if (!localAspire || !item || !/^\/api\/aspire-lineage\/artifacts\/[a-f0-9]{64}$/.test(item.url)) return;
    const a = node('a',label);a.href = url(item.url);a.target = '_blank';a.rel = 'noopener';box.append(a);
  };
  function snippets(box,refs = []) {
    for (const ref of refs) {
      const d = details((ref.source || 'Recorded source')+' · lines '+ref.start_line+'–'+ref.end_line);
      paragraph(d,'Source SHA256: '+ref.source_sha256,'aspireSource');
      link(d,'Immutable recorded program used in this episode',ref.recorded_program);
      link(d,'Original source at the recorded hash',ref.original_artifact);
      if(ref.original_source_state === 'changed_or_unavailable') paragraph(d,'The original source path has changed or is unavailable. The exact saved code is preserved in the immutable recorded program.','aspireCaveat');
      if (ref.target_start_line) paragraph(d,'Program lines '+ref.target_start_line+'–'+ref.target_end_line+' · '+(ref.exact_match ? 'Exact text match' : 'Model-declared source linkage'));
      if (ref.original_code) code(d,'Original source lines',ref.original_code);
      if (ref.recipe_code || ref.code) code(d,'Generalized recipe / saved executable',ref.recipe_code || ref.code);
      if (ref.generalization) code(d,'Recorded generalization',ref.generalization);
      if (ref.derived_code) code(d,'Code used in this program',ref.derived_code);
      box.append(d);
    }
  }
  function episodeLink(box,id) {
    const episode = catalog?.episodes?.find(item => item.id === id);if (!episode) return;
    const a = node('a',episode.task || id);a.href = '#aspire-episode-'+id;
    a.addEventListener('click',() => {let target = $('aspire-episode-'+id);while(target) {if(target.tagName === 'DETAILS') target.open = true;target = target.parentElement;}});
    box.append(a);
  }
  function recipe(box,item) {
    const d = details(item.title || item.id);
    paragraph(d,item.id+' · version SHA256 '+item.version,'aspireSource');
    paragraph(d,item.why || item.trigger || '');paragraph(d,item.scope || '');
    if(item.statuses?.length) paragraph(d,'Evidence statuses: '+item.statuses.join(', '));
    for(const limit of item.limits || []) paragraph(d,limit,'aspireCaveat');
    snippets(d,item.snippets);paragraph(d,'Producing episodes');
    for(const id of item.episodes || []) episodeLink(d,id);box.append(d);
  }
  function lineage(trace = {},snapshots = [],key) {
    const root = builtFrom(key),groups = aspireLineageGroups(trace);
    const fresh = node('section','','aspireGroup');fresh.append(node('h3','New skills added'));
    if(trace.pending) {badge(fresh,'Matching saved programs');paragraph(fresh,catalog?.execution_environment === 'local' ? 'A saved match skips coding. Otherwise ASPIRE generates Python and validates a complete offline plan before queue/Home.' : 'Tasks without a compatible match use normal Astra.');}
    else if(trace.stage === 'needed') {badge(fresh,'Pending · new skills needed');paragraph(fresh,'Missing behavior is being identified during program preparation.');}
    else if(!groups.created.length) paragraph(fresh,groups.unknown ? 'Creation unknown; no new skill record is available.' : 'No new skill creation recorded.');
    for(const item of groups.created) {
      const d = details(item.title || item.id);paragraph(d,item.behavior);paragraph(d,item.reason);
      badge(d,'New code recorded · validation is scoped');
      badge(d,item.planning_success === true ? 'Plan passed' : item.planning_success === false ? 'Plan failed' : 'Planning unrecorded');
      badge(d,item.physical_success === true ? 'Live harness success · parking review separate' : 'Physical success not demonstrated');
      paragraph(d,'Code SHA256 '+item.code_sha256+' · lines '+item.start_line+'–'+item.end_line,'aspireSource');
      code(d,'New behavior code',item.code);fresh.append(d);
    }
    root.append(fresh);
    const existing = node('section','','aspireGroup');existing.append(node('h3','Skills reused'));
    paragraph(existing,trace.executable || trace.pending ? 'Saved code reuse retains source provenance. Current geometry and paths are rebuilt live.' :
      'Reuse to generate or refine code. Model weights remain unchanged.','aspireSource');
    if(!groups.used.length) paragraph(existing,groups.unknown ? 'Use not recorded. Retrieval alone does not prove use.' : 'No existing skill use was declared.');
    for(const use of groups.used) {
      const d = details(use.id+' · '+use.usage);
      badge(d,use.verification === 'exact_source_match' ? 'Exact source match' : use.verification === 'hash_mismatch' ? 'Hash mismatch' : 'Model-declared adaptation');
      paragraph(d,'Version: '+use.version,'aspireSource');paragraph(d,use.changed);
      if(use.validation_scope) paragraph(d,use.validation_scope,'aspireCaveat');
      if(use.inputs) code(d,'Bound task inputs',use.inputs);
      snippets(d,use.source_refs);
      if(use.core_revision || trace.executable?.core_revision) code(d,'Recorded repair before this reuse',use.core_revision || trace.executable.core_revision);
      for(const evidence of use.development_evidence || []) {
        const e = details('Development source · '+(evidence.scope || 'Scoped evidence'));
        paragraph(e,evidence.path+' · SHA256 '+evidence.sha256,'aspireSource');
        paragraph(e,'Declared development reference; this reference alone does not establish exact code ancestry.');
        for(const episode of catalog?.episodes?.filter(ep => ep.attempts.some(at => at.source?.source === evidence.path)) || []) episodeLink(e,episode.id);
        d.append(e);
      }
      const item = snapshots.find(item => item.id === use.id && item.version === use.version) || catalog?.recipes?.find(item => item.id === use.id && item.version === use.version);
      if(item) recipe(d,item);existing.append(d);
    }
    if(groups.retrievedOnly.length) {
      const retrieved = details('Retrieved; use not recorded ('+groups.retrievedOnly.length+')');
      for(const item of groups.retrievedOnly) {
        const d = details(item.id+' · retrieved; use not recorded');paragraph(d,'Version: '+item.version,'aspireSource');
        const original = snapshots.find(e => e.id === item.id && e.version === item.version);
        if(original) {paragraph(d,original.scope || original.validation || '');snippets(d,original.snippets);for(const id of original.episodes || []) episodeLink(d,id);}
        else paragraph(d,'Exact retrieval snapshot unavailable in this historical record.');retrieved.append(d);
      }
      existing.append(retrieved);
    }
    root.append(existing);
    if(trace.program) {const d = details('Complete program code');paragraph(d,trace.program.source+' · SHA256 '+trace.program.source_sha256,'aspireSource');d.append(node('pre',trace.program.code));root.append(d);}
    if(trace.authorship) code(root,'Program authorship record',trace.authorship);
    paragraph(root,'Episodes → skills / recipes → derived code → new episodes','aspireFlow');return root;
  }
  const updateViews = new WeakMap();
  const repairTraceViews = new WeakMap();
  const answerViews = new WeakMap();
  function renderTaskAnswer(box,answer,runId,visible=true) {
    let view=answerViews.get(box);
    if(!view) {
      const title=node('strong'),detail=node('p'),next=node('p'),button=node('button','Check scene and recover');
      button.type='button';box.append(title,detail,next,button);view={title,detail,next,button};answerViews.set(box,view);
      box.classList.add('notice');box.setAttribute('role','status');
      button.addEventListener('click',async()=>{
        button.disabled=true;
        try {await window.yamRecoverTask(view.runId);} catch(error) {view.next.textContent=error.message;button.disabled=false;}
      });
    }
    setHidden(box,!answer || !visible);if(!answer)return;
    for(const [field,value] of Object.entries({title:answer.title,detail:answer.detail,next:'Next: '+answer.next_action}))
      if(view[field].textContent !== value) view[field].textContent=value;
    view.runId=runId;setHidden(view.button,!localAspire || !runId || !answer.recovery_available);
  }
  function renderRecoveryTraces(box,attempts = [],events = []) {
    let views=repairTraceViews.get(box);
    if(!views) {views=new Map();repairTraceViews.set(box,views);}
    const relevant=attempts.filter(a=>a.trace !== undefined || ['offline_code_repair','offline_rerun_diagnosis'].includes(a.mode) || a.id === 'astra-1');
    for(const [id,view] of views) if(!relevant.some(a=>a.id === id)) {view.root.remove();views.delete(id);}
    for(const attempt of relevant) {
      let view=views.get(attempt.id);
      const active=['running','starting'].includes(attempt.status);
      const label=attempt.mode === 'offline_rerun_diagnosis' ? 'Justified unchanged-code rerun' : attempt.policy === 'codex_local' ? 'Local Codex repair' : attempt.mode === 'offline_code_repair' ? 'Astra repair' : 'Astra recovery';
      if(!view) {
        const root=details(label+' activity · '+attempt.id),status=node('p'),waiting=node('p'),ol=node('ol','','aspireTaskLog');
        root.classList.add('aspireRepairTrace');root.open=active;
        paragraph(root,'Public model summaries and repair activity. Follows '+attempt.parent_attempt_id+'.','aspireSource');
        ol.setAttribute('aria-label','Recovery activity · '+attempt.id);
        root.append(status,waiting,ol);box.append(root);view={root,status,waiting,ol,rows:new Map()};views.set(attempt.id,view);
      }
      const heading=label+' activity · '+attempt.id;
      if(view.root.children[0].textContent !== heading) view.root.children[0].textContent=heading;
      const rows=aspireAttemptTrace(attempt,events),ids=new Set(rows.map(e=>String(e.id)));
      const follow=active && (!view.rendered || view.ol.scrollHeight-view.ol.scrollTop-view.ol.clientHeight<80);
      const scroll=view.ol.scrollTop;
      for(const [id,row] of view.rows) if(!ids.has(id)) {row.li.remove();view.rows.delete(id);}
      for(const event of rows) {
        const id=String(event.id),key=JSON.stringify(event);let row=view.rows.get(id);
        if(!row) {
          const li=node('li'),title=node('strong'),message=node('p'),changed=node('p'),lesson=node('p'),next=node('p');
          li.append(title,message,changed,lesson,next);view.ol.append(li);row={li,title,message,changed,lesson,next};view.rows.set(id,row);
        }
        if(row.key === key) continue;row.key=key;
        const label=event.kind === 'model_progress' ? event.progress_type === 'summary' ? 'Model summary' : 'Model status' :
          event.kind === 'model_response' ? 'Diagnosis / response' : event.kind === 'model_request' ? 'Model request' :
          ['model_error','tool_error'].includes(event.kind) ? 'Reported failure' : 'Repair activity';
        for(const [field,value] of Object.entries({title:label+(event.revision ? ' · revision '+event.revision : ''),
          message:event.message || '',changed:event.changed || '',lesson:event.lesson || '',next:event.next_action || ''})) {
          if(row[field].textContent !== value) row[field].textContent=value;
          setHidden(row[field],!value);
        }
      }
      const status=attempt.status || attempt.execution?.status || 'Outcome unrecorded';
      if(view.status.textContent !== status) view.status.textContent=status;
      const hasSummary=rows.some(e=>e.kind === 'model_response' || e.kind === 'model_progress' && e.progress_type === 'summary');
      const waiting=hasSummary ? '' : active ? 'Waiting for the model to publish a summary. Available tool activity appears as it arrives.' : 'No public model summary was recorded for this attempt.';
      if(view.waiting.textContent !== waiting) view.waiting.textContent=waiting;
      setHidden(view.waiting,!waiting);view.rendered=true;
      view.ol.scrollTop=follow ? view.ol.scrollHeight : scroll;
    }
    setHidden(box,!relevant.length);
  }

  const activityViews=new WeakMap(),workStatusViews=new WeakMap();
  function renderWorkStatus(box,status) {
    let view=workStatusViews.get(box);
    if(!view) {
      const label=node('span','Current phase','aspirePhaseLabel'),title=node('strong'),state=node('span','','aspireWorkState'),
        timer=node('span','','aspirePhaseTimer'),meta=node('p','','aspirePhaseMeta'),detail=node('p','','aspirePhaseDetail');
      state.setAttribute('role','status');state.setAttribute('aria-live','polite');
      timer.setAttribute('role','timer');timer.setAttribute('aria-live','off');
      const path=node('div','','aspirePhasePath'),source=node('p','','aspireCurrentCode');
      path.setAttribute('aria-label','Task phases');
      const info=node('details','','aspirePhaseInfo');
      info.append(node('summary','Details'),meta,detail,source);
      box.append(path,label,title,state,timer,info);view={title,status:state,timer,meta,detail,path,source};workStatusViews.set(box,view);
      box.classList.add('aspirePhaseCard');
    }
    box.dataset.state=status.state;setHidden(box,false);
    const pathKey=JSON.stringify(status.path || []);
    if(view.pathKey !== pathKey) {
      view.path.replaceChildren();
      for(const [index,step] of (status.path || []).entries()) {
        if(index) view.path.append(node('span','›','aspirePhaseArrow'));
        const label=node('span',step.label,'aspirePhaseStep');label.dataset.state=step.state;
        if(step.state === 'current' || step.state === 'hold') label.setAttribute('aria-current','step');
        view.path.append(label);
      }
      view.pathKey=pathKey;setHidden(view.path,!(status.path || []).length);
    }
    const sourceText=status.source ? 'Saved code · '+status.source.slice(0,12) : '';
    if(view.source.textContent !== sourceText) view.source.textContent=sourceText;
    view.source.title=status.source || '';setHidden(view.source,!sourceText);
    for(const field of ['title','status','timer','meta','detail']) {
      if(view[field].textContent !== status[field]) view[field].textContent=status[field];
      setHidden(view[field],!status[field]);
    }
  }
  const activityPreviewLimit=400;
  let activityContentId=0;
  function renderActivityContent(row) {
    const parts=Object.entries(row.values).map(([field,value])=>[field,Array.from(value || '')]);
    const truncated=parts.reduce((sum,[,chars])=>sum+chars.length,0)>activityPreviewLimit;
    let remaining=activityPreviewLimit-1,lastVisible='';
    const values={};
    for(const [field,chars] of parts) {
      const shown=truncated && !row.expanded ? chars.slice(0,Math.max(0,remaining)) : chars;
      values[field]=shown.join('');
      if(shown.length) lastVisible=field;
      remaining-=shown.length;
    }
    if(truncated && !row.expanded && lastVisible) values[lastVisible]+='…';
    for(const [field,value] of Object.entries(values)) {
      if(row[field].textContent !== value) row[field].textContent=value;
      setHidden(row[field],!value);
    }
    row.li.dataset.truncated=String(truncated);
    const label=row.expanded ? 'Show less' : 'Show more';
    if(row.more.textContent !== label) row.more.textContent=label;
    row.more.setAttribute('aria-expanded',String(row.expanded));
    setHidden(row.more,!truncated);
  }
  function renderActivity(box,messages,active) {
    let view=activityViews.get(box);
    if(!view || view.ol.parentElement !== box) {
      const ol=node('ol','','aspireActivityLog');ol.setAttribute('role','log');ol.setAttribute('aria-live','polite');
      ol.setAttribute('aria-relevant','additions text');ol.setAttribute('aria-label','Task conversation');ol.tabIndex=0;
      box.append(ol);view={ol,rows:new Map()};activityViews.set(box,view);
    }
    const follow=active && (!view.rendered || view.ol.scrollHeight-view.ol.scrollTop-view.ol.clientHeight<80),scroll=view.ol.scrollTop;
    const ids=new Set(messages.map(message=>message.id));
    for(const [id,row] of view.rows) if(!ids.has(id)) {row.li.remove();view.rows.delete(id);}
    for(const [index,message] of messages.entries()) {
      let row=view.rows.get(message.id);
      if(!row) {
        const li=node('li','','aspireActivityEntry'),heading=node('div','','aspireActivityHeading conversationHeading'),author=node('strong'),time=node('time'),
          context=node('p','','aspireActivityContext'),title=node('strong','','aspireActivityTitle'),body=node('p'),detail=node('p'),lesson=node('p'),next=node('p','','aspireActivityNext'),hash=node('p','','aspireSource');
        const content=node('div','','aspireMessageContent'),more=node('button','Show more','aspireMessageToggle');
        content.id='aspire-message-content-'+(++activityContentId);
        more.type='button';more.setAttribute('aria-controls',content.id);
        const localSetup=node('p','','aspireLocalSetup help');
        heading.append(author,time);content.append(context,title,body,detail,lesson,next,hash);li.append(heading,content,more,localSetup);
        row={li,author,time,context,title,body,detail,lesson,next,hash,content,more,localSetup,expanded:false};
        const messageRow=row;
        more.addEventListener('click',()=>{messageRow.expanded=!messageRow.expanded;renderActivityContent(messageRow);});
        view.rows.set(message.id,row);
      }
      if(view.ol.children[index] !== row.li) view.ol.insertBefore(row.li,view.ol.children[index] || null);
      const key=JSON.stringify(message);if(row.key === key) continue;row.key=key;
      setHidden(row.localSetup,!message.localSetup);
      if(message.localSetup) {
        if(!row.readme) {
          row.readme=node('a','You can run ASPIRE locally (README)');
          row.readme.target='_blank';row.readme.rel='noopener noreferrer';row.localSetup.append(row.readme);
        }
        row.readme.href=message.localSetup;
      }
      row.li.dataset.role=message.role;row.li.dataset.tone=message.tone || '';row.li.dataset.messageId=message.id;
      row.li.className='aspireActivityEntry conversationTurn '+(message.role === 'You' ? 'conversationOutgoing' : 'conversationIncoming');
      const timestamp=Number.isFinite(message.timestamp) ? new Date(message.timestamp*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}) : '';
      for(const [field,value] of Object.entries({author:message.role,time:timestamp})) {
        if(row[field].textContent !== value) row[field].textContent=value;
        setHidden(row[field],!value);
      }
      row.values={title:message.role === 'You' || ['Activity','Code-writing update','Model status'].includes(message.title) ? '' : message.title,
        body:message.message,detail:message.detail || '',lesson:message.lesson ? 'Lesson: '+message.lesson : '',
        next:message.next || '',hash:message.hash ? 'Saved code · '+message.hash.slice(0,12) : '',
        context:[message.attempt,message.revision ? 'Revision '+message.revision : ''].filter(Boolean).join(' · ')};
      renderActivityContent(row);
    }
    view.rendered=true;view.ol.scrollTop=follow ? view.ol.scrollHeight : scroll;
  }

  function renderUpdates(box,updates,followLatest=false) {
    let view = updateViews.get(box);
    if(!view || view.ol.parentElement !== box) {
      const ol = node('ol','','aspireTaskLog');ol.setAttribute('aria-label','Task work log');
      const empty = node('p','No task updates have been recorded yet.');box.append(ol,empty);
      view = {ol,empty,rows:[],keys:[]};updateViews.set(box,view);
    }
    const keys = updates.map(update => JSON.stringify([update.happened,update.changed,update.next_action]));
    if(JSON.stringify(keys) === JSON.stringify(view.keys) && view.rendered) return;
    const follow = followLatest && (!view.rendered || view.ol.scrollHeight-view.ol.scrollTop-view.ol.clientHeight < 80);
    const scroll = view.ol.scrollTop;
    for(let i=0;i<updates.length;i++) {
      if(keys[i] === view.keys[i]) continue;
      const update=updates[i], li=view.rows[i] || node('li');
      li.replaceChildren(node('strong',update.happened),node('p','Change: '+(update.changed.length > 220 ? update.changed.slice(0,217)+'…' : update.changed)),node('p','Next: '+update.next_action));
      if(update.changed.length > 220) code(li,'Exact update details',update.changed);
      if(!view.rows[i]) view.ol.append(li);view.rows[i]=li;
    }
    for(const row of view.rows.splice(updates.length)) row.remove();
    view.keys=keys;view.rendered=true;view.ol.hidden=!updates.length;view.empty.hidden=!!updates.length;
    view.ol.scrollTop = follow ? view.ol.scrollHeight : scroll;
  }
  function renderEpisode(item) {
    const box = details((item.task || item.id)+' · '+(item.review_status || item.native_status || 'Outcome unknown'));box.id = 'aspire-episode-'+item.id;
    paragraph(box,item.id+(item.episode_id ? ' · '+item.episode_id : ''),'aspireSource');
    paragraph(box,item.scope || 'Validation scope unrecorded');
    const answer=node('div');renderTaskAnswer(answer,aspireTaskAnswer({},item),item.recovery_run_id);box.append(answer);
    badge(box,'Original harness: '+(item.native_status || 'unknown'));
    badge(box,'After parking: '+(item.after_parking_success === true ? 'automatically confirmed for this episode' : item.after_parking_success === false ? 'automated result unverified' : 'automatic result unrecorded'));
    if(item.visual_review_success != null) badge(box,'Visual review: '+(item.visual_review_success ? 'retained placement confirmed for this episode' : 'not confirmed'));
    if(item.postpark_checks) code(box,'After-parking checks',item.postpark_checks);
    for(const attempt of item.attempts) {
      const d = details((attempt.policy === 'aspire_retry' ? 'Automatic live retry '+attempt.id : attempt.mode === 'offline_rerun_diagnosis' ? 'Justified unchanged-code rerun '+attempt.id : attempt.mode === 'offline_code_repair' ? 'Astra code repair '+attempt.id : attempt.policy === 'astra' ? 'Astra repair revision '+attempt.id : attempt.policy === 'codex_local' ? 'Local Codex repair '+attempt.id : 'Robot program '+attempt.attempt)+' · '+(attempt.execution.status || attempt.plan.status || 'Result unrecorded'));
      if(attempt.parent_attempt_id) paragraph(d,'Follows failed attempt '+attempt.parent_attempt_id+'.');
      if(attempt.trace) {const trace=node('div');d.append(trace);renderRecoveryTraces(trace,[attempt]);}
      paragraph(d,aspireProgramAttribution(attempt.lineage),'aspireAuthorship');paragraph(d,attempt.summary || 'Recorded program');
      paragraph(d,'Program SHA256: '+(attempt.source_sha256 || 'unknown'),'aspireSource');
      code(d,'Planning evidence',attempt.plan);code(d,'Physical harness evidence',attempt.execution);
      link(d,'Immutable recorded program source',attempt.source);
      link(d,'Native candidate plans and rejections',attempt.candidate_evidence);
      link(d,'Raw recovery preflight log',attempt.preflight_log);
      link(d,'Recovery launcher arguments and exit status',attempt.preflight_process);
      link(d,'Source changes',attempt.diff);
      d.append(lineage(attempt.lineage,attempt.retrieval_snapshot,['episode',item.id,attempt.attempt]));if(attempt.code) code(d,'Original program code',attempt.code);box.append(d);
    }
    if(!item.attempts.length) paragraph(box,'Program usage not recorded; ancestry unknown.');
    if(item.images.length) {
      const d = details('Recorded review images'),gallery = node('div','','aspireImages');
      for(const image of item.images) {const figure = node('figure'),img = document.createElement('img');img.src = url(image.url);img.alt = item.task+' · '+image.phase+' · '+image.name;img.loading = 'lazy';figure.append(img,node('figcaption',image.name+' · '+image.phase));gallery.append(figure);}
      d.append(gallery);box.append(d);
    }
    if(item.replays.length) {
      const d = details('Replay through parking · failures retained');
      for(const replay of item.replays) {const video = document.createElement('video');video.controls = true;video.preload = 'none';video.src = url(replay.url);d.append(video);}box.append(d);
    }
    link(box,'Recorded receipt',item.receipt);link(box,'Independent review',item.review);return box;
  }
  const panel = node('div','','aspireTaskPanel');panel.id = 'aspireTaskPanel';
  const selectorLabel = node('label','View task');selectorLabel.htmlFor = 'aspireTaskSelector';
  const selector = node('select');selector.id = 'aspireTaskSelector';
  selector.append(Object.assign(node('option','Prompt preview · not submitted'),{value:'preview'}));
  const selectorBox = node('div','','aspireTaskPicker');selectorBox.append(selectorLabel,selector);
  const work = node('div');work.id = 'aspireTaskWork';
  let currentWork = null;
  const library = details('Recorded tasks and source recipes');library.id = 'aspireRecordedTasks';
  const support=$('aspireTaskDetails') || panel,evidence=$('aspireTaskEvidence') || work;
  function placeEvidence(...items) {
    if(evidence === work) work.append(...items);else evidence.replaceChildren(...items);
  }
  library.append(selectorBox);panel.append(work);support.append(library);$('liveConversationPanel')?.append(panel);
  const promptBox = $('sidePrompt')?.closest('details');promptBox?.after(panel);
  function renderPrompt() {
    currentWork=null;
    work.replaceChildren();if(evidence !== work) evidence.replaceChildren();const route = aspirePromptRoute($('prompt').value,catalog);
    const prompt = $('prompt').value.trim() || 'Enter a task below.';
    if($('sidePrompt').textContent !== prompt) $('sidePrompt').textContent = prompt;
    const phase=node('div');renderWorkStatus(phase,{state:'draft',status:'Not submitted',title:'Task preview',timer:'',path:aspirePhasePath({}),meta:'No task work has started',detail:route.title});work.append(phase);
    renderActivity(work,aspireActivityEntries({task:prompt,task_progress:{updates:[{happened:route.title+'.',changed:route.reason,next_action:'Run checks the full prompt against saved programs before any scene capture.'}]}}),false);
    const pending = builtFrom('preview');
    const fresh = node('section','','aspireGroup');fresh.append(node('h3','New skills added'));badge(fresh,route.generate ? 'ASPIRE code generation after Run' : 'No coding request for saved reuse');
    paragraph(fresh,route.compatible ? 'Saved code still needs fresh live planning.' : route.generate ? 'New code must pass complete offline planning before queue/Home.' : 'This launch defaults to normal Astra. Adding reusable ASPIRE code is optional local development.');pending.append(fresh);
    const used = node('section','','aspireGroup');used.append(node('h3','Skills reused'));paragraph(used,'Preview only; retrieval and actual use are recorded during preparation.');
    if(route.exact) {
      const d=details('Exact-task saved program · compatible candidate');paragraph(d,'SHA256 '+route.exact.source_sha256,'aspireSource');used.append(d);
    } else if(route.compatible) {
      const d = details(catalog.executable.skill+' · compatible candidate');paragraph(d,'Version '+catalog.executable.version+' · SHA256 '+catalog.executable.source_sha256,'aspireSource');
      paragraph(d,catalog.executable.validation_scope);code(d,'Saved executable code',catalog.executable.source_code);
      if(catalog.executable.latest_core_revision) code(d,'Recorded core repair',catalog.executable.latest_core_revision);used.append(d);
    }
    pending.append(used);placeEvidence(pending);
  }
  function refresh() {
    rememberBuiltFrom(work);if(evidence !== work) rememberBuiltFrom(evidence);
    robot = $('robotSelector')?.value || robot;
    const failure = taskRunFailure(liveRun);
    const currentFailure = !!(!draftPreview && selected === 'preview' && failure &&
      (!localAspire || liveRun.task_progress || pendingAttempt ||
        (liveState?.attempt_id && liveState.attempt_id === liveRun.attempt_id)));
    const fallback = !draftPreview && (liveRun?.launch_route || liveState?.provider?.launch_route)?.actual_policy === 'astra';
    const stationMatches = catalog ? robot === catalog.robot_id : !!liveRun?.task_progress;
    const enabled = currentFailure || !!(!fallback && stationMatches && ($('provider')?.value === 'aspire' || liveRun?.task_progress || selected !== 'preview'));
    const reasoning = $('liveConversationPanel')?.dataset.mode === 'reasoning';
    if(panel.dataset.enabled !== String(enabled)) panel.dataset.enabled = String(enabled);
    setHidden(panel,!enabled || !reasoning);setHidden(selectorBox,!enabled || !reasoning);
    if(support !== panel) setHidden(support,!enabled || !reasoning);
    setHidden(library,!catalog);
    const stream = document.querySelector('.astraStream');
    setHidden(stream,!!enabled && reasoning);
    if(!enabled) {
      setHidden(promptBox,false);
      if(work.children.length) {currentWork=null;work.replaceChildren();signature='';}
      for(const id of ['sideConversationTitle','sideConversationState','sideConversationMessages']) setHidden($(id),false);
      return;
    }
    if($('openLocalRun')) {if($('openLocalRun').textContent !== 'Run') $('openLocalRun').textContent = 'Run';$('openLocalRun').classList.add('primary');}
    for(const id of ['sideConversationTitle','sideConversationState','sideConversationMessages']) setHidden($(id),reasoning);
    const title = reasoning ? 'Chat' : 'Convo mode';
    if($('conversationModeTitle').textContent !== title) $('conversationModeTitle').textContent = title;
    const modeButton = document.querySelector('[data-conversation-mode="reasoning"]');
    if(modeButton && modeButton.textContent !== 'Chat') modeButton.textContent = 'Chat';
    setHidden(promptBox,reasoning);
    if(!reasoning) return;
    const ownProgress = liveState?.provider?.task_progress;
    const progress = liveRun?.task_progress || (ownProgress && liveState?.attempt_id === liveRun?.attempt_id ? ownProgress : null);
    const live = aspireTaskWorking({...liveRun,task_progress:progress});
    const vision = aspireSam3Progress({...liveRun,task_progress:progress});
    const stage = aspireStageProgress({...liveRun,task_progress:progress});
    const workStatus=aspireWorkStatus({...liveRun,task_progress:progress},Date.now()/1000,lastPollAt);
    const showCurrent = live || currentFailure || (!draftPreview && progress && selected === 'preview');
    const failureKey=JSON.stringify(showCurrent ? failure : null);
    if(panel.dataset.failureKey !== failureKey) panel.dataset.failureKey=failureKey;
    if(selector.disabled !== !!live) selector.disabled=!!live;
    const selection = live ? 'preview' : selected;
    if(selector.value !== selection) selector.value=selection;
    const currentOption = selector.options?.[0] || selector.children?.[0];
    const optionLabel = showCurrent ? 'Submitted task · '+(failure ? (failure.label === 'Task timed out' ? 'timed out' : 'failed') : liveRun.status) : 'Prompt preview · not submitted';
    if(currentOption && currentOption.textContent !== optionLabel) currentOption.textContent = optionLabel;
    const anchor = showCurrent ? (liveRun?.task || progress?.task) : selected === 'preview' ? $('prompt').value.trim() || 'Enter a task below.' : catalog?.episodes?.find(item => item.id === selected)?.task;
    if(anchor && $('sidePrompt').textContent !== anchor) $('sidePrompt').textContent = anchor;
    // A previous/public run changing must not invalidate an idle draft or recorded task.
    const traceKey=(progress?.attempts || []).map(a=>aspireAttemptTrace(a,liveRun?.events));
    const modelUpdates=aspireModelUpdates(liveRun?.events);
    const key = JSON.stringify(showCurrent ? ['current',robot,selected,liveRun?.attempt_id,liveRun?.run_id,liveRun?.status,liveRun?.error,liveRun?.display_error,progress,vision,traceKey,modelUpdates,workStatus]
      : selected === 'preview' ? ['preview',robot,$('prompt').value.trim()] : ['recorded',robot,selected]);
    if(key === signature) return;signature = key;
    if(showCurrent) {
      const identity = JSON.stringify([robot,pendingAttempt?.version ?? liveRun.attempt_id ?? liveRun.run_id ?? liveRun.task]);
      if(currentWork?.identity !== identity) {
        const heading=node('p','','aspireAuthorship'),answer=node('div'),visionBox=node('div','','notice'),stageBox=node('div'),log=node('div'),provenance=node('div','','aspireChatEvidence'),outcome=details('Parked-scene images and evidence'),error=node('div');
        answer.id='aspireTaskAnswer';
        visionBox.id='aspireVisionStatus';
        stageBox.id='aspireStageStatus';log.id='aspireTaskActivity';
        work.replaceChildren(stageBox,log,answer,heading,visionBox,error);
        placeEvidence(provenance,outcome);
        currentWork={identity,answer,heading,visionBox,stageBox,log,provenance,outcome,error};
      }
      const attribution=failure ? '' : progress?.stage?.active && progress?.attempts?.some(a=>a.mode === 'offline_code_repair' && a.status === 'running') ? 'Astra is repairing the unverified outcome' :
        !live ? (liveRun.status === 'completed' ? 'Task completed' : 'Task stopped') :
        progress?.recovery ? 'Astra recovery in progress' :
        progress?.lineage ? aspireProgramAttribution(progress.lineage) :
          liveRun.status === 'running' ? 'Task running' : liveRun.status === 'queued' ? 'Task queued' : 'Preparing this task';
      const authorship=progress?.lineage?.authorship?.generated_by_codex === true ? aspireProgramAttribution(progress.lineage) : '';
      const heading=[attribution,authorship && authorship !== attribution ? authorship : ''].filter(Boolean).join(' · ');
      if(currentWork.heading.textContent !== heading) currentWork.heading.textContent=heading;
      currentWork.heading.hidden=true;
      renderVision(currentWork.visionBox,vision && (!stage || vision.state === 'starting') ? vision : null);
      renderTaskAnswer(currentWork.answer,aspireTaskAnswer({...liveRun,task_progress:progress}),undefined,!live);
      renderWorkStatus(currentWork.stageBox,workStatus);
      renderActivity(currentWork.log,aspireActivityEntries({...liveRun,task_progress:progress}),['active','waiting'].includes(workStatus.state));
      const lineageKey=JSON.stringify(progress?.lineage ?? null);
      if(currentWork.lineageKey !== lineageKey) {
        currentWork.provenance.replaceChildren(...(progress?.lineage ? [lineage(progress.lineage,[],
          ['current',pendingAttempt?.version ?? liveRun.attempt_id ?? liveRun.run_id ?? liveRun.task])] : []));
        currentWork.lineageKey=lineageKey;
      }
      const outcomeKey=JSON.stringify(progress?.outcome ?? null);
      if(currentWork.outcomeKey !== outcomeKey) {
        currentWork.outcome.replaceChildren(node('summary','Parked-scene images and evidence'));currentWork.outcomeKey=outcomeKey;
        setHidden(currentWork.outcome,!progress?.outcome);
        if(progress?.outcome) {
          const outcome=progress.outcome, box=node('div','','aspireOutcome');
          paragraph(box,'After-parking result: '+outcome.status+'.');
          paragraph(box,outcome.reason || 'Placement evidence is unavailable.');
          for(const item of localAspire ? outcome.images || [] : []) {
            if(!/^\/api\/aspire-lineage\/artifacts\/[a-f0-9]{64}$/.test(item.url)) continue;
            const image=node('img');image.src=url(item.url);image.alt=item.camera+' camera after parking';
            image.style.maxWidth='100%';box.append(image);
            if(item.captured_at) paragraph(box,item.camera+' capture: '+new Date(item.captured_at*1000).toISOString(),'aspireSource');
            link(box,'Open '+item.camera+' parked-scene image',item);
          }
          currentWork.outcome.append(box);
        }
      }
      const errors = [];
      if(liveRun.display_error) errors.push({happened:'Playground error.',changed:liveRun.display_error,next_action:'Review this display error; it does not change the robot run result.'});
      renderUpdates(currentWork.error,errors);
      currentWork.error.hidden=!errors.length;
    } else if(selected === 'preview') renderPrompt();
    else {
      const item = catalog?.episodes?.find(item => item.id === selected);if(!item) {selected = 'preview';renderPrompt();return;}
      currentWork=null;const attempt = item.attempts.at(-1);$('sidePrompt').textContent = item.task;work.replaceChildren();
      const answer=node('div');answer.id='aspireTaskAnswer';renderTaskAnswer(answer,aspireTaskAnswer({},item),item.recovery_run_id);work.append(answer);
      paragraph(work,aspireProgramAttribution(attempt?.lineage),'aspireAuthorship');badge(work,item.evidence_role === 'saved_program' ? 'Saved program · no episode review' : 'Recorded task');
      const recorded=aspireRecordedActivity(item),phase=node('div');
      renderWorkStatus(phase,{...aspireWorkStatus(recorded),status:'Recorded task · not running',meta:'History view',detail:'Recorded code revisions and outcomes; viewing this task does not start work.'});work.insertBefore(phase,work.children[0] || null);
      renderActivity(work,aspireActivityEntries(recorded),false);placeEvidence(lineage(attempt?.lineage || {},attempt?.retrieval_snapshot || [],['selected',item.id]),
        renderEpisode({...item,id:item.id+'-selected'}));
    }
  }
  function renderCatalog() {
    rememberBuiltFrom(library);
    selector.replaceChildren(Object.assign(node('option','Prompt preview · not submitted'),{value:'preview'}));
    for(const item of catalog?.episodes || []) selector.append(Object.assign(node('option',item.task+' · '+(item.review_status || item.native_status || 'unknown')),{value:item.id}));
    if(!catalog?.episodes?.some(item => item.id === selected)) selected = 'preview';selector.value = selected;
    library.replaceChildren(node('summary','Recorded tasks and source recipes'),selectorBox);
    paragraph(library,catalog.semantics,'aspireSource');
    for(const item of catalog?.episodes || []) library.append(renderEpisode(item));
    const recipes = details('Recipes in library · availability does not establish use');for(const item of catalog.recipes) recipe(recipes,item);library.append(recipes);
    signature = '';refresh();
  }
  async function loadCatalog() {
    if(!localAspire) return;
    robot = $('robotSelector')?.value || new URLSearchParams(location.search).get('robot_id');if(!robot) return;
    const generation = ++fetchGeneration;catalog = null;signature = '';panel.hidden = selectorBox.hidden = true;
    try {
      const response = await fetch('/api/aspire-lineage?robot_id='+encodeURIComponent(robot),{cache:'no-store',headers:{'X-Blupe-Robot':robot}});if(!response.ok) return;
      const data = await response.json();if(generation !== fetchGeneration) return;
      if(!data || !Array.isArray(data.episodes) || !Array.isArray(data.recipes)) throw new Error('Invalid recorded task catalog');
      catalog = data;renderCatalog();
    } catch {if(generation === fetchGeneration) paragraph(work,'Recorded skill evidence is unavailable.');}
  }
  window.yamAspireLineage = {
    reset() {draftPreview=true;pendingAttempt=null;liveRun=liveState=null;selected='preview';signature='';refresh();},
    preview() {if(!aspireTaskWorking(liveRun)) {draftPreview=true;pendingAttempt=null;liveRun=liveState=null;selected='preview';refresh();}},
    requested(task,version,aspire = true) {
      draftPreview=false;pendingAttempt={version,phase:'pending',id:null};liveState=null;selected='preview';signature='';
      liveRun={task,status:'preparing',task_progress:aspire ? {task,lineage:{pending:true,usage_recorded:false},updates:[{
        happened:'Reviewing your prompt…',changed:'Working through your prompt and preparing the next steps.',
        next_action:'Preparation updates and recorded skill evidence will appear here.'}]} : null};
      refresh();
    },
    accepted(id,version,route) {
      if(pendingAttempt?.version !== version) return;
      pendingAttempt={version,phase:'accepted',id};liveRun={...liveRun,attempt_id:id,launch_route:route};
      if(route?.actual_policy === 'astra') liveRun.task_progress=null;
      signature='';refresh();
    },
    rejected(reason,version) {
      if(pendingAttempt?.version !== version) return;
      pendingAttempt.phase='rejected';
      const progress=liveRun.task_progress;
      liveRun={...liveRun,status:'failed',error:reason,task_progress:progress ? {...progress,updates:[
        ...(progress.updates || []),{happened:'Preparation failed.',changed:reason,
          next_action:'Resolve this error before starting another attempt.'}]} : null};
      signature='';refresh();
    },
    live(run,state) {
      lastPollAt=Date.now()/1000;
      const candidate=aspireLiveTask(run,state);
      if(pendingAttempt && (pendingAttempt.phase !== 'accepted' ||
          (pendingAttempt.id && candidate?.attempt_id !== pendingAttempt.id))) return;
      if(aspireTaskWorking(candidate)) draftPreview=false;
      liveRun=candidate && !candidate.task_progress && candidate.launch_route?.actual_policy !== 'astra' &&
        candidate.attempt_id && candidate.attempt_id === liveRun?.attempt_id
        ? {...candidate,task_progress:liveRun.task_progress} : candidate;
      liveState=state;refresh();
    },refresh,
    history(box,run) {if(!catalog || robot !== catalog.robot_id) return;
      const item = catalog?.episodes?.find(item => item.episode_id && item.episode_id === run.episode_id);
      if(item) box.append(renderEpisode({...item,id:item.id+'-history'}));else box.append(lineage(run.lineage || {usage_recorded:false},[],
        ['history',run.episode_id || run.id || run.run_id || run.prompt]));}
  };
  window.setInterval?.(() => {
    if(currentWork && !panel.hidden && liveRun) renderWorkStatus(currentWork.stageBox,aspireWorkStatus(liveRun,Date.now()/1000,lastPollAt));
  },1000);
  selector.addEventListener('change',() => {selected = selector.value;draftPreview=selected === 'preview';signature = '';refresh();});
  $('prompt')?.addEventListener('input',() => {if(!aspireTaskWorking(liveRun)) {draftPreview=true;selected = 'preview';selector.value = selected;refresh();}});
  window.addEventListener('blupe-robot-selected',() => {draftPreview=localAspire;liveRun = liveState = pendingAttempt = null;selected = 'preview';renderFailure($('taskRunFailure'),null);loadCatalog();});
  panel.hidden = selectorBox.hidden = true;loadCatalog();
})();
