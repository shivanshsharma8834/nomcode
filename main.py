import httpx
import logging
from fastapi import FastAPI, Request

from config import get_settings
from github_utils import GithubAuthHelper, validate_signature
from agents import review_agent

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI()
auth_helper = GithubAuthHelper(settings.APP_ID, settings.PRIVATE_KEY_PATH)

@app.post("/webhook")
async def handle_webhook(request: Request):
    payload_bytes = await request.body()
    validate_signature(payload_bytes, request.headers.get("X-Hub-Signature-256"), settings.WEBHOOK_SECRET)
    payload = await request.json()

    if payload.get("action") in ["opened", "synchronize"]:
        pr = payload["pull_request"]
        installation_id = payload["installation"]["id"]
        
        logger.info(f"🔄 Processing PR #{pr['number']}: {pr['title']}")

        # 1. Get Token
        token = await auth_helper.get_installation_token(installation_id)
        
        # 2. Fetch Diff
        async with httpx.AsyncClient() as client:
            diff_response = await client.get(
                pr["url"], 
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3.diff"}
            )
            diff_text = diff_response.text

        try:
            # 3. RUN THE AGENT
            # No need to craft a complex JSON prompt. The agent handles it.
            result = await review_agent.run(f"Review this diff:\n\n{diff_text[:6000]}")
            review_data = result.output  # This is now a real Python object, not a dict!

            logger.info("✅ Analysis Complete")
            
            # 4. USE THE DATA OBJECTS DIRECTLY
            comment_body = f"## 🤖 AI Code Review\n\n**Summary:** {review_data.summary}\n\n"
            
            # Prepare inline comments
            inline_comments = []
            for issue in review_data.issues:
                # Add to summary table
                comment_body += f"- **{issue.issue_type}** in `{issue.file_path}`: {issue.suggestion}\n"
                
                # Add structured inline comment
                inline_comments.append({
                    "path": issue.file_path,
                    "line": issue.line_number,
                    "body": f"**[{issue.issue_type}]** {issue.suggestion}"
                })

            
        
        except Exception as e:
            logger.error(f"❌ AI Error: {e}", exc_info=True)
            return {"status": "ai_failed"}

        # review_body = f"### AI Code Review Summary:\n{ai_response['summary']}\n\n### Issues Found:\n{ai_response['issues']}"

        # 4. Post Comment
        reviews_url = f"{pr['url']}/reviews"
        async with httpx.AsyncClient() as client:
            await client.post(
                reviews_url,
                headers={"Authorization": f"token {token}"},
                json={
                    "body": comment_body,
                    "event": "COMMENT",
                    "comments": inline_comments
                }
            )
        
        logger.info(f"🚀 Posted review to PR #{pr['number']}")
        return {"status": "success"}

    return {"status": "ignored"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3000)