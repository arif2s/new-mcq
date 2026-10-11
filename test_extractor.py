from agents.topic_extractor import extract_topics

text = "The patient has dyspnea but no asthma. They were prescribed amoxicillin."
print("No refinement:", extract_topics(text, False, False))

# Check with dummy mock ZIM and Tantivy checks
