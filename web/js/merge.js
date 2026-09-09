/** The merge dialog.
 *
 * Merging is always the analyst's instruction, so the dialog shows exactly
 * what would happen before it happens: which record survives, which properties
 * disagree, and how many edges would move. Conflicts default to keeping the
 * survivor's values; switching to "choose field by field" turns every conflict
 * into an explicit decision.
 */

import { api } from './api.js';

let state = null;
let callbacks = {};

const $ = (id) => document.getElementById(id);

export function initMerge(handlers) {
  callbacks = handlers;
  $('merge-close').addEventListener('click', close);
  $('merge-cancel').addEventListener('click', close);
  $('merge-confirm').addEventListener('click', confirm);
  for (const radio of document.querySelectorAll('input[name="merge-mode"]')) {
    radio.addEventListener('change', () => renderConflicts(radio.value));
  }
  $('merge-modal').addEventListener('click', (event) => {
    if (event.target === $('merge-modal')) close();
  });
}

export function close() {
  $('merge-modal').hidden = true;
  state = null;
}

/** Open the dialog for a proposed merge, fetching the preview. */
export async function openMerge(survivorId, absorbedId) {
  try {
    const preview = await api.mergePreview(survivorId, absorbedId);
    state = { survivorId, absorbedId, preview };
    render();
    $('merge-modal').hidden = false;
  } catch (error) {
    callbacks.onError?.(error);
  }
}

function render() {
  const { preview } = state;
  $('merge-subtitle').textContent =
    `Two ${preview.entity_type} records combined into one.`;

  const sides = $('merge-sides');
  sides.replaceChildren(
    side('Survivor — kept', preview.survivor_name, true),
    arrow(),
    side('Absorbed — hidden', preview.absorbed_name, false),
  );

  const hasConflicts = preview.conflicts.length > 0;
  $('merge-conflicts-block').hidden = !hasConflicts;
  if (hasConflicts) renderConflicts(currentMode());

  const effects = [];
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  // Past participles throughout, so the phrasing agrees whether the count is
  // one or many.
  if (preview.edges_to_move) {
    effects.push(`${plural(preview.edges_to_move, 'relationship', 'relationships')} moved across`);
  }
  if (preview.edges_to_collapse) {
    effects.push(
      `${plural(preview.edges_to_collapse, 'duplicate relationship', 'duplicate relationships')}` +
        ` combined into one`,
    );
  }
  if (preview.self_edges_to_drop) {
    effects.push(
      `${plural(preview.self_edges_to_drop, 'relationship', 'relationships')} between the two ` +
        `dropped (they would point at themselves)`,
    );
  }
  const gained = Object.keys(preview.gained_properties).length;
  if (gained) {
    effects.push(
      `${plural(gained, 'property', 'properties')} the survivor lacked taken across`,
    );
  }
  $('merge-effect').textContent = effects.length
    ? effects.join(' · ')
    : 'No relationships or properties change hands.';
}

function side(role, name, isSurvivor) {
  const box = document.createElement('div');
  box.className = `merge-side ${isSurvivor ? 'survivor' : ''}`;
  const roleLabel = document.createElement('div');
  roleLabel.className = 'role';
  roleLabel.textContent = role;
  const who = document.createElement('div');
  who.className = 'who';
  who.textContent = name;
  box.append(roleLabel, who);

  if (isSurvivor) {
    const swap = document.createElement('button');
    swap.className = 'ghost swap';
    swap.textContent = 'Swap direction';
    swap.title = 'Keep the other record instead';
    swap.addEventListener('click', () => openMerge(state.absorbedId, state.survivorId));
    box.append(swap);
  }
  return box;
}

function arrow() {
  const element = document.createElement('div');
  element.className = 'merge-arrow';
  element.textContent = '←';
  return element;
}

function currentMode() {
  return document.querySelector('input[name="merge-mode"]:checked').value;
}

function renderConflicts(mode) {
  const container = $('merge-conflicts');
  container.replaceChildren();

  for (const conflict of state.preview.conflicts) {
    const row = document.createElement('div');
    row.className = `merge-conflict ${mode === 'survivor' ? 'resolved-automatically' : ''}`;

    const field = document.createElement('div');
    field.className = 'field';
    field.textContent = conflict.name.replaceAll('_', ' ');
    row.append(field);

    const choices = document.createElement('div');
    choices.className = 'choices';
    choices.append(
      choice(conflict, conflict.survivor_value, true, mode),
      choice(conflict, conflict.absorbed_value, false, mode),
    );
    row.append(choices);
    container.append(row);
  }
}

function choice(conflict, value, isSurvivor, mode) {
  const label = document.createElement('label');
  const radio = document.createElement('input');
  radio.type = 'radio';
  radio.name = `conflict-${conflict.name}`;
  radio.checked = isSurvivor;
  radio.disabled = mode === 'survivor';
  radio.dataset.field = conflict.name;
  radio.dataset.survivor = String(isSurvivor);

  const text = document.createElement('span');
  text.className = 'value';
  text.textContent = value === null || value === undefined ? '(not set)' : String(value);
  label.append(radio, text);
  return label;
}

/** The values the analyst chose, or {} when the survivor simply wins. */
function resolutions() {
  if (currentMode() === 'survivor') return {};
  const chosen = {};
  for (const conflict of state.preview.conflicts) {
    const selected = document.querySelector(
      `input[name="conflict-${conflict.name}"]:checked`,
    );
    if (selected && selected.dataset.survivor === 'false') {
      chosen[conflict.name] = conflict.absorbed_value;
    }
  }
  return chosen;
}

async function confirm() {
  const button = $('merge-confirm');
  button.disabled = true;
  try {
    const result = await api.merge(state.survivorId, state.absorbedId, resolutions());
    const survivorId = state.survivorId;
    const absorbedId = state.absorbedId;
    close();
    callbacks.onMerged?.(result, { survivorId, absorbedId });
  } catch (error) {
    callbacks.onError?.(error);
  } finally {
    button.disabled = false;
  }
}
