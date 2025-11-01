# encrypt_like_body.py
import binascii
import time
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from ff_proto.send_like_pb2 import like as LikeProfileReq

# OB51 AES constants
MAIN_KEY = b'R3d$7%yHq#P2t@v!'   # 16 bytes
MAIN_IV = b'FvT!9zP0q@b6w$3L'    # 16 bytes

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = pad(plaintext, AES.block_size)
    return cipher.encrypt(padded)

def create_like_payload(uid: int, region: str) -> bytes:
    """
    Build and encrypt the protobuf payload for /LikeProfile (OB51).
    Returns raw bytes ready to send.
    """
    message = LikeProfileReq()
    # Basic fields (existing)
    message.uid = int(uid)
    message.region = region

    # OB51 additions (if present in your proto; using hasattr to be safe)
    if hasattr(message, "devicePlatform"):
        message.devicePlatform = "Android"
    if hasattr(message, "clientVersion"):
        message.clientVersion = "OB51"
    if hasattr(message, "source"):
        message.source = 1
    if hasattr(message, "timestamp"):
        message.timestamp = int(time.time() * 1000)
    if hasattr(message, "extra"):
        # If proto contains an 'extra' map or bytes, leave empty or set minimal metadata
        try:
            # some protos have a bytes field; keep it blank
            message.extra = b""
        except Exception:
            pass

    protobuf_bytes = message.SerializeToString()
    encrypted_bytes = aes_cbc_encrypt(MAIN_KEY, MAIN_IV, protobuf_bytes)
    return encrypted_bytes

# quick local test
if __name__ == "__main__":
    payload = create_like_payload(111119900, "IND")
    print("Hex:", binascii.hexlify(payload).decode())
