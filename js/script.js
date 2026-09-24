/* 雨季少年的博客 — 交互层
   主题切换 / 本地搜索 / 分类筛选 / 复制链接 / 阅读进度 / 回到顶部 / 目录高亮
   全部为渐进增强：脚本不可用时页面文字与导航仍然完整可读。 */
(() => {
  'use strict';

  const root = document.documentElement;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');

  const store = {
    key: 'rain-theme',
    read() {
      try {
        return window.localStorage.getItem(this.key);
      } catch (error) {
        return null; // 隐私模式 / 存储被禁用时按未设置处理
      }
    },
    write(value) {
      try {
        window.localStorage.setItem(this.key, value);
      } catch (error) {
        /* 写入失败不影响当前会话的显示 */
      }
    }
  };

  if (!(window.CSS && CSS.supports &&
      (CSS.supports('backdrop-filter', 'blur(2px)') || CSS.supports('-webkit-backdrop-filter', 'blur(2px)')))) {
    root.classList.add('no-backdrop');
  }

  /* --- 深浅色：默认跟随系统，用户选择后持久化 ---------------------------- */
  const themeToggle = document.querySelector('[data-theme-toggle]');
  const syncToggle = (theme) => {
    if (!themeToggle) return;
    const dark = theme === 'dark';
    themeToggle.setAttribute('aria-pressed', String(dark));
    themeToggle.setAttribute('aria-label', dark ? '切换到浅色模式' : '切换到深色模式');
    themeToggle.setAttribute('title', dark ? '切换到浅色模式' : '切换到深色模式');
  };
  const applyTheme = (theme, persist) => {
    root.setAttribute('data-theme', theme);
    if (persist) {
      root.setAttribute('data-theme-choice', 'user');
      store.write(theme);
    }
    syncToggle(theme);
  };

  applyTheme(root.getAttribute('data-theme') || (prefersDark.matches ? 'dark' : 'light'), false);

  if (themeToggle) {
    themeToggle.hidden = false;
    themeToggle.addEventListener('click', () => {
      applyTheme(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark', true);
    });
  }
  const followSystem = () => {
    if (root.getAttribute('data-theme-choice') === 'user') return; // 已显式选择，不再跟随系统
    applyTheme(prefersDark.matches ? 'dark' : 'light', false);
  };
  if (typeof prefersDark.addEventListener === 'function') {
    prefersDark.addEventListener('change', followSystem);
  } else if (typeof prefersDark.addListener === 'function') {
    prefersDark.addListener(followSystem);
  }

  /* --- 本地搜索对话框 ---------------------------------------------------- */
  const dialog = document.querySelector('#search-dialog');
  const input = document.querySelector('#search-input');
  const trigger = document.querySelector('[data-open-search]');
  const searchStatus = document.querySelector('#search-status');

  if (dialog && input && trigger && typeof dialog.showModal === 'function') {
    trigger.hidden = false;
    const entries = [...dialog.querySelectorAll('.search-item')];

    const runSearch = () => {
      const terms = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
      let count = 0;
      entries.forEach((entry) => {
        const haystack = (entry.dataset.search || '').toLocaleLowerCase();
        const hit = terms.every((term) => haystack.includes(term));
        entry.hidden = !hit;
        if (hit) count += 1;
      });
      if (!searchStatus) return;
      if (!terms.length) {
        searchStatus.textContent = `共 ${entries.length} 篇文章，输入关键词开始筛选。`;
      } else if (count) {
        searchStatus.textContent = `找到 ${count} 篇文章。`;
      } else {
        searchStatus.textContent = '没有找到相关文章，换一个关键词试试。';
      }
    };

    const openSearch = () => {
      if (dialog.open) return;
      dialog.showModal();
      runSearch();
      input.focus();
      input.select();
    };
    const closeSearch = () => {
      if (dialog.open) dialog.close();
    };

    runSearch();
    trigger.addEventListener('click', openSearch);
    const closeButton = dialog.querySelector('[data-close-search]');
    if (closeButton) closeButton.addEventListener('click', closeSearch);
    dialog.addEventListener('close', () => trigger.focus());
    dialog.addEventListener('click', (event) => {
      const rect = dialog.getBoundingClientRect();
      const inside = event.clientX >= rect.left && event.clientX <= rect.right &&
        event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (event.target === dialog && !inside) closeSearch();
    });
    input.addEventListener('input', runSearch);

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && dialog.open) {
        event.preventDefault();
        closeSearch();
        return;
      }
      if (event.key !== '/' || dialog.open || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      const editing = target instanceof HTMLElement &&
        (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
      if (editing) return;
      event.preventDefault();
      openSearch();
    });
  }

  /* --- 分类筛选（首页卡片 / 归档时间线） --------------------------------- */
  const scope = document.querySelector('[data-filter-scope]');
  const filterBar = scope ? scope.querySelector('[data-filters]') : null;
  const chips = filterBar ? [...filterBar.querySelectorAll('[data-filter]')] : [];
  const filterItems = scope ? [...scope.querySelectorAll('[data-filter-items] [data-item]')] : [];
  const filterStatus = scope ? scope.querySelector('#filter-status') : null;

  if (scope && filterBar && chips.length && filterItems.length) {
    // 筛选条与侧栏提示在 HTML 中默认 hidden：这里确认脚本可用后再显示，
    // 无 JS 时页面只保留“分类锚点跳到列表”的回退。
    filterBar.hidden = false;
    scope.querySelectorAll('[data-filter-hint]').forEach((hint) => {
      hint.hidden = false;
    });
    const defaultStatus = filterStatus ? filterStatus.textContent : '';
    const scrollToBar = () => {
      filterBar.scrollIntoView({
        behavior: reduceMotion.matches ? 'auto' : 'smooth',
        block: 'start'
      });
    };
    const applyFilter = (value, options = {}) => {
      let visible = 0;
      filterItems.forEach((item) => {
        const hit = value === 'all' || item.dataset.category === value;
        item.hidden = !hit;
        if (hit) visible += 1;
      });
      chips.forEach((chip) => {
        chip.setAttribute('aria-pressed', String(chip.dataset.filter === value));
      });
      scope.querySelectorAll('[data-group]').forEach((group) => {
        const count = group.querySelectorAll('[data-item]:not([hidden])').length;
        const label = group.querySelector('[data-count]');
        if (label) label.textContent = `${count} 篇`;
        group.hidden = count === 0;
      });
      if (filterStatus) {
        filterStatus.textContent = value === 'all'
          ? defaultStatus
          : `筛选「${value}」：显示 ${visible} 篇手记`;
      }
      if (options.scroll) scrollToBar();
    };

    filterBar.addEventListener('click', (event) => {
      const chip = event.target instanceof Element ? event.target.closest('[data-filter]') : null;
      if (chip) applyFilter(chip.dataset.filter);
    });

    // 侧栏分类：跳转到筛选按钮并应用筛选（无 JS 时仍可跳到列表）
    scope.querySelectorAll('[data-filter-value]').forEach((link) => {
      link.addEventListener('click', (event) => {
        const value = link.dataset.filterValue;
        if (value !== 'all' && !chips.some((chip) => chip.dataset.filter === value)) return;
        event.preventDefault();
        applyFilter(value, { scroll: true });
      });
    });

    applyFilter('all');
  }

  /* --- 复制文章链接 ------------------------------------------------------ */
  const copyButton = document.querySelector('[data-copy-url]');
  const copyStatus = document.querySelector('#copy-status');
  if (copyButton && navigator.clipboard && window.isSecureContext) {
    copyButton.hidden = false;
    copyButton.addEventListener('click', async () => {
      const canonical = document.querySelector('link[rel="canonical"]');
      const url = canonical ? canonical.href : window.location.href;
      try {
        await navigator.clipboard.writeText(url);
        if (copyStatus) copyStatus.textContent = '链接已复制';
      } catch (error) {
        if (copyStatus) copyStatus.textContent = '未能自动复制，请从地址栏复制链接';
      }
      window.setTimeout(() => {
        if (copyStatus) copyStatus.textContent = '';
      }, 4000);
    });
  }

  /* --- 滚动相关：吸顶样式、阅读进度、回到顶部、目录高亮 ------------------ */
  const topbar = document.querySelector('.topbar');
  const toTop = document.querySelector('[data-to-top]');
  const progress = document.querySelector('[data-progress]');
  const isPost = document.body.classList.contains('page-post');
  // 只统计目录列表内的锚点：侧栏“回到文章顶部”链接也以 # 开头，不能参与高亮。
  const tocLinks = isPost
    ? [...document.querySelectorAll('.widget-toc .toc-list a[href^="#"]')]
    : [];
  const tocTargets = tocLinks.map((link) => {
    const id = decodeURIComponent(link.getAttribute('href').slice(1));
    return document.getElementById(id);
  });

  if (toTop) {
    toTop.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: reduceMotion.matches ? 'auto' : 'smooth' });
    });
  }

  let ticking = false;
  const update = () => {
    ticking = false;
    const offset = window.scrollY || window.pageYOffset || 0;

    if (topbar) topbar.classList.toggle('is-scrolled', offset > 12);
    if (toTop) toTop.hidden = offset < 600;

    if (progress && isPost) {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const ratio = max > 0 ? Math.min(1, Math.max(0, offset / max)) : 0;
      progress.style.width = `${(ratio * 100).toFixed(2)}%`;
    }

    if (tocTargets.length) {
      let active = -1;
      tocTargets.forEach((target, index) => {
        if (target && target.getBoundingClientRect().top <= 140) active = index;
      });
      tocLinks.forEach((link, index) => {
        const on = index === active;
        link.classList.toggle('is-active', on);
        if (on) {
          link.setAttribute('aria-current', 'true');
        } else {
          link.removeAttribute('aria-current');
        }
      });
    }
  };
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(update);
  };

  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
  update();
})();
