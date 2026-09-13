/* Frame callbacks and their timing fields: docs/refs/canvas/requestVideoFrameCallback.html. */
(() => {
  'use strict';
  class StreamHealth {
    constructor(video, {badge, detail, recover, report, clock = () => performance.now()}) {
      Object.assign(this, {video, badge, detail, recover, report, clock});
      this.started = clock(); this.lastFrame = null; this.frameTimes = [];
      this.delay = null; this.previous = null; this.lastRecovery = -Infinity;
      this.closed = false; this.frameId = null;
      const presented = (now, metadata) => {
        if (this.closed) return;
        if (!document.hidden && video.readyState >= 2 && !video.paused) {
          this.lastFrame = this.clock();
          this.frameTimes.push(this.lastFrame);
          this.frameTimes = this.frameTimes.filter(t => this.lastFrame - t <= 5000);
          // WebRTC captureTime is an estimate in this browser's clock domain.
          this.delay = Number.isFinite(metadata.captureTime) && this.lastFrame >= metadata.captureTime
            ? this.lastFrame - metadata.captureTime : null;
        }
        this.frameId = video.requestVideoFrameCallback(presented);
      };
      if (video.requestVideoFrameCallback) this.frameId = video.requestVideoFrameCallback(presented);
      else video.addEventListener('timeupdate', () => {
        if (!this.closed && !document.hidden && !video.paused && video.readyState >= 2 && video.currentTime !== this.lastMediaTime) {
          this.lastMediaTime = video.currentTime; this.lastFrame = this.clock();
        }
      });
      this.interval = setInterval(() => this.tick(), 1000);
      document.addEventListener('visibilitychange', () => {
        if (!document.hidden) { this.started = this.clock(); this.lastFrame = null; this.frameTimes = []; }
        this.tick();
      });
      window.addEventListener('pagehide', () => this.close());
      this.tick();
    }
    snapshot() {
      const now = this.clock(), age = this.lastFrame === null ? null : now - this.lastFrame;
      const transport = this.video.dataset.transport || 'unknown';
      let delay = this.delay;
      if (transport === 'hls') {
        delay = null;
        if (this.video.seekable.length) delay = Math.max(0, (this.video.seekable.end(this.video.seekable.length - 1) - this.video.currentTime) * 1000);
      }
      const recent = this.frameTimes.filter(t => now - t <= 5000);
      const span = recent.length > 1 ? recent[recent.length - 1] - recent[0] : 0;
      const fps = span > 0 ? (recent.length - 1) * 1000 / span : 0;
      let state = 'connecting';
      if (document.hidden) state = 'paused';
      else if (age === null) state = now - this.started >= 8000 ? 'stalled' : 'connecting';
      else if (age >= 3000) state = 'stalled';
      else if (delay !== null && delay > (transport === 'hls' ? 15000 : 2000)) state = 'behind';
      else state = transport === 'hls' ? 'delayed' : 'live';
      return {state, transport, frame_age_ms: age === null ? null : Math.round(age),
        delay_ms: delay === null ? null : Math.round(delay), fps: Math.round(fps * 10) / 10};
    }
    tick() {
      if (this.closed) return;
      const data = this.snapshot();
      const names = {connecting:'Checking stream', live:'Live video', delayed:'Video playing · delayed',
        behind:'Stream behind live', stalled:'Stream stalled · reconnecting', paused:'Stream paused in background'};
      if (this.badge) {
        this.badge.textContent = names[data.state]; this.badge.dataset.state = data.state;
        this.badge.title = 'Checks frames presented in this browser. Delay is estimated when available.';
      }
      if (this.detail) this.detail.textContent = ['live','delayed','behind'].includes(data.state)
        ? `${data.fps ? data.fps + ' fps' : 'Frames advancing'}${data.delay_ms !== null ? ' · ' + (data.delay_ms / 1000).toFixed(1) + 's estimated delay' : ''}` : '';
      if (data.state !== this.previous) {
        console.info('[stream-health]', data);
        this.report?.(data); this.previous = data.state;
      }
      const outage = this.lastFrame === null ? this.clock() - this.started : this.clock() - this.lastFrame;
      if (!document.hidden && this.clock() - this.lastRecovery >= 10000 &&
          ((data.state === 'stalled' && outage >= 8000) ||
           (data.state === 'behind' && data.delay_ms > (data.transport === 'hls' ? 20000 : 8000)))) {
        this.lastRecovery = this.clock(); this.recover();
      }
    }
    stale() { return this.snapshot().state === 'stalled'; }
    close() {
      this.closed = true; clearInterval(this.interval);
      if (this.frameId !== null) this.video.cancelVideoFrameCallback?.(this.frameId);
    }
  }
  window.YamStreamHealth = StreamHealth;
})();
