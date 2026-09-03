"""Automated delivery: what gets sent, to whom, and what happens when it fails.

Two properties matter more than the rest and are tested hardest.

Delivery is best-effort. A Resend outage, a bad key or a rejected sender must
cost the caller a warning line, never the analysis they just paid a deck-sized
model call for — so every entry point returns an outcome dict and nothing here
raises.

The email must not become a second source of truth. Its figures come from the
same formatting helpers the PDF uses, so the body and the page cannot quote
different numbers; the tests below check the headline figures agree.

Nothing here sends a real email: the Resend SDK is stubbed throughout.
"""

from __future__ import annotations

import base64
import json

import pytest

from app import config, mailer
from app.utils import formatting as fmt
from app.utils import validation
from app.valuation import valuation_engine


@pytest.fixture
def analysed(saas):
    analysis = valuation_engine.run(saas)
    analysis.issues = validation.run(saas, analysis)
    return saas, analysis


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("REPORT_EMAIL_TO", "Info@tencapital.group")
    monkeypatch.setenv("RESEND_FROM", "TEN Capital Valuation <reports@tencapital.group>")
    monkeypatch.delenv("EMAIL_REPORTS", raising=False)


@pytest.fixture
def sent(monkeypatch):
    """Capture the payload the SDK would have posted."""
    captured: dict = {}

    class Emails:
        @staticmethod
        def send(payload):
            captured["payload"] = payload
            return {"id": "email-123"}

    module = type("resend", (), {"Emails": Emails, "api_key": ""})
    monkeypatch.setitem(__import__("sys").modules, "resend", module)
    return captured


def deliver(profile, analysis, **kwargs):
    return mailer.send_analysis(
        profile, analysis, b"%PDF-1.4 fake", b'{"ok":true}', stem="acme_valuation", **kwargs
    )


# --- Configuration ----------------------------------------------------------


