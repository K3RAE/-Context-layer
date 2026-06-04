"""
Shared fixtures for ContextLayer tests.
"""

import pytest
from typing import List, Dict


@pytest.fixture
def sample_conversation() -> List[Dict[str, str]]:
    """A 10-message sample conversation for general testing."""
    return [
        {"role": "user", "content": "I need to build a FastAPI backend for my project."},
        {"role": "assistant", "content": "Sure! Let's start by setting up the project structure. We'll use FastAPI with uvicorn as the ASGI server."},
        {"role": "user", "content": "What about the database? I'm thinking SQLite for simplicity."},
        {"role": "assistant", "content": "SQLite is a great choice for local-first applications. We can use the built-in sqlite3 module. Let me set up the schema."},
        {"role": "user", "content": "Can we add compression for the conversation data?"},
        {"role": "assistant", "content": "Yes, we can use LLMLingua-2 for intelligent compression. It uses a transformer model to identify and remove redundant tokens while preserving meaning."},
        {"role": "user", "content": "What if LLMLingua isn't available?"},
        {"role": "assistant", "content": "We'll implement a rule-based fallback that removes short pleasantries, truncates very long messages, and deduplicates consecutive assistant replies."},
        {"role": "user", "content": "Sounds good. Let's also add a handoff feature."},
        {"role": "assistant", "content": "The handoff feature will generate a structured context summary that can be pasted into a new LLM conversation to preserve context."},
    ]


@pytest.fixture
def large_conversation() -> List[Dict[str, str]]:
    """A 20-message conversation with substantial content for compression tests."""
    messages = []
    topics = [
        ("How do I set up a Python virtual environment?",
         "To set up a Python virtual environment, you can use the venv module that comes built-in with Python 3. Run 'python -m venv myenv' in your terminal. This creates an isolated environment where you can install packages without affecting your system Python installation. After creation, activate it with 'source myenv/bin/activate' on Linux/Mac or 'myenv\\Scripts\\activate' on Windows."),
        ("What's the difference between pip and conda?",
         "Pip and conda are both package managers, but they serve different purposes. Pip is Python-specific and installs packages from PyPI. Conda is a cross-platform package manager that can handle non-Python dependencies and manages environments natively. Conda is often preferred for data science workflows because it handles binary dependencies like BLAS, CUDA, and MKL automatically."),
        ("How do I handle environment variables in Python?",
         "The recommended approach is to use python-dotenv. Create a .env file in your project root with KEY=VALUE pairs. Then load them using 'from dotenv import load_dotenv; load_dotenv()'. Access values with os.getenv('KEY'). Never commit .env files to version control — add them to .gitignore."),
        ("Can you explain FastAPI dependency injection?",
         "FastAPI's dependency injection system is powerful and flexible. You define dependencies as functions or classes, then declare them in your route parameters using Depends(). FastAPI resolves the dependency graph automatically. Dependencies can have their own dependencies, creating a tree. Common uses include database sessions, authentication, and request validation."),
        ("What about error handling in FastAPI?",
         "FastAPI provides HTTPException for raising HTTP errors. You can also create custom exception handlers using @app.exception_handler(). For validation errors, FastAPI automatically returns 422 responses with detailed error messages. Use try/except blocks for application-level errors and convert them to appropriate HTTP responses."),
        ("How do I test FastAPI endpoints?",
         "Use the TestClient from fastapi.testclient for synchronous tests, or httpx.AsyncClient for async tests. Create a test client with TestClient(app) and make requests like response = client.post('/endpoint', json=data). Assert status codes and response bodies. Use pytest fixtures for setup and teardown."),
        ("What's the best way to structure a FastAPI project?",
         "A well-structured FastAPI project separates concerns: routes in routers/, business logic in services/, data models in models/, database operations in repositories/. Use APIRouter for modular routing. Keep main.py thin — it should only configure the app and include routers."),
        ("How do I add authentication?",
         "For JWT-based auth, use python-jose for token creation and verification. Create a /login endpoint that validates credentials and returns a JWT. Use a dependency function that extracts and verifies the token from the Authorization header. FastAPI's OAuth2PasswordBearer handles the token extraction automatically."),
        ("What about database migrations?",
         "Alembic is the standard migration tool for SQLAlchemy. Initialize with 'alembic init alembic', configure the database URL in alembic.ini, and create migrations with 'alembic revision --autogenerate'. Apply migrations with 'alembic upgrade head'. Always review auto-generated migrations before applying them."),
        ("Let's deploy this to production.",
         "For production deployment, use gunicorn with uvicorn workers: 'gunicorn main:app -k uvicorn.workers.UvicornWorker -w 4'. Set up a reverse proxy with nginx. Use Docker for containerization. Configure environment-specific settings via environment variables. Set up health check endpoints and monitoring with Prometheus/Grafana."),
    ]

    for user_msg, assistant_msg in topics:
        messages.append({"role": "user", "content": user_msg})
        messages.append({"role": "assistant", "content": assistant_msg})

    return messages
