// Shared appearance, including native top-layer dialogs.
(() => {
  let theme = 'dark';
  try { theme = localStorage.getItem('socialscrape-theme') || theme; } catch {}
  document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark';
  let collapsed = false;
  try { collapsed = localStorage.getItem('socialscrape-sidebar') === 'collapsed'; } catch {}
  document.documentElement.dataset.sidebar = collapsed ? 'collapsed' : 'expanded';
  function updateSidebar() {
    const collapsed = document.documentElement.dataset.sidebar === 'collapsed';
    document.querySelectorAll('[data-sidebar-toggle]').forEach(button => {
      button.setAttribute('aria-expanded', String(!collapsed));
      button.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} sidebar`);
      button.title = button.getAttribute('aria-label');
    });
  }
  function updateButtons() {
    const light = document.documentElement.dataset.theme === 'light';
    const version = encodeURIComponent(document.documentElement.dataset.assetVersion || '');
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-label', `Switch to ${light ? 'dark' : 'light'} theme`);
      button.title = button.getAttribute('aria-label');
      button.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/assets/brand/icons.svg?v=${version}#${light ? 'moon' : 'sun'}"></use></svg>`;
    });
  }
  document.addEventListener('DOMContentLoaded', () => { updateButtons(); updateSidebar(); });
  document.addEventListener('click', event => {
    if (event.target.closest('[data-sidebar-toggle]')) {
      const next = document.documentElement.dataset.sidebar === 'collapsed' ? 'expanded' : 'collapsed';
      document.documentElement.dataset.sidebar = next;
      try { localStorage.setItem('socialscrape-sidebar', next); } catch {}
      updateSidebar();
      document.querySelectorAll('.account-popup:popover-open').forEach(popup => popup.hidePopover());
      return;
    }
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
