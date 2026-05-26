import os, sqlite3, hashlib, re, math
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
DB_PATH = "/tmp/wc.db"
 
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
 
def init_db():
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS sites (
            id TEXT PRIMARY KEY, url TEXT, title TEXT, scanned_at TEXT, page_count INTEGER DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT, url TEXT,
            title TEXT, content TEXT, section TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT,
            session_id TEXT, role TEXT, content TEXT, created_at TEXT)""")
        db.commit()
 
init_db()
 
def make_id(url): return hashlib.md5(url.encode()).hexdigest()[:12]
def get_domain(url): return urlparse(url).netloc
 
def fix_encoding(text):
    """Fix broken encoding like â→', ð→emoji etc"""
    try:
        # Try to fix latin1 mis-decoded as utf8
        fixed = text.encode('latin1').decode('utf8')
        return fixed
    except:
        pass
    # Remove non-printable and weird chars
    text = re.sub(r'[^\x20-\x7Eа-яА-ЯёЁ\s\.,!?:;()\-\"\'«»–—/\\@#$%&*+=]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()
 
def clean(text):
    if not text: return ""
    text = fix_encoding(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
 
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru,en;q=0.9",
}
 
def scrape(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        # Force correct encoding detection
        r.encoding = r.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(r.text, "html.parser")
 
        for t in soup(["script","style","nav","footer","header","aside","form","iframe","noscript","svg"]):
            t.decompose()
 
        title = ""
        if soup.title and soup.title.string:
            title = clean(soup.title.string)
 
        desc = ""
        for meta in soup.find_all("meta"):
            if meta.get("name","").lower() == "description":
                desc = clean(meta.get("content",""))
                break
 
        # Get main content
        main = (soup.find("main") or soup.find("article") or
                soup.find(id=re.compile(r"content|main|body", re.I)) or
                soup.find(class_=re.compile(r"content|main|body", re.I)) or
                soup.body)
 
        # Extract paragraphs - cleaner than get_text
        paragraphs = []
        if main:
            for tag in main.find_all(["p","h1","h2","h3","h4","li","td","dd"]):
                t = clean(tag.get_text(" "))
                if len(t) > 20:
                    paragraphs.append(t)
 
        content = " ".join(paragraphs[:80])
        if desc:
            content = desc + ". " + content
        content = content[:5000]
 
        # Links
        domain = get_domain(url)
        links = set()
        for a in soup.find_all("a", href=True):
            h = urljoin(url, a["href"]).split("#")[0].split("?")[0]
            if get_domain(h) == domain and h.startswith("http"):
                if not any(h.endswith(e) for e in [".pdf",".jpg",".jpeg",".png",".zip",".xml",".svg",".gif",".css",".js"]):
                    links.add(h)
 
        path = urlparse(url).path.strip("/")
        section = path.split("/")[0].capitalize() if path else "Home"
 
        return {"url": url, "title": title or url, "content": content, "section": section, "links": list(links)[:20]}
    except Exception as e:
        return {"url": url, "title": url, "content": "", "section": "", "links": []}
 
def crawl(start_url, max_pages=12):
    visited, queue, pages = set(), [start_url], []
    domain = get_domain(start_url)
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in visited: continue
        visited.add(url)
        p = scrape(url)
        if p["content"] and len(p["content"]) > 50:
            pages.append(p)
        for link in p["links"]:
            if link not in visited and get_domain(link) == domain:
                queue.append(link)
    return pages
 
def tokenize(t):
    return re.findall(r'[a-zA-Zа-яА-ЯёЁ]{2,}', t.lower())
 
def search(site_id, query, k=5):
    with get_db() as db:
        rows = db.execute("SELECT title,content,url,section FROM pages WHERE site_id=?", (site_id,)).fetchall()
    docs = [dict(r) for r in rows]
    if not docs: return []
    q_tok = tokenize(query)
    if not q_tok: return docs[:k]
    N = len(docs)
    scored = []
    for doc in docs:
        tokens = tokenize((doc["title"] or "") + " " + (doc["content"] or ""))
        if not tokens: continue
        tf = Counter(tokens)
        score = 0.0
        for t in q_tok:
            if t in tf:
                tf_v = tf[t] / len(tokens)
                df = sum(1 for d in docs if t in (d["title"]+" "+d["content"]).lower())
                idf = math.log((N + 1) / (df + 1)) + 1
                score += tf_v * idf
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:k]]
 
