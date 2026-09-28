"""
agent-bridge: buzón MCP para que dos agentes (p. ej. Codex frontend y Codex backend),
cada uno en su propia computadora, se hagan preguntas entre sí.

- Transporte: Streamable HTTP en http://<host>:8000/mcp
- Autenticación: un token Bearer por agente (variable AGENT_TOKENS).
  La identidad del agente sale del token, no de lo que el agente diga ser.
- Almacenamiento: SQLite en /data/bridge.db (volumen de Docker)
- Avisos: notificación push por ntfy (NTFY_TOPICS) al llegar una pregunta y al llegar su respuesta
"""
import os
import secrets
import json
import sqlite3
import threading
import time
import urllib.request

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from starlette.responses import JSONResponse

DB_PATH = os.environ.get("DB_PATH", "/data/bridge.db")
PORT = int(os.environ.get("PORT", "8000"))
MAX_TEXT = 20_000  # caracteres máximos por pregunta/respuesta/contexto
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "").strip()  # solo si tu servidor ntfy pide autenticación
NTFY_INCLUDE_TEXT = os.environ.get("NTFY_INCLUDE_TEXT", "false").strip().lower() in ("1", "true", "yes", "si", "sí")


# ---------------------------------------------------------------- tokens ---
def load_tokens() -> list[tuple[str, str]]:
    """AGENT_TOKENS="frontend:TOKEN1,backend:TOKEN2" -> [(token, agente), ...]"""
    pairs = []
    for item in os.environ.get("AGENT_TOKENS", "").split(","):
        item = item.strip()
        if not item:
            continue
        name, _, token = item.partition(":")
        name, token = name.strip().lower(), token.strip()
        if not name or len(token) < 24:
            raise SystemExit(f"[agent-bridge] Token ausente o demasiado corto para '{name}' (mínimo 24 caracteres).")
        pairs.append((token, name))
    if len({n for _, n in pairs}) < 2:
        raise SystemExit("[agent-bridge] AGENT_TOKENS debe definir al menos dos agentes distintos.")
    return pairs


TOKENS = load_tokens()
AGENTS = sorted({name for _, name in TOKENS})


def load_ntfy_topics() -> dict[str, str]:
    """NTFY_TOPICS="frontend:TEMA1,backend:TEMA2" -> {agente: tema}"""
    topics = {}
    for item in os.environ.get("NTFY_TOPICS", "").split(","):
        name, _, topic = item.strip().partition(":")
        name, topic = name.strip().lower(), topic.strip()
        if name and topic:
            if name not in AGENTS:
                raise SystemExit(f"[agent-bridge] NTFY_TOPICS menciona '{name}', que no está en AGENT_TOKENS.")
            topics[name] = topic
    return topics


NTFY_TOPICS = load_ntfy_topics()


def agent_for_header(auth_header: str) -> str | None:
    if not auth_header.lower().startswith("bearer "):
        return None
    given = auth_header[7:].strip()
    for token, name in TOKENS:
        if secrets.compare_digest(given, token):
            return name
    return None


def current_agent(ctx: Context) -> str:
    request = getattr(ctx.request_context, "request", None)
    agent = agent_for_header(request.headers.get("authorization", "")) if request else None
    if not agent:
        raise PermissionError("No autenticado.")
    return agent


def log(agent: str, action: str, detail: str = "") -> None:
    print(f"[agent-bridge] {time.strftime('%Y-%m-%d %H:%M:%S')} {agent} -> {action} {detail}", flush=True)


