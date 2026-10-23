// Shared appearance, including native top-layer dialogs.
(() => {
  const pendingButtons = new Map(), animations = new Map();
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  function pendingButton(button, label, iconOnly = false) {
    if (!button || pendingButtons.has(button)) return null;
    const original = {html:button.innerHTML, label:button.getAttribute('aria-label'), busy:button.getAttribute('aria-busy'), disabled:button.getAttribute('aria-disabled')};
    const form = button.closest('form'), formBusy = form?.getAttribute('aria-busy');
    button.dataset.pending = 'true';
    button.setAttribute('aria-disabled', 'true'); button.setAttribute('aria-busy', 'true');
    button.setAttribute('aria-label', label);
    button.innerHTML = `<span class="button-rest" aria-hidden="true">${original.html}</span><span class="button-working" aria-hidden="true"><span class="busy-spinner"></span>${iconOnly ? '' : '<span></span>'}</span>`;
    if (!iconOnly) button.querySelector('.button-working>span:last-child').textContent = label;
    if (form) form.setAttribute('aria-busy', 'true');
    const status = document.getElementById('loading-status');
    if (status) status.textContent = label;
    const restore = () => {
      if (!pendingButtons.delete(button)) return;
      delete button.dataset.pending;
      button.innerHTML = original.html;
      for (const [name, value] of [['aria-label', original.label], ['aria-busy', original.busy], ['aria-disabled', original.disabled]]) {
        if (value === null) button.removeAttribute(name); else button.setAttribute(name, value);
      }
      if (form) { if (formBusy === null) form.removeAttribute('aria-busy'); else form.setAttribute('aria-busy', formBusy); }
      if (status?.textContent === label) status.textContent = '';
    };
    pendingButtons.set(button, restore);
    return restore;
  }
  function cancelReveals() {
    for (const [animation, cleanup] of animations) { animation.cancel(); cleanup(); }
    animations.clear();
  }
  function reveal(content, overlay = null) {
    if (overlay) { overlay.setAttribute('aria-hidden', 'true'); overlay.inert = true; overlay.style.opacity = '0'; }
    content.style.opacity = '1';
    if (reduced.matches || !content.animate) { overlay?.remove(); return; }
    const style = getComputedStyle(document.documentElement);
    const duration = parseFloat(style.getPropertyValue('--dur')) || 180;
    const options = {duration, easing:style.getPropertyValue('--ease').trim() || 'ease-out'};
    for (const [target, frames] of [[content,[{opacity:0},{opacity:1}]], ...(overlay ? [[overlay,[{opacity:1},{opacity:0}]]] : [])]) {
      const animation = target.animate(frames, options);
      const cleanup = () => { if (target === overlay) overlay.remove(); animations.delete(animation); };
      animations.set(animation, cleanup);
      animation.finished.then(cleanup, cleanup);
    }
  }
  reduced.addEventListener('change', () => { if (reduced.matches) cancelReveals(); });
  window.PortalLoading = {pendingButton, reveal, cancelReveals};
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
    const auth = document.body.classList.contains('auth-page');
    if (!auth && event.target.getAttribute('action') !== '/logout') return;
    if (event.submitter?.hasAttribute('data-pending')) { event.preventDefault(); return; }
    const action = event.target.getAttribute('action');
    pendingButton(event.submitter, action === '/login' ? 'Signing in…' : action === '/setup' ? 'Creating account…' : action === '/logout' ? 'Signing out…' : 'Joining workspace…');
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) for (const restore of pendingButtons.values()) restore();
  });
})();
