/** Adding entities and relationships by hand.
 *
 * Both dialogs are built from the ontology: the type list is what the ontology
 * declares, the fields come from that type's properties, and a connection can
 * only be made where the ontology permits one. Neither dialog knows what a
 * Company is.
 */

import { api } from './api.js';
import { buildPropertyForm, relationshipsBetween } from './forms.js';

let ontology = null;
let callbacks = {};
let form = null;
let context = null;

const $ = (id) => document.getElementById(id);

export function initCreate(loadedOntology, handlers) {
  ontology = loadedOntology;
  callbacks = handlers;

  $('create-close').addEventListener('click', close);
  $('create-cancel').addEventListener('click', close);
  $('create-confirm').addEventListener('click', submit);
  $('create-modal').addEventListener('click', (event) => {
    if (event.target === $('create-modal')) close();
  });
  $('create-type').addEventListener('change', renderFields);
  $('create-swap').addEventListener('click', swapDirection);
}

export function close() {
  $('create-modal').hidden = true;
  context = null;
  form = null;
}

/** Open the dialog for a new entity, to be placed at `position` on the canvas. */
export function openNewEntity(position) {
  context = { kind: 'entity', position };

  $('create-heading').textContent = 'Add an entity';
  $('create-subtitle').textContent =
    'Created directly — this is you asserting it, so there is nothing to review.';
  $('create-type-label').textContent = 'Type';
  $('create-confirm').textContent = 'Add entity';

  const select = $('create-type');
  select.replaceChildren();
  for (const [name, type] of Object.entries(ontology.entity_types).sort((a, b) =>
    a[1].label.localeCompare(b[1].label),
  )) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = type.label;
    select.append(option);
  }

  renderFields();
  $('create-modal').hidden = false;
  select.focus();
}

/**
 * Open the dialog for a new relationship between two nodes.
 *
 * Only relationship types the ontology permits between these two types are
 * offered; if there are none, the dialog says so rather than presenting an
 * empty list.
 */
export function openNewRelationship(source, target) {
  context = { kind: 'relationship', source, target };

  $('create-heading').textContent = 'Connect two entities';
  $('create-subtitle').textContent = `${source.label} → ${target.label}`;
  $('create-type-label').textContent = 'Relationship';
  $('create-confirm').textContent = 'Add relationship';

  const options = relationshipsBetween(ontology, source.type, target.type);
  const select = $('create-type');
  select.replaceChildren();

  if (options.length === 0) {
    const reversed = relationshipsBetween(ontology, target.type, source.type);
    $('create-fields').replaceChildren(
      note(
        `The ontology defines no relationship from ${article(label(source.type))} ` +
          `to ${article(label(target.type))}.` +
          (reversed.length ? ' Try swapping the direction.' : ''),
      ),
    );
    $('create-confirm').disabled = true;
    $('create-swap').hidden = reversed.length === 0;
    $('create-modal').hidden = false;
    return;
  }

  for (const option of options) {
    const element = document.createElement('option');
    element.value = option.name;
    element.textContent = option.label;
    select.append(element);
  }
  $('create-confirm').disabled = false;
  $('create-swap').hidden = false;
  renderFields();
  $('create-modal').hidden = false;
  select.focus();
}

function label(entityType) {
  return ontology.entity_types[entityType]?.label ?? entityType;
}

/** "an Address", "a Company" — good enough for the type names in the ontology. */
function article(text) {
  return `${/^[aeiou]/i.test(text) ? 'an' : 'a'} ${text}`;
}

function note(text) {
  const element = document.createElement('p');
  element.className = 'create-note';
  element.textContent = text;
  return element;
}

function renderFields() {
  const chosen = $('create-type').value;
  const container = $('create-fields');

  if (context.kind === 'entity') {
    const type = ontology.entity_types[chosen];
    form = buildPropertyForm(type?.properties ?? {});
    container.replaceChildren(form.element);
    return;
  }

  const spec =
    ontology.relationship_types[chosen] ?? ontology.system_relationship_types[chosen];
  const declared = { ...(spec?.properties ?? {}) };

  // Validity dates are supplied by the system rather than declared, so they
  // are added here for temporal relationships only.
  if (spec?.temporal) {
    declared.valid_from = {
      type: 'date',
      label: 'Valid from',
      description: 'When this began to hold. Leave blank if unknown.',
      required: false,
      multi: false,
    };
    declared.valid_to = {
      type: 'date',
      label: 'Valid to',
      description: 'When it stopped holding. Leave blank if it still does.',
      required: false,
      multi: false,
    };
  }

  form = buildPropertyForm(declared);
  container.replaceChildren(form.element);
}

async function submit() {
  const button = $('create-confirm');
  const missing = form?.missing() ?? [];
  if (missing.length) {
    callbacks.onError?.(new Error(`${missing.join(', ')} required`));
    return;
  }

  button.disabled = true;
  try {
    if (context.kind === 'entity') {
      const result = await api.createEntity($('create-type').value, form.read());
      const { position } = context;
      close();
      callbacks.onEntityCreated?.(result, position);
    } else {
      const values = form.read();
      const { valid_from: validFrom, valid_to: validTo, ...properties } = values;
      const edge = await api.createRelationship({
        type: $('create-type').value,
        source_id: context.source.id,
        target_id: context.target.id,
        properties,
        valid_from: validFrom ?? null,
        valid_to: validTo ?? null,
      });
      close();
      callbacks.onRelationshipCreated?.(edge);
    }
  } catch (error) {
    callbacks.onError?.(error);
  } finally {
    button.disabled = false;
  }
}

/** Swap which end is the source, and rebuild the offered relationship types. */
export function swapDirection() {
  if (context?.kind !== 'relationship') return;
  openNewRelationship(context.target, context.source);
}
