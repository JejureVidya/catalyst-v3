# Catalyst ⚡



> Deccan AI Hackathon submission by **Akshay Bapusaheb Kalwaghe**



> An AI agent that actually interviews you — not just reads your resume.

I built this for the **Deccan AI Catalyst Hackathon**. The idea came from a simple frustration: resumes lie. Not intentionally, but they do. Someone writes "Python" and it could mean they wrote one script three years ago, or they've been building production pipelines for five years. There's no way to tell from a bullet point.

So I built an agent that finds out.

\---

## What it does



You paste a job description. You upload your resume. The agent reads both, figures out which skills actually matter for the role, and then **has a real conversation with you** — asking specific questions based on your own experience, following up when your answers are vague, and building a picture of where you actually stand.

At the end, instead of a generic "you need to learn Kubernetes" message, you get a personalised plan that says *why* you need it, exactly what to focus on, how long it'll realistically take, and which resources to use.

\---

## How it works under the hood



I built this as a 5-node agent pipeline. Here's the honest explanation of each part:

```
Your JD + Resume
      │
      ▼
┌─────────────────┐
│  Skill Extractor│   One LLM call that reads both documents
│                 │   and maps JD requirements against resume claims.
│                 │   Output: each skill is "matched", "partial", or "missing"
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Orchestrator   │   Decides what to ask and in what order.
│                 │   Partial matches go first — those are the interesting ones.
│                 │   Missing skills get skipped (no point asking about something
│                 │   you've never touched — it goes straight to the learning plan)
└────────┬────────┘
         │
         ▼  ◄──────────────────────┐
┌─────────────────┐                │ answer was vague
│  Assessor       │   Generates questions that reference YOUR resume.
│                 │   Not "do you know Docker?" but "you mentioned containerising
│                 │   services at Infosys — how did you handle container
│                 │   communication between services?"
│                 │   Scores your answer 0–1. Below 0.6 → follow-up.
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Gap Analyzer   │   Takes all the scores and classifies gaps.
│                 │   must-have + weak/missing = critical
│                 │   nice-to-have + weak/missing = moderate
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Learning Plan  │   Builds your plan from the gap report.
│  Generator      │   Specific objectives, honest time estimates,
│                 │   and real resources — not just "Google it"
└─────────────────┘
```

The thing I'm most happy with is the assessor. It doesn't have a question bank. Every question is generated fresh, grounded in what your resume actually says. Two people with different resumes applying for the same role get completely different conversations.

\---



## Scoring logic



I wanted the scoring to be transparent so here it is:



### Response score → skill classification





|Score|Label|What it means|Action|
|-|-|-|-|
|0.9 – 1.0|⭐ Expert|Deep knowledge, trade-offs, edge cases|✅ Skipped in plan|
|0.7 – 0.89|✅ Strong|Clear hands-on experience, specific details|✅ Skipped in plan|
|0.4 – 0.69|⚠️ Weak|Surface-level, basic familiarity only|Added as gap|
|0.1 – 0.39|❌ Poor|Vague answer, no real knowledge shown|Added as critical gap|
|null|❌ Missing|Not in resume, not assessed at all|Straight to learning plan|

### 

### Gap priority — how urgency is decided





|JD Importance|Classification|Priority|In Learning Plan|
|-|-|-|-|
|must-have|missing / poor / weak|🔴 Critical|Addressed first|
|nice-to-have|missing / poor / weak|🟡 Moderate|Addressed second|
|must-have|strong / expert|✅ None|Skipped entirely|
|nice-to-have|strong / expert|✅ None|Skipped entirely|

### 

### Assessment rules





|Rule|Value|Why|
|-|-|-|
|Max questions per skill|2|Nobody wants a 45-minute interrogation|
|Follow-up trigger|score < 0.6|Gives benefit of doubt on first answer|
|Skip condition|status = missing|No point asking what you've never touched|
|Typical session length|10–15 minutes|Realistic for a real candidate|

