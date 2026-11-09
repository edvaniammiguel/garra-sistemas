"""core.models — modelos Pydantic das requisições."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

from pydantic import BaseModel
from typing import Optional, List

# ── PYDANTIC MODELS ───────────────────────────────────────────
class LoginRequest(BaseModel):
    login: str
    senha: str

class UsuarioCreate(BaseModel):
    login: str; nome: str; email: str; senha: str
    perfil: str; perfil_checklist: Optional[str] = None

class UsuarioEdit(BaseModel):
    nome: Optional[str] = None; email: Optional[str] = None
    perfil: Optional[str] = None; perfil_checklist: Optional[str] = None
    ativo: Optional[bool] = None
    senha: Optional[str] = None  # tratada à parte: vira senha_hash com bcrypt

class SenhaChange(BaseModel):
    senha_atual: str; senha_nova: str

class SenhaResetRequest(BaseModel):
    login: str

class SenhaResetConfirm(BaseModel):
    token: str; senha_nova: str

class EnvioCreate(BaseModel):
    envio_id: str; usuario_login: str; usuario_nome: str
    cl_id: str; cl_label: Optional[str] = ""
    meta: dict = {}; respostas: dict = {}
    pts: int = 0; tem_nc: bool = False; total_nc: int = 0
    enviado_em: Optional[str] = None

class FrotaItem(BaseModel):
    categoria: str; identificacao: str; descricao: Optional[str] = ""

class ChecklistModeloCreate(BaseModel):
    cl_id: str; label: str; icon: str = "📋"
    descricao: Optional[str] = ""; vehicle_cat: Optional[str] = ""
    is_default: bool = False; score_full: int = 100
    score_nc: int = 60; score_obs: int = 20; score_ontime: int = 10
    questions: List[dict] = []; steps: List[dict] = []

class LogMotoristaCreate(BaseModel):
    motor_id: str; nome: str; cpf: Optional[str] = ""
    cnh: Optional[str] = ""; telefone: Optional[str] = ""
    status: str = "ativo"; observacoes: Optional[str] = ""

class LogVeiculoCreate(BaseModel):
    veiculo_id: str; car_id: str; placa: Optional[str] = ""
    modelo: Optional[str] = ""; ano: Optional[int] = None
    cor: Optional[str] = ""; status: str = "disponivel"
    extras: List[dict] = []; observacoes: Optional[str] = ""
    # (09/07/2026) Trava otimista: versão que o cliente viu (atualizado_em)
    visto_em: Optional[str] = None

class LogRegistroCreate(BaseModel):
    registro_id: str; responsavel: str
    data_hora: str; carros: List[dict] = []


# Perfis customizados (movidos do main na Fase 2 · Etapa 4)
class PerfilCreate(BaseModel):
    nome: str
    label: str
    modulos: List[str] = []

class PerfilUpdate(BaseModel):
    modulos: List[str]
    label: Optional[str] = None


# Mural de Avisos e Cartilha (movidos do main na Fase 2 · Etapa 5)
class MuralCreate(BaseModel):
    titulo: str
    mensagem: str
    perfis: str = ""  # "" = todos, ou "operador,campo,motorista"
    destinatario: str = ""  # "" = usa perfis, ou "gilson@garra.local" = só ele
    # (09/11/2026) card com imagem, confirmação "Li e entendi" e validade
    imagem_path: str = ""          # path no Storage (garra-fotos:mural/…) devolvido por POST /api/mural/imagem
    exige_confirmacao: bool = True # mostra o botão ✔ Li e entendi e conta quem confirmou
    valido_ate: str = ""           # "AAAA-MM-DD" — depois dessa data some do painel (fica no histórico)
    bloqueia_checklist: bool = False  # (09/11/2026) alerta de segurança: aparece antes do checklist e só libera após "Li e entendi"
    audio_path: str = ""           # (09/11/2026) áudio curto da gestão explicando o card (POST /api/mural/audio)

class CartilhaBloco(BaseModel):
    ordem: int = 0
    titulo: str
    subtitulo: str = ""
    conteudo: str
