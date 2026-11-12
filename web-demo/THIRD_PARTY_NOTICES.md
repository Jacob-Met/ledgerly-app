# Browser runtime notices

Ledgerly source is MIT-licensed; see the repository or package LICENSE.

The browser bundle uses the unmodified Pyodide npm runtime pinned in
package-lock.json (package version 0.29.3), under the Mozilla Public License 2.0.
The complete license is included in licenses/PYODIDE-MPL-2.0.txt.
Runtime source: https://github.com/pyodide/pyodide/tree/0.29.3

The bundled runtime's own pyodide-lock.json identifies CPython 3.13.2.
Its license is included in licenses/PYTHON-LICENSE.txt.
CPython source: https://github.com/python/cpython/tree/v3.13.2

The runtime assets and the Python standard library are distributed unchanged.
The npm version and embedded runtime metadata are recorded separately rather
than treating the runtime lock's development-version field as the npm version.
The release MANIFEST.json records each packaged file's SHA256.
