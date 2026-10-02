import re

def extract_topics_local(question: str, options: dict, strategy: str = 'regex') -> dict:
    if strategy == 'medspacy':
        try:
            import medspacy
            # medSpacy extraction logic
            nlp = medspacy.load()

            combined_text = question + " " + " ".join(options.values())
            doc = nlp(combined_text)

            # Filter out negated concepts
            topics = []
            for ent in doc.ents:
                if not ent._.is_negated:
                    topics.append(ent.text)

            if not topics:
                topics = ["General Medical Concept"]

            main_topic = topics[0] if topics else "General Medical Concept"

            # Remove duplicates while preserving order
            seen = set()
            unique_topics = [x for x in topics if not (x in seen or seen.add(x))]

            return {"main_topic": main_topic, "all_topics": unique_topics}
        except ImportError:
            # Fallback to regex if medspacy is not installed
            print("medspacy not installed, falling back to regex")
            strategy = 'regex'

    if strategy == 'regex':
        # Regex / Fast Heuristics extraction
        topics = []

        # Simple heuristic to extract capitalized words or medical-sounding terms (e.g. ending in -itis, -oma)
        def extract_from_text(text):
            matches = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b|\b[A-Za-z]+(?:itis|oma|osis|pathy|emia|plasia|algia)\b', text)
            return matches

        topics.extend(extract_from_text(question))
        for opt in options.values():
            topics.extend(extract_from_text(opt))

        # Filter out common stop words if any matched by accident
        stop_words = {"A", "An", "The", "In", "On", "At", "To", "Is", "Are", "Was", "Were", "It", "This", "That", "Which", "What", "When", "Where", "Why", "How", "None", "All", "Of", "These", "And", "Or", "But", "If"}

        filtered_topics = [m.strip() for m in topics if m.strip() not in stop_words]

        if not filtered_topics:
            filtered_topics = ["General Medical Concept"]

        main_topic = filtered_topics[0] if filtered_topics else "General Medical Concept"

        # Remove duplicates while preserving order
        seen = set()
        unique_topics = [x for x in filtered_topics if not (x in seen or seen.add(x))]

        return {"main_topic": main_topic, "all_topics": unique_topics}

    return {"main_topic": "General Medical Concept", "all_topics": []}
