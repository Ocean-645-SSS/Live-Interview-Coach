"""Interview FastAPI 路由的依赖注入与错误映射测试。"""

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from liverag.api.interview_routes import get_interview_service, router
from liverag.interview.persistence.db import create_session_factory, create_sqlite_engine
from liverag.interview.persistence.models import Base
from liverag.interview.schemas import (
    InterviewConfig,
    InterviewDifficulty,
    InterviewPlan,
    InterviewQuestion,
    QuestionRubric,
    QuestionSource,
    QuestionType,
    RubricPoint,
)
from liverag.interview.application.service import InterviewService
from liverag.interview.persistence.sqlalchemy_repository import SQLAlchemyInterviewRepository


class _EvaluationServiceStub:
    def __init__(self):
        self.answer_ids: list[str] = []

    async def evaluate_answer(self, answer_id: str):
        self.answer_ids.append(answer_id)
        return {
            "evaluation": {"answer_id": answer_id},
            "decision": {"event_type": "NEXT_QUESTION"},
        }


def test_create_and_get_interview_through_routes(tmp_path: Path):
    engine = create_sqlite_engine(tmp_path / "routes.db")
    Base.metadata.create_all(engine)
    service = InterviewService(
        SQLAlchemyInterviewRepository(create_session_factory(engine))
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_interview_service] = lambda: service

    with TestClient(app) as client:
        created = client.post(
            "/api/interviews",
            json={"title": "API 测试", "config": {"question_count": 1}},
        )
        assert created.status_code == 200
        interview_id = created.json()["id"]

        fetched = client.get(f"/api/interviews/{interview_id}")
        missing = client.get("/api/interviews/missing")

    engine.dispose()
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "API 测试"
    assert missing.status_code == 404


def test_evaluation_route_calls_async_service_without_client_scores():
    stub = _EvaluationServiceStub()
    service = cast(InterviewService, stub)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_interview_service] = lambda: service

    with TestClient(app) as client:
        response = client.post("/api/interviews/answers/answer-1/evaluation")

    assert response.status_code == 200
    assert response.json()["evaluation"]["answer_id"] == "answer-1"
    assert stub.answer_ids == ["answer-1"]


def test_session_attempt_and_read_routes(tmp_path: Path):
    engine = create_sqlite_engine(tmp_path / "attempt-routes.db")
    Base.metadata.create_all(engine)
    service = InterviewService(
        SQLAlchemyInterviewRepository(create_session_factory(engine))
    )
    interview = service.create_interview(
        title="Attempt API 测试",
        config=InterviewConfig(question_count=1),
    )
    service.save_interview_plan(
        interview_id=interview.id,
        expected_version=interview.version,
        plan=InterviewPlan(
            id="plan-api",
            title="API 测试计划",
            introduction="现在开始面试。",
            config=InterviewConfig(question_count=1),
            questions=[
                InterviewQuestion(
                    id="question-api",
                    order=1,
                    type=QuestionType.TECHNICAL_KNOWLEDGE,
                    source=QuestionSource.QUESTION_BANK,
                    difficulty=InterviewDifficulty.INTERMEDIATE,
                    category="Python",
                    topics=["异步"],
                    question_text="什么是异步编程？",
                    objective="检查异步基础",
                    rubric=QuestionRubric(
                        expected_points=[RubricPoint(id="point-1", content="事件循环")]
                    ),
                )
            ],
            closing_message="面试结束。",
        ),
    )
    session = service.create_session(interview.id)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_interview_service] = lambda: service

    with TestClient(app) as client:
        created = client.post(f"/api/interviews/sessions/{session.id}/attempts")
        attempt_id = created.json()["id"]
        fetched_session = client.get(f"/api/interviews/sessions/{session.id}")
        fetched_attempt = client.get(f"/api/interviews/attempts/{attempt_id}")
        events = client.get(f"/api/interviews/sessions/{session.id}/events")
        answers = client.get(f"/api/interviews/sessions/{session.id}/answers")
        report = client.get(f"/api/interviews/sessions/{session.id}/report")

    engine.dispose()
    assert created.status_code == 200
    assert created.json()["session_id"] == session.id
    assert created.json()["room_name"].startswith("interview-")
    assert fetched_session.status_code == 200
    assert fetched_attempt.json()["state"] == "CREATED"
    assert events.json() == []
    assert answers.json() == []
    assert report.json() is None


def test_create_prepared_interview_uses_versioned_question_bank(tmp_path: Path):
    engine = create_sqlite_engine(tmp_path / "prepared-routes.db")
    Base.metadata.create_all(engine)
    service = InterviewService(
        SQLAlchemyInterviewRepository(create_session_factory(engine))
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_interview_service] = lambda: service

    with TestClient(app) as client:
        response = client.post(
            "/api/interviews/prepared",
            json={
                "title": "后端模拟面试",
                "config": {"question_count": 1, "difficulty": "INTERMEDIATE"},
            },
        )

    engine.dispose()
    assert response.status_code == 200
    assert response.json()["interview"]["state"] == "READY"
    assert response.json()["session"]["state"] == "READY"
    assert len(response.json()["plan"]["questions"]) == 1
