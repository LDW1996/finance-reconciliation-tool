"""The fixed local account capability registry (no user administration)."""
ROLE_CAPABILITIES = {"excel": frozenset({"excel"}), "mail": frozenset({"mail"}), "ilog": frozenset({"ilog"}), "full": frozenset({"excel", "mail", "ilog"})}

def required_capability(path: str):
    if path in {"/api/analyze", "/api/analyze-combined", "/api/inspect-combined"}:
        return "excel"
    for capability in ("mail", "ilog"):
        if path.startswith(f"/api/{capability}/"):
            return capability
    return None
