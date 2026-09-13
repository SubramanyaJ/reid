# Research delivery notes

- Main manuscript: `paper.org`; plain Org with ordinary LaTeX math and local PNG figure links. No PDF was generated.
- Reference limitation: the only uploaded material available was a crop showing VII / A / 1) Dataset. Those heading levels are reproduced. The remaining main headings are a conventional engineering-paper reconstruction, not a claim to match an unseen full paper. No response to the requested title/DOI/URL was available during drafting.
- Bibliography: 15 recent papers (final publication date, September 2021–September 2026 window) and 11 foundational/software sources. Section II is the literature survey. Its comparisons are qualitative except for the explicitly implemented exhaustive/LSH experiments.
- Local run: `paper-local-20260914`, created `2026-09-13T18:33:31.310903+00:00`. `metrics.json` SHA-256: `a1cb30f36010660cb256c949f8143a4f8dcc99587ce2b6cc549eaa493abca8a8`.
- CPU checked separately through Windows: AMD Ryzen 9 7940HS, 8 cores / 16 logical processors. The metrics archive records its processor identifier, platform and runtime versions.
- Measurements are synthetic-only. No real vehicle count, physical camera spacing, or real multi-camera recognition rate is claimed. The 0.82 visual threshold falsely accepts all 16 generated unknowns; no favorable result was substituted.
- Latest verification: 32 tests passed; two existing dependency deprecation warnings. The installed source checksums match the archived metric source map. Numerical tables were populated from that archive. Timing variations in earlier exploratory runs were not mixed into these paper tables.
- Local root: `E:/home/gitthings/reid`. Updated default configuration and local C1 YAML only; C1 camera index remains 1. No remote hosts or live cameras were accessed for this work. Port 9000 had no local listener when checked.
- Generated experiments remain under the repository's existing `experiments/results/` ignore rule. The delivery copy includes the paper, metrics, raw timing CSV, descriptors, figures, examples and research instructions. Keep the relative figure directory when moving `paper.org`.
