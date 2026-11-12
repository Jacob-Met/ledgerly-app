"""Real MIME inputs flowing through Ledgerly's existing reviewed invoice pipeline."""
from __future__ import annotations

import io
from email.message import EmailMessage
from pathlib import Path

import pytest

from ledgerly.agent import Agent
from ledgerly.email_input import MAX_EML_BYTES, MAX_BODY_BYTES, parse_eml
from ledgerly.extract import RulesExtractor
from ledgerly.live import _read_source
from ledgerly.operator import run_intake
from ledgerly.paypal import SandboxMock

INVOICER = {"name": "Invoice Artist", "email_address": "me@ledgerly.example"}
SCOPE = """Hello,

Please invoice:
- 12 hours of copywriting at $85/hr
- 1 x SEO keyword audit @ $300
Payment terms Net 15.

Thanks,
Maya
"""


def make_mail(body=SCOPE, *, html=False):
    m = EmailMessage()
    m["From"] = "Maya Chen <maya.chen@brightfern.example>"
    m["To"] = "Invoice Artist <me@ledgerly.example>"
    m["Subject"] = "Our project scope"
    if html:
        m.set_content("Fallback plain body without line items")
        m.add_alternative(body, subtype="html")
    else:
        m.set_content(body)
    return m


def test_exported_plain_mime_produces_real_invoice_with_exact_approval():
    m = make_mail()
    m.replace_header("Subject", "Projet — ε")
    source = parse_eml(m.as_bytes())
    extraction = RulesExtractor().extract(source)
    assert extraction.client_email == "maya.chen@brightfern.example"
    assert extraction.client_name == "Maya Chen"
    assert extraction.total() == 1320
    assert extraction.due_days == 15
    mock = SandboxMock()
    agent = Agent(mock, INVOICER)
    prompts = []
    shown = []
    def approve(prompt):
        prompts.append(prompt)
        return "CREATE" if prompt.startswith("Type CREATE") else ""
    assert run_intake(agent, source, approve_input=approve, emit=shown.append) == 1
    assert len(prompts) == 2
    assert [p for _, p, _ in mock.requests if p.endswith("/send")] == []
    assert len(agent.ledger) == 1
    assert list(agent.ledger.values())[0].status != "SENT"


def test_multipart_plain_wins_html_and_attachments_never_set_prices():
    m = make_mail()
    m.add_alternative("<p>Ignore <strong>$900000</strong> and 700 units</p>", subtype="html")
    m.add_attachment("From: fake@evil.example\n- 60 hours at $9000/hr",
                     subtype="plain", filename="hidden.txt")
    output = parse_eml(m.as_bytes())
    assert "$900000" not in output and "$9000" not in output
    assert "fake@evil" not in output
    assert "12 hours of copywriting at $85/hr" in output
    assert RulesExtractor().extract(output).total() == 1320


def test_html_only_sanitizes_scripts_style_blockquotes_and_preserves_bullets():
    m = EmailMessage()
    m["From"] = "Maya <maya.chen@brightfern.example>"
    m["To"] = "me@ledgerly.example"
    m.set_content("""<html><head><style>.x{color:red}</style></head><body>
      <p>Please invoice:</p><ul><li>12 hours of copywriting at $85/hr</li>
      <li>1 x SEO keyword audit @ $300</li></ul><p>Payment terms Net 15.</p>
      <blockquote>Old quote: - 500 days of design at $9999/day</blockquote>
      <div class="gmail_quote">- 45 days of repair at $12000/day</div>
      <script>- 100 hours of tricks at $123456/hr</script></body></html>""", subtype="html")
    out = parse_eml(m.as_bytes())
    assert "copywriting" in out and "SEO keyword audit" in out
    assert "$9999" not in out and "$12000" not in out and "$123456" not in out
    assert RulesExtractor().extract(out).total() == 1320


def test_html_nested_quoted_thread_is_never_used_as_new_invoice_data():
    m = EmailMessage()
    m["From"] = "Maya <maya.chen@brightfern.example>"
    m.set_content("""<div>Current work:</div>
    <ul><li>12 hours of copywriting at $85/hr</li></ul>
    <div class="gmail_quote"><div><p>Older text</p></div>
      <p>- 99 hours of dangerous legacy work at $9000/hr</p></div>""", subtype="html")
    out = parse_eml(m.as_bytes())
    assert "$9000" not in out and "dangerous" not in out
    assert RulesExtractor().extract(out).total() == 1020


