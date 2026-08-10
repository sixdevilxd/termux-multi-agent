"""Local multi-chain wallet create/import. Secrets never go to chat."""
from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

from config.settings import settings

ROOT = settings.storage_dir / "wallets"
INDEX = ROOT / "index.json"


def _ensure() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ROOT, 0o700)
    except OSError:
        pass


def _load() -> dict:
    _ensure()
    if not INDEX.exists():
        return {"wallets": []}
    return json.loads(INDEX.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    _ensure()
    INDEX.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(INDEX, 0o600)
    except OSError:
        pass


def _write(name: str, chain: str, payload: dict) -> Path:
    _ensure()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:48]
    path = ROOT / f"{safe}_{chain}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def list_wallets() -> str:
    data = _load()
    wallets = data.get("wallets") or []
    if not wallets:
        return "Belum ada wallet. Bilang: *buat wallet solana namanya main*"
    lines = ["*Wallet tersimpan*", ""]
    for w in wallets:
        lines.append(
            f"• *{w.get('name')}* ({w.get('chain')}) — `{w.get('address') or 'see secret file'}`"
        )
        lines.append(f"  file: `{w.get('path')}`")
    lines += ["", "Private key *tidak* ditampilkan di chat."]
    return "\n".join(lines)


def create_wallet(chain: str, name: str) -> str:
    chain_l = (chain or "").lower().strip()
    name = (name or "wallet").strip()[:48] or "wallet"
    if chain_l in {"eth", "evm"}:
        chain_l = "ethereum"
    if chain_l in {"sol"}:
        chain_l = "solana"

    address = None
    private_key = None
    mnemonic = None
    note = ""

    if chain_l in {"ethereum", "base", "bsc"}:
        try:
            from eth_account import Account  # type: ignore

            Account.enable_unaudited_hdwallet_features()
            acct, mnemonic = Account.create_with_mnemonic()
            address = acct.address
            private_key = acct.key.hex()
            note = "EVM key — bisa dipakai di ETH/Base/BSC jika di-import di sana."
        except Exception:
            private_key = "0x" + secrets.token_hex(32)
            note = "eth-account tidak terpasang; disimpan raw key. Install eth-account untuk address."
    elif chain_l == "solana":
        try:
            from solders.keypair import Keypair  # type: ignore

            kp = Keypair()
            address = str(kp.pubkey())
            private_key = bytes(kp).hex()
            note = "Solana keypair secret (hex)."
        except Exception:
            private_key = secrets.token_hex(32)
            note = "solders tidak terpasang; seed 32-byte hex saja. Install solders untuk pubkey."
    else:
        return "Chain didukung: solana, ethereum, base, bsc."

    payload = {
        "chain": chain_l,
        "name": name,
        "address": address,
        "private_key": private_key,
        "mnemonic": mnemonic,
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _write(name, chain_l, payload)
    idx = _load()
    idx["wallets"].append(
        {
            "name": name,
            "chain": chain_l,
            "address": address,
            "path": str(path),
            "created_at": payload["created_at"],
        }
    )
    _save(idx)
    return (
        f"*Wallet dibuat*\n"
        f"• Nama: *{name}*\n"
        f"• Chain: *{chain_l}*\n"
        f"• Address: `{address or '(lihat file rahasia)'}`\n"
        f"• Secret file: `{path}` (permission 600)\n\n"
        f"Private key / seed *tidak* dikirim ke chat. Backup offline.\n"
        f"_{note}_"
    )


def import_wallet(chain: str, name: str, secret: str) -> str:
    chain_l = (chain or "").lower().strip()
    name = (name or "imported").strip()[:48] or "imported"
    secret = (secret or "").strip()
    if not secret:
        return "Secret kosong. Format: `import wallet solana namanya X | <privatekey>`"
    if chain_l in {"eth", "evm"}:
        chain_l = "ethereum"
    if chain_l in {"sol"}:
        chain_l = "solana"

    address = None
    private_key = secret
    mnemonic = secret if " " in secret else None
    note = "Imported by user."

    if chain_l in {"ethereum", "base", "bsc"}:
        try:
            from eth_account import Account  # type: ignore

            if " " in secret:
                Account.enable_unaudited_hdwallet_features()
                acct = Account.from_mnemonic(secret)
            else:
                acct = Account.from_key(secret)
            address = acct.address
            private_key = acct.key.hex()
        except Exception as exc:  # noqa: BLE001
            note = f"Stored raw; derive address failed: {exc}"
    elif chain_l == "solana":
        try:
            from solders.keypair import Keypair  # type: ignore

            if secret.startswith("["):
                kp = Keypair.from_bytes(bytes(json.loads(secret)))
            else:
                try:
                    kp = Keypair.from_base58_string(secret)
                except Exception:
                    kp = Keypair.from_bytes(bytes.fromhex(secret))
            address = str(kp.pubkey())
        except Exception:
            note = "Stored raw Solana secret; verify address locally if needed."
    else:
        return "Chain didukung: solana, ethereum, base, bsc."

    payload = {
        "chain": chain_l,
        "name": name,
        "address": address,
        "private_key": private_key,
        "mnemonic": mnemonic,
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _write(name, chain_l, payload)
    idx = _load()
    idx["wallets"].append(
        {
            "name": name,
            "chain": chain_l,
            "address": address,
            "path": str(path),
            "created_at": payload["created_at"],
        }
    )
    _save(idx)
    return (
        f"*Wallet di-import*\n"
        f"• Nama: *{name}*\n"
        f"• Chain: *{chain_l}*\n"
        f"• Address: `{address or '(lihat file)'}`\n"
        f"• Secret file: `{path}`\n\n"
        f"Jangan share file ini. Hapus pesan yang berisi key dari chat history."
    )
