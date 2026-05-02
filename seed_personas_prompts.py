#!/usr/bin/env python3
"""
Seed the database with initial personas and prompt templates.
"""
import os
import sys
from app.database import SessionLocal
from app.models import Persona, PromptTemplate, PromptVersion
from datetime import datetime
import json

def seed_personas(db):
    """Create default personas."""
    personas_data = [
        {
            "name": "Technical Writer",
            "description": "Expert in writing clear, concise technical documentation for government proposals",
            "role": "technical_writer",
            "expertise_areas": ["technical_writing", "government_proposals", "compliance", "requirements"],
            "writing_style": {
                "clarity": "high",
                "conciseness": "high",
                "formality": "high",
                "structure": "logical",
                "terminology": "technical"
            },
            "tone": "professional",
            "audience": "technical"
        },
        {
            "name": "Compliance Officer",
            "description": "Specializes in ensuring proposals meet all regulatory and compliance requirements",
            "role": "compliance_officer",
            "expertise_areas": ["regulatory_compliance", "government_regulations", "audit_requirements", "legal_standards"],
            "writing_style": {
                "clarity": "high",
                "conciseness": "high",
                "formality": "very_high",
                "structure": "regulatory",
                "terminology": "legal"
            },
            "tone": "formal",
            "audience": "regulatory"
        },
        {
            "name": "Business Analyst",
            "description": "Focuses on business requirements, cost analysis, and value propositions",
            "role": "business_analyst",
            "expertise_areas": ["business_analysis", "cost_benefit", "requirements_gathering", "stakeholder_management"],
            "writing_style": {
                "clarity": "high",
                "conciseness": "medium",
                "formality": "high",
                "structure": "analytical",
                "terminology": "business"
            },
            "tone": "professional",
            "audience": "executive"
        },
        {
            "name": "Project Manager",
            "description": "Expert in project planning, scheduling, and resource management",
            "role": "project_manager",
            "expertise_areas": ["project_management", "scheduling", "resource_allocation", "risk_management"],
            "writing_style": {
                "clarity": "high",
                "conciseness": "high",
                "formality": "high",
                "structure": "organized",
                "terminology": "management"
            },
            "tone": "professional",
            "audience": "management"
        }
    ]

    personas = []
    for persona_data in personas_data:
        # Check if persona already exists
        existing = db.query(Persona).filter(Persona.name == persona_data["name"]).first()
        if existing:
            print(f"Persona '{persona_data['name']}' already exists, skipping...")
            personas.append(existing)
            continue

        persona = Persona(
            name=persona_data["name"],
            description=persona_data["description"],
            role=persona_data["role"],
            expertise_areas=json.dumps(persona_data["expertise_areas"]),
            writing_style=json.dumps(persona_data["writing_style"]),
            tone=persona_data["tone"],
            audience=persona_data["audience"],
            created_by=1,  # Assume admin user ID 1
            usage_count=0
        )
        db.add(persona)
        db.flush()  # Get the ID
        personas.append(persona)
        print(f"Created persona: {persona.name}")

    db.commit()
    return personas


