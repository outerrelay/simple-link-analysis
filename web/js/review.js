/** The review panel: deciding what an action proposed.
 *
 * Nothing an action returns is in the database yet. This panel is where that
 * is decided, item by item — accepting eight of twelve officers is a normal
 * outcome, so the unit of decision is the item, not the set.
 */

import { api } from './api.js';

let current = null;
let callbacks = {};

const $ = (id) => document.getElementById(id);

export function initReview(handlers) {
  callbacks = handlers;
  $('review-close').addEventListener('click', close);
  $('review-accept').addEventListener('click', acceptChecked);
  $('review-reject').addEventListener('click', rejectAll);
  $('review-select-all').addEventListener('change', (event) => {
    for (const box of checkboxes()) box.checked = event.target.checked;
    updateCount();
  });
}

export function close() {
  $('review').hidden = true;
  if (current) callbacks.onDismiss?.(current);
  current = null;
}

export function isOpen() {
  return current !== null;
}

/** Show a proposal set for decision. */
export function showProposal(proposal) {
  current = proposal;

  $('review-title').textContent = actionLabel(proposal.action_id);
  $('review-summary').textContent = proposal.summary || '';

  const list = $('review-items');
  list.replaceChildren();

  const pending = proposal.items.filter((item) => item.decision === 'pending');
  if (pending.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'Nothing new — everything here was already decided.';
    list.append(empty);
  }

  for (const item of pending) {
    list.append(renderItem(item));
  }

  $('review-select-all').checked = true;
  updateCount();
  $('review').hidden = false;
}

function renderItem(item) {
  const row = document.createElement('label');
  row.className = `review-item ${item.kind}`;

  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = true;
  box.dataset.itemId = item.id;
  box.addEventListener('change', updateCount);

  const text = document.createElement('span');
  text.className = 'review-item-text';
  const label = document.createElement('strong');
  label.textContent = item.label;
  const detail = document.createElement('span');
  detail.className = 'detail';
  detail.textContent = item.detail || (item.kind === 'entity' ? 'new entity' : 'relationship');
  text.append(label, detail);

  row.append(box, text);

  // Hovering a row highlights the element it refers to on the canvas.
  row.addEventListener('mouseenter', () => callbacks.onHighlight?.(item));
  row.addEventListener('mouseleave', () => callbacks.onHighlight?.(null));
  return row;
}

function checkboxes() {
  return [...$('review-items').querySelectorAll('input[type="checkbox"]')];
}

function updateCount() {
  const boxes = checkboxes();
  const checked = boxes.filter((b) => b.checked).length;
  $('review-count').textContent = `${checked} of ${boxes.length}`;
  $('review-accept').disabled = checked === 0;
}

async function acceptChecked() {
  if (!current) return;
  const boxes = checkboxes();
  const accepted = boxes.filter((b) => b.checked).map((b) => b.dataset.itemId);
  const rejected = boxes.filter((b) => !b.checked).map((b) => b.dataset.itemId);

  $('review-accept').disabled = true;
  try {
    const result = await api.decideProposal(current.id, accepted, rejected);
    const setId = current.id;
    current = null;
    $('review').hidden = true;
    callbacks.onDecided?.(result, { accepted: accepted.length, rejected: rejected.length, setId });
  } catch (error) {
    callbacks.onError?.(error);
    $('review-accept').disabled = false;
  }
}

async function rejectAll() {
  if (!current) return;
  const setId = current.id;
  try {
    const result = await api.rejectProposal(setId);
    current = null;
    $('review').hidden = true;
    callbacks.onDecided?.(result, { accepted: 0, rejected: result.items.length, setId });
  } catch (error) {
    callbacks.onError?.(error);
  }
}

function actionLabel(actionId) {
  return (
    {
      'expand.database': 'Expanded from the database',
      'gleif.lookup': 'GLEIF LEI lookup',
      'companies_house.profile': 'Companies House: company details',
      'companies_house.officers': 'Companies House: officers',
    }[actionId] || actionId
  );
}
