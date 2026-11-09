from agents.tema_q.topic_extractor import (
    clean_html_prose,
    is_valid_clinical_candidate,
    normalize_topic_key,
    add_ignored_topic,
    remove_ignored_topic,
    record_topic_note_selections,
    remove_verified_note_topic,
    get_ignored_topics_set,
    get_frequent_note_topics_set,
    check_topic_files_sync_diff,
    apply_topic_files_sync_update,
    IGNORE_LIST_FILE,
    _sync_ignore_list_file_from_db,
)
from core.database import get_db_connection
import contextlib


def test_clean_html_prose():
    assert clean_html_prose("<p>Patient has <b>diabetes mellitus</b> and <i>hypertension</i></p>") == "Patient has diabetes mellitus and hypertension"
    assert clean_html_prose("<span>heart</span>failure") == "heart failure"
    assert clean_html_prose("acute<b>pancreatitis</b>") == "acute pancreatitis"
    assert clean_html_prose("<span style=\"color:red\">Gout</span>") == "Gout"
    assert clean_html_prose("Asthma</em>COPD") == "Asthma COPD"


def test_is_valid_clinical_candidate_html_rejection():
    assert not is_valid_clinical_candidate("<span>Asthma", set())
    assert not is_valid_clinical_candidate("Asthma</span>", set())
    assert not is_valid_clinical_candidate("<br/>", set())
    assert is_valid_clinical_candidate("Asthma", set())


def test_mutual_exclusivity_and_removal():
    topic = "Unit Test Condition"
    k = normalize_topic_key(topic)

    # Add as ignored
    assert add_ignored_topic(topic)
    assert k in get_ignored_topics_set(force_refresh=True)
    assert k not in get_frequent_note_topics_set()

    # Select for notes -> must remove from ignored
    record_topic_note_selections([topic, topic, topic])
    assert k not in get_ignored_topics_set(force_refresh=True)
    assert k in get_frequent_note_topics_set()

    # Re-ignore -> must remove from frequent notes
    assert add_ignored_topic(topic)
    assert k in get_ignored_topics_set(force_refresh=True)
    assert k not in get_frequent_note_topics_set()

    # Cleanup
    assert remove_ignored_topic(topic)
    assert remove_verified_note_topic(topic)
    assert k not in get_ignored_topics_set(force_refresh=True)
    assert k not in get_frequent_note_topics_set()


def test_flat_file_sync_diff_and_apply():
    # Initial diff
    diff_init = check_topic_files_sync_diff()
    assert not diff_init["has_changes"]

    # Append test entry to Ignore_list.txt
    orig_content = IGNORE_LIST_FILE.read_text(encoding="utf-8")
    test_line = "unit_test_synthetic_ignored_entry"
    IGNORE_LIST_FILE.write_text(orig_content + f"\n{test_line}\n", encoding="utf-8")

    diff_after = check_topic_files_sync_diff()
    assert diff_after["has_changes"]
    assert diff_after["excluded"]["added_count"] == 1
    assert test_line in diff_after["excluded"]["added"]

    # Apply sync
    res = apply_topic_files_sync_update()
    assert res["status"] == "success"

    diff_synced = check_topic_files_sync_diff()
    assert not diff_synced["has_changes"]

    # Teardown: delete test entry from DB and resync file
    with contextlib.closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM ignored_topics WHERE original_text = ?", (test_line,))
        conn.commit()
        _sync_ignore_list_file_from_db(conn)

    diff_final = check_topic_files_sync_diff()
    assert not diff_final["has_changes"]


if __name__ == "__main__":
    test_clean_html_prose()
    print("test_clean_html_prose passed")
    test_is_valid_clinical_candidate_html_rejection()
    print("test_is_valid_clinical_candidate_html_rejection passed")
    test_mutual_exclusivity_and_removal()
    print("test_mutual_exclusivity_and_removal passed")
    test_flat_file_sync_diff_and_apply()
    print("test_flat_file_sync_diff_and_apply passed")
    print("ALL TESTS PASSED!")
