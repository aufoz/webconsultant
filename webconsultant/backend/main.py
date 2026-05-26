import os
import sqlite3
import hashlib
import re
import math
from datetime import datetime
from urllib.parse import urljoin, urlparse
from collections import Counter
 
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
 
app = FastAPI(title="WebConsultant")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
 
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "frontend/static")), name="static")
DB_PATH = os.path.join(BASE_DIR, "data.db")
 
# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
 
def init_db():
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS sites (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT,
            scanned_at TEXT, page_count INTEGER DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL,
            url TEXT NOT NULL, title TEXT, content TEXT, section TEXT,
            FOREIGN KEY(site_id) REFERENCES sites(id))""")
        db.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL,
            session_id TEXT, role TEXT, content TEXT, created_at TEXT)""")
        db.commit()
 
init_db()
 
# ── HELPERS ───────────────────────────────────────────────────────────────────
def make_site_id(url): return hashlib.md5(url.encode()).hexdigest()[:12]
def clean_text(text):
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
def get_domain(url): return urlparse(url).netloc
 
# ── SCRAPER ───────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WebConsultantBot/1.0)"}
MAX_PAGES = 15
 
def scrape_page(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script","style","nav","footer","header","aside","form","iframe"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title else url
        meta_desc = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta: meta_desc = meta.get("content", "")
        main = soup.find("main") or soup.find("article") or soup.body
        text = clean_text(main.get_text(separator=" ") if main else "")
        if meta_desc: text = meta_desc + " " + text
        text = text[:4000]
        base_domain = get_domain(url)
        links = set()
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            if get_domain(href) == base_domain and href.startswith("http"):
                p = urlparse(href)
                if not any(p.path.endswith(e) for e in [".pdf",".jpg",".png",".zip",".xml"]):
                    links.add(href.split("#")[0])
        path = urlparse(url).path.strip("/")
        section = path.split("/")[0].capitalize() if path else "Home"
        return {"url": url, "title": clean_text(title), "content": text, "section": section, "links": list(links)[:30]}
    except Exception as e:
        return {"url": url, "title": url, "content": "", "section": "", "links": [], "error": str(e)}
 
def crawl_site(start_url):
    visited, queue, pages = set(), [start_url], []
    base_domain = get_domain(start_url)
    while queue and len(pages) < MAX_PAGES:
        url = queue.pop(0)
        if url in visited: continue
        visited.add(url)
        page = scrape_page(url)
        if page.get("content"): pages.append(page)
        for link in page.get("links", []):
            if link not in visited and get_domain(link) == base_domain:
                queue.append(link)
    return pages
 
# ── TF-IDF SEARCH ─────────────────────────────────────────────────────────────
def tokenize(text):
    return re.findall(r'[a-zA-Zа-яА-ЯёЁ]{2,}', text.lower())
 
def tfidf_search(site_id, query, top_k=5):
    with get_db() as db:
        rows = db.execute("SELECT title, content, url, section FROM pages WHERE site_id=?", (site_id,)).fetchall()
    if not rows: return []
    
    docs = [dict(r) for r in rows]
    q_tokens = tokenize(query)
    
    # TF-IDF scoring
    N = len(docs)
    scored = []
    for doc in docs:
        text = doc["title"] + " " + doc["content"]
        tokens = tokenize(text)
        if not tokens: continue
        tf = Counter(tokens)
        score = 0
        for t in q_tokens:
            if t in tf:
                # TF
                tf_score = tf[t] / len(tokens)
                # IDF
                df = sum(1 for d in docs if t in tokenize(d["title"] + " " + d["content"]))
                idf = math.log((N + 1) / (df + 1)) + 1
                score += tf_score * idf
        if score > 0:
            scored.append((score, doc))
    
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:top_k]]
 
# ── SMART ANSWER ENGINE ───────────────────────────────────────────────────────
def extract_sentences(text):
    sents = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sents if len(s.strip()) > 20]
 
def score_sentence(sent, q_tokens):
    s_tokens = set(tokenize(sent))
    return sum(1 for t in q_tokens if t in s_tokens)
 
