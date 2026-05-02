from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, UniqueConstraint
from .database import Base
from datetime import datetime

class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    content = Column(Text)
    doc_type = Column(String)  # e.g., pdf, docx, image
    embeddings = Column(Text)  # Placeholder for embeddings

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    hashed_password = Column(String)
    role = Column(String)  # vendor, evaluator, admin, state_official
    is_active = Column(Boolean, default=True)
    must_change_password = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Proposal(Base):
    __tablename__ = "proposals"

    id = Column(Integer, primary_key=True, index=True)
    vendor_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String)
    # URL-friendly stable identifier (e.g. "nj-mvc-2026"). Optional; falls back
    # to numeric id everywhere. Lets the portfolio link bookmark cleanly.
    slug = Column(String, nullable=True, unique=True, index=True)
    rfp_reference = Column(String)  # NJ MVC RFP reference
    solicitation_number = Column(String, nullable=True)  # e.g. "20DPP00471"
    issuing_agency = Column(String, nullable=True)  # e.g. "NJ Treasury / MVC"
    due_date = Column(DateTime, nullable=True)  # proposal submission deadline
    # Contract Effective Date / "Start". Anchor for relative implementation
    # deadlines like "Start + 30d". Populated manually by the user once the
    # contract is awarded; before that, plan deliverables are shown as
    # offsets only (Gantt view). Added 2026-04-28.
    contract_effective_date = Column(DateTime, nullable=True)
    status = Column(String, default="draft")  # draft, in_flight, submitted, under_review, awarded, lost
    submission_date = Column(DateTime)
    target_competitor_id = Column(Integer, ForeignKey("competitors.id"), nullable=True)  # competitor to score against in the response panel
    # Portfolio dashboard fields — populated by the New-RFP wizard, editable
    # later. All optional so legacy seed rows keep working.
    capture_lead = Column(String, nullable=True)  # "Robert Leckie" — display only
    win_probability = Column(Integer, nullable=True)  # 0-100, capture-team estimate
    target_value_usd = Column(Float, nullable=True)  # contract ceiling / TCV
    description = Column(Text, nullable=True)  # short capture summary for the portfolio card
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ProposalSection(Base):
    __tablename__ = "proposal_sections"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"))
    section_id = Column(String)  # vendorLegal, technicalProposal, etc.
    section_name = Column(String)
    content = Column(Text)
    refined_content = Column(Text)
    status = Column(String, default="draft")  # draft, refined, approved, needs_review
    conflicts = Column(Text)  # JSON string of conflicts
    score = Column(Integer, default=0)
    evaluator_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"))
    evaluator_id = Column(Integer, ForeignKey("users.id"))
    section_id = Column(String)
    score = Column(Integer)
    comments = Column(Text)
    evaluation_date = Column(DateTime, default=datetime.utcnow)

class OrchestratorData(Base):
    __tablename__ = "orchestrator_data"
    id = Column(Integer, primary_key=True)
    data = Column(Text)

class CompetitiveResearchData(Base):
    __tablename__ = "competitive_research_data"
    id = Column(Integer, primary_key=True)
    data = Column(Text)

class ParsonsSMEData(Base):
    __tablename__ = "parsons_sme_data"
    id = Column(Integer, primary_key=True)
    data = Column(Text)

class ConflictDetectorData(Base):
    __tablename__ = "conflict_detector_data"
    id = Column(Integer, primary_key=True)
    data = Column(Text)

class DocumentComposerData(Base):
    __tablename__ = "document_composer_data"
    id = Column(Integer, primary_key=True)
    data = Column(Text)

class RequirementExtractorData(Base):
    __tablename__ = "requirement_extractor_data"
    id = Column(Integer, primary_key=True)
    data = Column(Text)

class WorkflowData(Base):
    __tablename__ = "workflow_data"
    id = Column(Integer, primary_key=True)
    workflow_type = Column(String)
    step = Column(String)
    data = Column(Text)
    status = Column(String)  # pending, approved, rejected
    approval_level = Column(Integer, default=0)

class Persona(Base):
    __tablename__ = "personas"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(Text)
    persona_type = Column(String, default="general")  # general, analyst, competitor_writer, parsons_writer, scorer, cure_advisor
    competitor_id = Column(Integer, ForeignKey("competitors.id"), nullable=True)  # for competitor-bound personas
    system_prompt = Column(Text, nullable=True)  # the full system prompt for this persona
    role = Column(String)  # e.g., "technical_writer", "compliance_officer", "business_analyst"
    expertise_areas = Column(Text)  # JSON array of expertise areas
    writing_style = Column(Text)  # JSON object with style preferences
    tone = Column(String)  # formal, professional, conversational
    audience = Column(String)  # internal, external, technical, executive
    created_by = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=True)
    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    persona_type = Column(String)  # e.g., "technical_writer", "compliance_officer", "business_analyst"

class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(Text)
    template_type = Column(String)  # section_refinement, compliance_check, technical_writing, etc.
    template_content = Column(Text)  # The actual prompt template with placeholders
    refined_content = Column(Text, nullable=True)  # AI-refined version tuned for the target model
    refined_at = Column(DateTime, nullable=True)
    refined_by_model = Column(String, nullable=True)  # e.g. "claude-sonnet-4-20250514"
    variables = Column(Text)  # JSON array of required variables
    persona_id = Column(Integer, ForeignKey("personas.id"), nullable=True)
    model_provider = Column(String, default="openai")  # openai, claude, etc.
    model_name = Column(String, default="gpt-4")  # gpt-4, gpt-3.5-turbo, claude-3, etc.
    temperature = Column(Float, default=0.7)
    max_tokens = Column(Integer, default=2000)
    created_by = Column(Integer, ForeignKey("users.id"))
    is_active = Column(Boolean, default=True)
    usage_count = Column(Integer, default=0)
    success_rate = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id = Column(Integer, primary_key=True, index=True)
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"))
    version_number = Column(Integer)
    template_content = Column(Text)
    variables = Column(Text)
    model_provider = Column(String)
    model_name = Column(String)
    temperature = Column(Float)
    max_tokens = Column(Integer)
    change_reason = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

