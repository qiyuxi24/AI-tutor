"""运维后台 API 测试：登录 / 用户管理 / 管理员账户 / 审计日志。

用临时 sqlite 文件冒充主系统库（users + 图谱 nodes/edges），并把各数据目录一并
指到 tmp_path，绝不触碰真实 data/ 与 backend/data/。

运行：
    cd backend-admin
    venv/Scripts/python.exe -m pytest tests -q
"""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.admin_auth import hash_password, verify_password
from app.core.config import settings
from app.core.db import init_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TEXT DEFAULT (datetime('now')),
            role          TEXT DEFAULT 'user',
            status        TEXT DEFAULT 'active',
            last_login_at TEXT
        );
        INSERT INTO users (username, password_hash, role, status)
            VALUES ('alice', 'placeholder', 'user', 'active'),
                   ('bob',   'placeholder', 'user', 'active');

        CREATE TABLE nodes (
            id        TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            file_path TEXT NOT NULL,
            mastery   INTEGER DEFAULT 0,
            user_id   INTEGER
        );
        CREATE TABLE edges (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            from_node TEXT,
            to_node   TEXT,
            user_id   INTEGER
        );
        INSERT INTO nodes (id, name, file_path, mastery, user_id) VALUES
            ('n1', '节点一', 'nodes/n1.md', 80, 1),
            ('n2', '节点二', 'nodes/n2.md', 10, 1),
            ('n3', '别人节点', 'nodes/n3.md', 0, 2);
        INSERT INTO edges (from_node, to_node, user_id) VALUES ('n1', 'n2', 1);
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(settings, "db_path", str(db))
    monkeypatch.setattr(settings, "default_admin_password", "admin123")
    # 数据目录也必须指到 tmp：删号用例会 rmtree 这些路径
    monkeypatch.setattr(settings, "backend_data_dir", str(tmp_path / "backend_data"))
    monkeypatch.setattr(settings, "conversations_db", str(tmp_path / "conversations.db"))
    init_db()

    # Agent 运行记录库（backend/data/agent_runs）
    runs_db = tmp_path / "backend_data" / "agent_runs" / "agent_runs.db"
    runs_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(runs_db))
    conn.executescript(
        """
        CREATE TABLE agent_runs (run_id TEXT PRIMARY KEY, user_id INTEGER);
        INSERT INTO agent_runs VALUES ('r1', 1), ('r2', 1), ('r3', 2);
        """
    )
    conn.commit()
    conn.close()

    from app.main import app

    with TestClient(app) as c:
        yield c


def _auth(client) -> dict:
    r = client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "admin123"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login(client, username: str, password: str) -> dict:
    r = client.post(
        "/api/v1/admin/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------- 基础 ----------

def test_health(client):
    assert client.get("/health").json()["service"] == "admin-api"


def test_login_success_returns_token_and_admin(client):
    body = client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "admin123"}
    ).json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["admin"]["role"] == "super_admin"


