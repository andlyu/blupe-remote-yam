(() => {
  const baseRender = window.render;
  const queueState = document.getElementById("queueState");
  const stationReadout = document.getElementById("stationReadout");
  const queueList = document.getElementById("queueList");

  function renderQueue(snapshot) {
    if (!snapshot) {
      queueState.textContent = "CONNECTING";
      stationReadout.textContent = "Waiting for station availability";
      return;
    }
    const stations = Array.isArray(snapshot.stations) ? snapshot.stations : [];
    const station = stations.find(item => item.jetson_id === "yam-1") || stations[0];
    if (station) {
      const state = !station.connected ? "DISCONNECTED" : station.mode === "DISABLED" ? "ARMS DISABLED" : station.mode === "FAULT" ? "ARM FAULT" : station.available ? "AVAILABLE" : "WAITING / RESERVED";
      stationReadout.textContent = `${station.jetson_id} / ${state} / ${station.source || "unknown"} / ${station.mode || "unknown"}`;
      queueState.textContent = state;
      queueState.className = `feedback-badge ${station.connected ? (station.available ? "live" : "stale") : ""}`;
    }
    const entries = Array.isArray(snapshot.entries) ? snapshot.entries : [];
    if (!entries.length) {
      const empty = document.createElement("li");
      empty.className = "queue-empty";
      empty.textContent = "Queue is empty";
      queueList.replaceChildren(empty);
      return;
    }
    queueList.replaceChildren(...entries.map(entry => {
      const row = document.createElement("li");
      row.className = `queue-row${entry.is_mine ? " mine" : ""}`;
      const position = document.createElement("span");
      position.className = "queue-position";
      position.textContent = `#${entry.position}`;
      const id = document.createElement("span");
      id.className = "queue-id";
      id.textContent = entry.session_id;
      const status = document.createElement("span");
      status.textContent = entry.status;
      const owner = document.createElement("span");
      owner.className = "queue-you";
      owner.textContent = entry.is_mine ? "YOU" : "";
      row.append(position, id, status, owner);
      return row;
    }));
  }

  window.render = state => {
    baseRender(state);
    renderQueue(state.queue_snapshot);
    const snapshot = state.queue_snapshot;
    const station = snapshot?.stations?.find(item => item.jetson_id === "yam-1");
    const myEntry = snapshot?.entries?.find(item => item.is_mine);
    const position = myEntry?.position ?? state.queue_position;
    const joinButton = document.getElementById("run");
    const joined = ["queued", "preparing", "running"].includes(state.status);
    joinButton.disabled = joined;
    joinButton.textContent = state.status === "queued"
      ? `Already queued${position != null ? ` — #${position}` : ""}`
      : joined ? "Session active" : "Join Queue";
    let waiting = state.execution_blocked_reason || "";
    if (state.status === "queued") {
      waiting = !station?.connected ? "Waiting for operator connection"
        : station.mode === "DISABLED" ? "Waiting for arms to be launched and prepared"
        : station.mode === "FAULT" ? "Waiting for arm fault recovery"
        : "Waiting for operator authorization and an available turn";
      if (state.hardware_control_enabled === false) waiting += "; hardware execution is not enabled";
      document.getElementById("status").textContent = `Queued${position != null ? ` — #${position}` : ""}`;
      document.getElementById("heartbeat").textContent = "Not started — no active episode";
      document.getElementById("command").textContent = "No commands sent";
    }
    let note = document.getElementById("runnerWaitReason");
    if (!note) {
      note = document.createElement("div");
      note.id = "runnerWaitReason";
      note.className = "notice";
      document.getElementById("status").closest("aside").append(note);
    }
    const age = snapshot?.generated_at == null ? null : (Date.now() / 1000 - snapshot.generated_at);
    const freshness = age == null || !Number.isFinite(age) ? "Queue freshness unavailable"
      : age > 5 ? `Queue feedback stale (${Math.round(age)}s old)`
      : age < -2 ? "Queue clock differs from this computer"
      : `Queue feedback current (${Math.max(0, Math.round(age))}s old)`;
    note.textContent = `${waiting || "Runner connected"}. ${freshness}. Last UI response: ${new Date().toLocaleTimeString()}`;
  };
})();
