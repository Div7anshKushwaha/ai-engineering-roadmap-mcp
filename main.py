from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Sequence

from fastmcp import FastMCP


# ============================================================================
# CONFIGURATION
# ============================================================================

APP_NAME = "AI Engineering Roadmap Tracker"
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "ai_roadmap.db"

DEFAULT_CONFIDENCE = 0
DEFAULT_PRIORITY = "medium"
WEAK_SKILL_THRESHOLD = 6
RECENT_SESSION_LIMIT = 10


# ============================================================================
# LOGGING
# ============================================================================

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | "
    "%(name)s | %(message)s"
)

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)

logger = logging.getLogger(APP_NAME)


# ============================================================================
# MCP SERVER
# ============================================================================

mcp = FastMCP(APP_NAME)


# ============================================================================
# ENUMS
# ============================================================================

class SkillStatus(StrEnum):
    """Valid states for a skill."""

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    REMAINING = "remaining"


class ReviewStatus(StrEnum):
    """Valid states for a review."""

    PENDING = "pending"
    COMPLETED = "completed"


class ProjectStatus(StrEnum):
    """Valid states for a project."""

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


# ============================================================================
# TYPES
# ============================================================================

RowDict = dict[str, Any]


# ============================================================================
# ROADMAP
# ============================================================================

INITIAL_ROADMAP: dict[str, list[str]] = {
    "Programming & Data Foundations": [
        "Python",
        "NumPy",
        "Pandas",
        "Matplotlib",
        "SQL",
        "Excel",
        "Power BI",
        "Git & GitHub",
    ],
    "Machine Learning": [
        "Machine Learning Fundamentals",
        "Linear Regression",
        "Logistic Regression",
        "Decision Trees",
        "Random Forest",
        "XGBoost",
        "LightGBM",
        "CatBoost",
        "K-Means",
        "DBSCAN",
        "Gaussian Mixture Models",
        "Hyperparameter Optimization",
        "Scikit-learn",
    ],
    "Deep Learning": [
        "ANN",
        "CNN",
        "RNN",
        "LSTM",
        "Attention Mechanism",
        "Transformers",
        "TensorFlow",
        "Keras",
    ],
    "Generative AI": [
        "LLM Fundamentals",
        "Embeddings",
        "Vector Databases",
        "LangChain",
        "RAG",
        "Contextual Compression",
        "RAGAS Evaluation",
        "Tool Calling",
        "Agents",
    ],
    "Agentic AI": [
        "LangGraph",
        "Stateful Agents",
        "Human-in-the-Loop",
        "Memory",
        "CRAG",
        "Self-RAG",
        "MCP Protocol",
        "MCP Servers",
        "MCP Clients",
        "Remote MCP Servers",
        "Advanced Agent Architectures",
    ],
    "MLOps": [
        "Docker",
        "GitHub Actions",
        "MLflow",
        "DVC",
        "Data Versioning",
        "Experiment Tracking",
        "Model Versioning",
        "CI/CD for ML",
        "Model Deployment",
        "Monitoring",
        "AWS",
    ],
    "Production AI Engineering": [
        "FastAPI",
        "Production APIs",
        "PostgreSQL",
        "Redis",
        "Caching",
        "Authentication",
        "Observability",
        "LLM Evaluation",
        "Production RAG",
        "Production Agents",
    ],
    "Projects": [
        "Laptop Price Predictor",
        "Bank Management System",
        "SQL Analytics Portfolio",
        "NLP Emotion Detection",
        "YouTube RAG Chatbot",
        "DVC MLOps Pipeline",
        "MCP-based AI Project",
        "End-to-End AI Engineering Project",
    ],
}


# ============================================================================
# DATABASE
# ============================================================================

