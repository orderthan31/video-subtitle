from pathlib import Path
import hashlib
import shutil
import sqlite3
import sys
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "packages/shared"), str(ROOT / "apps/api")]
from fastapi.testclient import TestClient
from app.api import routes
from app.api.auth_routes import AuthConfig, configured_auth
from app.main import create_app
from app.services.upload_service import UploadService
from video_service.auth import AuthStore, LoginThrottled, password_hash, verify_password
from video_service.models import JobStatus
from video_service.repository import FilesystemJobRepository

PASSWORD = "test-only-password-123"


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.store = AuthStore(self.root / ".auth/accounts.sqlite3")

    def test_hash_validation_and_case_insensitive_accounts(self):
        encoded = password_hash(PASSWORD)
        self.assertNotIn(PASSWORD, encoded)
        self.assertTrue(verify_password(PASSWORD, encoded))
        self.assertFalse(verify_password("wrong-password", encoded))
        for value in (None, "short", "x" * 257):
            self.assertFalse(verify_password(value, encoded))
            with self.assertRaises(ValueError):
                password_hash(value)
        self.assertFalse(verify_password(PASSWORD, "invalid"))
        self.store.create_user("Alice", PASSWORD)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_user("alice", PASSWORD)
        with self.assertRaises(ValueError):
            self.store.create_user("../alice", PASSWORD)

    def test_sessions_store_only_token_hash_and_revoke_on_reset(self):
        user = self.store.create_user("alice", PASSWORD)
        login = self.store.login("ALICE", PASSWORD, "local")
        self.assertEqual(login["user"], user)
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM sessions").fetchone()
            self.assertEqual(row["token_hash"], hashlib.sha256(login["token"].encode()).hexdigest())
        self.assertNotIn(login["token"].encode(), self.store.path.read_bytes())
        self.assertEqual(self.store.session(login["token"])["user"], user)
        self.store.reset_password("alice", "replacement-password")
        self.assertIsNone(self.store.session(login["token"]))
        self.assertIsNone(self.store.login("alice", PASSWORD, "local"))
        again = self.store.login("alice", "replacement-password", "local")
        self.store.logout(again["token"])
        self.assertIsNone(self.store.session(again["token"]))

    def test_expiry_inactive_users_and_invalid_tokens(self):
        self.store.create_user("alice", PASSWORD)
        login = self.store.login("alice", PASSWORD, "local")
        with patch("video_service.auth.time.time", return_value=login["expires_at"]):
            self.assertIsNone(self.store.session(login["token"]))
        for token in (None, "invalid", "!" * 43):
            self.assertIsNone(self.store.session(token))
        with self.store.connection() as db:
            db.execute("UPDATE users SET active = 0")
        self.assertIsNone(self.store.session(login["token"]))
        self.assertIsNone(self.store.login("alice", PASSWORD, "local"))
        self.assertIsNone(self.store.login("missing", PASSWORD, "local"))

    def test_persistent_ip_limit_and_expiry(self):
        with patch("video_service.auth.time.time", return_value=1000):
            for _ in range(20):
                self.store.consume_login_attempt("local")
            with self.assertRaises(LoginThrottled) as caught:
                AuthStore(self.store.path).consume_login_attempt("local")
            self.assertGreater(caught.exception.retry_after, 0)
            self.store.consume_login_attempt("another-address")
        with patch("video_service.auth.time.time", return_value=1900):
            self.store.consume_login_attempt("local")

    def test_invalid_configuration_fails_closed(self):
        for values in ({"AUTH_ENABLED": "typo"}, {"AUTH_SESSION_HOURS": "0"},
                       {"AUTH_COOKIE_SECURE": "typo"}):
            with patch.dict("os.environ", values), self.assertRaises(ValueError):
                configured_auth(self.root)


class AuthApiTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "data/test-runs" / uuid4().hex
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.store = AuthStore(self.root / ".auth/accounts.sqlite3")
        self.alice = self.store.create_user("alice", PASSWORD)
        self.bob = self.store.create_user("bob", PASSWORD)
        self.repo = FilesystemJobRepository(self.root / "jobs")
        for patcher in (patch.object(routes, "repository", self.repo),
                        patch.object(routes, "upload_service", UploadService(self.repo)),
                        patch.object(routes, "storage_guard", Mock(spec=["assert_can_accept_upload"]))):
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch.dict("os.environ", {"AUTH_ENABLED": "false"}):
            self.app = create_app()
        self.app.state.auth = AuthConfig(self.store, secure=False)
        self.client = TestClient(self.app)
        self.other = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.other.close)
        self.payload = {"filename": "movie.mp4", "size": 3, "request_id": str(uuid4())}

    def login(self, client, username="alice"):
        result = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
        self.assertEqual(result.status_code, 200, result.text)
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        return result

    def create(self, client):
        response = client.post("/api/uploads", json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["job_id"]

    def test_login_csrf_logout_and_public_health(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        self.assertIsNone(self.client.get("/api/auth/session").json()["user"])
        result = self.login(self.client)
        self.assertIn("HttpOnly", result.headers["set-cookie"])
        self.assertIn("SameSite=strict", result.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/auth/session").json()["user"], self.alice)
        csrf = self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.client.post("/api/uploads", json=self.payload).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 403)
        self.client.headers["X-CSRF-Token"] = csrf
        old_cookie = self.client.cookies.get("video-session")
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertIsNone(self.store.session(old_cookie))
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)

    def test_secure_cookie_origin_and_generic_login_failure(self):
        self.app.state.auth.secure = True
        with TestClient(self.app, base_url="https://testserver") as client:
            blocked = client.post("/api/auth/login", headers={"Origin": "https://untrusted.example"},
                json={"username": "alice", "password": PASSWORD})
            self.assertEqual(blocked.status_code, 403)
            result = self.login(client)
            self.assertIn("__Host-video-session=", result.headers["set-cookie"])
            self.assertIn("Secure", result.headers["set-cookie"])
            self.assertEqual(client.get("/api/jobs").status_code, 200)
        responses = [self.client.post("/api/auth/login", json={"username": name, "password": "incorrect-password"})
            for name in ("alice", "missing")]
        self.assertEqual([r.status_code for r in responses], [401, 401])
        self.assertEqual(responses[0].json(), responses[1].json())
        for _ in range(20):
            try:
                self.store.consume_login_attempt("testclient")
            except LoginThrottled:
                break
        limited = self.client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
        self.assertEqual(limited.status_code, 429)
        self.assertIn("retry-after", limited.headers)

    def test_ownership_and_idempotency_are_scoped_per_user(self):
        self.login(self.client)
        self.login(self.other, "bob")
        first, second = self.create(self.client), self.create(self.other)
        self.assertNotEqual(first, second)
        self.assertEqual(self.create(self.client), first)
        self.assertEqual(self.repo.read(first).metadata["owner_id"], self.alice["id"])
        self.assertEqual([j["job_id"] for j in self.client.get("/api/jobs").json()["jobs"]], [first])
        cases = [("GET", f"/uploads/{first}", {}), ("GET", f"/jobs/{first}", {}),
            ("PUT", f"/uploads/{first}/chunks?offset=0", {"content": b"abc"}),
            ("POST", f"/uploads/{first}/complete", {}),
            ("POST", f"/uploads/{first}/verify", {"json": {"uploaded_bytes": 3, "offset": 0, "length": 3, "sha256": "0" * 64}}),
            ("GET", f"/jobs/{first}/subtitles", {}),
            ("PUT", f"/jobs/{first}/subtitles", {"json": {"revision": 1, "tracks": {"original": [], "translated": []}}}),
            ("POST", f"/jobs/{first}/cancel", {}), ("DELETE", f"/jobs/{first}", {}),
            ("GET", f"/jobs/{first}/results/final.mp4", {})]
        for method, path, options in cases:
            with self.subTest(path=path):
                response = self.other.request(method, "/api" + path, **options)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json(), self.other.get(f"/api/jobs/{uuid4().hex}").json())
        self.assertEqual(self.repo.read(first).uploaded_bytes, 0)
        self.assertEqual(self.client.put(f"/api/uploads/{first}/chunks?offset=0", content=b"abc").status_code, 200)
        self.assertEqual(self.client.post(f"/api/uploads/{first}/complete").status_code, 200)

    def test_owned_download_range_and_anonymous_isolation(self):
        self.login(self.client)
        job = self.create(self.client)
        (self.repo.job_dir(job) / "output/final.mp4").write_bytes(b"0123456789")
        self.repo.update_status(job, JobStatus.COMPLETED)
        url = f"/api/jobs/{job}/results/final.mp4"
        response = self.client.get(url, headers={"Range": "bytes=2-4"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"234")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.app.state.auth = AuthConfig(None)
        self.assertEqual(self.other.get("/api/jobs").json()["jobs"], [])
        self.assertEqual(self.other.get(url).status_code, 404)
        anonymous = self.create(self.other)
        self.app.state.auth = AuthConfig(self.store, secure=False)
        self.assertEqual(self.client.get(f"/api/jobs/{anonymous}").status_code, 404)
        self.assertEqual([j["job_id"] for j in self.client.get("/api/jobs").json()["jobs"]], [job])

    def test_shared_cookie_account_switch_rejects_the_previous_tabs_session(self):
        first = self.login(self.client)
        self.create(self.client)
        second = self.login(self.other, "bob")
        bob_job = self.create(self.other)
        self.client.headers["X-Session-Token"] = first.json()["csrf_token"]
        self.client.cookies.update(self.other.cookies)
        # Another tab changed the shared cookie, but this tab still represents Alice.
        for method, path, options in [("GET", "/api/jobs", {}),
                ("POST", "/api/uploads", {"json": self.payload}),
                ("POST", "/api/auth/logout", {})]:
            with self.subTest(path=path):
                result = self.client.request(method, path, **options)
                self.assertEqual(result.status_code, 401, result.text)
        self.assertEqual(len(self.repo.list()), 2)
        self.assertEqual(self.other.get("/api/auth/session").json()["user"], self.bob)
        self.client.headers["X-Session-Token"] = second.json()["csrf_token"]
        self.assertEqual([j["job_id"] for j in self.client.get("/api/jobs").json()["jobs"]], [bob_job])


if __name__ == "__main__":
    unittest.main()
