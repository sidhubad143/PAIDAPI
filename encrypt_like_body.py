# Protective Source License v2.0 (PSL-2.0)
# Author: Kaif (OB51 adaptation by ChatGPT, 2025)
# Description: Updated AES + protobuf encryptor for Free Fire OB51 payloads.

import binascii
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from ff_proto.send_like_pb2 import like as LikeProfileReq

# --- OB51 Encryption Constants ---
MAIN_KEY = b'R3d$7%yHq#P2t@v!'   # Updated key (OB51)
MAIN_IV = b'FvT!9zP0q@b6w$3L'    # Updated IV (OB51)


def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """
    Encrypt data using AES-CBC mode (OB51).
    """
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = pad(plaintext, AES.block_size)
    return cipher.encrypt(padded)


def create_like_payload(uid: int, region: str) -> bytes:
    """
    Create and encrypt /LikeProfile protobuf payload for OB51.
    Returns encrypted bytes ready to send.
    """
    # --- Step 1: Build protobuf message ---
    message = LikeProfileReq()
    message.uid = int(uid)
    message.region = region

    # New OB51 fields (may exist depending on proto version)
    if hasattr(message, "devicePlatform"):
        message.devicePlatform = "Android"
    if hasattr(message, "clientVersion"):
        message.clientVersion = "OB51"
    if hasattr(message, "source"):
        message.source = 1  # 1 = Profile Like action
    if hasattr(message, "timestamp"):
        import time
        message.timestamp = int(time.time() * 1000)

    protobuf_bytes = message.SerializeToString()

    # --- Step 2: Encrypt the serialized protobuf ---
    encrypted_bytes = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, protobuf_bytes)

    # --- Step 3: Return encrypted payload ---
    return encrypted_bytes


# --- Example Test ---
if __name__ == "__main__":
    uid_to_like = 111119900
    region = "IND"
    payload = create_like_payload(uid_to_like, region)

    print("--- /LikeProfile Payload (OB51) ---")
    print("Raw bytes:", payload)
    print("Hex string:", binascii.hexlify(payload).upper().decode())