class PromptExecution(Base):
    __tablename__ = "prompt_executions"

    id = Column(Integer, primary_key=True, index=True)
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"))
    persona_id = Column(Integer, ForeignKey("personas.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    input_variables = Column(Text)  # JSON object of input variables
    output_content = Column(Text)
    model_provider = Column(String)
    model_name = Column(String)
    tokens_used = Column(Integer)
    execution_time_ms = Column(Integer)
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class ImplementationPhase(Base):
    __tablename__ = "implementation_phases"
    
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    description = Column(Text)
    priority = Column(String)  # critical, high, medium, low
    status = Column(String, default="pending")  # pending, in_progress, completed, blocked
    estimated_weeks = Column(Integer)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ImplementationTask(Base):
    __tablename__ = "implementation_tasks"
    
    id = Column(Integer, primary_key=True)
    phase_id = Column(Integer, ForeignKey("implementation_phases.id"))
    title = Column(String)
    description = Column(Text)
    priority = Column(String)  # critical, high, medium, low
    status = Column(String, default="pending")  # pending, in_progress, completed, blocked
    estimated_hours = Column(Float)
    dependencies = Column(Text)  # JSON array of task IDs
    assigned_to = Column(String)  # For future multi-user assignment
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ParsonsData(Base):
    __tablename__ = "parsons_data"

    id = Column(Integer, primary_key=True, index=True)
    section = Column(String, index=True)  # company-overview, technical-capabilities, etc.
    data = Column(Text)  # JSON string containing the section data
    user_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ── Scoring & Rubric ────────────────────────────────────────────────

class ScoringRubric(Base):
    __tablename__ = "scoring_rubrics"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    is_default = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ScoringRubricSection(Base):
    __tablename__ = "scoring_rubric_sections"

    id = Column(Integer, primary_key=True, index=True)
    rubric_id = Column(Integer, ForeignKey("scoring_rubrics.id"))
    section_id = Column(String)  # e.g. "technicalProposal"
    title = Column(String)
    weight_points = Column(Integer, default=0)  # 0-100
    pass_fail = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)

class SectionScore(Base):
    __tablename__ = "section_scores"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"))
    section_id = Column(String)  # matches ScoringRubricSection.section_id
    scorer_type = Column(String)  # "parsons" or "competitor"
    scorer_name = Column(String, nullable=True)  # null for parsons, competitor name otherwise
    score = Column(Integer, default=0)  # 0-100
    rationale = Column(Text, nullable=True)
    scored_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    scored_at = Column(DateTime, default=datetime.utcnow)

# ── Application Settings (key-value) ────────────────────────────────

class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value_json = Column(Text)  # JSON-encoded value
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ── Competitor Library & News ────────────────────────────────────────

class Competitor(Base):
    __tablename__ = "competitors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    website = Column(String, nullable=True)
    aliases = Column(Text, nullable=True)  # JSON array of alias names
    description = Column(Text, nullable=True)
    watchlist = Column(Boolean, default=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CompetitorNewsItem(Base):
    """News feed entry. Acts as the single news table for both:
      * competitor-tagged news (competitor_id set, proposal_id may be set
        too if the news was fetched via a proposal-scoped refresh)
      * proposal-tagged news (proposal_id set, competitor_id NULL — RFP /
        solicitation / agency-specific news)
    The dashboard's news widget queries by `proposal_id IS NOT NULL OR
    competitor_id IN (proposal's targeted competitors)`.
    """
    __tablename__ = "competitor_news_items"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"), nullable=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    title = Column(String)
    url = Column(String, nullable=True)
    source = Column(String, nullable=True)  # "google_news", "bing_news", "rss"
    # The query string the fetcher actually used — handy for debugging /
    # showing the user "we're searching for: X".
    query_used = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class ProposalCompetitor(Base):
    """Many-to-many between a proposal and the competitors the capture team
    is tracking on it. The dashboard news widget (and dossier roll-ups)
    fan out across every competitor listed here so the user can dynamically
    grow / shrink the watchlist without touching schema or restarting.
    """
    __tablename__ = "proposal_competitors"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    # "primary" is the prior single-target — surfaced first in the UI;
    # later additions land as "tracking".
    relevance = Column(String, default="tracking")  # primary | tracking | ruled_out
    notes = Column(Text, nullable=True)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ── Competitor Intelligence Dossier ──────────────────────────────────

class CompetitorDossier(Base):
    __tablename__ = "competitor_dossiers"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"), index=True)
    category = Column(String, index=True)  # leadership, technology, customer_service, contracts, financials, strengths, weaknesses, strategy
    title = Column(String)
    content = Column(Text)
    source = Column(String, nullable=True)  # "ingested_doc:42", "news:15", "analyst_generated", "manual"
    confidence = Column(String, default="medium")  # high, medium, low, unverified
    verified = Column(Boolean, default=False)
    verified_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ── Competitor Response Predictions ──────────────────────────────────

class CompetitorPrediction(Base):
    __tablename__ = "competitor_predictions"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"), index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True)
    section_id = Column(String, index=True)  # e.g. "technicalProposal"
    predicted_response = Column(Text)  # the AI-generated predicted response
    reasoning = Column(Text, nullable=True)  # why the AI thinks they'd write this
    confidence_score = Column(Integer, default=50)  # 0-100
    generated_by_persona = Column(Integer, ForeignKey("personas.id"), nullable=True)
    model_used = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ── Competitor Intelligence Evidence Pool ────────────────────────────
# These three tables back the unified intelligence pipeline. Every dossier
# category and every synthesized thread cites by ``evidence_id`` instead of
# free-text, so we get cross-category memory and traceable provenance.

class CompetitorEvidence(Base):
    __tablename__ = "competitor_evidence"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    # Connector that produced this row.
    source_connector = Column(String, nullable=False, index=True)
    source_ref = Column(String, nullable=True)
    citation_url = Column(String, nullable=True)
    title = Column(String, nullable=True)
    snippet = Column(Text, nullable=True)
    # Class buckets — see migration for the canonical list.
    claim_class = Column(String, nullable=False, index=True, default="other")
    event_date = Column(String, nullable=True)
    jurisdiction = Column(String, nullable=True)
    amount_usd = Column(Float, nullable=True)
    payload_json = Column(Text, nullable=True)
    confidence = Column(String, nullable=False, default="reported")
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class CompetitorThread(Base):
    __tablename__ = "competitor_threads"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    # Stable slug like "acquire_the_winner" for deep-linking.
    slug = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    headline = Column(Text, nullable=True)
    narrative_markdown = Column(Text, nullable=True)
    # JSON array of evidence ids.
    evidence_ids_json = Column(Text, nullable=False, default="[]")
    # JSON array of dossier-category slugs.
    category_tags_json = Column(Text, nullable=False, default="[]")
    confidence = Column(String, nullable=False, default="medium")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class CompetitorTimelineEvent(Base):
    __tablename__ = "competitor_timeline_events"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    event_date = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    jurisdiction = Column(String, nullable=True)
    amount_usd = Column(Float, nullable=True)
    evidence_ids_json = Column(Text, nullable=False, default="[]")
    thread_id = Column(Integer, ForeignKey("competitor_threads.id", ondelete="SET NULL"),
                       nullable=True, index=True)
    confidence = Column(String, nullable=False, default="reported")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

# ── Document Ingestion ───────────────────────────────────────────────

class IngestedDocument(Base):
    __tablename__ = "ingested_documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    original_filename = Column(String)
    file_type = Column(String)  # pdf, docx, txt, xlsx
    file_size = Column(Integer)  # bytes
    source_type = Column(String, index=True)  # "parsons", "competitor_foia", "competitor_proposal", "rfp", "reference"
    # Fine-grained document classification within source_type="rfp":
    #   notice_of_solicitation, rfp, addendum, qa_response, attachment, reference
    # Used by the schedule extractor to decide whether to scan for key events,
    # and by the UI to group related RFP documents.
    document_type = Column(String, nullable=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"), nullable=True)  # if source_type is competitor-related
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True)
    description = Column(Text, nullable=True)
    total_pages = Column(Integer, default=0)
    total_chunks = Column(Integer, default=0)
    status = Column(String, default="processing")  # processing, completed, failed
    error_message = Column(Text, nullable=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    # ── Parsons-knowledge metadata (only meaningful when source_type='parsons') ─
    # Slug pointing into the parsons_doc_categories taxonomy table — e.g.
    # "past_proposal", "capability_statement", "sop", "cert", "case_study".
    parsons_category = Column(String, nullable=True, index=True)
    # JSON array of jurisdiction codes the doc is relevant to ("NJ","MD","UK").
    jurisdictions_json = Column(Text, nullable=True)
    # Free-text practice area ("vehicle inspection", "transportation IT").
    practice_area = Column(String, nullable=True, index=True)
    # The proposal this knowledge entry is scoped to. NULL = global Parsons
    # library (visible to every RFP); not-NULL = industry-specific to that
    # proposal only.
    parsons_scope_proposal_id = Column(Integer, ForeignKey("proposals.id"),
                                        nullable=True, index=True)
    # ── Quality assessment (Parsons Knowledge only) ────────────────
    # 0-100 LLM-assigned quality score against the category's expected
    # purpose ("does this past_proposal actually substantiate Parsons'
    # capabilities?"). NULL = not yet assessed.
    quality_score = Column(Integer, nullable=True)
    # Short headline ("Strong technical depth, missing pricing context")
    quality_headline = Column(String, nullable=True)
    # Full rationale from the assessor (markdown).
    quality_rationale = Column(Text, nullable=True)
    quality_assessed_at = Column(DateTime, nullable=True)
    # ── Supersession (Parsons Knowledge) ────────────────────────────
    # When a newer document supersedes an older one, the older row's
    # `superseded_by_document_id` points at the newer doc and
    # `superseded_at` is stamped. Superseded docs STAY in the DB for
    # auditability but are excluded from default vector retrieval — the
    # response writer cites the active version. To restore, NULL these
    # fields out via the supersession-revert endpoint.
    superseded_by_document_id = Column(Integer, ForeignKey("ingested_documents.id"),
                                        nullable=True, index=True)
    superseded_at = Column(DateTime, nullable=True)
    superseded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    superseded_reason = Column(Text, nullable=True)
    # Upload timestamp (column exists in the DB; declare here so the ORM
    # can use it for ordering and JSON serialization in /api/documents).
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)


class ParsonsUploadJob(Base):
    """Tracks the multi-stage upload+ingest+enrich+coverage-rerun pipeline
    that fires when a user uploads a Parsons knowledge document via the
    wizard. The wizard polls this row to render the live progress timeline.

    Stages (in order):
      1. uploaded         — file saved + IngestedDocument row created
      2. parsing          — chunking the document
      3. embedding        — generating chunk embeddings + section tags
      4. quality_assess   — per-doc quality scoring
      5. supersession     — applying user's superseded-predecessors choices
      6. coverage_rerun   — re-running parsons_coverage on impacted reqs
      7. classify         — re-running disposition classifier on newly-affected gaps
      8. complete         — all done; UI can show the final summary

    Each stage records started_at / completed_at / error so the wizard
    timeline can render durations and pinpoint which stage failed.
    """
    __tablename__ = "parsons_upload_jobs"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # current overall status: queued | running | complete | failed
    status = Column(String, default="queued", index=True)
    # current stage label for the live progress UI
    current_stage = Column(String, nullable=True)
    progress_note = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    # JSON payload of the wizard form choices: parsons_category, scope, etc.
    form_payload_json = Column(Text, nullable=True)
    # Per-stage timestamps
    parsing_started_at = Column(DateTime, nullable=True)
    parsing_completed_at = Column(DateTime, nullable=True)
    embedding_started_at = Column(DateTime, nullable=True)
    embedding_completed_at = Column(DateTime, nullable=True)
    quality_started_at = Column(DateTime, nullable=True)
    quality_completed_at = Column(DateTime, nullable=True)
    supersession_started_at = Column(DateTime, nullable=True)
    supersession_completed_at = Column(DateTime, nullable=True)
    coverage_started_at = Column(DateTime, nullable=True)
    coverage_completed_at = Column(DateTime, nullable=True)
    classify_started_at = Column(DateTime, nullable=True)
    classify_completed_at = Column(DateTime, nullable=True)
    # Counts surfaced in the wizard summary card on completion
    coverage_reqs_reassessed = Column(Integer, default=0)
    coverage_reqs_promoted = Column(Integer, default=0)  # gap → covered/partial
    classify_reqs_classified = Column(Integer, default=0)
    superseded_doc_ids = Column(Text, nullable=True)  # JSON array of int
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id"), index=True)
    chunk_index = Column(Integer)  # ordering within the document
    page_number = Column(Integer, nullable=True)  # source page (for PDFs)
    content = Column(Text)
    char_count = Column(Integer)
    metadata_json = Column(Text, nullable=True)  # JSON: headings, tables detected, etc.
    created_at = Column(DateTime, default=datetime.utcnow) 

    # Smart ingestion fields (added for knowledge base)
    embedding = Column(Text, nullable=True)  # JSON array of floats (384-dim for MiniLM)
    section_tags = Column(Text, nullable=True)  # JSON array of section_ids this chunk is relevant to
    classification = Column(String, nullable=True)  # "requirement", "past_performance", "technical", "staffing", "cost", "compliance", "general"
    key_facts = Column(Text, nullable=True)  # JSON array of extracted facts
    # Primary section anchor — populated by section-aware chunker for RFP
    # docs. The string of the section header this chunk falls under, e.g.
    # "4.15", "8.9.1", "Appendix 3.2H", "Section 5.7(B)". When present,
    # search can boost or filter by exact section. NULL for chunks that
    # don't have a clear section boundary (e.g. cover pages, tables).
    section_id = Column(String, nullable=True, index=True)
    # Form-factor tag for fit-aware synthesis. See
    # scripts/context/s12_form_factor_tags.py for the taxonomy. NULL means
    # general / cross-cutting and survives every fit filter.
    form_factor = Column(String, nullable=True)

# ── RFP Requirements (extracted from the RFP document) ───────────────

class RfpRequirement(Base):
    __tablename__ = "rfp_requirements"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id"), nullable=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    requirement_id = Column(String, index=True)  # e.g. "REQ-001"
    section_id = Column(String, index=True)  # which RFP section this maps to
    category = Column(String)  # "mandatory", "scored", "informational", "certification", "form", "signature"
    priority = Column(String, nullable=True)  # "critical", "high", "medium", "low" (from orchestrator)
    title = Column(String)
    description = Column(Text)
    source_page = Column(Integer, nullable=True)
    source_text = Column(Text, nullable=True)  # original text from the RFP
    compliance_status = Column(String, default="not_assessed")  # not_assessed, compliant, partial, non_compliant, not_applicable
    parsons_evidence = Column(Text, nullable=True)  # how Parsons meets this
    verified = Column(Boolean, default=False)
    verified_by = Column(String, nullable=True)  # persona name or user
    verification_notes = Column(Text, nullable=True)
    reviewer_confidence = Column(String, nullable=True)  # "high"/"medium"/"low" — set by validator pass
    extraction_pass = Column(String, nullable=True)  # "reconciled" (both agreed), "analyst_only", "validator_only"
    notes = Column(Text, nullable=True)  # orchestrator-supplied commentary / disagreements
    # Requirement-class layer (populated post-extraction by classify_requirements):
    #   proposal_obligation  — "The Contractor shall/must do X" (compliance matrix + coverage)
    #   technical_spec       — equipment/system specifications (coverage, tech volume)
    #   checklist_row        — rows from inspection/business-rule tables (reference only)
    #   deadline             — dated obligation (schedule + coverage)
    #   inherited_hr         — CBA/union clauses (inherited IF staff transition)
    #   sf_form              — "Submit Form X" / certifications / signature pages
    #   informational        — definitions / background / context
    #   unclassified         — not yet classified (default for new rows)
    requirement_class = Column(String, nullable=True, index=True, default="unclassified")
    class_reason = Column(String, nullable=True)  # short rationale from classifier
    class_source = Column(String, nullable=True)  # "heuristic" | "llm" | "manual"
    # ── Parsons coverage assessment (driven by vector search + LLM rubric) ──
    # Coverage status:
    #   covered     — direct match in Parsons knowledge with high confidence
    #   partial     — some matches but with gaps the SME must close
    #   gap         — no relevant Parsons evidence found
    #   uncertain   — match exists but LLM can't tell if it answers the req
    #   not_assessed — coverage pass hasn't run yet (default)
    parsons_coverage_status = Column(String, nullable=True, index=True,
                                      default="not_assessed")
    parsons_coverage_notes = Column(Text, nullable=True)
    # JSON array of IngestedDocument.id values that fed the assessment.
    parsons_evidence_doc_ids = Column(Text, nullable=True)
    parsons_coverage_assessed_at = Column(DateTime, nullable=True)
    # ── Response disposition: how this requirement should be RESPONDED TO ──
    # Distinct from parsons_coverage_status (which measures "do we have
    # capability evidence for this"). Some requirements need historical
    # evidence; others just need a commitment, an acknowledgment, or a form.
    # Treating all gaps the same way is wrong — site visits don't need past
    # proposal evidence, they need a calendar attendance commitment.
    #
    # Values:
    #   evidence_required   — needs past performance / capability statement
    #   commitment_only     — Parsons promises to follow this process
    #   acknowledgment_only — Parsons accepts this clause / right
    #   event_attendance    — scheduled event we'll attend
    #   form_to_complete    — fill out an RFP form/cert at submission time
    #   informational_only  — no response required
    response_disposition = Column(String, nullable=True, index=True)
    response_disposition_notes = Column(Text, nullable=True)
    response_disposition_classified_at = Column(DateTime, nullable=True)
    # How the disposition was set: regex (rule-based), llm, or manual
    response_disposition_classifier = Column(String, nullable=True)
    # Form-factor tag for fit-aware Parsons-content matching. Values come
    # from scripts/context/s12_form_factor_tags.py — keeps "workstation"
    # content out of "tablet" responses and vice versa.
    form_factor = Column(String, nullable=True, index=True)
    # Dedup + rollup metadata, populated by scripts/context/s01 + s09. NULL
    # means "no relationship recorded" (= a standalone canonical row).
    superseded_by_requirement_id = Column(Integer, ForeignKey("rfp_requirements.id"), nullable=True)
    duplicate_group_key = Column(String, nullable=True)
    rollup_role = Column(String, nullable=True)         # 'parent' | 'child' | NULL
    primary_section_id = Column(Integer, nullable=True)  # FK to section_hierarchy.id (table managed by scripts)
    # ── Per-requirement Parsons response (the response-from-compliance flow) ─
    # The user-edited / AI-suggested paragraph that answers THIS requirement.
    # Section narratives are assembled from these in the response builder, so
    # there's a single voice across the proposal. compliance_disposition is
    # one of: Comply | Comply-with-exception | Take-exception | Not-Applicable
    # | Needs-Clarification (matches the existing RfpSectionResponse tagging).
    parsons_response = Column(Text, nullable=True)
    parsons_response_status = Column(String, nullable=True, index=True,
                                       default="not_started")
    # not_started | ai_drafted | user_edited | approved | exported
    compliance_disposition = Column(String, nullable=True)
    parsons_response_authored_by_user_id = Column(Integer,
        ForeignKey("users.id"), nullable=True)
    parsons_response_updated_at = Column(DateTime, nullable=True)
    # Chunk-level evidence cited by the drafter — JSON array of objects:
    # [{"chunk_id": int, "document_id": int, "document_name": str,
    #   "page": int|null, "similarity": float, "snippet": str}]
    # Persisted at draft time so the user can audit "what did the AI read?"
    # without re-running the retrieval. Cleared/regenerated on each AI draft.
    parsons_response_cited_evidence = Column(Text, nullable=True)
    # ── RFP-native submission bucket (populated by the structure-mapper) ──
    # Slug pointing into ProposalSubmissionSection.slug for THIS proposal.
    # When NULL, the dashboard falls back to the regex-based _section_root.
    # Once populated, the rollup respects what the RFP itself says about
    # how the vendor must submit (e.g. "Forms", "Technical Quote",
    # "State-Supplied Price Sheet" for the NJ MVC RFP).
    submission_section_slug = Column(String, nullable=True, index=True)
    submission_subsection_slug = Column(String, nullable=True, index=True)
    submission_mapping_confidence = Column(String, nullable=True)
    submission_mapping_rationale = Column(Text, nullable=True)
    # ── Competitive positioning ──
    # Tag each requirement with where Parsons stands relative to known
    # competitors. Populated by ``competitive_positioning`` service.
    #   strong  — Parsons is well-positioned AND/OR competitors weak here
    #   parity  — both can answer this similarly
    #   weak    — Parsons gap AND/OR competitors known to be strong here
    #   neutral — informational / no competitive angle
    competitive_position = Column(String, nullable=True, index=True)
    competitive_rationale = Column(Text, nullable=True)
    # JSON arrays of competitor IDs the analyst tagged on either side of
    # the comparison so the UI can deep-link into dossiers.
    competitive_advantage_competitors = Column(Text, nullable=True)
    competitive_risk_competitors = Column(Text, nullable=True)
    competitive_assessed_at = Column(DateTime, nullable=True)
    # Snapshot of the FIRST AI draft so user_edited responses can diff back.
    parsons_response_ai_original = Column(Text, nullable=True)
    # Reviewer rejection workflow: when set, indicates a reviewer kicked the
    # response back to AI with `parsons_response_review_feedback` as the
    # rationale. Drafter can then re-run with the feedback in the prompt.
    parsons_response_review_feedback = Column(Text, nullable=True)
    # Owner / assignee for the per-requirement response (FK to users).
    parsons_response_assignee_id = Column(Integer,
        ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProposalSubmissionSection(Base):
    """The RFP's OWN definition of how the vendor must submit.

    Extracted directly from the RFP (typically a "Quote Content" or
    "Proposal Format" section, e.g. "3.12 QUOTE CONTENT" in the NJ MVC
    Bid Solicitation). Each row represents one bullet/heading in that
    structure so we can drive the readiness dashboard with the RFP's
    actual terminology rather than a regex rollup of requirement IDs.

    Hierarchy: a top-level row has parent_id=NULL. Sub-items reference
    their parent. ``order_index`` preserves the order the RFP lists them.
    """
    __tablename__ = "proposal_submission_sections"
    __table_args__ = (
        UniqueConstraint("proposal_id", "slug",
                         name="uq_submission_section_per_proposal"),
    )

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"),
                         nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("proposal_submission_sections.id"),
                       nullable=True, index=True)
    slug = Column(String, nullable=False, index=True)  # stable id
    label = Column(String, nullable=False)             # the RFP's exact name
    rfp_section_ref = Column(String, nullable=True)    # e.g. "3.12", "3.13.1"
    description = Column(Text, nullable=True)
    source_document_id = Column(Integer, ForeignKey("ingested_documents.id"),
                                 nullable=True)
    source_page = Column(Integer, nullable=True)
    source_quote = Column(Text, nullable=True)
    order_index = Column(Integer, default=0, index=True)
    is_top_level = Column(Boolean, default=False, index=True)
    extracted_at = Column(DateTime, default=datetime.utcnow)
    extraction_run_id = Column(String, nullable=True, index=True)


class ProposalSectionNarrative(Base):
    """Persisted assembled narrative for one (proposal, section_root) pair.

    Built by ``assemble_section_narrative`` and saved here so reviewers can
    edit the prose without losing it on refresh. ``is_stale`` is set whenever
    any underlying RfpRequirement.parsons_response in the section changes,
    nudging the user to re-assemble.
    """
    __tablename__ = "proposal_section_narratives"
    __table_args__ = (
        UniqueConstraint("proposal_id", "section_root",
                         name="uq_section_narrative_per_proposal"),
    )

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=False, index=True)
    section_root = Column(String, nullable=False, index=True)
    narrative_md = Column(Text, nullable=True)
    requirement_ids = Column(Text, nullable=True)  # JSON array of source RfpRequirement IDs
    is_stale = Column(Boolean, default=False, index=True)
    locked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_assembled_at = Column(DateTime, nullable=True)
    last_edited_at = Column(DateTime, nullable=True)
    last_edited_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RequirementComment(Base):
    """Comment thread on a single RfpRequirement (review notes, questions
    to the SME, etc.). Lightweight — no resolution status yet, just chrono."""
    __tablename__ = "requirement_comments"

    id = Column(Integer, primary_key=True, index=True)
    requirement_id = Column(Integer, ForeignKey("rfp_requirements.id"),
                            nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    author_label = Column(String, nullable=True)  # "AI" or display name fallback
    body = Column(Text, nullable=False)
    kind = Column(String, nullable=True)  # "comment" | "rejection" | "system"
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class CrossSectionConflict(Base):
    """A single cross-section conflict surfaced by the conflict scanner.
    Helps reviewers catch drift between sections (e.g. Section 3 says we
    use Method A, Section 7 says Method B)."""
    __tablename__ = "cross_section_conflicts"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"),
                         nullable=False, index=True)
    section_root_a = Column(String, nullable=False)
    section_root_b = Column(String, nullable=False)
    severity = Column(String, nullable=True)  # high|medium|low
    summary = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)
    requirement_ids_a = Column(Text, nullable=True)  # JSON array
    requirement_ids_b = Column(Text, nullable=True)  # JSON array
    status = Column(String, default="open", index=True)  # open | dismissed | resolved
    resolved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    scan_id = Column(String, nullable=True, index=True)  # group conflicts from same scan run
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class DiffCouplingFinding(Base):
    """A second-order effect surfaced by the coupling scanner.

    Pairs a CHANGED diff row with a related (often UNCHANGED) requirement
    in the new RFP whose meaning may shift because of the change — i.e.
    a defining-language change that has implicit downstream impact on
    requirements that still read identically.

    Example: 2021 said "Contractor shall provide adequate staffing"
    + a separate row defined "adequate" as 60 staff. 2026 keeps the
    same first sentence (sim≈0.99 → unchanged) but raises the staffing
    definition to 80. The coupling finding pairs the unchanged
    "adequate staffing" row with the changed definition row and tells
    the reviewer the implicit obligation just changed.
    """
    __tablename__ = "diff_coupling_findings"

    id = Column(Integer, primary_key=True, index=True)
    diff_run_id = Column(Integer, ForeignKey("rfp_diff_runs.id"),
                         nullable=False, index=True)
    # The CHANGED diff row that triggered the finding
    source_diff_row_id = Column(Integer, ForeignKey("rfp_requirement_diffs.id"),
                                nullable=False, index=True)
    # The downstream requirement (in the new RFP) that may be affected.
    # Stored as a target-side RfpRequirement.id so the UI can deep-link.
    affected_target_requirement_id = Column(Integer,
        ForeignKey("rfp_requirements.id"), nullable=False, index=True)
    # similarity (cosine) between the two requirements — a coupling
    # signal: high similarity in language but the source row changed
    # while the affected row didn't.
    coupling_similarity = Column(Float, nullable=True)
    coupling_kind = Column(String, nullable=True)
    # coupling_kind: "definition_drift" (defining language changed)
    #              | "shared_term"     (both reference the same key term/threshold)
    #              | "scope_overlap"   (same scope topic, intersecting language)
    #              | "schedule_dependency" (one references a deadline that moved)
    severity = Column(String, nullable=True)  # critical|high|medium|low
    summary = Column(Text, nullable=False)
    detail = Column(Text, nullable=True)
    suggested_question = Column(Text, nullable=True)
    status = Column(String, default="open", index=True)  # open | dismissed | accepted
    scan_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ConsolidatedBaselineRun(Base):
    """One execution of the baseline consolidator.

    The consolidator takes a master RFP document plus a chronologically-
    ordered list of amendment documents and produces a synthetic "as-amended"
    baseline — applying each amendment's modifications, additions, and
    removals to the master so that downstream diffs (against a new
    solicitation) are apples-to-apples instead of comparing the new RFP to
    a mix of original master language and incremental amendments.

    Status transitions:
      pending → running → complete (or failed)
    """
    __tablename__ = "consolidated_baseline_runs"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String, nullable=True)
    master_document_id = Column(Integer, ForeignKey("ingested_documents.id"),
                                nullable=False, index=True)
    # JSON array of amendment IngestedDocument.id values, in chronological
    # order. Each amendment is processed against the running consolidated
    # state so later amendments can supersede earlier ones.
    amendment_document_ids = Column(Text, nullable=False)
    status = Column(String, default="pending", index=True)
    progress_note = Column(String, nullable=True)
    # Counts populated as the run progresses
    total_master_requirements = Column(Integer, default=0)
    total_amendment_actions = Column(Integer, default=0)
    actions_modified = Column(Integer, default=0)
    actions_added = Column(Integer, default=0)
    actions_removed = Column(Integer, default=0)
    actions_skipped = Column(Integer, default=0)
    actions_unchanged = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ConsolidatedRequirement(Base):
    """One requirement in the as-amended consolidated baseline.

    Provenance: ``original_requirement_id`` points to the RfpRequirement
    this row was seeded from (typically a master-RFP requirement).
    ``amendment_history`` is a JSON array of audit entries describing every
    amendment action that touched this requirement:
        [{"amendment_doc_id": int, "action": "modify"|"add"|"remove",
          "amendment_req_id": int, "rationale": str, "applied_at": iso}]
    """
    __tablename__ = "consolidated_requirements"

    id = Column(Integer, primary_key=True, index=True)
    consolidation_run_id = Column(Integer,
        ForeignKey("consolidated_baseline_runs.id"),
        nullable=False, index=True)
    # Snapshot of the requirement's content as of the end of consolidation.
    # These mirror RfpRequirement fields so downstream diff code can treat
    # consolidated rows like regular requirements.
    requirement_id = Column(String, index=True)
    section_id = Column(String, index=True)
    category = Column(String)
    priority = Column(String, nullable=True)
    title = Column(String)
    description = Column(Text)
    source_text = Column(Text, nullable=True)
    source_page = Column(Integer, nullable=True)
    # Bookkeeping
    original_requirement_id = Column(Integer,
        ForeignKey("rfp_requirements.id"), nullable=True, index=True)
    source_document_id = Column(Integer,
        ForeignKey("ingested_documents.id"), nullable=True)
    is_removed = Column(Boolean, default=False, index=True)
    is_added_by_amendment = Column(Boolean, default=False, index=True)
    amendment_history = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class ParsonsDocCategory(Base):
    """Extensible taxonomy for the Parsons Knowledge library.

    Seeded with the standard buckets (past_proposal, capability_statement,
    sop, cert, case_study, pricing_history, org_resume, other). The UI
    surfaces an "Add category" affordance so the user can extend without
    a code change. Categories with is_system=True can't be deleted from
    the UI to protect the seeded set.
    """
    __tablename__ = "parsons_doc_categories"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    label = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    icon_hint = Column(String, nullable=True)  # MUI icon name hint for the UI
    is_system = Column(Boolean, default=False)
    sort_order = Column(Integer, default=100)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ParsonsDocSuggestion(Base):
    """LLM-generated improvement suggestion for a Parsons knowledge document.

    Created by the quality-assessment pass. Each suggestion proposes a
    concrete edit (or addition) that would make the document more useful
    to the AI when it tries to cite Parsons evidence on RFP requirements.

    Lifecycle:
      pending  — awaiting user disposition
      accepted — user accepted: edit applied to a chunk; new content embedded
      rejected — user rejected: no change applied
      ignored  — user dismissed for now (re-surfacable on the next pass)
    """
    __tablename__ = "parsons_doc_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    severity = Column(String, default="medium")  # critical | high | medium | low
    # Short title for the row ("Add jurisdictions covered").
    title = Column(String, nullable=False)
    # The full suggestion explanation, markdown.
    rationale = Column(Text, nullable=True)
    # Optional structured fields the LLM can fill if it has a concrete edit:
    suggested_text = Column(Text, nullable=True)         # exact text to insert/replace
    target_chunk_id = Column(Integer, nullable=True)     # which chunk to edit (or null = whole-doc)
    edit_kind = Column(String, default="append")        # append | replace | prepend | metadata
    # Lifecycle:
    status = Column(String, default="pending", index=True)  # pending|accepted|rejected|ignored
    # When accepted, the actual content the user committed (may differ
    # from suggested_text if they edited it).
    applied_text = Column(Text, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    applied_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ParsonsDocAuditLog(Base):
    """Audit trail for every state change on a Parsons knowledge document.

    Captures who did what and when so the team can answer "who edited
    this past proposal yesterday and why?". Auto-approved actions still
    write rows here so the future approval-workflow refit gets full
    history out of the box.

    action values:
      uploaded, assessed_quality, suggestion_accepted, suggestion_rejected,
      suggestion_ignored, content_edited, scope_changed, category_changed,
      deleted, approval_auto, approval_granted, approval_revoked
    """
    __tablename__ = "parsons_doc_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    suggestion_id = Column(Integer, ForeignKey("parsons_doc_suggestions.id", ondelete="SET NULL"),
                           nullable=True)
    action = Column(String, nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_label = Column(String, nullable=True)  # cached user display name
    # JSON {before, after, ...} so the audit row is self-contained even
    # if the underlying chunk gets later mutated.
    payload_json = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ParsonsDocApproval(Base):
    """Approval record for a Parsons knowledge document.

    For now every upload + accepted edit auto-approves (status='auto_approved'
    written by the system) — the row exists so the real review workflow
    can drop in cleanly later: a reviewer just changes the status, the UI
    starts gating on it, and the audit log already has the trail.
    """
    __tablename__ = "parsons_doc_approvals"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    status = Column(String, default="auto_approved", index=True)
    # auto_approved | pending_review | approved | rejected | revoked
    approver_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── RFP Schedule / Key Events (extracted from Notice of Solicitation) ─

class RfpScheduleEvent(Base):
    """
    A single scheduled event / milestone tied to a solicitation.

    Sourced from:
      - AI extraction over the Notice / RFP text (extracted_by_ai=True)
      - User manually added (extracted_by_ai=False)
      - Derived internal deadlines (event_type starts with "internal_")

    event_type values (canonical, but free-form allowed):
      question_period_start, question_period_end, pre_bid_conference,
      mandatory_site_visit, addenda_cutoff, proposal_due, bid_opening,
      evaluation_period_start, evaluation_period_end, bafo_due,
      award_notification, contract_start, period_of_performance_start,
      period_of_performance_end, internal_review, internal_red_team,
      internal_pricing_final, internal_sme_signoff, internal_production,
      other
    """
    __tablename__ = "rfp_schedule_events"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id"), nullable=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)

    event_type = Column(String, index=True)
    label = Column(String)  # human-readable name, e.g. "Proposal Due Date"
    event_date = Column(DateTime, nullable=True)  # primary date/time
    end_date = Column(DateTime, nullable=True)  # for ranges (e.g. Q&A period)
    is_mandatory = Column(Boolean, default=False)  # mandatory attendance / hard deadline

    source_page = Column(Integer, nullable=True)
    source_text = Column(Text, nullable=True)  # verbatim excerpt from the doc
    notes = Column(Text, nullable=True)  # AI commentary or user notes

    confidence = Column(String, default="medium")  # high, medium, low
    extracted_by_ai = Column(Boolean, default=False)
    verified = Column(Boolean, default=False)
    verified_by = Column(String, nullable=True)

    assignee = Column(String, nullable=True)  # who owns this milestone internally
    status = Column(String, default="upcoming")  # upcoming, in_progress, complete, missed, cancelled

    # ── Relative deadlines (added 2026-04-28) ──────────────────────
    # When the RFP expresses a deadline as "X days after Contract
    # Effective Date" or "Y days before End of Period of Performance",
    # we store the OFFSET here and let _resolve_relative_dates() compute
    # the absolute event_date once the anchor is known.
    offset_days = Column(Integer, nullable=True)            # may be negative for "End - 360d"
    offset_anchor = Column(String, nullable=True)           # 'contract_start' | 'pop_end'
    is_draft_with_quote = Column(Boolean, default=False)    # plan must be submitted in draft form with the bid
    section_refs = Column(Text, nullable=True)              # "4.2.1, 4.29.3, ..."
    event_date_resolved = Column(Boolean, default=False)    # event_date was computed from offset

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# ── RFP Questions (drafted for submission to the buyer) ──────────────

class RfpQuestion(Base):
    """
    A question drafted for submission to the procurement agency during the
    solicitation's Q&A period. Produced by the Q-drafter pipeline (spotter →
    strategist → drafter) or manually added. Flows through a configurable
    approval workflow before export for NJSTART submission.
    """
    __tablename__ = "rfp_questions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id"), nullable=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    source_section = Column(String, nullable=True)  # e.g. "4.11", "Attachment 2"
    source_page = Column(Integer, nullable=True)
    category = Column(String, index=True)  # clarification, risk, pricing, scope, competitive, form
    priority = Column(String, nullable=True)  # critical, high, medium, low
    question_text = Column(Text)
    rationale = Column(Text, nullable=True)  # why this question matters (internal only)
    source_quote = Column(Text, nullable=True)  # the ambiguous/conflicting text triggering the question
    related_requirement_ids = Column(Text, nullable=True)  # JSON array of RfpRequirement.id
    status = Column(String, default="draft", index=True)  # draft, reviewed, approved, rejected, submitted, answered
    answer_text = Column(Text, nullable=True)  # the agency's official response, once received
    answered_date = Column(DateTime, nullable=True)
    submitted_date = Column(DateTime, nullable=True)
    reviewer_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_notes = Column(Text, nullable=True)
    created_by_ai = Column(Boolean, default=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # ── Diff-driven question provenance ──
    # When a question was generated by the diff-question pipeline (post-extraction
    # → diff → "what should we ask about the changes"), these track WHY it was
    # asked so reviewers can audit the chain of reasoning.
    source_kind = Column(String, nullable=True, index=True)
    # source_kind: "manual" | "drafter" (per-document Q-drafter) | "diff_change"
    #            | "diff_addition" | "diff_removal" | "diff_inference" | "ask_agent"
    diff_run_id = Column(Integer, ForeignKey("rfp_diff_runs.id"), nullable=True, index=True)
    diff_row_ids = Column(Text, nullable=True)  # JSON array of RfpRequirementDiff.id
    inference_flag = Column(Boolean, default=False)
    # Inference confidence: how sure the agent is that a clarification is needed.
    inference_confidence = Column(String, nullable=True)
    # Free-form summary of what change in the contract operation prompted the
    # question (used when inference_flag=true). Populated by the diff-question
    # analyst service.
    operation_change_summary = Column(Text, nullable=True)
    # Reconciliation audit: what was done to/with this question relative to
    # any pre-existing questions on the same topic.
    reconciliation_action = Column(String, nullable=True, index=True)
    # reconciliation_action:
    #   "added"     — net-new question, no existing match
    #   "replaces"  — supersedes an existing question (see replaces_question_id)
    #   "updates"   — text was edited based on new analysis
    #   "duplicate" — already covered by existing; marked rejected by analyst
    replaces_question_id = Column(Integer, ForeignKey("rfp_questions.id"), nullable=True)
    superseded_by_question_id = Column(Integer, ForeignKey("rfp_questions.id"), nullable=True)
    # ── Strategic curator output (post-reconcile triage) ──
    # The curator is a separate LLM pass that asks "should we ACTUALLY
    # send this to the agency?" — checks if the answer is in the RFP,
    # if it reveals competitive strategy, etc. Distinct from quality_guard
    # (which asks "is this question coherent") and from reconcile (which
    # asks "is this redundant with our existing bank").
    curator_score = Column(Integer, nullable=True, index=True)  # 0-100
    curator_recommended = Column(Boolean, nullable=True, index=True)
    curator_reason = Column(String, nullable=True, index=True)
    # curator_reason values:
    #   "recommended"        — strong include
    #   "answer_in_rfp"      — already answered by the RFP text
    #   "reveals_strategy"   — would tip our hand competitively
    #   "trick_removal"      — asking about intentionally removed text
    #   "vague"              — wording too soft to elicit useful answer
    #   "redundant"          — overlaps another high-scored question
    #   "not_actionable"     — answer wouldn't change our bid
    curator_improved_text = Column(Text, nullable=True)
    curator_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DiffQuestionAnalysisRun(Base):
    """One run of the post-extraction analysis pipeline:
       re-run diff → generate questions from diff → reconcile with existing.

    The pipeline runs as a chained background job. Each run owns:
      * the diff_run_id it analyzed
      * the candidate questions generated (RfpQuestion rows where
        diff_run_id == self.diff_run_id and source_kind starts with "diff_")
      * the reconciliation decisions stored as RfpQuestion.reconciliation_action
    """
    __tablename__ = "diff_question_analysis_runs"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=False, index=True)
    diff_run_id = Column(Integer, ForeignKey("rfp_diff_runs.id"), nullable=True, index=True)
    label = Column(String, nullable=True)
    status = Column(String, default="pending", index=True)
    # status: pending | diff_running | generating_questions | reconciling
    #         | complete | failed
    progress_note = Column(String, nullable=True)
    diff_started_at = Column(DateTime, nullable=True)
    diff_completed_at = Column(DateTime, nullable=True)
    generation_started_at = Column(DateTime, nullable=True)
    generation_completed_at = Column(DateTime, nullable=True)
    reconcile_started_at = Column(DateTime, nullable=True)
    reconcile_completed_at = Column(DateTime, nullable=True)
    candidates_generated = Column(Integer, default=0)
    high_confidence_count = Column(Integer, default=0)
    inference_count = Column(Integer, default=0)
    reconciled_added = Column(Integer, default=0)
    reconciled_replaces = Column(Integer, default=0)
    reconciled_updates = Column(Integer, default=0)
    reconciled_duplicates = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)


