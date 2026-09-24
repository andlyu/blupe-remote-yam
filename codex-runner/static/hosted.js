(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('pastRunsList')) return;
  let next = 0, loading = false, historyRobot = null, historyGeneration = 0;
  const dialog = $('pastRunDialog'), player = $('pastRunVideo');
  const zeroSecondVideo = duration => typeof duration === 'number' && Number.isFinite(duration) && duration >= 0 && duration < 1;
  let selectedRun = null;
  let preview = null;
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
    selectedRun = run;
    speedButton.hidden = run.original_available === false;
    speedButton.dataset.live = String(live);
    speedButton.textContent = live ? '10× speed' : 'Live speed';
    const cameras = live ? 'Top, left and right cameras' : run.camera_order.includes('observer') ? 'Top, side, left and right cameras' : 'Three cameras · side view was not recorded';
    $('pastRunDetails').textContent = (live ? 'Live speed · 1× · all pauses retained' : run.compressed ? '10× speed · idle pauses removed' : '10× playback · original recording, pauses retained') + ' · ' + cameras + ' · ' + (run.result || 'Unknown') + ': ' + ((run.result === 'Failure' && run.errors) || run.result_reason || 'Result was not recorded');
    $('pastRunError').textContent = '';
    const source = playbackSource(run, live);
    player.pause();
    player.src = source.url;
    player.defaultPlaybackRate = player.playbackRate = source.rate;
    if (!dialog.open) dialog.showModal();
    player.play().catch(() => {});
  }
  $('closePastRun').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { player.pause(); player.removeAttribute('src'); player.load(); });
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
      if (reset) { stopPreview(); $('pastRunsList').replaceChildren(); }
      for (const run of data.runs) {
        const li = document.createElement('li'), button = document.createElement('button');
        button.type = 'button'; button.className = 'pastRunCard';
        button.setAttribute('aria-label', `Watch run ${run.episode_index}: ${run.prompt}`);
        const video = document.createElement('video');
        video.preload = 'metadata'; video.muted = true; video.playsInline = true; video.loop = true;
        button.addEventListener('pointerenter', event => {
          if (event.pointerType !== 'mouse' || dialog.open || zeroSecondVideo(run.video_duration_s)) return;
          stopPreview();
          preview = video;
          video.defaultPlaybackRate = video.playbackRate = playbackSource(run, false).rate;
          video.play().catch(() => {});
        });
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
        button.append(title, prompt, video, watch); button.addEventListener('click', () => openRun(run)); li.append(button);
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
  'use strict';
  const $ = id => document.getElementById(id);
  let claudeModel = 'claude-opus-5-5';
  const originalProviderMarkup = $('provider').innerHTML;
  let selectedRobot = window.yamApplication?.defaultRobot || new URLSearchParams(location.search).get('robot_id') || 'yam-1', robotGeneration = 0, robotCatalog = null, pollingStarted = false;
  let ownRunLive = false, conversationSharingAllowed = true;
  let csrf = '', ended = false, submitting = false, active = false, lastHistory = 0, contactRequested = false;
  function message(text, error = false) { $('message').textContent = text; $('message').classList.toggle('error', error); }
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
  async function api(path, data) {
    const generation = robotGeneration;
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
    if (!response.ok) {
      if (response.status === 401 && path !== '/api/chat') { ended = true; buttons(); $('apiKey').value = ''; }
      const error = new Error(result.error || 'Request failed');
      error.subscriptionSetup = result.subscription_setup;
      error.paymentConfirmed = result.payment_confirmed === true;
      throw error;
    }
    return result;
  }
  function buttons() {
    $('run').disabled = !csrf || ended || active || submitting;
    $('runGroot').disabled = !csrf || ended || active || submitting;
    $('runAstra').disabled = $('runClaude').disabled = !csrf || ended || active || submitting;
    ['runDuration', 'runnerName', 'email', 'provider', 'model', 'prompt', 'apiKey'].forEach(id => { $(id).disabled = active || submitting || ended; });
    $('shareConversation').disabled = !conversationSharingAllowed || active || submitting || ended;
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
    $('copyCodexPrompt').dataset.copied = 'false';
    $('codexCopyStatus').textContent = '';
    $('codexInstructions').showModal();
  };
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
  function updateRunLabel() {
    const runParent = $('runSettings').open ? $('setupRunActions') : document.querySelector('.promptRow');
    if ($('runButtons').parentElement !== runParent) runParent.append($('runButtons'));
    const firstAction = $('run');
    if ($('runButtons').firstElementChild !== firstAction) $('runButtons').prepend(firstAction);
    const modelAnchor = $('runSettings').open ? $('run') : $('openCodexInstructions');
    modelAnchor.after($('runAstra'), $('runClaude'), $('runGroot'));
    $('openCodexInstructions').textContent = $('runSettings').open
      ? 'Run locally through subscription' : 'Run Locally through Subscription';
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
  }, true);
  $('runSettings').addEventListener('toggle', updateRunLabel);
  $('runForm').addEventListener('input', updateRunLabel);
  $('provider').addEventListener('change', () => queueMicrotask(updateRunLabel));

  function updateSavedKey(providers) {
    if (Array.isArray(providers)) savedKeyProviders = providers;
    const saved = savedKeyProviders.includes($('provider').value);
    const keyed = ['openai', 'astra', 'anthropic'].includes($('provider').value);
    $('apiKey').required = keyed && !saved;
    $('apiKey').placeholder = saved ? 'Key saved for this session · enter a new key to replace it' : 'Paste a dedicated API key';
    updateRunLabel();
  }
  function providerChanged() {
    $('subscriptionHelp').close();
    setupProvider = null;
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
    $('prompt').value = builtIn ? 'Raise and lower both arms.' : 'place green block on plate';
    $('policyHelp').textContent = builtIn ? 'The built-in policy performs three raise/lower cycles. No model calls or API key are needed.' : 'The model observes the cameras, chooses a move, and waits for robot feedback before deciding again.';
  }
  $('provider').addEventListener('change', () => { providerChanged(); applyModelName(lastLive); window.yamAnalytics?.selected($('provider').value, $('model').value.trim()); });
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
    contactRequested = false;
    if (await window.yamApplication?.submit?.(applicationContext())) return;
    window.yamAnalytics?.requested($('provider').value, $('model').value.trim());
    const payload = {runner_name: $('runnerName').value.trim(), provider: $('provider').value, model: $('model').value.trim(),
      email: $('email').value.trim(), share_conversation: $('shareConversation').checked,
      prompt: $('prompt').value, run_duration_s:Number($('runDuration').value)*60, api_key: $('apiKey').value.trim()};
    $('subscriptionHelp').close();
    submitting = true; buttons(); message(['codex', 'claude'].includes(payload.provider) ? 'Checking your subscription connection…' : 'Joining the robot queue…');
    try { const state = await api('/api/run', payload); updateSavedKey(state.saved_key_providers); window.yamAnalytics?.observe(state); $('apiKey').value = ''; $('runSettings').open = false; active = true; guideRunAttention({status:'queued'}); message(payload.provider === 'groot' ? 'You’re in the queue. GR00T GPU warmup has started.' : 'You’re in the queue. Your position is highlighted above.'); }
    catch (error) {
      if (error.stale) return;
      window.yamAnalytics?.rejected(payload.provider, payload.model);
      message(error.message, true);
      if (error.subscriptionSetup) showSubscriptionSetup(error.subscriptionSetup);
    }
    finally { delete payload.api_key; submitting = false; buttons(); }
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
  function syncStopwatch(run) {
    stopwatch = run && typeof run.run_elapsed_s === 'number' && Number.isFinite(run.run_elapsed_s)
      ? {elapsed: Math.max(0, run.run_elapsed_s), status: run.status,
         duration: typeof run.run_duration_s === 'number' && Number.isFinite(run.run_duration_s) ? run.run_duration_s : null,
         receivedAt: performance.now()} : null;
    tickStopwatch();
  }
  setInterval(tickStopwatch, 250);
  let attentionSession = null;
  let attentionPhase = null;
  function guideRunAttention(state) {
    const phase = state.status;
    const session = state.session_id || attentionSession;
    const changed = session !== attentionSession || phase !== attentionPhase;
    const queued = phase === 'queued';
    const preparing = phase === 'preparing';
    const live = phase === 'running';
    $('yourQueue').classList.toggle('yourTurnWaiting', queued || preparing);
    $('position').closest('.queuePlace').hidden = !(queued || preparing || live);
    $('liveViewer').classList.toggle('yourRunLive', live);
    $('viewerCue').hidden = !(preparing || live);
    $('viewerCueTitle').textContent = preparing ? 'Your run is preparing' : 'Your run is live';
    $('viewerCueDetail').textContent = preparing
      ? 'The robot is getting ready. You can stop your run here.'
      : 'Watch your robot here. The video may follow with a short delay.';
    $('position').textContent = queued ? (state.queue_position ? `#${state.queue_position}` : 'Joining…') : preparing ? 'Up next' : live ? 'Your turn' : '—';
    $('queueGuidance').textContent = queued
      ? (state.queue_position === 1 ? "You're next. We'll bring you to the viewer when your run starts." : "You're in the queue. We'll bring you to the viewer when it's your turn.")
      : preparing ? 'The robot is getting ready for your run.'
      : live ? 'Your run is live — watch the highlighted viewer.'
      : 'Join the queue to reserve your turn.';
    if (changed && (queued || preparing || live)) {
      const target = $(live ? 'liveViewer' : 'yourQueue');
      // Guide once per transition; polling must never pull someone away from reading.
      if (live || !['queued', 'preparing'].includes(attentionPhase)) {
        target.focus({preventScroll: true});
        target.scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center'});
      }
    }
    attentionSession = session;
    attentionPhase = phase;
  }
  function astraStreamNote(event, notesOnly = false) {
    if (event.kind !== 'model_response') return '';
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
    return notes.join('\n\n') || (!notesOnly && event.speaker && typeof event.message === 'string' ? event.message : '');
  }
  let liveModelName = 'Astra', lastLive = null;
  function selectedModelName() {
    const provider = $('provider').value;
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
    if (reasoningButton) reasoningButton.textContent = liveModelName + ' reasoning';
    if ($('liveConversationPanel').dataset.mode !== 'conversation') $('conversationModeTitle').textContent = liveModelName + ' reasoning';
  }
  function renderAstraStream(live) {
    const events = live?.events || [];
    const latest = [...events].reverse().find(event => astraStreamNote(event));
    const running = ['preparing', 'running'].includes(live?.status);
    const output = (latest && astraStreamNote(latest)) || (live
      ? (running ? `Waiting for ${liveModelName}’s first note…` : `No ${liveModelName} note was recorded for this run.`)
      : `${liveModelName}’s next note will appear here.`);
    const body = $('astraStreamOutput');
    if (body.textContent !== output) {
      body.textContent = output;
      body.scrollTop = 0;
    }
    $('astraStreamState').textContent = running ? 'Live' : live ? 'Run ended' : 'Waiting for a run';
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
    const parkedYam = robotId === 'yam-1' && mode === 'DISABLED' && station.queue_ready === true;
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
    const live = state.public_run;
    syncStopwatch(live);
    $('currentRunner').textContent = 'Runner: ' + (live?.runner_name || '—');
    $('currentPrompt').textContent = live?.task || 'Waiting for someone to run a policy.';
    $('conversationState').textContent = (live?.status || 'Waiting for a run').replaceAll('_', ' ') + (live?.error ? ' · ' + live.error : '');
    applyModelName(live);
    renderConversation(live?.events || [], 'liveConversationMessages');
    renderAstraStream(live);
    $('sideRunner').textContent = $('currentRunner').textContent;
    $('sidePrompt').textContent = $('currentPrompt').textContent;
    $('sideConversationState').textContent = $('conversationState').textContent;
    renderConversation(live?.events || [], 'sideConversationMessages');
    $('sideConversationTitle').textContent = $('liveConversationPanel').dataset.mode === 'reasoning' ? 'Response notes' : 'Conversation with ' + liveModelName;
    active = ['queued', 'preparing', 'running'].includes(state.status);
    ownRunLive = ['preparing', 'running'].includes(state.status);
    $('status').textContent = state.status.replaceAll('_', ' ');
    guideRunAttention(state);
    if (contactRequested || (!contactDismissed && state.error?.startsWith('Operator request failed:'))) operatorContact();
    else if (contactDismissed && state.error?.startsWith('Operator request failed:')) { /* Keep dismissed contact information closed. */ }
    else if (state.error || state.execution_blocked_reason) message(state.error || state.execution_blocked_reason, true);
    else if (state.feedback_warning) message(state.feedback_warning);
    else if ($('message').textContent.startsWith('Motion paused while refreshing robot status:')) message('Robot status confirmed. Continuing the run.');
    else if (state.provider?.provider === 'groot' && state.provider.warmup === 'warming') message('In the robot queue · warming GR00T GPU…');
    else if (state.provider?.provider === 'groot' && state.provider.warmup === 'failed') message('GR00T GPU startup failed. Leave the queue and try again.', true);
    else if (state.provider?.vision?.retry?.state === 'retrying') message(state.provider.vision.retry.message);
    else if ($('message').textContent.startsWith('Waiting for next ')) message('Camera feeds recovered. Continuing the run.');
    const queue = state.queue_snapshot;
    const sharedError = state.public_run?.error || state.robot_fault;
    $('publicRunError').hidden = !sharedError;
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
    $('queueSummary').textContent = queue ? `Queue · ${waiting} waiting` : 'Queue · unavailable';
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
      $('conversationModeTitle').textContent = mode === 'reasoning' ? liveModelName + ' reasoning' : 'Convo mode';
      $('sideConversationTitle').textContent = mode === 'reasoning' ? 'Response notes' : 'Conversation with ' + liveModelName;
      document.querySelectorAll('[data-conversation-mode]').forEach(item => {
        item.setAttribute('aria-pressed', String(item === button));
      });
      renderConversation(sideConversationEvents, 'sideConversationMessages');
    });
  });
  function renderConversation(events, target = 'conversationMessages') {
    if (target === 'sideConversationMessages') sideConversationEvents = events;
    const reasoning = target === 'sideConversationMessages' && $('liveConversationPanel').dataset.mode === 'reasoning';
    const messages = events.filter(event => ['model_request', 'model_response'].includes(event.kind));
    const snapshot = JSON.stringify([reasoning, messages]);
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

      if (reasoning && (event.kind !== 'model_response' || !astraStreamNote(event, true))) return null;
      const li = document.createElement('li'), heading = document.createElement('strong'), body = document.createElement('p');
      li.classList.add('conversationTurn');
      li.classList.add(event.kind === 'model_request' ? 'conversationOutgoing' : 'conversationIncoming');
      heading.className = 'conversationHeading';
      const speaker = event.speaker || (event.kind === 'model_request' ? 'To ' + liveModelName : event.kind === 'model_response' ? liveModelName : 'Robot / tool feedback');
      const speakerLabel = document.createElement('span'), timestamp = document.createElement('time');
      speakerLabel.textContent = speaker;
      timestamp.textContent = new Date(event.timestamp * 1000).toLocaleTimeString([], {hour:'numeric', minute:'2-digit', second:'2-digit'});
      heading.append(speakerLabel, timestamp);
      body.textContent = event.kind === 'model_request' ? event.details?.request_text || event.details?.observation || event.message : event.details?.response || event.message;
      if (freshText !== null) body.textContent = freshText;
      if (reasoning) body.textContent = astraStreamNote(event, true);
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
    function paint() {
      if (stopped) return;
      const stale = video.yamHealth?.stale() === true;
      if (!document.hidden && !stale && video.readyState >= 2 && !video.paused && video.currentTime > 0 && video.currentTime !== lastMediaTime) {
        // Snapshot ONCE, then crop all panels from that immutable canvas frame.
        // MDN drawImage nine-argument contract: docs/refs/canvas.
        context.drawImage(video, 0, 0, 1280, 720);
        frame++;
        tiles.forEach((tile, i) => {
          contexts[i].drawImage(atlas, (i%2)*640, Math.floor(i/2)*360, 640, 360, 0, 0, 640, 360);
          tile.dataset.presentedFrame = String(frame);
        });
        lastMediaTime = video.currentTime;
      }
      labels.forEach(label => { label.textContent = stale ? 'Stream stalled' : sourceLabel.textContent; });
      callback = requestAnimationFrame(paint);
    }
    document.querySelectorAll('[data-expand-camera]').forEach(button => {
      button.addEventListener('click', () => {
        const panel = button.closest('figure');
        if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
        else panel.requestFullscreen?.().catch(() => {});
      });
    });
    video.yamStopPainting = () => { stopped = true; cancelAnimationFrame(callback); };
    window.addEventListener('pagehide', video.yamStopPainting);
    paint();
  }
  function camera(image) {
    const label = image.parentElement.querySelector('figcaption span');
    const cameraName = image.dataset.camera, video = document.createElement('video');
    video.muted = true; video.autoplay = true; video.playsInline = true; video.controls = true;
    video.setAttribute('aria-label', image.alt); image.replaceWith(video);
    video.dataset.camera = cameraName;
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
          reader = new MediaMTXWebRTCReader({url: new URL(`/${cameraName}/whep`, location.href).href,
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
      const url = cameraName === 'synchronized' ? '/synchronized/index.m3u8' : `/live-video/hls/${cameraName}/index.m3u8`;
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
    $('videoDelayNotice').hidden = selectedRobot !== 'yam-1';
    $('liveViewer').dataset.layout = names.length === 1 ? 'single' : 'multi';
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
    if (selectedRobot === 'yam-1') {
      const roles = ['top', 'observer', 'left', 'right'];
      $('liveViewer').querySelectorAll('[data-sync-tile]').forEach(tile => addModelBadge(tile.parentElement, roles[Number(tile.dataset.syncTile)]));
      document.querySelectorAll('[data-camera]').forEach(camera);
      return;
    }
    $('liveViewer').querySelectorAll('figure').forEach(node => node.remove());
    const generation = robotGeneration;
    for (const name of names) {
      const figure = document.createElement('figure'), image = document.createElement('img'), caption = document.createElement('figcaption');
      image.alt = name + ' robot camera'; caption.textContent = name + ' · Connecting';
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
          caption.textContent = name + ' · Live';
        } catch {
          // Keep the last successful frame visible while reconnecting.
          if (generation === robotGeneration && epoch === cameraEpoch) caption.textContent = name + (image.hasAttribute('src') ? ' · Reconnecting (last frame)' : ' · Connecting');
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
        selectedRobot = robotCatalog.robots.find(robot => robot.connected === true)?.id || selectedRobot;
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
        $('provider').value = session.default_provider || (session.codex ? 'codex' : 'claude');
        $('runnerName').value ||= 'Local runner';
        document.title = 'BluPe · Local runner';
        providerChanged();
      }
      await window.yamApplication?.sessionReady?.(session, applicationContext());
      if (session.joint_policy) {
        $('provider').replaceChildren(...(session.local_runner ? [['codex','Codex subscription · joint control'],['openai','OpenAI · joint control']] : [['openai','OpenAI · joint control']]).map(([value,textContent])=>Object.assign(document.createElement('option'),{value,textContent})));
        // Joint-control robots have no Claude policy; keep the button off the form.
        $('runClaude').hidden = true; $('runAstra').hidden = true;
        providerChanged();
        $('policyHelp').textContent = session.local_runner ? 'Uses your ChatGPT sign-in through Codex. Enter a task; the operator starts your turn.' : 'Enter your OpenAI API key and a task; the operator starts your turn.';
      }
      $('runGroot').hidden = !session.groot_enabled;
      if (session.groot_enabled) $('provider').append(Object.assign(document.createElement('option'), {value:'groot',textContent:'GR00T'}));
      buttons(); if (!new URLSearchParams(location.search).has('purchase') && !$('message').textContent.startsWith('Payment')) message('');
      camerasDisconnected = robotCatalog?.robots.find(robot => robot.id === selectedRobot)?.connected === false;
      modelCameraNames = new Set(session.model_cameras || []);
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
    open.hidden = visible;
    panel.closest('main').classList.toggle('chatClosed', !visible);
    open.setAttribute('aria-expanded', String(visible));
    close.setAttribute('aria-expanded', String(visible));
    if (userAction) {
      try { localStorage.setItem("yam-chat-open", String(visible)); } catch (_) {}
      (visible ? close : open).focus();
    }
  }
  try { toggle(localStorage.getItem("yam-chat-open") !== "false", false); } catch (_) {}
  close.addEventListener('click', () => toggle(false));
  open.addEventListener('click', () => toggle(true));
})();
