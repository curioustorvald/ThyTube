# Vendored libraries

No CDN at runtime and no build step: everything the frontend imports lives here.

| File | Library | Version | Source | SHA-256 |
|---|---|---|---|---|
| `preact-htm.js` | htm + Preact (incl. hooks), standalone ES module | htm 3.1.1 (bundles Preact 10.x) | https://unpkg.com/htm@3.1.1/preact/standalone.module.js | `72284e8e9079c87817145df1110f74e8a2aa040b2fc384922e18dfcb46fc1fd7` |

Exports: `h, html, render, Component, createContext, useState, useReducer, useEffect, useLayoutEffect,
useRef, useImperativeHandle, useMemo, useCallback, useContext, useDebugValue, useErrorBoundary`.

Licences: htm and Preact are MIT-licensed.