# ── RFP Requirement Diff (compare two solicitations/revisions) ───────

class RfpDiffRun(Base):
    """
    A single diff run compares a baseline scope (old solicitation / revision)
    against a target scope (new one). Scope is expressed by proposal_id OR by
    an explicit document_ids list stored as JSON. Results (one row per matched
    pair or unmatched item) are stored in RfpRequirementDiff.
    """
    __tablename__ = "rfp_diff_runs"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String, nullable=True)
    baseline_proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True)
    baseline_document_ids = Column(Text, nullable=True)  # JSON array
    target_proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    target_document_ids = Column(Text, nullable=True)    # JSON array
    status = Column(String, default="pending", index=True)  # pending, running, complete, failed
    progress_note = Column(String, nullable=True)
    counts_added = Column(Integer, default=0)
    counts_removed = Column(Integer, default=0)
    counts_changed = Column(Integer, default=0)
    counts_unchanged = Column(Integer, default=0)
    high_match_threshold = Column(Float, default=0.92)
    low_match_threshold = Column(Float, default=0.70)
    error = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class RfpRequirementDiff(Base):
    __tablename__ = "rfp_requirement_diffs"

    id = Column(Integer, primary_key=True, index=True)
    diff_run_id = Column(Integer, ForeignKey("rfp_diff_runs.id"), index=True)
    baseline_requirement_id = Column(Integer, ForeignKey("rfp_requirements.id"), nullable=True, index=True)
    target_requirement_id = Column(Integer, ForeignKey("rfp_requirements.id"), nullable=True, index=True)
    status = Column(String, index=True)  # added, removed, changed, unchanged
    similarity = Column(Float, nullable=True)
    match_method = Column(String, nullable=True)  # embedding, llm_adjudicated, manual
    change_summary = Column(Text, nullable=True)  # short "what changed"
    impact_blurb = Column(Text, nullable=True)    # "why it matters" — the agent explanation
    impact_severity = Column(String, nullable=True)  # critical, high, medium, low, informational
    reviewer_status = Column(String, default="new")  # new, reviewed, dismissed, flagged
    reviewer_notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Extracted Facts (structured intelligence from chunks) ────────────

