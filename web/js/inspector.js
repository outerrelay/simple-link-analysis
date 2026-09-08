/** The detail panel for a selected node or edge. */

const panel = () => document.getElementById('inspector');
const body = () => document.getElementById('inspector-body');

export function hideInspector() {
  panel().hidden = true;
}

export function showEntity(node, ontology) {
  const data = node.data();
  const type = ontology.entity_types[data.type];
  render(
    data.label,
    type ? type.label : data.type,
    data.properties ?? {},
    type ? type.properties : {},
    data.id,
  );
}

export function showRelationship(edge, ontology) {
  const data = edge.data();
  const spec =
    ontology.relationship_types[data.type] || ontology.system_relationship_types[data.type];

  const properties = { ...(data.properties ?? {}) };
  if (data.valid_from) properties.valid_from = data.valid_from;
  if (data.valid_to) properties.valid_to = data.valid_to;
  properties.supported_by =
    `${data.assertion_count} assertion${data.assertion_count === 1 ? '' : 's'}`;

  render(
    spec ? spec.label : data.type,
    data.type,
    properties,
    spec ? spec.properties : {},
    data.id,
  );
}

function render(title, subtitle, properties, declared, id) {
  const element = body();
  element.replaceChildren();

  const heading = document.createElement('h2');
  heading.textContent = title;
  const sub = document.createElement('div');
  sub.className = 'subtitle';
  sub.textContent = subtitle;
  element.append(heading, sub);

  const entries = Object.entries(properties).filter(
    ([, value]) => value !== null && value !== undefined && value !== '' &&
      !(Array.isArray(value) && value.length === 0),
  );

  if (entries.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'No properties recorded.';
    element.append(empty);
  } else {
    const list = document.createElement('dl');
    list.className = 'properties';
    for (const [key, value] of entries) {
      const term = document.createElement('dt');
      const spec = declared?.[key];
      term.textContent = spec ? spec.label : key.replaceAll('_', ' ');
      if (spec?.pii) term.classList.add('pii');
      const definition = document.createElement('dd');
      definition.textContent = Array.isArray(value) ? value.join(', ') : String(value);
      list.append(term, definition);
    }
    element.append(list);
  }

  const identity = document.createElement('p');
  identity.className = 'id';
  identity.textContent = id;
  element.append(identity);

  panel().hidden = false;
}
