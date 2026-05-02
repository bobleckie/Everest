#!/usr/bin/env python3
"""
Test script for prompt execution workflow.
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from app.database import SessionLocal
from app.ai_service import ai_service
from app.models import PromptTemplate, User
import json


async def test_prompt_execution():
    """Test the prompt execution workflow."""
    print("🔧 Testing Prompt Execution Workflow")
    print("=" * 50)

    db = SessionLocal()

    try:
        # Get a test user (create one if needed)
        test_user = db.query(User).filter(User.email == "test@example.com").first()
        if not test_user:
            test_user = User(
                email="test@example.com",
                hashed_password="dummy_hash",
                role="proposal_manager",
                is_active=True
            )
            db.add(test_user)
            db.commit()
            db.refresh(test_user)

        # Get the first available prompt template
        template = db.query(PromptTemplate).filter(PromptTemplate.is_active == True).first()
        if not template:
            print("❌ No active prompt templates found. Run seed_personas_prompts.py first.")
            return

        print(f"📝 Using template: {template.name}")
        print(f"📋 Description: {template.description}")
        print(f"🤖 Model: {template.model_provider}/{template.model_name}")

        # Prepare test variables based on template requirements
        variables = {}
        required_vars = json.loads(template.variables) if template.variables else []
        placeholder_values = {
            "proposal_content": "This is a sample government proposal for infrastructure development.",
            "requirements": "Must comply with federal regulations, demonstrate technical expertise, and provide competitive pricing.",
            "compliance_areas": "FAR compliance, environmental regulations, safety standards",
            "cost_data": "$2.5M total project cost, $500K engineering, $1.2M construction, $800K management",
            "schedule_requirements": "24-month project duration, critical path items must be completed within 12 months",
            "rfp_reference": "NJ-MVC-2026-001",
            "section_name": "Technical Approach",
            "current_content": "Current draft content needing revision.",
            "compliance_requirements": "Ensure all responses map to applicable FAR clauses and security controls.",
            "proposal_section": "technical_proposal"
        }

        for required_var in required_vars:
            if required_var not in variables:
                variables[required_var] = placeholder_values.get(required_var, f"Sample value for {required_var}")

        print(f"🔄 Executing prompt with variables: {json.dumps(variables, indent=2)}")

        # Execute the prompt
        result = await ai_service.execute_prompt(
            template.id,
            variables,
            test_user.id,
            db
        )

        print("✅ Prompt execution successful!")
        print(f"⏱️  Execution time: {result['execution_time_ms']}ms")
        print(f"🔢 Tokens used: {result['tokens_used']}")
        print(f"🎭 Persona used: {result['persona_used']}")
        print("\n📄 Generated Content:")
        print("-" * 30)
        print(result['content'])
        print("-" * 30)

        # Verify execution was recorded
        from app.models import PromptExecution
        execution = db.query(PromptExecution).filter(
            PromptExecution.id == result['execution_id']
        ).first()

        if execution:
            print("✅ Execution recorded in database")
            print(f"📊 Success: {execution.success}")
            print(f"⏱️  Recorded time: {execution.execution_time_ms}ms")
        else:
            print("❌ Execution not found in database")

    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()

    finally:
        db.close()


if __name__ == "__main__":
    # Check for OpenAI API key
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY environment variable not set")
        print("Please set your OpenAI API key to run this test")
        sys.exit(1)

    asyncio.run(test_prompt_execution())