def generate_answer(question, chunks, history):
    if not chunks:
        return "К сожалению, по вашему вопросу ничего не найдено на этом сайте. Попробуйте переформулировать вопрос."
    
    q_tokens = tokenize(question)
    
    # Detect question type
    q_lower = question.lower()
    is_what = any(w in q_lower for w in ["что","чем","what","which","какой","какая","какие","о чём","о чем"])
    is_how = any(w in q_lower for w in ["как","how","каким образом","способ"])
    is_where = any(w in q_lower for w in ["где","where","куда","откуда"])
    is_who = any(w in q_lower for w in ["кто","who","чья","чьё"])
    is_price = any(w in q_lower for w in ["цена","цены","стоимость","сколько стоит","price","cost","тариф"])
    is_contact = any(w in q_lower for w in ["контакт","связаться","телефон","email","почта","contact","адрес"])
    is_greeting = any(w in q_lower for w in ["привет","здравствуй","hello","hi","добрый"])
 
    if is_greeting:
        site_name = chunks[0].get("section","") if chunks else ""
        return f"Привет! Я AI-консультант этого сайта. Готов ответить на ваши вопросы. Чем могу помочь?"
 
    # Collect best sentences from all chunks
    all_sentences = []
    for chunk in chunks:
        text = chunk["title"] + ". " + chunk["content"]
        sents = extract_sentences(text)
        for s in sents:
            sc = score_sentence(s, q_tokens)
            if sc > 0:
                all_sentences.append((sc, s, chunk))
    
    all_sentences.sort(key=lambda x: -x[0])
    best = all_sentences[:6]
    
    if not best:
        # Return general info about the site
        first = chunks[0]
        sents = extract_sentences(first["content"])[:3]
        if sents:
            return "Вот что я нашёл на сайте:\n\n" + " ".join(sents)
        return "На сайте есть информация по этой теме, но точного ответа найти не удалось. Попробуйте уточнить вопрос."
 
    # Build coherent answer
    seen = set()
    unique_sents = []
    for _, s, chunk in best:
        if s not in seen:
            seen.add(s)
            unique_sents.append((s, chunk))
 
    # Intro phrase based on question type
    if is_price:
        intro = "По поводу цен и стоимости:"
    elif is_how:
        intro = "Вот как это работает:"
    elif is_where:
        intro = "По поводу местонахождения:"
    elif is_who:
        intro = "Вот что известно:"
    elif is_contact:
        intro = "Контактная информация:"
    elif is_what:
        intro = "Вот что об этом известно:"
    else:
        intro = "По вашему вопросу нашёл следующее:"
 
    answer_parts = [intro, ""]
    for s, _ in unique_sents[:4]:
        answer_parts.append("• " + s)
    
    # Add source hint
    sources_used = list({c["section"] for _, c in unique_sents[:4] if c.get("section")})
    if sources_used:
        answer_parts.append(f"\n(Источник: раздел «{', '.join(sources_used[:2])}»)")
 
    return "\n".join(answer_parts)
 
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
    if not url.startswith("http"): url = "https://" + url
    site_id = make_site_id(url)
    with get_db() as db:
        existing = db.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        if existing:
            pages = db.execute("SELECT section, title, url FROM pages WHERE site_id=?", (site_id,)).fetchall()
            sections = list({p["section"] for p in pages if p["section"]})
            return {"site_id": site_id, "title": existing["title"], "url": url,
                    "page_count": existing["page_count"], "sections": sections, "cached": True}
    pages = crawl_site(url)
    if not pages: raise HTTPException(400, "Не удалось просканировать сайт.")
    site_title = pages[0]["title"] if pages else get_domain(url)
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO sites VALUES (?,?,?,?,?)",
                   (site_id, url, site_title, datetime.now().isoformat(), len(pages)))
        for p in pages:
            db.execute("INSERT INTO pages (site_id,url,title,content,section) VALUES (?,?,?,?,?)",
                       (site_id, p["url"], p["title"], p["content"], p["section"]))
        db.commit()
    sections = list({p["section"] for p in pages if p["section"]})
    return {"site_id": site_id, "title": site_title, "url": url,
            "page_count": len(pages), "sections": sections, "cached": False}
 
@app.post("/api/chat")
def chat(req: ChatRequest):
    with get_db() as db:
        site = db.execute("SELECT * FROM sites WHERE id=?", (req.site_id,)).fetchone()
    if not site: raise HTTPException(404, "Сайт не найден.")
    chunks = tfidf_search(req.site_id, req.message)
    answer = generate_answer(req.message, chunks, req.history)
    with get_db() as db:
        now = datetime.now().isoformat()
        db.execute("INSERT INTO messages (site_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
                   (req.site_id, req.session_id, "user", req.message, now))
        db.execute("INSERT INTO messages (site_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
                   (req.site_id, req.session_id, "assistant", answer, now))
        db.commit()
    sources = [{"title": c["title"], "url": c["url"]} for c in chunks]
    return {"answer": answer, "sources": sources}
 
@app.get("/api/sites")
def list_sites():
    with get_db() as db:
        rows = db.execute("SELECT id,url,title,scanned_at,page_count FROM sites ORDER BY scanned_at DESC").fetchall()
    return [dict(r) for r in rows]
 
@app.delete("/api/sites/{site_id}")
def delete_site(site_id: str):
    with get_db() as db:
        db.execute("DELETE FROM pages WHERE site_id=?", (site_id,))
        db.execute("DELETE FROM messages WHERE site_id=?", (site_id,))
        db.execute("DELETE FROM sites WHERE id=?", (site_id,))
        db.commit()
    return {"ok": True}
 
@app.get("/api/stats/{site_id}")
def site_stats(site_id: str):
    with get_db() as db:
        site = db.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        pages = db.execute("SELECT section,title,url FROM pages WHERE site_id=?", (site_id,)).fetchall()
        msg_count = db.execute("SELECT COUNT(*) as c FROM messages WHERE site_id=? AND role='user'", (site_id,)).fetchone()["c"]
    if not site: raise HTTPException(404)
    sections = {}
    for p in pages:
        s = p["section"] or "Other"
        sections[s] = sections.get(s, 0) + 1
    return {"title": site["title"], "url": site["url"], "page_count": site["page_count"],
            "question_count": msg_count, "scanned_at": site["scanned_at"],
            "sections": sections, "pages": [dict(p) for p in pages]}
