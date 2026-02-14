import asyncio
import logging
import httpx
from celery import Celery
from asgiref.sync import async_to_sync

from config import get_settings
from github_utils import GithubAuthHelper
from agents import review_agent

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

# Initialize Celery
celery_app = Celery("nomcode_worker", broker=settings.REDIS_URL)

# Initialize Auth Helper (Worker needs its own instance)
auth_helper = GithubAuthHelper(settings.APP_ID, settings.PRIVATE_KEY_PATH)

async def _analyze_pr_async(installation_id: int, pr: dict):
    """
    The actual async logic for processing the PR.
    """
    try:
        logger.info(f"🔄 [Worker] Processing PR #{pr['number']}: {pr['title']}")

        # 1. Get Token
        token = await auth_helper.get_installation_token(installation_id)
        
        # 2. Fetch Diff (Async)
        async with httpx.AsyncClient() as client:
            diff_response = await client.get(
                pr["url"], 
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3.diff"}
            )
            diff_text = diff_response.text

        # 3. RUN THE AGENT (Async)
        logger.info(f"🧠 [Worker] Sending {len(diff_text)} chars to AI...")
        
        # Safety clip to avoid blowing context before we implement smart chunking
        result = await review_agent.run(f"Review this diff:\n\n{diff_text[:6000]}")
        review_data = result.output

        logger.info("✅ [Worker] Analysis Complete")
        
        # 4. Construct Comment
        comment_body = f"## 🤖 AI Code Review\n\n**Summary:** {review_data.summary}\n\n"
        
        inline_comments = []
        for issue in review_data.issues:
            comment_body += f"- **{issue.issue_type}** in `{issue.file_path}`: {issue.suggestion}\n"
            inline_comments.append({
                "path": issue.file_path,
                "line": issue.line_number,
                "body": f"**[{issue.issue_type}]** {issue.suggestion}"
            })

        # 5. Post Comment (Async)
        reviews_url = f"{pr['url']}/reviews"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                reviews_url,
                headers={"Authorization": f"token {token}"},
                json={
                    "body": comment_body,
                    "event": "COMMENT",
                    "comments": inline_comments
                }
            )
            resp.raise_for_status()
        
        logger.info(f"🚀 [Worker] Posted review to PR #{pr['number']}")

    except Exception as e:
        logger.error(f"❌ [Worker] Failed: {e}", exc_info=True)
        # In a real production app, you might want to re-raise this 
        # so Celery knows to retry the task.
        raise e

@celery_app.task(name="analyze_pr_task", acks_late=True)
def analyze_pr_task(installation_id: int, pr: dict):
    """
    The Celery Task wrapper.
    It bridges the Sync world of Celery with the Async world of our logic.
    """
    asyncio.run(_analyze_pr_async(installation_id, pr))