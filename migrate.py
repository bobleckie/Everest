#!/usr/bin/env python3
"""
Migration runner script for database migrations.
"""
import os
import sys
from alembic.config import Config
from alembic import command
from dotenv import load_dotenv

load_dotenv()

def run_migration():
    """Run database migration."""
    # Set up Alembic configuration
    alembic_cfg = Config("alembic.ini")

    # Set the database URL from environment
    database_url = os.getenv("DATABASE_URL", "sqlite:///./rfp.db")
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)

    print(f"Running migration on database: {database_url}")

    try:
        # Run the upgrade
        command.upgrade(alembic_cfg, "head")
        print("✅ Migration completed successfully!")
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_migration()