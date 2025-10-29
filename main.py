from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import httpx
import asyncio
import binascii
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any
from pydantic import BaseModel
import uvicorn

async def create_jwt(uid: str, password: str) -> tuple:
    # Placeholder: Return dummy values
    return "dummy_jwt", "dummy_region", "dummy_server_url"

def create_like_payload(target_uid: str, region: str) -> bytes:
    # Placeholder: Return dummy payload
    return b"dummy_payload"

async def GetAccountInformation(uid: str, param2: str, server: str, endpoint: str) -> Dict[str, Any]:
    # Placeholder: Return dummy info
    return {"basicInfo": {"liked": 0}}

def count() -> int:
    # Placeholder: Return dummy count
    return 100  # Assume 100 guests

# FastAPI app
app = FastAPI(title="FF Like Sender API", version="1.0.0")

# API Key security (simple bearer token for demo; use proper secrets in prod)
security = HTTPBearer()

async def get_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    return credentials.credentials

# Paths
guests_file = "guests_manager/guests_converted.json"
usage_dir = "usage_history"
usage_file = os.path.join(usage_dir, "guest_usage_by_target.json")
endpoint = "/GetPlayerPersonalShow"

# Ensure dirs
os.makedirs(usage_dir, exist_ok=True)

# Load usage file (per-target with 24h expiration)
if os.path.exists(usage_file):
    with open(usage_file, "r") as f:
        usage_by_target = json.load(f)
else:
    usage_by_target = {}

# Helpers for per-target with 24h expiration
def ensure_target(target_uid: str):
    if target_uid not in usage_by_target:
        usage_by_target[target_uid] = {"used_guests": {}, "total_likes": 0}

def guest_used_for_target(target_uid: str, guest_uid: str) -> bool:
    ensure_target(target_uid)
    used_guests = usage_by_target[target_uid]["used_guests"]
    now_ts = time.time()
    # Clean expired (older than 24 hours)
    expired = {k: v for k, v in used_guests.items() if now_ts - v > 86400}  # 24h in seconds
    if expired:
        used_guests.update({k: v for k, v in used_guests.items() if k not in expired})
        usage_by_target[target_uid]["total_likes"] = len(used_guests)
        print(f"Cleaned {len(expired)} expired guests for {target_uid}")
    return guest_uid in used_guests

def mark_used(target_uid: str, guest_uid: str, ts: float):
    ensure_target(target_uid)
    usage_by_target[target_uid]["used_guests"][guest_uid] = ts
    usage_by_target[target_uid]["total_likes"] = len(usage_by_target[target_uid]["used_guests"])

def save_usage():
    with open(usage_file, "w") as f:
        json.dump(usage_by_target, f, indent=2)

# Determine Base URL based on Server Input
def get_base_url(server_name: str) -> str:
    if server_name == "IND":
        return "https://client.ind.freefiremobile.com"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com"
    else:
        raise ValueError(f"Unsupported server: {server_name}. Supported: IND, BR, US, SAC, NA")

# Pydantic models for request/response
class LikeResponse(BaseModel):
    success: int
    total_planned: int
    target_uid: str
    initial_likes: int
    final_likes: int
    increase: int
    message: str

# Async worker (same as original, but adapted)
async def like_with_guest(guest: dict, target_uid: str, BASE_URL: str, semaphore: asyncio.Semaphore) -> bool:
    guest_uid = str(guest["uid"])
    guest_pass = guest["password"]
    now_ms = int(time.time() * 1000)

    if guest_used_for_target(target_uid, guest_uid):
        print(f"[{guest_uid}] Used within 24h for target {target_uid}, skipping...")
        return False

    async with semaphore:
        try:
            jwt, region, server_url_from_jwt = await create_jwt(guest_uid, guest_pass)
            payload = create_like_payload(target_uid, region)
            if isinstance(payload, str):
                payload = binascii.unhexlify(payload)

            headers = {
                "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)",
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/octet-stream",
                "Expect": "100-continue",
                "Authorization": f"Bearer {jwt}",
                "X-Unity-Version": "2018.4.11f1",
                "X-GA": "v1 1",
                "ReleaseVersion": "OB50",
            }

            async with httpx.AsyncClient() as client:
                url = f"{BASE_URL}/LikeProfile"
                response = await client.post(url, data=payload, headers=headers, timeout=30)
                response.raise_for_status()

            print(f"[{guest_uid}] Like sent to {target_uid}! Status: {response.status_code}")
            mark_used(target_uid, guest_uid, time.time())
            return True

        except httpx.HTTPStatusError as err:
            body = err.response.text if err.response is not None else ""
            print(f"[{guest_uid}] HTTP error: {err}, Response: {body}")
        except httpx.RequestError as err:
            print(f"[{guest_uid}] Request exception: {err}")
        except Exception as e:
            print(f"[{guest_uid}] Unexpected error: {e}")

    return False