class Database:
    """Small SQLite database abstraction for the roadmap application."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection."""

        connection = sqlite3.connect(
            self.path,
            timeout=30,
        )

        connection.row_factory = sqlite3.Row

        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")

        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """
        Provide a database session.

        Commits on success and rolls back automatically on failure.
        """

        connection = self.connect()

        try:
            yield connection
            connection.commit()

        except Exception:
            connection.rollback()
            logger.exception("Database transaction failed.")
            raise

        finally:
            connection.close()

    def initialize(self) -> None:
        """Create database schema and indexes."""

        with self.session() as connection:
            connection.executescript(SCHEMA)

        logger.info("Database initialized: %s", self.path)

    def seed(self) -> None:
        """Insert the initial roadmap if the database is empty."""

        with self.session() as connection:
            category_count = connection.execute(
                "SELECT COUNT(*) FROM categories"
            ).fetchone()[0]

            if category_count:
                logger.info("Roadmap already exists. Skipping seed.")
                return

            now = utc_now()

            for category_name, skills in INITIAL_ROADMAP.items():
                cursor = connection.execute(
                    """
                    INSERT INTO categories(name, description)
                    VALUES (?, ?)
                    """,
                    (
                        category_name,
                        f"{category_name} skills",
                    ),
                )

                category_id = cursor.lastrowid

                connection.executemany(
                    """
                    INSERT INTO skills(
                        category_id,
                        name,
                        status,
                        confidence,
                        priority,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            category_id,
                            skill_name,
                            SkillStatus.REMAINING.value,
                            DEFAULT_CONFIDENCE,
                            DEFAULT_PRIORITY,
                            now,
                            now,
                        )
                        for skill_name in skills
                    ],
                )

        logger.info("Initial roadmap seeded.")


# ============================================================================
# SCHEMA
# ============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    name TEXT NOT NULL UNIQUE,

    status TEXT NOT NULL DEFAULT 'remaining',

    confidence INTEGER NOT NULL DEFAULT 0
        CHECK(confidence BETWEEN 0 AND 10),

    priority TEXT NOT NULL DEFAULT 'medium',

    notes TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY(category_id)
        REFERENCES categories(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS skill_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    skill_id INTEGER NOT NULL,

    old_status TEXT,
    new_status TEXT NOT NULL,

    old_confidence INTEGER,
    new_confidence INTEGER,

    changed_at TEXT NOT NULL,

    notes TEXT,

    FOREIGN KEY(skill_id)
        REFERENCES skills(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS learning_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    skill_id INTEGER NOT NULL,

    session_date TEXT NOT NULL,

    duration_minutes INTEGER NOT NULL
        CHECK(duration_minutes > 0),

    topic TEXT,

    notes TEXT,

    FOREIGN KEY(skill_id)
        REFERENCES skills(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    skill_id INTEGER NOT NULL,

    review_date TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending',

    confidence INTEGER,

    notes TEXT,

    FOREIGN KEY(skill_id)
        REFERENCES skills(id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    name TEXT NOT NULL UNIQUE,

    description TEXT,

    status TEXT NOT NULL DEFAULT 'planned',

    github_url TEXT,

    deployment_url TEXT,

    created_at TEXT NOT NULL,

    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_skills (
    project_id INTEGER NOT NULL,
    skill_id INTEGER NOT NULL,

    PRIMARY KEY(project_id, skill_id),

    FOREIGN KEY(project_id)
        REFERENCES projects(id)
        ON DELETE CASCADE,

    FOREIGN KEY(skill_id)
        REFERENCES skills(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_skills_status
    ON skills(status);

CREATE INDEX IF NOT EXISTS idx_skills_category
    ON skills(category_id);

CREATE INDEX IF NOT EXISTS idx_skill_history_skill
    ON skill_history(skill_id);

CREATE INDEX IF NOT EXISTS idx_learning_skill
    ON learning_sessions(skill_id);

CREATE INDEX IF NOT EXISTS idx_learning_date
    ON learning_sessions(session_date);

CREATE INDEX IF NOT EXISTS idx_reviews_date_status
    ON reviews(review_date, status);

CREATE INDEX IF NOT EXISTS idx_project_skills_skill
    ON project_skills(skill_id);
"""


db = Database(DATABASE_PATH)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def utc_now() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(UTC).isoformat()


def rows_to_dicts(rows: Sequence[sqlite3.Row]) -> list[RowDict]:
    """Convert SQLite rows into serializable dictionaries."""

    return [dict(row) for row in rows]


def validate_confidence(confidence: int | None) -> None:
    """Validate confidence score."""

    if confidence is None:
        return

    if not 0 <= confidence <= 10:
        raise ValueError("Confidence must be between 0 and 10.")


def get_skill(
    connection: sqlite3.Connection,
    skill_name: str,
) -> sqlite3.Row | None:
    """Find a skill by name, case-insensitively."""

    return connection.execute(
        """
        SELECT id, name, status, confidence
        FROM skills
        WHERE LOWER(name) = LOWER(?)
        """,
        (skill_name,),
    ).fetchone()


def get_project(
    connection: sqlite3.Connection,
    project_name: str,
) -> sqlite3.Row | None:
    """Find a project by name, case-insensitively."""

    return connection.execute(
        """
        SELECT id, name
        FROM projects
        WHERE LOWER(name) = LOWER(?)
        """,
        (project_name,),
    ).fetchone()


# ============================================================================
# SKILL TOOLS
# ============================================================================

@mcp.tool
def get_progress() -> dict[str, Any]:
    """Return overall AI Engineering roadmap progress."""

    with db.session() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN status = ?
                        THEN 1 ELSE 0
                    END
                ) AS completed,

                SUM(
                    CASE
                        WHEN status = ?
                        THEN 1 ELSE 0
                    END
                ) AS in_progress,

                SUM(
                    CASE
                        WHEN status = ?
                        THEN 1 ELSE 0
                    END
                ) AS remaining,

                ROUND(AVG(confidence), 2)
                    AS average_confidence

            FROM skills
            """,
            (
                SkillStatus.COMPLETED.value,
                SkillStatus.IN_PROGRESS.value,
                SkillStatus.REMAINING.value,
            ),
        ).fetchone()

    total = row["total"] or 0
    completed = row["completed"] or 0

    percentage = (
        round(completed / total * 100, 2)
        if total
        else 0.0
    )

    return {
        "total_skills": total,
        "completed": completed,
        "in_progress": row["in_progress"] or 0,
        "remaining": row["remaining"] or 0,
        "completion_percentage": percentage,
        "average_confidence": row["average_confidence"] or 0,
    }


@mcp.tool
def get_skills(
    status: str | None = None,
) -> list[RowDict]:
    """
    Return skills.

    Optional status:
    completed
    in_progress
    remaining
    """

    if status is not None:
        try:
            SkillStatus(status)
        except ValueError as exc:
            raise ValueError(
                f"Invalid status: {status}"
            ) from exc

    with db.session() as connection:
        if status is None:
            rows = connection.execute(
                """
                SELECT
                    skills.id,
                    skills.name,
                    skills.status,
                    skills.confidence,
                    skills.priority,
                    skills.notes,
                    categories.name AS category

                FROM skills

                JOIN categories
                    ON skills.category_id = categories.id

                ORDER BY categories.id, skills.id
                """
            ).fetchall()

        else:
            rows = connection.execute(
                """
                SELECT
                    skills.id,
                    skills.name,
                    skills.status,
                    skills.confidence,
                    skills.priority,
                    skills.notes,
                    categories.name AS category

                FROM skills

                JOIN categories
                    ON skills.category_id = categories.id

                WHERE skills.status = ?

                ORDER BY categories.id, skills.id
                """,
                (status,),
            ).fetchall()

    return rows_to_dicts(rows)


@mcp.tool
def search_skill(query: str) -> list[RowDict]:
    """Search skills by name or category."""

    if not query.strip():
        raise ValueError("Search query cannot be empty.")

    pattern = f"%{query.strip()}%"

    with db.session() as connection:
        rows = connection.execute(
            """
            SELECT
                skills.id,
                skills.name,
                skills.status,
                skills.confidence,
                skills.priority,
                categories.name AS category

            FROM skills

            JOIN categories
                ON skills.category_id = categories.id

            WHERE
                skills.name LIKE ?
                OR categories.name LIKE ?

            ORDER BY categories.id, skills.id
            """,
            (pattern, pattern),
        ).fetchall()

    return rows_to_dicts(rows)


@mcp.tool
def update_skill(
    skill_name: str,
    status: str,
    confidence: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """
    Update a skill's status and confidence.

    Confidence is between 0 and 10.
    """

    try:
        SkillStatus(status)
    except ValueError as exc:
        raise ValueError(
            f"Invalid status: {status}"
        ) from exc

    validate_confidence(confidence)

    with db.session() as connection:
        skill = get_skill(connection, skill_name)

        if skill is None:
            raise ValueError(
                f"Skill '{skill_name}' not found."
            )

        old_status = skill["status"]
        old_confidence = skill["confidence"]

        new_confidence = (
            confidence
            if confidence is not None
            else old_confidence
        )

        now = utc_now()

        connection.execute(
            """
            UPDATE skills

            SET
                status = ?,
                confidence = ?,
                notes = ?,
                updated_at = ?

            WHERE id = ?
            """,
            (
                status,
                new_confidence,
                notes,
                now,
                skill["id"],
            ),
        )

        connection.execute(
            """
            INSERT INTO skill_history(
                skill_id,
                old_status,
                new_status,
                old_confidence,
                new_confidence,
                changed_at,
                notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skill["id"],
                old_status,
                status,
                old_confidence,
                new_confidence,
                now,
                notes,
            ),
        )

    logger.info(
        "Updated skill '%s': %s -> %s",
        skill["name"],
        old_status,
        status,
    )

    return {
        "success": True,
        "skill": skill["name"],
        "old_status": old_status,
        "new_status": status,
        "confidence": new_confidence,
        "updated_at": now,
    }


