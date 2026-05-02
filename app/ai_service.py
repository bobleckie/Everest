"""
AI service for prompt execution and optimization.
"""
import os
import json
import time
from typing import Dict, Any, Optional, List
from datetime import datetime
import openai
import anthropic
from .database import SessionLocal
from .models import PromptTemplate, PromptExecution, Persona
from .logging_config import logger
from .exceptions import ExternalServiceError, ValidationError


class AIService:
    """Service for executing AI prompts with different providers and models."""

    def __init__(self):
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            logger.warning("OpenAI API key not found in environment variables")
        
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.anthropic_api_key:
            logger.warning("Anthropic API key not found in environment variables")

    async def execute_prompt(
        self,
        template_id: int,
        variables: Dict[str, Any],
        user_id: int,
        db_session
    ) -> Dict[str, Any]:
        """Execute a prompt template with given variables."""
        try:
            # Get template
            template = db_session.query(PromptTemplate).filter(
                PromptTemplate.id == template_id,
                PromptTemplate.is_active == True
            ).first()

            if not template:
                raise ValidationError("Prompt template not found or inactive")

            # Validate required variables
            required_vars = json.loads(template.variables) if template.variables else []
            missing_vars = []
            for var in required_vars:
                if var not in variables:
                    missing_vars.append(var)

            if missing_vars:
                raise ValidationError(f"Missing required variables: {', '.join(missing_vars)}")

            # Get persona if specified
            persona = None
            if template.persona_id:
                persona = db_session.query(Persona).filter(
                    Persona.id == template.persona_id,
                    Persona.is_active == True
                ).first()

            # Prepare the prompt content
            prompt_content = self._prepare_prompt_content(template.template_content, variables, persona)

            # Execute based on provider
            start_time = time.time()
            result = await self._execute_with_provider(
                template.model_provider,
                template.model_name,
                prompt_content,
                template.temperature,
                template.max_tokens
            )
            execution_time = int((time.time() - start_time) * 1000)  # milliseconds

            # Record execution
            execution = PromptExecution(
                prompt_template_id=template.id,
                persona_id=template.persona_id,
                user_id=user_id,
                input_variables=json.dumps(variables),
                output_content=result["content"],
                model_provider=template.model_provider,
                model_name=template.model_name,
                tokens_used=result.get("tokens_used", 0),
                execution_time_ms=execution_time,
                success=True
            )
            db_session.add(execution)

            # Update template statistics
            template.usage_count += 1
            # Simple success rate calculation (could be more sophisticated)
            if template.usage_count > 0:
                template.success_rate = ((template.success_rate * (template.usage_count - 1)) + 1) / template.usage_count

            # Update persona usage if applicable
            if persona:
                persona.usage_count += 1

            db_session.commit()

            logger.info(f"Prompt executed successfully: template {template_id}, user {user_id}, time {execution_time}ms")

            return {
                "execution_id": execution.id,
                "content": result["content"],
                "model_provider": template.model_provider,
                "model_name": template.model_name,
                "tokens_used": result.get("tokens_used", 0),
                "execution_time_ms": execution_time,
                "persona_used": persona.name if persona else None
            }

        except Exception as e:
            # Record failed execution
            try:
                execution = PromptExecution(
                    prompt_template_id=template_id if 'template' in locals() else None,
                    user_id=user_id,
                    input_variables=json.dumps(variables) if variables else None,
                    model_provider=template.model_provider if 'template' in locals() else None,
                    model_name=template.model_name if 'template' in locals() else None,
                    execution_time_ms=int((time.time() - start_time) * 1000) if 'start_time' in locals() else 0,
                    success=False,
                    error_message=str(e)
                )
                db_session.add(execution)
                db_session.commit()
            except Exception as log_error:
                logger.error(f"Failed to log execution error: {log_error}")

            logger.error(f"Prompt execution failed: {str(e)}")
            if isinstance(e, (ValidationError, ExternalServiceError)):
                raise
            raise ExternalServiceError("AI Service", str(e))

    def _prepare_prompt_content(
        self,
        template_content: str,
        variables: Dict[str, Any],
        persona: Optional[Persona] = None
    ) -> str:
        """Prepare the final prompt content with variables and persona context."""
        # Replace variables in template
        prompt = template_content
        for key, value in variables.items():
            placeholder = f"{{{key}}}"
            prompt = prompt.replace(placeholder, str(value))

        # Add persona context if available
        if persona:
            persona_context = f"""

You are role-playing as: {persona.name}
Description: {persona.description}
Expertise Areas: {', '.join(json.loads(persona.expertise_areas))}
Writing Style: {json.dumps(json.loads(persona.writing_style), indent=2)}
Tone: {persona.tone}
Audience: {persona.audience}

Please respond in character, maintaining the specified tone and writing style appropriate for the audience.
"""
            prompt = persona_context + "\n\n" + prompt

        return prompt

    async def _execute_with_provider(
        self,
        provider: str,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int
    ) -> Dict[str, Any]:
        """Execute prompt with the specified AI provider."""
        if provider.lower() == "openai":
            return await self._execute_openai(model, prompt, temperature, max_tokens)
        elif provider.lower() in ("anthropic", "claude"):
            return await self._execute_anthropic(model, prompt, temperature, max_tokens)
        else:
            raise ExternalServiceError("AI Service", f"Unsupported provider: {provider}")

    async def _execute_openai(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int
    ) -> Dict[str, Any]:
        """Execute prompt using OpenAI API."""
        try:
            if not self.openai_api_key:
                raise ExternalServiceError("OpenAI", "Agent Feature is not available at this time.")

            # Create client with SSL verification disabled for development
            import httpx
            async with httpx.AsyncClient(verify=False) as http_client:
                client = openai.AsyncOpenAI(
                    api_key=self.openai_api_key,
                    http_client=http_client
                )

                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are an expert assistant helping with government proposal development."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=60  # 60 second timeout
                )

                content = response.choices[0].message.content
                tokens_used = response.usage.total_tokens if response.usage else 0

                return {
                    "content": content,
                    "tokens_used": tokens_used,
                    "finish_reason": response.choices[0].finish_reason
                }

        except openai.APIError as e:
            logger.error(f"OpenAI API error: {str(e)}")
            raise ExternalServiceError("OpenAI", f"API error: {str(e)}")
        except openai.RateLimitError as e:
            logger.warning(f"OpenAI rate limit exceeded: {str(e)}")
            raise ExternalServiceError("OpenAI", "Rate limit exceeded. Please try again later.")
        except openai.AuthenticationError as e:
            logger.error(f"OpenAI authentication error: {str(e)}")
            raise ExternalServiceError("OpenAI", "Authentication failed. Check API key.")
        except Exception as e:
            logger.error(f"Unexpected OpenAI error: {str(e)}")
            raise ExternalServiceError("OpenAI", f"Unexpected error: {str(e)}")

    async def _execute_anthropic(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int
    ) -> Dict[str, Any]:
        """Execute prompt using Anthropic Claude API."""
        try:
            if not self.anthropic_api_key:
                raise ExternalServiceError("Anthropic", "Agent Feature is not available at this time.")

            # Create Anthropic client
            client = anthropic.Anthropic(api_key=self.anthropic_api_key)

            # Call Claude API (using synchronous call since we're in an async context)
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system="You are an expert assistant helping with government proposal development.",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature
            )

            content = response.content[0].text
            tokens_used = response.usage.output_tokens + response.usage.input_tokens

            return {
                "content": content,
                "tokens_used": tokens_used,
                "finish_reason": response.stop_reason
            }

        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {str(e)}")
            raise ExternalServiceError("Anthropic", f"API error: {str(e)}")
        except anthropic.RateLimitError as e:
            logger.warning(f"Anthropic rate limit exceeded: {str(e)}")
            raise ExternalServiceError("Anthropic", "Rate limit exceeded. Please try again later.")
        except anthropic.AuthenticationError as e:
            logger.error(f"Anthropic authentication error: {str(e)}")
            raise ExternalServiceError("Anthropic", "Authentication failed. Check API key.")
        except Exception as e:
            logger.error(f"Unexpected Anthropic error: {str(e)}")
            raise ExternalServiceError("Anthropic", f"Unexpected error: {str(e)}")

    async def optimize_prompt(
        self,
        template_id: int,
        test_variables: Dict[str, Any],
        optimization_criteria: List[str],
        user_id: int,
        db_session
    ) -> Dict[str, Any]:
        """Optimize a prompt template based on specified criteria."""
        try:
            # Get the original template
            template = db_session.query(PromptTemplate).filter(
                PromptTemplate.id == template_id
            ).first()

            if not template:
                raise ValidationError("Prompt template not found")

            # Execute the current prompt
            original_result = await self.execute_prompt(template_id, test_variables, user_id, db_session)

            # Generate optimization suggestions
            optimization_prompt = f"""
Analyze this prompt template and suggest optimizations based on the following criteria: {', '.join(optimization_criteria)}

Original Template:
{template.template_content}

Test Variables: {json.dumps(test_variables, indent=2)}

Original Output:
{original_result['content']}

Please provide:
1. Specific suggestions for improving the prompt template
2. Recommended changes to temperature, max_tokens, or model
3. An optimized version of the template
4. Expected improvements in output quality

Optimization Analysis:
"""

            # Use a meta-prompt to analyze and optimize
            # Use the same provider as the template for consistency
            optimization_provider = template.model_provider or "openai"
            optimization_model = "claude-3-5-haiku-20241022" if optimization_provider.lower() in ("anthropic", "claude") else "gpt-4"
            
            meta_template = PromptTemplate(
                name="Prompt Optimizer",
                description="Optimizes prompt templates for better performance",
                template_type="meta_optimization",
                template_content=optimization_prompt,
                variables=json.dumps([]),
                model_provider=optimization_provider,
                model_name=optimization_model,
                temperature=0.3,
                max_tokens=1500,
                created_by=user_id
            )

            # Execute optimization analysis using the same provider
            optimization_result = await self._execute_with_provider(
                optimization_provider,
                optimization_model,
                optimization_prompt,
                0.3,
                1500
            )

            return {
                "original_output": original_result,
                "optimization_analysis": optimization_result["content"],
                "optimization_suggestions": self._parse_optimization_suggestions(optimization_result["content"])
            }

        except Exception as e:
            logger.error(f"Prompt optimization failed: {str(e)}")
            raise ExternalServiceError("Optimization Service", str(e))

    def _parse_optimization_suggestions(self, analysis: str) -> Dict[str, Any]:
        """Parse optimization suggestions from the analysis text."""
        # Simple parsing - could be enhanced with more sophisticated NLP
        suggestions = {
            "temperature_recommendation": None,
            "max_tokens_recommendation": None,
            "model_recommendation": None,
            "template_improvements": [],
            "key_insights": []
        }

        # Basic keyword extraction (could be improved)
        if "temperature" in analysis.lower():
            suggestions["temperature_recommendation"] = "Consider adjusting temperature based on analysis"
        if "max_tokens" in analysis.lower() or "tokens" in analysis.lower():
            suggestions["max_tokens_recommendation"] = "Review token limits based on analysis"
        if "model" in analysis.lower():
            suggestions["model_recommendation"] = "Consider model changes based on analysis"

        return suggestions


# Global AI service instance
ai_service = AIService()