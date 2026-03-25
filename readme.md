# FormalizeAI v2.1

> Entrevista Técnica Adaptativa + Geração de SDD  
> Powered by **Groq** (LLaMA 3) · Flask · Deploy-ready

---

## ⚡ Início rápido (local)

```bash
# 1. Clone o repositório
git clone https://github.com/SEU_USUARIO/formalizeai.git
cd formalizeai

# 2. Crie o ambiente virtual
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Configure a chave da API
cp .env.example .env
# Edite .env e coloque sua GROQ_API_KEY
# Obtenha em: https://console.groq.com/keys

# 5. Rode
python app.py
```

Acesse em: http://localhost:5000

---

## 🔑 Obtendo a chave Groq (gratuita)

1. Crie conta em https://console.groq.com
2. Vá em **API Keys** → **Create API Key**
3. Copie a chave (começa com `gsk_...`)
4. Cole no `.env`: `GROQ_API_KEY=gsk_...`

---

## 🚀 Deploy no Railway

1. Faça push do código para o GitHub
2. Acesse https://railway.app → **New Project** → **Deploy from GitHub repo**
3. Em **Variables**, adicione: `GROQ_API_KEY=gsk_...`
4. Railway detecta o `Procfile` automaticamente — pronto!

## 🚀 Deploy no Render

1. Faça push para o GitHub
2. Acesse https://render.com → **New Web Service** → conecte o repo
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
5. Em **Environment Variables**, adicione `GROQ_API_KEY`

---

## 🤖 Modelos disponíveis

| Modelo | Velocidade | Qualidade | Uso recomendado |
|---|---|---|---|
| `llama-3.3-70b-versatile` | Média | ⭐⭐⭐⭐⭐ | SDDs completos |
| `llama-3.1-8b-instant` | Muito rápida | ⭐⭐⭐ | Testes rápidos |
| `llama3-70b-8192` | Média | ⭐⭐⭐⭐ | Alternativa 70B |
| `llama3-8b-8192` | Rápida | ⭐⭐⭐ | Alternativa 8B |

---

## 📁 Estrutura do projeto

```
formalizeai/
├── app.py                  # API Flask principal
├── requirements.txt        # Dependências Python
├── Procfile                # Comando de deploy (Railway/Render)
├── .env.example            # Template de variáveis de ambiente
├── .gitignore
├── templates/
│   └── index.html          # Frontend
└── formalizeai_projects/   # Projetos salvos (gerado automaticamente)
```

---

## 🛡️ Segurança

- **Nunca** commite o arquivo `.env` (já está no `.gitignore`)
- A chave Groq só é lida via variável de ambiente
- Em produção, configure a variável direto no painel do Railway/Render
