import os,requests, re, time
from dataclasses import dataclass, field, replace
from typing import List, Iterable, Dict, Any, Optional, Literal
from datetime import date, datetime, timezone
import uuid
import pysqlite3 as sqlite3

NYT_ENDPOINT_URL = "https://api.nytimes.com/svc/books/v3/lists/overview.json"
NYT_API_KEY = "xxx"  # get your own key from https://developer.nytimes.com/
NYT_LIST_DATE = date.today().strftime("%Y-%m-%d")  # use today's date for current list

ABS_DB_PATH = r"/share/homes/xxx/docker/audiobookshelf/config/absdatabase.sqlite" # path to your ABS sqlite database
ABS_LIBRARY_NAME = "books" # your library name in ABS

ABS_COLLECTIONS = {
    "NY Times Best Sellers": ["combined-print-and-e-book-fiction", "combined-print-and-e-book-nonfiction", "hardcover-fiction", "hardcover-nonfiction", "trade-fiction-paperback", "paperback-nonfiction","advice-how-to-and-miscellaneous","childrens-middle-grade-hardcover","series-books","young-adult-hardcover","audio-fiction","audio-nonfiction","business-books","mass-market-monthly","middle-grade-paperback-monthly","young-adult-paperback-monthly"],
}

@dataclass
class Book:
    title: str
    authors: List[str] = field(default_factory=list)
    isbn: str = ""
    nyt_list: str = ""
    rank: int | None = None
    abs_book_id: str | None = None 

    def __post_init__(self):
        # normalize authors to a list of stripped strings
        object.__setattr__(self, "authors", [a.strip() for a in self.authors])

    def __str__(self) -> str:
        by = ", ".join(self.authors) if self.authors else "Unknown"
        return f"{self.title} — {by} (ISBN: {self.isbn}) (ABSID: {self.abs_book_id})"

    @classmethod
    def from_title_author_isbn(cls, title: str, authors: Iterable[str] | str, isbn: str):
        if isinstance(authors, str):
            authors = [authors]
        return cls(title=title, authors=list(authors), isbn=isbn)

def normalize_isbn(s: str) -> str:
    return re.sub(r"[^0-9Xx]", "", s or "").lower()

def make_author_like_pattern(name: str) -> str:
    """
    Build a case-insensitive LIKE pattern:
      "James Patterson" -> "%james%patterson%"
      "Trey Gowdy with Christopher Greyson" -> "%trey%gowdy%with%christopher%greyson%"
    We already parse authors, but this makes individual name lookups robust.
    """
    name = " ".join(name.split()).lower()
    # replace spaces with %, leave everything else as-is
    return f"%{name.replace(' ', '%')}%"

def normalize_title(s: str) -> str:
    # Keep it simple: collapse whitespace; compare lowercased
    return " ".join((s or "").split()).lower()

def parse_authors(s: str) -> list[str]:
    """Return a list of author names from strings like:
       'by James Patterson and Duane Swierczynski'."""
    
    _LEADING_BY = re.compile(r'^\s*by\b[:\s]*', re.IGNORECASE)
    _SEPARATORS = re.compile(r'\s*(?:,|\band\b|\bwith\b)\s*', re.IGNORECASE)

    if not s:
        return []
    s = _LEADING_BY.sub('', s.strip())
    parts = [p.strip() for p in _SEPARATORS.split(s) if p.strip()]
    # optional: de-dupe while preserving order (case-insensitive)
    seen, out = set(), []
    for p in parts:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out

