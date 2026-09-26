from agent_swarm.textutil import (
    extract_urls,
    is_placeholder,
    nfkc_handle,
    normalize_host,
    redaction_types,
)

ZWSP = "\u200b"


def test_extract_urls_handles_adversarial_text():
    text = (
        f"see https://r.jina.ai/https://x.org/a?b=1).{ZWSP}<script>alert(1)</script> http://...html"
    )
    assert extract_urls(text) == ["https://r.jina.ai/https://x.org/a?b=1", "http://...html"]


def test_extract_urls_keeps_balanced_parentheses():
    assert extract_urls("(https://en.wikipedia.org/wiki/Foo_(bar))") == [
        "https://en.wikipedia.org/wiki/Foo_(bar)"
    ]


def test_extract_urls_dedupes_in_order():
    assert extract_urls("http://a.org/1 http://b.org http://a.org/1") == [
        "http://a.org/1",
        "http://b.org",
    ]


def test_extract_urls_empty_inputs():
    assert extract_urls(None) == []
    assert extract_urls("") == []
    assert extract_urls("x" * 5_000_000) == []


def test_is_placeholder():
    assert is_placeholder("https://[SERVICE HOST 1]/x")
    assert is_placeholder("[SHORTENER CODE 225495]")
    assert is_placeholder("[REDACTED:destination:000002]")
    assert is_placeholder("[API KEY REDACTED]")
    assert not is_placeholder("https://example.org/[x]")


def test_normalize_host():
    assert normalize_host("https://WWW.Example.org:443/a") == "example.org"
    assert normalize_host("r.jina.ai") == "r.jina.ai"
    assert normalize_host("http://10.0.0.1:8080/") == "10.0.0.1"
    assert normalize_host("https://api.datausa.io./x") == "api.datausa.io"


def test_normalize_host_rejects_placeholders_and_junk():
    assert normalize_host("https://[SERVICE HOST 1]/x") is None
    assert normalize_host("http://...html") is None
    assert normalize_host("http://localhost/") is None
    assert normalize_host("") is None
    assert normalize_host("http://exa mple.org") is None


def test_redaction_types():
    text = "a [REDACTED:secret_key] b [REDACTED:destination:000002] [REDACTED:secret_key]"
    assert redaction_types(text) == ["destination", "secret_key"]
    assert redaction_types(None) == []


def test_nfkc_handle_folds_lookalikes():
    assert nfkc_handle(f"ＯｐｅｎＡＩ{ZWSP}Researcher") == "openairesearcher"
    assert nfkc_handle("  AgentRelent ") == "agentrelent"


def test_placeholder_forms_seen_in_swarmtraces_and_collusion():
    for s in [
        "[CREDENTIAL 3405]",
        "[REDIRECT URL 122]",
        "[SHORTENER-1]",
        "[ENCODED BLOB]",
        "[REDACTED SENSITIVE CONTENT]",
        "[SERVICE 2 URL 3]",
        "[operational URL omitted; host=jqp.vercel.app; sha256=abc]",
    ]:
        assert is_placeholder(s), s


def test_redaction_types_include_untyped_placeholders():
    text = (
        "x-api-key [CREDENTIAL 1] to [REDIRECT URL 9] via [SHORTENER CODE 5] [SHORTENER-1] "
        "[SERVICE HOST 1]/[SERVICE 2 URL 3] [ENCODED BLOB] [API KEY REDACTED] "
        "[REDACTED SENSITIVE CONTENT] [REDACTED:username:000001] "
        "[operational URL omitted; host=a.b; sha256=c]"
    )
    assert redaction_types(text) == [
        "api_key",
        "credential",
        "encoded_blob",
        "operational_url",
        "redirect_url",
        "sensitive_content",
        "service",
        "shortener",
        "username",
    ]