class ExtractedFact(Base):
    __tablename__ = "extracted_facts"

    id = Column(Integer, primary_key=True, index=True)
    chunk_id = Column(Integer, ForeignKey("document_chunks.id"), nullable=True)
    document_id = Column(Integer, ForeignKey("ingested_documents.id"), nullable=True)
    owner_type = Column(String, index=True)  # "parsons", "competitor"
    owner_id = Column(Integer, nullable=True)  # competitor_id if competitor
    fact_type = Column(String)  # "metric", "certification", "contract_value", "staff_count", "technology", "date", "name", "location"
    fact_key = Column(String)  # e.g. "uptime_percentage", "iso_27001", "annual_revenue"
    fact_value = Column(String)
    context = Column(Text, nullable=True)  # surrounding text for verification
    section_relevance = Column(Text, nullable=True)  # JSON array of section_ids
    confidence = Column(String, default="medium")  # high, medium, low
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# ── Multi-Agent Conversations ────────────────────────────────────────

class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id = Column(Integer, primary_key=True, index=True)
    conversation_type = Column(String, index=True)  # "rfp_extraction", "dossier_build", "section_authoring", "cure_review", "quality_check"
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True)
    section_id = Column(String, nullable=True)
    status = Column(String, default="active")  # active, consensus_reached, max_loops, completed
    current_loop = Column(Integer, default=0)
    max_loops = Column(Integer, default=3)
    summary = Column(Text, nullable=True)  # orchestrator's summary of outcome
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("agent_conversations.id"), index=True)
    persona_name = Column(String)  # which persona sent this
    persona_type = Column(String)  # "orchestrator", "rfp_analyst", "compliance_validator", etc.
    message_type = Column(String)  # "analysis", "review", "challenge", "agreement", "recommendation", "decision"
    content = Column(Text)
    loop_number = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Pricing Model (what-if engine) ───────────────────────────────────
