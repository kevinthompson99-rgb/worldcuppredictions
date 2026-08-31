"""One-off VAPID key pair generator for Web Push.

Run with `python scripts/generate_vapid_keys.py` and copy the printed values into
Railway's environment variables: VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_CLAIMS_EMAIL.
Keys are printed only - nothing is written to disk or committed to the repo.
"""

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from py_vapid.utils import b64urlencode


def main():
    vapid = Vapid()
    vapid.generate_keys()

    private_value = vapid.private_key.private_numbers().private_value
    private_raw = private_value.to_bytes(32, "big")
    public_raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

    print("VAPID_PUBLIC_KEY=" + b64urlencode(public_raw))
    print("VAPID_PRIVATE_KEY=" + b64urlencode(private_raw))
    print("VAPID_CLAIMS_EMAIL=mailto:you@example.com")


if __name__ == "__main__":
    main()
