# RFP Response Generator
A FastAPI web application for generating RFP responses using AI agents.

## Features
- Orchestrator for human interaction
- Competitive Research Agent
- Parsons SME (Subject Matter Expert)
- Conflict Detector
- Document Composer
- Document and image ingestion
- Presentation generation in PDF, HTML, Word, PPT

## Setup
1. Ensure Python 3.8+ is installed.
2. Install dependencies: `pip install -r requirements.txt`
3. Install Docker and Docker Compose.
4. Start the database: `docker-compose up -d`
5. Configure .env with your API keys and DB URL.
6. Run the app: `uvicorn app.main:app --reload`
7. Access at http://localhost:8000