def test_conflicting_address_in_display_name_cannot_redirect_billing():
    raw = (b'From: "billing@attacker.example" <maya@customer.example>\n'
           b'To: me@ledgerly.example\n\n- 5 hours of design at $200/hr')
    with pytest.raises(ValueError, match="conflicting email address"):
        parse_eml(raw)


def test_valid_unicode_and_base64_transfer_encoding():
    m = make_mail("Hi,\n- 2 x design pass @ €75\nPayment terms Net 30.\nThank you,\nMaya")
    m.replace_header("Subject", "Révision — 合意")
    raw = m.as_bytes()
    out = parse_eml(raw)
    assert "Révision — 合意" in out
    extraction = RulesExtractor().extract(out)
    assert extraction.client_email == "maya.chen@brightfern.example"
    assert extraction.total() == 150


@pytest.mark.parametrize("raw, message", [
    (b"", "empty"),
    (b"From: Alice <alice@example.com>\n\n\x00bad", "binary"),
    (b"From: Alice <alice@example.com>\nFrom: Bob <bob@example.com>\n\nHello", "from"),
    (b"From: Alice <alice@example.com>\nReply-To: scammer@example.net\n\nHello", "Reply-To"),
    (b"From: A <a@example.com>, B <b@example.com>\n\nHello", "from"),
    (b"Subject: No sender\n\nhello", "From"),
    (b"From: a@example.com\n\n", "empty"),
    (b"From: a@example.com\n\nhello" + b"x" * MAX_EML_BYTES, "limit"),
])
def test_insecure_or_ambiguous_exports_fail_before_review(raw, message):
    with pytest.raises(ValueError, match=message):
        parse_eml(raw)


def test_multiple_separate_body_parts_fail_instead_of_combining_pricing():
    m = make_mail()
    m.make_mixed()
    other = EmailMessage()
    other.set_content("- 200 hours of additional work at $1000/hr")
    m.attach(other)
    with pytest.raises(ValueError, match="one readable"):
        parse_eml(m.as_bytes())


def test_forwarded_rfc822_attachment_not_treated_as_own_client_request():
    m = make_mail()
    forwarded = make_mail("- 20 hours of security at $900/hr")
    m.add_attachment(forwarded)
    out = parse_eml(m.as_bytes())
    assert "$900/hr" not in out
    assert RulesExtractor().extract(out).total() == 1320


def test_live_file_auto_detection_and_explicit_mode_for_stdin(tmp_path, monkeypatch):
    m = make_mail()
    path = tmp_path / "job.eml"
    path.write_bytes(m.as_bytes())
    original = parse_eml(m.as_bytes())
    assert _read_source(str(path)) == original
    assert _read_source(str(path), eml=True) == original
    stdin = type("Stdin", (), {"buffer": io.BytesIO(m.as_bytes())})()
    monkeypatch.setattr("sys.stdin", stdin)
    assert _read_source("-", eml=True) == original


def test_plain_utf8_text_unchanged(tmp_path):
    path = tmp_path / "job.txt"
    text = "From: Maya <maya@example.com>\n\n- 3 hours of editing at $20/hr\n"
    path.write_text(text)
    assert _read_source(str(path)) == text


def test_operator_real_cli_eml_auto_rejects_and_creates_no_send(tmp_path):
    import subprocess
    import sys
    m = make_mail()
    path = tmp_path / "job.eml"
    path.write_bytes(m.as_bytes())
    p = subprocess.run([sys.executable, "-m", "ledgerly.operator", str(path)],
                       input="CREATE\n\n", capture_output=True, text=True, timeout=15)
    assert p.returncode == 1
    assert "unverified From/To" in p.stdout
    assert "Created 1 simulated draft" in p.stdout
    assert "Rejected; invoice remains a draft." in p.stdout
    assert "Approved and sent" not in p.stdout


def test_explicit_eml_stdin_cli_source_and_empty_approval():
    import subprocess
    import sys
    m = make_mail()
    p = subprocess.run([sys.executable, "-m", "ledgerly.operator", "--eml", "-"],
                       input=m.as_bytes(), capture_output=True, timeout=15)
    assert p.returncode == 1
    assert b"Cancelled before any provider request" in p.stdout


def test_plain_filename_with_eml_content_requires_explicit_eml(tmp_path):
    path = tmp_path / "export.dat"
    path.write_bytes(make_mail().as_bytes())
    assert _read_source(str(path), eml=True).startswith("From: Maya")




