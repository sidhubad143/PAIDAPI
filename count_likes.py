# count_likes.py
from ff_proto import freefire_pb2, core_pb2, account_show_pb2
import httpx
import asyncio
import json
from google.protobuf import json_format, message
from Crypto.Cipher import AES
from typing import Tuple
import sys

from get_jwt import create_jwt  # use unified create_jwt from get_jwt.py

# OB51 AES constants (must match get_jwt/encrypt)
MAIN_KEY = b'R3d$7%yHq#P2t@v!'
MAIN_IV = b'FvT!9zP0q@b6w$3L'
RELEASEVERSION = "OB51"
USERAGENT = "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 8 Build/UP1A.231005.007)"
SUPPORTED_REGIONS = ["IND", "BR", "SG", "RU", "ID", "TW", "US", "VN", "TH", "ME", "PK", "CIS"]

def pad_pkcs7(data: bytes) -> bytes:
    pad_len = AES.block_size - (len(data) % AES.block_size)
    return data + bytes([pad_len]) * pad_len

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    aes = AES.new(key, AES.MODE_CBC, iv)
    return aes.encrypt(pad_pkcs7(plaintext))

def decode_protobuf(encoded_data: bytes, message_type):
    inst = message_type()
    inst.ParseFromString(encoded_data)
    return inst

async def json_to_proto(json_data: str, proto_message: message.Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

async def GetAccountInformation(ID, UNKNOWN_ID, regionMain, endpoint="/GetPlayerPersonalShow"):
    regionMain = regionMain.upper()
    if regionMain not in SUPPORTED_REGIONS:
        return {
            "error": "Invalid request",
            "message": f"Unsupported 'region' parameter. Supported regions are: {', '.join(SUPPORTED_REGIONS)}."
        }

    # Build request proto
    json_data = json.dumps({"a": ID, "b": UNKNOWN_ID})
    core_proto_bytes = await json_to_proto(json_data, core_pb2.GetPlayerPersonalShow())

    # encrypt request using OB51 AES constants
    payload = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, core_proto_bytes)

    # Get token + server info using create_jwt(region)
    token, locked_region, serverUrl = await create_jwt(regionMain)
    if not token:
        return {"error": "JWT failed", "message": "Could not obtain JWT for region."}

    headers = {
        'User-Agent': USERAGENT,
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/octet-stream",
        'Expect': "100-continue",
        'Authorization': f"Bearer {token}",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': RELEASEVERSION
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(serverUrl + endpoint, data=payload, headers=headers)
        resp.raise_for_status()
        resp_content = resp.content

        # decode using expected response proto
        account_info = decode_protobuf(resp_content, account_show_pb2.AccountPersonalShowInfo)
        account_json = json.loads(json_format.MessageToJson(account_info))
        return account_json

# interactive runner (optional)
if __name__ == "__main__":
    async def main():
        print("Supported regions:", ", ".join(SUPPORTED_REGIONS))
        region = input("Region: ").upper()
        player = input("Player ID: ").strip()
        info = await GetAccountInformation(player, "0", region)
        print(json.dumps(info, indent=4))
    asyncio.run(main())
