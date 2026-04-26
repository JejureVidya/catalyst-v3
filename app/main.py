"""
Catalyst v3 - AI-Powered Skill Assessment Agent
Groq API (FREE) + PDF resume support + Beautiful UI
"""

import os, json, re, uuid
from datetime import datetime
from typing import Optional, Dict, List, Any
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
import fitz  # PyMuPDF for PDF parsing

app = FastAPI(title="Catalyst - Skill Assessment Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/samples", StaticFiles(directory="samples"), name="samples")

client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
MODEL  = "llama-3.3-70b-versatile"

sessions = {}  # type: Dict[str, Any]


# ═══════════════════════════════════════════════════════════════════
# PDF PARSER
# ═══════════════════════════════════════════════════════════════════

def extract_text_from_pdf(pdf_bytes):
    # type: (bytes) -> str
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n".join(pages)[:12000]
    except Exception as e:
        return ""


# ═══════════════════════════════════════════════════════════════════
# CORE LLM CALL
# ═══════════════════════════════════════════════════════════════════

def call_llm(prompt, system="", max_tokens=2000):
    # type: (str, str, int) -> str
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.3,
    )
    return response.choices[0].message.content.strip()


def parse_json(raw):
    # type: (str) -> Any
    cleaned = re.sub(r'^```json\s*', '', raw, flags=re.MULTILINE)
    cleaned = re.sub(r'\s*```$', '', cleaned, flags=re.MULTILINE).strip()
    match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', cleaned)
    return json.loads(match.group(1) if match else cleaned)


# ═══════════════════════════════════════════════════════════════════
# NODE 1 - SKILL EXTRACTOR
# ═══════════════════════════════════════════════════════════════════

def extract_skills(jd_text, resume_text):
    # type: (str, str) -> Dict
    prompt = (
        "You are an expert technical recruiter.\n\n"
        "JOB DESCRIPTION:\n" + jd_text[:5000] + "\n\n"
        "RESUME:\n" + resume_text[:5000] + "\n\n"
        "Extract the 6 most important required skills from the JD and match against the resume.\n\n"
        "Return ONLY valid JSON, no markdown:\n"
        '{"role_title":"Backend Engineer","candidate_name":"John Doe",'
        '"skill_matrix":[{"skill":"Python","jd_importance":"must-have",'
        '"status":"matched","resume_evidence":"4 years Python at Infosys",'
        '"resume_proficiency":"advanced"}]}\n\n'
        "Rules:\n"
        '- status: "matched", "partial" (adjacent skill), or "missing"\n'
        '- jd_importance: "must-have" or "nice-to-have"\n'
        '- resume_proficiency: "beginner","intermediate","advanced","expert" or null\n'
        "- Return max 6 skills"
    )
    raw = call_llm(prompt, max_tokens=1500)
    return parse_json(raw)


# ═══════════════════════════════════════════════════════════════════
# NODE 2 - ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════

PRIORITY = {
    ("must-have",    "partial"):  0,
    ("must-have",    "matched"):  1,
    ("nice-to-have", "partial"):  2,
    ("nice-to-have", "matched"):  3,
}
MAX_Q_PER_SKILL = 2


def build_queue(matrix):
    # type: (List) -> List
    queue = []
    for s in matrix:
        entry = dict(s)
        entry["status_assess"]  = "skipped" if s["status"] == "missing" else "pending"
        entry["score"]          = None
        entry["assessor_note"]  = ""
        entry["questions_asked"] = 0
        entry["conversation"]   = []
        queue.append(entry)
    queue.sort(key=lambda x: (
        99 if x["status_assess"] == "skipped"
        else PRIORITY.get((x["jd_importance"], x["status"]), 50)
    ))
    return queue


def current_skill(session):
    # type: (Dict) -> Optional[Dict]
    q, i = session["queue"], session["idx"]
    return q[i] if i < len(q) else None


def advance(session):
    # type: (Dict) -> None
    q = session["queue"]
    i = session["idx"]
    while i < len(q) and q[i]["status_assess"] in ("assessed", "skipped"):
        i += 1
    session["idx"] = i


# ═══════════════════════════════════════════════════════════════════
# NODE 3 - CONVERSATIONAL ASSESSOR
# ═══════════════════════════════════════════════════════════════════