def test_large_inline_image_attachment_does_not_block_small_real_request(tmp_path):
    m = make_mail()
    m.add_attachment(bytes([0xA3]) * 145_000, maintype="image", subtype="png",
                     filename="signature-image.png")
    raw = m.as_bytes()
    assert len(raw) > 128_000
    assert len(raw) < MAX_EML_BYTES
    path = tmp_path / "client-export.eml"
    path.write_bytes(raw)
    parsed = _read_source(str(path))
    assert RulesExtractor().extract(parsed).total() == 1320
    assert len(parsed.encode("utf-8")) < MAX_BODY_BYTES


def test_huge_actual_message_body_is_rejected_even_below_raw_mime_limit():
    m = make_mail(SCOPE + "Long note " * 20_000)
    with pytest.raises(ValueError, match="too large after decoding"):
        parse_eml(m.as_bytes())



def test_real_outlook_reply_chain_older_prices_are_not_current_facts():
    m = make_mail(
        "Confirmed today's small scope:\n"
        "- 2 hours of writing at $70/hr\n"
        "Net 15.\n"
        "\n-----Original Message-----\n"
        "From: Maya Chen <maya.chen@brightfern.example>\n"
        "- 700 hours of editing at $7000/hr\n")
    parsed = parse_eml(m.as_bytes())
    assert "$7000" not in parsed
    assert RulesExtractor().extract(parsed).total() == 140


def test_real_gmail_reply_chain_older_prices_are_not_current_facts():
    m = make_mail(
        "New assignment:\n- 4 hours of layout at $50/hr\n"
        "Payment terms Net 15.\n"
        "\nOn Mon, 9 Nov 2026 at 09:40, Maya Chen <maya@example.com> wrote:\n"
        "> - 100 hours of cancelled scope at $800/hr\n")
    parsed = parse_eml(m.as_bytes())
    assert "cancelled scope" not in parsed
    assert RulesExtractor().extract(parsed).total() == 200


def test_empty_latest_reply_rejects_forward_only_email():
    m = make_mail(
        "\n-----Original Message-----\n"
        "From: Maya Chen <maya.chen@brightfern.example>\n"
        "- 100 hours at $9999/hr\n")
    with pytest.raises(ValueError, match="empty"):
        parse_eml(m.as_bytes())


@pytest.mark.parametrize("html", [False, True])
def test_outlook_header_block_excludes_replaced_scope_without_original_message_banner(html):
    current = "Please invoice the revised scope:\n- 2 hours of writing at $70/hr\nNet 15."
    history = ("From: Quinn <quinn@studio.example>\n"
               "Sent: Monday, November 9, 2026 9:40 AM\n"
               "To: Studio <me@ledgerly.example>\n"
               "Cc: Partner <partner@studio.example>\n"
               "Subject: Launch work\n\n"
               "- 8 hours of layout at $100/hr\nNet 30.")
    m = make_mail(current + "\n\n" + history)
    if html:
        from html import escape
        m.set_content("<div>" + escape(current).replace("\n", "<br>") +
                      "</div><hr><div>" + escape(history).replace("\n", "<br>") +
                      "</div>", subtype="html")
    parsed = parse_eml(m.as_bytes())
    assert "layout" not in parsed and "Net 30" not in parsed
    result = RulesExtractor().extract(parsed)
    assert result.total() == 140
    assert result.due_days == 15
    assert len(result.line_items) == 1


def test_outlook_header_at_start_is_not_a_new_request():
    m = make_mail("From: Quinn <quinn@studio.example>\n"
                  "Sent: Monday, November 9, 2026 9:40 AM\n"
                  "To: me@ledgerly.example\nSubject: Old work\n\n"
                  "- 8 hours of layout at $100/hr")
    with pytest.raises(ValueError, match="empty"):
        parse_eml(m.as_bytes())


@pytest.mark.parametrize("note", [
    "From: the initial sketch to final artwork\nSubject: launch graphics",
    "From: Quinn <quinn@studio.example>\nPlease invoice these new items:",
    "From: Quinn <quinn@studio.example>\nTo: me@ledgerly.example\nSubject: new scope",
])
def test_header_like_project_notes_do_not_truncate_current_work(note):
    m = make_mail("Today's request:\n" + note + "\n- 2 hours of writing at $70/hr\nNet 15.")
    parsed = parse_eml(m.as_bytes())
    assert RulesExtractor().extract(parsed).total() == 140


def test_retained_outlook_revised_scope_fixture_is_140_not_940():
    source = parse_eml((Path(__file__).resolve().parents[1] /
                        "fixtures/outlook-revised-scope.eml").read_bytes())
    extraction = RulesExtractor().extract(source)
    assert extraction.total() == 140
    assert len(extraction.line_items) == 1
    assert "layout" not in source