def test_login_wrong_password_401(client):
    r = client.post("/api/v1/admin/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_users_endpoint_requires_token(client):
    assert client.get("/api/v1/admin/users").status_code == 401


def test_me_returns_current_admin(client):
    body = client.get("/api/v1/admin/me", headers=_auth(client)).json()
    assert body["username"] == "admin"


# ---------- 用户列表 ----------

def test_list_users_pagination_and_search(client):
    headers = _auth(client)

    body = client.get("/api/v1/admin/users", headers=headers).json()
    assert body["total"] == 2
    assert [u["username"] for u in body["users"]] == ["bob", "alice"]  # id DESC

    body = client.get("/api/v1/admin/users?search=ali", headers=headers).json()
    assert [u["username"] for u in body["users"]] == ["alice"]

    body = client.get("/api/v1/admin/users?page_size=1&page=1", headers=headers).json()
    assert len(body["users"]) == 1 and body["total"] == 2


def test_get_user_404(client):
    assert client.get("/api/v1/admin/users/999", headers=_auth(client)).status_code == 404


# ---------- 禁用 / 启用 ----------

def test_disable_then_enable_user(client):
    headers = _auth(client)

    assert client.post("/api/v1/admin/users/1/disable", headers=headers).status_code == 200
    assert client.get("/api/v1/admin/users/1", headers=headers).json()["status"] == "disabled"

    assert client.post("/api/v1/admin/users/1/enable", headers=headers).status_code == 200
    assert client.get("/api/v1/admin/users/1", headers=headers).json()["status"] == "active"


def test_disable_missing_user_404(client):
    assert client.post("/api/v1/admin/users/999/disable", headers=_auth(client)).status_code == 404


# ---------- 重置密码（回归：必须写 password_hash 列） ----------

def test_reset_password_writes_password_hash_column(client):
    headers = _auth(client)
    r = client.post(
        "/api/v1/admin/users/1/reset-password",
        json={"new_password": "brand-new-pw"},
        headers=headers,
    )
    assert r.status_code == 200, r.text

    conn = sqlite3.connect(settings.db_path)
    try:
        hashed = conn.execute("SELECT password_hash FROM users WHERE id = 1").fetchone()[0]
    finally:
        conn.close()

    assert hashed != "placeholder"
    assert verify_password("brand-new-pw", hashed)


def test_reset_password_rejects_short_password(client):
    r = client.post(
        "/api/v1/admin/users/1/reset-password",
        json={"new_password": "123"},
        headers=_auth(client),
    )
    assert r.status_code == 422


# ---------- 角色 ----------

def test_update_role_accepts_valid_and_rejects_unknown(client):
    headers = _auth(client)

    r = client.post("/api/v1/admin/users/1/role", json={"role": "admin"}, headers=headers)
    assert r.status_code == 200
    assert client.get("/api/v1/admin/users/1", headers=headers).json()["role"] == "admin"

    r = client.post("/api/v1/admin/users/1/role", json={"role": "teacher"}, headers=headers)
    assert r.status_code == 422


# ---------- 审计日志 ----------

def test_audit_logs_record_operations_with_real_ip(client):
    headers = _auth(client)
    client.post("/api/v1/admin/users/2/disable", headers=headers)

    body = client.get("/api/v1/admin/audit-logs", headers=headers).json()
    actions = [log["action"] for log in body["logs"]]
    assert "login" in actions and "disable_user" in actions

    disabled = next(log for log in body["logs"] if log["action"] == "disable_user")
    assert disabled["admin_username"] == "admin"
    assert disabled["target_username"] == "bob"
    assert disabled["ip_address"] == "testclient"  # 不再是写死的 127.0.0.1

    filtered = client.get("/api/v1/admin/audit-logs?action=login", headers=headers).json()
    assert {log["action"] for log in filtered["logs"]} == {"login"}


# ---------- 新建用户 ----------

def test_create_user_writes_bcrypt_hash_and_is_listed(client):
    headers = _auth(client)
    r = client.post(
        "/api/v1/admin/users",
        json={"username": "carol", "password": "carol-pw-1"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["username"] == "carol" and r.json()["status"] == "active"

    conn = sqlite3.connect(settings.db_path)
    try:
        hashed = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'carol'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert hashed != "carol-pw-1" and verify_password("carol-pw-1", hashed)

    assert client.get("/api/v1/admin/users?search=carol", headers=headers).json()["total"] == 1


def test_create_user_rejects_duplicate_and_short_username(client):
    headers = _auth(client)

    dup = client.post(
        "/api/v1/admin/users", json={"username": "alice", "password": "whatever-1"}, headers=headers
    )
    assert dup.status_code == 409

    short = client.post(
        "/api/v1/admin/users", json={"username": "xy", "password": "whatever-1"}, headers=headers
    )
    assert short.status_code == 422


def test_create_user_can_grant_admin_role(client):
    headers = _auth(client)
    r = client.post(
        "/api/v1/admin/users",
        json={"username": "dave", "password": "dave-pw-1", "role": "admin"},
        headers=headers,
    )
    assert r.json()["role"] == "admin"


# ---------- 查看用户数据 ----------

def test_user_stats_counts_graph_rows(client):
    headers = _auth(client)
    body = client.get("/api/v1/admin/users/1/stats", headers=headers).json()

    assert body["user"]["username"] == "alice"
    assert body["data"]["nodes"] == 2
    assert body["data"]["mastered_nodes"] == 1  # n1 mastery=80 ≥ 70
    assert body["data"]["edges"] == 1
    assert body["data"]["agent_runs"] == 2
    # 对话库/知识库文件不存在时必须回落成 0，而不是 500
    assert body["data"]["conversations"] == 0
    assert body["data"]["kb_documents"] == 0
    assert body["data"]["kb_chunks"] == 0


def test_user_stats_404_for_missing_user(client):
    assert client.get("/api/v1/admin/users/999/stats", headers=_auth(client)).status_code == 404


# ---------- 删除用户 ----------

def test_delete_user_requires_matching_confirm(client):
    headers = _auth(client)
    r = client.delete("/api/v1/admin/users/1?confirm=bob", headers=headers)
    assert r.status_code == 400
    assert client.get("/api/v1/admin/users/1", headers=headers).status_code == 200


def test_delete_user_removes_rows_and_files(client, tmp_path):
    headers = _auth(client)
    # 造出该用户遗留的磁盘数据，验证会被一并清掉
    for path in (
        Path(settings.db_path).parent / "nodes" / "1",
        Path(settings.backend_data_dir) / "kb" / "1",
        Path(settings.backend_data_dir) / "rag" / "1",
    ):
        path.mkdir(parents=True, exist_ok=True)
        (path / "leftover.txt").write_text("x", encoding="utf-8")

    r = client.delete("/api/v1/admin/users/1?confirm=alice", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["nodes"] == 2
    assert r.json()["deleted"]["agent_runs"] == 2

    assert client.get("/api/v1/admin/users/1", headers=headers).status_code == 404
    conn = sqlite3.connect(settings.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE user_id = 1").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM edges WHERE user_id = 1").fetchone()[0] == 0
        # 别人的数据不能跟着陪葬
        assert conn.execute("SELECT COUNT(*) FROM nodes WHERE user_id = 2").fetchone()[0] == 1
    finally:
        conn.close()

    runs_db = Path(settings.backend_data_dir) / "agent_runs" / "agent_runs.db"
    conn = sqlite3.connect(str(runs_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM agent_runs WHERE user_id = 1").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_runs WHERE user_id = 2").fetchone()[0] == 1
    finally:
        conn.close()

    assert not (Path(settings.db_path).parent / "nodes" / "1").exists()
    assert not (Path(settings.backend_data_dir) / "kb" / "1").exists()


def test_delete_user_is_audited(client):
    headers = _auth(client)
    client.delete("/api/v1/admin/users/2?confirm=bob", headers=headers)

    logs = client.get("/api/v1/admin/audit-logs?action=delete_user", headers=headers).json()
    assert logs["logs"][0]["target_username"] == "bob"
    assert "nodes=1" in logs["logs"][0]["details"]


# ---------- 管理员账户 CRUD ----------

def test_create_admin_then_new_admin_can_login(client):
    headers = _auth(client)
    r = client.post(
        "/api/v1/admin/admins",
        json={"username": "ops1", "password": "ops1-pw-1", "role": "admin"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "admin" and r.json()["is_active"] is True

    _login(client, "ops1", "ops1-pw-1")  # 能登录即密码落库正确


def test_create_admin_rejects_duplicate(client):
    headers = _auth(client)
    r = client.post(
        "/api/v1/admin/admins",
        json={"username": "admin", "password": "whatever-1"},
        headers=headers,
    )
    assert r.status_code == 409


def test_normal_admin_cannot_manage_admins(client):
    """普通 admin 只能管用户，管不了同行"""
    super_headers = _auth(client)
    client.post(
        "/api/v1/admin/admins",
        json={"username": "ops2", "password": "ops2-pw-1", "role": "admin"},
        headers=super_headers,
    )
    ops_headers = _login(client, "ops2", "ops2-pw-1")

    assert client.get("/api/v1/admin/admins", headers=ops_headers).status_code == 403
    assert (
        client.post(
            "/api/v1/admin/admins",
            json={"username": "ops3", "password": "ops3-pw-1"},
            headers=ops_headers,
        ).status_code
        == 403
    )
    # 但用户管理仍然可用
    assert client.get("/api/v1/admin/users", headers=ops_headers).status_code == 200


def test_disable_and_enable_admin_blocks_login(client):
    headers = _auth(client)
    created = client.post(
        "/api/v1/admin/admins",
        json={"username": "ops4", "password": "ops4-pw-1"},
        headers=headers,
    ).json()

    assert client.post(f"/api/v1/admin/admins/{created['id']}/disable", headers=headers).status_code == 200
    blocked = client.post("/api/v1/admin/login", json={"username": "ops4", "password": "ops4-pw-1"})
    assert blocked.status_code == 403

    assert client.post(f"/api/v1/admin/admins/{created['id']}/enable", headers=headers).status_code == 200
    _login(client, "ops4", "ops4-pw-1")


def test_admin_cannot_disable_or_delete_self(client):
    headers = _auth(client)
    me = client.get("/api/v1/admin/me", headers=headers).json()

    assert client.post(f"/api/v1/admin/admins/{me['id']}/disable", headers=headers).status_code == 400
    assert client.delete(f"/api/v1/admin/admins/{me['id']}", headers=headers).status_code == 400
    assert (
        client.post(
            f"/api/v1/admin/admins/{me['id']}/role", json={"role": "admin"}, headers=headers
        ).status_code
        == 400
    )


def test_delete_admin_removes_account_and_audits(client):
    headers = _auth(client)
    created = client.post(
        "/api/v1/admin/admins",
        json={"username": "ops5", "password": "ops5-pw-1"},
        headers=headers,
    ).json()

    assert client.delete(f"/api/v1/admin/admins/{created['id']}", headers=headers).status_code == 200
    assert client.post(
        "/api/v1/admin/login", json={"username": "ops5", "password": "ops5-pw-1"}
    ).status_code == 401

    logs = client.get("/api/v1/admin/audit-logs?action=delete_admin", headers=headers).json()
    assert logs["logs"][0]["target_username"] == "ops5"


def test_update_admin_role_and_reset_password(client):
    headers = _auth(client)
    created = client.post(
        "/api/v1/admin/admins",
        json={"username": "ops6", "password": "ops6-pw-1", "role": "admin"},
        headers=headers,
    ).json()

    r = client.post(
        f"/api/v1/admin/admins/{created['id']}/role", json={"role": "super_admin"}, headers=headers
    )
    assert r.status_code == 200
    _login(client, "ops6", "ops6-pw-1")  # 升级后仍可登录

    r = client.post(
        f"/api/v1/admin/admins/{created['id']}/reset-password",
        json={"new_password": "reset-pw-1"},
        headers=headers,
    )
    assert r.status_code == 200
    assert client.post(
        "/api/v1/admin/login", json={"username": "ops6", "password": "ops6-pw-1"}
    ).status_code == 401
    _login(client, "ops6", "reset-pw-1")


def test_guard_refuses_to_remove_the_only_active_super_admin():
    """最后超管保护：单超管库上禁用/降级/删除都必须被拦（当前 API 因自我保护不可达，
    故直接对守卫函数做单元测试）"""
    from fastapi import HTTPException

    from app.api.v1.admin.admins import _guard_not_last_super

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE admin_users (
            id TEXT PRIMARY KEY, username TEXT, role TEXT, is_active INTEGER
        );
        INSERT INTO admin_users VALUES ('a', 'admin', 'super_admin', 1),
                                       ('b', 'idle',  'admin',       0);
        """
    )
    solo_super = conn.execute("SELECT * FROM admin_users WHERE id = 'a'").fetchone()
    with pytest.raises(HTTPException) as err:
        _guard_not_last_super(conn, solo_super)
    assert err.value.status_code == 400

    # 再来一个启用的超管就放行
    conn.execute("INSERT INTO admin_users VALUES ('c', 'ops', 'super_admin', 1)")
    _guard_not_last_super(conn, solo_super)


# ---------- 自助改密 ----------

def test_change_own_password_requires_old_password(client):
    headers = _auth(client)

    wrong = client.post(
        "/api/v1/admin/me/password",
        json={"old_password": "nope", "new_password": "new-pw-1"},
        headers=headers,
    )
    assert wrong.status_code == 400

    ok = client.post(
        "/api/v1/admin/me/password",
        json={"old_password": "admin123", "new_password": "new-pw-1"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "admin123"}
    ).status_code == 401
    _login(client, "admin", "new-pw-1")