ASSESSOR_SYSTEM = (
    "You are a senior technical interviewer. Ask one focused specific question at a time. "
    "Ground questions in the candidate's actual resume evidence. "
    "Never ask yes/no questions. Sound human, not robotic. "
    "No preamble like 'Great!' or 'Sure!'. Just the question."
)


def generate_question(skill):
    # type: (Dict) -> str
    is_followup = skill["questions_asked"] > 0
    evidence = (
        'Resume says: "' + str(skill.get("resume_evidence", "")) + '"'
        if skill.get("resume_evidence")
        else "No resume evidence for this skill."
    )
    history_parts = []
    for m in skill["conversation"][-4:]:
        role = "Interviewer" if m["role"] == "ai" else "Candidate"
        history_parts.append(role + ": " + m["content"])
    history = "\n".join(history_parts) or "None yet."

    if not is_followup:
        instruction = (
            "PARTIAL MATCH: Bridge from what they know to what the JD requires."
            if skill["status"] == "partial"
            else "MATCHED: Anchor to resume evidence. Ask about a specific decision or trade-off."
        )
    else:
        score_val = round(skill["score"] if skill["score"] is not None else 0.5, 1)
        instruction = (
            "FOLLOW-UP: Last answer scored " + str(score_val) +
            "/1.0 - too vague. Probe the weakest part. Do NOT repeat the previous question."
        )

    prompt = (
        "Skill: " + skill["skill"] + " (" + skill["jd_importance"] + ")\n"
        + evidence + "\nConversation:\n" + history
        + "\nInstruction: " + instruction
        + "\n\nExactly ONE question. Max 2 sentences. Conversational."
    )
    return call_llm(prompt, system=ASSESSOR_SYSTEM, max_tokens=150)


def evaluate_response(skill_name, question, response):
    # type: (str, str, str) -> Dict
    prompt = (
        'Score this response for skill "' + skill_name + '".\n'
        "Q: " + question + "\nA: " + response + "\n\n"
        "0.0-0.3=vague, 0.4-0.6=basic, 0.7-0.8=hands-on, 0.9-1.0=expert\n"
        'Return ONLY JSON: {"score":0.7,"note":"one sentence"}'
    )
    raw = call_llm(prompt, max_tokens=120)
    result = parse_json(raw)
    return {"score": float(result.get("score", 0.5)), "note": result.get("note", "")}


# ═══════════════════════════════════════════════════════════════════
# NODE 4 - GAP ANALYZER
# ═══════════════════════════════════════════════════════════════════

def classify(score):
    # type: (Optional[float]) -> str
    if score is None: return "missing"
    if score >= 0.7:  return "strong"
    if score >= 0.4:  return "weak"
    return "missing"


def priority_label(importance, classification):
    # type: (str, str) -> str
    if importance == "must-have"    and classification != "strong": return "critical"
    if importance == "nice-to-have" and classification != "strong": return "moderate"
    return "minor"


def build_gap_report(session):
    # type: (Dict) -> Dict
    gaps = {"critical": [], "moderate": [], "minor": []}  # type: Dict[str, List]
    for s in session["queue"]:
        cl = classify(s["score"])
        if cl == "strong" and s["jd_importance"] == "must-have":
            continue
        pl = priority_label(s["jd_importance"], cl)
        gaps[pl].append({
            "skill":          s["skill"],
            "jd_importance":  s["jd_importance"],
            "classification": cl,
            "score":          s["score"],
            "note": s["assessor_note"] or (
                "Not in resume — not assessed."
                if s["score"] is None
                else "Some familiarity but lacks hands-on depth."
            ),
        })
    return {
        "candidate_name": session["candidate_name"],
        "role_title":     session["role_title"],
        "summary": {
            "critical": len(gaps["critical"]),
            "moderate": len(gaps["moderate"]),
            "minor":    len(gaps["minor"]),
            "assessed": sum(1 for s in session["queue"] if s["status_assess"] == "assessed"),
        },
        "gaps": gaps,
    }


# ═══════════════════════════════════════════════════════════════════
# NODE 5 - LEARNING PLAN
# ═══════════════════════════════════════════════════════════════════

