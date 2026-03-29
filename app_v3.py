#!/usr/bin/env python3
"""
FormalizeAI v4.0 - Enterprise Flask API
========================================
Arquitetura: Orchestrator → Validator → Scorer + Groq LLM

Melhorias sobre v3.1:
  - Pipeline Orchestrator/Validator/Scorer integrado ao Groq real
  - Validação estrutural do SDD por seções obrigatórias
  - Score de qualidade (0–10) antes de finalizar
  - Retry automático com prompt corretivo (máx. 2 ciclos)
  - Mantém: Supabase singleton, Redis opcional, cache in-memory,
    API Key auth, trim_history robusto, persistência dual (Supabase + local),
    health check detalhado, todos os fixes da v3.1

Variáveis de ambiente necessárias:
  GROQ_API_KEY      — obrigatória
  SUPABASE_URL      — opcional (recomendado em produção)
  SUPABASE_KEY      — opcional
  REDIS_URL         — opcional
  X_API_KEY         — opcional (habilita auth básica nas rotas)
  DEFAULT_MODEL     — padrão: llama-3.3-70b-versatile
  CACHE_TTL         — padrão: 300s
  CACHE_MAX_ITEMS   — padrão: 100
  MAX_MESSAGE_LENGTH— padrão: 5000
  MAX_HISTORY_LENGTH— padrão: 20
  QUALITY_THRESHOLD — padrão: 7  (score mínimo para aprovar SDD)
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from hashlib import sha256
from functools import wraps
from dotenv import load_dotenv
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from groq import Groq

# ─────────────────────────────────────────────────────────────────────────────
# Ambiente
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv(".env")

# ─────────────────────────────────────────────────────────────────────────────
# Logging estruturado
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("formalizeai")

# ─────────────────────────────────────────────────────────────────────────────
# Configurações via variáveis de ambiente
# ─────────────────────────────────────────────────────────────────────────────
GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "")
X_API_KEY       = os.environ.get("X_API_KEY", "")          # auth básica (opcional)
REDIS_URL       = os.environ.get("REDIS_URL", "")

AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
]
DEFAULT_MODEL   = os.environ.get("DEFAULT_MODEL", "llama-3.3-70b-versatile")

CACHE_TTL        = int(os.environ.get("CACHE_TTL", 300))
CACHE_MAX_ITEMS  = int(os.environ.get("CACHE_MAX_ITEMS", 100))
MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", 5000))
MAX_HISTORY_LENGTH = int(os.environ.get("MAX_HISTORY_LENGTH", 20))
QUALITY_THRESHOLD  = int(os.environ.get("QUALITY_THRESHOLD", 12))  # mínimo para aprovar SDD (escala 0–18)

CACHE: dict = {}  # cache em memória — fallback quando Redis não está disponível

# ─────────────────────────────────────────────────────────────────────────────
# Redis (opcional) — sem Redis, usa CACHE dict em memória
# ─────────────────────────────────────────────────────────────────────────────
_redis_client = None

if REDIS_URL:
    try:
        import redis as _redis_lib
        _redis_client = _redis_lib.from_url(REDIS_URL, decode_responses=True)
        _redis_client.ping()
        log.info("Redis conectado via REDIS_URL")
    except Exception as e:
        log.warning(f"Redis indisponível — usando cache em memória: {e}")
        _redis_client = None
else:
    log.info("REDIS_URL não configurada — usando cache em memória")


def get_cache(key: str):
    """Lê do Redis (se ativo) ou do dict local com TTL manual."""
    if _redis_client:
        return _redis_client.get(key)
    entry = CACHE.get(key)
    if entry and (datetime.now().timestamp() - entry["time"]) < CACHE_TTL:
        return entry["response"]
    return None


def set_cache(key: str, value: str):
    """Grava no Redis (se ativo) ou no dict local."""
    if _redis_client:
        _redis_client.setex(key, CACHE_TTL, value)
    else:
        CACHE[key] = {"response": value, "time": datetime.now().timestamp()}
        _cleanup_cache()


def _cleanup_cache():
    """Remove entradas expiradas; limita tamanho máximo do cache em memória."""
    if _redis_client:
        return  # Redis gerencia TTL automaticamente
    now = datetime.now().timestamp()
    expired = [k for k, v in CACHE.items() if now - v["time"] > CACHE_TTL]
    for k in expired:
        del CACHE[k]
    if len(CACHE) > CACHE_MAX_ITEMS:
        oldest = sorted(CACHE.items(), key=lambda x: x[1]["time"])
        for k, _ in oldest[: len(CACHE) - CACHE_MAX_ITEMS]:
            del CACHE[k]


# ─────────────────────────────────────────────────────────────────────────────
# Supabase (singleton) — sem Supabase, usa persistência local em /tmp
# ─────────────────────────────────────────────────────────────────────────────
try:
    from supabase import create_client, Client as SupabaseClient
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False

_supabase_instance = None


def get_supabase():
    """Retorna instância Supabase reutilizável (singleton) ou None."""
    global _supabase_instance
    if _supabase_instance is not None:
        return _supabase_instance
    if not SUPABASE_AVAILABLE or not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        _supabase_instance = create_client(SUPABASE_URL, SUPABASE_KEY)
        log.info("Supabase client inicializado (singleton)")
        return _supabase_instance
    except Exception as e:
        log.warning(f"Falha ao conectar Supabase: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Diretório de persistência local
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_projects_dir() -> Path:
    """Resolve diretório de projetos; alerta quando em Railway sem Supabase."""
    env_dir = os.environ.get("PROJECTS_DIR")
    if env_dir:
        p = Path(env_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p
    on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT"))
    try:
        p = Path("/tmp/formalizeai_projects")
        p.mkdir(parents=True, exist_ok=True)
        if on_railway and not (SUPABASE_URL and SUPABASE_KEY):
            log.warning(
                "⚠️  Railway detectado sem Supabase. "
                "Dados em /tmp são PERDIDOS a cada deploy. "
                "Configure SUPABASE_URL e SUPABASE_KEY."
            )
        return p
    except OSError:
        p = Path(__file__).parent / "formalizeai_projects"
        p.mkdir(parents=True, exist_ok=True)
        return p


PROJECTS_DIR = _resolve_projects_dir()
log.info(f"Diretório de projetos: {PROJECTS_DIR}")

# ─────────────────────────────────────────────────────────────────────────────
# System Prompt — multi-agentes full-cycle
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
Você é o FormalizeAI v4 — sistema multi-agentes de engenharia de software full-cycle (nível sênior / produção).

OBJETIVO:
Gerar um SDD corporativo completo a partir de requisitos, passando pelos agentes abaixo em sequência.

━━━━━━━━━━━━━━━━━━━━━━━
AGENTE 1 — ARQUITETO
━━━━━━━━━━━━━━━━━━━━━━━
- Refinar requisitos e definir arquitetura (C4)
- Escolher stack técnica (ex: FastAPI, PostgreSQL)
- Modelar dados e APIs REST
- Registrar ADRs (decisões técnicas)

Saída: [ARQUITETO] arquitetura · APIs · modelo de dados · ADRs · dúvida (máx. 1)

━━━━━━━━━━━━━━━━━━━━━━━
AGENTE 2 — REVISOR
━━━━━━━━━━━━━━━━━━━━━━━
- Validar arquitetura e rastreabilidade
- Verificar over/under engineering
- Emitir veredito: APROVADO / REPROVADO

Saída: [REVISOR] problemas · impacto · veredito

━━━━━━━━━━━━━━━━━━━━━━━
AGENTE 3 — SEGURANÇA
━━━━━━━━━━━━━━━━━━━━━━━
- Validar OWASP Top 10 e autenticação/autorização
- Verificar conformidade LGPD

Saída: [SEGURANÇA] vulnerabilidades · mitigações

━━━━━━━━━━━━━━━━━━━━━━━
AGENTE 4 — SRE
━━━━━━━━━━━━━━━━━━━━━━━
- Definir logs estruturados, métricas e deploy (Docker)

Saída: [SRE] logs · métricas · estratégia de deploy

━━━━━━━━━━━━━━━━━━━━━━━
AGENTE 5 — ENGINEER
━━━━━━━━━━━━━━━━━━━━━━━
Stack padrão: FastAPI · SQLAlchemy · PostgreSQL · Pydantic · Pytest

Gera:
1. Estrutura de pastas modular
2. Código backend funcional (models, schemas, routes, services)
3. SQL de criação de tabelas
4. Testes Pytest básicos
5. Documentação OpenAPI automática

Regras: código deve rodar · modular · separação de responsabilidades obrigatória

━━━━━━━━━━━━━━━━━━━━━━━
FLUXO
━━━━━━━━━━━━━━━━━━━━━━━
1. Arquiteto propõe
2. Revisor valida (se REPROVADO → iterar, máx. 2 ciclos)
3. Segurança audita
4. SRE define operação
5. Engineer gera código

━━━━━━━━━━━━━━━━━━━━━━━
FINALIZAÇÃO
━━━━━━━━━━━━━━━━━━━━━━━
Só finalizar quando:
✔ Arquitetura consistente · APIs definidas · Banco modelado
✔ Segurança validada · Código executável · Testes incluídos

Ao finalizar, responder EXATAMENTE com:
[FINALIZANDO SDD]
# Software Design Document (SDD)
## 1. Visão Geral
## 2. Objetivos de Negócio
## 3. Stakeholders
## 4. Requisitos Funcionais
## 5. Requisitos Não Funcionais
## 6. Arquitetura
## 7. Modelo de Dados
## 8. APIs
## 9. Segurança
## 10. Observabilidade
## 11. Deploy
## 12. ADRs
## 13. Testes
## 14. Riscos
## 15. Roadmap
"""

