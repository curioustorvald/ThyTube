# Vendored libraries

No CDN at runtime and no build step: everything the frontend imports lives here.

| File | Library | Version | Source | SHA-256 |
|---|---|---|---|---|
| `preact-htm.js` | htm + Preact (incl. hooks), standalone ES module | htm 3.1.1 (bundles Preact 10.x) | https://unpkg.com/htm@3.1.1/preact/standalone.module.js | `72284e8e9079c87817145df1110f74e8a2aa040b2fc384922e18dfcb46fc1fd7` |

Exports: `h, html, render, Component, createContext, useState, useReducer, useEffect, useLayoutEffect,
useRef, useImperativeHandle, useMemo, useCallback, useContext, useDebugValue, useErrorBoundary`.

## Fonts (`fonts/`)

| File | Font | Version | Source | SHA-256 |
|---|---|---|---|---|
| `fonts/NotoSansCJKkr-2.004-Hangul-Regular.woff2` | Noto Sans CJK KR Regular, Hangul subset | 2.004 | https://github.com/notofonts/noto-cjk (`Sans/OTF/Korean/NotoSansCJKkr-Regular.otf`, SHA-256 `6bcb2a0703aa137e874fc2dffa85f6c21ba9a67fa329e81b8c801663af7e992a`) | `e80ab8ec692ffccd7d7461d208136a2aabbcc2ac128185716391fa7710b33761` |
| `fonts/NotoSansCJKkr-2.004-Hangul-Bold.woff2` | Noto Sans CJK KR Bold, Hangul subset | 2.004 | same, `NotoSansCJKkr-Bold.otf` (SHA-256 `26d0c6748500a0444844280b308f5b62c7ae92ac6c6ac88148e502dd211eb52a`) | `371ac83db6a2d5a147d2941f7be7ed13cf10404578914479c93e689d65740b4a` |

Used only by the Korean UI (`:root:lang(ko)` in `css/app.css`). The subset keeps all modern syllables, the
compatibility and conjoining jamo (incl. Old Hangul: ㆍ, ㅺ, ㅼ…) and the `ljmo`/`vjmo`/`tjmo`/`ccmp` features that
compose Old Hangul syllables. Made with fontTools 4.62:

```sh
pyftsubset NotoSansCJKkr-Regular.otf --flavor=woff2 --desubroutinize \
  --unicodes="U+1100-11FF,U+302E-302F,U+3131-318E,U+A960-A97C,U+AC00-D7A3,U+D7B0-D7FB" \
  --layout-features+=ljmo,vjmo,tjmo --output-file=NotoSansCJKkr-2.004-Hangul-Regular.woff2
```

If the unicode range changes, change it in `css/app.css` too. The version is in the file name so Apache can serve
the fonts as immutable.

Licences: htm and Preact are MIT-licensed. Noto Sans CJK is under the SIL Open Font License 1.1 (`fonts/OFL.txt`).
