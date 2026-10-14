'use strict';

(() => {
  const tabs = [...document.querySelectorAll('[data-settings-tab]')];
  if (!tabs.length) return;

  function activate(tab, moveFocus = false, revealPersonnel = false) {
    for (const candidate of tabs) {
      const selected = candidate === tab;
      candidate.setAttribute('aria-selected', String(selected));
      candidate.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(candidate.getAttribute('aria-controls'));
      if (panel) panel.hidden = !selected;
    }
    if (moveFocus) tab.focus();
    if (revealPersonnel && tab.dataset.settingsTab === 'workers') {
      document.getElementById('personnelPanel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener('click', () => activate(tab, false, true));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault();
      activate(tabs[next], true);
    });
  }

  activate(tabs.find(tab => tab.getAttribute('aria-selected') === 'true') || tabs[0]);
})();