@mcp.tool
def get_weak_skills(
    threshold: int = WEAK_SKILL_THRESHOLD,
) -> list[RowDict]:
    """Return completed or active skills below a confidence threshold."""

    validate_confidence(threshold)

    with db.session() as connection:
        rows = connection.execute(
            """
            SELECT
                skills.name,
                skills.status,
                skills.confidence,
                categories.name AS category

            FROM skills

            JOIN categories
                ON skills.category_id = categories.id

            WHERE
                skills.status IN (?, ?)
                AND skills.confidence < ?

            ORDER BY skills.confidence ASC
            """,
            (
                SkillStatus.COMPLETED.value,
                SkillStatus.IN_PROGRESS.value,
                threshold,
            ),
        ).fetchall()

    return rows_to_dicts(rows)


# ============================================================================
# LEARNING SESSION TOOLS
# ============================================================================

@mcp.tool
def log_learning_session(
    skill_name: str,
    duration_minutes: int,
    topic: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Record a learning session for a skill."""

    if duration_minutes <= 0:
        raise ValueError(
            "Duration must be greater than zero."
        )

    with db.session() as connection:
        skill = get_skill(connection, skill_name)

        if skill is None:
            raise ValueError(
                f"Skill '{skill_name}' not found."
            )

        session_date = datetime.now(UTC).date().isoformat()

        connection.execute(
            """
            INSERT INTO learning_sessions(
                skill_id,
                session_date,
                duration_minutes,
                topic,
                notes
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                skill["id"],
                session_date,
                duration_minutes,
                topic,
                notes,
            ),
        )

    logger.info(
        "Logged %d minutes for '%s'.",
        duration_minutes,
        skill["name"],
    )

    return {
        "success": True,
        "skill": skill["name"],
        "duration_minutes": duration_minutes,
        "topic": topic,
        "date": session_date,
    }