def fetch_nyt_overview(published_date: str = NYT_LIST_DATE) -> dict:
    """Fetch NYT overview.json (all lists snapshot). Cache to disk."""
    resp = requests.get(NYT_ENDPOINT_URL, params={"api-key": NYT_API_KEY, "published_date": published_date}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Basic sanity
    if "results" not in data or "lists" not in data["results"]:
        raise ValueError("Unexpected NYT response shape; no results.lists found")

    return data

def overview_to_index(overview_json: dict) -> Dict[str, dict]:
    """
    Build an index: list_name_encoded -> the list object (with its books).
    Each list object contains 'books' array.
    """
    idx = {}
    for lst in overview_json.get("results", {}).get("lists", []):
        key = lst.get("list_name_encoded")
        if key:
            idx[key] = lst
    return idx

def extract_books_from_list(list_obj: dict) -> List[Book]:
    """Turn an NYT list object into a list[Book]."""
    out: List[Book] = []
    for b in list_obj.get("books", []):
        # NYT fields
        title = b.get("title") or ""
        # Prefer 'contributor' (often "by ...") else 'author'
        author = b.get("author") or b.get("contributor") or ""
        authors = parse_authors(author)

        # Prefer primary_isbn13; fall back to any ISBN-13 available
        isbn = b.get("primary_isbn13") or b.get("primary_isbn10") or ""
        publisher = b.get("publisher") or ""
        description = b.get("description") or ""
        rank = b.get("rank")  # rank on this list

        out.append(Book(
            title=title.strip(),
            authors=authors,
            isbn=str(isbn).strip(),
            #publisher=publisher.strip(),
            #description=description.strip(),
            nyt_list=list_obj.get("list_name_encoded", ""),
            rank=rank if isinstance(rank, int) else None,
        ))
    return out


def build_abs_collections(overview_json: dict, mapping: Dict[str, List[str]]) -> Dict[str, List[Book]]:
    """
    For each ABS collection name, union the books from its configured NYT lists.
    Deduplicate by ISBN-13; keep the best (lowest) rank seen across lists.
    """
    idx = overview_to_index(overview_json)
    collections: Dict[str, List[Book]] = {}

    for coll_name, list_slugs in mapping.items():
        merged: Dict[str, Book] = {}
        for slug in list_slugs:
            lst = idx.get(slug)
            if not lst:
                # Skip missing slugs; you could log a warning here
                continue
            books = extract_books_from_list(lst)
            for bk in books:
                if not bk.isbn:
                    # If no ISBN, key by title+authors as a fallback
                    key = f"{bk.title}|{','.join(bk.authors)}".lower()
                else:
                    key = bk.isbn

                if key not in merged:
                    merged[key] = bk
                else:
                    # Keep the better rank (lower number)
                    old = merged[key]
                    better_rank = (
                        bk if (bk.rank is not None and (old.rank is None or bk.rank < old.rank)) else old
                    )
                    # If the better is bk, overwrite; otherwise keep old
                    if better_rank is bk:
                        merged[key] = bk

        # Stabilize order: by rank first (None at the end), then title
        result = sorted(
            merged.values(),
            key=lambda b: (b.rank is None, b.rank if b.rank is not None else 1_000_000, b.title.lower()),
        )
        collections[coll_name] = result

    return collections

def open_abs_db(
    path: str = ABS_DB_PATH,
    mode: Literal["ro","rw","rwc"] = "rw",   # ro=read-only, rw=read/write existing, rwc=read/write create
    timeout: float = 5.0,                    # seconds to wait per statement for locks
    retries: int = 5,                        # how many times to retry connect on lock
    backoff: float = 0.25,                   # initial backoff between retries
) -> sqlite3.Connection:
    """
    Open with URI so we can set mode. Retries on 'database is locked' or 'busy'.
    """
    uri = f"file:{path}?mode={mode}&cache=shared"
    last_exc = None
    for attempt in range(retries):
        try:
            conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=timeout,          # per-statement wait
                isolation_level=None,     # autocommit; control txns explicitly
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            # Apply busy timeout too (ms). Redundant with connect(timeout), but harmless.
            conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)};")
            return conn
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            last_exc = e
            if "locked" in msg or "busy" in msg:
                time.sleep(backoff * (2 ** attempt))  # exponential backoff
                continue
            raise
    raise last_exc

def find_book_id_by_isbn(conn: sqlite3.Connection, isbn: str, library_id: str) -> Optional[str]:
    if not isbn:
        return None
    n = normalize_isbn(isbn)
    if not n:
        return None
    row = conn.execute(
        """
        SELECT b.id
        FROM books b
        WHERE 
            REPLACE(LOWER(COALESCE(b.isbn,'')),'-','') = ?
            AND EXISTS (
                SELECT 1 FROM libraryItems li WHERE li.mediaId = b.id AND li.libraryId = ?
            )
        LIMIT 1
        """,
        (n,library_id),
    ).fetchone()
    return row["id"] if row else None

def find_book_id_by_author_title(
    conn: sqlite3.Connection,
    title: str,
    authors: Iterable[str],
    library_id: str
) -> Optional[str]:
    """
    Tries (any author LIKE pattern) AND exact title match (case-insensitive).
    Loops authors, returns first hit. You can tighten to “all authors” later if needed.
    """
    tnorm = normalize_title(title)
    if not tnorm:
        return None

    sql = """
    SELECT b.id
    FROM books b
    WHERE LOWER(b.title) = ?
      AND EXISTS (
            SELECT 1
            FROM bookAuthors ba
            JOIN authors a ON a.id = ba.authorId
            WHERE ba.bookId = b.id
              AND LOWER(a.name) LIKE ?
      )
      AND EXISTS (
        SELECT 1 FROM libraryItems li WHERE li.mediaId = b.id AND li.libraryId = ?
        )
    LIMIT 1
    """

    for a in authors or []:
        pat = make_author_like_pattern(a)
        row = conn.execute(sql, (tnorm, pat, library_id)).fetchone()
        if row:
            return row["id"]
    return None

def resolve_abs_id_for_book(conn: sqlite3.Connection, book, library_id: str) -> Optional[str]:
    """
    book: your NYT Book object (with fields: title, authors, isbn)
    Returns ABS books.id or None.
    """
    # 1) ISBN
    bid = find_book_id_by_isbn(conn, getattr(book, "isbn", ""), library_id)
    if bid:
        return bid

    # 2) Author + exact title (case-insensitive)
    bid = find_book_id_by_author_title(conn, getattr(book, "title", ""), getattr(book, "authors", []), library_id)
    if bid:
        return bid

    # (Optional) 3) Title-only fallback (case-insensitive)
    # row = conn.execute("SELECT id FROM books WHERE LOWER(title)=? LIMIT 1", (normalize_title(book.title),)).fetchone()
    # if row:
    #     return row["id"]

    return None