# Main API endpoint - Now using query parameters (call as GET /send-likes?uid=123&server=IND)
@app.get("/send-likes", response_model=LikeResponse)
async def send_likes(
    uid: str = Query(..., description="Target UID to send likes to"),
    server: str = Query(..., description="Server: IND, BR, US, SAC, NA"),
    num_likes: int = Query(100, description="Number of likes (max 100)"),
    concurrent: int = Query(20, description="Concurrent requests per second"),
    api_key: str = Query(None, description="API Key for authentication (required for access)")
):
    # API Key validation - now via query param for easy browser testing
    SECRET_API_KEY = "your_secret_api_key_here"  # Change this to your desired key
    if api_key != SECRET_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key. Provide ?api_key=your_secret_api_key_here")

    uid_to_like = uid.strip()
    server_name_in = server.strip().upper()
    requested_likes = min(100, max(1, num_likes))  # Enforce daily 100 max
    MAX_CONCURRENT = min(50, max(1, concurrent))  # Cap concurrent

    if not uid_to_like:
        raise HTTPException(status_code=400, detail="UID is required")

    # Validate server
    if server_name_in not in {"IND", "BR", "US", "SAC", "NA"}:
        raise HTTPException(status_code=400, detail="Invalid server. Supported: IND, BR, US, SAC, NA")

    BASE_URL = get_base_url(server_name_in)

    # Fetch initial info
    print("\nFetching target account info...")
    try:
        info = await GetAccountInformation(uid_to_like, "0", server_name_in, endpoint)
        if info.get("error"):
            raise HTTPException(status_code=400, detail=f"Error: {info['message']}")
        basic_info = info.get("basicInfo", {})
        current_likes = basic_info.get("liked", 0)
        print(f"Initial like count = {current_likes}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get account info: {e}")

    guest_count = count()
    print(f"{guest_count} guest accounts found")

    with open(guests_file, "r") as f:
        guests = json.load(f)

    available_guests = [g for g in guests if not guest_used_for_target(uid_to_like, str(g["uid"]))]

    if not available_guests:
        raise HTTPException(status_code=400, detail=f"No available guests for {uid_to_like} (wait 24h for reset)")

    likes_planned = min(requested_likes, len(available_guests))
    print(f"Planning to send {likes_planned} likes to {uid_to_like}")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    tasks = [like_with_guest(g, uid_to_like, BASE_URL, semaphore) for g in available_guests[:likes_planned]]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    save_usage()

    success = sum(1 for r in results if isinstance(r, bool) and r)
    print(f"Success: {success}/{likes_planned}")

    # Fetch final info
    try:
        info_after = await GetAccountInformation(uid_to_like, "0", server_name_in, endpoint)
        basic_info_after = info_after.get("basicInfo", {})  # Fixed: was info, now info_after
        new_likes = basic_info_after.get("liked", 0)
        diff = new_likes - current_likes
    except Exception as e:
        new_likes = current_likes  # Fallback
        diff = 0
        print(f"Could not fetch final count: {e}")

    return LikeResponse(
        success=success,
        total_planned=likes_planned,
        target_uid=uid_to_like,
        initial_likes=current_likes,
        final_likes=new_likes,
        increase=diff,
        message=f"Sent {success}/{likes_planned} likes. Likes increased by {diff}. Resets in 24h."
    )

# Health check
@app.get("/health")
def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