# Designed from NJ 2019 v1.20.xlsx pricing build-up + vendor comparison.
# Every cost is a line item with current + future + reduction% + include
# toggle + per-year CPI. Rolls up to CIF/PIF price-per-transaction given
# margin %, total contract value, and base/extension years.

class PricingModel(Base):
    """Top-level pricing model bound to an RFP/Proposal."""
    __tablename__ = "pricing_models"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    name = Column(String, index=True)  # "NJ 2026 Base Case"
    description = Column(Text, nullable=True)
    version = Column(Integer, default=1)
    # Contract term
    base_years = Column(Integer, default=6)
    extension_years = Column(Integer, default=4)
    # Target & volumes (per-year volumes stored as JSON: [yr1, yr2, ...])
    target_contract_value = Column(Float, default=0.0)
    cif_volumes_json = Column(Text, nullable=True)  # e.g. "[1425000, 1900000, 1900000, ...]"
    pif_volumes_json = Column(Text, nullable=True)
    # Margins (independent per transaction type — per user requirement C)
    cif_margin_pct = Column(Float, default=12.0)
    pif_margin_pct = Column(Float, default=15.0)
    # Cached computed outputs (recomputed on save)
    computed_total_cost = Column(Float, default=0.0)
    computed_cif_cost = Column(Float, default=0.0)
    computed_pif_cost = Column(Float, default=0.0)
    computed_cif_ppt = Column(Float, default=0.0)   # price per transaction
    computed_pif_ppt = Column(Float, default=0.0)
    computed_cif_revenue = Column(Float, default=0.0)
    computed_pif_revenue = Column(Float, default=0.0)
    computed_cif_gp = Column(Float, default=0.0)
    computed_pif_gp = Column(Float, default=0.0)
    computed_total_revenue = Column(Float, default=0.0)
    computed_total_gp = Column(Float, default=0.0)
    computed_at = Column(DateTime, nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PricingScenario(Base):
    """Named variants of a PricingModel (Base / Aggressive / Conservative)."""
    __tablename__ = "pricing_scenarios"

    id = Column(Integer, primary_key=True, index=True)
    pricing_model_id = Column(Integer, ForeignKey("pricing_models.id"), index=True)
    name = Column(String)  # "Base Case", "Aggressive", "Conservative"
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False)
    # Scenario-level margin overrides (null = inherit from model)
    cif_margin_pct_override = Column(Float, nullable=True)
    pif_margin_pct_override = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PricingCategory(Base):
    """Tab + grouping on the pricing page. Admin-editable."""
    __tablename__ = "pricing_categories"

    id = Column(Integer, primary_key=True, index=True)
    pricing_model_id = Column(Integer, ForeignKey("pricing_models.id"), index=True)
    # Tab code matches the UI tab keys
    tab = Column(String, index=True)  # "assumptions", "labor", "odcs", "capital",
                                       # "hourly_rates", "submittal", "cashflow", "comparison"
    name = Column(String)  # "Canadian Labor", "Snow Removal", etc.
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    user_defined = Column(Boolean, default=False)  # True if admin added via UI
    created_at = Column(DateTime, default=datetime.utcnow)


