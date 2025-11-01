# get_jwt.py
import httpx
import asyncio
import json
import base64
import time
from typing import Tuple, Union
from google.protobuf import json_format, message
from Crypto.Cipher import AES

# Protobuf imports (use your existing generated modules)
from ff_proto import freefire_pb2

# --- OB51 constants ---
MAIN_KEY = b'R3d$7%yHq#P2t@v!'   # OB51 AES key (16 bytes)
MAIN_IV = b'FvT!9zP0q@b6w$3L'    # OB51 AES IV (16 bytes)
RELEASEVERSION = "OB51"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)"

# Pre-seeded server accounts (used when create_jwt(region) is called)
ACCOUNTS = {
    'IND': "uid=4104125669&password=E5655A0D14EF812A908726152BDD38021BEF528801AA42B16CFA4ED67141C4CA",
    'SG': "uid=3158350464&password=70EA041FCF79190E3D0A8F3CA95CAAE1F39782696CE9D85C2CCD525E28D223FC",
    'BR': "uid=3158668455&password=44296D19343151B25DE68286BDC565904A0DA5A5CC5E96B7A7ADBE7C11E07933",
    # add any others you depend on...
}

# Helpers
def pad_pkcs7(data: bytes) -> bytes:
    pad_len = AES.block_size - (len(data) % AES.block_size)
    return data + bytes([pad_len]) * pad_len

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad_pkcs7(plaintext))

def json_to_proto_bytes(json_data: str, proto_message: message.Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

def decode_protobuf(encoded_data: bytes, message_type) -> message.Message:
    inst = message_type()
    inst.ParseFromString(encoded_data)
    return inst

async def getAccess_Token_guest(uid: str, password: str) -> Tuple[str, str]:
    """
    For guest accounts: call the ffmconnect oauth endpoint to get access_token + open_id
    """
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = f"uid={uid}&password={password}&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {
        'User-Agent': USERAGENT,
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/x-www-form-urlencoded"
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, data=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data.get("access_token", "0"), data.get("open_id", "0")

async def getAccess_Token_server_account(account_payload: str) -> Tuple[str, str]:
    """
    For pre-seeded server accounts (ACCOUNTS mapping): same endpoint, payload already constructed
    """
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    headers = {
        'User-Agent': USERAGENT,
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/x-www-form-urlencoded"
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, data=account_payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data.get("access_token", "0"), data.get("open_id", "0")

async def create_jwt(*args) -> Tuple[str, str, str]:
    """
    Unified create_jwt:
    - If called as create_jwt(uid, password): uses guest flow
    - If called as create_jwt(region): uses ACCOUNTS mapping for that region
    Returns: (token, lockRegion, serverUrl) — token is RAW (no 'Bearer ' prefix)
    """
    try:
        # Guest flow
        if len(args) == 2:
            uid, password = str(args[0]), str(args[1])
            access_token, open_id = await getAccess_Token_guest(uid, password)
            if access_token == "0":
                raise ValueError("Failed to get access_token for guest (invalid creds?)")
        # Region/server-account flow
        elif len(args) == 1:
            region = str(args[0]).upper()
            account_payload = ACCOUNTS.get(region)
            if not account_payload:
                raise ValueError(f"No server account configured for region '{region}'")
            access_token, open_id = await getAccess_Token_server_account(account_payload)
            if access_token == "0":
                raise ValueError("Failed to get access_token for server account")
        else:
            raise ValueError("create_jwt requires either (uid, password) or (region)")

        # Build LoginReq protobuf JSON
        json_data = json.dumps({
            "open_id": open_id,
            "open_id_type": "4",
            "login_token": access_token,
            "orign_platform_type": "4"
        })

        encoded_proto = json_to_proto_bytes(json_data, freefire_pb2.LoginReq())
        encrypted_payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, encoded_proto)

        url = "https://loginbp.ggblueshark.com/MajorLogin"
        headers = {
            'User-Agent': USERAGENT,
            'Connection': "Keep-Alive",
            'Accept-Encoding': "gzip",
            'Content-Type': "application/octet-stream",
            'Expect': "100-continue",
            'X-Unity-Version': "2018.4.11f1",
            'X-GA': "v1 1",
            'ReleaseVersion': RELEASEVERSION
        }

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, data=encrypted_payload, headers=headers)
            resp.raise_for_status()
            resp_content = resp.content

            # decode protobuf LoginRes
            login_res = decode_protobuf(resp_content, freefire_pb2.LoginRes)
            login_res_json = json.loads(json_format.MessageToJson(login_res))

            token = login_res_json.get("token", "")
            lock_region = login_res_json.get("lockRegion", "")
            server_url = login_res_json.get("serverUrl", "")

            if not token:
                raise ValueError(f"Login failed or token missing: {login_res_json}")

            # return raw token (no 'Bearer '), region locked and server URL
            return token, lock_region, server_url

    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"HTTP error while creating JWT: {e.response.status_code} {e.response.text}") from e
    except Exception as ex:
        raise

# Quick test runner (manual)
if __name__ == "__main__":
    import sys, asyncio
    async def t():
        if len(sys.argv) == 3:
            tok, region, server = await create_jwt(sys.argv[1], sys.argv[2])
            print("Token:", tok[:40] + "...")
            print("Region:", region)
            print("Server:", server)
        elif len(sys.argv) == 2:
            tok, region, server = await create_jwt(sys.argv[1])
            print("Token:", tok[:40] + "...")
            print("Region:", region)
            print("Server:", server)
        else:
            print("Usage: python get_jwt.py <uid> <password>  OR  python get_jwt.py <REGION>")

    asyncio.run(t())
