"""长期能力画像只读 API 的合同测试。"""

from datetime import datetime, timezone
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from liverag.api.interview_routes import get_interview_service, router
from liverag.interview.application.service import InterviewService
from liverag.interview.persistence.repository import RecordNotFoundError


class _SkillProgressServiceStub:
    def get_skill_progress_dashboard(self, candidate_kb_id: str):
        assert candidate_kb_id == "default"
        return {
            "candidate_profile_id": "candidate_profile_default",
            "taxonomy_version": 1,
            "skills": [
                {
                    "skill_key": "skill_python",
                    "source_evaluation_ids": ["evaluation_1"],
                }
            ],
            "recommendations": [],
        }

    def get_skill_progress_detail(self, *, candidate_kb_id: str, skill_key: str):
        assert candidate_kb_id == "default"
        if skill_key != "skill_python":
            raise RecordNotFoundError(f"技能画像不存在：{skill_key}")
        return {
            "skill_key": skill_key,
            "trend": [
                {
                    "evaluation_id": "evaluation_1",
                    "session_id": "session_1",
                    "interview_id": "interview_1",
                    "question_id": "question_1",
                    "score": 45.0,
                    "rubric_version": 1,
                    "evaluated_at": datetime(
                        2026, 8, 10, tzinfo=timezone.utc
                    ).isoformat(),
                }
            ],
        }


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    service = cast(InterviewService, _SkillProgressServiceStub())
    app.dependency_overrides[get_interview_service] = lambda: service
    return TestClient(app)


def test_get_skill_progress_dashboard():
    with _client() as client:
        response = client.get(
            "/api/interviews/skill-progress?candidate_kb_id=default"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["candidate_profile_id"] == "candidate_profile_default"
    assert body["taxonomy_version"] == 1
    assert body["skills"][0]["source_evaluation_ids"] == ["evaluation_1"]


def test_get_skill_detail_contains_trend_and_sources():
    with _client() as client:
        response = client.get(
            "/api/interviews/skill-progress/skill_python?candidate_kb_id=default"
        )

    assert response.status_code == 200
    assert response.json()["trend"][0]["evaluation_id"] == "evaluation_1"


def test_skill_progress_rejects_empty_candidate_kb_id():
    with _client() as client:
        response = client.get("/api/interviews/skill-progress?candidate_kb_id=")

    assert response.status_code == 422


def test_missing_skill_progress_returns_404():
    with _client() as client:
        response = client.get(
            "/api/interviews/skill-progress/skill_missing?candidate_kb_id=default"
        )

    assert response.status_code == 404
