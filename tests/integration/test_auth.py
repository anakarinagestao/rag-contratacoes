"""Authentication and authorization against isolated SQLite databases."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import jwt
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
    from app.core.config import Settings, settings
    from app.core.database import create_sqlite_engine, get_db, init_db
    from app.core.security import create_access_token, hash_password, verify_password
    from app.main import app

from app import create_admin
from app.modules.admin.services import AdminAlreadyExistsError, create_first_admin
from app.modules.auth.model import User
from app.modules.auth.schemas import UserCreate
from app.modules.auth.services import UserConflictError, create_user


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.password_hash = hash_password(cls.password)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = (Path(self.directory.name) / "auth.db").as_posix()
        self.engine = create_sqlite_engine(f"sqlite:///{path}")
        self.addCleanup(self.engine.dispose)
        init_db(self.engine)
        with Session(self.engine) as db:
            db.add_all([
                User(id=1, username="admin", email="admin@example.com", role="admin",
                     password_hash=self.password_hash),
                User(id=2, username="ordinary", email="user@example.com",
                     password_hash=self.password_hash),
            ])
            db.commit()
        self.key = secrets.token_urlsafe(48)
        secret_patch = patch.object(settings, "jwt_secret_key", SecretStr(self.key))
        secret_patch.start()
        self.addCleanup(secret_patch.stop)
        ttl_patch = patch.object(settings, "jwt_access_token_expire_minutes", 30)
        ttl_patch.start()
        self.addCleanup(ttl_patch.stop)
        init_patch = patch("app.main.init_db", side_effect=lambda: init_db(self.engine))
        init_patch.start()
        self.addCleanup(init_patch.stop)

        def test_db():
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = test_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = self.enterContext(TestClient(app))
        self.admin_headers = {"Authorization": f"Bearer {create_access_token(1)}"}
        self.user_headers = {"Authorization": f"Bearer {create_access_token(2)}"}

    def test_login_and_me_hide_secrets(self):
        response = self.client.post("/auth/login", json={"username": " ADMIN ", "password": self.password})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()
        self.assertEqual(data["token_type"], "bearer")
        self.assertEqual(data["expires_in"], 1800)
        claims = jwt.decode(data["access_token"], self.key, algorithms=["HS256"])
        self.assertEqual(claims["sub"], "1")
        self.assertEqual(claims["exp"] - claims["iat"], 1800)
        self.assertNotIn("role", claims)
        result = self.client.get("/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["username"], "admin")
        self.assertNotIn("password_hash", result.json())
        self.assertNotIn(self.password, result.text)

    def test_wrong_password_unknown_user_and_inactive_user_have_same_error(self):
        with Session(self.engine) as db:
            db.get(User, 2).is_active = False
            db.commit()
        messages = []
        for username, password in [("admin", "wrong"), ("missing", self.password), ("ordinary", self.password)]:
            response = self.client.post("/auth/login", json={"username": username, "password": password})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["www-authenticate"], "Bearer")
            messages.append(response.json())
        self.assertEqual(messages[0], messages[1])
        self.assertEqual(messages[1], messages[2])

    def test_all_protected_routes_require_auth_and_admin_routes_require_role(self):
        endpoints = [("GET", "/admin/users"), ("GET", "/admin/users/2"),
                     ("PATCH", "/admin/users/2/status")]
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/auth/me", headers=self.user_headers).status_code, 200)
        for method, path in endpoints:
            kwargs = {"json": {"is_active": False}} if method == "PATCH" else {}
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method, path, **kwargs).status_code, 401)
                response = self.client.request(method, path, headers=self.user_headers, **kwargs)
                self.assertEqual(response.status_code, 403)

    def test_admin_can_create_each_role_and_login(self):
        for role in ("user", "admin"):
            with self.subTest(role=role):
                data = {"username": f"new_{role}", "email": f"new_{role}@example.com",
                        "password": self.password, "role": role}
                response = self.client.post("/admin/users", headers=self.admin_headers, json=data)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()["role"], role)
                self.assertTrue(response.json()["is_active"])
                self.assertNotIn("password_hash", response.json())
                self.assertNotIn(self.password, response.text)
                with Session(self.engine) as db:
                    user = db.get(User, response.json()["id"])
                    self.assertEqual(user.role, role)
                    self.assertTrue(user.password_hash.startswith("$argon2id$"))
                    self.assertTrue(verify_password(self.password, user.password_hash))
                login = self.client.post("/auth/login", json={"username": data["username"], "password": self.password})
                self.assertEqual(login.status_code, 200)
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                self.assertEqual(self.client.get("/auth/me", headers=headers).json()["role"], role)
                self.assertEqual(self.client.get("/admin/users", headers=headers).status_code,
                                 200 if role == "admin" else 403)

    def test_admin_creation_requires_admin_and_validates_role_and_duplicates(self):
        data = {"username": "created", "email": "created@example.com", "password": self.password}
        for headers, expected in (({}, 401), (self.user_headers, 403)):
            for role in ("user", "admin"):
                response = self.client.post("/admin/users", headers=headers, json={**data, "role": role})
                self.assertEqual(response.status_code, expected)
        for invalid in ({"role": "owner"}, {"role": None}, {"is_active": False}, {"password": "short"}):
            response = self.client.post("/admin/users", headers=self.admin_headers, json={**data, **invalid})
            self.assertEqual(response.status_code, 422)
        for duplicate in ({"username": "ADMIN"}, {"email": "ADMIN@example.com"}):
            response = self.client.post("/admin/users", headers=self.admin_headers, json={**data, **duplicate})
            self.assertEqual(response.status_code, 409)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(User)), 2)
        response = self.client.post("/admin/users", headers=self.admin_headers, json=data)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["role"], "user")

    def test_admin_list_detail_and_pagination(self):
        response = self.client.get("/admin/users?offset=1&limit=1", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([user["id"] for user in response.json()], [2])
        self.assertNotIn("password_hash", response.text)
        response = self.client.get("/admin/users/2", headers=self.admin_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "user")
        self.assertEqual(self.client.get("/admin/users?limit=101", headers=self.admin_headers).status_code, 422)
        for method, path in [("GET", "/admin/users/999"), ("PATCH", "/admin/users/999/status")]:
            kwargs = {"json": {"is_active": False}} if method == "PATCH" else {}
            self.assertEqual(self.client.request(method, path, headers=self.admin_headers, **kwargs).status_code, 404)

    def test_deactivation_blocks_existing_token_and_reactivation_allows_login(self):
        response = self.client.patch("/admin/users/2/status", headers=self.admin_headers, json={"is_active": False})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])
        self.assertEqual(self.client.get("/auth/me", headers=self.user_headers).status_code, 401)
        response = self.client.patch("/admin/users/2/status", headers=self.admin_headers, json={"is_active": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"username": "ordinary", "password": self.password}).status_code, 200)

    def test_self_deactivation_and_mass_assignment_are_rejected(self):
        self.assertEqual(self.client.patch("/admin/users/1/status", headers=self.admin_headers,
                                          json={"is_active": False}).status_code, 409)
        for data in ({"is_active": True, "role": "admin"}, {"is_active": "false"}, {}):
            self.assertEqual(self.client.patch("/admin/users/2/status", headers=self.admin_headers,
                                              json=data).status_code, 422)
        with self.assertRaises(ValidationError):
            UserCreate(username="hacker", email="hacker@example.com", password=self.password, role="admin")
        for extra in ({"role": "admin"}, {"is_active": False}):
            response = self.client.post("/auth/register", json={
                "username": "hacker", "email": "hacker@example.com",
                "password": self.password, **extra,
            })
            self.assertEqual(response.status_code, 422)
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(User).where(User.username == "hacker")))

    def test_public_registration_can_login_but_cannot_access_admin(self):
        data = {"username": "newuser", "email": "new@example.com", "password": self.password}
        response = self.client.post("/auth/register", json=data)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["role"], "user")
        self.assertTrue(response.json()["is_active"])
        self.assertNotIn("password_hash", response.json())
        self.assertNotIn(self.password, response.text)
        with Session(self.engine) as db:
            saved = db.get(User, response.json()["id"])
            self.assertTrue(saved.password_hash.startswith("$argon2id$"))
            self.assertTrue(verify_password(self.password, saved.password_hash))
        login = self.client.post("/auth/login", json={"username": data["username"], "password": self.password})
        self.assertEqual(login.status_code, 200)
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(self.client.get("/auth/me", headers=headers).json()["id"], response.json()["id"])
        self.assertEqual(self.client.get("/admin/users", headers=headers).status_code, 403)

    def test_registration_rejects_duplicates_and_invalid_input(self):
        valid = {"username": "newuser", "email": "new@example.com", "password": self.password}
        for duplicate in ({"username": "ADMIN"}, {"email": "ADMIN@example.com"}):
            response = self.client.post("/auth/register", json={**valid, **duplicate})
            self.assertEqual(response.status_code, 409)
        for invalid in ({"email": "invalid"}, {"password": "short"}, {"username": "a"}):
            self.assertEqual(self.client.post("/auth/register", json={**valid, **invalid}).status_code, 422)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(User)), 2)

    def test_expired_forged_malformed_or_incomplete_tokens_are_rejected(self):
        now = datetime.now(timezone.utc)
        claims = {"sub": "1", "iat": now, "exp": now + timedelta(minutes=1)}
        tokens = [
            "not-a-token",
            jwt.encode(claims, secrets.token_urlsafe(48), algorithm="HS256"),
            jwt.encode(claims, self.key, algorithm="HS384"),
            jwt.encode(claims, "", algorithm="none"),
            jwt.encode({**claims, "exp": now - timedelta(seconds=1)}, self.key, algorithm="HS256"),
            jwt.encode({**claims, "iat": now + timedelta(minutes=5)}, self.key, algorithm="HS256"),
        ]
        for required in ("sub", "exp", "iat"):
            tokens.append(jwt.encode({k: v for k, v in claims.items() if k != required}, self.key, algorithm="HS256"))
        for subject in ("999", "-1", "abc", "9" * 100, 1, None, ""):
            tokens.append(jwt.encode({**claims, "sub": subject}, self.key, algorithm="HS256"))
        for index, token in enumerate(tokens):
            with self.subTest(case=index):
                response = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertEqual(self.client.get("/auth/me", headers={"Authorization": "Basic ignored"}).status_code, 401)

    def test_database_role_and_existence_are_checked_for_every_request(self):
        with Session(self.engine) as db:
            db.get(User, 1).role = "user"
            db.commit()
        self.assertEqual(self.client.get("/admin/users", headers=self.admin_headers).status_code, 403)
        self.assertEqual(self.client.get("/auth/me", headers=self.admin_headers).json()["role"], "user")
        with Session(self.engine) as db:
            db.execute(delete(User).where(User.id == 2))
            db.commit()
        self.assertEqual(self.client.get("/auth/me", headers=self.user_headers).status_code, 401)

    def test_password_hash_and_unique_account_constraints(self):
        data = UserCreate(username="newuser", email="new@example.com", password=self.password)
        with Session(self.engine) as db:
            user = create_user(db, data)
            self.assertEqual(user.role, "user")
            self.assertTrue(user.is_active)
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
            self.assertNotEqual(user.password_hash, self.password)
            self.assertTrue(verify_password(self.password, user.password_hash))
            self.assertFalse(verify_password("wrong", user.password_hash))
            for duplicate in (
                UserCreate(username="NEWUSER", email="other@example.com", password=self.password),
                UserCreate(username="other", email="NEW@example.com", password=self.password),
            ):
                with self.assertRaises(UserConflictError):
                    create_user(db, duplicate)
            db.add(User(username="badrole", email="bad@example.com", role="owner", password_hash=self.password_hash))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

    def test_bootstrap_refuses_existing_admin_or_account_collision(self):
        data = UserCreate(username="bootstrap", email="boot@example.com", password=self.password)
        with Session(self.engine) as db:
            with self.assertRaises(AdminAlreadyExistsError):
                create_first_admin(db, data)
            db.execute(delete(User).where(User.id == 1))
            db.commit()
            collision = UserCreate(username="ordinary", email="fresh@example.com", password=self.password)
            with self.assertRaises(UserConflictError):
                create_first_admin(db, collision)
            self.assertEqual(db.get(User, 2).role, "user")
            db.rollback()
            user = create_first_admin(db, data)
            self.assertEqual(user.role, "admin")
            self.assertTrue(verify_password(self.password, user.password_hash))

    def test_concurrent_bootstrap_creates_only_one_admin(self):
        with Session(self.engine) as db:
            db.execute(delete(User).where(User.role == "admin"))
            db.commit()

        def bootstrap(index):
            data = UserCreate(username=f"admin{index}", email=f"admin{index}@example.com", password=self.password)
            with Session(self.engine) as db:
                try:
                    create_first_admin(db, data)
                    return "created"
                except AdminAlreadyExistsError:
                    return "exists"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(bootstrap, (1, 2)))
        self.assertCountEqual(results, ["created", "exists"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(User).where(User.role == "admin")), 1)

    def test_cli_prompts_create_admin_without_printing_password(self):
        with Session(self.engine) as db:
            db.execute(delete(User).where(User.role == "admin"))
            db.commit()
        output = StringIO()
        with patch("app.create_admin.sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["cliadmin", "cli@example.com"]), \
             patch("app.create_admin.getpass", side_effect=[self.password, self.password]), \
             patch("app.create_admin.init_db", side_effect=lambda: init_db(self.engine)), \
             patch("app.create_admin.SessionLocal", sessionmaker(bind=self.engine)), \
             patch("sys.stdout", output):
            self.assertEqual(create_admin.main(), 0)
        self.assertNotIn(self.password, output.getvalue())
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(User).where(User.username == "cliadmin")).role, "admin")

    def test_cli_validation_does_not_echo_password(self):
        error_output = StringIO()
        with patch("app.create_admin.sys.stdin.isatty", return_value=True), \
             patch("builtins.input", side_effect=["cliadmin", "cli@example.com"]), \
             patch("app.create_admin.getpass", side_effect=["short", "short"]), \
             patch("sys.stderr", error_output):
            self.assertEqual(create_admin.main(), 1)
        self.assertNotIn("short", error_output.getvalue())

    def test_swagger_documents_bearer_and_keeps_login_public(self):
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/redoc").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["components"]["securitySchemes"]["BearerAuth"]["scheme"], "bearer")
        self.assertNotIn("security", schema["paths"]["/auth/login"]["post"])
        registration = schema["paths"]["/auth/register"]["post"]
        self.assertNotIn("security", registration)
        self.assertIn("201", registration["responses"])
        self.assertIn("409", registration["responses"])
        self.assertIn("application/json", schema["paths"]["/auth/login"]["post"]["requestBody"]["content"])
        for path, operations in schema["paths"].items():
            if path.startswith("/admin/") or path == "/auth/me":
                for operation in operations.values():
                    self.assertIn({"BearerAuth": []}, operation["security"])
        self.assertNotIn("password_hash", schema["components"]["schemas"]["UserResponse"]["properties"])
        self.assertEqual(self.client.get("/").json(), {"message": "API funcionando"})

    def test_api_rejects_empty_secret_and_configuration_rejects_unsafe_values(self):
        with patch.object(settings, "jwt_secret_key", SecretStr("")):
            with self.assertRaisesRegex(RuntimeError, "JWT_SECRET_KEY"):
                with TestClient(app):
                    pass
        for options in ({"jwt_algorithm": "none"}, {"jwt_access_token_expire_minutes": 0}):
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, database_url="sqlite:///:memory:", **options)


if __name__ == "__main__":
    unittest.main()
