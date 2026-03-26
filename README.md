```markdown
# FormalizeAI v2.2

**FormalizeAI** is an intelligent technical interview assistant that generates high‑quality **Software Design Documents (SDD)** through an adaptive, multi‑phase conversation.  
It uses Large Language Models (via Groq) to ask strategic questions, detect gaps, and produce a complete SDD with diagrams (Mermaid), security considerations, and architectural decisions – all in Portuguese.

![Demo](docs/demo.gif) *(add a screenshot/gif if available)*

---

## 🚀 Features

- **Adaptive Technical Interview** – The AI acts as a senior architect, leading the user through 7 phases (vision, functional/non‑functional requirements, architecture, data modeling, validation, final SDD).
- **Full SDD Generation** – Outputs a professional document in Markdown with:
  - Business objectives & metrics
  - Functional & non‑functional requirements
  - C4 models, sequence diagrams, ER diagrams (Mermaid)
  - Security & compliance analysis
  - Risk register, ADRs, glossary
- **Multi‑Model Support** – Choose between Groq’s LLaMA‑3 models (70B, 8B, instant) via the frontend dropdown.
- **PWA Frontend** – Vanilla HTML/CSS/JS with a modern, responsive sidebar, chat interface, and live SDD preview/download.
- **Dual Storage** – Persists projects either in **Supabase** (PostgreSQL + RLS) or, as a fallback, in a local `json` directory. Perfect for development or offline deployments.
- **Regenerate SDD** – If you’re not satisfied, you can regenerate the final document from the entire conversation history.
- **Analytics Endpoint** – See project statistics when Supabase is enabled.

---

## 🧰 Tech Stack

| Component          | Technology                                                          |
|--------------------|---------------------------------------------------------------------|
| Frontend           | Vanilla JavaScript (ES6), HTML5, CSS3, Bootstrap 5, FontAwesome     |
| Backend            | Python 3.8+, Flask, Flask-CORS, Gunicorn                            |
| LLM Integration    | Groq API (`llama-3.3-70b-versatile` by default)                     |
| Database           | Supabase (PostgreSQL + Auth) optional; local file fallback          |
| Deployment         | Railway / Render / any Python‑compatible PaaS (Procfile included)   |

---

## 📋 Prerequisites

- Python 3.8 or higher
- A [Groq API key](https://console.groq.com/keys) (free tier available)
- (Optional) A [Supabase project](https://supabase.com/) – if you want multi‑user support and cloud persistence

---

## ⚙️ Installation & Setup

### 1. Clone the repository
```bash
git clone https://github.com/your-username/formalizeai.git
cd formalizeai
```

2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
```

3. Install dependencies

```bash
pip install -r requirements.txt
```

4. Configure environment variables

Create a .env file in the root directory with the following content:

```env
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key-or-anon-key
```

· GROQ_API_KEY is required.
· SUPABASE_URL and SUPABASE_KEY are optional. If omitted, all data will be stored in the formalizeai_projects directory inside /tmp (or the current working directory).

5. Run the application locally

```bash
python app.py
```

Open your browser at http://127.0.0.1:5000.

---

🌐 Deployment (Railway / Render)

The project includes a Procfile and uses gunicorn.
Just set the environment variables in your deployment platform.

Example (Railway):

1. Create a new web service from your GitHub repo.
2. Add the environment variables (GROQ_API_KEY, SUPABASE_URL, SUPABASE_KEY).
3. Railway will automatically detect the Procfile and run gunicorn.

Example (Render):

· Use “Web Service” and set the start command: gunicorn app:app.

---

🧪 API Endpoints

Method Endpoint Description
GET /api/health Check API health and configuration status
GET /api/models List available Groq models
GET /api/projects List all projects (Supabase or local)
POST /api/projects Create a new project (name, optional model)
GET /api/projects/<name> Retrieve a project’s full conversation
DELETE /api/projects/<name> Delete a project (Supabase + local files)
POST /api/projects/<name>/chat Send a user message, get AI response, detect final SDD
POST /api/projects/<name>/regenerate-sdd Force SDD regeneration from existing history
GET /api/analytics (Supabase only) Return project statistics

The frontend (served at /) automatically calls these endpoints.

---

📁 Project Structure

```
.
├── app.py                  # Flask backend + routes + Groq/Supabase logic
├── requirements.txt        # Python dependencies
├── Procfile                # Gunicorn startup command
├── .env.example            # Example environment file (copy to .env)
├── formalizeai_projects/   # Local fallback storage (created automatically)
│   └── *.json
└── templates/
    └── index.html          # The frontend PWA (embedded in the Flask template)
```

---

🧠 How It Works

1. The frontend communicates with the Flask backend.
2. When the user creates a project, the backend initializes a conversation with a system prompt that defines the AI’s role as a senior architect.
3. The AI asks questions in Portuguese, one at a time, following a structured 7‑phase interview.
4. After each user response, the AI:
   · Summarises the current understanding
   · Points out gaps or inconsistencies
   · Asks the next strategic question
5. When the AI determines the interview is complete, it outputs [FINALIZANDO SDD] followed by the full SDD in Markdown.
6. The SDD is saved both to the database (if Supabase is used) and as a .md file in the local storage directory.
7. The user can download the SDD as a file or regenerate it at any time.

---

🔧 Customisation

· Change the system prompt – edit the SYSTEM_PROMPT variable in app.py.
· Add more Groq models – update the AVAILABLE_MODELS list.
· Frontend styling – modify the <style> block inside templates/index.html.

---

📄 License

This project is licensed under the MIT License – see the LICENSE file (if you include one).
Feel free to use, modify, and distribute it.

---

🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.
Make sure to update tests and documentation accordingly.

---

📬 Contact

Created by [Your Name] – feel free to reach out via GitHub issues or [email].

---

Enjoy building great software with FormalizeAI! 🎉
