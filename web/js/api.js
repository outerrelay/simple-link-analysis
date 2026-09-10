/** Thin wrapper over the HTTP API. Every call returns parsed JSON or throws. */

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* a non-JSON error body is not worth reporting over the status text */
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

const query = (params) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, v));
    else search.append(key, value);
  }
  return search.toString();
};

export const api = {
  ontology: () => request('/api/ontology'),

  search: (q, entityType) =>
    request(`/api/graph/search?${query({ q, entity_type: entityType })}`),

  expand: (entityIds, options = {}) =>
    request(
      `/api/graph/expand?${query({
        entity_ids: entityIds,
        depth: options.depth ?? 1,
        relationship_types: options.relationshipTypes,
        include_sources: options.includeSources ?? false,
        as_of: options.asOf,
      })}`,
    ),

  degree: (entityId) => request(`/api/graph/degree/${entityId}`),

  listCharts: () => request('/api/charts'),
  createChart: (name, description = '') =>
    request('/api/charts', { method: 'POST', body: JSON.stringify({ name, description }) }),
  getChart: (chartId) => request(`/api/charts/${chartId}`),

  addNodes: (chartId, placements) =>
    request(`/api/charts/${chartId}/nodes`, {
      method: 'POST',
      body: JSON.stringify(placements),
    }),

  savePositions: (chartId, placements) =>
    request(`/api/charts/${chartId}/positions`, {
      method: 'PUT',
      body: JSON.stringify(placements),
    }),

  actionsFor: (entityType) => request(`/api/actions/for/${entityType}`),

  runAction: (actionId, body) =>
    request(`/api/actions/${actionId}/run`, { method: 'POST', body: JSON.stringify(body) }),

  job: (jobId) => request(`/api/actions/jobs/${jobId}`),

  proposal: (setId) => request(`/api/actions/proposals/${setId}`),

  pendingProposals: () => request('/api/actions/proposals'),

  decideProposal: (setId, accepted, rejected) =>
    request(`/api/actions/proposals/${setId}/decide`, {
      method: 'POST',
      body: JSON.stringify({ accepted, rejected }),
    }),

  rejectProposal: (setId) =>
    request(`/api/actions/proposals/${setId}/reject`, { method: 'POST' }),

  deleteFromGraph: (entityId, { suppress = true } = {}) =>
    request(`/api/actions/entity/${entityId}?suppress=${suppress}`, { method: 'DELETE' }),

  createEntity: (type, properties) =>
    request('/api/graph/entity', {
      method: 'POST',
      body: JSON.stringify({ type, properties }),
    }),

  createRelationship: (body) =>
    request('/api/graph/relationship', { method: 'POST', body: JSON.stringify(body) }),

  matchesFor: (entityId) => request(`/api/merges/matches/${entityId}`),

  mergePreview: (survivorId, absorbedId) =>
    request(`/api/merges/preview?${query({ survivor_id: survivorId, absorbed_id: absorbedId })}`),

  merge: (survivorId, absorbedId, resolutions = {}) =>
    request('/api/merges', {
      method: 'POST',
      body: JSON.stringify({
        survivor_id: survivorId,
        absorbed_id: absorbedId,
        resolutions,
      }),
    }),

  undoMerge: (mergeId) => request(`/api/merges/${mergeId}/undo`, { method: 'POST' }),

  removeNodes: (chartId, entityIds) =>
    request(`/api/charts/${chartId}/nodes/remove`, {
      method: 'POST',
      body: JSON.stringify({ entity_ids: entityIds }),
    }),
};
