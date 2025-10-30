import httpx
import asyncio
import binascii
import json
import os
import time
from get_jwt import create_jwt
from encrypt_like_body import create_like_payload  # protobuf + AES encryptor
from guests_manager.count_guest import count

# Paths
guests_file = "guests_manager/guests_converted.json"
usage_dir = "usage_history"
usage_file = os.path.join(usage_dir, "guest_usage_by_target.json")
endpoint = "/GetPlayerPersonalShow"

# Ensure dirs
os.makedirs(usage_dir, exist_ok=True)

# Load usage file (per-target permanent mapping)
if os.path.exists(usage_file):
    with open(usage_file, "r") as f:
        usage_by_target = json.load(f)
else:
    usage_by_target = {}

# Helpers for per-target permanent skip
def ensure_target(target_uid: str):
    if target_uid not in usage_by_target:
        usage_by_target[target_uid] = {"used_guests": {}, "total_likes": 0}

def guest_used_for_target(target_uid: str, guest_uid: str) -> bool:
    ensure_target(target_uid)
    return guest_uid in usage_by_target[target_uid]["used_guests"]

def mark_used(target_uid: str, guest_uid: str, ts_ms: int):
    ensure_target(target_uid)
    usage_by_target[target_uid]["used_guests"][guest_uid] = ts_ms
    usage_by_target[target_uid]["total_likes"] = len(usage_by_target[target_uid]["used_guests"])

def save_usage():
    with open(usage_file, "w") as f:
        json.dump(usage_by_target, f, indent=2)

# Supported regions (for reference / validation)
SUPPORTED_REGIONS = [
    "IND", "BR", "SG", "RU", "ID", "TW", "US", "VN",
    "TH", "ME", "PK", "CIS", "SAC", "NA"
]

# Determine Base URL based on Server Input (mapping + safe fallback)
def get_base_url(server_name: str) -> str:
    if not server_name:
        return "https://client.ind.freefiremobile.com"

    s = server_name.strip().upper()
    mapping = {
        "IND": "https://client.ind.freefiremobile.com",
        "BR":  "https://client.br.freefiremobile.com",
        "SG":  "https://client.sg.freefiremobile.com",
        "RU":  "https://client.ru.freefiremobile.com",
        "ID":  "https://client.id.freefiremobile.com",
        "TW":  "https://client.tw.freefiremobile.com",
        "US":  "https://client.us.freefiremobile.com",
        "VN":  "https://client.vn.freefiremobile.com",
        "TH":  "https://client.th.freefiremobile.com",
        # Regions that are commonly routed to IND (merged/inactive)
        "PK":  "https://client.ind.freefiremobile.com",
        "ME":  "https://client.ind.freefiremobile.com",
        "CIS": "https://client.ind.freefiremobile.com",
        # SA/NA clusters to US endpoint
        "SAC": "https://client.us.freefiremobile.com",
        "NA":  "https://client.us.freefiremobile.com",
    }
    # Return mapped url or default to IND cluster (safer than unknown host)
    return mapping.get(s, "https://client.ind.freefiremobile.com")

# Async worker
async def like_with_guest(guest: dict, target_uid: str, BASE_URL: str, semaphore: asyncio.Semaphore) -> bool:
    guest_uid = str(guest.get("uid", ""))
    guest_pass = guest.get("password", "")
    now_ms = int(time.time() * 1000)

    if not guest_uid:
        print("[WARN] Guest entry missing uid, skipping...")
        return False

    if guest_used_for_target(target_uid, guest_uid):
        print(f"[{guest_uid}] Permanently used for target {target_uid}, skipping...")
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
            mark_used(target_uid, guest_uid, now_ms)
            return True

        except httpx.HTTPStatusError as err:
            body = err.response.text if err.response is not None else ""
            print(f"[{guest_uid}] HTTP error: {err}, Response: {body}")
        except httpx.RequestError as err:
            print(f"[{guest_uid}] Request exception: {err}")
        except Exception as e:
            print(f"[{guest_uid}] Unexpected error: {e}")

    return False

# Async main
async def main():
    uid_to_like = input("Enter UID to like: ").strip()
    server_name_in = input("Enter server name (e.g., IND, BR, US, SAC, NA, PK): ").strip().upper()
    from count_likes import GetAccountInformation

    # Validate server input (optional friendly warning)
    if server_name_in and server_name_in not in SUPPORTED_REGIONS:
        print(f"[WARN] Server '{server_name_in}' is not in supported list. Proceeding with best-effort fallback (IND).")

    BASE_URL = get_base_url(server_name_in)
    print(f"\nUsing base URL: {BASE_URL} (server code: {server_name_in or 'IND'})")

    print("\nFetching target account info...")
    try:
        info = await GetAccountInformation(uid_to_like, "0", server_name_in, endpoint)
        if info.get("error"):
            print(f"Error: {info.get('message', 'unknown')}")
            return
        else:
            print(json.dumps(info, indent=4))
            # Extract initial like count
            basic_info = info.get("basicInfo", {})
            current_likes = basic_info.get("liked", 0)
            print(f"\nCurrent like count = {current_likes}")
    except Exception as e:
        print(f"An error occurred while getting account information: {e}")
        return

    guest_count = count()
    print(f"\n{guest_count} guest accounts found in '{guests_file}'")
    print("\nFree Fire allows 100 guest accounts to like a single profile within 24 hours")

    requested_likes_in = input("How many likes you want to send? (recommended: 100/day): ").strip()
    try:
        requested_likes = int(requested_likes_in) if requested_likes_in else 100
    except ValueError:
        requested_likes = 100

    max_conc_in = input("How many like requests to send per second? (eg. 20): ").strip()
    try:
        MAX_CONCURRENT = int(max_conc_in) if max_conc_in else 20
    except ValueError:
        MAX_CONCURRENT = 20
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    ensure_target(uid_to_like)
    try:
        with open(guests_file, "r") as f:
            guests = json.load(f)
    except Exception as e:
        print(f"Could not open guests file '{guests_file}': {e}")
        return

    available_guests = [g for g in guests if not guest_used_for_target(uid_to_like, str(g.get("uid", "")))]

    if not available_guests:
        print(f"No available guests left for target {uid_to_like} under permanent-skip policy.")
        save_usage()
        return

    likes_planned = min(max(0, requested_likes), len(available_guests))
    print(f"Planning to send {likes_planned} likes to {uid_to_like} using unused guests for this target.")

    tasks = []
    for g in available_guests[:likes_planned]:
        tasks.append(like_with_guest(g, uid_to_like, BASE_URL, semaphore))

    results = await asyncio.gather(*tasks)
    save_usage()

    success = sum(1 for r in results if r)
    print(f"\nCompleted. Success: {success}/{likes_planned}. Total used guests for {uid_to_like}: {usage_by_target[uid_to_like]['total_likes']}")

    # Fetch again after likes sent
    print("\nRe-fetching account info to verify new like count...")
    try:
        info_after = await GetAccountInformation(uid_to_like, "0", server_name_in, endpoint)
        basic_info_after = info_after.get("basicInfo", {})
        new_likes = basic_info_after.get("liked", 0)
        print(f"Like count now = {new_likes}")
        diff = new_likes - current_likes
        print(f"Likes increased by +{diff}")
    except Exception as e:
        print(f"Could not fetch updated like count: {e}")

# Entry
if __name__ == "__main__":
    asyncio.run(main())
