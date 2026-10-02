import pytest
import pytest_asyncio

import server


@pytest_asyncio.fixture
async def app(tmp_path):
    original = server.db
    server.db = server.Database(tmp_path / "test.db")
    await server.db.initialize()
    await server.db.seed()
    yield server
    server.db = original


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_progress_is_valid(app):
    before = await app.get_progress()
    await app.db.seed()
    after = await app.get_progress()
    assert before["total"] == after["total"]
    assert before["completed"] == 0
    assert after["weighted_completion_percentage"] == 0.0


@pytest.mark.asyncio
async def test_skill_update_preserves_notes_when_omitted_and_records_history(app):
    await app.update_skill("Python", "in_progress", confidence=5, notes="Keep this")
    result = await app.update_skill("Python", "completed", confidence=8)
    assert result["new_status"] == "completed"
    rows = await app.get_skills(status="completed")
    python = next(row for row in rows if row["name"] == "Python")
    assert python["notes"] == "Keep this"
    assert python["confidence"] == 8


@pytest.mark.asyncio
async def test_evidence_prerequisite_and_unblocked_skill(app):
    await app.update_skill("Python", "completed", confidence=8, readiness="implemented")
    await app.add_skill_evidence("Python", "implemented_from_scratch", "Python exercises")
    await app.add_prerequisite("RAG", "Python")
    unblocked = await app.get_unblocked_skills()
    assert any(row["name"] == "RAG" for row in unblocked)
    evidence = await app.get_skill_evidence("Python")
    assert evidence[0]["evidence_type"] == "implemented_from_scratch"


@pytest.mark.asyncio
async def test_review_completion_creates_next_review(app):
    await app.schedule_review("Python", 0)
    result = await app.complete_review("Python", 8, "Could explain the topic")
    assert result["interval_days"] == 7
    due = await app.get_due_reviews()
    assert all(row["skill"] != "Python" for row in due)


@pytest.mark.asyncio
async def test_project_link_adds_evidence_and_dashboard(app):
    await app.add_project("Test RAG", technologies=["Python", "RAG"])
    result = await app.link_project_to_skill("Test RAG", "RAG")
    assert result["evidence_added"] is True
    dashboard = await app.get_dashboard()
    assert any(row["name"] == "Test RAG" for row in dashboard["projects"])


@pytest.mark.asyncio
async def test_learning_and_tasks(app):
    await app.log_learning_session("Python", 45, "asyncio")
    stats = await app.get_learning_stats()
    assert stats["total_minutes"] == 45
    task = await app.create_task("Write async tests", skill_name="Python", estimated_minutes=30)
    plan = await app.get_today_plan(60)
    assert any(row["id"] == task["task_id"] for row in plan)
    await app.complete_task(task["task_id"])
    assert not any(row["id"] == task["task_id"] for row in await app.get_today_plan(60))
