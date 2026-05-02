# Context-collapse + volume remediation plan

## Diagnosis (concrete)

**Volume:** 13,295 requirements. 2,522 duplicate `(title, source_text)` groups
holding 2,932 excess rows. 2,613 `source_text` strings repeat across documents
(amendments + master + revised master). Real distinct obligations are
substantially fewer than the row count suggests.

**Context loss:**
- Sections are stored as opaque strings on requirements. No
  parent/child hierarchy, no breadcrumb path, no doc-level ToC.
- `document_chunks.section_id` is NULL until `enrich_chunks` runs (and
  enrichment was not run on any of these documents — verified empirically).
- 1,052 chunks (14 % of corpus) are tables that were flattened to
  pipe-separated text. No structured rows. Cell-level highlighting
  impossible from current state.
- Zero glossary extraction. "Defined terms" sections are buried in
  ordinary chunks.
- No attachment-label → document mapping. "Attachment C" cannot be
  resolved to a specific `ingested_documents` row.
- `citation_resolver.py` is recompute-on-call, intra-proposal only,
  understands explicit refs only (no "the foregoing" / "as defined").
- React frontend has **no requirement-detail route**. Requirements
  surface in accordion lists or flat tables only. Citations open a
  side drawer but do not surface the referenced text inline.

**Reference density** (in `source_text`):
- Section X.Y: 492 (3.7 %)
- Appendix X: 197 (1.5 %)
- "in accordance with": 198 (1.5 %)
- "as defined / set forth": 137 (1.0 %)
- Form X: 112 · Schedule X: 104 · "See Section/Att": 92
- Total: roughly 10–15 % of requirements have at least one explicit
  cross-reference. Each must resolve correctly because they're carrying
  the substance of the obligation.

## Architecture

Heavy lift in this Claude Code session. Persisted to SQLite. UI consumes
denormalized rows. Live API is for runtime interaction (chat, accept/decline,
score-after-edit) — not heavy computation.

Each computation produces its own table; pipelines are re-runnable
(idempotent — drop & rebuild). Original `rfp_requirements` data is never
mutated; we add side tables and a final denormalized bundle.

## New schema (additive only — no migrations to existing tables)

```
section_hierarchy           parent/child tree per document
extracted_tables            structured table extraction
extracted_table_rows        rows with cell-level addressability
glossary_terms              defined terms with source pointers
requirement_references      resolved cross-refs, persisted
requirement_glossary_link   which terms appear in each requirement
requirement_reverse_refs    inbound citations
requirement_groups          consolidation rollup (parent + children)
requirement_context_bundle  denormalized JSON for fast UI render
```

`requirement_source_links` is already built (98.9 % linkage).

## Execution order

1. **Hard dedup** — collapse identical `(title, source_text)` groups across
   docs. ~2,900 row reduction. Mark survivors; keep originals as
   `superseded_by_requirement_id`.

2. **Section hierarchy** — parse heading patterns from chunk content for
   each document; build `(document_id, section_id, parent_section_id, title,
   depth, ord)`. Backfill `document_chunks.section_id` from heading positions.

3. **Table extraction** — for each tabular chunk, detect headers, parse rows,
   persist to `extracted_tables` + `extracted_table_rows`. Link table to its
   chunk and section.

4. **Glossary extraction** — regex-detect "X means Y", "X shall mean Y",
   "X is defined as Y", plus explicit "Definitions" sections (look for
   chunks whose nearest heading is "Definitions" / "Defined Terms").

5. **Reference resolution + persistence** — for every requirement, scan
   `source_text` and `description` for: section refs, appendix/exhibit/
   attachment/schedule refs, form refs, regulatory citations
   (N.J.S.A./C.F.R./U.S.C.). Resolve each to `(target_kind, target_id,
   doc_id, page)`. Cross-document resolution for attachments. Mark
   unresolved.

6. **Reverse references + glossary linkage** — derived from step 5.

7. **Volume rollup** — group atomic requirements into thematic / sub-part
   parents. Use deterministic heuristics: same parent section + sequential
   chunk_index + shared lead-in phrase = sub-parts of one obligation. Keep
   atomic rows as children for audit.

8. **Context bundle** — for each surviving requirement, write a
   `requirement_context_bundle` row containing the full UI payload as JSON:
   text, breadcrumb, source paragraph, all resolved refs (with their
   resolved content excerpts), applicable glossary terms (with definitions),
   nearby tables (with highlighted rows), reverse refs.

9. **API endpoints**:
   - `GET /api/knowledge/requirements/{id}/bundle`
   - `GET /api/knowledge/requirements/tree?proposal_id=`
   - `GET /api/knowledge/glossary?proposal_id=`
   - `GET /api/knowledge/tables/{id}`

10. **UI**:
    - New route `/p/:proposalId/requirement/:requirementId` —
      single-screen detail view.
    - List view consumes the consolidated tree.
    - Inline reference resolution (hover/click expands referenced text).
    - Table rendering with row highlighting.

11. **Downstream**: update synthesis (`parsons_response.draft`) to pull
    from the bundle so paraphrasing uses the resolved-ref payload, not the
    bare source_text.

## Quality bar (self-assessment criteria)

- Open any requirement → all context on one screen, zero navigation.
- Volume manageable: hundreds of nodes, not 13K.
- Eight phases not broken; synthesis improved by richer input.
- UI organized around the actual flow.
