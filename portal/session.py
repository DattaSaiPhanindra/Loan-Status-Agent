import time
import uuid

_sessions: dict[str, dict] = {}


def create_session(username: str) -> str:
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "id": session_id,
        "created_at": time.time(),
        "username": username,
    }
    return session_id


def validate_session(session_id: str, ttl: int) -> bool:
    session = _sessions.get(session_id)
    if session is None:
        return False
    return (time.time() - session["created_at"]) < ttl


def get_session(session_id: str) -> dict | None:
    return _sessions.get(session_id)


def delete_session(session_id: str) -> None:
    _sessions.pop(session_id, None)
