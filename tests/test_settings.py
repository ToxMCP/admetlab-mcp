from admetlab_mcp.settings import Settings


def test_fallback_endpoints_accept_csv_string():
    settings = Settings(admet_fallback_endpoints="/api/one,api/two")
    assert settings.admet_fallback_endpoints == ["/api/one", "/api/two"]


def test_fallback_endpoints_accept_json_array_string():
    settings = Settings(admet_fallback_endpoints='["/api/one", "api/two"]')
    assert settings.admet_fallback_endpoints == ["/api/one", "/api/two"]


def test_admet_endpoint_normalizes_leading_slash():
    settings = Settings(admet_endpoint="api/admet")
    assert settings.admet_endpoint == "/api/admet"
