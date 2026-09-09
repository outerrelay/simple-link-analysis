#!/usr/bin/env bash
# Refresh the vendored browser libraries from the npm registry.
#
# Edit the versions below, run this, then check the canvas still works before
# committing. Keep README.md in step with whatever you set here.

set -euo pipefail

CYTOSCAPE=3.30.2
DAGRE=0.8.5
CYTOSCAPE_DAGRE=2.5.0

VENDOR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch() {
  local package="$1" version="$2"
  local url="https://registry.npmjs.org/${package}/-/${package}-${version}.tgz"
  echo "  fetching ${package}@${version}"
  curl -sSfL "$url" -o "${WORK}/${package}.tgz"
  mkdir -p "${WORK}/${package}"
  tar xzf "${WORK}/${package}.tgz" -C "${WORK}/${package}"
}

install_file() {
  local source="$1" destination="$2"
  cp "$source" "${VENDOR}/${destination}"
  echo "  wrote ${destination}"
}

install_licence() {
  local package="$1" name="$2"
  for candidate in LICENSE LICENSE.txt LICENSE.md license; do
    if [ -f "${WORK}/${package}/package/${candidate}" ]; then
      cp "${WORK}/${package}/package/${candidate}" "${VENDOR}/${name}.LICENSE"
      return
    fi
  done
  echo "  WARNING: no licence file found in ${package}" >&2
}

fetch cytoscape "$CYTOSCAPE"
install_file "${WORK}/cytoscape/package/dist/cytoscape.min.js" cytoscape.min.js
install_licence cytoscape cytoscape

fetch dagre "$DAGRE"
install_file "${WORK}/dagre/package/dist/dagre.min.js" dagre.min.js
install_licence dagre dagre

fetch cytoscape-dagre "$CYTOSCAPE_DAGRE"
install_file "${WORK}/cytoscape-dagre/package/cytoscape-dagre.js" cytoscape-dagre.js
install_licence cytoscape-dagre cytoscape-dagre

echo "done — open the canvas and check the layouts before committing"
