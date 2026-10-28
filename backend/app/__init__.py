"""AI Job Hunter backend."""
try:  # Use the operating system's certificate store (like browsers), so HTTPS works behind company TLS proxies.
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - optional
    pass