class PricingLineItem(Base):
    """A single cost line inside a category. The core what-if unit."""
    __tablename__ = "pricing_line_items"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("pricing_categories.id"), index=True)
    name = Column(String)
    description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    # How this cost is allocated between CIF and PIF and whether it's amortized
    allocation_basis = Column(String, default="SHARED")  # CIF | PIF | SHARED | CAPITAL_AMORT
    # What-if controls (per user requirement #3)
    included = Column(Boolean, default=True)           # toggle in/out
    current_cost = Column(Float, default=0.0)          # what it costs today
    future_cost = Column(Float, default=0.0)           # projected
    reduction_pct = Column(Float, default=0.0)         # 0-100, squeezed off future_cost
    fringe_pct = Column(Float, default=0.0)            # for labor lines
    # Bill-rate build-up for hourly lines (stacked multiplicatively on the
    # fringed base rate). Non-hourly lines should leave these at 0.0.
    burden_pct = Column(Float, default=0.0)            # payroll taxes / overhead
    ga_pct = Column(Float, default=0.0)                # general & administrative
    fee_pct = Column(Float, default=0.0)               # profit / fee
    # Quantity handling
    qty = Column(Float, default=1.0)
    unit = Column(String, default="each")              # hourly | annual | monthly | per-txn | each | station
    # Sort + audit
    sort_order = Column(Integer, default=0)
    user_defined = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PricingLineEscalation(Base):
    """Per-line per-year CPI / inflation %. One row per (line, year)."""
    __tablename__ = "pricing_line_escalations"

    id = Column(Integer, primary_key=True, index=True)
    line_item_id = Column(Integer, ForeignKey("pricing_line_items.id"), index=True)
    year_idx = Column(Integer)  # 1-based: year 1, year 2, ...
    cpi_pct = Column(Float, default=0.0)  # e.g. 3.0 = 3%
    # Absolute override if you know the exact number for that year
    override_amount = Column(Float, nullable=True)


class PricingScenarioOverride(Base):
    """Sparse field-level override per scenario.

    Stores only the fields the scenario changes; all others inherit from the
    base line item. ``field`` names are one of:
    included | current_cost | future_cost | reduction_pct | fringe_pct | qty
    | cpi_pct (with year_idx set).
    """
    __tablename__ = "pricing_scenario_overrides"

    id = Column(Integer, primary_key=True, index=True)
    scenario_id = Column(Integer, ForeignKey("pricing_scenarios.id"), index=True)
    line_item_id = Column(Integer, ForeignKey("pricing_line_items.id"), index=True)
    field = Column(String)
    year_idx = Column(Integer, nullable=True)  # only for cpi_pct overrides
    value_numeric = Column(Float, nullable=True)
    value_bool = Column(Boolean, nullable=True)


# ── Pricing Coverage Analysis ────────────────────────────────────────
# Maps extracted RFP requirements onto pricing model line items so we can
# verify the model accounts for every cost-bearing obligation in the RFP.
# Suggestions are LLM-produced; a human reviews each and accepts/rejects.

