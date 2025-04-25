import requests
import math
from preprocess import normalize_author_name, clean_title, strip_markup
from tqdm import tqdm
from pyzotero import zotero
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from datetime import datetime

# Configure logging
log_file = "script_log.txt"
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# === Config ===
ZOTERO_API_KEY = 'lDjfA0baCFO75mk0m7sA0D8G'
ZOTERO_USER_ID = '16452772'
LIBRARY_TYPE = 'user'  # or 'group' if you're working with a group library
COLLECTION_NAME = "Unidentified" 

# Initialize Zotero API client
zot = zotero.Zotero(ZOTERO_USER_ID, LIBRARY_TYPE, ZOTERO_API_KEY)


# === SIMILARITY LOGIC ===

def compute_similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()

def compare_metadata(item, target):
    score = 0
    if not isinstance(item, dict) or not isinstance(target, dict):
        return score

    if item.get('DOI') and target.get('DOI') and item['DOI'].strip().lower() == target['DOI'].strip().lower():
        score += 5

    if item.get('date') and target.get('date') and item['date'] == target['date']:
        score += 2

    if item.get('itemType') == target.get('itemType'):
        score += 1

    # ✅ Normalize and convert creators to sets
    creators_a = {
        normalize_author_name(c.get('family') or c.get('lastName', ''))
        for c in item.get('creators', []) if isinstance(c, dict)
    }
    creators_b = {
        normalize_author_name(c.get('family') or c.get('lastName', ''))
        for c in target.get('creators', []) if isinstance(c, dict)
    }

    # ✅ Only use intersection if both are sets
    intersection = creators_a.intersection(creators_b)
    if len(intersection) >= 2 or len(intersection) / max(len(creators_b), 1) >= 0.5:
        score += 2

    return score

def zotero_query(query_string, limit=15):
    try:
        return zot.items(q=strip_markup(query_string), limit=limit)
    except Exception as e:
        print(f"Zotero API error: {e}")
        return []

def evaluate_match(entry, cleaned_target_title, target_metadata):
    data = entry.get('data', {})
    candidate_title = data.get('title', '')
    cleaned_candidate_title = clean_title(candidate_title)

    # ✅ Exact DOI match = auto match
    if data.get('DOI') and target_metadata.get('DOI'):
        if data['DOI'].strip().lower() == target_metadata['DOI'].strip().lower():
            return {
                'title': candidate_title,
                'similarity': 1.0,
                'score': 999,  # High score for exact DOI match
                'DOI': data['DOI'],
                'ISSN': data.get('ISSN'),
                'creators': data.get('creators'),
                'date': data.get('date'),
                'itemType': data.get('itemType'),
                'key': entry.get('key')
            }

    # ✅ Exact ISSN match = auto match
    if data.get('ISSN') and target_metadata.get('ISSN'):
        if data['ISSN'].strip().lower() == target_metadata['ISSN'].strip().lower():
            return {
                'title': candidate_title,
                'similarity': 1.0,
                'score': 999,  # High score for exact ISSN match
                'DOI': data.get('DOI'),
                'ISSN': data.get('ISSN'),
                'creators': data.get('creators'),
                'date': data.get('date'),
                'itemType': data.get('itemType'),
                'key': entry.get('key')
            }

    # Check title similarity and metadata score
    similarity = compute_similarity(cleaned_target_title, cleaned_candidate_title)
    metadata_score = compare_metadata(data, target_metadata)

    if similarity >= 0.80 or (similarity >= 0.76 and metadata_score >= 5):
        return {
            'title': candidate_title,
            'similarity': similarity,
            'score': metadata_score,
            'DOI': data.get('DOI'),
            'ISSN': data.get('ISSN'),
            'creators': data.get('creators'),
            'date': data.get('date'),
            'itemType': data.get('itemType'),
            'key': entry.get('key')
        }

    return None

def find_matching_titles(title, metadata, query_len=80):
    # Remove leading articles from the title
    def remove_leading_articles(text):
        articles = {"the", "a", "an"}
        words = text.split()
        if words and words[0].lower() in articles:
            return " ".join(words[1:])
        return text

    cleaned_title = clean_title(title)
    cleaned_title = remove_leading_articles(cleaned_title)  # Remove leading articles
    query_string = cleaned_title[:query_len].strip()

    print(f"🔍 Searching with: '{query_string}' (truncated from full title)")

    # === Sequential querying for title ===
    queries = [query_string]
    search_results = []

    for query in queries:
        search_results.extend(zotero_query(query))

    # === Match evaluation (sequential) ===
    matches = []
    for item in search_results:
        match = evaluate_match(item, cleaned_title, metadata)
        if match:
            matches.append(match)

    if matches:
        matches.sort(key=lambda x: (x['score'], x['similarity']), reverse=True)
        return matches

    # === Fallback: Query by creators if no matches found ===
    creators = metadata.get('creators', [])
    if creators:
        # Use top two creators at most
        creator_names = [
            normalize_author_name(c.get('family') or c.get('lastName', ''))
            for c in creators if isinstance(c, dict)
        ]
        creator_query = " ".join(creator_names).strip()
        if creator_query:
            print(f"🔍 No matches found by title. Searching with creators: '{creator_query}'")
            search_results = zotero_query(creator_query)
            for item in search_results:
                match = evaluate_match(item, cleaned_title, metadata)
                if match:
                    matches.append(match)

            if matches:
                matches.sort(key=lambda x: (x['score'], x['similarity']), reverse=True)

    return matches


