"""Interview FastAPI 路由的依赖注入与错误映射测试。"""

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from liverag.api.interview_routes import get_interview_service, router
from liverag.interview.db import create_session_factory, create_sqlite_engine
from liverag.interview.models import Base
from liverag.interview.service import InterviewService
from liverag.interview.sqlalchemy_repository import SQLAlchemyInterviewRepository


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
