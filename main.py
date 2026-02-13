import os
import hmac
import hashlib
import time
import jwt  
import httpx  
from fastapi import FastAPI, Request, HTTPException
app = FastAPI()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "shiv1234")
APP_ID = os.getenv("APP_ID", "2856252")
PRIVATE_KEY_PATH = "github_private_key/nomcode-v1.2026-02-12.private-key.pem"

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

    # Handling PR Events
    if payload.get("action") in ["opened", "synchronize"]:
        pr = payload["pull_request"]
        installation_id = payload["installation"]["id"]
        
        print(f"🔄 Processing PR #{pr['number']}: {pr['title']}")

        # 1. Get Permission (Token)
        token = await auth_helper.get_installation_token(installation_id)
        
        # 2. Fetch the Diff using the Token
        async with httpx.AsyncClient() as client:
            diff_response = await client.get(
                pr["url"], # API URL for the PR
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github.v3.diff" # Important: Request the Diff format
                }
            )
            diff_text = diff_response.text

        # 3. Print the Diff (Proof of Life)
        print("--- DIFF START ---")
        print(diff_text[:500]) # Print first 500 chars to avoid clutter
        print("--- DIFF END ---")
        
        return {"status": "diff_fetched", "size": len(diff_text)}

    return {"status": "ignored"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3000)