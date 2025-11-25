import streamlit as st
import requests
import re
from urllib.parse import urlparse, quote_plus
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

st.set_page_config(page_title="Howard's Senior QA Job Finder", layout="wide")

# ---------------------------
# Defaults tuned to YOU
# ---------------------------
DEFAULT_KEYWORDS = [
    "Senior QA Engineer",
    "Senior Test Engineer",
    "QA Lead",
    "Senior Quality Engineer",
    "SDET",
    "Test Automation Engineer",
    "Systems Test Engineer",
    "Validation Engineer",
    "Integration & Test Engineer",
]
DEFAULT_TECH = [
    "Python", "Java", "Selenium", "Playwright", "Cypress",
    "API testing", "Postman", "CI/CD", "AWS", "Autonomous", "EV", "Aerospace"
]
DEFAULT_LOCATIONS = ["Remote", "San Jose, CA", "San Francisco, CA", "Bay Area, CA"]

# Example boards you can edit in UI
DEFAULT_COMPANY_BOARDS = [
    # Lever examples (public)
    "https://jobs.lever.co/wayve",
    "https://jobs.lever.co/nuro",
    # Greenhouse examples (public)
    "https://boards.greenhouse.io/lucidmotors",
    "https://boards.greenhouse.io/andurilindustries",
]

USER_AGENT = {
    "User-Agent": "Mozilla/5.0 (compatible; JobFinderBot/1.0; +https://example.com/bot)"
}

# ---------------------------
# Helpers
# ---------------------------
def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()

def safe_get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=USER_AGENT, timeout=timeout)
        if r.status_code == 200:
            return r
    except Exception:
        pass
    return None

def dedupe_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for j in jobs:
        key = (normalize_text(j.get("title","")), normalize_text(j.get("company","")), j.get("url",""))
        if key not in seen:
            seen.add(key)
            out.append(j)
    return out

def keyword_match(job: Dict[str, Any], keywords: List[str]) -> bool:
    hay = normalize_text(" ".join([
        job.get("title",""),
        job.get("company",""),
        job.get("location",""),
        job.get("description",""),
        " ".join(job.get("tags",[]) or [])
    ]))
    return any(normalize_text(k) in hay for k in keywords if k.strip())

def location_match(job: Dict[str, Any], locations: List[str], remote_ok: bool) -> bool:
    loc = normalize_text(job.get("location",""))
    if remote_ok and ("remote" in loc or loc == ""):
        return True
    return any(normalize_text(L) in loc for L in locations if L.strip())

def days_ago(date_str: str) -> Optional[int]:
    # expects ISO-ish date
    try:
        dt = datetime.fromisoformat(date_str.replace("Z","").replace("+00:00",""))
        return (datetime.utcnow() - dt).days
    except Exception:
        return None

# ---------------------------
# Lever Fetcher
# Lever public endpoint: https://api.lever.co/v0/postings/{company}?mode=json
# ---------------------------
@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_lever(company_slug: str) -> List[Dict[str, Any]]:
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    r = safe_get(url)
    if not r:
        return []
    data = r.json()
    jobs = []
    for it in data:
        jobs.append({
            "title": it.get("text"),
            "company": company_slug,
            "location": (it.get("categories") or {}).get("location",""),
            "team": (it.get("categories") or {}).get("team",""),
            "commitment": (it.get("categories") or {}).get("commitment",""),
            "url": it.get("hostedUrl") or it.get("applyUrl"),
            "description": (it.get("descriptionPlain") or "")[:1200],
            "posted_at": it.get("createdAt") and datetime.utcfromtimestamp(it["createdAt"]/1000).isoformat(),
            "source": "Lever",
            "tags": it.get("tags") or []
        })
    return jobs

