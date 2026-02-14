from pydantic_ai import Agent
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.providers.groq import GroqProvider

from config import get_settings
from schemas import PRReview

settings = get_settings()

model = GroqModel(
    'llama-3.3-70b-versatile',
    provider=GroqProvider(api_key=settings.GROQ_API_KEY)
)

review_agent = Agent(
    model,
    output_type=PRReview,
    system_prompt="You are a senior code reviewer. Analyze the git diff and find critical issues."
)