import re
from bs4 import BeautifulSoup
from unidecode import unidecode

# === TEXT CLEANING UTILITIES ===

def strip_markup(text):
    """Remove HTML, XML, and LaTeX-style markup."""
    if not isinstance(text, str):
        return text

    # Remove HTML tags
    text = BeautifulSoup(text, "html.parser").get_text()

    # Remove inline LaTeX or math-like content
    text = re.sub(r'\$.*?\$', '', text)              # TeX math in $
    text = re.sub(r'\\\(.*?\\\)', '', text)          # TeX math in \( \)

    return text.strip()

def clean_mathml(text):
    """Remove MathML tags like <mml:...>...</mml:...>"""
    if not isinstance(text, str):
        return text

    # Remove entire <mml:...>...</mml:...> blocks
    text = re.sub(r'<mml:[^>]+>.*?</mml:[^>]+>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove any lingering single tags
    text = re.sub(r'</?mml:[^>]+>', '', text, flags=re.IGNORECASE)

    return text

def collapse_whitespace(text):
    """Normalize all whitespace."""
    if not isinstance(text, str):
        return text
    return re.sub(r'\s+', ' ', text).strip()

def clean_title(title):
    """Full title cleanup pipeline."""
    title = strip_markup(title)
    title = clean_mathml(title)
    title = re.sub(r'<[^>]+>', '', title)  # Remove any leftover HTML/XML
    title = re.sub(r'[\u2010\u2011\u2012\u2013\u2014\u2212]', '-', title)  # unify dash types
    title = re.sub(r'\s*-\s*', '-', title)  # tighten hyphen spacing
    title = re.sub(r'[\.,;:!?]+(?=\s|$)', '', title)  # remove trailing punctuation
    title = collapse_whitespace(title)
    return title


# === AUTHOR NORMALIZATION ===

def normalize_author_name(name):
    name = unidecode(name.lower().strip())
    name = re.sub(r'[^\w\s]', '', name)  # remove punctuation
    name_parts = name.split()
    # Try to return a set of initials + last name as a fallback
    if len(name_parts) >= 2:
        return f"{name_parts[-1]} {' '.join(p[0] for p in name_parts[:-1])}"
    return name

def extract_author_last_names(creators):
    """Safely extract family/last names from creator lists."""
    if not isinstance(creators, list):
        return set()
    return {
        normalize_author_name(c.get("family") or c.get("lastName", ""))
        for c in creators if isinstance(c, dict)
    }


# === METADATA STANDARDIZER ===

def normalize_metadata(raw):
    """Standardize relevant metadata fields for reliable comparison."""
    return {
        'DOI': raw.get('DOI', '').strip().lower() if isinstance(raw.get('DOI'), str) else '',
        'date': raw.get('date') or raw.get('issued', {}).get('date-parts', [[None]])[0][0],
        'itemType': raw.get('type') or raw.get('itemType'),
        'creators': extract_author_last_names(raw.get('author') or raw.get('creators', [])),
        'title': clean_title(raw.get('title', ''))
    }
