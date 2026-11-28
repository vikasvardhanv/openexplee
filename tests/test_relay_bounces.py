from app.engine.replies import relay_bounced_addresses

LOG = """\
2026-11-22T18:56:12+00:00 INFO postfix/smtp[4168]: B3512418C9: to=<Jane.Doe@Example.com>, relay=mx.example.com[192.0.2.1]:25, dsn=5.1.1, status=bounced (host said: 550 5.1.1 user unknown)
2026-11-22T18:57:00+00:00 INFO postfix/smtp[4169]: C4612418D0: to=<ok@example.org>, relay=mx.example.org[192.0.2.2]:25, dsn=2.0.0, status=sent (250 ok)
2026-11-22T18:58:00+00:00 INFO postfix/smtp[4170]: D5712418E1: to=<later@example.net>, relay=none, dsn=4.4.1, status=deferred (connect timed out)
2026-11-22T18:59:00+00:00 INFO postfix/smtp[4171]: E6812418F2: to=<jane.doe@example.com>, relay=mx.example.com[192.0.2.1]:25, dsn=5.1.1, status=bounced (host said: 550 5.1.1 user unknown)
"""


def test_only_bounced_recipients_are_returned_once_in_lower_case():
    assert relay_bounced_addresses(LOG) == ["jane.doe@example.com"]


def test_empty_log_gives_no_addresses():
    assert relay_bounced_addresses("") == []
