import httpx
import json
import time
import base64

# --- CONFIG ---
JWT_API_BASE = "https://jwt-api-kanha.vercel.app/token"
# Example API: https://jwt-api-kanha.vercel.app/token?uid={uid}&password={password}

# --- CORE FUNCTION ---

async def create_jwt(uid: str, password: str):
    """
    Generate JWT token for a Free Fire account (OB51 compatible).
    Returns: (jwt_token, region, server_url)
    """

    url = f"{JWT_API_BASE}?uid={uid}&password={password}"

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()
            # Example response:
            # {
            #   "region": "IND",
            #   "status": "live",
            #   "team": "@AuraXseller",
            #   "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            # }

            if "token" not in data:
                raise ValueError(f"Invalid response: {data}")

            jwt_token = data["token"]
            region = data.get("region", "IND")

            # Pick correct base URL for region
            if region == "IND":
                server_url = "https://client.ind.freefiremobile.com"
            elif region in {"BR", "US", "SAC", "NA"}:
                server_url = "https://client.us.freefiremobile.com"
            else:
                server_url = "https://clientbp.ggblueshark.com"

            print(f"[{uid}] ✅ JWT created successfully | Region: {region}")
            return jwt_token, region, server_url

    except httpx.HTTPStatusError as e:
        print(f"[{uid}] ❌ HTTP error: {e.response.status_code} | {e.response.text}")
    except httpx.RequestError as e:
        print(f"[{uid}] 🌐 Request error: {e}")
    except Exception as e:
        print(f"[{uid}] ⚠️ Unexpected error: {e}")

    return None, None, None


# --- TEST MODE ---
if __name__ == "__main__":
    import asyncio

    async def test():
        print("--- Free Fire JWT Generator (OB51) ---")
        uid = input("Enter UID: ").strip()
        password = input("Enter password: ").strip()

        print("\nGenerating JWT...")
        jwt_token, region, server_url = await create_jwt(uid, password)

        if jwt_token:
            print("\n✅ JWT Generated Successfully!")
            print(json.dumps({
                "uid": uid,
                "region": region,
                "server_url": server_url,
                "jwt": jwt_token[:50] + "..."  # partial print
            }, indent=4))
        else:
            print("\n❌ Failed to generate JWT.")

    asyncio.run(test())
