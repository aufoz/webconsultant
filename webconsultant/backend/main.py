import os
import json
import sqlite3
import hashlib
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- Try to import ollama, fallback to simple keyword search ---
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

app = FastAPI(title="WebConsultant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "frontend/static")), name="static")

DB_PATH = os.path.join(BASE_DIR, "data.db")

# ── DB SETUP ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS sites (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT,
                scanned_at TEXT,
                page_count INTEGER DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                content TEXT,
                section TEXT,
                FOREIGN KEY(site_id) REFERENCES sites(id)
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                session_id TEXT,
                role TEXT,
                content TEXT,
                created_at TEXT
            )
        """)
        db.commit()

init_db()

# ── HELPERS ───────────────────────────────────────────────────────────────────
def make_site_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

def clean_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def get_domain(url: str) -> str:
    return urlparse(url).netloc

# ── SCRAPER ───────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WebConsultantBot/1.0)"
}
MAX_PAGES = 15
MAX_CONTENT_LEN = 3000

def scrape_page(url: str) -> dict:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else url
        meta_desc = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            meta_desc = meta.get("content", "")

        # Extract main content
        main = soup.find("main") or soup.find("article") or soup.find("div", class_=re.compile(r"content|main|body", re.I))
        body = main or soup.body
        text = clean_text(body.get_text(separator="\n") if body else "") if body else ""
        if meta_desc:
            text = meta_desc + "\n\n" + text
        text = text[:MAX_CONTENT_LEN]

        # Collect internal links
        base_domain = get_domain(url)
        links = set()
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            if get_domain(href) == base_domain and href.startswith("http"):
                # Skip anchors, files, etc.
                p = urlparse(href)
                if not any(p.path.endswith(ext) for ext in [".pdf",".jpg",".png",".zip",".xml"]):
                    links.add(href.split("#")[0])

        # Determine section
        path = urlparse(url).path.strip("/")
        section = path.split("/")[0].capitalize() if path else "Home"

        return {
            "url": url,
            "title": clean_text(title),
            "content": text,
            "section": section,
            "links": list(links)[:30]
        }
    except Exception as e:
        return {"url": url, "title": url, "content": "", "section": "", "links": [], "error": str(e)}


def crawl_site(start_url: str) -> list[dict]:
    """BFS crawl up to MAX_PAGES pages."""
    visited = set()
    queue = [start_url]
    pages = []
    base_domain = get_domain(start_url)

    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        page = scrape_page(url)
        if page.get("content"):
            pages.append(page)

        # Add new links to queue
        for link in page.get("links", []):
            if link not in visited and get_domain(link) == base_domain:
                queue.append(link)

    return pages


# ── SEARCH (keyword-based RAG fallback) ───────────────────────────────────────
def search_knowledge(site_id: str, query: str, top_k: int = 4) -> list[dict]:
    words = re.findall(r'\w+', query.lower())
    with get_db() as db:
        rows = db.execute(
            "SELECT title, content, url, section FROM pages WHERE site_id = ?", (site_id,)
        ).fetchall()

    scored = []
    for row in rows:
        text = (row["title"] + " " + row["content"]).lower()
        score = sum(text.count(w) for w in words)
        if score > 0:
            scored.append((score, dict(row)))

    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:top_k]]


# ── AI ANSWER ─────────────────────────────────────────────────────────────────
def build_context(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        parts.append(f"[{c['section']} — {c['title']}]\n{c['content'][:800]}")
    return "\n\n---\n\n".join(parts)


def answer_with_ollama(question: str, context: str, history: list) -> str:
    messages = []
    messages.append({
        "role": "system",
        "content": (
            "Ты — AI-консультант сайта. Отвечай на вопросы пользователей, "
            "опираясь на базу знаний ниже. Будь вежлив и конкретен. "
            "Если ответа нет в базе — скажи честно.\n\n"
            f"БАЗА ЗНАНИЙ:\n{context}"
        )
    })
    for msg in history[-6:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": question})

    resp = ollama.chat(model="llama3.2", messages=messages)
    return resp["message"]["content"]


def answer_simple(question: str, context: str, site_title: str) -> str:
    """Fallback: keyword extraction without AI."""
    if not context:
        return f"К сожалению, по вашему вопросу информации на сайте {site_title} не найдено. Попробуйте переформулировать."

    # Extract relevant sentences
    sentences = re.split(r'[.!?\n]', context)
    words = set(re.findall(r'\w+', question.lower()))
    relevant = []
    for s in sentences:
        s = s.strip()
        if len(s) > 30 and any(w in s.lower() for w in words):
            relevant.append(s)

    if relevant:
        answer = ". ".join(relevant[:4]) + "."
        return f"На основе информации с сайта:\n\n{answer}\n\nЕсли нужны подробности, уточните вопрос."
    else:
        # Return first chunk summary
        first = context[:500].strip()
        return f"Вот что удалось найти по вашему запросу:\n\n{first}...\n\nУточните вопрос для более точного ответа."


# ── API MODELS ────────────────────────────────────────────────────────────────
class ScanRequest(BaseModel):
    url: str

class ChatRequest(BaseModel):
    site_id: str
    session_id: str
    message: str
    history: list = []


# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(BASE_DIR, "frontend/index.html"))


@app.post("/api/scan")
def scan_site(req: ScanRequest):
    url = req.url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    site_id = make_site_id(url)

    # Check if already scanned (cache)
    with get_db() as db:
        existing = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if existing:
            pages = db.execute(
                "SELECT section, title, url FROM pages WHERE site_id = ?", (site_id,)
            ).fetchall()
            sections = list({p["section"] for p in pages if p["section"]})
            return {
                "site_id": site_id,
                "title": existing["title"],
                "url": url,
                "page_count": existing["page_count"],
                "sections": sections,
                "cached": True
            }

    # Crawl
    pages = crawl_site(url)
    if not pages:
        raise HTTPException(400, "Не удалось просканировать сайт. Проверьте URL.")

    site_title = pages[0]["title"] if pages else get_domain(url)

    # Save to DB
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO sites VALUES (?,?,?,?,?)",
            (site_id, url, site_title, datetime.now().isoformat(), len(pages))
        )
        for p in pages:
            db.execute(
                "INSERT INTO pages (site_id, url, title, content, section) VALUES (?,?,?,?,?)",
                (site_id, p["url"], p["title"], p["content"], p["section"])
            )
        db.commit()

    sections = list({p["section"] for p in pages if p["section"]})
    return {
        "site_id": site_id,
        "title": site_title,
        "url": url,
        "page_count": len(pages),
        "sections": sections,
        "cached": False
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    # Check site exists
    with get_db() as db:
        site = db.execute("SELECT * FROM sites WHERE id = ?", (req.site_id,)).fetchone()
    if not site:
        raise HTTPException(404, "Сайт не найден. Сначала просканируйте его.")

    # Search relevant chunks
    chunks = search_knowledge(req.site_id, req.message)
    context = build_context(chunks)

    # Generate answer
    if OLLAMA_AVAILABLE:
        try:
            answer = answer_with_ollama(req.message, context, req.history)
        except Exception:
            answer = answer_simple(req.message, context, site["title"])
    else:
        answer = answer_simple(req.message, context, site["title"])

    # Save to history
    with get_db() as db:
        now = datetime.now().isoformat()
        db.execute(
            "INSERT INTO messages (site_id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (req.site_id, req.session_id, "user", req.message, now)
        )
        db.execute(
            "INSERT INTO messages (site_id, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (req.site_id, req.session_id, "assistant", answer, now)
        )
        db.commit()

    sources = [{"title": c["title"], "url": c["url"]} for c in chunks]
    return {"answer": answer, "sources": sources}


@app.get("/api/sites")
def list_sites():
    with get_db() as db:
        rows = db.execute("SELECT id, url, title, scanned_at, page_count FROM sites ORDER BY scanned_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.delete("/api/sites/{site_id}")
def delete_site(site_id: str):
    with get_db() as db:
        db.execute("DELETE FROM pages WHERE site_id = ?", (site_id,))
        db.execute("DELETE FROM messages WHERE site_id = ?", (site_id,))
        db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        db.commit()
    return {"ok": True}


@app.get("/api/stats/{site_id}")
def site_stats(site_id: str):
    with get_db() as db:
        site = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        pages = db.execute("SELECT section, title, url FROM pages WHERE site_id = ?", (site_id,)).fetchall()
        msg_count = db.execute(
            "SELECT COUNT(*) as c FROM messages WHERE site_id = ? AND role = 'user'", (site_id,)
        ).fetchone()["c"]
    if not site:
        raise HTTPException(404)
    sections = {}
    for p in pages:
        s = p["section"] or "Other"
        sections[s] = sections.get(s, 0) + 1
    return {
        "title": site["title"],
        "url": site["url"],
        "page_count": site["page_count"],
        "question_count": msg_count,
        "scanned_at": site["scanned_at"],
        "sections": sections,
        "pages": [dict(p) for p in pages]
    }