RESOURCE_DB = {
    "python":       [{"title":"Python Official Docs","url":"https://docs.python.org/3/","type":"docs","why":"Complete reference for all Python concepts","free":True},
                     {"title":"Real Python","url":"https://realpython.com","type":"tutorial","why":"Practical hands-on tutorials for every skill level","free":True}],
    "fastapi":      [{"title":"FastAPI Official Docs","url":"https://fastapi.tiangolo.com","type":"docs","why":"Best docs covering async, DI, and advanced patterns","free":True},
                     {"title":"FastAPI Full Course","url":"https://www.youtube.com/watch?v=7t2alSnE2-I","type":"course","why":"4-hour project-based course","free":True}],
    "docker":       [{"title":"Docker Get Started","url":"https://docs.docker.com/get-started/","type":"docs","why":"Official step-by-step guide from zero to multi-container","free":True},
                     {"title":"Play with Docker","url":"https://labs.play-with-docker.com","type":"tutorial","why":"Browser-based playground — no install needed","free":True}],
    "kubernetes":   [{"title":"Kubernetes Tutorials","url":"https://kubernetes.io/docs/tutorials/","type":"tutorial","why":"Official browser-based cluster — zero local setup","free":True},
                     {"title":"KodeKloud K8s","url":"https://kodekloud.com/courses/kubernetes-for-the-absolute-beginners-hands-on/","type":"course","why":"Structured Docker→K8s path","free":True}],
    "aws":          [{"title":"AWS Free Tier","url":"https://aws.amazon.com/free/","type":"tutorial","why":"Deploy real workloads at no cost","free":True},
                     {"title":"AWS Skill Builder","url":"https://skillbuilder.aws","type":"course","why":"Official free foundational courses","free":True}],
    "postgresql":   [{"title":"PostgreSQL Tutorial","url":"https://www.postgresqltutorial.com","type":"tutorial","why":"From basics to advanced queries","free":True},
                     {"title":"Use The Index Luke","url":"https://use-the-index-luke.com","type":"docs","why":"Deep dive into query optimisation","free":True}],
    "redis":        [{"title":"Redis Docs","url":"https://redis.io/docs/","type":"docs","why":"Official docs with examples","free":True},
                     {"title":"Redis University","url":"https://university.redis.com","type":"course","why":"Free structured courses","free":True}],
    "kafka":        [{"title":"Kafka Quickstart","url":"https://kafka.apache.org/quickstart","type":"docs","why":"Official quickstart","free":True},
                     {"title":"Kafka Definitive Guide","url":"https://www.confluent.io/resources/kafka-the-definitive-guide/","type":"course","why":"Free comprehensive book","free":True}],
    "django":       [{"title":"Django Tutorial","url":"https://docs.djangoproject.com/en/stable/intro/tutorial01/","type":"docs","why":"Official tutorial — builds a real app","free":True}],
    "react":        [{"title":"React Official Docs","url":"https://react.dev","type":"docs","why":"Interactive examples","free":True}],
    "system design":[{"title":"System Design Primer","url":"https://github.com/donnemartin/system-design-primer","type":"tutorial","why":"Top GitHub resource for trade-off reasoning","free":True},
                     {"title":"Designing Data-Intensive Apps","url":"https://dataintensive.net","type":"course","why":"Best book for real intuition","free":False}],
    "default":      [{"title":"freeCodeCamp","url":"https://www.freecodecamp.org","type":"course","why":"Free courses on most tech topics","free":True},
                     {"title":"The Odin Project","url":"https://www.theodinproject.com","type":"course","why":"Free full-stack curriculum","free":True}],
}

def get_resources(skill):
    # type: (str) -> List
    key = skill.lower()
    for k, v in RESOURCE_DB.items():
        if k in key or key in k:
            return v
    return RESOURCE_DB["default"]


def generate_plan(gap_report):
    # type: (Dict) -> Dict
    actionable = gap_report["gaps"]["critical"] + gap_report["gaps"]["moderate"]
    if not actionable:
        return {
            "message": "No significant gaps — candidate is well matched!",
            "skills": [], "total_weeks": 0, "sequence": [],
            "candidate_name": gap_report["candidate_name"],
            "role_title": gap_report["role_title"],
        }
    gaps_lines = []
    for g in actionable:
        gaps_lines.append("- " + g["skill"] + " [" + g["jd_importance"] + "] (" + g["classification"] + "): " + g["note"])
    gaps_text = "\n".join(gaps_lines)

    prompt = (
        "Create a learning plan for " + gap_report["candidate_name"] +
        " applying for: " + gap_report["role_title"] + "\n\n"
        "Gaps:\n" + gaps_text + "\n\n"
        "Return ONLY JSON:\n"
        '{"total_weeks":7,"weekly_hours":10,"sequence":["A","B"],'
        '"skills":[{"skill":"Kubernetes","priority":"critical",'
        '"objective":"Deploy containerised app to K8s","estimated_hours":25,'
        '"note":"Not in resume"}]}\n\n'
        "Be realistic with hours. Sequence in logical order."
    )
    raw = call_llm(prompt, max_tokens=2000)
    plan = parse_json(raw)
    for sk in plan.get("skills", []):
        sk["resources"] = get_resources(sk["skill"])
    plan["candidate_name"] = gap_report["candidate_name"]
    plan["role_title"]     = gap_report["role_title"]
    plan["generated_at"]   = datetime.utcnow().isoformat()
    return plan


