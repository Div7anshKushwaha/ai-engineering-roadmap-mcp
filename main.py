from __future__ import annotations

"""AI Engineering Roadmap MCP server.

The server keeps the durable state in SQLite while exposing an async MCP API.
SQLite work is moved to worker threads with asyncio.to_thread; writes are
serialized and independent dashboard reads are gathered concurrently.
"""

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence, TypeVar

try:
    from fastmcp import FastMCP
except ImportError:  # Allows the data layer to be tested without FastMCP installed.
    FastMCP = None  # type: ignore[assignment,misc]

APP_NAME = "AI Engineering Roadmap Coach"
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("ROADMAP_DB_PATH", str(BASE_DIR / "ai_roadmap.db")))
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8000"))
WEAK_SKILL_THRESHOLD = 6
RECENT_SESSION_LIMIT = 10

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(APP_NAME)
T = TypeVar("T")


class SkillStatus(StrEnum):
    REMAINING = "remaining"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class Readiness(StrEnum):
    AWARENESS = "awareness"
    STUDIED = "studied"
    IMPLEMENTED = "implemented"
    PROJECT = "project"
    DEPLOYED = "deployed"
    INTERVIEW_READY = "interview_ready"
    PRODUCTION_READY = "production_ready"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ProjectStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'remaining' CHECK(status IN ('remaining','in_progress','completed')),
    readiness TEXT NOT NULL DEFAULT 'awareness',
    confidence INTEGER NOT NULL DEFAULT 0 CHECK(confidence BETWEEN 0 AND 10),
    priority TEXT NOT NULL DEFAULT 'medium' CHECK(priority IN ('critical','high','medium','low')),
    estimated_hours REAL NOT NULL DEFAULT 0 CHECK(estimated_hours >= 0),
    target_date TEXT,
    next_action TEXT NOT NULL DEFAULT '',
    resource_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_practiced_at TEXT,
    FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS skill_prerequisites (
    skill_id INTEGER NOT NULL,
    prerequisite_skill_id INTEGER NOT NULL,
    PRIMARY KEY(skill_id, prerequisite_skill_id),
    CHECK(skill_id <> prerequisite_skill_id),
    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE,
    FOREIGN KEY(prerequisite_skill_id) REFERENCES skills(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS skill_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER NOT NULL,
    evidence_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS skill_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    old_confidence INTEGER,
    new_confidence INTEGER,
    old_readiness TEXT,
    new_readiness TEXT,
    changed_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS learning_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER NOT NULL,
    session_date TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes > 0),
    topic TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id INTEGER NOT NULL,
    review_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','skipped')),
    confidence INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    completed_at TEXT,
    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    problem_statement TEXT NOT NULL DEFAULT '',
    architecture TEXT NOT NULL DEFAULT '',
    technologies TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'planned',
    github_url TEXT NOT NULL DEFAULT '',
    deployment_url TEXT NOT NULL DEFAULT '',
    evaluation_notes TEXT NOT NULL DEFAULT '',
    limitations TEXT NOT NULL DEFAULT '',
    portfolio_score INTEGER CHECK(portfolio_score BETWEEN 0 AND 10),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_skills (
    project_id INTEGER NOT NULL,
    skill_id INTEGER NOT NULL,
    PRIMARY KEY(project_id, skill_id),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',
    due_date TEXT,
    completed_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value TEXT NOT NULL,
    dataset TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'learning',
    skill_id INTEGER,
    project_id INTEGER,
    priority TEXT NOT NULL DEFAULT 'medium',
    estimated_minutes INTEGER NOT NULL DEFAULT 30 CHECK(estimated_minutes > 0),
    status TEXT NOT NULL DEFAULT 'planned',
    due_date TEXT,
    completed_at TEXT,
    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE SET NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_skills_status ON skills(status);
CREATE INDEX IF NOT EXISTS idx_skills_category ON skills(category_id);
CREATE INDEX IF NOT EXISTS idx_evidence_skill ON skill_evidence(skill_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON learning_sessions(session_date);
CREATE INDEX IF NOT EXISTS idx_reviews_due ON reviews(review_date, status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date, status);
"""

INITIAL_ROADMAP: dict[str, list[str]] = {
    "Programming & Data Foundations": ["Python", "NumPy", "Pandas", "Matplotlib", "SQL", "Git & GitHub", "APIs", "Testing"],
    "Machine Learning": ["Machine Learning Fundamentals", "Linear Regression", "Logistic Regression", "Decision Trees", "Random Forest", "XGBoost", "LightGBM", "CatBoost", "K-Means", "DBSCAN", "Gaussian Mixture Models", "Hyperparameter Optimization", "Scikit-learn"],
    "Deep Learning": ["ANN", "CNN", "RNN", "LSTM", "Attention Mechanism", "Transformers", "TensorFlow", "Keras"],
    "Generative AI": ["LLM Fundamentals", "Prompting", "Embeddings", "Vector Databases", "LangChain", "RAG", "Contextual Compression", "RAGAS Evaluation", "Tool Calling", "Agents"],
    "Agentic AI": ["LangGraph", "Stateful Agents", "Human-in-the-Loop", "Memory", "CRAG", "Self-RAG", "MCP Protocol", "MCP Servers", "MCP Clients", "Remote MCP Servers", "Advanced Agent Architectures"],
    "MLOps": ["Docker", "GitHub Actions", "MLflow", "DVC", "Data Versioning", "Experiment Tracking", "Model Versioning", "CI/CD for ML", "Model Deployment", "Monitoring", "AWS"],
    "Production AI Engineering": ["FastAPI", "Production APIs", "PostgreSQL", "Redis", "Caching", "Authentication", "Observability", "LLM Evaluation", "Production RAG", "Production Agents"],
    "Projects": ["Laptop Price Predictor", "Bank Management System", "SQL Analytics Portfolio", "NLP Emotion Detection", "YouTube RAG Chatbot", "DVC MLOps Pipeline", "MCP-based AI Project", "End-to-End AI Engineering Project"],
}


class Database:
    """Async facade over SQLite. Each operation uses a short-lived connection."""

    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.path = path
        self._write_lock = asyncio.Lock()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    async def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._read_sync, fn)

    def _read_sync(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        with self.connection() as conn:
            return fn(conn)

    async def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        async with self._write_lock:
            return await asyncio.to_thread(self._write_sync, fn)

    def _write_sync(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        with self.connection() as conn:
            return fn(conn)

    async def initialize(self) -> None:
        await self.write(lambda conn: conn.executescript(SCHEMA))

    async def seed(self) -> None:
        def op(conn: sqlite3.Connection) -> None:
            if conn.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
                return
            now = utc_now()
            for order, (category, skills) in enumerate(INITIAL_ROADMAP.items()):
                category_id = conn.execute(
                    "INSERT INTO categories(name, description, sort_order) VALUES(?,?,?)",
                    (category, f"{category} competencies", order),
                ).lastrowid
                conn.executemany(
                    """INSERT INTO skills(category_id,name,description,status,readiness,confidence,priority,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    [(category_id, skill, f"Demonstrate practical ability in {skill}.", SkillStatus.REMAINING.value, Readiness.AWARENESS.value, 0, "medium", now, now) for skill in skills],
                )
        await self.write(op)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def dicts(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def validate_confidence(value: int) -> None:
    if not 0 <= value <= 10:
        raise ValueError("Confidence must be between 0 and 10.")


def validate_enum(enum_type: type[StrEnum], value: str, field: str) -> None:
    try:
        enum_type(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {value}") from exc


def skill_row(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM skills WHERE LOWER(name)=LOWER(?)", (name.strip(),)
    ).fetchone()


def project_row(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM projects WHERE LOWER(name)=LOWER(?)", (name.strip(),)
    ).fetchone()


def json_list(value: str) -> list[str]:
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []


if FastMCP is not None:
    mcp = FastMCP(APP_NAME)
else:
    class _UnavailableMCP:
        def tool(self, fn: Callable[..., Any]) -> Callable[..., Any]: return fn
        def resource(self, *_args: Any, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            return lambda fn: fn
        def prompt(self, fn: Callable[..., Any]) -> Callable[..., Any]: return fn
    mcp = _UnavailableMCP()

db = Database()


@mcp.tool
def health() -> dict[str, Any]:
    """Return server and database configuration health."""
    return {"ok": True, "app": APP_NAME, "database": str(db.path), "async_api": True}


@mcp.tool
async def get_progress() -> dict[str, Any]:
    """Return weighted and basic overall roadmap progress."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("""SELECT COUNT(*) total,
            SUM(status='completed') completed, SUM(status='in_progress') in_progress,
            SUM(status='remaining') remaining, ROUND(AVG(confidence),2) average_confidence,
            COALESCE(SUM(CASE WHEN status='completed' THEN CASE priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END ELSE 0 END),0) completed_weight,
            COALESCE(SUM(CASE priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END),0) total_weight
            FROM skills""").fetchone()
        return {**dict(row), "completion_percentage": round((row['completed'] or 0) / row['total'] * 100, 2) if row['total'] else 0.0,
                "weighted_completion_percentage": round((row['completed_weight'] or 0) / row['total_weight'] * 100, 2) if row['total_weight'] else 0.0}
    return await db.read(op)


@mcp.tool
async def get_skills(status: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
    """List skills, optionally filtered by status and/or category."""
    if status: validate_enum(SkillStatus, status, "status")
    def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status: clauses.append("skills.status=?"); params.append(status)
        if category: clauses.append("LOWER(categories.name) LIKE LOWER(?)"); params.append(f"%{category}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(f"""SELECT skills.*, categories.name category FROM skills JOIN categories ON categories.id=skills.category_id {where} ORDER BY categories.sort_order, skills.id""", params).fetchall()
        return dicts(rows)
    return await db.read(op)


@mcp.tool
async def search_skill(query: str) -> list[dict[str, Any]]:
    """Search skill names, categories, descriptions, and next actions."""
    if not query.strip(): raise ValueError("Search query cannot be empty.")
    pattern = f"%{query.strip()}%"
    return await db.read(lambda conn: dicts(conn.execute("""SELECT skills.*, categories.name category FROM skills JOIN categories ON categories.id=skills.category_id
        WHERE skills.name LIKE ? OR categories.name LIKE ? OR skills.description LIKE ? OR skills.next_action LIKE ? ORDER BY skills.id""", (pattern, pattern, pattern, pattern)).fetchall()))


@mcp.tool
async def update_skill(skill_name: str, status: str, confidence: int | None = None, readiness: str | None = None,
                       priority: str | None = None, notes: str | None = None, next_action: str | None = None) -> dict[str, Any]:
    """Update a skill while preserving history and existing text when omitted."""
    validate_enum(SkillStatus, status, "status")
    if confidence is not None: validate_confidence(confidence)
    if readiness: validate_enum(Readiness, readiness, "readiness")
    if priority and priority not in {"critical", "high", "medium", "low"}: raise ValueError("Invalid priority.")
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = skill_row(conn, skill_name)
        if row is None: raise ValueError(f"Skill '{skill_name}' not found.")
        now = utc_now(); new_conf = row["confidence"] if confidence is None else confidence
        new_ready = row["readiness"] if readiness is None else readiness
        new_priority = row["priority"] if priority is None else priority
        new_notes = row["notes"] if notes is None else notes
        new_action = row["next_action"] if next_action is None else next_action
        conn.execute("""UPDATE skills SET status=?,confidence=?,readiness=?,priority=?,notes=?,next_action=?,updated_at=?,last_practiced_at=? WHERE id=?""",
                     (status, new_conf, new_ready, new_priority, new_notes, new_action, now, now if status != SkillStatus.REMAINING.value else row["last_practiced_at"], row["id"]))
        conn.execute("""INSERT INTO skill_history(skill_id,old_status,new_status,old_confidence,new_confidence,old_readiness,new_readiness,changed_at,notes) VALUES(?,?,?,?,?,?,?,?,?)""",
                     (row["id"], row["status"], status, row["confidence"], new_conf, row["readiness"], new_ready, now, new_notes))
        return {"success": True, "skill": row["name"], "old_status": row["status"], "new_status": status, "confidence": new_conf, "readiness": new_ready, "updated_at": now}
    return await db.write(op)


@mcp.tool
async def add_skill_evidence(skill_name: str, evidence_type: str, title: str, url: str = "", notes: str = "") -> dict[str, Any]:
    """Attach verifiable evidence such as a project, deployment, course, or interview explanation to a skill."""
    allowed = {"course_completed", "notes_created", "quiz_passed", "implemented_from_scratch", "used_in_project", "deployed", "explained_in_interview", "reviewed_after_delay"}
    if evidence_type not in allowed: raise ValueError(f"evidence_type must be one of: {', '.join(sorted(allowed))}")
    if not title.strip(): raise ValueError("Evidence title cannot be empty.")
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = skill_row(conn, skill_name)
        if row is None: raise ValueError(f"Skill '{skill_name}' not found.")
        evidence_id = conn.execute("INSERT INTO skill_evidence(skill_id,evidence_type,title,url,notes,created_at) VALUES(?,?,?,?,?,?)", (row["id"], evidence_type, title.strip(), url, notes, utc_now())).lastrowid
        return {"success": True, "evidence_id": evidence_id, "skill": row["name"], "evidence_type": evidence_type, "title": title.strip()}
    return await db.write(op)


@mcp.tool
async def get_skill_evidence(skill_name: str) -> list[dict[str, Any]]:
    """Return all evidence recorded for a skill."""
    return await db.read(lambda conn: dicts(conn.execute("""SELECT skill_evidence.* FROM skill_evidence JOIN skills ON skills.id=skill_evidence.skill_id WHERE LOWER(skills.name)=LOWER(?) ORDER BY skill_evidence.id DESC""", (skill_name,)).fetchall()))


@mcp.tool
async def get_skill_readiness(skill_name: str) -> dict[str, Any]:
    """Assess readiness from current status, confidence, readiness level, and evidence."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = skill_row(conn, skill_name)
        if row is None: raise ValueError(f"Skill '{skill_name}' not found.")
        evidence = dicts(conn.execute("SELECT evidence_type,title,url FROM skill_evidence WHERE skill_id=? ORDER BY id", (row["id"],)).fetchall())
        return {"skill": row["name"], "status": row["status"], "confidence": row["confidence"], "readiness": row["readiness"], "evidence": evidence, "evidence_count": len(evidence), "has_project_evidence": any(e["evidence_type"] in {"used_in_project", "deployed"} for e in evidence)}
    return await db.read(op)


@mcp.tool
async def get_weak_skills(threshold: int = WEAK_SKILL_THRESHOLD) -> list[dict[str, Any]]:
    """Return active or completed skills below the confidence threshold."""
    validate_confidence(threshold)
    return await db.read(lambda conn: dicts(conn.execute("""SELECT skills.name,skills.status,skills.confidence,skills.readiness,categories.name category FROM skills JOIN categories ON categories.id=skills.category_id WHERE skills.status IN ('completed','in_progress') AND skills.confidence<? ORDER BY skills.confidence, skills.name""", (threshold,)).fetchall()))


@mcp.tool
async def log_learning_session(skill_name: str, duration_minutes: int, topic: str = "", notes: str = "") -> dict[str, Any]:
    """Record focused learning time and update the skill's last-practiced timestamp."""
    if duration_minutes <= 0: raise ValueError("duration_minutes must be greater than zero.")
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = skill_row(conn, skill_name)
        if row is None: raise ValueError(f"Skill '{skill_name}' not found.")
        conn.execute("INSERT INTO learning_sessions(skill_id,session_date,duration_minutes,topic,notes) VALUES(?,?,?,?,?)", (row["id"], today(), duration_minutes, topic, notes))
        conn.execute("UPDATE skills SET last_practiced_at=?,updated_at=? WHERE id=?", (today(), utc_now(), row["id"]))
        return {"success": True, "skill": row["name"], "duration_minutes": duration_minutes, "date": today()}
    return await db.write(op)


@mcp.tool
async def get_learning_stats() -> dict[str, Any]:
    """Return total learning time, recent sessions, and activity by week."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        total = conn.execute("SELECT COALESCE(SUM(duration_minutes),0) value FROM learning_sessions").fetchone()["value"]
        recent = dicts(conn.execute("""SELECT skills.name,session_date,duration_minutes,topic FROM learning_sessions JOIN skills ON skills.id=learning_sessions.skill_id ORDER BY learning_sessions.id DESC LIMIT ?""", (RECENT_SESSION_LIMIT,)).fetchall())
        weekly = dicts(conn.execute("SELECT substr(session_date,1,7) month, SUM(duration_minutes) minutes FROM learning_sessions GROUP BY substr(session_date,1,7) ORDER BY month DESC LIMIT 12").fetchall())
        return {"total_minutes": total, "total_hours": round(total / 60, 2), "recent_sessions": recent, "monthly_activity": weekly}
    return await db.read(op)


@mcp.tool
async def add_prerequisite(skill_name: str, prerequisite_name: str) -> dict[str, Any]:
    """Declare that one skill must be learned before another."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        skill, prereq = skill_row(conn, skill_name), skill_row(conn, prerequisite_name)
        if skill is None or prereq is None: raise ValueError("Both skills must exist.")
        if skill["id"] == prereq["id"]: raise ValueError("A skill cannot depend on itself.")
        try: conn.execute("INSERT INTO skill_prerequisites(skill_id,prerequisite_skill_id) VALUES(?,?)", (skill["id"], prereq["id"]))
        except sqlite3.IntegrityError as exc: raise ValueError("Prerequisite already exists or creates an invalid relationship.") from exc
        return {"success": True, "skill": skill["name"], "prerequisite": prereq["name"]}
    return await db.write(op)


@mcp.tool
async def get_prerequisites(skill_name: str) -> list[dict[str, Any]]:
    """List direct prerequisites and their current status."""
    return await db.read(lambda conn: dicts(conn.execute("""SELECT prerequisite.name,prerequisite.status,prerequisite.confidence FROM skill_prerequisites JOIN skills target ON target.id=skill_prerequisites.skill_id JOIN skills prerequisite ON prerequisite.id=skill_prerequisites.prerequisite_skill_id WHERE LOWER(target.name)=LOWER(?) ORDER BY prerequisite.name""", (skill_name,)).fetchall()))


@mcp.tool
async def get_unblocked_skills() -> list[dict[str, Any]]:
    """Return remaining skills whose direct prerequisites are all completed."""
    return await db.read(lambda conn: dicts(conn.execute("""SELECT skills.name,skills.status,skills.priority,skills.confidence FROM skills WHERE skills.status='remaining' AND NOT EXISTS (SELECT 1 FROM skill_prerequisites sp JOIN skills p ON p.id=sp.prerequisite_skill_id WHERE sp.skill_id=skills.id AND p.status<>'completed') ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, skills.id""").fetchall()))


@mcp.tool
async def schedule_review(skill_name: str, days_from_now: int = 7) -> dict[str, Any]:
    """Schedule a spaced review for a skill."""
    if days_from_now < 0: raise ValueError("days_from_now cannot be negative.")
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = skill_row(conn, skill_name)
        if row is None: raise ValueError(f"Skill '{skill_name}' not found.")
        review_date = (datetime.now(UTC) + timedelta(days=days_from_now)).date().isoformat()
        review_id = conn.execute("INSERT INTO reviews(skill_id,review_date,confidence) VALUES(?,?,?)", (row["id"], review_date, row["confidence"])).lastrowid
        return {"success": True, "review_id": review_id, "skill": row["name"], "review_date": review_date}
    return await db.write(op)


@mcp.tool
async def get_due_reviews() -> list[dict[str, Any]]:
    """Return pending reviews due today or overdue."""
    return await db.read(lambda conn: dicts(conn.execute("""SELECT reviews.id,skills.name skill,reviews.review_date,reviews.status,skills.confidence FROM reviews JOIN skills ON skills.id=reviews.skill_id WHERE reviews.review_date<=? AND reviews.status='pending' ORDER BY reviews.review_date""", (today(),)).fetchall()))


@mcp.tool
async def complete_review(skill_name: str, confidence: int, notes: str = "") -> dict[str, Any]:
    """Complete the oldest pending review, update confidence, and schedule the next interval."""
    validate_confidence(confidence)
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        skill = skill_row(conn, skill_name)
        if skill is None: raise ValueError(f"Skill '{skill_name}' not found.")
        review = conn.execute("SELECT * FROM reviews WHERE skill_id=? AND status='pending' ORDER BY review_date,id LIMIT 1", (skill["id"],)).fetchone()
        if review is None: raise ValueError(f"No pending review found for '{skill_name}'.")
        interval = 1 if confidence <= 3 else 3 if confidence <= 6 else 7 if confidence <= 8 else 21
        now = utc_now(); next_date = (datetime.now(UTC) + timedelta(days=interval)).date().isoformat()
        conn.execute("UPDATE reviews SET status='completed',confidence=?,notes=?,completed_at=? WHERE id=?", (confidence, notes, now, review["id"]))
        conn.execute("UPDATE skills SET confidence=?,updated_at=?,last_practiced_at=? WHERE id=?", (confidence, now, today(), skill["id"]))
        conn.execute("INSERT INTO reviews(skill_id,review_date,confidence) VALUES(?,?,?)", (skill["id"], next_date, confidence))
        return {"success": True, "skill": skill["name"], "confidence": confidence, "next_review": next_date, "interval_days": interval}
    return await db.write(op)


@mcp.tool
async def add_project(name: str, description: str = "", status: str = "planned", github_url: str = "", deployment_url: str = "", problem_statement: str = "", architecture: str = "", technologies: list[str] | None = None) -> dict[str, Any]:
    """Create a project with portfolio metadata."""
    validate_enum(ProjectStatus, status, "project status")
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        now = utc_now()
        try:
            project_id = conn.execute("""INSERT INTO projects(name,description,problem_statement,architecture,technologies,status,github_url,deployment_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", (name.strip(), description, problem_statement, architecture, json.dumps(technologies or []), status, github_url, deployment_url, now, now)).lastrowid
        except sqlite3.IntegrityError as exc: raise ValueError(f"Project '{name}' already exists.") from exc
        return {"success": True, "project_id": project_id, "project": name.strip()}
    return await db.write(op)


@mcp.tool
async def update_project(name: str, description: str | None = None, problem_statement: str | None = None,
                         architecture: str | None = None, technologies: list[str] | None = None,
                         status: str | None = None, github_url: str | None = None,
                         deployment_url: str | None = None, evaluation_notes: str | None = None,
                         limitations: str | None = None, portfolio_score: int | None = None) -> dict[str, Any]:
    """Update supplied project metadata fields; omitted fields remain unchanged."""
    fields = {key: value for key, value in {
        "description": description, "problem_statement": problem_statement,
        "architecture": architecture, "technologies": technologies,
        "status": status, "github_url": github_url, "deployment_url": deployment_url,
        "evaluation_notes": evaluation_notes, "limitations": limitations,
        "portfolio_score": portfolio_score,
    }.items() if value is not None}
    allowed = {"description", "problem_statement", "architecture", "technologies", "status", "github_url", "deployment_url", "evaluation_notes", "limitations", "portfolio_score"}
    unknown = set(fields) - allowed
    if unknown: raise ValueError(f"Unknown project fields: {sorted(unknown)}")
    if "status" in fields: validate_enum(ProjectStatus, fields["status"], "project status")
    if "portfolio_score" in fields and fields["portfolio_score"] is not None and not 0 <= fields["portfolio_score"] <= 10: raise ValueError("portfolio_score must be between 0 and 10.")
    if "technologies" in fields: fields["technologies"] = json.dumps(fields["technologies"])
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        project = project_row(conn, name)
        if project is None: raise ValueError(f"Project '{name}' not found.")
        if fields:
            assignments = ", ".join(f"{key}=?" for key in fields)
            conn.execute(f"UPDATE projects SET {assignments},updated_at=? WHERE id=?", (*fields.values(), utc_now(), project["id"]))
        return {"success": True, "project": project["name"], "updated_fields": list(fields)}
    return await db.write(op)


@mcp.tool
async def get_projects() -> list[dict[str, Any]]:
    """List projects with decoded technologies and milestone counts."""
    def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = dicts(conn.execute("SELECT * FROM projects ORDER BY id").fetchall())
        for row in rows:
            row["technologies"] = json_list(row["technologies"])
            row["milestones"] = conn.execute("SELECT COUNT(*) FROM project_milestones WHERE project_id=?", (row["id"],)).fetchone()[0]
        return rows
    return await db.read(op)


@mcp.tool
async def link_project_to_skill(project_name: str, skill_name: str, evidence_type: str = "used_in_project") -> dict[str, Any]:
    """Link a project to a skill and optionally create project evidence."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        project, skill = project_row(conn, project_name), skill_row(conn, skill_name)
        if project is None or skill is None: raise ValueError("Both project and skill must exist.")
        try: conn.execute("INSERT INTO project_skills(project_id,skill_id) VALUES(?,?)", (project["id"], skill["id"]))
        except sqlite3.IntegrityError: pass
        conn.execute("INSERT INTO skill_evidence(skill_id,evidence_type,title,created_at) VALUES(?,?,?,?)", (skill["id"], evidence_type, project["name"], utc_now()))
        return {"success": True, "project": project["name"], "skill": skill["name"], "evidence_added": True}
    return await db.write(op)


@mcp.tool
async def add_project_milestone(project_name: str, title: str, description: str = "", due_date: str | None = None) -> dict[str, Any]:
    """Add a trackable milestone to a project."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        project = project_row(conn, project_name)
        if project is None: raise ValueError(f"Project '{project_name}' not found.")
        milestone_id = conn.execute("INSERT INTO project_milestones(project_id,title,description,due_date) VALUES(?,?,?,?)", (project["id"], title, description, due_date)).lastrowid
        return {"success": True, "milestone_id": milestone_id, "project": project["name"], "title": title}
    return await db.write(op)


@mcp.tool
async def complete_project_milestone(milestone_id: int) -> dict[str, Any]:
    """Mark a project milestone complete."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM project_milestones WHERE id=?", (milestone_id,)).fetchone()
        if row is None: raise ValueError("Milestone not found.")
        conn.execute("UPDATE project_milestones SET status='completed',completed_at=? WHERE id=?", (utc_now(), milestone_id))
        return {"success": True, "milestone_id": milestone_id, "title": row["title"]}
    return await db.write(op)


@mcp.tool
async def add_project_metric(project_name: str, metric_name: str, metric_value: str, dataset: str = "", notes: str = "") -> dict[str, Any]:
    """Record a project evaluation metric such as RMSE, faithfulness, or latency."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        project = project_row(conn, project_name)
        if project is None: raise ValueError(f"Project '{project_name}' not found.")
        metric_id = conn.execute("INSERT INTO project_metrics(project_id,metric_name,metric_value,dataset,notes) VALUES(?,?,?,?,?)", (project["id"], metric_name, metric_value, dataset, notes)).lastrowid
        return {"success": True, "metric_id": metric_id, "project": project["name"], "metric": metric_name, "value": metric_value}
    return await db.write(op)


@mcp.tool
async def create_task(title: str, task_type: str = "learning", skill_name: str | None = None, project_name: str | None = None, priority: str = "medium", estimated_minutes: int = 30, due_date: str | None = None) -> dict[str, Any]:
    """Create a concrete learning or project task."""
    if estimated_minutes <= 0: raise ValueError("estimated_minutes must be positive.")
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        skill = skill_row(conn, skill_name) if skill_name else None
        project = project_row(conn, project_name) if project_name else None
        if skill_name and skill is None: raise ValueError(f"Skill '{skill_name}' not found.")
        if project_name and project is None: raise ValueError(f"Project '{project_name}' not found.")
        task_id = conn.execute("INSERT INTO tasks(title,task_type,skill_id,project_id,priority,estimated_minutes,due_date) VALUES(?,?,?,?,?,?,?)", (title, task_type, skill["id"] if skill else None, project["id"] if project else None, priority, estimated_minutes, due_date)).lastrowid
        return {"success": True, "task_id": task_id, "title": title}
    return await db.write(op)


@mcp.tool
async def get_today_plan(available_minutes: int = 120) -> list[dict[str, Any]]:
    """Return the highest-priority unfinished tasks that fit the available time."""
    if available_minutes <= 0: raise ValueError("available_minutes must be positive.")
    def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = dicts(conn.execute("""SELECT tasks.*,skills.name skill,projects.name project FROM tasks LEFT JOIN skills ON skills.id=tasks.skill_id LEFT JOIN projects ON projects.id=tasks.project_id WHERE tasks.status<>'completed' ORDER BY CASE tasks.priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, CASE WHEN tasks.due_date IS NULL THEN 1 ELSE 0 END, tasks.due_date, tasks.id""").fetchall())
        plan, used = [], 0
        for row in rows:
            if used + row["estimated_minutes"] <= available_minutes:
                plan.append(row)
                used += row["estimated_minutes"]
        return plan
    return await db.read(op)


@mcp.tool
async def complete_task(task_id: int) -> dict[str, Any]:
    """Complete a planned task."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None: raise ValueError("Task not found.")
        conn.execute("UPDATE tasks SET status='completed',completed_at=? WHERE id=?", (utc_now(), task_id))
        return {"success": True, "task_id": task_id, "title": row["title"]}
    return await db.write(op)


async def _category_progress() -> list[dict[str, Any]]:
    def op(conn: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = dicts(conn.execute("""SELECT categories.name category,COUNT(skills.id) total,SUM(skills.status='completed') completed,SUM(skills.status='in_progress') in_progress,ROUND(AVG(skills.confidence),2) average_confidence FROM categories LEFT JOIN skills ON skills.category_id=categories.id GROUP BY categories.id ORDER BY categories.sort_order""").fetchall())
        for row in rows: row["completion_percentage"] = round((row["completed"] or 0) / row["total"] * 100, 2) if row["total"] else 0.0
        return rows
    return await db.read(op)


async def _projects_summary() -> list[dict[str, Any]]:
    return await get_projects()


@mcp.tool
async def get_dashboard() -> dict[str, Any]:
    """Return the full dashboard; independent sections are read concurrently."""
    progress, categories, learning, reviews, projects, weak, unblocked = await asyncio.gather(
        get_progress(), _category_progress(), get_learning_stats(), get_due_reviews(), _projects_summary(), get_weak_skills(), get_unblocked_skills()
    )
    return {"overall": progress, "categories": categories, "learning": learning, "reviews_due": reviews, "projects": projects, "weak_skills": weak, "unblocked_skills": unblocked}


@mcp.tool
async def get_next_best_action() -> dict[str, Any]:
    """Recommend one next action using prerequisites, priority, confidence, and project evidence."""
    def op(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("""SELECT skills.*,categories.name category FROM skills JOIN categories ON categories.id=skills.category_id WHERE skills.status='in_progress' ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, confidence, skills.id LIMIT 1""").fetchone()
        if row is None:
            row = conn.execute("""SELECT skills.*,categories.name category FROM skills JOIN categories ON categories.id=skills.category_id WHERE skills.status='remaining' AND NOT EXISTS (SELECT 1 FROM skill_prerequisites sp JOIN skills p ON p.id=sp.prerequisite_skill_id WHERE sp.skill_id=skills.id AND p.status<>'completed') ORDER BY CASE priority WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, skills.id LIMIT 1""").fetchone()
        if row is None: return {"action": "No unblocked skill found. Review projects and add missing evidence or prerequisites."}
        return {"action": row["next_action"] or f"Study and implement a small exercise for {row['name']}", "skill": row["name"], "category": row["category"], "priority": row["priority"], "confidence": row["confidence"], "reason": "Selected from active or unblocked skills using priority and confidence."}
    return await db.read(op)


@mcp.tool
async def get_readiness_report(target_role: str = "AI Engineer") -> dict[str, Any]:
    """Summarize strengths, developing skills, evidence gaps, and recommended action for a target role."""
    skills, projects, weak = await asyncio.gather(get_skills(), get_projects(), get_weak_skills())
    production = [s for s in skills if s["readiness"] in {Readiness.DEPLOYED.value, Readiness.INTERVIEW_READY.value, Readiness.PRODUCTION_READY.value}]
    gaps = [s for s in skills if s["status"] != SkillStatus.COMPLETED.value or s["confidence"] < 7]
    return {"target_role": target_role, "strong_areas": [s["name"] for s in production[:12]], "developing_areas": [s["name"] for s in gaps[:15]], "weak_skills": weak, "project_count": len(projects), "portfolio_gaps": [p["name"] for p in projects if not p.get("github_url") or not p.get("deployment_url")], "recommended_next_action": await get_next_best_action()}


@mcp.tool
async def get_portfolio_gaps() -> list[dict[str, Any]]:
    """Find projects missing common portfolio evidence."""
    projects = await get_projects()
    result = []
    for p in projects:
        missing = [field for field in ("github_url", "deployment_url", "description", "problem_statement", "architecture", "evaluation_notes") if not p.get(field)]
        if missing: result.append({"project": p["name"], "missing": missing})
    return result


@mcp.resource("roadmap://overview")
async def roadmap_overview() -> str:
    """Compact roadmap overview for MCP clients."""
    progress = await get_progress()
    return json.dumps({"app": APP_NAME, "progress": progress, "next_action": await get_next_best_action()}, indent=2)


@mcp.resource("roadmap://dashboard")
async def roadmap_dashboard() -> str:
    """Full current dashboard as JSON."""
    return json.dumps(await get_dashboard(), indent=2, default=str)


@mcp.resource("roadmap://career-gaps")
async def career_gaps() -> str:
    """Current role-readiness gaps as JSON."""
    return json.dumps(await get_readiness_report(), indent=2, default=str)


@mcp.prompt
def weekly_review() -> str:
    """Prompt an MCP client to conduct a weekly career review."""
    return """Act as my AI Engineering career coach. Call get_dashboard, get_learning_stats, get_due_reviews, get_portfolio_gaps, and get_next_best_action. Summarize what I completed this week, identify evidence-backed progress, flag overdue reviews and weak skills, then propose a realistic plan for the next 7 days. Do not mark skills completed without evidence."""


@mcp.prompt
def project_review() -> str:
    """Prompt an MCP client to review a project for portfolio readiness."""
    return """Review my AI Engineering projects. Inspect project metadata, linked skills, milestones, metrics, deployment, evaluation, documentation, and limitations. Identify the three highest-impact improvements for recruiter and interview readiness. Be specific and do not call a project production-ready without deployment, tests, evaluation, and observability evidence."""


async def initialize_application() -> None:
    await db.initialize()
    await db.seed()
    logger.info("Initialized %s at %s", APP_NAME, db.path)


async def main_async() -> None:
    await initialize_application()
    if FastMCP is None:
        raise RuntimeError("Install dependencies with: pip install -r requirements.txt")
    await mcp.run_async(transport="http", host=HOST, port=PORT)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
