"""core.webauthn — login por biometria (digital/Face ID) via WebAuthn.

Sessão 1 da Biometria (03/07/2026). Helpers puros — as 4 rotas vivem no
main.py até a Fase 2 (routers). A senha permanece SEMPRE como fallback.

Fluxo:
  1. Cadastro (logado):  /auth/webauthn/registro/desafio → navigator.credentials.create()
                         → /auth/webauthn/registro/verificar (salva a chave pública)
  2. Login (deslogado):  /auth/webauthn/login/desafio → navigator.credentials.get()
                         → /auth/webauthn/login/verificar (emite o MESMO JWT do /auth/login)

Desafios são persistidos em public.webauthn_desafios (não em memória —
o Render roda com WEB_CONCURRENCY=2 e o desafio pode voltar em outro worker).
"""
import json
from webauthn import (
    generate_registration_options, verify_registration_response,
    generate_authentication_options, verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor, UserVerificationRequirement,
    AuthenticatorSelectionCriteria, ResidentKeyRequirement,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from .config import WEBAUTHN_RP_ID, WEBAUTHN_RP_NAME, WEBAUTHN_ORIGIN

# ── DDL (criado no startup do main.py via jard_query) ─────────────────
WEBAUTHN_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS public.credenciais_webauthn (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id UUID NOT NULL REFERENCES public.usuarios_garra(id),
    credential_id TEXT UNIQUE NOT NULL,
    public_key TEXT NOT NULL,
    sign_count BIGINT DEFAULT 0,
    transports TEXT DEFAULT '',
    apelido TEXT DEFAULT '',
    criado_em TIMESTAMP DEFAULT NOW(),
    ultimo_uso TIMESTAMP,
    ativo BOOLEAN DEFAULT true
);
CREATE TABLE IF NOT EXISTS public.webauthn_desafios (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    login TEXT NOT NULL,
    desafio TEXT NOT NULL,
    tipo TEXT NOT NULL,
    criado_em TIMESTAMP DEFAULT NOW()
);
"""


def gerar_opcoes_registro(usuario: dict, credenciais_existentes: list) -> tuple:
    """Opções para navigator.credentials.create().
    Retorna (options_json: str, desafio_b64url: str)."""
    options = generate_registration_options(
        rp_id=WEBAUTHN_RP_ID,
        rp_name=WEBAUTHN_RP_NAME,
        user_id=str(usuario["id"]).encode(),
        user_name=usuario["login"],
        user_display_name=usuario["nome"] or usuario["login"],
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
            resident_key=ResidentKeyRequirement.PREFERRED,
        ),
        # impede cadastrar duas vezes a mesma digital/aparelho
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["credential_id"]))
            for c in (credenciais_existentes or [])
        ],
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)


def verificar_registro(credencial_json: dict, desafio_b64url: str) -> dict:
    """Valida a resposta do create(). Retorna os campos a persistir.
    Levanta exceção da lib se a assinatura/origem/desafio não bater."""
    verificacao = verify_registration_response(
        credential=credencial_json,
        expected_challenge=base64url_to_bytes(desafio_b64url),
        expected_rp_id=WEBAUTHN_RP_ID,
        expected_origin=WEBAUTHN_ORIGIN,
    )
    return {
        "credential_id": bytes_to_base64url(verificacao.credential_id),
        "public_key":    bytes_to_base64url(verificacao.credential_public_key),
        "sign_count":    verificacao.sign_count,
    }


def gerar_opcoes_login(credenciais: list) -> tuple:
    """Opções para navigator.credentials.get().
    Retorna (options_json: str, desafio_b64url: str)."""
    options = generate_authentication_options(
        rp_id=WEBAUTHN_RP_ID,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["credential_id"]))
            for c in (credenciais or [])
        ],
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)


def verificar_login(credencial_json: dict, desafio_b64url: str,
                    public_key_b64url: str, sign_count_atual: int) -> int:
    """Valida a assinatura do get(). Retorna o novo sign_count.
    Levanta exceção da lib se inválida."""
    verificacao = verify_authentication_response(
        credential=credencial_json,
        expected_challenge=base64url_to_bytes(desafio_b64url),
        expected_rp_id=WEBAUTHN_RP_ID,
        expected_origin=WEBAUTHN_ORIGIN,
        credential_public_key=base64url_to_bytes(public_key_b64url),
        credential_current_sign_count=sign_count_atual,
        require_user_verification=True,
    )
    return verificacao.new_sign_count