# Seções obrigatórias para validação estrutural do SDD
REQUIRED_SECTIONS = [
    "## 1. Visão Geral",
    "## 2. Objetivos de Negócio",
    "## 3. Stakeholders",
    "## 4. Requisitos Funcionais",
    "## 5. Requisitos Não Funcionais",
    "## 6. Arquitetura",
    "## 7. Modelo de Dados",
    "## 8. APIs",
    "## 9. Segurança",
    "## 10. Observabilidade",
    "## 11. Deploy",
    "## 12. ADRs",
    "## 13. Testes",
    "## 14. Riscos",
    "## 15. Roadmap",
]

# ─────────────────────────────────────────────────────────────────────────────
# Validator — verifica seções obrigatórias no SDD
# ─────────────────────────────────────────────────────────────────────────────
class Validator:
    @staticmethod
    def validate(sdd: str) -> dict:
        """
        Verifica se todas as seções obrigatórias estão presentes.
        Retorna: {'valid': bool, 'missing': list[str]}
        """
        if not sdd:
            return {"valid": False, "missing": REQUIRED_SECTIONS}
        missing = [sec for sec in REQUIRED_SECTIONS if sec not in sdd]
        return {"valid": len(missing) == 0, "missing": missing}


# ─────────────────────────────────────────────────────────────────────────────
# Scorer — pontuação de qualidade do SDD (0–10)
# ─────────────────────────────────────────────────────────────────────────────
class Scorer:
    # Critérios de presença de seções (peso 2 cada) — máx. 10
    _SECTION_CRITERIA = [
        ("Arquitetura", 2),
        ("API", 2),
        ("Modelo de Dados", 2),
        ("Segurança", 2),
        ("Testes", 2),
    ]

    # Critérios de profundidade técnica (peso 1 cada) — máx. 8
    # Verificam se o conteúdo vai além de descrições genéricas
    _DEPTH_CRITERIA = [
        # APIs com endpoints reais
        ("POST",     1),   # métodos HTTP definidos
        ("GET",      1),   # ao menos um endpoint GET
        # Schema com tipos de dados
        ("VARCHAR",  1),   # colunas SQL tipadas
        ("INTEGER",  1),   # tipos numéricos presentes
        # Segurança concreta
        ("JWT",      1),   # autenticação com token
        ("bcrypt",   1),   # hash de senha definido
        # ADRs numerados
        ("ADR-",     1),   # pelo menos 1 ADR registrado
        # Roadmap com versões
        ("v1.",      1),   # versão/milestone definido
    ]

    # Score máximo: 10 (seções) + 8 (profundidade) = 18
    MAX_SCORE = 18

    @staticmethod
    def score(sdd: str) -> int:
        """
        Pontua o SDD em dois níveis:
          1. Presença das seções obrigatórias (0–10)
          2. Profundidade técnica do conteúdo  (0–8)
        Total máximo: 18 pontos.
        """
        if not sdd:
            return 0
        section_score = sum(pts for term, pts in Scorer._SECTION_CRITERIA if term in sdd)
        depth_score   = sum(pts for term, pts in Scorer._DEPTH_CRITERIA   if term in sdd)
        return section_score + depth_score

    @staticmethod
    def breakdown(sdd: str) -> dict:
        """Retorna detalhamento completo da pontuação para debug/frontend."""
        if not sdd:
            return {"section_score": 0, "depth_score": 0, "total": 0, "details": []}

        details = []
        section_score = 0
        for term, pts in Scorer._SECTION_CRITERIA:
            hit = term in sdd
            if hit:
                section_score += pts
            details.append({"criterion": term, "type": "section", "points": pts, "hit": hit})

        depth_score = 0
        for term, pts in Scorer._DEPTH_CRITERIA:
            hit = term in sdd
            if hit:
                depth_score += pts
            details.append({"criterion": term, "type": "depth", "points": pts, "hit": hit})

        return {
            "section_score": section_score,
            "depth_score":   depth_score,
            "total":         section_score + depth_score,
            "max":           Scorer.MAX_SCORE,
            "details":       details,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator — pipeline de geração, validação e retry com Groq
# ─────────────────────────────────────────────────────────────────────────────
class Orchestrator:
    MAX_CYCLES = 2

    def __init__(self, model: str):
        self.model = model

    def run(self, messages: list) -> dict:
        """
        Executa o pipeline de geração do SDD:
        1. Gera resposta via Groq
        2. Extrai o SDD da resposta
        3. Valida estrutura (Validator) e qualidade (Scorer)
        4. Se reprovado, injeta prompt corretivo e repete (máx. MAX_CYCLES)

        Retorna: {
            'response': str,       — resposta completa do LLM
            'sdd': str | None,     — SDD extraído
            'score': int,          — pontuação de qualidade
            'validation': dict,    — resultado da validação
            'status': str,         — 'approved' | 'needs_review'
            'cycles': int,         — número de ciclos executados
        }
        """
        sdd = ""
        score = 0
        validation = {"valid": False, "missing": REQUIRED_SECTIONS}
        last_response = ""

        for cycle in range(1, self.MAX_CYCLES + 1):
            log.info(f"Orchestrator: ciclo {cycle}/{self.MAX_CYCLES} — modelo {self.model}")
            last_response = _groq_generate(messages, self.model)

            sdd = _extract_sdd(last_response)
            validation = Validator.validate(sdd)
            score = Scorer.score(sdd)

            log.info(
                f"Ciclo {cycle}: score={score}/{QUALITY_THRESHOLD} "
                f"valid={validation['valid']} missing={len(validation['missing'])}"
            )

            if validation["valid"] and score >= QUALITY_THRESHOLD:
                return {
                    "response":   last_response,
                    "sdd":        sdd,
                    "score":      score,
                    "validation": validation,
                    "status":     "approved",
                    "cycles":     cycle,
                }

            # Injeta prompt corretivo para o próximo ciclo
            if cycle < self.MAX_CYCLES:
                messages = messages + [
                    {"role": "assistant", "content": last_response},
                    {"role": "user",      "content": self._fix_prompt(validation, score)},
                ]

        # Esgotou ciclos — retorna o melhor resultado obtido
        return {
            "response":   last_response,
            "sdd":        sdd,
            "score":      score,
            "validation": validation,
            "status":     "needs_review",
            "cycles":     self.MAX_CYCLES,
        }

    def _fix_prompt(self, validation: dict, score: int) -> str:
        missing_list = "\n".join(f"  - {s}" for s in validation["missing"]) or "  (nenhuma)"
        return (
            f"O SDD gerado não atingiu o padrão mínimo.\n\n"
            f"Score atual: {score}/{QUALITY_THRESHOLD}\n"
            f"Seções faltando:\n{missing_list}\n\n"
            "Por favor, REGENERE o SDD completo e corrigido, "
            "incluindo TODAS as seções obrigatórias com conteúdo técnico detalhado."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Groq — cliente e geração de resposta
# ─────────────────────────────────────────────────────────────────────────────
def _get_groq_client() -> Groq:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY não configurada")
    return Groq(api_key=GROQ_API_KEY)


def _groq_generate(messages: list, model: str) -> str:
    """
    Chama a API Groq com cache (Redis ou in-memory).
    Lança RuntimeError em caso de falha não recuperável.
    """
    trimmed   = _trim_history(messages)
    cache_key = _generate_cache_key(trimmed, model)
    cached    = get_cache(cache_key)

    if cached:
        log.info("Cache hit — resposta reutilizada")
        return cached if isinstance(cached, str) else cached.get("response", "")

    try:
        client = _get_groq_client()
        completion = client.chat.completions.create(
            model=model,
            messages=trimmed,
            temperature=0.7,
            max_tokens=4096,
        )
        response = completion.choices[0].message.content
        set_cache(cache_key, response)
        return response
    except Exception as e:
        log.exception("Erro na chamada Groq")
        raise RuntimeError(f"Groq falhou: {e}") from e


def _extract_sdd(text: str) -> str:
    """Extrai o bloco SDD (após '# Software Design Document (SDD)') da resposta."""
    if not text:
        return ""
    start = text.find("# Software Design Document (SDD)")
    return text[start:] if start != -1 else ""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de histórico e cache
# ─────────────────────────────────────────────────────────────────────────────
def _trim_history(messages: list) -> list:
    """
    Mantém a(s) mensagem(ns) de sistema + as últimas MAX_HISTORY_LENGTH mensagens.
    Robusto mesmo se o histórico vier sem system prompt.
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    other_msgs  = [m for m in messages if m.get("role") != "system"]
    if len(other_msgs) <= MAX_HISTORY_LENGTH:
        return system_msgs + other_msgs
    return system_msgs + other_msgs[-MAX_HISTORY_LENGTH:]


def _generate_cache_key(messages: list, model: str) -> str:
    payload = json.dumps({"messages": messages[-10:], "model": model}, sort_keys=True)
    return sha256(payload.encode()).hexdigest()


def _validate_message(message: str) -> str:
    """Sanitiza e valida mensagem de entrada."""
    if not message or not isinstance(message, str):
        raise ValueError("Mensagem inválida ou vazia")
    msg = message.strip()
    if len(msg) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"Mensagem excede {MAX_MESSAGE_LENGTH} caracteres")
    return msg


# ─────────────────────────────────────────────────────────────────────────────
# Persistência (Supabase com fallback local)
# ─────────────────────────────────────────────────────────────────────────────
def _local_path(project_name: str) -> Path:
    return PROJECTS_DIR / f"{project_name}.json"


def load_project(project_name: str) -> dict:
    """Carrega projeto do Supabase ou do disco local. Retorna estrutura nova se inexistente."""
    sb = get_supabase()
    if sb:
        try:
            proj = (
                sb.table("projects")
                .select("id, name, model, status, created_at, updated_at")
                .eq("name", project_name)
                .maybe_single()
                .execute()
            )
            if proj.data:
                project_id = proj.data["id"]
                msgs = (
                    sb.table("messages")
                    .select("role, content")
                    .eq("project_id", project_id)
                    .order("seq")
                    .execute()
                )
                return {
                    "id":       project_id,
                    "messages": [{"role": m["role"], "content": m["content"]}
                                 for m in (msgs.data or [])],
                    "model":    proj.data["model"],
                    "status":   proj.data["status"],
                    "created":  proj.data["created_at"],
                    "_source":  "supabase",
                }
        except Exception as e:
            log.warning(f"load_project Supabase falhou: {e}")

    local = _local_path(project_name)
    if local.exists():
        try:
            with open(local, "r", encoding="utf-8") as f:
                data = json.load(f)
                data["_source"] = "local"
                return data
        except Exception as e:
            log.error(f"Erro ao ler arquivo local {local}: {e}")

    return {
        "messages": [],
        "model":    DEFAULT_MODEL,
        "status":   "em_andamento",
        "created":  datetime.now().isoformat(),
        "_source":  "new",
    }


def save_project(project_name: str, data: dict) -> None:
    """
    Salva projeto no Supabase (se disponível) e sempre no disco local.
    Guard: só apaga mensagens se houver novas para inserir (evita perda de histórico).
    """
    sb = get_supabase()
    project_id = data.get("id")

    if sb:
        try:
            messages = data.get("messages", [])
            if project_id:
                sb.table("projects").update({
                    "model":      data.get("model", DEFAULT_MODEL),
                    "status":     data.get("status", "em_andamento"),
                    "updated_at": datetime.now().isoformat(),
                }).eq("id", project_id).execute()
            else:
                result = sb.table("projects").insert({
                    "name":   project_name,
                    "model":  data.get("model", DEFAULT_MODEL),
                    "status": data.get("status", "em_andamento"),
                }).execute()
                if result.data:
                    project_id = result.data[0]["id"]
                    data["id"] = project_id

            if project_id and messages:
                rows = [
                    {"project_id": project_id, "role": m["role"],
                     "content": m["content"], "seq": i}
                    for i, m in enumerate(messages)
                ]
                sb.table("messages").delete().eq("project_id", project_id).execute()
                sb.table("messages").insert(rows).execute()
        except Exception as e:
            log.warning(f"save_project Supabase falhou: {e}")

    local = _local_path(project_name)
    try:
        with open(local, "w", encoding="utf-8") as f:
            clean = {k: v for k, v in data.items() if not k.startswith("_")}
            json.dump(clean, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Erro ao salvar arquivo local {local}: {e}")


def save_sdd(project_name: str, sdd_content: str, data: dict) -> str:
    """Persiste o SDD no Supabase (tabela sdds, com versionamento) e em .md local."""
    sb = get_supabase()
    project_id = data.get("id")

    if sb and project_id:
        try:
            result = (
                sb.table("sdds")
                .select("version")
                .eq("project_id", project_id)
                .order("version", desc=True)
                .limit(1)
                .execute()
            )
            next_version = (result.data[0]["version"] + 1) if result.data else 1
            sb.table("sdds").insert({
                "project_id": project_id,
                "content":    sdd_content,
                "version":    next_version,
            }).execute()
            sb.table("projects").update({"status": "finalizado"}).eq("id", project_id).execute()
        except Exception as e:
            log.warning(f"save_sdd Supabase falhou: {e}")

    md_file = PROJECTS_DIR / f"{project_name}_SDD.md"
    try:
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(sdd_content)
    except Exception as e:
        log.error(f"Erro ao salvar SDD local: {e}")

    return str(md_file)


# ─────────────────────────────────────────────────────────────────────────────
# Auth decorator
# ─────────────────────────────────────────────────────────────────────────────
def require_api_key(f):
    """Exige header X-Api-Key se X_API_KEY estiver configurada no ambiente."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if X_API_KEY:
            if request.headers.get("X-Api-Key", "") != X_API_KEY:
                return jsonify({"error": "Unauthorized — X-Api-Key inválida"}), 401
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# Flask App
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)


# ─────────────────────────────────────────────────────────────────────────────
# Rotas — Modelos
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/models", methods=["GET"])
def list_models():
    """Lista modelos Groq disponíveis."""
    return jsonify({"models": AVAILABLE_MODELS, "default": DEFAULT_MODEL})


# ─────────────────────────────────────────────────────────────────────────────
# Rotas — Projetos (CRUD)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/projects", methods=["GET"])
@require_api_key
def list_projects():
    """Lista todos os projetos (Supabase ou fallback local)."""
    sb = get_supabase()
    if sb:
        try:
            result = (
                sb.table("v_projects_summary")
                .select("*")
                .order("updated_at", desc=True)
                .execute()
            )
            return jsonify({"projects": result.data or [], "source": "supabase"})
        except Exception as e:
            log.warning(f"list_projects Supabase falhou: {e}")

    projects = []
    for f in PROJECTS_DIR.glob("*.json"):
        name = f.stem
        try:
            with open(f, "r", encoding="utf-8") as fp:
                d = json.load(fp)
                status  = d.get("status", "desconhecido")
                updated = d.get("updated_at") or d.get("created") or ""
        except Exception:
            status, updated = "erro", ""
        projects.append({"name": name, "status": status, "updated_at": updated})
    return jsonify({"projects": projects, "source": "local"})


@app.route("/api/projects", methods=["POST"])
@require_api_key
def create_project():
    """Cria novo projeto com nome e modelo especificados."""
    data = request.json or {}
    project_name = (
        data.get("name") or f"projeto-{datetime.now().strftime('%Y%m%d-%H%M')}"
    ).strip()
    model = data.get("model", DEFAULT_MODEL)

    if model not in AVAILABLE_MODELS:
        return jsonify({"error": f"Modelo inválido. Escolha: {AVAILABLE_MODELS}"}), 400

    project_data = load_project(project_name)
    if not project_data["messages"]:
        project_data["messages"].append({"role": "system", "content": SYSTEM_PROMPT})
        project_data["model"] = model
        save_project(project_name, project_data)

    return jsonify({
        "project": project_name,
        "model":   model,
        "status":  "created",
        "source":  project_data.get("_source", "unknown"),
    })


@app.route("/api/projects/<project_name>", methods=["GET"])
@require_api_key
def get_project(project_name):
    """Retorna dados completos de um projeto."""
    data = load_project(project_name)
    return jsonify({k: v for k, v in data.items() if not k.startswith("_")})


@app.route("/api/projects/<project_name>", methods=["PATCH"])
@require_api_key
def update_project(project_name):
    """Atualiza model e/ou status de um projeto existente."""
    body = request.json or {}
    project_data = load_project(project_name)

    if "model" in body:
        if body["model"] not in AVAILABLE_MODELS:
            return jsonify({"error": f"Modelo inválido. Escolha: {AVAILABLE_MODELS}"}), 400
        project_data["model"] = body["model"]
        log.info(f"Projeto '{project_name}': modelo atualizado → {body['model']}")

    if "status" in body:
        project_data["status"] = body["status"]

    save_project(project_name, project_data)
    return jsonify({
        "project": project_name,
        "model":   project_data["model"],
        "status":  project_data["status"],
    })


@app.route("/api/projects/<project_name>", methods=["DELETE"])
@require_api_key
def delete_project(project_name):
    """Remove projeto do Supabase e do disco local."""
    sb = get_supabase()
    if sb:
        try:
            sb.table("projects").delete().eq("name", project_name).execute()
        except Exception as e:
            log.warning(f"delete Supabase falhou: {e}")

    for suffix in [".json", "_SDD.md"]:
        f = PROJECTS_DIR / f"{project_name}{suffix}"
        if f.exists():
            try:
                f.unlink()
            except Exception as e:
                log.warning(f"Falha ao remover {f}: {e}")

    return jsonify({"status": "deleted", "project": project_name})


# ─────────────────────────────────────────────────────────────────────────────
# Rota — Chat conversacional (com Orchestrator ativado na finalização)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/projects/<project_name>/chat", methods=["POST"])
@require_api_key
def chat(project_name):
    """
    Conversa iterativa com o agente.
    Quando o agente sinalizar [FINALIZANDO SDD], o Orchestrator assume:
    valida, pontua e executa retry automático se necessário.
    """
    try:
        body         = request.json or {}
        user_message = _validate_message(body.get("message", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    project_data = load_project(project_name)
    messages     = project_data["messages"]
    model        = project_data["model"]

    messages.append({"role": "user", "content": user_message})

    # Geração inicial
    try:
        assistant_response = _groq_generate(messages, model)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503

    is_final    = "[FINALIZANDO SDD]" in assistant_response
    sdd_content = None
    sdd_path    = None
    orch_result = None

    if is_final:
        # Aciona o Orchestrator para validar e pontuar o SDD
        orch = Orchestrator(model)
        orch_result = orch.run(messages + [{"role": "assistant", "content": assistant_response}])

        sdd_content = orch_result["sdd"] or None
        assistant_response = orch_result["response"]  # pode ser resposta corrigida

        if sdd_content:
            project_data["status"] = "finalizado"
            sdd_path = save_sdd(project_name, sdd_content, project_data)

    messages.append({"role": "assistant", "content": assistant_response})
    save_project(project_name, project_data)

    response_payload = {
        "project":     project_name,
        "response":    assistant_response,
        "is_final":    is_final,
        "sdd_path":    sdd_path,
        "sdd_content": sdd_content,
    }
    if orch_result:
        response_payload["orchestrator"] = {
            "score":      orch_result["score"],
            "max_score":  Scorer.MAX_SCORE,
            "status":     orch_result["status"],
            "cycles":     orch_result["cycles"],
            "validation": orch_result["validation"],
            "breakdown":  Scorer.breakdown(orch_result.get("sdd", "")),
        }
    return jsonify(response_payload)


# ─────────────────────────────────────────────────────────────────────────────
# Rota — Geração direta via Orchestrator (endpoint standalone, ex-v4 concept)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/generate", methods=["POST"])
@require_api_key
def generate_sdd():
    """
    Gera SDD completo em uma única chamada (sem projeto persistido).
    Corpo: { "prompt": "...", "model": "..." (opcional) }
    Retorna SDD + score + status de aprovação.
    """
    body   = request.json or {}
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Campo 'prompt' obrigatório"}), 400

    model = body.get("model", DEFAULT_MODEL)
    if model not in AVAILABLE_MODELS:
        return jsonify({"error": f"Modelo inválido. Escolha: {AVAILABLE_MODELS}"}), 400

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": prompt},
    ]

    orch = Orchestrator(model)
    result = orch.run(messages)

    project_id = sha256(prompt.encode()).hexdigest()[:16]
    return jsonify({
        "project_id": project_id,
        "sdd":        result["sdd"],
        "score":      result["score"],
        "max_score":  Scorer.MAX_SCORE,
        "status":     result["status"],
        "cycles":     result["cycles"],
        "validation": result["validation"],
        "breakdown":  Scorer.breakdown(result.get("sdd", "")),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Rota — Regenerar SDD de projeto existente
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/projects/<project_name>/regenerate-sdd", methods=["POST"])
@require_api_key
def regenerate_sdd(project_name):
    """Força a regeneração do SDD com base no histórico completo do projeto."""
    project_data = load_project(project_name)
    if not project_data["messages"]:
        return jsonify({"error": "Projeto sem histórico"}), 400

    regen_messages = project_data["messages"] + [{
        "role": "user",
        "content": (
            "Com base em TUDO que foi discutido nesta entrevista, "
            "regenere agora o Software Design Document completo e atualizado. "
            "Responda exatamente com [FINALIZANDO SDD] seguido do documento inteiro em Markdown."
        ),
    }]

    orch = Orchestrator(project_data["model"])
    result = orch.run(regen_messages)

    sdd_path = None
    if result["sdd"]:
        sdd_path = save_sdd(project_name, result["sdd"], project_data)
        project_data["status"] = "finalizado"
        save_project(project_name, project_data)

    return jsonify({
        "project":     project_name,
        "sdd_content": result["sdd"],
        "sdd_path":    sdd_path,
        "score":       result["score"],
        "max_score":   Scorer.MAX_SCORE,
        "status":      result["status"],
        "cycles":      result["cycles"],
        "validation":  result["validation"],
        "breakdown":   Scorer.breakdown(result.get("sdd", "")),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Rota — Analytics
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/analytics", methods=["GET"])
@require_api_key
def analytics():
    """Estatísticas de projetos via Supabase (requer Supabase configurado)."""
    sb = get_supabase()
    if not sb:
        return jsonify({"error": "Supabase não configurado"}), 503
    try:
        result = (
            sb.table("v_projects_summary")
            .select("*")
            .order("updated_at", desc=True)
            .execute()
        )
        projects  = result.data or []
        total     = len(projects)
        finalized = sum(1 for p in projects if p.get("status") == "finalizado")
        return jsonify({
            "total_projects": total,
            "finalizados":    finalized,
            "em_andamento":   total - finalized,
            "projects":       projects,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Rota — Health Check
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    """Health check detalhado: Groq, Supabase, Redis, cache."""
    groq_ok  = bool(GROQ_API_KEY)
    sb       = get_supabase()
    sb_ok    = False
    if sb:
        try:
            sb.table("projects").select("id").limit(1).execute()
            sb_ok = True
        except Exception:
            pass

    redis_ok = False
    if _redis_client:
        try:
            _redis_client.ping()
            redis_ok = True
        except Exception:
            pass

    status_code = 200 if groq_ok else 503
    return jsonify({
        "status":    "ok" if groq_ok else "missing_groq_key",
        "version":   "4.0",
        "groq_key":  "configurada" if groq_ok else "AUSENTE",
        "supabase":  "conectado" if sb_ok else "não configurado / erro",
        "redis":     "conectado" if redis_ok else "não configurado (cache em memória)",
        "model":     DEFAULT_MODEL,
        "quality_threshold": QUALITY_THRESHOLD,
        "quality_max_score": Scorer.MAX_SCORE,
        "storage":   "supabase+local" if sb_ok else "local_only",
        "cache": {
            "backend":      "redis" if redis_ok else "memory",
            "ttl_seconds":  CACHE_TTL,
            "current_size": len(CACHE) if not _redis_client else "gerenciado pelo Redis",
        },
        "time": datetime.now().isoformat(),
    }), status_code


# ─────────────────────────────────────────────────────────────────────────────
# Frontend
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Validações de inicialização
    if not GROQ_API_KEY:
        print("❌ GROQ_API_KEY não encontrada!")
        print("   Crie um arquivo .env com: GROQ_API_KEY=gsk_...")
        sys.exit(1)

    if DEFAULT_MODEL not in AVAILABLE_MODELS:
        print(f"⚠️  Modelo padrão '{DEFAULT_MODEL}' não reconhecido. Usando o primeiro disponível.")
        DEFAULT_MODEL = AVAILABLE_MODELS[0]

    # Status de inicialização
    sb_status = (
        "✅ Supabase configurado"
        if (SUPABASE_URL and SUPABASE_KEY)
        else "⚠️  Supabase não configurado — usando apenas /tmp"
    )
    rd_status = (
        "✅ Redis configurado"
        if REDIS_URL
        else "⚠️  Redis não configurado — cache em memória"
    )

    print("✅ Groq API configurada")
    print(sb_status)
    print(rd_status)
    if X_API_KEY:
        print("🔐 API Key ativa — autenticação habilitada")
    print(f"🤖 Modelo padrão  : {DEFAULT_MODEL}")
    print(f"🎯 Quality threshold: {QUALITY_THRESHOLD}/{Scorer.MAX_SCORE}")
    env = "desenvolvimento" if os.environ.get("FLASK_ENV") == "development" else "produção"
    print(f"🚀 FormalizeAI v4.0 rodando em modo {env}")
    print("   Acesse: http://127.0.0.1:5000")
    print("   Produção: gunicorn -w 2 -b 0.0.0.0:$PORT app_v4:app")

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG", "False").lower() == "true",
    )
