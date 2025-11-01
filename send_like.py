import httpx
import asyncio
import binascii
import json
import os
import time
from datetime import datetime, timedelta
from get_jwt import create_jwt
from encrypt_like_body import create_like_payload  # protobuf + AES encryptor
from count_likes import GetAccountInformation  # Now OB51 compatible
from guests_manager.count_guest import count  # Assuming this is your guest counter

# Paths
guests_file = "guests_manager/guests_converted.json"
usage_dir = "usage_history"
usage_file = os.path.join(usage_dir, "guest_usage_by_target.json")
endpoint = "/GetPlayerPersonalShow"

# Ensure dirs
os.makedirs(usage_dir, exist_ok=True)

# Load usage file (per-target permanent mapping)
def load_usage():
    if os.path.exists(usage_file):
        with open(usage_file, "r") as f:
            return json.load(f)
    return {}

usage_by_target = load_usage()

# Helpers for per-target skip (with 24h reset)
def ensure_target(target_uid: str):
    if target_uid not in usage_by_target:
        usage_by_target[target_uid] = {"used_guests": {}, "total_likes": 0}

def is_guest_expired(ts_ms: int) -> bool:
    """Check if guest usage >24h old"""
    now_ms = int(time.time() * 1000)
    return (now_ms - ts_ms) > (24 * 60 * 60 * 1000)  # 24h in ms

def guest_used_for_target(target_uid: str, guest_uid: str) -> bool:
    ensure_target(target_uid)
    used = usage_by_target[target_uid]["used_guests"].get(guest_uid)
    if used and is_guest_expired(used):
        # Auto-reset expired
        del usage_by_target[target_uid]["used_guests"][guest_uid]
        usage_by_target[target_uid]["total_likes"] -= 1
        print(f"[{guest_uid}] Usage expired (>24h), resetting for reuse.")
        return False
    return bool(used)

def mark_used(target_uid: str, guest_uid: str, ts_ms: int):
    ensure_target(target_uid)
    usage_by_target[target_uid]["used_guests"][guest_uid] = ts_ms
    usage_by_target[target_uid]["total_likes"] = len(usage_by_target[target_uid]["used_guests"])

def save_usage():
    with open(usage_file, "w") as f:
        json.dump(usage_by_target, f, indent=2)

def reset_target_usage(target_uid: str):
    """Reset all for specific target"""
    if target_uid in usage_by_target:
        del usage_by_target[target_uid]
        save_usage()
        print(f"Reset usage for target {target_uid} – all guests available now!")

# Determine Base URL based on Server Input (OB51 compatible)
def get_base_url(server_name: str) -> str:
    server_name = server_name.upper()
    if server_name == "IND":
        return "https://client.ind.freefiremobile.com"
    elif server_name in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com"
    else:
        return "https://clientbp.ggblueshark.com"

# Async worker
async def like_with_guest(guest: dict, target_uid: str, BASE_URL: str, semaphore: asyncio.Semaphore) -> bool:
    guest_uid = str(guest["uid"])
    guest_pass = guest["password"]
    now_ms = int(time.time() * 1000)

    if guest_used_for_target(target_uid, guest_uid):
        print(f"[{guest_uid}] Still in use for target {target_uid}, skipping...")
        return False

    async with semaphore:
        try:
            jwt, region, server_url_from_jwt = await create_jwt(int(guest_uid), guest_pass)
            payload = create_like_payload(int(target_uid), region)
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
                "ReleaseVersion": "OB51",
            }

            async with httpx.AsyncClient() as client:
                url = f"{BASE_URL}/LikeProfile"
                response = await client.post(url, data=payload, headers=headers, timeout=45)
                response.raise_for_status()
                
                # Debug: Response body
                resp_body = response.content
                print(f"[{guest_uid}] Response hex preview: {binascii.hexlify(resp_body[:100]).decode()[:200]}...")  # More bytes
                # If you have response proto, uncomment:
                # try:
                #     from ff_proto.send_like_pb2 import like as LikeRes
                #     like_res = LikeRes()
                #     like_res.ParseFromString(resp_body)
                #     print(f"Decoded response: {json_format.MessageToJson(like_res)}")
                # except:
                #     pass

            print(f"[{guest_uid}] Like sent to {target_uid}! Status: {response.status_code}")
            mark_used(target_uid, guest_uid, now_ms)
            return True

        except httpx.HTTPStatusError as err:
            body = err.response.text if err.response is not None else ""
            print(f"[{guest_uid}] HTTP error: {err}, Body: {body}")
        except Exception as e:
            print(f"[{guest_uid}] Error: {e}")

    return False

