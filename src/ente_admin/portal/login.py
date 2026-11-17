"""Signing in to Ente as an admin, the way the apps do it with an email code:

1. POST /users/ott            museum emails a six-digit code
2. POST /users/verify-email   the code -> the account's key attributes and its new auth
                              token, sealed to the account's public key (or, with TOTP
                              2FA on, a two-factor session id first)
3. POST /users/two-factor/verify   (only with 2FA) the TOTP code -> the same as step 2
4. The password unlocks the token: argon2id(password, kekSalt) -> key-encryption key,
   which opens the master key, which opens the secret key, which opens the sealed token.

The password is used for step 4 and dropped; it is never stored or logged. The token
lives in memory (sessions.py) and is signed out with POST /users/logout. Passkey-only
accounts can't sign in here (passkeys need the accounts web app).

Museum creates an account when an unknown email verifies a code, so callers must only
send codes to emails of existing accounts (portal.py checks this).
"""
from __future__ import annotations

import base64
import threading

from ..sources.museum import MuseumClient, MuseumError

# One key derivation at a time: each can take hundreds of MiB (the account's memLimit)
_KDF_LOCK = threading.Lock()


class LoginError(RuntimeError):
    pass


def _b64(value: str) -> bytes:
    return base64.b64decode(value)


def send_code(client: MuseumClient, email: str) -> None:
    try:
        client.request("POST", "/users/ott", body={"email": email, "client": "web", "purpose": "login"})
    except MuseumError as e:
        raise LoginError(f"museum didn't send a code ({e})") from None


def verify_code(client: MuseumClient, email: str, code: str) -> dict:
    try:
        return client.request("POST", "/users/verify-email", body={"email": email, "ott": code})
    except MuseumError as e:
        raise LoginError("wrong or expired code" if e.status in (400, 401, 404, 410) else str(e)) from None


def verify_totp(client: MuseumClient, session_id: str, code: str) -> dict:
    try:
        return client.request("POST", "/users/two-factor/verify", body={"sessionID": session_id, "code": code})
    except MuseumError as e:
        raise LoginError("wrong two-factor code" if e.status in (400, 401, 404) else str(e)) from None


def stage(resp: dict) -> str:
    """What the verify response asks for next: "password", "totp" or "passkey"."""
    if resp.get("encryptedToken") and resp.get("keyAttributes"):
        return "password"
    if resp.get("twoFactorSessionID") or resp.get("twoFactorSessionIDV2"):
        return "totp"
    if resp.get("passkeySessionID"):
        return "passkey"
    raise LoginError("museum didn't return a token (has this account finished signing up?)")


def decrypt_token(resp: dict, password: str, max_mem_bytes: int) -> str:
    """The auth token from a verify response, opened with the account password."""
    from nacl import exceptions, public, pwhash, secret  # pynacl, a dependency of the portal

    ka = resp["keyAttributes"]
    mem, ops = int(ka["memLimit"]), int(ka["opsLimit"])
    if mem > max_mem_bytes:
        raise LoginError(f"this account's key derivation needs {mem >> 20} MiB, more than the "
                         f"{max_mem_bytes >> 20} MiB allowed (ENTE_ADMIN_KDF_MAX_MIB)")
    try:
        with _KDF_LOCK:
            kek = pwhash.argon2id.kdf(secret.SecretBox.KEY_SIZE, password.encode(), _b64(ka["kekSalt"]),
                                      opslimit=ops, memlimit=mem)
        try:
            master = secret.SecretBox(kek).decrypt(_b64(ka["encryptedKey"]), _b64(ka["keyDecryptionNonce"]))
        except exceptions.CryptoError:
            raise LoginError("wrong password") from None
        sk = secret.SecretBox(master).decrypt(_b64(ka["encryptedSecretKey"]), _b64(ka["secretKeyDecryptionNonce"]))
        raw = public.SealedBox(public.PrivateKey(sk)).decrypt(_b64(resp["encryptedToken"]))
    except (KeyError, ValueError, TypeError, exceptions.CryptoError) as e:
        raise LoginError(f"couldn't open the token ({type(e).__name__})") from None
    # museum hands out URL-safe base64 tokens and seals their decoded bytes
    return base64.urlsafe_b64encode(raw).decode()


def logout(client: MuseumClient, token: str) -> None:
    """Best effort: sign the token out of museum."""
    try:
        client.request("POST", "/users/logout", token=token)
    except MuseumError:
        pass
