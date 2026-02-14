import os
import hmac
import hashlib
import time
import jwt  
import httpx  
from fastapi import FastAPI, Request, HTTPException
from litellm import completion
import json
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.providers.groq import GroqProvider

# from dotenv import load_dotenv

# load_dotenv()  # Load environment variables from .env file

class CodeIssue(BaseModel):
    file_path: str = Field(description="The full path to the file containing the issue")
    line_number: int = Field(description="The line number where the issue occurs")
    issue_type: str = Field(description="Type of issue: 'Bug', 'Security', 'Performance', or 'Style'")
    suggestion: str = Field(description="Actionable advice to fix the issue")

class PRReview(BaseModel):
    summary: str = Field(description="A concise summary of the changes")
    issues: list[CodeIssue]

model = GroqModel(
    'llama-3.3-70b-versatile',
    provider=GroqProvider(api_key=os.getenv("GROQ_API_KEY"))
)

review_agent = Agent(
    model,
    output_type=PRReview,  # <--- MAGIC: Enforces the schema
    system_prompt="You are a senior code reviewer. Analyze the git diff and find critical issues."
)

app = FastAPI()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "shiv1234")
APP_ID = os.getenv("APP_ID", "2856252")
PRIVATE_KEY_PATH = "github_private_key/nomcode-v1.2026-02-12.private-key.pem"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

class GithubAuthHelper:

    def __init__(self, app_id, private_key_path):
        self.app_id = app_id
        with open(private_key_path, "r") as key_file:
            self.private_key = key_file.read()

    def generate_jwt(self):
        """Generates a JWT to authenticate as the App itself."""
        payload = {
            "iat": int(time.time()),
            "exp": int(time.time()) + (10 * 60), # 10 minute expiration
            "iss": self.app_id
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")
    
    async def get_installation_token(self, installation_id):
        """Gets a token to act on behalf of a specific installation (user/org)."""
        jwt_token = self.generate_jwt()
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            return response.json()["token"]

auth_helper = GithubAuthHelper(APP_ID, PRIVATE_KEY_PATH)

def validate_signature(payload: bytes, signature_header: str):
    if not signature_header:
        raise HTTPException(status_code=403, detail="Missing signature")
    sha_name, signature = signature_header.split('=')
    mac = hmac.new(WEBHOOK_SECRET.encode(), msg=payload, digestmod=hashlib.sha256)
    if not hmac.compare_digest(str(mac.hexdigest()), str(signature)):
        raise HTTPException(status_code=403, detail="Invalid signature")

@app.post("/webhook")
async def handle_webhook(request: Request):
    payload_bytes = await request.body()
    validate_signature(payload_bytes, request.headers.get("X-Hub-Signature-256"))
    payload = await request.json()

    if payload.get("action") in ["opened", "synchronize"]:
        pr = payload["pull_request"]
        installation_id = payload["installation"]["id"]
        
        print(f"🔄 Processing PR #{pr['number']}: {pr['title']}")

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

            print("✅ Analysis Complete")
            
            # 4. USE THE DATA OBJECTS DIRECTLY
            comment_body = f"## 🤖 AI Code Review\n\n**Summary:** {review_data.summary}\n\n"
            
            # Prepare inline comments
            comments_payload = []
            for issue in review_data.issues:
                comment_body += f"- {issue.issue_type} in `{issue.file_path}`: {issue.suggestion}\n"
                
                comments_payload.append({
                    "path": issue.file_path,
                    "line": issue.line_number,
                    "body": f"**[{issue.issue_type}]** {issue.suggestion}"
                })# We build the inline comments list

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
            print(f"❌ AI Error: {e}")
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
        
        print(f"🚀 Posted review to PR #{pr['number']}")
        return {"status": "success"}

    return {"status": "ignored"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3000)