@mcp.tool
def get_learning_stats() -> dict[str, Any]:
    """Return total learning time and recent sessions."""

    with db.session() as connection:
        total_minutes = connection.execute(
            """
            SELECT
                COALESCE(
                    SUM(duration_minutes),
                    0
                ) AS total_minutes

            FROM learning_sessions
            """
        ).fetchone()["total_minutes"]

        recent = connection.execute(
            """
            SELECT
                skills.name,
                learning_sessions.session_date,
                learning_sessions.duration_minutes,
                learning_sessions.topic

            FROM learning_sessions

            JOIN skills
                ON learning_sessions.skill_id = skills.id

            ORDER BY learning_sessions.id DESC

            LIMIT ?
            """,
            (RECENT_SESSION_LIMIT,),
        ).fetchall()

    return {
        "total_hours": round(total_minutes / 60, 2),
        "recent_sessions": rows_to_dicts(recent),
    }


# ============================================================================
# REVIEW TOOLS
# ============================================================================

@mcp.tool
def schedule_review(
    skill_name: str,
    days_from_now: int = 7,
) -> dict[str, Any]:
    """Schedule a future review for a skill."""

    if days_from_now < 0:
        raise ValueError(
            "days_from_now cannot be negative."
        )

    with db.session() as connection:
        skill = get_skill(connection, skill_name)

        if skill is None:
            raise ValueError(
                f"Skill '{skill_name}' not found."
            )

        review_date = (
            datetime.now(UTC)
            + timedelta(days=days_from_now)
        ).date().isoformat()

        connection.execute(
            """
            INSERT INTO reviews(
                skill_id,
                review_date,
                status,
                confidence
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                skill["id"],
                review_date,
                ReviewStatus.PENDING.value,
                skill["confidence"],
            ),
        )

    logger.info(
        "Review scheduled for '%s' on %s.",
        skill["name"],
        review_date,
    )

    return {
        "success": True,
        "skill": skill["name"],
        "review_date": review_date,
    }


@mcp.tool
def get_due_reviews() -> list[RowDict]:
    """Return reviews that are due today or overdue."""

    today = datetime.now(UTC).date().isoformat()

    with db.session() as connection:
        rows = connection.execute(
            """
            SELECT
                reviews.id,
                skills.name AS skill,
                reviews.review_date,
                reviews.status,
                skills.confidence

            FROM reviews

            JOIN skills
                ON reviews.skill_id = skills.id

            WHERE
                reviews.review_date <= ?
                AND reviews.status = ?

            ORDER BY reviews.review_date
            """,
            (
                today,
                ReviewStatus.PENDING.value,
            ),
        ).fetchall()

    return rows_to_dicts(rows)


# ============================================================================
# PROJECT TOOLS
# ============================================================================

@mcp.tool
def add_project(
    name: str,
    description: str = "",
    status: str = ProjectStatus.PLANNED.value,
    github_url: str = "",
    deployment_url: str = "",
) -> dict[str, Any]:
    """Add an AI Engineering project."""

    try:
        ProjectStatus(status)
    except ValueError as exc:
        raise ValueError(
            f"Invalid project status: {status}"
        ) from exc

    now = utc_now()

    try:
        with db.session() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    name,
                    description,
                    status,
                    github_url,
                    deployment_url,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    description,
                    status,
                    github_url,
                    deployment_url,
                    now,
                    now,
                ),
            )

    except sqlite3.IntegrityError as exc:
        logger.warning(
            "Project already exists: %s",
            name,
        )

        raise ValueError(
            f"Project '{name}' already exists."
        ) from exc

    logger.info("Project created: %s", name)

    return {
        "success": True,
        "project": name,
    }


@mcp.tool
def get_projects() -> list[RowDict]:
    """Return all AI Engineering projects."""

    with db.session() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                name,
                description,
                status,
                github_url,
                deployment_url,
                created_at,
                updated_at

            FROM projects

            ORDER BY id
            """
        ).fetchall()

    return rows_to_dicts(rows)


