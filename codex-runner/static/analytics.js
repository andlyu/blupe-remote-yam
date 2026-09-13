(() => {
  'use strict';
  // GA4 gtag event/config reference: docs/refs/google-analytics/INDEX.md.
  const measurementId = 'G-LWNRDNKM2J';
  const productionHost = 'blupe-yam.100-61-149-60.sslip.io';
  const models = new Set(['gpt-6-astra', 'astra-default', 'gpt-4.1', 'gpt-4o', 'gpt-5', 'gpt-5.4']);
  let enabled = false, route = 'direct';
  const pageEvents = new Set();
  function watchViews(entries, emit) {
    const pending = new Map(), seen = new Set();
    function visible(element) {
      if (document.hidden) return false;
      const r = element.getBoundingClientRect();
      const height = Math.min(r.bottom, innerHeight) - Math.max(r.top, 0);
      const width = Math.min(r.right, innerWidth) - Math.max(r.left, 0);
      return r.height > 0 && r.width > 0 && height >= Math.min(r.height, innerHeight) * 0.5
        && width >= Math.min(r.width, innerWidth) * 0.5;
    }
    function check() {
      for (const [name, element] of entries) {
        if (!element || seen.has(name)) continue;
        if (!visible(element)) { clearTimeout(pending.get(name)); pending.delete(name); }
        else if (!pending.has(name)) pending.set(name, setTimeout(() => {
          pending.delete(name);
          if (visible(element)) { seen.add(name); emit(name); }
        }, 1000));
      }
    }
    // Check layout shifts as well as scrolling. Timers reset on hidden tabs.
    const observer = new IntersectionObserver(check, {threshold: [0, 0.25, 0.5, 0.75, 1]});
    entries.forEach(([, element]) => { if (element) observer.observe(element); });
    window.addEventListener('scroll', check, {passive: true});
    window.addEventListener('resize', check);
    document.addEventListener('visibilitychange', check);
    check();
  }

  const sent = new Set();
  function parameters(provider, model) {
    // API use is a policy, not proof that the visitor used the ChatGPT product.
    const result = {control_method: 'other_policy', run_route: provider === 'stripe' ? 'paid' : 'direct'};
    if (provider === 'stripe') provider = 'openai';
    if (['openai', 'astra', 'local_raise_lower'].includes(provider)) result.provider = provider;
    if (models.has(model)) result.model_name = model;
    return result;
  }
  function emit(name, params = {}) {
    if (!enabled) return;
    try { window.gtag('event', name, {send_to: measurementId, ...params}); }
    catch (_) { /* Analytics must never interrupt robot controls. */ }
  }
  function once(id, name, params, dedupName = name) {
    const key = `yam-ga:${id}:${dedupName}`;
    if (sent.has(key)) return;
    try { if (localStorage.getItem(key)) return; } catch (_) {}
    emit(name, params);
    sent.add(key);
    try { localStorage.setItem(key, '1'); } catch (_) {}
  }
  window.yamAnalytics = {
    init(simulation, paid = false) {
      route = paid ? 'paid' : 'direct';
      if (enabled || simulation || location.hostname !== productionHost) return;
      enabled = true;
      window.dataLayer = window.dataLayer || [];
      window.gtag = function () { window.dataLayer.push(arguments); };
      window.gtag('js', new Date());
      // Leave the real URL (including _gl) intact for Google's linker, but send
      // only a fixed public URL/title and no potentially sensitive referrer.
      window.gtag('config', measurementId, {
        send_page_view: false, page_location: `https://${productionHost}/`,
        page_referrer: '', page_title: 'BluPe controller',
        allow_google_signals: false, allow_ad_personalization_signals: false
      });
      const tag = document.createElement('script');
      tag.async = true;
      tag.src = `https://www.googletagmanager.com/gtag/js?id=${measurementId}`;
      document.head.appendChild(tag);
      emit('page_view');
      emit('controller_loaded');
      watchViews([
        ['live_video_viewed', document.querySelector('.cameras')],
        ['current_run_viewed', document.getElementById('currentPrompt')],
        ['run_setup_viewed', document.querySelector('#runForm').previousElementSibling]
      ], name => emit(name, {run_route: route}));
    },
    selected(provider, model) {
      route = provider === 'stripe' ? 'paid' : 'direct';
      emit('provider_selected', parameters(provider, model));
    },
    requested(provider, model) { emit('run_requested', parameters(provider, model)); },
    checkout(provider, model) {
      if (!enabled) return Promise.resolve();
      return new Promise(resolve => {
        const timeout = setTimeout(resolve, 700);
        emit('checkout_started', {...parameters(provider, model),
          event_callback: () => { clearTimeout(timeout); resolve(); }, event_timeout: 700});
      });
    },
    paid(order) { once(order, 'payment_succeeded', {provider: 'openai', run_route: 'paid'}); },
    playback(camera) {
      if (!['top', 'observer', 'left', 'right'].includes(camera) || pageEvents.has(camera)) return;
      pageEvents.add(camera); emit('live_video_playback_started', {camera});
    },
    rejected(provider, model) {
      emit(provider === 'stripe' ? 'checkout_failed' : 'run_failed', {...parameters(provider, model), run_outcome: 'request_failed'});
    },
    observe(state) {
      if (!enabled || !state.analytics_run) return;
      const run = state.analytics_run;
      if (!/^[a-f0-9]{32}$/.test(run.run_id)) return;
      const params = {...parameters(run.provider, run.model), run_route: run.run_route === 'paid' ? 'paid' : 'direct', run_id: run.run_id};
      if (run.execution_confirmed) once(run.run_id, 'run_started', params);
      if (!['resetting', 'stopped', 'timed_out', 'disconnected', 'safety_aborted'].includes(state.status)) return;
      const failed = !!state.error || ['timed_out', 'disconnected', 'safety_aborted'].includes(state.status);
      // A cancellation before any motion is not a successful run.
      const outcome = failed ? 'failed' : run.execution_confirmed ? 'finished' : 'cancelled_before_start';
      this.finish(run.run_id, failed || !run.execution_confirmed, {...params, run_outcome: outcome});
    },
    finish(id, failed, params) {
      once(id, failed ? 'run_failed' : 'run_completed', params, 'terminal');
    }
  };
})();
