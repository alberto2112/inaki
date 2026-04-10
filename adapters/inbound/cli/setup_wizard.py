"""Interactive setup wizard for Iñaki system configuration.

Handles first-run initialization and variable management for the .env file.
Currently manages: INAKI_SECRET_KEY.
Designed to be extensible: add new variables to _MANAGED_VARS.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv, set_key

_ENV_KEY_SECRET = "INAKI_SECRET_KEY"

_MANAGED_VARS = [
    {
        "key": _ENV_KEY_SECRET,
        "label": "Clave de cifrado (INAKI_SECRET_KEY)",
        "description": (
            "Clave Fernet de 32 bytes en base64 usada para cifrar credenciales sensibles.\n"
            "  Si no existe, el sistema la genera automáticamente al arrancar."
        ),
        "sensitive": True,
        "auto_generated": True,
    },
]

_SEP = "─" * 52


def _project_root() -> Path:
    # adapters/inbound/cli/setup_wizard.py → parents[3] = project root
    return Path(__file__).resolve().parents[3]


def _env_path() -> Path:
    return _project_root() / ".env"


def _load_env() -> None:
    load_dotenv(_env_path())


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-4:]


def _generate_fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _print_header() -> None:
    print(f"\n{'━' * 52}")
    print("  🔧 Iñaki — Modo configuración")
    print(f"{'━' * 52}\n")
    print(f"  Archivo: {_env_path()}\n")


def _print_status(var: dict) -> str:
    """Print current status for a variable. Returns current value or empty string."""
    key = var["key"]
    value = os.getenv(key, "")
    if value:
        display = _mask(value) if var["sensitive"] else value
        print(f"  [{key}]")
        print(f"    {var['description']}")
        print(f"    Estado: ✓ configurada  ({display})")
    else:
        print(f"  [{key}]")
        print(f"    {var['description']}")
        tag = "  se genera automáticamente al arrancar" if var.get("auto_generated") else "  ⚠ no configurada"
        print(f"    Estado: —{tag}")
    return value


def run_setup() -> None:
    """Run the interactive setup wizard."""
    _load_env()
    _print_header()

    for var in _MANAGED_VARS:
        current = _print_status(var)
        print()

        if current:
            ans = input(f"  ¿Regenerar {var['key']}? [s/N] > ").strip().lower()
            if ans not in ("s", "si", "sí", "y", "yes"):
                print(f"  → Mantenida.\n")
                continue

        if var.get("auto_generated"):
            ans = input(
                f"  ¿Generar {var['key']} automáticamente? [S/n] > "
            ).strip().lower()
            if ans in ("n", "no"):
                value = input(f"  Ingresá el valor manualmente: ").strip()
                if not value:
                    print("  → Omitida (valor vacío).\n")
                    continue
            else:
                value = _generate_fernet_key()
                print(f"  → Clave generada: {value}")
                print(f"     ⚠  Guardá esta clave. Sin ella no podrás descifrar tus datos.\n")
        else:
            value = input(f"  Ingresá el valor para {var['key']}: ").strip()
            if not value:
                print("  → Omitida (valor vacío).\n")
                continue

        set_key(str(_env_path()), var["key"], value)
        print(f"  → Guardada en {_env_path().name}.\n")

    print(_SEP)
    print("  Configuración completa.")
    print(f"  Archivo: {_env_path()}")
    print(f"{_SEP}\n")
