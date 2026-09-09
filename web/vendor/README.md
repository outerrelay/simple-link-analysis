# Vendored browser libraries

These files are copied into the repository rather than loaded from a CDN, so
the tool works with no internet connection and opening a chart does not tell a
third party which investigation tool is running and when. See decision 15 in
[docs/decisions.md](../../docs/decisions.md).

The cost is that updates are manual — there is no package manifest and so no
automated advisories. This file is the manifest: check these versions against
upstream releases periodically.

| File | Package | Version | Licence |
|---|---|---|---|
| `cytoscape.min.js` | [cytoscape](https://github.com/cytoscape/cytoscape.js) | 3.30.2 | MIT |
| `dagre.min.js` | [dagre](https://github.com/dagrejs/dagre) | 0.8.5 | MIT |
| `cytoscape-dagre.js` | [cytoscape-dagre](https://github.com/cytoscape/cytoscape.js-dagre) | 2.5.0 | MIT |

Licence texts sit beside each file as `<name>.LICENSE`.

## Updating

`update.sh` fetches the versions named above from the npm registry and
replaces the files in place. Edit the versions in that script, run it, then
open the canvas and confirm the layouts still work before committing.

```bash
./web/vendor/update.sh
```