def enrich_books_with_abs_ids(books, library_id: str, db_path: str = ABS_DB_PATH):
    """
    For a list of Book objects, set/attach abs_book_id.
    """
    conn = open_abs_db(db_path)
    try:
        out = []
        for b in books:
            abs_id = resolve_abs_id_for_book(conn, b, library_id)

            setattr(b, "abs_book_id", abs_id)
            out.append(b)
        return out
    finally:
        conn.close()

def utc_now_sql() -> str:
    # '2025-09-27 19:34:35.791 +00:00'  (milliseconds + UTC offset)
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:23] + " +00:00"

def new_uuid() -> str:
    return str(uuid.uuid4())

def get_library_id(conn: sqlite3.Connection, library_name: str) -> str:
    row = conn.execute(
        "SELECT id FROM libraries WHERE name = ? LIMIT 1", (library_name,)
    ).fetchone()
    if not row:
        raise RuntimeError(f"libraries.name='{library_name}' not found")
    return row["id"]

def get_or_create_collection(
    conn: sqlite3.Connection,
    collection_name: str,
    library_id: str,
) -> str:
    """
    Return collections.id for `collection_name`. If missing, INSERT.
    If present, UPDATE updatedAt only.
    """
    now = utc_now_sql()

    # Try to find existing
    row = conn.execute(
        "SELECT id FROM collections WHERE name = ? LIMIT 1", (collection_name,)
    ).fetchone()
    if row:
        coll_id = row["id"]
        conn.execute(
            "UPDATE collections SET updatedAt = ? WHERE id = ?",
            (now, coll_id),
        )
        return coll_id

    # Insert new
    coll_id = new_uuid()
    conn.execute(
        """
        INSERT INTO collections (id, name, description, createdAt, updatedAt, libraryId)
        VALUES (?, ?, NULL, ?, ?, ?)
        """,
        (coll_id, collection_name, now, now, library_id),
    )
    return coll_id

def replace_collection_books(
    conn: sqlite3.Connection,
    collection_id: str,
    books: Iterable,  # iterable of Book objs, each with .abs_book_id
) -> None:
    """
    Delete existing rows for this collection and insert distinct books in input order.
    Skips any book without abs_book_id. Orders 1..N with no gaps.
    """
    now = utc_now_sql()

    # remove any existing rows for this collection
    conn.execute("DELETE FROM collectionBooks WHERE collectionId = ?", (collection_id,))

    # stable de-dup by abs_book_id (keep first occurrence)
    seen = set()
    unique_books = []
    for b in books:
        abs_id = getattr(b, "abs_book_id", None)
        if not abs_id:
            continue
        if abs_id in seen:
            continue
        seen.add(abs_id)
        unique_books.append(abs_id)  # store just the id; we only need bookId here

    if not unique_books:
        return

    # build payload with contiguous "order" starting at 1
    payload = [
        (new_uuid(), i, now, book_id, collection_id)
        for i, book_id in enumerate(unique_books, start=1)
    ]

    conn.executemany(
        """
        INSERT INTO collectionBooks (id, "order", createdAt, bookId, collectionId)
        VALUES (?, ?, ?, ?, ?)
        """,
        payload,
    )

def upsert_abs_collection_with_books(
    conn: sqlite3.Connection,
    abs_collection_name: str,
    books_for_collection: list,
    library_name: str = ABS_LIBRARY_NAME,
    library_id: Optional[str] = None
) -> str:
    """
    Ensure the collection exists/updated, then replace its collectionBooks rows.
    Returns the collection id.
    """
    lib_id = library_id if library_id is not None else get_library_id(conn, library_name)
    coll_id = get_or_create_collection(conn, abs_collection_name, lib_id)
    replace_collection_books(conn, coll_id, books_for_collection)
    return coll_id

def main():
    overview = fetch_nyt_overview()
    collections = build_abs_collections(overview, ABS_COLLECTIONS)
    lib_id = None

    # enrich with ABS ids and remove books where ABS id not found
    with open_abs_db() as conn:
        lib_id = get_library_id(conn, ABS_LIBRARY_NAME)

        conn.isolation_level = None          # manual transaction
        conn.execute("BEGIN IMMEDIATE;")     # lock for consistent updates
        try:
            # 1) enrich and filter (keep only books we can resolve)
            for name, books in collections.items():
                seen = set()
                kept = []
                for b in books:
                    abs_id = resolve_abs_id_for_book(conn, b, lib_id)
                    if not abs_id or abs_id in seen:
                        continue
                    b.abs_book_id = abs_id
                    seen.add(abs_id)
                    kept.append(b)
                collections[name] = kept

            # 2) upsert each ABS collection and replace its rows
            for name, books in collections.items():
                coll_id = upsert_abs_collection_with_books(conn, name, books, library_id=lib_id)
                print(f"Updated collection '{name}' (id={coll_id}) with {len(books)} books")

            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise




if __name__ == "__main__":
    main()