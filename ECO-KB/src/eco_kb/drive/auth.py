"""Autorización OAuth de usuario (una sola vez). Ejecutar en una máquina CON navegador:

    uv run python -m eco_kb.drive.auth

y copiar después secrets/google_token.json al servidor. El token se refresca solo.
IMPORTANTE: la pantalla de consentimiento debe estar en "In production"; en "Testing" el
refresh token caduca a los 7 días y el sync dejará de funcionar.
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from eco_kb.drive.client import SCOPES
from eco_kb.settings import get_settings


def main() -> None:
    s = get_settings()
    flow = InstalledAppFlow.from_client_secrets_file(s.google_oauth_client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    out = Path(s.google_oauth_token_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(creds.to_json(), encoding="utf-8")
    print(f"Token guardado en {out}")


if __name__ == "__main__":
    main()
