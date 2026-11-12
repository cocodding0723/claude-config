"""HTTPS 요청용 공용 SSL 문맥. 검증은 항상 켬(끄는 경로 없음).

python.org 파이썬은 인증서 묶음이 없어 launchd 처럼 환경이 비면 CERTIFICATE_VERIFY_FAILED 가 남.
인증서 묶음 선택 순서: SSL_CERT_FILE → 기본 경로에 인증서가 있으면 기본 → 시스템 묶음 → certifi
"""
import os
import ssl

SYSTEM_BUNDLE = "/etc/ssl/cert.pem"  # macOS 시스템 인증서 묶음


def _certifi_path():
    try:
        import certifi
        return certifi.where()
    except Exception:
        return None


def pick_cafile(env=None, paths=None, system_bundle=SYSTEM_BUNDLE, certifi_path=_certifi_path):
    """사용할 인증서 파일 경로. None 이면 파이썬 기본 경로 사용"""
    env = os.environ if env is None else env
    paths = paths or ssl.get_default_verify_paths()
    explicit = env.get("SSL_CERT_FILE")
    if explicit and os.path.isfile(explicit):
        return explicit
    for p in (paths.cafile, paths.openssl_cafile):
        if p and os.path.isfile(p):
            return None
    if os.path.isfile(system_bundle):
        return system_bundle
    return certifi_path()


def ssl_context(**kw):
    cafile = pick_cafile(**kw)
    return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
