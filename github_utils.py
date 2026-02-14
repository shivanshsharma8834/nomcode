import time
import jwt
import httpx
import hmac
import hashlib
from fastapi import HTTPException

class GithubAuthHelper:
    def __init__(self, app_id: str, private_key_path: str):
        self.app_id = app_id
        try:
            with open(private_key_path, "r") as key_file:
                self.private_key = key_file.read()
        except FileNotFoundError:
            raise RuntimeError(f"Private key not found at: {private_key_path}")

    def generate_jwt(self) -> str:
        """Generates a JWT to authenticate as the App itself."""
        payload = {
            "iat": int(time.time()),
            "exp": int(time.time()) + (10 * 60),  # 10 minute expiration
            "iss": self.app_id
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")
    
    async def get_installation_token(self, installation_id: int) -> str:
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
            response.raise_for_status()
            return response.json()["token"]

def validate_signature(payload: bytes, signature_header: str, secret: str):
    """Validates the GitHub webhook signature to ensure request integrity."""
    if not signature_header:
        raise HTTPException(status_code=403, detail="Missing signature")
    
    if not secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    sha_name, signature = signature_header.split('=')
    mac = hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256)
    if not hmac.compare_digest(str(mac.hexdigest()), str(signature)):
        raise HTTPException(status_code=403, detail="Invalid signature")