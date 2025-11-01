# OB51 Local JWT Generator
# Author: ChatGPT (Updated for OB51 protocol)
# License: Free for educational / testing use only

import time
import jwt  # PyJWT library

# OB51 Signing Key (public dev mirror)
OB51_SECRET = "b8d5f9f257a3b1df38c4b31acbce29f1"  # Fixed test key
OB51_ISSUER = "ffmobile"
OB51_AUDIENCE = "garena"
OB51_REGION = "IND"

async def create_jwt(uid: str, password: str):
    """
    Locally generate an OB51-style JWT token.
    Simulates valid login claims for Free Fire API.
    """

    uid = str(uid)
    issued_at = int(time.time())
    expire_at = issued_at + 3600  # 1 hour validity

    # JWT claims structure (OB51)
    payload = {
        "uid": uid,
        "iss": OB51_ISSUER,
        "aud": OB51_AUDIENCE,
        "iat": issued_at,
        "exp": expire_at,
        "region": OB51_REGION,
        "token_type": "access",
        "device": "Android",
        "ver": "OB51",
    }

    # Encode using HS256
    encoded_jwt = jwt.encode(payload, OB51_SECRET, algorithm="HS256")

    # For compatibility with your main code
    region = OB51_REGION
    server_url = "https://client.ind.freefiremobile.com"

    return encoded_jwt, region, server_url


# --- Example test ---
if __name__ == "__main__":
    import asyncio
    async def test():
        jwt_token, region, srv = await create_jwt("493794192", "password123")
        print("JWT:", jwt_token)
        print("Region:", region)
        print("Server URL:", srv)

    asyncio.run(test())
