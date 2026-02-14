import logging
from fastapi import FastAPI, Request, HTTPException

from config import get_settings
from github_utils import validate_signature
from tasks import analyze_pr_task  # Import the task signature

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI()

@app.post("/webhook")
async def handle_webhook(request: Request):
    payload_bytes = await request.body()
    
    # Validate - Security First
    try:
        validate_signature(payload_bytes, request.headers.get("X-Hub-Signature-256"), settings.WEBHOOK_SECRET)
    except HTTPException as e:
        logger.warning(f"⚠️ Auth failed: {e.detail}")
        raise e

    payload = await request.json()

    if payload.get("action") in ["opened", "synchronize"]:
        pr = payload["pull_request"]
        installation_id = payload["installation"]["id"]
        
        # DISPATCH TO QUEUE
        # .delay() is the magic method that serializes arguments 
        # and pushes them to Redis. It returns instantly.
        task = analyze_pr_task.delay(installation_id, pr)
        
        logger.info(f"📥 Webhook PR #{pr['number']} -> Queued as Task ID: {task.id}")
        return {"status": "queued", "task_id": task.id}

    return {"status": "ignored"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3000)