"""Offline host-script acceptance. Run with python3 -m unittest (no Frappe/site needed)."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infra/host/configure-public-access.sh"


@unittest.skipUnless(shutil.which("bash") and shutil.which("openssl"), "Linux Bash/OpenSSL required")
class PublicAccessSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.secrets = self.root / "secrets"
        self.secrets.mkdir()
        self.ufw = self.root / "ufw"
        self.ufw.mkdir()
        (self.ufw / "user.rules").write_text("original SSH/WireGuard rules\n")
        (self.ufw / "ufw.conf").write_text("ENABLED=yes\n")
        (self.root / "ufw-defaults").write_text("ENABLED=yes\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls"
        self.evidence = self.secrets / "public-access-evidence.txt"
        self.evidence.write_text("Operator reviewed ICP, provider firewall and privileged 2FA enrollment.\n")
        self.cert = self.secrets / "public.pem"
        self.key = self.secrets / "public.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                        "-subj", "/CN=ledger.example.com", "-addext", "subjectAltName=DNS:ledger.example.com",
                        "-keyout", str(self.key), "-out", str(self.cert)], check=True, capture_output=True)
        shutil.copy(self.cert, self.secrets / "internal-fullchain.pem")
        shutil.copy(self.key, self.secrets / "internal-privkey.pem")
        self.runtime = self.secrets / "runtime.env"
        self.original = ("SITE_NAME=ledger.example.com\nHTTP_BIND=10.77.0.1\nHTTPS_BIND=10.77.0.1\n"
                         "HTTP_PORT=80\nHTTPS_PORT=443\nPUBLIC_ACCESS_ENABLED=0\nPUBLIC_HOSTNAME=\n"
                         f"ICP_APPROVED=1\nTLS_CERTIFICATE_FILE={self.cert}\nTLS_PRIVATE_KEY_FILE={self.key}\n")
        self.runtime.write_text(self.original)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        FIXTURE_ROOT=str(self.root), SSL_CERT_FILE=str(self.cert),
                        PUBLIC_ACCESS_EVIDENCE_FILE=str(self.evidence), PRIVILEGED_2FA_CONFIRMED="1")
        self.fake("curl", '[[ ${*: -1} == "http://metadata.tencentyun.com/latest/meta-data/public-ipv4" ]] || exit 93\n'
                  'echo "93.184.216.34"\n')
        self.fake("getent", '[[ $* == "ahostsv4 ledger.example.com" ]] || exit 91\n'
                  'echo "${DNS_IPV4:-93.184.216.34} STREAM ledger.example.com"\n')
        self.fake("ufw", 'printf "ufw %s\\n" "$*" >> "$FIXTURE_ROOT/calls"\n'
                  '[[ ${FAIL_STAGE:-} != rollback-ufw || $1 != reload ]] || exit 31\n'
                  '[[ $1 != reload ]] || exit 0\n'
                  'if [[ $* == *"delete"* && ${FAIL_STAGE:-} == absent-rule ]]; then\n'
                  '  echo "Could not delete non-existent rule" >&2; exit 1\n'
                  'fi\n'
                  'echo changed >> "$FIXTURE_ROOT/ufw/user.rules"\n'
                  '[[ ${FAIL_STAGE:-} != ufw ]] || exit 29\n')
        self.fake("docker", 'printf "docker %s\\n" "$*" >> "$FIXTURE_ROOT/calls"\n'
                  'if [[ $* == *"exec -T backend python -"* ]]; then\n'
                  '  cat > "$FIXTURE_ROOT/security-check.py"\n'
                  '  [[ ${FAIL_STAGE:-} != security ]] || exit 27\n'
                  'elif [[ $* == *"up -d --no-deps --force-recreate gateway"* ]]; then\n'
                  '  [[ ${FAIL_STAGE:-} != rollback-gateway ]] || exit 28\n'
                  '  if [[ ${FAIL_STAGE:-} == restart && ! -f "$FIXTURE_ROOT/restart-failed" ]]; then\n'
                  '    touch "$FIXTURE_ROOT/restart-failed"; exit 28\n'
                  '  fi\n'
                  'elif [[ $* == *"exec -T gateway nginx -t"* ]]; then\n'
                  '  [[ ${FAIL_STAGE:-} != nginx ]] || exit 30\n'
                  'fi\n')
        self.fake("pwsh", 'printf "pwsh %s\\n" "$*" >> "$FIXTURE_ROOT/calls"\n'
                  '[[ ${FAIL_STAGE:-} != config ]] || exit 26\n')
        self.fake("systemctl", '[[ $* == "is-active --quiet wg-quick@wg0" ]] || exit 92\n')
        self.script = self.root / "configure.sh"

    def fake(self, name, content):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + content)
        path.chmod(0o755)

    def run_script(self, hostname="ledger.example.com"):
        self.assertTrue(SCRIPT.is_file(), "public-access script must exist")
        source = SCRIPT.read_text().replace("if [[ $EUID -ne 0 ]]; then", "if false; then")
        source = source.replace("secrets=/etc/selfquanta/ledger", f"secrets={self.secrets}")
        source = source.replace("ufw_dir=/etc/ufw", f"ufw_dir={self.ufw}")
        source = source.replace("ufw_defaults=/etc/default/ufw", f"ufw_defaults={self.root / 'ufw-defaults'}")
        self.script.write_text(source)
        return subprocess.run(["bash", str(self.script), hostname], env=self.env, text=True, capture_output=True)

    def assert_preflight_rejected(self, fragment, hostname="ledger.example.com"):
        result = self.run_script(hostname)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(fragment, result.stderr)
        self.assertEqual(self.runtime.read_text(), self.original)
        calls = self.log.read_text() if self.log.exists() else ""
        self.assertNotIn("ufw ", calls)
        self.assertNotIn("up -d", calls)

    def test_rejects_ip_and_malformed_hostname(self):
        for hostname in ("198.51.100.10", "localhost", "https://ledger.example.com", "bad_name.example.com"):
            with self.subTest(hostname=hostname):
                self.assert_preflight_rejected("DNS hostname", hostname)

    def test_requires_literal_runtime_icp_approval(self):
        self.original = self.original.replace("ICP_APPROVED=1", "ICP_APPROVED=0")
        self.runtime.write_text(self.original)
        self.env["ICP_APPROVED"] = "1"
        self.assert_preflight_rejected("ICP_APPROVED=1")

    def test_rejects_wrong_dns(self):
        self.env["DNS_IPV4"] = "198.51.100.11"
        self.assert_preflight_rejected("DNS")

    def test_rejects_multiple_dns_targets(self):
        self.fake("getent", 'printf "93.184.216.34 STREAM ledger.example.com\\n93.184.216.35 STREAM ledger.example.com\\n"\n')
        self.assert_preflight_rejected("DNS")

    def test_rejects_untrusted_certificate(self):
        self.env.pop("SSL_CERT_FILE")
        self.assert_preflight_rejected("not trusted")

    def test_rejects_missing_certificate(self):
        self.cert.unlink()
        self.assert_preflight_rejected("certificate/key")

    def test_rejects_certificate_hostname_mismatch(self):
        self.fake("getent", 'echo "93.184.216.34 STREAM wrong.example.com"\n')
        self.assert_preflight_rejected("certificate", "wrong.example.com")

    def test_rejects_mismatched_key(self):
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048",
                        "-out", str(self.key)], check=True, capture_output=True)
        self.assert_preflight_rejected("key")

    def test_requires_operator_evidence_and_two_factor_confirmation(self):
        self.evidence.unlink()
        self.assert_preflight_rejected("evidence")

    def test_security_check_failure_never_opens_firewall(self):
        self.env["FAIL_STAGE"] = "security"
        self.assert_preflight_rejected("security")

    def test_valid_public_cutover_changes_only_gateway_https(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = self.runtime.read_text()
        for entry in ("PUBLIC_ACCESS_ENABLED=1", "PUBLIC_HOSTNAME=ledger.example.com",
                      "HTTP_BIND=10.77.0.1", "HTTPS_BIND=0.0.0.0"):
            self.assertIn(entry + "\n", runtime)
        calls = self.log.read_text()
        self.assertIn("ufw insert 1 allow 443/tcp", calls)
        self.assertNotIn("ufw insert 1 allow 80/tcp", calls)
        self.assertNotIn("OpenSSH", calls)
        self.assertNotIn("51820", calls)
        self.assertLess(calls.index("pwsh "), calls.index("up -d"))
        self.assertNotIn("PRIVATE KEY", result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose parser required")
    def test_real_compose_parser_keeps_defaults_private_and_only_gateway_published(self):
        environment = dict(self.env, INTERNAL_LEDGER_IMAGE="internal-ledger:" + "1" * 40,
                           OCR_IMAGE="internal-ledger-ocr:" + "1" * 40, SITE_NAME="ledger.example.com",
                           DB_ROOT_PASSWORD_FILE=str(self.key), SITE_ADMIN_PASSWORD_FILE=str(self.key),
                           TLS_CERTIFICATE_FILE=str(self.cert), TLS_PRIVATE_KEY_FILE=str(self.key))
        environment.pop("HTTP_BIND", None)
        environment.pop("HTTPS_BIND", None)
        command = [shutil.which("docker"), "compose", "--env-file", str(ROOT / "infra/versions.env"),
                   "-f", str(ROOT / "infra/compose.yaml"), "config", "--format", "json"]
        for public in (False, True):
            if public:
                environment["HTTP_BIND"] = "10.77.0.1"
                environment["HTTPS_BIND"] = "0.0.0.0"
            result = subprocess.run(command, env=environment, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            services = json.loads(result.stdout)["services"]
            self.assertEqual([name for name, service in services.items() if service.get("ports")], ["gateway"])
            ports = services["gateway"]["ports"]
            self.assertEqual([(port["host_ip"], str(port["published"]), port["target"]) for port in ports],
                             [("10.77.0.1", "80", 8080), ("0.0.0.0" if public else "10.77.0.1", "443", 8443)])

    def test_transaction_restores_runtime_and_firewall_on_each_failure(self):
        for stage in ("config", "ufw", "restart", "nginx"):
            with self.subTest(stage=stage):
                self.env["FAIL_STAGE"] = stage
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.runtime.read_text(), self.original)
                self.assertEqual((self.ufw / "user.rules").read_text(), "original SSH/WireGuard rules\n")
                self.assertEqual((self.root / "ufw-defaults").read_text(), "ENABLED=yes\n")

    def test_absent_managed_firewall_rules_allow_first_cutover(self):
        self.env["FAIL_STAGE"] = "absent-rule"
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_recovery_requests_gateway_stop_and_reports_manual_intervention(self):
        for stage in ("rollback-ufw", "rollback-gateway"):
            with self.subTest(stage=stage):
                self.env["FAIL_STAGE"] = stage
                result = self.run_script()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ROLLBACK FAILED", result.stderr)
                self.assertIn("stop gateway", self.log.read_text())

    def test_private_rollback_needs_no_dns_or_icp_and_keeps_volumes(self):
        self.original = self.original.replace("PUBLIC_ACCESS_ENABLED=0", "PUBLIC_ACCESS_ENABLED=1")
        self.original = self.original.replace("HTTPS_BIND=10.77.0.1", "HTTPS_BIND=0.0.0.0")
        self.original = self.original.replace("ICP_APPROVED=1", "ICP_APPROVED=0")
        self.runtime.write_text(self.original)
        self.evidence.unlink()
        result = self.run_script("--private")
        self.assertEqual(result.returncode, 0, result.stderr)
        for entry in ("PUBLIC_ACCESS_ENABLED=0", "PUBLIC_HOSTNAME=", "HTTPS_BIND=10.77.0.1",
                      f"TLS_CERTIFICATE_FILE={self.secrets}/internal-fullchain.pem"):
            self.assertIn(entry + "\n", self.runtime.read_text())
        calls = self.log.read_text()
        self.assertIn("ufw insert 1 deny 443/tcp", calls)
        self.assertNotIn("down", calls)
        self.assertNotIn("volume", calls)

    def test_read_only_frappe_security_policy_rejects_each_unsafe_setting(self):
        policy = SCRIPT.read_text().split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
        settings = {"disable_signup": 1, "allow_consecutive_login_attempts": 5,
                    "allow_login_after_fail": 60, "enable_two_factor_auth": 1}
        fake = types.ModuleType("frappe")
        fake.init = lambda **kwargs: None
        fake.connect = lambda: None
        fake.destroy = lambda: None
        fake.conf = {"rate_limit": {"limit": 120, "window": 60}}
        fake.get_all = lambda doctype, **kwargs: (["named-admin@example.com"] if doctype == "Has Role" else ["System Manager"] if role_2fa else [])
        fake.get_doc = lambda doctype, user: types.SimpleNamespace(roles=[types.SimpleNamespace(role="System Manager")])
        user_bypass = False
        role_2fa = True
        fake.db = types.SimpleNamespace(
            get_single_value=lambda doctype, field: settings.get(field, 0),
            get_value=lambda doctype, name, field: (role_2fa if doctype == "Role" else
                                                    user_bypass if field == "bypass_restrict_ip_check_if_2fa_enabled" else 1))
        with patch.dict("sys.modules", frappe=fake), patch("sys.argv", ["security", "test.localhost"]):
            exec(policy, {})
            for field in tuple(settings):
                previous = settings[field]
                settings[field] = 0
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    exec(compile(policy, "security", "exec", optimize=2), {})
                settings[field] = previous
            for field in ("bypass_2fa_for_retricted_ip_users", "bypass_restrict_ip_check_if_2fa_enabled"):
                settings[field] = 1
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    exec(policy, {})
                settings[field] = 0
            for value in (None, {}, {"limit": 0, "window": 60}, {"limit": True, "window": 60}):
                fake.conf["rate_limit"] = value
                with self.subTest(rate=value), self.assertRaises(RuntimeError):
                    exec(policy, {})
            fake.conf["rate_limit"] = {"limit": 120, "window": 60}
            role_2fa = False
            with self.assertRaises(RuntimeError):
                exec(policy, {})
            role_2fa = True
            user_bypass = True
            with self.assertRaises(RuntimeError):
                exec(policy, {})

    def test_disabled_administrator_still_requires_two_factor_role(self):
        policy = SCRIPT.read_text().split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
        settings = {"disable_signup": 1, "allow_consecutive_login_attempts": 5,
                    "allow_login_after_fail": 60, "enable_two_factor_auth": 1}
        fake = types.ModuleType("frappe")
        fake.init = lambda **kwargs: None
        fake.connect = lambda: None
        fake.destroy = lambda: None
        fake.conf = {"rate_limit": {"limit": 120, "window": 60}}
        fake.get_all = lambda doctype, **kwargs: []
        fake.get_doc = lambda doctype, user: types.SimpleNamespace(roles=[])
        fake.db = types.SimpleNamespace(get_single_value=lambda doctype, field: settings.get(field, 0),
                                        get_value=lambda doctype, name, field: 0)
        with patch.dict("sys.modules", frappe=fake), patch("sys.argv", ["security", "test.localhost"]):
            with self.assertRaises(RuntimeError):
                exec(policy, {})


class ReproducibleImageTests(unittest.TestCase):
    def test_hrms_is_pinned_and_installed_in_deterministic_order(self):
        versions = (ROOT / "infra/versions.env").read_text()
        build = (ROOT / "infra/build_image.ps1").read_text()
        compose = (ROOT / "infra/compose.yaml").read_text()
        self.assertIn("HRMS_VERSION=v16.52.0", versions)
        self.assertEqual(build.count("url = 'https://github.com/frappe/hrms'"), 1)
        self.assertLess(build.index("url = 'https://github.com/frappe/erpnext'"),
                        build.index("url = 'https://github.com/frappe/hrms'"))
        self.assertLess(build.index("url = 'https://github.com/frappe/hrms'"),
                        build.index("url = $origin"))
        self.assertLess(compose.index("--install-app erpnext"), compose.index("--install-app hrms"))
        self.assertLess(compose.index("--install-app hrms"), compose.index("--install-app internal_ledger"))


if __name__ == "__main__":
    unittest.main()
