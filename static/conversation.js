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
if (typeof module !== 'undefined') module.exports = {conversationCalls};
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
  function message(parent, item, currentVersion) {
    if (typeof item === 'string') { parent.append(element('pre', item)); return; }
    if (!item || typeof item !== 'object') return;
    const block = element('article'); block.className = 'conversation-message';
    block.append(element('h4', item.role || item.type || 'Message'));
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
    const currentVersion = ++version; clear();
    const call = calls.find(c => c.number === selected); if (!call) return;
    const sent = element('section'); sent.append(element('h3', 'Sent to model'));
    if (call.request) {
      sent.append(element('p', `Model: ${call.request.model || 'Unknown'}`));
      if (call.request.instructions) message(sent, call.request.instructions, currentVersion);
      for (const item of Array.isArray(call.request.input) ? call.request.input : [call.request.input]) message(sent, item, currentVersion);
      if (call.request.tools) raw(sent, 'Tools available to the model', call.request.tools);
      raw(sent, 'Full request JSON', call.request);
    } else sent.append(element('p', 'Request was not recorded.'));
    const reply = element('section'); reply.append(element('h3', 'Model response'));
    if (call.response) {
      for (const item of call.response.output || []) message(reply, item, currentVersion);
      raw(reply, 'Full response JSON', call.response);
    } else reply.append(element('p', 'No response recorded yet. If the call failed, see the Interaction log.'));
    content.append(sent, reply);
  }
  async function refresh() {
    if (panel.hidden || pending || !runId) return;
    pending = true; const requestedRun = runId, requestedVersion = version;
    try {
      const text = await (await get(`/api/recordings/${requestedRun}/calls.jsonl`)).text();
      if (requestedRun !== runId || requestedVersion !== version || panel.hidden) return;
      status.textContent = `Run ${runId.slice(-8)} · saved model requests and responses · updates while open`;
      if (text === signature) return;
      signature = text; const followLatest = selected === null || selected === calls.at(-1)?.number;
      calls = conversationCalls(text);
      if (followLatest) selected = calls.at(-1)?.number ?? null;
      selector.replaceChildren(...calls.map(call => new Option(`Call ${call.number}${call.response ? '' : ' — no response yet'}`, String(call.number))));
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
  selector.onchange = () => { selected = Number(selector.value); draw(); };
  window.addEventListener('runner-recording-selected', event => changeRun(event.detail || ''));
  setInterval(refresh, 2000);
})();
