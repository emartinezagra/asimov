"""Owns the policy for what may become persistent [MEM]: both the explicit
REMEMBER/FORGET commands and the passive extraction in context.py call
through here, so the sensitive-data guard only has to live in one place.
"""
import re

import db

# Deliberately broad and cheap (no LLM call): refusing a borderline case is
# always safer than accidentally persisting a credential.
_SENSITIVE_RE = re.compile(
    r"contrase|password|\btoken\b|api[\s_-]?key|clave secreta|\bpin\b|"
    r"tarjeta de cr[eé]dito|\bcvv\b|n[uú]mero de cuenta|\biban\b|credencial",
    re.IGNORECASE,
)


def is_sensitive(text):
    return bool(_SENSITIVE_RE.search(text or ""))


def remember(user_id, fact):
    fact = (fact or "").strip()
    if not fact:
        return {"status": "empty"}
    if is_sensitive(fact):
        return {"status": "refused_sensitive"}
    db.upsert_user_fact(user_id, fact)
    return {"status": "saved", "fact": fact}


def forget(user_id, text_fragment):
    matches = db.find_user_facts_by_text(user_id, text_fragment)
    if not matches:
        return {"status": "not_found"}
    if len(matches) > 1:
        return {"status": "ambiguous", "matches": [m[1] for m in matches]}
    fact_id, fact = matches[0]
    db.delete_user_fact(fact_id)
    return {"status": "deleted", "fact": fact}


def list_all(user_id):
    return db.get_user_facts(user_id)
