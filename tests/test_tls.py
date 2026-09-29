import shutil
import subprocess

import pytest

import hadro
from hadro.app import create_app
from hadro.cli import build_parser, main


@pytest.fixture(scope="module")
def cert(tmp_path_factory):
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    d = tmp_path_factory.mktemp("tls")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "2",
         "-keyout", str(d / "key.pem"), "-out", str(d / "cert.pem"), "-subj", "/CN=localhost",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        check=True, capture_output=True,
    )
    return str(d / "cert.pem"), str(d / "key.pem")


def test_https_with_trusted_self_signed_cert(cert, data_dir):
    import urllib.request
    import ssl

    with hadro.Server(data=str(data_dir), tls_cert=cert[0], tls_key=cert[1]) as server:
        assert server.endpoint.startswith("https://")
        context = ssl.create_default_context(cafile=cert[0])
        with urllib.request.urlopen(f"{server.endpoint}/health", context=context) as response:
            assert response.status == 200


def test_untrusted_cert_is_rejected_by_default_clients(cert, data_dir):
    import ssl
    import urllib.error
    import urllib.request

    with hadro.Server(data=str(data_dir), tls_cert=cert[0], tls_key=cert[1]) as server:
        with pytest.raises(urllib.error.URLError) as exc:
            urllib.request.urlopen(f"{server.endpoint}/health", context=ssl.create_default_context())
        assert isinstance(exc.value.reason, ssl.SSLCertVerificationError)


def test_plain_http_by_default(data_dir):
    with hadro.Server(data=str(data_dir)) as server:
        assert server.endpoint.startswith("http://")


def test_cert_and_key_must_come_together(data_dir, capsys):
    with pytest.raises(ValueError):
        create_app(hadro.Config(data=str(data_dir), tls_cert="cert.pem"))
    assert main([str(data_dir), "--tls-key", "key.pem"]) == 2
    assert "must be given together" in capsys.readouterr().err


def test_env_and_cli(monkeypatch):
    monkeypatch.setenv("HADRO_TLS_CERT", "c.pem")
    monkeypatch.setenv("HADRO_TLS_KEY", "k.pem")
    assert hadro.Config.from_env().tls_enabled
    args = build_parser().parse_args(["--tls-cert", "a", "--tls-key", "b"])
    assert (args.tls_cert, args.tls_key) == ("a", "b")
