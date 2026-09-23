(() => {
  'use strict';
  const filters = document.querySelector('.filters');
  if (filters) {
    filters.hidden = false;
    const cards = [...document.querySelectorAll('.post-card')];
    filters.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-filter]');
      if (!button) return;
      filters.querySelectorAll('button').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
      cards.forEach((card) => { card.hidden = button.dataset.filter !== 'all' && card.dataset.category !== button.dataset.filter; });
      document.querySelector('#filter-status').textContent = `显示 ${cards.filter((card) => !card.hidden).length} 篇手记`;
    });
  }

  const dialog = document.querySelector('#search-dialog');
  const input = document.querySelector('#search-input');
  const trigger = document.querySelector('[data-open-search]');
  if (dialog && input && trigger && typeof dialog.showModal === 'function') {
    trigger.hidden = false;
    const results = [...dialog.querySelectorAll('[data-search]')];
    const search = () => {
      const terms = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
      results.forEach((item) => { item.hidden = !terms.every((term) => item.dataset.search.toLocaleLowerCase().includes(term)); });
      const count = results.filter((item) => !item.hidden).length;
      document.querySelector('#search-status').textContent = count ? `找到 ${count} 篇手记` : '没有找到相关手记，试试其他关键词。';
    };
    const open = () => { if (!dialog.open) { dialog.showModal(); search(); input.focus(); } };
    trigger.addEventListener('click', open);
    dialog.querySelector('[data-close-search]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', (event) => {
      const rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    input.addEventListener('input', search);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && dialog.open) {
        event.preventDefault();
        dialog.close();
        return;
      }
      const editing = event.target instanceof HTMLElement && (event.target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName));
      if (event.key === '/' && !editing && !event.metaKey && !event.ctrlKey && !event.altKey) { event.preventDefault(); open(); }
    });
  }

  const copy = document.querySelector('[data-copy-url]');
  if (copy && navigator.clipboard && window.isSecureContext) {
    copy.hidden = false;
    copy.addEventListener('click', async () => {
      const status = document.querySelector('#copy-status');
      try {
        await navigator.clipboard.writeText(document.querySelector('link[rel="canonical"]').href);
        status.textContent = '链接已复制';
      } catch {
        status.textContent = '未能复制，请从地址栏复制链接';
      }
    });
  }
})();
