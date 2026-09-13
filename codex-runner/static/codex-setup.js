/* Local runner only. Authentication remains in the Codex CLI. */
(() => {
  const provider = document.getElementById('provider');
  const panel = document.getElementById('codexSetup');
  const button = document.getElementById('codexCheck');
  const result = document.getElementById('codexResult');
  function update() {
    const codex = provider.value === 'codex';
    panel.hidden = !codex;
    document.getElementById('forget').hidden = codex;
    document.getElementById('apiSetupHelp').hidden = codex;
  }
  provider.addEventListener('change', update);
  // Initial provider is loaded asynchronously by the existing config request.
  const initial = setInterval(() => {
    update();
    if (cfg) clearInterval(initial);
  }, 100);
  button.addEventListener('click', async () => {
    button.disabled = true;
    result.textContent = 'Checking local Codex sign-in…';
    try {
      const status = await post('/api/codex/check');
      result.textContent = status.message;
      cfg = await config();
    } catch (error) {
      result.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  update();
})();