# ==== OA FETCHING ====
def fetch_oa_data(baseurl, per_page, institution_tag, sort_param, pages=1):
    headers = {"Content-Type": "text/plain"}
    zts_search = "http://127.0.0.1:1969/web"
    all_items = []

    for page in range(1, pages + 1):
        query = f"{baseurl}{per_page}&page={page}&filter={institution_tag}&{sort_param}"
        try:
            response = requests.post(zts_search, data=query, headers=headers)
            response.raise_for_status()
            all_items.extend(response.json())
        except requests.RequestException as e:
            print(f"[ERROR] Failed to fetch page {page}: {e}")
    return all_items

# ==== ADD TO ZOTERO ====
def add_items_to_zotero(filtered_items):
    batches = [filtered_items[i:i + 50] for i in range(0, len(filtered_items), 50)]

    # Find or create collection
    collections = zot.collections()
    collection_key = next(
        (c['key'] for c in collections if c['data']['name'] == COLLECTION_NAME), None
    )

    if not collection_key:
        created = zot.create_collection({'name': COLLECTION_NAME})
        success = created.get('success', {})
        collection_key = list(success.values())[0]['key'] if isinstance(success, dict) else success[0]['key']
        print(f"[INFO] Created collection: {COLLECTION_NAME} ({collection_key})")

    for batch in tqdm(batches, desc="Adding items to Zotero", total=len(batches)):
        response = zot.create_items(batch)
        item_keys = list(response.get('success', {}).values())

        if not item_keys:
            print("[ERROR] No items were successfully added.")
            continue

        full_items = zot.items(itemKey=','.join(item_keys))
        for item in full_items:
            zot.addto_collection(collection_key, item)

def adaptive_query(title, metadata, thresholds=[80, 40, 20]):
    for length in thresholds:
        results = find_matching_titles(title, metadata, query_len=length)
        if results:
            return results
    return []

if __name__ == '__main__':
    baseurl = "https://api.openalex.org/works?"
    institution_tag = "institutions.id:I35462925"
    per_page = "per-page=50"
    sort_param = "sort=publication_year:desc"

    # Log script start
    logging.info("Script started.")
    logging.info(f"Base URL: {baseurl}")
    logging.info(f"Institution Tag: {institution_tag}")
    logging.info(f"Sort Parameter: {sort_param}")

    # Fetch total items
    try:
        meta_resp = requests.get("https://api.openalex.org/institutions/I35462925")
        meta_resp.raise_for_status()
        total_items = meta_resp.json().get("works_count", 0)
        logging.info(f"Total items fetched from OpenAlex: {total_items}")
    except requests.RequestException as e:
        logging.error(f"Failed to fetch total items from OpenAlex: {e}")
        total_items = 0

    # Load previously saved total_items value
    total_items_tracker_file = "total_items_tracker.txt"
    try:
        with open(total_items_tracker_file, "r") as f:
            previous_total_items = int(f.read().strip())
        logging.info(f"Previous total items: {previous_total_items}")
    except (FileNotFoundError, ValueError):
        previous_total_items = 0
        logging.warning("No previous total_items value found. Assuming 0.")

    # Calculate the number of new items to fetch
    new_items_to_fetch = max(0, total_items - previous_total_items)
    pages_to_fetch = math.ceil(new_items_to_fetch / 50)
    logging.info(f"New items to fetch: {new_items_to_fetch}, Pages to fetch: {pages_to_fetch}")

    # Save the current total_items value for the next run
    with open(total_items_tracker_file, "w") as f:
        f.write(str(total_items))
    logging.info(f"Saved current total_items value: {total_items}")

    # Fetch data if there are new items to fetch
    if new_items_to_fetch > 0:
        try:
            oa_items = fetch_oa_data(baseurl, per_page, institution_tag, sort_param, pages_to_fetch)
            logging.info(f"Fetched {len(oa_items)} new items from OpenAlex.")
        except Exception as e:
            logging.error(f"Error fetching data from OpenAlex: {e}")
            oa_items = []

        filtered_items = []
        processed_titles = set()  # Set to track processed titles

        for item in oa_items:
            title = item.get('title', '').strip().lower()  # Normalize title for comparison
            if not title:
                logging.warning(f"Skipping item without a title: {item}")
                continue  # Skip this item if it doesn't have a title

            if title in processed_titles:
                logging.info(f"Skipping duplicate item with title: {title}")
                continue  # Skip duplicate titles

            processed_titles.add(title)  # Mark this title as processed

            creators = item.get('creators', [])
            year = item.get('date', '')
            doi = item.get('DOI', '')
            artype = item.get('type', '')
            metadata = {
                'title': title,
                'DOI': doi,
                'date': year,
                'itemType': artype,
                'creators': creators
            }
            logging.info(f"Processing item: {title}")
            results = adaptive_query(title, metadata)
            if results:
                logging.info(f"Potential matches found for '{title}':")
                for res in results:
                    logging.info(f"- {res['title']} (Similarity: {res['similarity']:.2f}, Score: {res['score']})")
            else:
                logging.info(f"No matches found for '{title}'.")
                filtered_items.append(item)

        try:
            add_items_to_zotero(filtered_items)
            logging.info("Filtered items successfully added to Zotero.")
        except Exception as e:
            logging.error(f"Error adding items to Zotero: {e}")
    else:
        logging.info("No new items to fetch.")

    # Log script end
    logging.info("Script finished.")