class TestConfiguration:
    def test_delivery_is_off_without_a_key(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        assert mailer.is_enabled() is False

    def test_delivery_is_on_with_a_key(self, configured):
        assert mailer.is_enabled() is True

    @pytest.mark.parametrize("switch", ["0", "false", "no", "off", "OFF"])
    def test_delivery_can_be_switched_off_without_removing_the_key(
        self, configured, monkeypatch, switch
    ):
        monkeypatch.setenv("EMAIL_REPORTS", switch)
        assert mailer.is_enabled() is False

    def test_the_default_recipient_is_the_desk_inbox(self, monkeypatch):
        monkeypatch.delenv("REPORT_EMAIL_TO", raising=False)
        assert mailer.recipients() == ["Info@tencapital.group"]

    def test_recipients_can_be_a_comma_separated_list(self, monkeypatch):
        monkeypatch.setenv("REPORT_EMAIL_TO", "a@x.com, b@x.com ,")
        assert mailer.recipients() == ["a@x.com", "b@x.com"]

    def test_the_configured_sender_is_used(self, configured):
        assert mailer.sender() == "TEN Capital Valuation <reports@tencapital.group>"

    def test_the_fallback_sender_is_the_resend_sandbox(self, monkeypatch):
        monkeypatch.delenv("RESEND_FROM", raising=False)
        assert "resend.dev" in mailer.sender()


# --- What the message says --------------------------------------------------


class TestSubject:
    def test_it_names_the_company_and_the_range(self, analysed):
        profile, analysis = analysed
        subject = mailer.build_subject(profile, analysis)
        assert profile.identity.name in subject
        assert fmt.fmt_range(analysis.range.low, analysis.range.high) in subject

    def test_it_carries_the_verdict(self, analysed):
        profile, analysis = analysed
        assert analysis.requested_status.title() in mailer.build_subject(profile, analysis)

    def test_it_counts_errors_when_there_are_any(self, analysed):
        from app.models.valuation import ValidationIssue

        profile, analysis = analysed
        analysis.issues.append(ValidationIssue("ERROR", "test.rule", "Something is wrong."))
        assert "1 error" in mailer.build_subject(profile, analysis)

    def test_an_unnamed_company_still_produces_a_subject(self, analysed):
        profile, analysis = analysed
        profile.identity.name = ""
        assert "Unnamed company" in mailer.build_subject(profile, analysis)


class TestBodies:
    def test_the_html_quotes_the_same_figures_as_the_page(self, analysed):
        """The email must not become a second source of truth."""
        profile, analysis = analysed
        body = mailer.build_html(profile, analysis, {})
        for value in (analysis.range.low, analysis.range.base, analysis.range.high):
            assert fmt.fmt_money(value) in body
        assert fmt.fmt_money(analysis.requested_valuation) in body
        assert fmt.fmt_signed_pct(analysis.requested_gap_percent) in body
        assert analysis.requested_status in body
        assert analysis.confidence in body

    def test_the_text_body_carries_the_same_answer(self, analysed):
        profile, analysis = analysed
        body = mailer.build_text(profile, analysis, {})
        assert fmt.fmt_money(analysis.range.base) in body
        assert analysis.requested_status in body
        assert "ROUND & DILUTION" in body
        assert "ANALYST FLAGS" in body

    def test_every_method_that_ran_is_listed(self, analysed):
        profile, analysis = analysed
        html_body = mailer.build_html(profile, analysis, {})
        text_body = mailer.build_text(profile, analysis, {})
        for method in analysis.used_methods:
            assert fmt.method_label(method.name) in html_body
            assert fmt.method_label(method.name) in text_body

    def test_the_bridge_reconciles_in_the_body(self, analysed):
        profile, analysis = analysed
        body = mailer.build_html(profile, analysis, {})
        assert fmt.fmt_money(analysis.baseline.base) in body
        assert fmt.fmt_signed_pct(analysis.net_adjustment) in body

    def test_the_disclaimer_and_confidentiality_line_are_present(self, analysed):
        profile, analysis = analysed
        for body in (
            mailer.build_html(profile, analysis, {}),
            mailer.build_text(profile, analysis, {}),
        ):
            assert "Startup valuation is inherently subjective" in body
            assert config.CONFIDENTIALITY in body

    def test_missing_information_is_reported_not_hidden(self, analysed):
        profile, analysis = analysed
        profile.note_missing("Net revenue retention")
        assert "Net revenue retention" in mailer.build_html(profile, analysis, {})

    def test_no_comparables_is_stated(self, analysed):
        profile, analysis = analysed
        assert config.NO_COMPARABLES in mailer.build_html(profile, analysis, {})

    def test_a_clean_run_says_validation_found_nothing(self, priced_with_comparables):
        analysis = valuation_engine.run(priced_with_comparables)
        analysis.issues = []
        body = mailer.build_html(priced_with_comparables, analysis, {})
        assert "raised no errors or warnings" in body

    def test_flags_appear_when_there_are_any(self, analysed):
        profile, analysis = analysed
        body = mailer.build_html(profile, analysis, {})
        if analysis.issues:
            assert "Analyst flags" in body

    def test_html_is_escaped_against_injection_from_deck_text(self, analysed):
        profile, analysis = analysed
        profile.identity.name = '<script>alert("x")</script>'
        body = mailer.build_html(profile, analysis, {})
        assert "<script>" not in body
        assert "&lt;script&gt;" in body

    def test_the_run_summary_reports_provenance(self, analysed):
        profile, analysis = analysed
        body = mailer.build_html(profile, analysis, {"source": "deck.pdf", "slides": 14})
        assert "deck.pdf" in body
        assert "Provenance" in body
        assert config.LLM_MODEL in body


# --- Delivery ---------------------------------------------------------------


class TestDelivery:
    def test_a_send_returns_the_message_id(self, analysed, configured, sent):
        outcome = deliver(*analysed)
        assert outcome["sent"] is True
        assert outcome["id"] == "email-123"
        assert outcome["to"] == ["Info@tencapital.group"]

    def test_the_payload_carries_both_attachments(self, analysed, configured, sent):
        deliver(*analysed)
        attachments = sent["payload"]["attachments"]
        assert [item["filename"] for item in attachments] == [
            "acme_valuation.pdf",
            "acme_valuation.json",
        ]
        assert base64.b64decode(attachments[0]["content"]) == b"%PDF-1.4 fake"
        assert json.loads(base64.b64decode(attachments[1]["content"])) == {"ok": True}

    def test_the_payload_carries_both_bodies(self, analysed, configured, sent):
        deliver(*analysed)
        payload = sent["payload"]
        assert payload["html"].lstrip().startswith("<!doctype html>")
        assert payload["text"]
        assert payload["from"] == "TEN Capital Valuation <reports@tencapital.group>"
        assert payload["to"] == ["Info@tencapital.group"]

    def test_the_json_attachment_is_optional(self, analysed, configured, sent):
        profile, analysis = analysed
        mailer.send_analysis(profile, analysis, b"%PDF", None, stem="x")
        assert len(sent["payload"]["attachments"]) == 1

    def test_a_send_without_a_key_is_skipped_not_attempted(self, analysed, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        outcome = deliver(*analysed)
        assert outcome["sent"] is False
        assert "RESEND_API_KEY is not set" in outcome["skipped"]

    def test_a_disabled_send_says_which_switch_turned_it_off(
        self, analysed, configured, monkeypatch
    ):
        monkeypatch.setenv("EMAIL_REPORTS", "0")
        outcome = deliver(*analysed)
        assert outcome["sent"] is False
        assert "EMAIL_REPORTS" in outcome["skipped"]

    def test_an_empty_recipient_list_is_skipped(self, analysed, configured, monkeypatch):
        monkeypatch.setenv("REPORT_EMAIL_TO", "  ,  ")
        outcome = deliver(*analysed)
        assert outcome["sent"] is False
        assert "REPORT_EMAIL_TO is empty" in outcome["skipped"]

    def test_a_failure_is_reported_not_raised(self, analysed, configured, monkeypatch):
        """The whole point: a delivery failure must not cost the caller the analysis."""

        class Emails:
            @staticmethod
            def send(payload):
                raise RuntimeError("Resend is down")

        module = type("resend", (), {"Emails": Emails, "api_key": ""})
        monkeypatch.setitem(__import__("sys").modules, "resend", module)

        outcome = deliver(*analysed)
        assert outcome["sent"] is False
        assert "Resend is down" in outcome["error"]
        assert outcome["to"] == ["Info@tencapital.group"]

    def test_a_rejected_sender_is_reported_with_its_reason(
        self, analysed, configured, monkeypatch
    ):
        class Emails:
            @staticmethod
            def send(payload):
                raise ValueError("The example.com domain is not verified")

        module = type("resend", (), {"Emails": Emails, "api_key": ""})
        monkeypatch.setitem(__import__("sys").modules, "resend", module)
        assert "not verified" in deliver(*analysed)["error"]


# --- Wiring -----------------------------------------------------------------


class TestCliWiring:
    def test_the_no_email_flag_exists_and_defaults_off(self):
        from app import main

        args = main._parser().parse_args(["deck.pdf"])
        assert args.no_email is False
        assert main._parser().parse_args(["deck.pdf", "--no-email"]).no_email is True


class TestWebWiring:
    @pytest.fixture
    def client(self, monkeypatch, configured):
        from web import server

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        server._jobs.clear()
        server.flask_app.config["TESTING"] = True
        return server.flask_app.test_client()

    def test_healthz_reports_the_delivery_configuration(self, client):
        payload = client.get("/healthz").get_json()
        assert payload["email"]["enabled"] is True
        assert payload["email"]["to"] == ["Info@tencapital.group"]
        assert "reports@tencapital.group" in payload["email"]["from"]

    def test_healthz_reports_delivery_off_when_unconfigured(self, client, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        payload = client.get("/healthz").get_json()
        assert payload["email"]["enabled"] is False
        assert payload["email"]["to"] == []

    def test_the_page_offers_the_toggle_and_names_the_inbox(self, client):
        body = client.get("/").get_data(as_text=True)
        assert "Email the result" in body
        assert "Info@tencapital.group" in body

    def test_the_page_hides_the_toggle_when_unconfigured(self, client, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        body = client.get("/").get_data(as_text=True)
        assert "Email the result" not in body
