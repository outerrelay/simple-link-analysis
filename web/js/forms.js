/** Property forms built from the ontology.
 *
 * Nothing here knows what a Company is. Each field's control comes from the
 * declared property type, so adding a property to ontology.yaml puts it on the
 * form at the next reload — the same rule the canvas styling follows.
 */

/** Controls per ontology property type. Anything unlisted gets a text box. */
const INPUT_TYPES = {
  integer: 'number',
  number: 'number',
  date: 'date',
  datetime: 'datetime-local',
  url: 'url',
  email: 'email',
  phone: 'tel',
};

/**
 * Build a form for a set of declared properties.
 *
 * Required fields come first: they are what the write will be rejected for.
 *
 * @param declared  the `properties` map from the ontology payload
 * @param values    existing values, when editing rather than creating
 * @returns         {element, read} — the DOM node, and a function returning
 *                  the entered values keyed by property name
 */
export function buildPropertyForm(declared, values = {}) {
  const element = document.createElement('div');
  element.className = 'property-form';

  const entries = Object.entries(declared).sort(
    ([, a], [, b]) => Number(b.required) - Number(a.required),
  );

  const readers = new Map();
  for (const [name, spec] of entries) {
    const { row, read } = buildField(name, spec, values[name]);
    element.append(row);
    readers.set(name, read);
  }

  return {
    element,
    read() {
      const result = {};
      for (const [name, read] of readers) {
        const value = read();
        if (value !== null && value !== undefined && value !== '') result[name] = value;
      }
      return result;
    },
    /** Names of required properties still empty, for a useful error. */
    missing() {
      return entries
        .filter(([name, spec]) => spec.required && !readers.get(name)())
        .map(([name]) => name);
    },
  };
}

function buildField(name, spec, value) {
  const row = document.createElement('label');
  row.className = 'field';

  const label = document.createElement('span');
  label.className = 'field-label';
  label.textContent = spec.label || name.replaceAll('_', ' ');
  if (spec.required) {
    const mark = document.createElement('span');
    mark.className = 'required';
    mark.textContent = ' required';
    label.append(mark);
  }
  if (spec.pii) {
    const mark = document.createElement('span');
    mark.className = 'pii-mark';
    mark.textContent = ' personal';
    label.append(mark);
  }
  row.append(label);

  const { control, read } = buildControl(name, spec, value);
  row.append(control);

  if (spec.description) {
    const hint = document.createElement('span');
    hint.className = 'field-hint';
    hint.textContent = spec.description;
    row.append(hint);
  }
  return { row, read };
}

function buildControl(name, spec, value) {
  if (spec.type === 'enum' && spec.enum) {
    const select = document.createElement('select');
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = spec.required ? 'Choose…' : '(not set)';
    select.append(blank);
    for (const option of spec.enum) {
      const element = document.createElement('option');
      element.value = option;
      element.textContent = option.replaceAll('_', ' ');
      select.append(element);
    }
    if (value) select.value = value;
    return { control: select, read: () => select.value || null };
  }

  if (spec.type === 'boolean') {
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = Boolean(value);
    return { control: input, read: () => input.checked };
  }

  if (spec.multi) {
    // One per line reads better than commas for names and addresses, which
    // frequently contain commas themselves.
    const area = document.createElement('textarea');
    area.rows = 2;
    area.placeholder = 'One per line';
    area.value = Array.isArray(value) ? value.join('\n') : (value ?? '');
    return {
      control: area,
      read: () => {
        const lines = area.value
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean);
        return lines.length ? lines : null;
      },
    };
  }

  if (spec.type === 'text') {
    const area = document.createElement('textarea');
    area.rows = 3;
    area.value = value ?? '';
    return { control: area, read: () => area.value.trim() || null };
  }

  const input = document.createElement('input');
  input.type = INPUT_TYPES[spec.type] ?? 'text';
  if (spec.type === 'number') input.step = 'any';
  if (spec.type === 'country') {
    input.placeholder = 'Two-letter code, e.g. NO';
    input.maxLength = 2;
  }
  if (spec.type === 'currency') input.placeholder = 'e.g. NOK';
  input.value = value ?? '';

  return {
    control: input,
    read: () => {
      const raw = input.value.trim();
      if (!raw) return null;
      if (spec.type === 'integer') return Number.parseInt(raw, 10);
      if (spec.type === 'number') return Number.parseFloat(raw);
      if (spec.type === 'country') return raw.toUpperCase();
      return raw;
    },
  };
}

/**
 * Relationship types that may join these two entity types, in this direction.
 *
 * The ontology payload already gives concrete source and target types per
 * relationship, so this needs no round trip — and the menu can only ever offer
 * a connection the ontology permits.
 */
export function relationshipsBetween(ontology, sourceType, targetType) {
  // System relationships are excluded. SAME_AS is produced by the duplicate
  // check and resolved by merging; MENTIONS is provenance attached by whatever
  // read the source. Offering them here would be a second, worse way to do
  // things that already have a workflow.
  return Object.entries(ontology.relationship_types)
    .filter(
      ([, spec]) =>
        spec.source_types.includes(sourceType) && spec.target_types.includes(targetType),
    )
    .map(([name, spec]) => ({ name, ...spec }))
    .sort((a, b) => a.label.localeCompare(b.label));
}
