# AI Engineering Roadmap MCP

A local [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server for tracking progress through an AI engineering learning roadmap. The server exposes roadmap, learning-session, review, project, and dashboard tools backed by a local SQLite database.

## Features

- Seed a structured AI engineering roadmap on first run.

- Track each skill as `remaining`, `in_progress`, or `completed`.

- Record confidence scores from 0 to 10 and add notes.

- Search skills by name or roadmap category.

- Log learning sessions and review total learning time.

- Schedule spaced-review reminders and retrieve due reviews.

- Add portfolio projects and link them to demonstrated skills.

- Query an aggregate dashboard with category progress, weak skills, reviews, learning time, and projects.

- Persist data locally in SQLite; no external database or API key is required.

## Roadmap coverage

The initial seed includes these categories:

- Programming & Data Foundations

- Machine Learning

- Deep Learning

- Generative AI

- Agentic AI

- MLOps

- Production AI Engineering

- Projects

## Requirements

- Python 3.13 or newer

- [uv](https://docs.astral.sh/uv/) (recommended) or another PEP 517-compatible environment

The supported Python version is declared in `pyproject.toml` and `.python-version`.

## Installation

Clone the repository and install the locked dependencies:

```bash
git clone https://github.com/Div7anshKushwaha/ai-engineering-roadmap-mcp.git
cd ai-engineering-roadmap-mcp
uv sync
```

## Run the server

Start the MCP server over its default stdio transport:

```bash
uv run main.py
```

On first startup, the application creates and seeds `ai_roadmap.db` in the repository directory. The database is intentionally ignored by Git so personal progress is not committed.

## MCP client configuration

For an MCP client that supports a local stdio server, use the absolute path to this repository. For example:

```json
{
  "mcpServers": {
    "ai-engineering-roadmap": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/ai-engineering-roadmap-mcp",
        "run",
        "main.py"
      ]
    }
  }
}
```

Adjust the configuration format to match your MCP client. The server does not require an API key or remote service credentials.

## Available MCP tools

| Tool | Purpose |
| --- | --- |
| `get_progress` | Return overall skill counts, completion percentage, and average confidence. |
| `get_skills` | List all skills or filter by `completed`, `in_progress`, or `remaining`. |
| `search_skill` | Search skills by name or category. |
| `update_skill` | Update a skill's status, confidence, and notes; changes are recorded in history. |
| `get_weak_skills` | Find completed or active skills below a confidence threshold. |
| `log_learning_session` | Record time spent learning a skill, with an optional topic and note. |
| `get_learning_stats` | Return total learning time and the ten most recent sessions. |
| `schedule_review` | Schedule a future review for a skill. |
| `get_due_reviews` | List pending reviews due today or overdue. |
| `add_project` | Create an AI engineering project with optional repository and deployment URLs. |
| `get_projects` | List tracked projects. |
| `link_project_to_skill` | Associate a project with a roadmap skill. |
| `get_project_skills` | List the skills associated with a project. |
| `get_dashboard` | Return a complete roadmap dashboard in one response. |

### Common input rules

- Skill status must be one of `completed`, `in_progress`, or `remaining`.

- Confidence values must be integers from 0 through 10.

- Learning-session duration must be greater than zero minutes.

- Review offsets cannot be negative.

- Project status must be one of `planned`, `in_progress`, or `completed`.

## Data and persistence

The application stores the following locally in SQLite:

- Categories and skills

- Skill status, confidence, notes, and change history

- Learning sessions

- Scheduled reviews

- Projects and project-to-skill links

The database path is defined by `DATABASE_PATH` in `main.py` and defaults to `ai_roadmap.db` beside the script. The schema is created automatically at startup.

To reset local progress, stop the server and remove the generated database files:

```bash
rm -f ai_roadmap.db ai_roadmap.db-shm ai_roadmap.db-wal
```

This permanently deletes local tracking data, so export or back it up first if needed.

## Development

Install the locked environment and run a syntax check:

```bash
uv sync
uv run python -m py_compile main.py
```

The application logs startup and database activity to standard error/output using Python's standard `logging` module.

## Security notes

- No API keys, tokens, passwords, private keys, or other credentials are required by this project.

- Keep the generated SQLite database local because it contains personal learning progress and project metadata.

- Do not add `.env` files, credentials, or local database files to Git. The repository `.gitignore` already excludes `.env`, SQLite databases, virtual environments, caches, and logs.

## License

No license file is currently included. Unless a license is added to the repository, standard copyright rules apply and reuse is not automatically granted.