# Async main
async def main():
    uid_to_like = input("Enter UID to like: ").strip()
    server_name_in = input("Enter server name (e.g., IND, BR, US, SAC, NA): ").strip().upper()

    # NEW: Reset option
    reset_choice = input(f"Reset usage for {uid_to_like}? (y/n, to free up guests): ").strip().lower()
    if reset_choice == 'y':
        reset_target_usage(uid_to_like)

    print("\nFetching target account info...")
    try:
        info = await GetAccountInformation(uid_to_like, "0", server_name_in, endpoint)
        if info.get("error"):
            print(f"Error: {info['message']}")
            return
        basic_info = info.get("basicInfo", {})
        print("--- BASIC INFO BEFORE ---")
        print(json.dumps(basic_info, indent=4))
        current_likes = basic_info.get("liked", 0)
        print(f"\nCurrent like count = {current_likes}")
    except Exception as e:
        print(f"Error fetching info: {e}")
        return

    guest_count = count()
    print(f"\n{guest_count} guest accounts found in '{guests_file}'")

    requested_likes_in = input("How many likes to send? (recommended: 100/day): ").strip()
    requested_likes = int(requested_likes_in) if requested_likes_in else 100

    max_conc_in = input("How many like requests per second? (eg. 5 for small batch): ").strip()
    MAX_CONCURRENT = int(max_conc_in) if max_conc_in else 5  # Lower default for testing
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    BASE_URL = get_base_url(server_name_in)

    ensure_target(uid_to_like)
    with open(guests_file, "r") as f:
        guests = json.load(f)

    available_guests = [g for g in guests if not guest_used_for_target(uid_to_like, str(g["uid"]))]

    if not available_guests:
        print(f"No available guests for {uid_to_like}. Delete {usage_file} manually or reset above.")
        save_usage()
        return

    likes_planned = min(requested_likes, len(available_guests))
    print(f"Planning {likes_planned} likes to {uid_to_like} with available guests.")

    tasks = [like_with_guest(g, uid_to_like, BASE_URL, semaphore) for g in available_guests[:likes_planned]]
    results = await asyncio.gather(*tasks)
    save_usage()

    success = sum(1 for r in results if r)
    print(f"\nSuccess: {success}/{likes_planned}. Total used for {uid_to_like}: {usage_by_target[uid_to_like]['total_likes']}")

    # Wait longer for registration
    wait_sec = input("Wait time before re-fetch (default 300s/5min): ").strip()
    wait_time = int(wait_sec) if wait_sec else 300
    print(f"\nWaiting {wait_time}s for likes to register...")
    await asyncio.sleep(wait_time)

    # Re-fetch
    try:
        info_after = await GetAccountInformation(uid_to_like, "0", server_name_in, endpoint)
        basic_info_after = info_after.get("basicInfo", {})
        print("--- BASIC INFO AFTER ---")
        print(json.dumps(basic_info_after, indent=4))
        new_likes = basic_info_after.get("liked", 0)
        diff = new_likes - current_likes
        print(f"Like count now = {new_likes} (+{diff})")
        if diff == 0:
            print("⚠️ No increase. Check response hex above. Try new guests or longer wait.")
    except Exception as e:
        print(f"Re-fetch error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