def make_answer(question, chunks, history):
    q = question.strip().lower()
 
    # Greetings
    greetings = ["привет","здравствуй","hello","hi ","добрый","hey","hola","вітаю","доброго"]
    if any(q.startswith(g) or q == g.strip() for g in greetings):
        return "Привет! 👋 Я консультант этого сайта. Задайте любой вопрос — отвечу на основе информации с сайта!"
 
    if not chunks:
        return ("Я не нашёл информации по этому вопросу на сайте. "
                "Попробуйте спросить иначе или задайте другой вопрос.")
 
    q_words = set(tokenize(question))
 
    # Score individual sentences
    candidates = []
    for chunk in chunks:
        text = (chunk.get("title","") or "") + ". " + (chunk.get("content","") or "")
        # Split on sentence boundaries
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-ZА-ЯЁ\d])', text)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 30 or len(sent) > 500: continue
            # Skip sentences with too many weird chars
            weird = len(re.findall(r'[^\x20-\x7Eа-яА-ЯёЁ]', sent))
            if weird > len(sent) * 0.1: continue
            s_words = set(tokenize(sent))
            overlap = len(q_words & s_words)
            if overlap > 0:
                candidates.append((overlap, sent, chunk.get("section",""), chunk.get("url","")))
 
    candidates.sort(key=lambda x: -x[0])
 
    # Detect question type for intro
    is_price   = any(w in q for w in ["цен","стоим","сколько","price","cost","тариф","платн","бесплатн","free","paid"])
    is_contact = any(w in q for w in ["контакт","связ","телефон","email","почт","адрес","contact","reach"])
    is_how     = any(w in q for w in ["как","how","каким","способ","установ","install","скачать","download"])
    is_about   = any(w in q for w in ["что","чем","what","какой","какая","расскажи","опиши","about","о сайте","о чём"])
 
    if is_price:    intro = "По вопросу цен:"
    elif is_contact: intro = "Контактная информация:"
    elif is_how:    intro = "Как это сделать:"
    elif is_about:  intro = "Об этом сайте:"
    else:           intro = "По вашему вопросу:"
 
    if not candidates:
        # No sentence matched — return summary of best chunk
        best = chunks[0]
        sents = re.split(r'(?<=[.!?])\s+', best.get("content",""))
        good = [s.strip() for s in sents
                if 30 < len(s.strip()) < 400
                and len(re.findall(r'[^\x20-\x7Eа-яА-ЯёЁ]', s)) < len(s)*0.1][:3]
        if good:
            return intro + "\n\n" + "\n\n".join(good)
        return ("На сайте есть информация по этой теме, но я не смог извлечь конкретный ответ. "
                "Попробуйте уточнить вопрос.")
 
    # Build answer from top unique sentences
    seen, result = set(), []
    for _, sent, sec, url in candidates:
        if sent not in seen and len(result) < 4:
            seen.add(sent)
            result.append(sent)
 
    return intro + "\n\n" + "\n\n".join(result)
 
# ── Models ────────────────────────────────────────────────────────────────────
class ScanRequest(BaseModel):
    url: str
 
class ChatRequest(BaseModel):
    site_id: str
    session_id: str
    message: str
    history: list = []
 
# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(BASE_DIR, "frontend/index.html"))
 
@app.post("/api/scan")
def scan_site(req: ScanRequest):
    url = req.url.strip()
    if not url.startswith("http"): url = "https://" + url
    site_id = make_id(url)
    with get_db() as db:
        ex = db.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        if ex:
            pages = db.execute("SELECT section,title,url FROM pages WHERE site_id=?", (site_id,)).fetchall()
            sections = list({p["section"] for p in pages if p["section"]})
            return {"site_id":site_id,"title":ex["title"],"url":url,
                    "page_count":ex["page_count"],"sections":sections,"cached":True}
    pages = crawl(url)
    if not pages: raise HTTPException(400, "Не удалось просканировать сайт.")
    title = pages[0]["title"]
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO sites VALUES (?,?,?,?,?)",
                   (site_id, url, title, datetime.now().isoformat(), len(pages)))
        for p in pages:
            db.execute("INSERT INTO pages (site_id,url,title,content,section) VALUES (?,?,?,?,?)",
                       (site_id, p["url"], p["title"], p["content"], p["section"]))
        db.commit()
    sections = list({p["section"] for p in pages if p["section"]})
    return {"site_id":site_id,"title":title,"url":url,
            "page_count":len(pages),"sections":sections,"cached":False}
 
@app.post("/api/chat")
def chat(req: ChatRequest):
    with get_db() as db:
        site = db.execute("SELECT * FROM sites WHERE id=?", (req.site_id,)).fetchone()
    if not site: raise HTTPException(404, "Сайт не найден.")
    chunks = search(req.site_id, req.message)
    ans = make_answer(req.message, chunks, req.history)
    with get_db() as db:
        now = datetime.now().isoformat()
        db.execute("INSERT INTO messages (site_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
                   (req.site_id, req.session_id, "user", req.message, now))
        db.execute("INSERT INTO messages (site_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
                   (req.site_id, req.session_id, "assistant", ans, now))
        db.commit()
    sources = [{"title":c["title"],"url":c["url"]} for c in chunks]
    return {"answer": ans, "sources": sources}
 
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
        cnt = db.execute("SELECT COUNT(*) as c FROM messages WHERE site_id=? AND role='user'", (site_id,)).fetchone()["c"]
    if not site: raise HTTPException(404)
    secs = {}
    for p in pages:
        s = p["section"] or "Other"
        secs[s] = secs.get(s, 0) + 1
    return {"title":site["title"],"url":site["url"],"page_count":site["page_count"],
            "question_count":cnt,"scanned_at":site["scanned_at"],
            "sections":secs,"pages":[dict(p) for p in pages]}
 