def short(text: str, n: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def send_ntfy(agent: str, title: str, body: str, what: str) -> None:
    """Envía un aviso por ntfy al tema de `agent` en segundo plano; si falla, solo se registra en el log."""
    topic = NTFY_TOPICS.get(agent)
    if not topic:
        return
    payload = json.dumps({"topic": topic, "title": title, "message": body, "priority": 4}).encode()
    headers = {"Content-Type": "application/json"}
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"

    def send() -> None:
        try:
            req = urllib.request.Request(NTFY_SERVER, data=payload, headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:  # el aviso nunca debe romper la herramienta que lo envía
            log(agent, "ntfy_error", f"{what}: {e}")

    threading.Thread(target=send, daemon=True).start()


def notify_new_question(to_agent: str, from_agent: str, msg_id: int, question: str) -> None:
    with db() as c:
        n = c.execute("SELECT COUNT(*) FROM messages WHERE to_agent = ? AND status = 'pending'", (to_agent,)).fetchone()[0]
    title = f"📬 {n} pregunta{'s' if n != 1 else ''} pendiente{'s' if n != 1 else ''}"
    body = f"#{msg_id} de {from_agent}"
    body += f": {short(question)}" if NTFY_INCLUDE_TEXT else ". Abre Codex y dile \"revisa el buzón\"."
    send_ntfy(to_agent, title, body, f"pregunta #{msg_id}")


def notify_answer(asker: str, answerer: str, msg_id: int, answer: str) -> None:
    title = f"✅ {answerer} respondió tu pregunta #{msg_id}"
    body = short(answer) if NTFY_INCLUDE_TEXT else "Abre Codex y dile \"revisa las respuestas\"."
    send_ntfy(asker, title, body, f"respuesta #{msg_id}")


def check_len(**fields: str) -> None:
    for name, value in fields.items():
        if value and len(value) > MAX_TEXT:
            raise ValueError(f"'{name}' supera {MAX_TEXT} caracteres.")


# ------------------------------------------------------------------- db ---
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                from_agent  TEXT NOT NULL,
                to_agent    TEXT NOT NULL,
                question    TEXT NOT NULL,
                context     TEXT,
                answer      TEXT,
                status      TEXT NOT NULL DEFAULT 'pending',
                created_at  REAL NOT NULL,
                answered_at REAL
            )
            """
        )


# ---------------------------------------------------------------- tools ---
mcp = FastMCP("agent-bridge", host="0.0.0.0", port=PORT, stateless_http=True)


@mcp.tool()
def whoami(ctx: Context) -> dict:
    """Dice qué agente eres según tu token y con qué otros agentes puedes hablar."""
    me = current_agent(ctx)
    return {"agent": me, "peers": [a for a in AGENTS if a != me]}


@mcp.tool()
def ask_peer(to_agent: str, question: str, ctx: Context, context: str = "") -> dict:
    """Envía una pregunta a otro agente (usa whoami para ver los nombres válidos).
    Incluye en `context` lo necesario para responder sin adivinar (endpoint, archivo, error...).
    Nunca incluyas secretos, tokens ni contenido de archivos .env.
    Devuelve el id de la pregunta para consultarla después con get_answer o my_questions."""
    me = current_agent(ctx)
    to_agent = to_agent.strip().lower()
    if to_agent not in AGENTS or to_agent == me:
        raise ValueError(f"Destinatario no válido. Opciones: {[a for a in AGENTS if a != me]}")
    check_len(question=question, context=context)
    with db() as c:
        cur = c.execute(
            "INSERT INTO messages (from_agent, to_agent, question, context, created_at) VALUES (?, ?, ?, ?, ?)",
            (me, to_agent, question, context, time.time()),
        )
        msg_id = cur.lastrowid
    log(me, "ask_peer", f"#{msg_id} para {to_agent}")
    notify_new_question(to_agent, me, msg_id, question)
    return {"id": msg_id, "status": "pending"}


@mcp.tool()
def check_inbox(ctx: Context) -> list[dict]:
    """Devuelve las preguntas pendientes que otros agentes te han hecho.
    No las respondas por tu cuenta: muéstraselas al usuario con una respuesta propuesta
    y espera su confirmación antes de llamar a reply."""
    me = current_agent(ctx)
    with db() as c:
        rows = c.execute(
            "SELECT id, from_agent, question, context, created_at FROM messages "
            "WHERE to_agent = ? AND status = 'pending' ORDER BY id",
            (me,),
        ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def reply(message_id: int, answer: str, ctx: Context) -> dict:
    """Responde a una pregunta pendiente que te hayan hecho a ti.
    Llámala SOLO después de que el usuario haya confirmado explícitamente el texto de `answer`
    (tras preguntarle "¿Envío esta respuesta?"). Si la corrige, envía su versión."""
    me = current_agent(ctx)
    check_len(answer=answer)
    with db() as c:
        cur = c.execute(
            "UPDATE messages SET answer = ?, status = 'answered', answered_at = ? "
            "WHERE id = ? AND to_agent = ? AND status = 'pending'",
            (answer, time.time(), message_id, me),
        )
        if cur.rowcount == 0:
            return {"ok": False, "error": "No tienes una pregunta pendiente con ese id."}
        asker = c.execute("SELECT from_agent FROM messages WHERE id = ?", (message_id,)).fetchone()[0]
    log(me, "reply", f"#{message_id}")
    notify_answer(asker, me, message_id, answer)
    return {"ok": True, "id": message_id}


@mcp.tool()
def my_questions(ctx: Context, only_answered: bool = False) -> list[dict]:
    """Lista las preguntas que tú has enviado, con su estado y respuesta si ya la hay."""
    me = current_agent(ctx)
    sql = ("SELECT id, to_agent, question, status, answer, created_at, answered_at "
           "FROM messages WHERE from_agent = ?")
    if only_answered:
        sql += " AND status = 'answered'"
    sql += " ORDER BY id DESC LIMIT 50"
    with db() as c:
        return [dict(r) for r in c.execute(sql, (me,)).fetchall()]


@mcp.tool()
def get_answer(message_id: int, ctx: Context) -> dict:
    """Consulta una pregunta concreta (solo si la enviaste o te la enviaron a ti)."""
    me = current_agent(ctx)
    with db() as c:
        row = c.execute(
            "SELECT * FROM messages WHERE id = ? AND (from_agent = ? OR to_agent = ?)",
            (message_id, me, me),
        ).fetchone()
    return dict(row) if row else {"error": "No encontrada."}


# ------------------------------------------------------------ http auth ---
class BearerAuth:
    """Rechaza con 401 cualquier petición HTTP sin un token válido (excepto /health)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/health":
            return await JSONResponse({"ok": True})(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if agent_for_header(headers.get("authorization", "")) is None:
            return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        await self.app(scope, receive, send)


if __name__ == "__main__":
    init_db()
    print(f"[agent-bridge] Agentes registrados: {AGENTS}", flush=True)
    print(f"[agent-bridge] Avisos ntfy ({NTFY_SERVER}) para: {sorted(NTFY_TOPICS) or 'nadie'}", flush=True)
    app = BearerAuth(mcp.streamable_http_app())
    uvicorn.run(app, host="0.0.0.0", port=PORT, proxy_headers=True, forwarded_allow_ips="*")
