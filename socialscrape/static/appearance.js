// Shared appearance, including native top-layer dialogs.
(() => {
  let theme = 'dark';
  try { theme = localStorage.getItem('socialscrape-theme') || theme; } catch {}
  document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark';
  function updateButtons() {
    const light = document.documentElement.dataset.theme === 'light';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-label', `Switch to ${light ? 'dark' : 'light'} theme`);
      button.title = button.getAttribute('aria-label');
      button.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/assets/brand/icons.svg#${light ? 'moon' : 'sun'}"></use></svg>`;
    });
  }
  document.addEventListener('DOMContentLoaded', updateButtons);
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-theme-toggle]')) return;
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('socialscrape-theme', next); } catch {}
    updateButtons();
  });
  document.addEventListener('submit', event => {
    if (!document.body.classList.contains('auth-page')) return;
    if (event.submitter) { event.submitter.disabled = true; event.submitter.textContent = 'Please wait…'; }
  });
})();
