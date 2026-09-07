// Render saved provider messages as text, never as executable HTML.
function conversationCalls(text) {
  const calls = new Map();
  for (const line of text.split('\n')) {
    let row; try { row = JSON.parse(line); } catch { continue; }
    if (!row || !['request', 'response'].includes(row.kind) || !Number.isInteger(row.call)) continue;
    if (!calls.has(row.call)) calls.set(row.call, {number: row.call});
    calls.get(row.call)[row.kind] = row[row.kind];
  }
  return [...calls.values()].sort((a, b) => a.number - b.number);
}
// Remove only the exact history prefix already displayed, retaining new repeated messages.
function conversationTurns(calls) {
  let previous = [];
  return calls.map(call => {
    const input = Array.isArray(call.request?.input) ? call.request.input : call.request?.input ? [call.request.input] : [];
    let shared = 0;
    while (shared < previous.length && shared < input.length && JSON.stringify(previous[shared]) === JSON.stringify(input[shared])) shared++;
    const output = Array.isArray(call.response?.output) ? call.response.output : [];
    previous = [...input, ...output];
    return {...call, messages: input.slice(shared), output};
  });
}
if (typeof module !== 'undefined') module.exports = {conversationCalls, conversationTurns};
if (typeof document !== 'undefined') (() => {
  const button = document.getElementById('conversationOpen');
  const panel = document.getElementById('runnerConversation');
  const selector = document.getElementById('conversationCall');
  const status = document.getElementById('conversationStatus');
  const content = document.getElementById('conversationContent');
  let runId = '', calls = [], selected = null, version = 0, pending = false, signature = '';
  let imageUrls = [];
  const element = (tag, text) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; };
  function clear() { imageUrls.forEach(url => URL.revokeObjectURL(url)); imageUrls = []; content.replaceChildren(); }
  async function get(path) {
    const response = await fetch(path, {cache: 'no-store', headers: {'X-YAM-Runner-Token': CSRF}});
    if (!response.ok) throw new Error(response.status === 404 ? 'No model conversation recorded for this run. Built-in policies do not call a model.' : 'Conversation unavailable. Try reopening it.');
    return response;
  }
  function raw(parent, label, data) {
    const details = element('details'); details.append(element('summary', label), element('pre', JSON.stringify(data, null, 2))); parent.append(details);
  }
  function message(parent, item, currentVersion, fromModel = false) {
    if (typeof item === 'string') item = {role: 'user', content: item};
    if (!item || typeof item !== 'object') return;
    const model = fromModel || item.role === 'assistant' || ['function_call', 'reasoning'].includes(item.type);
    const system = ['system', 'developer'].includes(item.role);
    const tool = item.type === 'function_call_output';
    const block = element('article'); block.className = `conversation-message ${system ? 'chat-system' : model ? 'chat-model' : 'chat-runner'}`;
    block.append(element('h4', system ? 'Instructions' : tool ? 'Runner · tool result' : model ? 'Model' : 'Runner'));
    if (system) { raw(block, 'System instructions', item); parent.append(block); return; }
    if (item.type === 'reasoning') {
      const summary = (item.summary || []).map(part => part.text || '').filter(Boolean).join('\n');
      block.append(element('p', summary || 'Reasoning data returned by the model.'));
      raw(block, 'Details', item); parent.append(block); return;
    }
    if (item.type === 'function_call') {
      block.append(element('p', `Tool call: ${item.name || 'Unknown tool'}`));
      let args = item.arguments; try { args = JSON.parse(args); } catch {}
      block.append(element('pre', typeof args === 'string' ? args : JSON.stringify(args, null, 2)));
      raw(block, 'Details', item); parent.append(block); return;
    }
    if (tool) {
      block.append(element('pre', typeof item.output === 'string' ? item.output : JSON.stringify(item.output, null, 2)));
      raw(block, 'Details', item); parent.append(block); return;
    }
    if (typeof item.content === 'string') block.append(element('pre', item.content));
    else if (Array.isArray(item.content)) {
      for (const part of item.content) {
        if (typeof part.text === 'string') block.append(element('pre', part.text));
        else if (part.type === 'input_image') {
          const match = /^data:image\/(?:jpeg|png);base64,\$blob:([a-f0-9]{64})$/.exec(part.image_url || '');
          const caption = element('p', 'Recorded model input image'); block.append(caption);
          if (match) {
            const image = element('img'); image.alt = 'Camera image sent to the model'; block.append(image);
            get(`/api/recordings/${runId}/blobs/${match[1]}`).then(r => r.blob()).then(blob => {
              if (currentVersion !== version || panel.hidden) return;
              const url = URL.createObjectURL(blob); imageUrls.push(url); image.src = url;
            }).catch(() => { if (currentVersion === version) caption.textContent = 'Recorded image unavailable'; });
          } else raw(block, 'Image reference', part);
        } else raw(block, part.type || 'Content', part);
      }
    } else block.append(element('pre', JSON.stringify(item, null, 2)));
    parent.append(block);
  }
  function draw() {
    const atBottom = content.scrollHeight - content.scrollTop - content.clientHeight < 70;
    const scrollTop = content.scrollTop;
    const currentVersion = ++version; clear();
    for (const call of conversationTurns(calls)) {
      if (selected !== null && call.number !== selected) continue;
      const turn = element('section'); turn.className = 'chat-turn';
      turn.append(element('p', `Call ${call.number} · ${call.request?.model || 'Model'}`));
      turn.firstChild.className = 'chat-divider';
      if (call.request?.instructions) message(turn, {role:'system', content:call.request.instructions}, currentVersion);
      for (const item of call.messages) message(turn, item, currentVersion);
      for (const item of call.output) message(turn, item, currentVersion, true);
      if (!call.response) {
        const pending = element('article'); pending.className = 'conversation-message chat-model chat-pending';
        pending.append(element('h4', 'Model'), element('p', 'No reply recorded yet…'), element('small', 'If the call failed, see the Interaction log.'));
        turn.append(pending);
      }
      const details = element('details'); details.className = 'chat-wire';
      details.append(element('summary', 'Call details'));
      if (call.request) raw(details, 'Full request JSON · includes history and tools', call.request);
      if (call.response) raw(details, 'Full response JSON', call.response);
      turn.append(details); content.append(turn);
    }
    content.scrollTop = atBottom ? content.scrollHeight : scrollTop;
  }
  async function refresh() {
    if (panel.hidden || pending || !runId) return;
    pending = true; const requestedRun = runId, requestedVersion = version;
    try {
      const text = await (await get(`/api/recordings/${requestedRun}/calls.jsonl`)).text();
      if (requestedRun !== runId || requestedVersion !== version || panel.hidden) return;
      status.textContent = `Run ${runId.slice(-8)} · saved model requests and responses · updates while open`;
      if (text === signature) return;
      signature = text;
      calls = conversationCalls(text);
      selector.replaceChildren(new Option('Full conversation', ''), ...calls.map(call => new Option(`Call ${call.number}${call.response ? '' : ' — no response yet'}`, String(call.number))));
      selector.disabled = !calls.length; selector.value = selected === null ? '' : String(selected);
      if (!calls.length) { clear(); status.textContent = 'No model calls recorded yet.'; } else draw();
    } catch (error) {
      if (requestedRun === runId && requestedVersion === version && !panel.hidden) status.textContent = error.message;
    } finally { pending = false; }
  }
  function changeRun(next) {
    if (next === runId) return;
    runId = next; version++; calls = []; selected = null; signature = ''; clear(); selector.replaceChildren(); selector.disabled = true;
    status.textContent = runId ? 'Loading conversation…' : 'Choose a recorded run or start a model run.';
    refresh();
  }
  button.onclick = () => {
    panel.hidden = !panel.hidden; button.setAttribute('aria-expanded', String(!panel.hidden));
    button.textContent = panel.hidden ? 'Open runner conversation' : 'Close runner conversation';
    if (panel.hidden) { version++; signature = ''; clear(); }
    else { panel.focus(); refresh(); }
  };
  selector.onchange = () => { selected = selector.value === '' ? null : Number(selector.value); draw(); };
  window.addEventListener('runner-recording-selected', event => changeRun(event.detail || ''));
  setInterval(refresh, 2000);
})();
