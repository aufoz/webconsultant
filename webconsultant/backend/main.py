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
DB_PATH = "/tmp/webconsultant.db"  # /tmp всегда чистый при рестарте
 
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
            url TEXT NOT NULL, title TEXT, content TEXT, section TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT NOT NULL,
            session_id TEXT, role TEXT, content TEXT, created_at TEXT)""")
        db.commit()
 
init_db()
 
def make_site_id(url): return hashlib.md5(url.encode()).hexdigest()[:12]
def clean(t): return re.sub(r'\s+', ' ', t).strip()
def get_domain(url): return urlparse(url).netloc
def tokenize(t): return re.findall(r'[a-zA-Zа-яА-ЯёЁ]{2,}', t.lower())
 
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
 
def scrape(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script","style","nav","footer","header","aside","form","iframe","noscript"]): t.decompose()
        title = clean(soup.title.string) if soup.title and soup.title.string else url
        meta = soup.find("meta", attrs={"name":"description"})
        desc = meta.get("content","") if meta else ""
        body = soup.find("main") or soup.find("article") or soup.find(id=re.compile(r"content|main",re.I)) or soup.body
        text = clean(body.get_text(" ") if body else "")
        if desc: text = desc + " " + text
        text = text[:5000]
        domain = get_domain(url)
        links = set()
        for a in soup.find_all("a", href=True):
            h = urljoin(url, a["href"]).split("#")[0]
            if get_domain(h) == domain and h.startswith("http"):
                if not any(h.endswith(e) for e in [".pdf",".jpg",".png",".zip",".xml",".svg"]):
                    links.add(h)
        path = urlparse(url).path.strip("/")
        section = path.split("/")[0].capitalize() if path else "Home"
        return {"url":url,"title":title,"content":text,"section":section,"links":list(links)[:25]}
    except Exception as e:
        return {"url":url,"title":url,"content":"","section":"","links":[]}
 
def crawl(start_url, max_pages=12):
    visited, queue, pages = set(), [start_url], []
    domain = get_domain(start_url)
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in visited: continue
        visited.add(url)
        p = scrape(url)
        if p["content"]: pages.append(p)
        for l in p["links"]:
            if l not in visited and get_domain(l) == domain: queue.append(l)
    return pages
 
def search(site_id, query, k=5):
    with get_db() as db:
        rows = db.execute("SELECT title,content,url,section FROM pages WHERE site_id=?", (site_id,)).fetchall()
    docs = [dict(r) for r in rows]
    if not docs: return []
    q_tok = tokenize(query)
    N = len(docs)
    scored = []
    for doc in docs:
        tokens = tokenize(doc["title"]+" "+doc["content"])
        if not tokens: continue
        tf = Counter(tokens)
        score = 0
        for t in q_tok:
            if t in tf:
                tf_v = tf[t]/len(tokens)
                df = sum(1 for d in docs if t in (d["title"]+" "+d["content"]).lower())
                idf = math.log((N+1)/(df+1))+1
                score += tf_v * idf
        if score > 0: scored.append((score, doc))
    scored.sort(key=lambda x: -x[0])
    return [d for _,d in scored[:k]]
 
def answer(question, chunks, history):
    q = question.lower()
 
    # Greeting
    if any(w in q for w in ["привет","здравствуй","hello","hi","добрый день","добрый вечер","доброе утро"]):
        return "Привет! Я консультант этого сайта. Задайте любой вопрос — я постараюсь помочь! 😊"
 
    if not chunks:
        return "По вашему вопросу ничего не найдено на этом сайте. Попробуйте переформулировать или задать другой вопрос."
 
    q_tok = set(tokenize(question))
 
    # Collect all sentences scored by relevance
    all_sents = []
    for chunk in chunks:
        raw = chunk["title"] + ". " + chunk["content"]
        # Split into sentences
        sents = re.split(r'(?<=[.!?])\s+', raw)
        for s in sents:
            s = s.strip()
            if len(s) < 25: continue
            stok = set(tokenize(s))
            overlap = len(q_tok & stok)
            if overlap > 0:
                all_sents.append((overlap, s, chunk["section"], chunk["url"]))
 
    all_sents.sort(key=lambda x: -x[0])
 
    # Detect intent
    is_price    = any(w in q for w in ["цен","стоим","сколько","price","cost","тариф","платн","бесплатн"])
    is_contact  = any(w in q for w in ["контакт","связ","телефон","email","почт","адрес","contact"])
    is_how      = any(w in q for w in ["как","how","каким","способ","метод"])
    is_what     = any(w in q for w in ["что","чем","what","какой","какая","о чём","расскажи","опиши"])
 
    if is_price:   intro = "По вопросу цен и стоимости:"
    elif is_contact: intro = "Контактная информация:"
    elif is_how:   intro = "Как это работает:"
    elif is_what:  intro = "Вот что об этом известно:"
    else:          intro = "По вашему вопросу:"
 
    if not all_sents:
        # Fallback: return first meaningful sentences from best chunk
        best = chunks[0]["content"]
        sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', best) if len(s.strip()) > 25][:3]
        if sents:
            return intro + "\n\n" + "\n".join("• " + s for s in sents)
        return "Информация по этой теме есть на сайте, но конкретного ответа найти не удалось. Попробуйте уточнить вопрос."
 
    # Pick unique best sentences
    seen, result = set(), []
    for _, s, sec, url in all_sents:
        if s not in seen and len(result) < 4:
            seen.add(s)
            result.append(s)
 
    return intro + "\n\n" + "\n".join("• " + s for s in result)
 
class ScanRequest(BaseModel):
    url: str
 
class ChatRequest(BaseModel):
    site_id: str
    session_id: str
    message: str
    history: list = []
 
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(BASE_DIR, "frontend/index.html"))
 
@app.post("/api/scan")
def scan_site(req: ScanRequest):
    url = req.url.strip()
    if not url.startswith("http"): url = "https://" + url
    site_id = make_site_id(url)
    with get_db() as db:
        ex = db.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        if ex:
            pages = db.execute("SELECT section,title,url FROM pages WHERE site_id=?", (site_id,)).fetchall()
            sections = list({p["section"] for p in pages if p["section"]})
            return {"site_id":site_id,"title":ex["title"],"url":url,"page_count":ex["page_count"],"sections":sections,"cached":True}
    pages = crawl(url)
    if not pages: raise HTTPException(400, "Не удалось просканировать сайт. Проверьте URL.")
    title = pages[0]["title"]
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO sites VALUES (?,?,?,?,?)", (site_id,url,title,datetime.now().isoformat(),len(pages)))
        for p in pages:
            db.execute("INSERT INTO pages (site_id,url,title,content,section) VALUES (?,?,?,?,?)",
                       (site_id,p["url"],p["title"],p["content"],p["section"]))
        db.commit()
    sections = list({p["section"] for p in pages if p["section"]})
    return {"site_id":site_id,"title":title,"url":url,"page_count":len(pages),"sections":sections,"cached":False}
 
@app.post("/api/chat")
def chat(req: ChatRequest):
    with get_db() as db:
        site = db.execute("SELECT * FROM sites WHERE id=?", (req.site_id,)).fetchone()
    if not site: raise HTTPException(404, "Сайт не найден.")
    chunks = search(req.site_id, req.message)
    ans = answer(req.message, chunks, req.history)
    with get_db() as db:
        now = datetime.now().isoformat()
        db.execute("INSERT INTO messages (site_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
                   (req.site_id,req.session_id,"user",req.message,now))
        db.execute("INSERT INTO messages (site_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
                   (req.site_id,req.session_id,"assistant",ans,now))
        db.commit()
    sources = [{"title":c["title"],"url":c["url"]} for c in chunks]
    return {"answer":ans,"sources":sources}
 
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
    return {"ok":True}
 
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
        secs[s] = secs.get(s,0)+1
    return {"title":site["title"],"url":site["url"],"page_count":site["page_count"],
            "question_count":cnt,"scanned_at":site["scanned_at"],"sections":secs,"pages":[dict(p) for p in pages]}
 