# ---------------------------
# Greenhouse Fetcher
# Public endpoint: https://boards-api.greenhouse.io/v1/boards/{board}/jobs
# board is the greenhouse board slug
# ---------------------------
@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_greenhouse(board_slug: str) -> List[Dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs"
    r = safe_get(url)
    if not r:
        return []
    data = r.json()
    jobs = []
    for it in data.get("jobs", []):
        jobs.append({
            "title": it.get("title"),
            "company": board_slug,
            "location": (it.get("location") or {}).get("name",""),
            "team": (it.get("departments") or [{}])[0].get("name","") if it.get("departments") else "",
            "commitment": it.get("commitment",""),
            "url": it.get("absolute_url"),
            "description": "",  # GH api doesn't include full desc in list endpoint
            "posted_at": it.get("updated_at") or it.get("created_at"),
            "source": "Greenhouse",
            "tags": []
        })
    return jobs

# ---------------------------
# Optional: SerpAPI (Google Jobs-like)
# Requires your key; safe default is OFF unless key provided
# ---------------------------
@st.cache_data(ttl=60 * 30, show_spinner=False)
def fetch_serpapi_jobs(query: str, location: str, api_key: str) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_jobs",
        "q": query,
        "location": location,
        "api_key": api_key,
        "hl": "en",
    }
    try:
        r = requests.get(url, params=params, headers=USER_AGENT, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        jobs = []
        for it in data.get("jobs_results", []):
            jobs.append({
                "title": it.get("title",""),
                "company": it.get("company_name",""),
                "location": it.get("location",""),
                "team": "",
                "commitment": it.get("detected_extensions", {}).get("schedule_type",""),
                "url": it.get("related_links", [{}])[0].get("link") or it.get("apply_options", [{}])[0].get("link"),
                "description": (it.get("description","") or "")[:1200],
                "posted_at": it.get("detected_extensions", {}).get("posted_at",""),
                "source": "SerpAPI",
                "tags": []
            })
        return jobs
    except Exception:
        return []

def parse_board_url(url: str) -> Tuple[str, str]:
    """
    Returns (source, slug)
      - Lever board: https://jobs.lever.co/{slug}
      - Greenhouse board: https://boards.greenhouse.io/{slug}
    """
    p = urlparse(url)
    host = p.netloc.lower()
    path = p.path.strip("/").split("/")
    if "lever.co" in host and path:
        return ("Lever", path[0])
    if "greenhouse.io" in host and path:
        return ("Greenhouse", path[0])
    return ("Unknown", "")

def build_linkedin_search_link(query: str, location: str) -> str:
    q = quote_plus(query)
    l = quote_plus(location)
    return f"https://www.linkedin.com/jobs/search/?keywords={q}&location={l}"

# ---------------------------
# UI
# ---------------------------
st.title("🧪 Howard's Senior QA / Test Engineer Job Finder")
st.caption("Public boards + optional search API. LinkedIn scraping avoided; links generated instead.")

with st.sidebar:
    st.header("Search Settings")

    keywords_txt = st.text_area(
        "Keywords (one per line)",
        value="\n".join(DEFAULT_KEYWORDS),
        height=180
    )
    keywords = [k.strip() for k in keywords_txt.splitlines() if k.strip()]

    tech_txt = st.text_area(
        "Tech / Domain Boosters (optional)",
        value="\n".join(DEFAULT_TECH),
        height=140
    )
    tech_keywords = [k.strip() for k in tech_txt.splitlines() if k.strip()]

    locations = st.multiselect(
        "Locations to include",
        DEFAULT_LOCATIONS,
        default=["Remote", "San Jose, CA"]
    )
    remote_ok = st.checkbox("Include Remote roles", value=True)

    posted_within_days = st.slider("Posted within (days)", 1, 60, 14)

    st.divider()
    st.subheader("Company Boards")
    boards_txt = st.text_area(
        "Lever/Greenhouse board URLs (one per line)",
        value="\n".join(DEFAULT_COMPANY_BOARDS),
        height=160
    )
    board_urls = [b.strip() for b in boards_txt.splitlines() if b.strip()]

    st.divider()
    st.subheader("Optional Search API")
    serpapi_key = st.text_input("SerpAPI Key (optional)", type="password")
    serpapi_location = st.text_input("SerpAPI location", value="San Jose, CA")
    use_serpapi = st.checkbox("Use SerpAPI aggregation", value=False)

    st.divider()
    st.subheader("Run Presets")
    run_main = st.button("▶ Run Senior QA / Test Scan", use_container_width=True)
    run_systems = st.button("▶ Run Systems/Validation Scan", use_container_width=True)

# Presets hard-wired (your preference)
PRESET_MAIN = [
    "Senior QA Engineer",
    "Senior Test Engineer",
    "QA Lead",
    "Senior Quality Engineer",
    "SDET",
]
PRESET_SYSTEMS = [
    "Systems Test Engineer",
    "Validation Engineer",
    "SI&T Engineer",
    "Integration & Test Engineer",
]

if "last_jobs" not in st.session_state:
    st.session_state.last_jobs = []

def run_scan(preset: List[str]):
    all_jobs = []
    diagnostics = {"Lever": 0, "Greenhouse": 0, "SerpAPI": 0, "Unknown": 0}

    # Fetch boards
    for url in board_urls:
        src, slug = parse_board_url(url)
        if src == "Lever":
            jobs = fetch_lever(slug)
            diagnostics["Lever"] += len(jobs)
            all_jobs.extend(jobs)
        elif src == "Greenhouse":
            jobs = fetch_greenhouse(slug)
            diagnostics["Greenhouse"] += len(jobs)
            all_jobs.extend(jobs)
        else:
            diagnostics["Unknown"] += 1

    # Optional SerpAPI aggregation
    if use_serpapi and serpapi_key:
        q = " OR ".join([f'"{k}"' for k in preset])
        jobs = fetch_serpapi_jobs(q, serpapi_location, serpapi_key)
        diagnostics["SerpAPI"] += len(jobs)
        all_jobs.extend(jobs)

    all_jobs = dedupe_jobs(all_jobs)

    # Filter by keywords + locations + recency
    combined_keywords = preset + tech_keywords
    filtered = []
    for j in all_jobs:
        if not keyword_match(j, combined_keywords):
            continue
        if not location_match(j, locations, remote_ok):
            continue
        if j.get("posted_at"):
            age = days_ago(j["posted_at"])
            if age is not None and age > posted_within_days:
                continue
        filtered.append(j)

    st.session_state.last_jobs = filtered
    return filtered, diagnostics, len(all_jobs)

# Auto-run based on button clicks
if run_main:
    jobs, diag, raw_total = run_scan(PRESET_MAIN)
elif run_systems:
    jobs, diag, raw_total = run_scan(PRESET_SYSTEMS)
else:
    jobs = st.session_state.last_jobs
    diag = None
    raw_total = None

# ---------------------------
# Results + Diagnostics
# ---------------------------
colA, colB = st.columns([2, 1], gap="large")

with colB:
    st.subheader("🔎 Diagnostics")
    if diag:
        st.write(f"**Boards scanned:** {len(board_urls)}")
        st.write(f"**Raw jobs pulled:** {raw_total}")
        st.write("**By source:**")
        st.json(diag)
        st.caption("If raw pulled looks low, add more boards or enable SerpAPI.")
    else:
        st.info("Run a scan to see diagnostics.")

with colA:
    st.subheader(f"✅ Matches ({len(jobs)})")

    # Quick sort
    sort_by = st.selectbox("Sort by", ["Most recent (if known)", "Company A–Z", "Title A–Z"])
    if sort_by == "Company A–Z":
        jobs = sorted(jobs, key=lambda x: normalize_text(x.get("company","")))
    elif sort_by == "Title A–Z":
        jobs = sorted(jobs, key=lambda x: normalize_text(x.get("title","")))
    else:
        def sort_recent(j):
            a = days_ago(j.get("posted_at","")) if j.get("posted_at") else 9999
            return a
        jobs = sorted(jobs, key=sort_recent)

    # Display cards
    for j in jobs:
        title = j.get("title","(no title)")
        company = j.get("company","")
        location = j.get("location","")
        url = j.get("url","")
        source = j.get("source","")
        posted = j.get("posted_at","")
        age = days_ago(posted) if posted else None

        with st.container(border=True):
            st.markdown(f"### [{title}]({url})" if url else f"### {title}")
            st.write(f"**Company:** {company}  |  **Location:** {location or 'n/a'}  |  **Source:** {source}")
            if age is not None:
                st.caption(f"Posted ~{age} days ago")
            if j.get("team") or j.get("commitment"):
                st.caption(f"{j.get('team','')} {('• ' + j.get('commitment')) if j.get('commitment') else ''}".strip())

            if j.get("description"):
                st.write(j["description"])

    if len(jobs) == 0:
        st.warning("No matches yet. Add more boards, widen locations, or enable SerpAPI.")

# ---------------------------
# LinkedIn click-out (safe)
# ---------------------------
st.divider()
st.subheader("🔗 LinkedIn One-Click Searches (safe click-out)")
link_cols = st.columns(3)
for idx, k in enumerate(PRESET_MAIN):
    with link_cols[idx % 3]:
        for L in locations[:2] or ["Remote"]:
            st.link_button(f"{k} • {L}", build_linkedin_search_link(k, L), use_container_width=True)