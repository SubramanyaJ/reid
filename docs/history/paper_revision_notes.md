# Parking-lot paper revision: evidence and reporting

The revised `main.tex` uses the real parking-lot run as its visual evaluation. Generated-image experiments, their tables, their illustration, and their accuracy claims have been removed. Synthetic vectors remain only in the query-speed experiment (one LSH table). The descriptor and parameter tables have been replaced by prose, leaving three tables overall. Twelve displayed equations remain, with explicit definitions of their terms.

## Review-score reconciliation

Source run: `a9721724-b609-424f-90ac-81a686eb6bb7`, in `experiments/results/video-review`.

Three records agree on the labels: the 13 individual rows in `match_reviews.csv`, the integer counts in `metrics_live.json`, and the persisted `match_reviews` SQLite table. They contain 9 approved MATCH pairs and 4 rejected MATCH pairs.

Consequently, accepted-match precision is **9 / 13 = 0.6923076923 = 69.23%**. Agreement on this all-positive review set is the same value. The JSON's stored accuracy and precision fields are `0.7923076923`, which do not agree with those counts. Its recall (`0.84`) and F1 (`0.8154571159`) also do not follow from the stored confusion counts. The paper uses the individually recorded judgments, not those inconsistent derived fields. The source JSON, CSV, and database were not edited.

Only MATCH predictions were reviewed. Therefore, full retrieval recall, F1, true-negative counts, missed-association counts, rank-1, mAP, and mask/box accuracy cannot be established by this run. The reported zeros for FN/TN are properties of the selected review set, not evidence that the system made no missed matches. The manuscript reports accepted-match precision and complete review coverage, with the number of judgments visible beside the result.

## Recording and timing

- 9,484 decoded frames at 30 FPS: 316.1333 seconds, including 120 warmup frames and 9,364 evaluated frames.
- 214 decisions: 13 MATCH, 179 UNCERTAIN, 16 NO_MATCH, 6 SKIP_QUALITY.
- 5,160 candidate-box instances, 3,946 confirmed-track frame instances, zero frame-processing errors. These are repeated frame observations, not counts of distinct labeled vehicles.
- 130.7174 seconds of replay wall time; 72.5535 wall FPS; 11.3249 ms mean frame-processing time and 88.3008 processing FPS. Source-duration/wall-time ratio is approximately 2.42.
- The 13 MATCH events span nine local track IDs; repeated confirmations are included. They are not thirteen independent cross-camera transitions.
- All 214 archived plate states are UNKNOWN. Local descriptor verification is available for six observation decisions. The paper describes the actual visual/context acceptance policy instead of attributing all decisions to standalone cosine or LSH.

## Hardware attribution

The processor brand/model previously named for the local measurement host has been removed from the manuscript. The user-reported Core i5-2520M Latitude node is described as part of the LAN deployment. The existing archived timings were not rerun on that node and have not been relabeled as its performance. The exact Latitude model designation was not independently verified, so the manuscript uses the family name.

## Competing approaches and sources

The three-method table compares TransReID, GiT, and SSBVER with the proposed system on representation construction and deployment requirements. No same-recording measurements exist for those methods. They were therefore not ranked as inferior or assigned invented accuracy/speed results. The measured numerical comparator remains exhaustive cosine search on identical vectors.

Primary sources checked for this revision:

- [TransReID, ICCV 2021](https://openaccess.thecvf.com/content/ICCV2021/html/He_TransReID_Transformer-Based_Object_Re-Identification_ICCV_2021_paper.html)
- [GiT, author manuscript](https://arxiv.org/abs/2107.05475), published in IEEE TIP 2023.
- [SSBVER, CVPR Workshops 2023](https://openaccess.thecvf.com/content/CVPR2023W/AICity/html/Khorramshahi_Robust_and_Scalable_Vehicle_Re-Identification_via_Self-Supervision_CVPRW_2023_paper.html)
- [DB-LSH, author manuscript](https://arxiv.org/abs/2207.07823)
- [DET-LSH, PVLDB 2024](https://www.vldb.org/pvldb/vol17/p2241-wei.pdf)

The LSH table is checked against `experiments/results/paper-local-20260914/metrics.json`. Its four gallery sizes and all three seeds are retained, including the small-gallery cases where exhaustive search is faster. At 10,000 vectors, the average seed-level latency ratio is 4.76577, candidate fraction 0.0262687, and exact-top-1 retention 0.983333. No image recognition claim is derived from these generated vectors.

## Figure provenance and validation

Four JPEG files in `paper_figures/parking-*.jpg` are byte-for-byte exports of the persistent `review_images` table, without image modification. They show the first approved and first rejected review entries (frames 646 and 1442), with their exact stored candidate images. This selection rule includes both outcomes rather than only successful examples.

The manuscript compiles to eight pages with resolved citations/references and no overfull boxes. All pages were rendered and visually checked for two-column layout and equation overlap. No application tests or new experiments were run for this document revision.
