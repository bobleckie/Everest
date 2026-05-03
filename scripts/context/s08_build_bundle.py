"""Stage 8 — Denormalized context bundle.

For each surviving requirement we precompute the full UI payload as a JSON
blob plus a few indexable columns. The UI calls a single endpoint, gets
everything it needs, and renders without round-trips.

Schema:
  requirement_context_bundle(
    requirement_id INTEGER PK,
    theme_id, theme_label,
    primary_section_id, breadcrumb,         -- "3 > 3.2 > 3.2.1" (optional)
    source_doc_id, source_doc_name,
    source_chunk_id,                        -- the linked verbatim paragraph
    bundle_json TEXT NOT NULL,              -- full payload
    n_refs, n_resolved_refs, n_glossary, n_reverse_refs, n_tables,
    last_built_at DATETIME DEFAULT CURRENT_TIMESTAMP
  )

Bundle JSON contains:
  {
    "requirement": {
      "id", "code", "title", "description", "source_text", "category",
      "priority", "requirement_class", "compliance_status", "verified",
      "section_id", "source_page", "extraction_pass", "reviewer_confidence"
    },
    "theme": {"id","label","slug"},
    "breadcrumb": [{"section_code","title","depth"}, ...],
    "source": {
      "document_id","document_name",
      "chunk": {"id","page_number","content","chunk_index"},
      "context_before": [{...}],
      "context_after": [{...}],
      "match_quality"
    },
    "references": [
      {"kind","label","normalized","target_kind",
       "target": {"document_id","document_name","section_code","title","excerpt","table_id"}}
    ],
    "reverse_refs": [{"requirement_id","title","section_id"}],
    "glossary_terms": [{"term","definition","source_doc_id"}],
    "tables_in_section": [
      {"id","title","n_rows","n_cols","header","preview_rows":[[...]]}
    ]
  }
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Any, Dict, List

from .common import banner, connect, stat


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 8 — Build context bundles")

    cur.executescript(
        """
        DROP TABLE IF EXISTS requirement_context_bundle;
        CREATE TABLE requirement_context_bundle (
            requirement_id      INTEGER PRIMARY KEY REFERENCES rfp_requirements(id),
            theme_id            INTEGER REFERENCES requirement_themes(id),
            theme_label         TEXT,
            primary_section_id  INTEGER REFERENCES section_hierarchy(id),
            breadcrumb          TEXT,
            source_doc_id       INTEGER,
            source_doc_name     TEXT,
            source_chunk_id     INTEGER,
            bundle_json         TEXT NOT NULL,
            n_refs              INTEGER NOT NULL DEFAULT 0,
            n_resolved_refs     INTEGER NOT NULL DEFAULT 0,
            n_glossary          INTEGER NOT NULL DEFAULT 0,
            n_reverse_refs      INTEGER NOT NULL DEFAULT 0,
            n_tables            INTEGER NOT NULL DEFAULT 0,
            last_built_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX idx_rcb_theme  ON requirement_context_bundle(theme_id);
        CREATE INDEX idx_rcb_doc    ON requirement_context_bundle(source_doc_id);
        CREATE INDEX idx_rcb_section ON requirement_context_bundle(primary_section_id);
        """
    )

    # --- Pre-load lookup indexes ---

    # section_hierarchy by id
    sec_by_id = {}
    sec_by_doc_code = {}
    for sid, did, code, parent_code, title, depth, ord_, fc, lc in cur.execute(
        "SELECT id, document_id, section_code, parent_section_code, title, depth, ord, first_chunk_id, last_chunk_id FROM section_hierarchy"
    ):
        sec_by_id[sid] = {
            "id": sid, "document_id": did, "section_code": code,
            "parent_section_code": parent_code, "title": title, "depth": depth,
            "ord": ord_, "first_chunk_id": fc, "last_chunk_id": lc,
        }
        sec_by_doc_code[(did, code.lower())] = sid

    # documents
    docs = {}
    for did, fname, orig in cur.execute(
        "SELECT id, filename, original_filename FROM ingested_documents"
    ):
        docs[did] = orig or fname

    # source_links: requirement_id -> (chunk_id, doc_id, match_quality)
    src_link = {}
    for rid, cid, did, q in cur.execute(
        "SELECT requirement_id, chunk_id, chunk_doc_id, match_quality FROM requirement_source_links"
    ):
        src_link[rid] = (cid, did, q)

    # chunk lookup
    def fetch_chunk(cid: int):
        row = cur.execute(
            "SELECT id, document_id, chunk_index, page_number, content FROM document_chunks WHERE id=?",
            (cid,),
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "document_id": row[1], "chunk_index": row[2],
                "page_number": row[3], "content": row[4]}

    def fetch_neighbors(doc_id: int, chunk_index: int, n: int = 1):
        before = cur.execute(
            "SELECT id, chunk_index, page_number, content FROM document_chunks WHERE document_id=? AND chunk_index<? ORDER BY chunk_index DESC LIMIT ?",
            (doc_id, chunk_index, n),
        ).fetchall()
        after = cur.execute(
            "SELECT id, chunk_index, page_number, content FROM document_chunks WHERE document_id=? AND chunk_index>? ORDER BY chunk_index ASC LIMIT ?",
            (doc_id, chunk_index, n),
        ).fetchall()
        fmt = lambda r: {"id": r[0], "chunk_index": r[1], "page_number": r[2], "content": r[3]}
        return [fmt(r) for r in reversed(before)], [fmt(r) for r in after]

    # references by requirement
    refs_by_req: Dict[int, List[dict]] = defaultdict(list)
    for r in cur.execute(
        """SELECT requirement_id, ref_kind, ref_label, ref_normalized,
                  target_kind, target_requirement_id, target_section_id,
                  target_document_id, target_table_id, target_excerpt, confidence
           FROM requirement_references"""
    ):
        rid = r[0]
        target = {}
        if r[5]:  # target_requirement_id
            target["requirement_id"] = r[5]
        if r[6]:  # target_section_id
            sec = sec_by_id.get(r[6], {})
            target["section_code"] = sec.get("section_code")
            target["section_title"] = sec.get("title")
            target["section_depth"] = sec.get("depth")
            target["document_id"] = sec.get("document_id")
            target["document_name"] = docs.get(sec.get("document_id"))
        if r[7]:  # target_document_id (overrides if set)
            target["document_id"] = r[7]
            target["document_name"] = docs.get(r[7])
        if r[8]:
            target["table_id"] = r[8]
        if r[9]:
            target["excerpt"] = r[9]
        refs_by_req[rid].append({
            "kind": r[1], "label": r[2], "normalized": r[3],
            "target_kind": r[4], "confidence": r[10], "target": target,
        })

    # reverse refs
    rev_refs_by_req: Dict[int, List[dict]] = defaultdict(list)
    for rid, referrer, via_sec_id in cur.execute(
        """SELECT requirement_id, referrer_requirement_id, via_section_id
           FROM requirement_reverse_refs"""
    ):
        ref_row = cur.execute(
            "SELECT title, section_id FROM rfp_requirements WHERE id=?",
            (referrer,),
        ).fetchone()
        if ref_row:
            rev_refs_by_req[rid].append({
                "requirement_id": referrer,
                "title": ref_row[0], "section_id": ref_row[1],
            })

    # glossary
    gloss_by_req: Dict[int, List[dict]] = defaultdict(list)
    for rid, term, defn, doc_id, occ in cur.execute(
        """SELECT rgl.requirement_id, gt.term, gt.definition, gt.document_id, rgl.occurrences
           FROM requirement_glossary_links rgl JOIN glossary_terms gt ON gt.id = rgl.glossary_term_id"""
    ):
        gloss_by_req[rid].append({
            "term": term, "definition": defn,
            "source_doc_id": doc_id, "occurrences": occ,
        })

    # themes
    theme_meta = {}
    for tid, slug, label in cur.execute(
        "SELECT id, slug, label FROM requirement_themes"
    ):
        theme_meta[tid] = {"id": tid, "slug": slug, "label": label}

    theme_assign = {}
    for rid, tid in cur.execute(
        "SELECT requirement_id, theme_id FROM requirement_theme_assignments"
    ):
        theme_assign[rid] = tid

    # Rollup children by parent
    children_by_parent: Dict[int, List[int]] = defaultdict(list)
    for parent_rid, child_rid in cur.execute(
        "SELECT parent_requirement_id, child_requirement_id FROM requirement_groups"
    ):
        children_by_parent[parent_rid].append(child_rid)
    parent_of_child: Dict[int, int] = {}
    for parent_rid, child_rid in cur.execute(
        "SELECT parent_requirement_id, child_requirement_id FROM requirement_groups"
    ):
        parent_of_child[child_rid] = parent_rid

    # Tables by section_code
    tables_by_section: Dict[tuple, List[dict]] = defaultdict(list)
    for tid, did, sec, title, header, n_rows, n_cols in cur.execute(
        """SELECT id, document_id, section_code, title, header_row_json, n_rows, n_cols
           FROM extracted_tables"""
    ):
        rows_preview = []
        for row in cur.execute(
            "SELECT cells_json FROM extracted_table_rows WHERE table_id=? ORDER BY row_index LIMIT 5",
            (tid,),
        ):
            try:
                rows_preview.append(json.loads(row[0]))
            except Exception:
                pass
        tables_by_section[(did, (sec or "").lower())].append({
            "id": tid, "title": title, "n_rows": n_rows, "n_cols": n_cols,
            "header": json.loads(header) if header else None,
            "preview_rows": rows_preview,
        })

    # --- Walk requirements ---
    rows = cur.execute(
        """SELECT r.id, r.requirement_id, r.title, r.description, r.source_text,
                  r.category, r.priority, r.requirement_class, r.compliance_status,
                  r.verified, r.section_id, r.source_page, r.extraction_pass,
                  r.reviewer_confidence, r.document_id, r.primary_section_id
           FROM rfp_requirements r
           WHERE r.superseded_by_requirement_id IS NULL"""
    ).fetchall()
    stat("Surviving requirements to bundle", len(rows))

    inserts = []
    for r in rows:
        (rid, code, title, desc, src, cat, prio, klass, comp_status,
         verified, scode, page, extr_pass, conf, doc_id, primary_sec_id) = r

        # breadcrumb
        breadcrumb: List[dict] = []
        sec = sec_by_id.get(primary_sec_id) if primary_sec_id else None
        cur_sec = sec
        while cur_sec:
            breadcrumb.append({
                "section_code": cur_sec["section_code"],
                "title": cur_sec["title"],
                "depth": cur_sec["depth"],
            })
            parent_code = cur_sec.get("parent_section_code")
            if not parent_code:
                break
            parent_sid = sec_by_doc_code.get((cur_sec["document_id"], parent_code.lower()))
            if not parent_sid or parent_sid in {b.get("_sid") for b in breadcrumb}:
                break
            cur_sec = sec_by_id.get(parent_sid)
        breadcrumb.reverse()

        # source linkage
        source = {}
        link = src_link.get(rid)
        chunk_id = None
        if link:
            cid, did, q = link
            chunk = fetch_chunk(cid) if cid else None
            chunk_id = cid
            before, after = ([], [])
            if chunk:
                before, after = fetch_neighbors(chunk["document_id"], chunk["chunk_index"], 1)
            source = {
                "document_id": did,
                "document_name": docs.get(did),
                "match_quality": q,
                "chunk": chunk,
                "context_before": before,
                "context_after": after,
            }

        refs = refs_by_req.get(rid, [])
        rev = rev_refs_by_req.get(rid, [])
        gloss = gloss_by_req.get(rid, [])
        tables = tables_by_section.get((doc_id, (scode or "").lower()), [])

        theme_id = theme_assign.get(rid)
        theme = theme_meta.get(theme_id, {})

        # Sub-parts: if this row is a parent, embed its children's titles +
        # source_text so the detail view can render them as nested items.
        sub_parts: List[dict] = []
        for child_rid in children_by_parent.get(rid, []):
            child_row = cur.execute(
                """SELECT id, requirement_id, title, description, source_text,
                          priority, category, compliance_status
                   FROM rfp_requirements WHERE id=?""",
                (child_rid,),
            ).fetchone()
            if not child_row:
                continue
            sub_parts.append({
                "id": child_row[0], "code": child_row[1], "title": child_row[2],
                "description": child_row[3], "source_text": child_row[4],
                "priority": child_row[5], "category": child_row[6],
                "compliance_status": child_row[7],
            })
        rollup_parent_id = parent_of_child.get(rid)

        # Pull row-kind classification from rfp_requirements for the bundle
        kind_row = cur.execute(
            "SELECT requirement_kind FROM rfp_requirements WHERE id=?", (rid,)
        ).fetchone()
        req_kind = (kind_row[0] if kind_row else None) or "obligation"

        bundle = {
            "requirement": {
                "id": rid, "code": code, "title": title,
                "description": desc, "source_text": src,
                "category": cat, "priority": prio,
                "requirement_class": klass,
                "requirement_kind": req_kind,
                "compliance_status": comp_status, "verified": verified,
                "section_id": scode, "source_page": page,
                "extraction_pass": extr_pass,
                "reviewer_confidence": conf,
                "rollup_parent_id": rollup_parent_id,
                "n_sub_parts": len(sub_parts),
            },
            "sub_parts": sub_parts,
            "theme": theme,
            "breadcrumb": breadcrumb,
            "source": source,
            "references": refs,
            "reverse_refs": rev,
            "glossary_terms": gloss,
            "tables_in_section": tables,
        }

        inserts.append((
            rid, theme_id, theme.get("label"), primary_sec_id,
            " > ".join(b["section_code"] for b in breadcrumb) if breadcrumb else None,
            doc_id, docs.get(doc_id), chunk_id,
            json.dumps(bundle, ensure_ascii=False),
            len(refs), sum(1 for x in refs if x["target_kind"] != "unresolved"),
            len(gloss), len(rev), len(tables),
        ))

    cur.executemany(
        """INSERT INTO requirement_context_bundle
           (requirement_id, theme_id, theme_label, primary_section_id, breadcrumb,
            source_doc_id, source_doc_name, source_chunk_id, bundle_json,
            n_refs, n_resolved_refs, n_glossary, n_reverse_refs, n_tables)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        inserts,
    )
    con.commit()

    print()
    n_bundles = cur.execute("SELECT COUNT(*) FROM requirement_context_bundle").fetchone()[0]
    n_with_chunk = cur.execute("SELECT COUNT(*) FROM requirement_context_bundle WHERE source_chunk_id IS NOT NULL").fetchone()[0]
    n_with_breadcrumb = cur.execute("SELECT COUNT(*) FROM requirement_context_bundle WHERE breadcrumb IS NOT NULL").fetchone()[0]
    n_with_refs = cur.execute("SELECT COUNT(*) FROM requirement_context_bundle WHERE n_refs > 0").fetchone()[0]

    stat("Bundles persisted", n_bundles)
    stat("Bundles with linked source paragraph", n_with_chunk, n_bundles)
    stat("Bundles with breadcrumb path", n_with_breadcrumb, n_bundles)
    stat("Bundles with at least one cross-reference", n_with_refs, n_bundles)

    # Avg sizes
    sizes = [int(r[0]) for r in cur.execute("SELECT length(bundle_json) FROM requirement_context_bundle")]
    if sizes:
        sizes.sort()
        print()
        print(f"  bundle_json size — median {sizes[len(sizes)//2]} bytes, max {sizes[-1]} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
