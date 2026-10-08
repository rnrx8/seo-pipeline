"""Keep explicit user limits distinct from automatic planning references."""


def planning_setting(job, outline):
    return job.get('word_count_setting') or (outline.get('meta') or {}).get('length_reference')
