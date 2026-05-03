"""Run the full context-restoration pipeline end-to-end.

  $ python -m scripts.context.run_all

Each stage is idempotent (drops + recreates its tables). Re-runnable any
time the underlying rfp_requirements / document_chunks change.
"""
from __future__ import annotations

import sys

from . import (
    s01_hard_dedup,
    s02_section_hierarchy,
    s03_extract_tables,
    s04_extract_glossary,
    s05_resolve_references,
    s06_reverse_and_glossary_links,
    s07_volume_rollup,
    s09_subpart_rollup,
    s10_attachment_map,
    s11_glossary_from_tables,
    s12_form_factor_tags,
    s13_requirement_kinds,
    s08_build_bundle,
)


STAGES = [
    s01_hard_dedup,
    s02_section_hierarchy,
    s03_extract_tables,
    s04_extract_glossary,
    s05_resolve_references,
    s06_reverse_and_glossary_links,
    s07_volume_rollup,
    s09_subpart_rollup,
    s10_attachment_map,
    s11_glossary_from_tables,
    s12_form_factor_tags,
    s13_requirement_kinds,   # must run before s08 so bundle gets kind
    s08_build_bundle,
]


def main() -> int:
    for stage in STAGES:
        rc = stage.main()
        if rc != 0:
            print(f"!! stage {stage.__name__} returned {rc} — aborting")
            return rc
    print()
    print("All stages complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
