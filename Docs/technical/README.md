# Technical documentation

Two documents, one source. Both are written in Markdown here and rendered to
Word for circulation; the diagrams are generated from code, not drawn by hand.

| File | Audience |
| --- | --- |
| `01-technical-brief.md` → `2PP_Platform_Technical_Brief.docx` | anyone — how the system works, in plain language |
| `02-detailed-technical-documentation.md` → `2PP_Platform_Technical_Documentation.docx` | engineers — code paths, data model, interfaces |

## Rebuilding

```
./scripts/docs/build_docs.sh
```

Regenerates every figure from `scripts/docs/fig_*.py` and re-renders both
`.docx` files. Edit the Markdown (or the diagram scripts) and re-run — never
edit the `.docx` directly, it is a build artefact.