# ═══════════════════════════════════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def root():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL, "api_key_set": bool(os.environ.get("GROQ_API_KEY", ""))}


@app.post("/api/start")
async def start_session(
    jd_text: str       = Form(...),
    resume:  UploadFile = File(...),
):
    resume_bytes = await resume.read()
    filename     = resume.filename or "resume.txt"

    # Parse PDF or plain text
    if filename.lower().endswith(".pdf"):
        resume_text = extract_text_from_pdf(resume_bytes)
        if not resume_text.strip():
            raise HTTPException(400, "Could not extract text from PDF. Try a text-based PDF.")
    else:
        resume_text = resume_bytes.decode("utf-8", errors="ignore")

    extracted  = extract_skills(jd_text, resume_text)
    session_id = str(uuid.uuid4())
    session = {
        "session_id":     session_id,
        "candidate_name": extracted.get("candidate_name", "Candidate"),
        "role_title":     extracted.get("role_title", "the role"),
        "queue":          build_queue(extracted.get("skill_matrix", [])),
        "idx":            0,
        "phase":          "assessing",
    }
    advance(session)
    sessions[session_id] = session

    sk = current_skill(session)
    if sk is None:
        return {"session_id": session_id, "phase": "complete"}

    question = generate_question(sk)
    sk["conversation"].append({"role": "ai", "content": question})
    sk["questions_asked"] += 1
    sk["status_assess"]    = "probing"

    assessed  = sum(1 for s in session["queue"] if s["status_assess"] == "assessed")
    to_assess = sum(1 for s in session["queue"] if s["status_assess"] != "skipped")

    return {
        "session_id":           session_id,
        "phase":                "assessing",
        "candidate_name":       session["candidate_name"],
        "role_title":           session["role_title"],
        "question":             question,
        "skill_being_assessed": sk["skill"],
        "progress":             {"assessed": assessed, "total": to_assess},
    }


@app.post("/api/chat")
async def chat(request: Request):
    body       = await request.json()
    session_id = body.get("session_id")
    message    = body.get("message")
    if not session_id or not message:
        raise HTTPException(400, "session_id and message required")

    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    sk = current_skill(session)
    if sk is None or session["phase"] == "complete":
        return {"phase": "complete"}

    last_q = next((m["content"] for m in reversed(sk["conversation"]) if m["role"] == "ai"), "")
    ev     = evaluate_response(sk["skill"], last_q, message)
    sk["score"]         = ev["score"]
    sk["assessor_note"] = ev["note"]
    sk["conversation"].append({"role": "user", "content": message})

    needs_followup = ev["score"] < 0.6 and sk["questions_asked"] < MAX_Q_PER_SKILL
    if not needs_followup:
        sk["status_assess"] = "assessed"
        advance(session)
        sk = current_skill(session)

    if sk is None:
        session["phase"] = "complete"
        return {"phase": "complete", "session_id": session_id}

    question = generate_question(sk)
    sk["conversation"].append({"role": "ai", "content": question})
    sk["questions_asked"] += 1
    sk["status_assess"]    = "probing"

    assessed  = sum(1 for s in session["queue"] if s["status_assess"] == "assessed")
    to_assess = sum(1 for s in session["queue"] if s["status_assess"] != "skipped")

    return {
        "phase":                "assessing",
        "question":             question,
        "skill_being_assessed": sk["skill"],
        "progress":             {"assessed": assessed, "total": to_assess},
    }


@app.get("/api/results/{session_id}")
def results(session_id):
    # type: (str) -> Dict
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    gap_report = build_gap_report(session)
    plan       = generate_plan(gap_report)
    return {"gap_report": gap_report, "learning_plan": plan}