class PricingCoverageRun(Base):
    """A single coverage analysis pass across a pricing model + proposal."""
    __tablename__ = "pricing_coverage_runs"

    id = Column(Integer, primary_key=True, index=True)
    pricing_model_id = Column(Integer, ForeignKey("pricing_models.id"), index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    label = Column(String, nullable=True)         # "T1628 2026 initial coverage"
    status = Column(String, default="running")    # running | completed | failed
    # Counts populated when status → completed
    requirements_scanned = Column(Integer, default=0)
    cost_bearing_count = Column(Integer, default=0)
    covered_count = Column(Integer, default=0)
    partial_count = Column(Integer, default=0)
    gap_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class PricingCoverageSuggestion(Base):
    """One requirement-to-pricing-line mapping decision.

    coverage_status:
      covered  → requirement is fully addressed by one or more existing lines
      partial  → partially addressed; recommend tweak or additional line
      gap      → no existing line addresses it; propose a new line
    """
    __tablename__ = "pricing_coverage_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("pricing_coverage_runs.id"), index=True)
    pricing_model_id = Column(Integer, ForeignKey("pricing_models.id"), index=True)
    requirement_id = Column(Integer, ForeignKey("rfp_requirements.id"), index=True)

    coverage_status = Column(String, index=True)  # covered | partial | gap
    # Existing line items that (partially or fully) address the requirement.
    # JSON array of PricingLineItem.id values.
    matched_line_item_ids_json = Column(Text, nullable=True)

    # For partial + gap: proposed line item fields (applied on accept).
    proposed_tab = Column(String, nullable=True)              # e.g. "labor"
    proposed_category_name = Column(String, nullable=True)    # "Compliance & Reporting"
    proposed_line_name = Column(String, nullable=True)
    proposed_description = Column(Text, nullable=True)
    proposed_unit = Column(String, nullable=True)             # "hours", "each", "per-txn"
    proposed_allocation_basis = Column(String, nullable=True) # CIF | PIF | SHARED | CAPITAL_AMORT
    proposed_qty = Column(Float, nullable=True)

    rationale = Column(Text, nullable=True)       # LLM explanation
    confidence = Column(String, nullable=True)    # high | medium | low
    severity = Column(String, nullable=True)      # critical | high | medium | low | informational

    # Review workflow
    status = Column(String, default="pending", index=True)  # pending | accepted | rejected | applied
    review_notes = Column(Text, nullable=True)
    accepted_line_item_id = Column(Integer, ForeignKey("pricing_line_items.id"), nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Competitor Historical Bids ──────────────────────────────────────
# Reference data stored PER COMPETITOR (user requirement #5).
# Consumed by the persona prompts when a writer persona needs to "think
# like Opus" and know their historical bidding posture.

class CompetitorHistoricalBid(Base):
    """A past bid (win or loss) by a competitor we can learn from."""
    __tablename__ = "competitor_historical_bids"

    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"), index=True)
    rfp_name = Column(String)                    # "NJ MVC Vehicle Inspection 2019"
    state = Column(String, nullable=True)        # "NJ"
    bid_year = Column(Integer, nullable=True)
    contract_term_years = Column(Integer, nullable=True)
    total_value = Column(Float, nullable=True)
    award_status = Column(String, nullable=True)  # "won" | "lost" | "withdrew"
    summary = Column(Text, nullable=True)
    source_doc = Column(String, nullable=True)    # "uploaded: NJ Vendor Cost Comparison 12-12-23.xlsx"
    notes = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CompetitorBidLineItem(Base):
    """Line-level pricing detail within a historical bid."""
    __tablename__ = "competitor_bid_line_items"

    id = Column(Integer, primary_key=True, index=True)
    historical_bid_id = Column(Integer, ForeignKey("competitor_historical_bids.id"), index=True)
    category = Column(String, nullable=True)    # "Labor", "Capital", "Per-Txn Fee", etc.
    line_name = Column(String)
    qty = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True)
    total_value = Column(Float, nullable=True)
    annual_values_json = Column(Text, nullable=True)  # JSON: [yr1, yr2, ...]
    notes = Column(Text, nullable=True)


class CompetitorBidStrategy(Base):
    """Labeled strategic tactic extracted from a historical bid.

    These get injected into persona prompts when writing "as" the competitor.
    """
    __tablename__ = "competitor_bid_strategies"

    id = Column(Integer, primary_key=True, index=True)
    historical_bid_id = Column(Integer, ForeignKey("competitor_historical_bids.id"), index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"), index=True)
    strategy_label = Column(String)               # "Union-break", "Low per-txn fee", "Flat hourly rates"
    description = Column(Text)
    evidence = Column(Text, nullable=True)        # specific numbers / quotes
    confidence = Column(String, default="medium") # high | medium | low | inferred
    created_at = Column(DateTime, default=datetime.utcnow)


class StaffingPosition(Base):
    """A named role in the staffing model for a pricing model.

    Stores headcount per contract year as a JSON array, plus cost build-up
    inputs (salary/rate, fringe, burden, G&A, fee).  Loaded annual cost is
    computed server-side: headcount × annual_cost × (1+fringe%) × (1+burden%)
    × (1+G&A%) × (1+fee%).

    For hourly roles: annual_cost = hourly_rate × hours_per_year.
    For salaried roles: annual_cost = base_salary.

    All roles must be US-based (N.J.S.A. 52:34-13.2 Source Disclosure compliance).
    """
    __tablename__ = "staffing_positions"

    id = Column(Integer, primary_key=True, index=True)
    pricing_model_id = Column(Integer, ForeignKey("pricing_models.id"), index=True)
    role_title = Column(String)
    classification = Column(String, default="salaried")
    # hourly_key_personnel | salaried_mgmt | union_hourly | odc | field_operations
    base_salary = Column(Float, default=0.0)      # annual salary (salaried/mgmt/field)
    hourly_rate = Column(Float, default=0.0)       # $/hr (hourly key personnel / union / canadian)
    hours_per_year = Column(Float, default=1696.0) # FT = 1696 hrs; PT = 1066 hrs; Warehouse = 1920
    fringe_pct = Column(Float, default=0.0)
    burden_pct = Column(Float, default=0.0)
    ga_pct = Column(Float, default=0.0)
    fee_pct = Column(Float, default=0.0)
    # headcount_by_year: JSON array [yr1, yr2, ...] length = base_years + extension_years
    headcount_by_year = Column(Text, default="[]")
    escalation_pct = Column(Float, default=0.0)   # annual % increase applied compounding from yr2
    allocation_basis = Column(String, default="SHARED")  # CIF | PIF | SHARED
    # Pay structure
    pay_type = Column(String, default="hourly")         # hourly | salaried
    overtime_eligible = Column(Boolean, default=False)
    overtime_pct = Column(Float, default=0.0)           # % of hours that are OT (paid at 1.5x)
    # Engagement scope
    engagement_type = Column(String, default="fte")     # fte | project | implementation | temporary | contract
    notes = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    included = Column(Boolean, default=True)
    user_defined = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PricingAuditLog(Base):
    """Immutable change log for a pricing model.

    One row per mutation. Used for version comparison, rollback, and
    compliance on high-dollar bids.
    """
    __tablename__ = "pricing_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    pricing_model_id = Column(Integer, ForeignKey("pricing_models.id"), index=True)
    version = Column(Integer)                       # model version AT TIME OF CHANGE
    action = Column(String)                         # create|update|delete|compute|clone
    entity_type = Column(String)                    # model|category|line_item|escalation|scenario|override
    entity_id = Column(Integer, nullable=True)
    field = Column(String, nullable=True)           # which field changed (for update)
    old_value = Column(Text, nullable=True)         # JSON-serialized
    new_value = Column(Text, nullable=True)         # JSON-serialized
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ── Response Workbench ───────────────────────────────────────────────
#
# The Workbench operates at the EXTRACTED RFP section granularity
# (e.g. "7.1", "3.13.8.1"), not the 13 big ProposalSection buckets.
# Every extracted section maps to exactly one rubric section via
# `RfpSectionRubricMap` so scores can still roll up to the 5-criterion
# NJ T1628 rubric.
#
# Existing tables reused without schema change:
#   - SectionScore      — scorer_type/scorer_name/score per (proposal_id, section_id)
#   - CompetitorPrediction — competitor "what would they write" per section
#   - ScoringRubric / ScoringRubricSection — weight_points (max) + pass_fail gates
#
# New tables here:
#   1. rfp_section_rubric_map   — extracted_section_id → rubric_section_id
#   2. rfp_section_response     — Parsons / competitor narrative per extracted section
#   3. section_cure_suggestion  — AI-proposed improvements with approve/deny workflow


class RfpSectionRubricMap(Base):
    """Maps an extracted RFP section_id (e.g. "7.1") to a rubric section_id
    (e.g. "technicalApproach") so per-section scores roll up to the
    5-criterion NJ T1628 rubric for aggregate reporting.

    `source` distinguishes auto-heuristic mappings from user-confirmed ones.
    """
    __tablename__ = "rfp_section_rubric_map"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), index=True)
    rfp_section_id = Column(String, index=True)  # extracted section id, e.g. "7.1"
    rubric_section_id = Column(String, index=True)  # e.g. "technicalApproach"
    source = Column(String, default="auto")  # "auto" | "manual"
    confidence = Column(String, nullable=True)  # "high" | "medium" | "low"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RfpSectionResponse(Base):
    """Per-extracted-section response narrative.

    One row per (proposal_id, rfp_section_id, author_type, author_id).
    `author_type='parsons'` → author_id is NULL (there's only one Parsons team).
    `author_type='competitor'` → author_id is the competitors.id of the
    persona who "wrote" the predicted response. (This duplicates some
    information with CompetitorPrediction; the workbench uses THIS table
    for edited/refined competitor responses so the rest of the app's
    CompetitorPrediction flow is unaffected.)

    `compliance_tags_json` is a JSON dict mapping requirement_id →
    one of: Comply | Comply-with-exception | Take-exception |
    Not-Applicable | Needs-Clarification.
    """
    __tablename__ = "rfp_section_response"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), index=True)
    rfp_section_id = Column(String, index=True)  # extracted section id
    author_type = Column(String)  # "parsons" | "competitor"
    author_id = Column(Integer, ForeignKey("competitors.id"), nullable=True)
    content = Column(Text)  # markdown narrative
    compliance_tags_json = Column(Text, nullable=True)  # JSON { requirement_id: tag }
    status = Column(String, default="draft")  # draft | in_review | approved | exported
    version = Column(Integer, default=1)
    last_edited_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SectionCureSuggestion(Base):
    """AI-generated "cure the scoring gap" suggestion with approve/deny workflow.

    Produced by the Cure Advisor persona after a section has been scored
    against one or more competitors. Default output is text-only
    (`suggestion_text`); when the user clicks the optional "Rewrite"
    button a full rewritten draft is stored in `suggested_rewrite`.

    status flow:
      proposed → (approved | denied)
      approved → applied   (when the suggestion is merged into the
                            Parsons response; `applied_response_version`
                            records the RfpSectionResponse.version that
                            resulted, and `rescore_delta` records the
                            score improvement after rescoring).
    """
    __tablename__ = "section_cure_suggestion"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), index=True)
    rfp_section_id = Column(String, index=True)
    based_on_section_score_id = Column(Integer, ForeignKey("section_scores.id"), nullable=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"), nullable=True)
    suggestion_text = Column(Text)  # markdown — what to change and why
    suggested_rewrite = Column(Text, nullable=True)  # full rewritten section (optional)
    status = Column(String, default="proposed", index=True)  # proposed|approved|denied|applied
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    applied_response_version = Column(Integer, nullable=True)
    rescore_delta = Column(Integer, nullable=True)  # points gained after application
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Audit log (added 2026-04-28) ─────────────────────────────────────
# Append-only record of who-did-what-when. Writes are best-effort and
# never block the underlying action (see app/services/audit.py).
class AuditLogEntry(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # Denormalized actor identity (if the user is later renamed/deleted,
    # the audit record still tells you who it was at the time).
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    username = Column(String, nullable=True, index=True)
    # Short action verb, e.g. "login_success", "register_user", "update_user",
    # "admin_reset_password", "delete_proposal".
    action = Column(String, nullable=False, index=True)
    # The kind of thing affected, e.g. "user", "proposal", "document".
    target_type = Column(String, nullable=True, index=True)
    target_id = Column(Integer, nullable=True, index=True)
    # Network / correlation context.
    ip = Column(String, nullable=True)
    request_id = Column(String, nullable=True, index=True)
    # "ok" | "error" — quick filter on failures.
    status = Column(String, nullable=False, default="ok", index=True)
    # Free-form JSON-encoded extras (target name, before/after diff, etc.).
    payload = Column(Text, nullable=True)


# ── LLM usage / cost (added 2026-04-28) ──────────────────────────────
# One row per outbound LLM call (Anthropic / OpenAI). Best-effort writes
# from app/services/llm_usage.py — never raise on failure.
class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # Actor (denormalized so user renames / deletes don't lose history).
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    username = Column(String, nullable=True, index=True)
    # Correlation
    request_id = Column(String, nullable=True, index=True)
    endpoint = Column(String, nullable=True, index=True)  # /api/questions/ask, etc.
    # Provider details
    provider = Column(String, nullable=False, index=True)  # "anthropic" | "openai"
    model = Column(String, nullable=False, index=True)
    # Token accounting
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    # Cost estimate (USD). NULL only if pricing for the model is unknown.
    cost_estimate_usd = Column(Float, nullable=True)
    # Latency in ms (best-effort)
    latency_ms = Column(Integer, nullable=True)
    # "ok" | "error"
    status = Column(String, nullable=False, default="ok", index=True)
    error = Column(Text, nullable=True)


# ── Refresh tokens (added 2026-04-28) ────────────────────────────────
# Server-side store of issued refresh tokens, hashed at rest. Each row
# represents ONE refresh token. Rotation creates a new row and revokes
# the previous one (parent_id chain). Reusing a revoked token in the
# chain triggers chain-wide revocation (theft detection).
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    # SHA-256 of the plaintext token. We never store the plaintext.
    token_hash = Column(String, nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    last_used_at = Column(DateTime, nullable=True)
    # Lineage: when this token was minted by rotating an older one,
    # parent_id points at that older row.
    parent_id = Column(Integer, ForeignKey("refresh_tokens.id"), nullable=True, index=True)
    client_ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)


# ── User invitations (added 2026-04-28) ──────────────────────────────
# One-time-use invitation tokens. Admin mints one for a user; the user
# redeems it by setting their own password. Replaces the manual
# admin-emails-temp-password handoff.
class UserInvitation(Base):
    __tablename__ = "user_invitations"

    id = Column(Integer, primary_key=True, index=True)
    # SHA-256 of the plaintext token. Plaintext never persisted.
    token_hash = Column(String, nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Denormalized for the email body & for forensics if the user is later renamed.
    email = Column(String, nullable=False)
    invited_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True, index=True)
    consumed_ip = Column(String, nullable=True)


# ── Flashcards (added 2026-04-28) ────────────────────────────────────
# RFP comprehension-testing flashcards. One row per question/answer
# pair, derived from a single rfp_requirements row. Scope limited to
# Section 4 (SOW) at generation time — no bid-mechanics cards.
# Spaced repetition via simplified SM-2 (Again / Good / Easy).
class Flashcard(Base):
    __tablename__ = "flashcards"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    requirement_id = Column(Integer, ForeignKey("rfp_requirements.id"), nullable=True, index=True)
    # Denormalized so we can filter cards without joining when reviewing.
    section_id = Column(String, nullable=True, index=True)  # e.g. "4.10.5"

    # Card content
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    # "recall" | "concept" | "numeric" | "cross_reference" — drives
    # filtering in the UI but is otherwise informational.
    card_type = Column(String, default="recall", index=True)
    # Difficulty hint from the generator: "easy" | "medium" | "hard".
    difficulty = Column(String, default="medium")

    # Source citation — lets the user click through to the verbatim RFP excerpt.
    source_page = Column(Integer, nullable=True)
    source_text = Column(Text, nullable=True)

    # Spaced-repetition state (SM-2 simplified). All NULL/0 until the
    # first review.
    review_count = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    last_reviewed_at = Column(DateTime, nullable=True, index=True)
    next_review_at = Column(DateTime, nullable=True, index=True)
    ease_factor = Column(Float, default=2.5)         # SM-2 ease, starts at 2.5
    interval_days = Column(Integer, default=0)        # current interval
    last_rating = Column(String, nullable=True)       # "again" | "good" | "easy"

    # Provenance / housekeeping
    generated_by_model = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Wait-Time A/B Test (LD comparison) ───────────────────────────────
#
# Imports an Excel file of station-by-day wait-time observations
# (STATION_ID, STATION_NAME, TEST_DATE, DAY_OF_MONTH, METRIC_NAME, H06..H19)
# and runs side-by-side liquidated-damage calculations under two
# different LD rules (the OLD contract rule and the NEW T1628 rule)
# so the user can quantify the cost differential before bidding.
#
# The rule shape is intentionally generic — see WaitTimeLdRule below.
# That lets the user plug in real formulas via the UI without code
# changes, and lets us A/B-test alternate proposals against the same
# observation set without re-importing.

class WaitTimeImport(Base):
    """One uploaded Excel file = one import. The raw observations are
    persisted so re-running with different rules is a fast lookup."""
    __tablename__ = "wait_time_imports"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    name = Column(String, nullable=False)            # user-supplied label
    description = Column(Text, nullable=True)
    source_filename = Column(String, nullable=True)  # original .xlsx name
    metric_name_filter = Column(String, default="Facility Average Wait Time")
    row_count = Column(Integer, default=0)
    station_count = Column(Integer, default=0)
    date_min = Column(DateTime, nullable=True)
    date_max = Column(DateTime, nullable=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class WaitTimeObservation(Base):
    """One row per (station, date, metric). Stores the 14 hourly columns
    as a JSON array indexed 0..13 corresponding to H06..H19. NULL entries
    are stored as nulls — those are hours the station is closed and MUST
    be skipped by every LD rule."""
    __tablename__ = "wait_time_observations"

    id = Column(Integer, primary_key=True, index=True)
    import_id = Column(Integer, ForeignKey("wait_time_imports.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    station_id = Column(String, index=True)          # e.g. "CIF000001"
    station_name = Column(String, nullable=True)
    test_date = Column(DateTime, index=True)
    day_of_month = Column(Integer, nullable=True)
    metric_name = Column(String, index=True)
    # JSON array of 14 nullable floats: [H06, H07, ..., H19]
    hourly_values = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class WaitTimeLdRule(Base):
    """A pluggable LD rule. The user creates two rules (Old and New) and
    picks them in the comparison UI. Stored as a generic parameter set so
    the same engine can score any rule shape we've encountered.

    Rule semantics (engine in services/wait_time_ab.py applies these):

      * threshold_minutes   — wait-time minutes above which a breach occurs
      * mode                — one of:
            'per_minute_over'   — penalty = (wait - threshold) * dollars_per_unit
            'per_hour_over'     — penalty = dollars_per_unit (flat per breaching hour)
            'per_breach_event'  — same as per_hour_over (alias for clarity)
            'per_day_if_any'    — flat dollars_per_unit if ANY hour breaches
            'tiered'            — uses tiers_json (array of {min_over, max_over, dollars})
      * dollars_per_unit    — coefficient used by the chosen mode
      * daily_cap_usd       — optional daily ceiling per (station, day)
      * monthly_cap_usd     — optional monthly ceiling per station
      * grace_period_minutes — wait values <= threshold + grace are NOT a breach
      * exclude_hours_csv   — comma-separated H-labels to skip ('H06,H19')
      * count_null_hours_as_breach — bool; NEVER true unless user explicitly opts in
      * tiers_json          — JSON array for 'tiered' mode
      * notes               — free text
    """
    __tablename__ = "wait_time_ld_rules"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    is_baseline = Column(Boolean, default=False)     # marks the OLD rule for the proposal
    threshold_minutes = Column(Float, nullable=False)
    mode = Column(String, nullable=False)            # see semantics above
    dollars_per_unit = Column(Float, default=0.0)
    daily_cap_usd = Column(Float, nullable=True)
    monthly_cap_usd = Column(Float, nullable=True)
    grace_period_minutes = Column(Float, default=0.0)
    exclude_hours_csv = Column(String, nullable=True)
    count_null_hours_as_breach = Column(Boolean, default=False)
    tiers_json = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    # CSV of facility names or station IDs to skip on the comparison
    # (e.g. T1628 explicitly exempts Cape May, Millville, Salem, Washington).
    # Match is case-insensitive on STATION_NAME (substring) or exact uppercase
    # match on STATION_ID for tokens shaped like "CIF000001".
    excluded_facilities_csv = Column(String, nullable=True)
    # For nj_old_2011 mode: number of breaching days per calendar month
    # that are waived (default 4 = LDs apply on the 5th+ breaching day).
    monthly_grace_days = Column(Integer, default=0)
    # ── Monthly LD parameters (independent of mode) ────────────────────
    # When enabled, the engine computes a per-station-per-month average
    # from the open-hour readings and applies a flat "base" LD if avg
    # exceeds the threshold, plus optional 10-min-band increments above
    # a second threshold (e.g. T1628 O-32 + O-33).
    monthly_enabled = Column(Boolean, default=False)
    monthly_threshold_minutes = Column(Float, nullable=True)
    monthly_dollars = Column(Float, nullable=True)
    monthly_increment_threshold = Column(Float, nullable=True)
    monthly_increment_dollars = Column(Float, nullable=True)
    monthly_band_size_minutes = Column(Float, nullable=True)
    # Scheduled hours of operation per day. Used as the DENOMINATOR for the
    # monthly-average calculation per the 2011 amendment: "divide by the
    # number of facility hours … only scheduled hours of operation
    # considered." Default 9 = NJ MVC CIFs typical scheduled-hour
    # convention. Override per-rule if the contract specifies a different window.
    monthly_scheduled_hours_per_day = Column(Float, nullable=True, default=9.0)
    # Optional override for the count of scheduled operating days in a
    # given month. NULL = auto-derive (weekdays + 1, which reproduces
    # the state's March 2026 figure of 23 days).
    monthly_scheduled_operating_days_override = Column(Integer, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WaitTimeAbRun(Base):
    """One A/B comparison execution. Stores the import + rule pair the
    user chose plus a JSON payload of the per-station daily breakdown so
    the comparison page can render fast on revisit and we have an audit
    trail of every comparison the user ran."""
    __tablename__ = "wait_time_ab_runs"

    id = Column(Integer, primary_key=True, index=True)
    proposal_id = Column(Integer, ForeignKey("proposals.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    import_id = Column(Integer, ForeignKey("wait_time_imports.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    old_rule_id = Column(Integer, ForeignKey("wait_time_ld_rules.id"), nullable=False)
    new_rule_id = Column(Integer, ForeignKey("wait_time_ld_rules.id"), nullable=False)
    # Totals (cents-accurate doubles, persisted to spare the UI a recompute):
    old_total_usd = Column(Float, default=0.0)
    new_total_usd = Column(Float, default=0.0)
    delta_usd = Column(Float, default=0.0)         # new - old (positive = new costs MORE)
    breaches_old = Column(Integer, default=0)
    breaches_new = Column(Integer, default=0)
    # Full payload for the comparison page (per-station daily rows, monthly rollup).
    breakdown_json = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