@mcp.tool
def link_project_to_skill(
    project_name: str,
    skill_name: str,
) -> dict[str, Any]:
    """Link an AI Engineering project to a skill."""

    with db.session() as connection:
        project = get_project(
            connection,
            project_name,
        )

        if project is None:
            raise ValueError(
                f"Project '{project_name}' not found."
            )

        skill = get_skill(
            connection,
            skill_name,
        )

        if skill is None:
            raise ValueError(
                f"Skill '{skill_name}' not found."
            )

        try:
            connection.execute(
                """
                INSERT INTO project_skills(
                    project_id,
                    skill_id
                )
                VALUES (?, ?)
                """,
                (
                    project["id"],
                    skill["id"],
                ),
            )

        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "Project and skill are already linked."
            ) from exc

    logger.info(
        "Linked project '%s' to skill '%s'.",
        project["name"],
        skill["name"],
    )

    return {
        "success": True,
        "project": project["name"],
        "skill": skill["name"],
    }


@mcp.tool
def get_project_skills(
    project_name: str,
) -> list[RowDict]:
    """Return skills demonstrated by a project."""

    with db.session() as connection:
        rows = connection.execute(
            """
            SELECT
                skills.name,
                categories.name AS category,
                skills.confidence

            FROM project_skills

            JOIN projects
                ON project_skills.project_id = projects.id

            JOIN skills
                ON project_skills.skill_id = skills.id

            JOIN categories
                ON skills.category_id = categories.id

            WHERE LOWER(projects.name) = LOWER(?)

            ORDER BY categories.id, skills.id
            """,
            (project_name,),
        ).fetchall()

    return rows_to_dicts(rows)


