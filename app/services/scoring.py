from ..services import ai_agent

def score_response(response: str, requirements: str, competitor_score: float = 0) -> dict:
    prompt = f"Score this RFP response on a scale of 1-10 based on: completeness, relevance to requirements '{requirements}', competitiveness, and quality. Provide detailed feedback. Competitor score: {competitor_score}"
    score_text = ai_agent.run_agent(prompt)
    
    # Extract score (simple parsing)
    try:
        score = float(score_text.split()[0])
    except:
        score = 5.0
    
    return {
        "score": score,
        "feedback": score_text,
        "beats_competitor": score > competitor_score
    }