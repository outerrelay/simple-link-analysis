/** Right-click menu on the canvas.
 *
 * The menu is built from the ontology: a node is only offered relationship
 * types its own entity type can actually be the source of, so a Person is
 * never asked to "expand by issued tender".
 *
 * M3 wires up the actions that read from the database. The actions that
 * reach outside it — registry lookups, online search — arrive in M4 and M5 as
 * additional items in this same menu.
 */

let open = null;

export function closeMenu() {
  if (open) {
    open.remove();
    open = null;
  }
}

document.addEventListener('click', closeMenu);
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeMenu();
});

/**
 * @param position  {x, y} in page coordinates
 * @param sections  [{ label?, items: [{ label, detail?, disabled?, danger?, onSelect }] }]
 * @param heading   { title, subtitle }
 */
export function showMenu(position, sections, heading) {
  closeMenu();

  const menu = document.createElement('div');
  menu.className = 'context-menu';
  menu.addEventListener('click', (event) => event.stopPropagation());

  if (heading) {
    const head = document.createElement('div');
    head.className = 'heading';
    const title = document.createElement('strong');
    title.textContent = heading.title;
    const subtitle = document.createElement('span');
    subtitle.textContent = heading.subtitle ?? '';
    head.append(title, subtitle);
    menu.append(head);
  }

  sections.forEach((section, index) => {
    if (index > 0) {
      const rule = document.createElement('div');
      rule.className = 'separator';
      menu.append(rule);
    }
    if (section.label) {
      const label = document.createElement('div');
      label.className = 'submenu-label';
      label.textContent = section.label;
      menu.append(label);
    }
    for (const item of section.items) {
      const button = document.createElement('button');
      button.textContent = item.label;
      if (item.detail) button.title = item.detail;
      button.disabled = Boolean(item.disabled);
      if (item.danger) button.classList.add('danger');
      button.addEventListener('click', () => {
        closeMenu();
        item.onSelect();
      });
      menu.append(button);
    }
  });

  // Place it, then nudge back inside the viewport if it would overflow.
  menu.style.left = `${position.x}px`;
  menu.style.top = `${position.y}px`;
  document.body.append(menu);

  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth) {
    menu.style.left = `${Math.max(4, window.innerWidth - rect.width - 8)}px`;
  }
  if (rect.bottom > window.innerHeight) {
    menu.style.top = `${Math.max(4, window.innerHeight - rect.height - 8)}px`;
  }

  open = menu;
  return menu;
}
