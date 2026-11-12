"""공용 SSL 문맥 시험. 네트워크 없이 인증서 파일 선택 순서와 검증 유지만 확인."""
import os
import ssl
import sys
import tempfile
import unittest
from collections import namedtuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hubs", "scripts"))
import tls_ctx  # noqa: E402

Paths = namedtuple("Paths", "cafile openssl_cafile")
NOPATHS = Paths(None, "/없는/경로/cert.pem")


class PickCafile(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.env_file, self.sys_file, self.default_file = (os.path.join(d, n) for n in ("env.pem", "sys.pem", "default.pem"))
        for f in (self.env_file, self.sys_file, self.default_file):
            open(f, "w").close()

    def pick(self, env=None, paths=NOPATHS, system=None, certifi=lambda: None):
        return tls_ctx.pick_cafile(env=env or {}, paths=paths, system_bundle=system or "/없는/시스템.pem", certifi_path=certifi)

    def test_env_wins(self):
        got = self.pick({"SSL_CERT_FILE": self.env_file}, Paths(self.default_file, None), self.sys_file)
        self.assertEqual(got, self.env_file)

    def test_missing_env_file_ignored(self):
        self.assertEqual(self.pick({"SSL_CERT_FILE": "/없는/파일"}, system=self.sys_file), self.sys_file)

    def test_default_path_with_cert_used_as_is(self):
        self.assertIsNone(self.pick(paths=Paths(self.default_file, None), system=self.sys_file))

    def test_no_default_cert_picks_system_bundle(self):
        self.assertEqual(self.pick(system=self.sys_file, certifi=lambda: "/certifi.pem"), self.sys_file)

    def test_certifi_last(self):
        self.assertEqual(self.pick(certifi=lambda: self.env_file), self.env_file)

    def test_nothing_found_is_none(self):
        self.assertIsNone(self.pick())


class Context(unittest.TestCase):
    def test_verification_stays_on(self):
        ctx = tls_ctx.ssl_context(env={}, paths=NOPATHS)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    @unittest.skipUnless(os.path.isfile(tls_ctx.SYSTEM_BUNDLE), "시스템 묶음 없음")
    def test_system_bundle_loaded_when_default_missing(self):
        ctx = tls_ctx.ssl_context(env={}, paths=NOPATHS)
        self.assertGreater(len(ctx.get_ca_certs()), 0)


if __name__ == "__main__":
    unittest.main()