\---

## 

## Sample conversation



Here's a real example of what the conversation looks like. JD is for a backend engineer role. Candidate has FastAPI listed on resume from a personal project.

**Agent asks:**

> \*"Your Budget Tracker project used FastAPI — when you were building the authentication flow, did you handle token expiry yourself or lean on a library, and why did you make that call?"\*

**Candidate answers something vague like "I used JWT and it worked fine"**

**Agent follows up:**

> \*"What specifically happened in your app when a token expired mid-session — did you handle the refresh client-side, server-side, or just log the user out?"\*

That follow-up is targeting exactly the gap in the first answer. That's the core of what makes this feel like a real interview rather than a quiz.

\---

## Tech I used

|Layer|Technology|Version|Why I chose it|
|-|-|-|-|
|🧠 LLM|Groq + Llama 3.3 70B|latest|Free (14,400 req/day), \~1s response, great at structured JSON output|
|📄 PDF Parsing|PyMuPDF (fitz)|1.24.0|Best text extraction I found — handles multi-column resume layouts|
|⚙️ Backend|FastAPI|0.115.0|Async routes matter since every request calls the LLM|
|🌐 Frontend|Vanilla HTML/CSS/JS|—|No framework overhead, entire UI in one file, instant load|
|🚀 Deployment|Render|free tier|Connects to GitHub, auto-deploys on push, zero config|
|📦 Web server|Uvicorn|0.30.6|Async ASGI server, pairs naturally with FastAPI|

### 

### Dependencies — full list



|Package|Purpose|
|-|-|
|`groq==0.11.0`|Official Groq SDK for LLM calls|
|`fastapi==0.115.0`|Web framework|
|`uvicorn\[standard]==0.30.6`|ASGI server|
|`PyMuPDF==1.24.0`|PDF text extraction|
|`python-multipart==0.0.9`|File upload handling|

No database. Sessions live in memory. For a demo this is fine — for production you'd swap in Redis.

\---

## 

## Running it locally



You'll need Python 3.11+ and a Groq API key. The key is free at [console.groq.com](https://console.groq.com) — no credit card.

```bash
git clone https://github.com/YOUR\_USERNAME/catalyst-v3.git
cd catalyst-v3
pip install -r requirements.txt

# Windows
set GROQ\_API\_KEY=gsk\_your\_key\_here

# Mac / Linux
export GROQ\_API\_KEY=gsk\_your\_key\_here

python -m uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`. There's a "Load sample data" button if you want to see it working immediately without preparing your own documents.

\---

## 

## Deploying to Render



```
1. Push to GitHub
2. New Web Service on render.com → connect your repo
3. Build command:  pip install -r requirements.txt
4. Start command:  uvicorn app.main:app --host 0.0.0.0 --port $PORT
5. Add env var:    GROQ\_API\_KEY = your key
6. Deploy
```

Takes about 3 minutes.

\---

## 

## Project structure



```
catalyst-v3/
├── app/
│   └── main.py          # The whole backend — all 5 nodes + API routes (\~400 lines)
├── static/
│   └── index.html       # The whole frontend — upload, chat, results (\~500 lines)
├── samples/
│   ├── sample\_job\_description.txt
│   └── sample\_resume.txt
├── requirements.txt
└── render.yaml
```

\---

## 

## What I'd improve with more time



* **Memory across sessions** — right now everything is lost on restart
* **Better partial matching** — "Flask" and "FastAPI" should fuzzy-match better
* **Confidence intervals** — show candidates not just the score but how confident the agent is
* **Multi-role comparison** — assess against multiple JDs at once

\---

## Live demo

🔗 [**catalyst-v3.onrender.com**](#) ← update after deployment

📹 [**Demo video**](#) ← add after recording

\---

*Built for the Catalyst Hackathon by Deccan AI Experts.
Stack: Groq · Llama 3.3 · FastAPI · PyMuPDF · Render*