# ============================================================================
# DASHBOARD
# ============================================================================

def fetch_category_progress(
    connection: sqlite3.Connection,
) -> list[RowDict]:
    """Return progress grouped by category."""

    rows = connection.execute(
        """
        SELECT
            categories.name AS category,
            COUNT(skills.id) AS total,

            SUM(
                CASE
                    WHEN skills.status = ?
                    THEN 1 ELSE 0
                END
            ) AS completed,

            SUM(
                CASE
                    WHEN skills.status = ?
                    THEN 1 ELSE 0
                END
            ) AS in_progress,

            SUM(
                CASE
                    WHEN skills.status = ?
                    THEN 1 ELSE 0
                END
            ) AS remaining,

            ROUND(
                AVG(skills.confidence),
                2
            ) AS average_confidence

        FROM categories

        LEFT JOIN skills
            ON skills.category_id = categories.id

        GROUP BY categories.id

        ORDER BY categories.id
        """,
        (
            SkillStatus.COMPLETED.value,
            SkillStatus.IN_PROGRESS.value,
            SkillStatus.REMAINING.value,
        ),
    ).fetchall()

    result: list[RowDict] = []

    for row in rows:
        total = row["total"] or 0
        completed = row["completed"] or 0

        percentage = (
            round(completed / total * 100, 2)
            if total
            else 0.0
        )

        result.append(
            {
                "category": row["category"],
                "total": total,
                "completed": completed,
                "in_progress": row["in_progress"] or 0,
                "remaining": row["remaining"] or 0,
                "completion_percentage": percentage,
                "average_confidence":
                    row["average_confidence"] or 0,
            }
        )

    return result


