from core.topic_filter import filter_topics, is_valid_topic


def test_stopword_and_noise_rejection():
    for invalid in ["does", "exits", "lists", "restarts", "skips", "a", "123", "---", ""]:
        assert is_valid_topic(invalid) is False, f"Expected {invalid!r} to be rejected"


def test_valid_domain_topics_accepted():
    raw = ["does", "Myocardial Infarction", "restarts", "myocardial infarction", "Python Asyncio"]
    assert filter_topics(raw) == ["Myocardial Infarction", "Python Asyncio"]
