import asyncio
import logging
import httpx
from celery import Celery
from asgiref.sync import async_to_sync

from config import get_settings
from github_utils import GithubAuthHelper
from agents import review_agent
from schemas import CodeIssue

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

# Initialize Celery
# Note: Ensure Redis is running at settings.REDIS_URL
celery_app = Celery("nomcode_worker", broker=settings.REDIS_URL)

# Initialize Auth Helper (Worker needs its own instance)
auth_helper = GithubAuthHelper(settings.APP_ID, settings.PRIVATE_KEY_PATH)

async def _analyze_pr_async(installation_id: int, pr: dict):
    """
    The actual async logic for processing the PR.
    Now enhanced to fetch FULL file content and provide actionable Suggestions.
    """
    try:
        logger.info(f"🔄 [Worker] Processing PR #{pr['number']}: {pr['title']}")

        # Extract PR metadata
        owner = pr["base"]["repo"]["owner"]["login"]
        repo = pr["base"]["repo"]["name"]
        pull_number = pr["number"]
        head_sha = pr["head"]["sha"]  # We need the SHA to fetch the exact file version

        # 1. Get Installation Token
        token = await auth_helper.get_installation_token(installation_id)

        # 2. Get List of Changed Files
        files = await auth_helper.get_pr_files(installation_id, owner, repo, pull_number)
        
        all_issues = []
        files_analyzed = 0

        # 3. Iterate Through Each File
        for file_data in files:
            filename = file_data["filename"]
            status = file_data["status"]
            patch = file_data.get("patch", "") # The specific changes (diff)

            # --- FILTERING ---
            # Skip deleted files, images, or non-code files to save tokens
            if status == "removed" or not filename.endswith((".py", ".js", ".ts", ".tsx", ".go", ".java", ".cpp")):
                logger.info(f"⏭️ Skipping {filename} ({status})")
                continue

            logger.info(f"🔍 Analyzing {filename}...")

            # 4. Fetch FULL File Content
            try:
                content = await auth_helper.get_file_content(installation_id, owner, repo, filename, head_sha)
            except Exception as e:
                logger.error(f"❌ Failed to fetch content for {filename}: {e}")
                continue

            # 5. Construct Context-Aware Prompt
            # We provide the AI with both the FULL file (for context) and the DIFF (focus area)
            prompt = f"""
            You are a senior code reviewer. Review the changes in this file.
            
            METADATA:
            File: {filename}
            
            FULL FILE CONTENT:
            ```
            {content}
            ```
            
            SPECIFIC CHANGES (DIFF):
            ```
            {patch}
            ```
            
            INSTRUCTIONS:
            - Analyze the changes in the context of the full file.
            - Look for bugs, security risks, and style violations.
            - If you find an issue that can be fixed with a code change, provide the fixed code in the 'proposed_fix' field.
            - If the code is correct, return an empty list of issues.
            """

            # 6. Run the Agent
            try:
                # Run the AI
                result = await review_agent.run(prompt)
                
                # --- CRITICAL: Using result.output as requested ---
                review_data = result.output 
                
                # Add found issues to our master list
                if review_data.issues:
                    all_issues.extend(review_data.issues)
                    logger.info(f"Found {len(review_data.issues)} issues in {filename}")
                
                files_analyzed += 1
            
            except Exception as e:
                logger.error(f"⚠️ AI Analysis failed for {filename}: {e}")
                continue

        # 7. Post Final Review to GitHub
        if not all_issues:
            logger.info("✅ No issues found across all files.")
            return

        logger.info(f"📝 Posting review with {len(all_issues)} issues...")

        # Construct Summary Body
        comment_body = f"## 🤖 AI Code Review\n\n"
        comment_body += f"Analyzed {files_analyzed} files. Found {len(all_issues)} issues.\n\n"
        
        inline_comments = []
        for issue in all_issues:
            # Handle schema fields (Assuming you updated CodeIssue to have 'description' instead of 'suggestion')
            # If you kept 'suggestion', change issue.description to issue.suggestion below.
            desc = getattr(issue, 'description', getattr(issue, 'suggestion', 'Issue found'))
            
            # Add to Markdown Table in Summary
            comment_body += f"- **{issue.issue_type}** in `{issue.file_path}`: {desc}\n"
            
            # --- NEW: Build the Suggestion Block ---
            comment_content = f"**[{issue.issue_type}]** {desc}"
            
            # Check if the AI proposed a fix (and that it's not empty)
            if hasattr(issue, 'proposed_fix') and issue.proposed_fix:
                # Append the GitHub Suggestion Markdown syntax
                comment_content += f"\n\n```suggestion\n{issue.proposed_fix}\n```"

            inline_comments.append({
                "path": issue.file_path,
                "line": issue.line_number,
                "body": comment_content
            })

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
            
            if resp.status_code >= 400:
                logger.error(f"❌ GitHub API Error: {resp.text}")
            else:
                resp.raise_for_status()
        
        logger.info(f"🚀 [Worker] Successfully posted review to PR #{pr['number']}")

    except Exception as e:
        logger.error(f"❌ [Worker] Critical Failure: {e}", exc_info=True)
        raise e

@celery_app.task(name="analyze_pr_task", acks_late=True)
def analyze_pr_task(installation_id: int, pr: dict):
    """
    The Celery Task wrapper.
    It bridges the Sync world of Celery with the Async world of our logic.
    """
    asyncio.run(_analyze_pr_async(installation_id, pr))