@mcp.tool
def get_dashboard() -> dict[str, Any]:
    """Return the complete AI Engineering dashboard."""

    with db.session() as connection:

        overall = connection.execute(
            """
            SELECT
                COUNT(*) AS total,

                SUM(
                    CASE
                        WHEN status = ?
                        THEN 1 ELSE 0
                    END
                ) AS completed,

                SUM(
                    CASE
                        WHEN status = ?
                        THEN 1 ELSE 0
                    END
                ) AS in_progress,

                SUM(
                    CASE
                        WHEN status = ?
                        THEN 1 ELSE 0
                    END
                ) AS remaining,

                ROUND(
                    AVG(confidence),
                    2
                ) AS average_confidence

            FROM skills
            """,
            (
                SkillStatus.COMPLETED.value,
                SkillStatus.IN_PROGRESS.value,
                SkillStatus.REMAINING.value,
            ),
        ).fetchone()

        total = overall["total"] or 0
        completed = overall["completed"] or 0

        completion_percentage = (
            round(completed / total * 100, 2)
            if total
            else 0.0
        )

        categories = fetch_category_progress(
            connection
        )

        completed_skills = connection.execute(
            """
            SELECT
                skills.name,
                categories.name AS category,
                skills.confidence

            FROM skills

            JOIN categories
                ON skills.category_id = categories.id

            WHERE skills.status = ?

            ORDER BY categories.id, skills.id
            """,
            (SkillStatus.COMPLETED.value,),
        ).fetchall()

        in_progress_skills = connection.execute(
            """
            SELECT
                skills.name,
                categories.name AS category,
                skills.confidence

            FROM skills

            JOIN categories
                ON skills.category_id = categories.id

            WHERE skills.status = ?

            ORDER BY categories.id, skills.id
            """,
            (SkillStatus.IN_PROGRESS.value,),
        ).fetchall()

        weak_skills = connection.execute(
            """
            SELECT
                skills.name,
                categories.name AS category,
                skills.status,
                skills.confidence

            FROM skills

            JOIN categories
                ON skills.category_id = categories.id

            WHERE
                skills.status IN (?, ?)
                AND skills.confidence < ?

            ORDER BY skills.confidence ASC
            """,
            (
                SkillStatus.COMPLETED.value,
                SkillStatus.IN_PROGRESS.value,
                WEAK_SKILL_THRESHOLD,
            ),
        ).fetchall()

        learning = connection.execute(
            """
            SELECT
                COALESCE(
                    SUM(duration_minutes),
                    0
                ) AS total_minutes

            FROM learning_sessions
            """
        ).fetchone()

        today = datetime.now(UTC).date().isoformat()

        reviews = connection.execute(
            """
            SELECT
                skills.name AS skill,
                reviews.review_date,
                skills.confidence

            FROM reviews

            JOIN skills
                ON reviews.skill_id = skills.id

            WHERE
                reviews.review_date <= ?
                AND reviews.status = ?

            ORDER BY reviews.review_date
            """,
            (
                today,
                ReviewStatus.PENDING.value,
            ),
        ).fetchall()

        projects = connection.execute(
            """
            SELECT
                name,
                status,
                github_url,
                deployment_url

            FROM projects

            ORDER BY id
            """
        ).fetchall()

    total_learning_minutes = (
        learning["total_minutes"] or 0
    )

    return {
        "overall": {
            "total_skills": total,
            "completed": completed,
            "in_progress": overall["in_progress"] or 0,
            "remaining": overall["remaining"] or 0,
            "completion_percentage":
                completion_percentage,
            "average_confidence":
                overall["average_confidence"] or 0,
        },
        "categories": categories,
        "completed_skills":
            rows_to_dicts(completed_skills),
        "in_progress_skills":
            rows_to_dicts(in_progress_skills),
        "weak_skills":
            rows_to_dicts(weak_skills),
        "learning": {
            "total_minutes":
                total_learning_minutes,
            "total_hours":
                round(
                    total_learning_minutes / 60,
                    2,
                ),
        },
        "reviews_due":
            rows_to_dicts(reviews),
        "projects":
            rows_to_dicts(projects),
    }


# ============================================================================
# APPLICATION STARTUP
# ============================================================================

def initialize_application() -> None:
    """Initialize application dependencies."""

    logger.info("Starting %s", APP_NAME)
    logger.info("Database: %s", DATABASE_PATH)

    db.initialize()
    db.seed()


def main() -> None:
    """Application entry point."""

    initialize_application()

    logger.info("Starting MCP server.")

    mcp.run()


if __name__ == "__main__":
    main()