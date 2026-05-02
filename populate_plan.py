#!/usr/bin/env python3
"""
Script to populate the implementation plan in the database.
This stores the Parsons RFP Platform development plan for tracking progress.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, engine, Base
from app.models import ImplementationPhase, ImplementationTask
import json
from datetime import datetime, timedelta

def populate_implementation_plan():
    """Populate the database with the implementation plan phases and tasks."""

    # Create tables first
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()

    try:
        # Phase 1: Foundation (Weeks 1-3) - Security & Core Infrastructure
        phase1 = ImplementationPhase(
            name="Foundation",
            description="Security & Core Infrastructure - Critical foundation for production readiness",
            priority="critical",
            status="pending",
            estimated_weeks=3,
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(weeks=3)
        )
        db.add(phase1)
        db.flush()  # Get the ID

        phase1_tasks = [
            ImplementationTask(
                phase_id=phase1.id,
                title="Implement JWT Authentication",
                description="Add JWT-based authentication with role-based access control for Parsons users",
                priority="critical",
                estimated_hours=16,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase1.id,
                title="Migrate to PostgreSQL",
                description="Replace SQLite with PostgreSQL and implement proper database migrations",
                priority="critical",
                estimated_hours=12,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase1.id,
                title="Add Comprehensive Error Handling",
                description="Implement proper error handling, logging, and monitoring throughout the application",
                priority="high",
                estimated_hours=8,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase1.id,
                title="Create User Management System",
                description="Build user registration, login, and profile management for Parsons team members",
                priority="high",
                estimated_hours=10,
                dependencies=json.dumps([1])  # Depends on JWT auth
            )
        ]

        for task in phase1_tasks:
            db.add(task)

        # Phase 2: Prompt & Persona Management (Weeks 4-6) - Core Functionality
        phase2 = ImplementationPhase(
            name="Prompt & Persona Management",
            description="Core Functionality - Enable Parsons-specific AI workflows and prompt optimization",
            priority="high",
            status="pending",
            estimated_weeks=3,
            start_date=datetime.utcnow() + timedelta(weeks=3),
            end_date=datetime.utcnow() + timedelta(weeks=6)
        )
        db.add(phase2)
        db.flush()

        phase2_tasks = [
            ImplementationTask(
                phase_id=phase2.id,
                title="Build Persona Management UI",
                description="Create UI for creating, editing, and managing AI personas with configurable settings",
                priority="high",
                estimated_hours=20,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase2.id,
                title="Implement Prompt Optimization Workflow",
                description="Add ability to send prompts to Claude for optimization with approval/rejection controls",
                priority="high",
                estimated_hours=16,
                dependencies=json.dumps([5])  # Depends on Persona UI
            ),
            ImplementationTask(
                phase_id=phase2.id,
                title="Add Model Selection & Provider Abstraction",
                description="Implement flexible model selection per persona/task with support for multiple providers",
                priority="high",
                estimated_hours=14,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase2.id,
                title="Create Prompt Version History",
                description="Add version control and auditability for prompt changes and optimizations",
                priority="medium",
                estimated_hours=8,
                dependencies=json.dumps([6])  # Depends on optimization workflow
            )
        ]

        for task in phase2_tasks:
            db.add(task)

        # Phase 3: Dashboard Redesign & Insights (Weeks 7-9) - User Value
        phase3 = ImplementationPhase(
            name="Dashboard Redesign & Insights",
            description="User Value - Redesign dashboard with Parsons-specific KPIs and actionable insights",
            priority="high",
            status="pending",
            estimated_weeks=3,
            start_date=datetime.utcnow() + timedelta(weeks=6),
            end_date=datetime.utcnow() + timedelta(weeks=9)
        )
        db.add(phase3)
        db.flush()

        phase3_tasks = [
            ImplementationTask(
                phase_id=phase3.id,
                title="Redesign Dashboard Layout",
                description="Create Parsons-specific dashboard with proper information hierarchy and visual design",
                priority="high",
                estimated_hours=18,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase3.id,
                title="Implement Proposal Intelligence Metrics",
                description="Add bid health score, win probability, completion tracking, and competitor analysis",
                priority="high",
                estimated_hours=24,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase3.id,
                title="Create Actionable Insights Engine",
                description="Build system to generate recommendations for improving proposal quality and win rates",
                priority="high",
                estimated_hours=16,
                dependencies=json.dumps([11])  # Depends on metrics
            ),
            ImplementationTask(
                phase_id=phase3.id,
                title="Add Real-time Updates",
                description="Implement live updates for dashboard metrics and proposal status changes",
                priority="medium",
                estimated_hours=10,
                dependencies=json.dumps([10, 11])  # Depends on dashboard and metrics
            )
        ]

        for task in phase3_tasks:
            db.add(task)

        # Phase 4: Advanced Features & Production Polish (Weeks 10-12)
        phase4 = ImplementationPhase(
            name="Advanced Features & Production Polish",
            description="Production Polish - Add collaboration, testing, and deployment readiness",
            priority="medium",
            status="pending",
            estimated_weeks=3,
            start_date=datetime.utcnow() + timedelta(weeks=9),
            end_date=datetime.utcnow() + timedelta(weeks=12)
        )
        db.add(phase4)
        db.flush()

        phase4_tasks = [
            ImplementationTask(
                phase_id=phase4.id,
                title="Add Real-time Collaboration",
                description="Implement WebSocket connections for live editing and team collaboration features",
                priority="medium",
                estimated_hours=20,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase4.id,
                title="Comprehensive Testing Suite",
                description="Add unit tests, integration tests, and API documentation with 80%+ coverage",
                priority="high",
                estimated_hours=24,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase4.id,
                title="Production Deployment Setup",
                description="Create Docker containerization, CI/CD pipeline, and production environment configuration",
                priority="high",
                estimated_hours=16,
                dependencies=json.dumps([])
            ),
            ImplementationTask(
                phase_id=phase4.id,
                title="Performance Optimization",
                description="Add caching, query optimization, and background job processing for AI operations",
                priority="medium",
                estimated_hours=12,
                dependencies=json.dumps([])
            )
        ]

        for task in phase4_tasks:
            db.add(task)

        db.commit()
        print("✅ Implementation plan successfully stored in database")

        # Print summary
        phases = db.query(ImplementationPhase).all()
        total_tasks = db.query(ImplementationTask).count()

        print(f"\n📊 Plan Summary:")
        print(f"Total Phases: {len(phases)}")
        print(f"Total Tasks: {total_tasks}")

        for phase in phases:
            task_count = db.query(ImplementationTask).filter(ImplementationTask.phase_id == phase.id).count()
            print(f"• {phase.name}: {task_count} tasks ({phase.estimated_weeks} weeks)")

    except Exception as e:
        db.rollback()
        print(f"❌ Error populating implementation plan: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    populate_implementation_plan()