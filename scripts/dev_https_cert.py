"""Certificado local para servir o PWA em HTTPS durante o desenvolvimento (§6.1).

Câmera e Geolocation exigem contexto seguro fora de `localhost`. Este certificado é
autoassinado e serve **apenas** para desenvolvimento e teste em rede local; em
produção o TLS é do servidor/hospedagem e nada disto é usado.

    python scripts/dev_https_cert.py                    # localhost + 127.0.0.1
    python scripts/dev_https_cert.py --host 192.168.0.10  # inclui o IP da máquina

Os arquivos vão para `certs/`, que o Git ignora. Nunca versione chave privada.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "certs"
VALID_DAYS = 365


def build(hosts: list[str], output_dir: Path) -> tuple[Path, Path]:
    names: list[x509.GeneralName] = []
    for host in hosts:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UrMind desenvolvimento")])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = output_dir / "dev-cert.pem", output_dir / "dev-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", default=[], help="host/IP extra (repetível)")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)
    hosts = ["localhost", "127.0.0.1", *args.host]
    cert_path, key_path = build(hosts, args.out)
    print(f"certificado de desenvolvimento para {', '.join(hosts)}")
    print(f"  cert: {cert_path}")
    print(f"  key:  {key_path}  (nunca versionar)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
