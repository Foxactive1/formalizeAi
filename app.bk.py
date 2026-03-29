#!/usr/bin/env python3
"""
FormalizeAI v2.1 - Flask API
Entrevista Técnica Adaptativa + Geração de SDD
Powered by Groq (LLaMA 3) — deploy-ready para Railway / Render / VPS
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS

load_dotenv()

app = Flask(__name__)
CORS(app)

# ===================== CONFIG =====================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

AVAILABLE_MODELS =  [
    "llama-3.3-70b-versatile",  # Melhor qualidade — recomendado para SDD
    "llama-3.1-8b-instant",     # Rápido e leve
    "llama3-70b-8192",          # LLaMA 3 70B clássico
    "llama3-8b-8192",           # LLaMA 3 8B clássico
]
DEFAULT_MODEL = "llama-3.3-70b-versatile"

PROJECTS_DIR = Path(os.environ.get("PROJECTS_DIR", "/tmp/formalizeai_projects"))
PROJECTS_DIR.mkdir(exist_ok=True)

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

# ===================== GROQ CLIENT =====================
def get_client() -> Groq:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY não configurada. Verifique seu arquivo .env")
    return Groq(api_key=GROQ_API_KEY)

# ===================== FUNÇÕES =====================
def load_project(project_name: str) -> dict:
    project_file = PROJECTS_DIR / f"{project_name}.json"
    if project_file.exists():
        with open(project_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "messages": [],
        "model": DEFAULT_MODEL,
        "created": datetime.now().isoformat(),
    }

def save_project(project_name: str, data: dict):
    project_file = PROJECTS_DIR / f"{project_name}.json"
    with open(project_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def generate_response(messages: list, model: str) -> str:
    try:
        client = get_client()
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=4096,
        )
        return completion.choices[0].message.content
    except RuntimeError as e:
        return f"ERRO DE CONFIGURAÇÃO: {e}"
    except Exception as e:
        return f"ERRO AO CHAMAR GROQ API: {e}"

def extract_sdd(response_text: str) -> str | None:
    start = response_text.find("# Software Design Document (SDD)")
    return response_text[start:] if start != -1 else None

def save_sdd_file(project_name: str, sdd_content: str) -> str:
    md_file = PROJECTS_DIR / f"{project_name}_SDD.md"
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(sdd_content)
    return str(md_file)

# ===================== ROTAS =====================
@app.route("/api/models", methods=["GET"])
def list_models():
    return jsonify({"models": AVAILABLE_MODELS, "default": DEFAULT_MODEL})

@app.route("/api/projects", methods=["GET"])
def list_projects():
    projects = sorted([f.stem for f in PROJECTS_DIR.glob("*.json")])
    return jsonify({"projects": projects})

@app.route("/api/projects", methods=["POST"])
def create_project():
    data = request.json or {}
    project_name = (data.get("name") or
                    f"projeto-{datetime.now().strftime('%Y%m%d-%H%M')}").strip()
    model = data.get("model", DEFAULT_MODEL)

    if model not in AVAILABLE_MODELS:
        return jsonify({"error": f"Modelo inválido. Escolha: {AVAILABLE_MODELS}"}), 400

    project_data = load_project(project_name)
    if not project_data["messages"]:
        project_data["messages"].append({"role": "system", "content": SYSTEM_PROMPT})
        project_data["model"] = model
        save_project(project_name, project_data)

    return jsonify({"project": project_name, "model": model, "status": "created"})

@app.route("/api/projects/<project_name>", methods=["GET"])
def get_project(project_name):
    return jsonify(load_project(project_name))

@app.route("/api/projects/<project_name>", methods=["DELETE"])
def delete_project(project_name):
    for suffix in [".json", "_SDD.md"]:
        f = PROJECTS_DIR / f"{project_name}{suffix}"
        if f.exists():
            f.unlink()
    return jsonify({"status": "deleted", "project": project_name})

@app.route("/api/projects/<project_name>/chat", methods=["POST"])
def chat(project_name):
    data = request.json or {}
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Mensagem vazia"}), 400

    project_data = load_project(project_name)
    messages = project_data["messages"]
    model    = project_data["model"]

    messages.append({"role": "user", "content": user_message})
    assistant_response = generate_response(messages, model)
    messages.append({"role": "assistant", "content": assistant_response})
    save_project(project_name, project_data)

    is_final    = "[FINALIZANDO SDD]" in assistant_response
    sdd_content = None
    sdd_path    = None

    if is_final:
        sdd_content = extract_sdd(assistant_response)
        if sdd_content:
            sdd_path = save_sdd_file(project_name, sdd_content)

    return jsonify({
        "project":     project_name,
        "response":    assistant_response,
        "is_final":    is_final,
        "sdd_path":    sdd_path,
        "sdd_content": sdd_content,
    })

@app.route("/api/projects/<project_name>/regenerate-sdd", methods=["POST"])
def regenerate_sdd(project_name):
    project_data = load_project(project_name)
    if not project_data["messages"]:
        return jsonify({"error": "Projeto sem histórico"}), 400

    regen_messages = project_data["messages"] + [{
        "role": "user",
        "content": (
            "Com base em TUDO que foi discutido nesta entrevista, "
            "por favor regenere agora o Software Design Document completo e atualizado. "
            "Responda exatamente com [FINALIZANDO SDD] seguido do documento inteiro em Markdown."
        )
    }]

    assistant_response = generate_response(regen_messages, project_data["model"])
    is_final    = "[FINALIZANDO SDD]" in assistant_response
    sdd_content = None

    if is_final:
        sdd_content = extract_sdd(assistant_response)
        if sdd_content:
            save_sdd_file(project_name, sdd_content)

    return jsonify({
        "project":     project_name,
        "is_final":    is_final,
        "sdd_content": sdd_content,
    })

# ===================== HEALTHCHECK =====================
@app.route("/api/health", methods=["GET"])
def health():
    key_ok = bool(GROQ_API_KEY)
    return jsonify({
        "status":   "ok" if key_ok else "missing_key",
        "groq_key": "configurada" if key_ok else "AUSENTE — defina GROQ_API_KEY",
        "model":    DEFAULT_MODEL,
    }), 200 if key_ok else 503

# ===================== FRONTEND =====================
@app.route("/")
def index():
    return render_template("index.html")

# ===================== MAIN =====================
if __name__ == "__main__":
    if not GROQ_API_KEY:
        print("❌  GROQ_API_KEY não encontrada!")
        print("    Crie um arquivo .env com:  GROQ_API_KEY=gsk_...")
        print("    Obtenha sua chave em: https://console.groq.com/keys")
        sys.exit(1)

    print("✅  Groq API configurada")
    print(f"🤖  Modelo padrão: {DEFAULT_MODEL}")
    print("🚀  FormalizeAI rodando em http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