def seed_prompt_templates(db, personas):
    """Create default prompt templates."""
    templates_data = [
        {
            "name": "Technical Proposal Refinement",
            "description": "Refines technical proposal sections for clarity and compliance",
            "template_type": "section_refinement",
            "template_content": """You are a technical writer specializing in government proposals. Your task is to refine the following proposal section to ensure it meets the highest standards of clarity, technical accuracy, and compliance.

Context:
- RFP Reference: {rfp_reference}
- Section: {section_name}
- Current Content: {current_content}
- Compliance Requirements: {compliance_requirements}

Instructions:
1. Improve technical clarity and precision
2. Ensure compliance with all stated requirements
3. Maintain professional tone appropriate for government proposals
4. Use industry-standard terminology
5. Structure the content logically with clear headings and subheadings
6. Remove any redundant or unnecessary information
7. Add any missing technical details that would strengthen the proposal

Refined Section:""",
            "variables": ["rfp_reference", "section_name", "current_content", "compliance_requirements"],
            "persona_name": "Technical Writer",
            "model_provider": "openai",
            "model_name": "gpt-4",
            "temperature": 0.3,
            "max_tokens": 2000
        },
        {
            "name": "Compliance Review",
            "description": "Reviews proposal content for regulatory compliance and requirements adherence",
            "template_type": "compliance_check",
            "template_content": """You are a compliance officer responsible for ensuring government proposals meet all regulatory requirements. Your task is to review the following proposal section and identify any compliance issues or missing requirements.

Context:
- RFP Reference: {rfp_reference}
- Section: {section_name}
- Content to Review: {content}
- Regulatory Framework: {regulatory_framework}
- Mandatory Requirements: {mandatory_requirements}

Instructions:
1. Check for compliance with all mandatory requirements
2. Identify any missing certifications, forms, or documentation
3. Verify adherence to regulatory standards and guidelines
4. Flag any potential compliance risks
5. Suggest specific improvements or additions needed
6. Provide detailed reasoning for any compliance concerns

Compliance Review Results:
[Provide a structured analysis with findings, recommendations, and risk assessments]""",
            "variables": ["rfp_reference", "section_name", "content", "regulatory_framework", "mandatory_requirements"],
            "persona_name": "Compliance Officer",
            "model_provider": "openai",
            "model_name": "gpt-4",
            "temperature": 0.1,
            "max_tokens": 1500
        },
        {
            "name": "Cost Analysis Enhancement",
            "description": "Enhances cost proposals with detailed analysis and justification",
            "template_type": "cost_analysis",
            "template_content": """You are a business analyst specializing in government contract cost proposals. Your task is to enhance the cost section of this proposal with detailed analysis and strong justification.

Context:
- RFP Reference: {rfp_reference}
- Proposed Costs: {proposed_costs}
- Project Scope: {project_scope}
- Market Conditions: {market_conditions}
- Competition Analysis: {competition_analysis}

Instructions:
1. Provide detailed cost breakdown and justification
2. Demonstrate cost realism and competitiveness
3. Include risk analysis and contingency planning
4. Show value for money and cost-benefit analysis
5. Address any cost-related concerns or questions
6. Ensure compliance with cost principles and regulations

Enhanced Cost Analysis:""",
            "variables": ["rfp_reference", "proposed_costs", "project_scope", "market_conditions", "competition_analysis"],
            "persona_name": "Business Analyst",
            "model_provider": "openai",
            "model_name": "gpt-4",
            "temperature": 0.4,
            "max_tokens": 1800
        },
        {
            "name": "Project Schedule Optimization",
            "description": "Creates optimized project schedules with realistic timelines and milestones",
            "template_type": "schedule_optimization",
            "template_content": """You are a project manager experienced in government contract execution. Your task is to create an optimized project schedule that demonstrates capability and realism.

Context:
- RFP Reference: {rfp_reference}
- Project Requirements: {project_requirements}
- Resource Availability: {resource_availability}
- Regulatory Deadlines: {regulatory_deadlines}
- Risk Factors: {risk_factors}

Instructions:
1. Create a realistic project schedule with clear milestones
2. Demonstrate understanding of project complexity and dependencies
3. Include appropriate contingency time for risks
4. Show compliance with regulatory deadlines and requirements
5. Provide detailed task breakdowns and resource allocation
6. Include quality assurance and testing phases

Optimized Project Schedule:""",
            "variables": ["rfp_reference", "project_requirements", "resource_availability", "regulatory_deadlines", "risk_factors"],
            "persona_name": "Project Manager",
            "model_provider": "openai",
            "model_name": "gpt-4",
            "temperature": 0.2,
            "max_tokens": 1600
        }
    ]

    # Create persona lookup
    persona_lookup = {p.name: p for p in personas}

    for template_data in templates_data:
        # Check if template already exists
        existing = db.query(PromptTemplate).filter(PromptTemplate.name == template_data["name"]).first()
        if existing:
            print(f"Template '{template_data['name']}' already exists, skipping...")
            continue

        # Get persona
        persona = persona_lookup.get(template_data["persona_name"])

        template = PromptTemplate(
            name=template_data["name"],
            description=template_data["description"],
            template_type=template_data["template_type"],
            template_content=template_data["template_content"],
            variables=json.dumps(template_data["variables"]),
            persona_id=persona.id if persona else None,
            model_provider=template_data["model_provider"],
            model_name=template_data["model_name"],
            temperature=template_data["temperature"],
            max_tokens=template_data["max_tokens"],
            created_by=1,  # Assume admin user ID 1
            usage_count=0,
            success_rate=0.0
        )

        db.add(template)
        db.flush()  # Get the ID

        # Create initial version
        version = PromptVersion(
            prompt_template_id=template.id,
            version_number=1,
            template_content=template.template_content,
            variables=template.variables,
            model_provider=template.model_provider,
            model_name=template.model_name,
            temperature=template.temperature,
            max_tokens=template.max_tokens,
            change_reason="Initial version",
            created_by=1
        )
        db.add(version)

        print(f"Created template: {template.name}")

    db.commit()


def main():
    """Main seeding function."""
    print("🌱 Seeding database with initial personas and prompt templates...")

    db = SessionLocal()
    try:
        # Seed personas first
        print("\n📝 Creating personas...")
        personas = seed_personas(db)

        # Seed prompt templates
        print("\n📋 Creating prompt templates...")
        seed_prompt_templates(db, personas)

        print("\n✅ Database seeding completed successfully!")

    except Exception as e:
        print(f"❌ Error during seeding: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()