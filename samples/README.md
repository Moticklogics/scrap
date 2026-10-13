# Sample output

Produced by `python -m forum_extractor run ...` against the local fixture forums in `tests/fixtures`
(host name replaced by `forum.example`).

* `xenforo2_from_page2_1_page_per_file/`: XenForo 2 listing, started at page 2 with `--pages-per-file 1`
  to show rotation (one file per listing page).
* `custom_layout_generic/`: a custom forum with no known platform markup, handled by generic DOM
  analysis. Includes the processing log. The sticky thread is written once and logged as a duplicate on
  later pages. A deleted thread is logged as a failure and is not written.
