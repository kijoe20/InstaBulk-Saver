import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests
import streamlit as st
from instaloader import Instaloader, Post
from instaloader import exceptions as insta_exceptions


# ==========================
# Data Models
# ==========================
@dataclass
class MediaItem:
	id: str
	type: str  # 'image' | 'video'
	shortcode: str
	preview_url: str
	download_url: str
	filename: str
	origin_url: str


# ==========================
# Core Logic
# ==========================
INSTAGRAM_SHORTCODE_PATTERNS = [
	r"https?://(www\.)?instagram\.com/p/([^/?#]+)/?",
	r"https?://(www\.)?instagram\.com/reel/([^/?#]+)/?",
	r"https?://(www\.)?instagram\.com/tv/([^/?#]+)/?",
	r"https?://(www\.)?instagram\.com/reels/([^/?#]+)/?",
]


def parse_input_urls(raw_text: str) -> List[str]:
	if not raw_text:
		return []
	# Split by comma or newline, strip whitespace
	candidates: List[str] = []
	for chunk in re.split(r"[\n,]", raw_text):
		value = chunk.strip()
		if value:
			candidates.append(value)
	# Normalize: remove URL params/fragments; keep only Instagram post-like URLs; de-duplicate preserving order
	seen: set = set()
	result: List[str] = []
	for url in candidates:
		url_no_query = re.split(r"[?#]", url)[0]
		if any(re.match(pat, url_no_query) for pat in INSTAGRAM_SHORTCODE_PATTERNS):
			if url_no_query not in seen:
				seen.add(url_no_query)
				result.append(url_no_query)
	return result


def _extract_shortcode(url: str) -> Optional[str]:
	for pat in INSTAGRAM_SHORTCODE_PATTERNS:
		m = re.match(pat, url)
		if m:
			return m.group(2)
	return None


def _build_loader(session_username: Optional[str], session_file_path: Optional[Path]) -> Instaloader:
	loader = Instaloader(
		download_comments=False,
		post_metadata_txt_pattern="",
		download_video_thumbnails=False,
		save_metadata=False,
	)
	# Polite user agent
	loader.context.user_agent = (
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
		"(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
	)
	if session_username and session_file_path and session_file_path.exists():
		try:
			loader.load_session_from_file(session_username, str(session_file_path))
		except (FileNotFoundError, ValueError, insta_exceptions.InstaloaderException):
			# Fall back to anonymous if session load fails
			pass
	return loader


def fetch_previews(
	input_urls: List[str],
	session_username: Optional[str] = None,
	session_file_path: Optional[Path] = None,
	sleep_seconds_between_requests: float = 2.0,
	progress_callback: Optional[Callable[[int, int, str], None]] = None,
	log_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[Dict[str, List[MediaItem]], Dict[str, str]]:
	"""
	Fetch preview info (without downloading files) for each post URL.
	Returns: (media_by_url, errors_by_url)
	"""
	loader = _build_loader(session_username, session_file_path)
	media_by_url: Dict[str, List[MediaItem]] = {}
	errors: Dict[str, str] = {}
	total = len(input_urls)
	for idx, url in enumerate(input_urls, start=1):
		if progress_callback:
			progress_callback(idx - 1, total, f"Fetching: {url}")
		try:
			shortcode = _extract_shortcode(url)
			if not shortcode:
				raise ValueError("Could not parse Instagram shortcode from URL")
			post = Post.from_shortcode(loader.context, shortcode)
			items: List[MediaItem] = []
			# Sidecar (carousel)
			is_sidecar = post.typename == "GraphSidecar"
			if is_sidecar:
				for index, node in enumerate(post.get_sidecar_nodes()):
					is_video = getattr(node, "is_video", False)
					preview_url = getattr(node, "display_url", None)
					download_url = getattr(node, "video_url", None) if is_video else getattr(node, "display_url", None)
					if not preview_url or not download_url:
						continue
					ext = ".mp4" if is_video else ".jpg"
					item_id = f"{shortcode}_{index}"
					filename = f"{shortcode}_{index}{ext}"
					items.append(
						MediaItem(
							id=item_id,
							type="video" if is_video else "image",
							shortcode=shortcode,
							preview_url=preview_url,
							download_url=download_url,
							filename=filename,
							origin_url=url,
						)
					)
			else:
				is_video = bool(getattr(post, "is_video", False))
				preview_url = getattr(post, "url", None) or getattr(post, "display_url", None)
				download_url = getattr(post, "video_url", None) if is_video else getattr(post, "url", None)
				if not preview_url or not download_url:
					raise ValueError("Unable to resolve media URLs for post")
				ext = ".mp4" if is_video else ".jpg"
				items.append(
					MediaItem(
						id=f"{shortcode}_0",
						type="video" if is_video else "image",
						shortcode=shortcode,
						preview_url=preview_url,
						download_url=download_url,
						filename=f"{shortcode}{ext}",
						origin_url=url,
					)
				)
			media_by_url[url] = items
			if log_callback:
				log_callback(f"Found {len(items)} item(s) in {url}")
		except insta_exceptions.InstaloaderException as exc:
			msg = f"{type(exc).__name__}: {exc}"
			errors[url] = msg
			if log_callback:
				log_callback(f"Error: {url} -> {msg}")
		except Exception as exc:
			errors[url] = str(exc)
			if log_callback:
				log_callback(f"Error: {url} -> {exc}")
		finally:
			if sleep_seconds_between_requests > 0 and idx < total:
				time.sleep(sleep_seconds_between_requests)
	if progress_callback:
		progress_callback(total, total, "Done")
	return media_by_url, errors


def _ensure_directory(path: Path) -> None:
	path.mkdir(parents=True, exist_ok=True)


def _sanitize_filename(name: str) -> str:
	return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def download_selected_images(
	selected_items: List[MediaItem],
	base_download_dir: Path,
	sleep_seconds_between_downloads: float = 0.5,
	progress_callback: Optional[Callable[[int, int, str], None]] = None,
	log_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[int, int, Path]:
	"""
	Downloads selected media. Returns: (saved_count, skipped_count, base_dir)
	"""
	saved = 0
	skipped = 0
	_headers = {
		"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
	}
	total = len(selected_items)
	for idx, item in enumerate(selected_items, start=1):
		if progress_callback:
			progress_callback(idx - 1, total, f"Downloading: {item.filename}")
		target_dir = base_download_dir / item.shortcode
		_ensure_directory(target_dir)
		filename = _sanitize_filename(item.filename)
		target_path = target_dir / filename
		if target_path.exists():
			skipped += 1
			if log_callback:
				log_callback(f"Skipped (exists): {target_path}")
		else:
			try:
				with requests.get(item.download_url, stream=True, headers=_headers, timeout=30) as r:
					r.raise_for_status()
					with open(target_path, "wb") as f:
						for chunk in r.iter_content(chunk_size=8192):
							if chunk:
								f.write(chunk)
				saved += 1
				if log_callback:
					log_callback(f"Saved: {target_path}")
			except requests.exceptions.RequestException as exc:
				if log_callback:
					log_callback(f"Error saving {filename}: {exc}")
			finally:
				if sleep_seconds_between_downloads > 0 and idx < total:
					time.sleep(sleep_seconds_between_downloads)
	if progress_callback:
		progress_callback(total, total, "Downloads complete")
	return saved, skipped, base_download_dir


# ==========================
# Streamlit UI
# ==========================
APP_TITLE = "Instagram Bulk Downloader (Local)"
BASE_DOWNLOAD_DIR = Path(os.getcwd()) / "downloads"
SESSION_DIR = Path(os.getcwd()) / ".sessions"


if "logs" not in st.session_state:
	st.session_state["logs"] = []
if "previews" not in st.session_state:
	st.session_state["previews"] = {}  # url -> List[MediaItem]
if "errors" not in st.session_state:
	st.session_state["errors"] = {}
if "parsed_urls" not in st.session_state:
	st.session_state["parsed_urls"] = []
if "session_username" not in st.session_state:
	st.session_state["session_username"] = ""
if "session_file_path" not in st.session_state:
	st.session_state["session_file_path"] = None


def _log(msg: str) -> None:
	st.session_state["logs"].append(msg)


def _progress_cb(done: int, total: int, message: str) -> None:
	# This will be wired to a Streamlit progress bar in the UI block
	pass


st.set_page_config(page_title=APP_TITLE, layout="wide")

st.title(APP_TITLE)

with st.sidebar:
	st.header("Authentication (optional)")
	session_username = st.text_input("Instagram username (for session)", value=st.session_state["session_username"])
	sessionfile = st.file_uploader("Instaloader session file", type=["session"], help="Upload a session file created by Instaloader for private content access")
	col_a, col_b = st.columns(2)
	with col_a:
		load_clicked = st.button("Load Session")
	with col_b:
		clear_session_clicked = st.button("Clear Session")
	if load_clicked:
		if session_username and sessionfile is not None:
			SESSION_DIR.mkdir(parents=True, exist_ok=True)
			session_path = SESSION_DIR / f"{_sanitize_filename(session_username)}.session"
			with open(session_path, "wb") as f:
				f.write(sessionfile.getbuffer())
			st.session_state["session_username"] = session_username
			st.session_state["session_file_path"] = str(session_path)
			st.success("Session loaded. It will be used for subsequent requests.")
		else:
			st.warning("Provide a username and select a session file.")
	if clear_session_clicked:
		st.session_state["session_username"] = ""
		st.session_state["session_file_path"] = None
		st.info("Cleared session. Using anonymous access.")

st.markdown("""
**Instructions**
- Paste Instagram post URLs separated by commas or new lines.
- Click Preview to list all images/videos found.
- Select desired media and click Download Selected.
""")

raw_input = st.text_area("Instagram post URLs", height=150, placeholder="https://www.instagram.com/p/XXXXXXXX/\nhttps://www.instagram.com/reel/XXXXXXXX/")

col1, col2, col3 = st.columns([1, 1, 2])
with col1:
	preview_clicked = st.button("Preview", type="primary")
with col2:
	select_all_clicked = st.button("Select All")
with col3:
	clear_all_clicked = st.button("Clear All")

progress_placeholder = st.empty()
log_expander = st.expander("Logs & Errors", expanded=False)

if preview_clicked:
	st.session_state["logs"] = []
	parsed = parse_input_urls(raw_input)
	st.session_state["parsed_urls"] = parsed
	if not parsed:
		st.warning("No valid Instagram post URLs found.")
	else:
		progress_bar = progress_placeholder.progress(0, text="Starting preview...")
		def _ui_progress(done: int, total: int, message: str) -> None:
			pct = int((done / total) * 100) if total else 0
			progress_bar.progress(pct, text=message)
		media_by_url, errors = fetch_previews(
			parsed,
			session_username=st.session_state.get("session_username") or None,
			session_file_path=Path(st.session_state.get("session_file_path")) if st.session_state.get("session_file_path") else None,
			sleep_seconds_between_requests=2.0,
			progress_callback=_ui_progress,
			log_callback=_log,
		)
		st.session_state["previews"] = media_by_url
		st.session_state["errors"] = errors
		progress_placeholder.empty()

# Render errors if any
errors = st.session_state.get("errors", {})
if errors:
	for bad_url, err in errors.items():
		st.warning(f"{bad_url}: {err}")

# Render preview grid and selection
all_items: List[MediaItem] = []
for url, items in st.session_state.get("previews", {}).items():
	all_items.extend(items)

if all_items:
	# Apply select/clear all clicks
	if select_all_clicked:
		for it in all_items:
			st.session_state[f"select_{it.id}"] = True
	if clear_all_clicked:
		for it in all_items:
			st.session_state[f"select_{it.id}"] = False

	st.subheader("Preview & Select")
	cols_per_row = 4
	cols = st.columns(cols_per_row)
	for idx, item in enumerate(all_items):
		with cols[idx % cols_per_row]:
			if item.type == "image":
				st.image(item.preview_url, caption=item.filename, use_column_width=True)
			else:
				st.image(item.preview_url, caption=f"[Video] {item.filename}", use_column_width=True)
			st.checkbox("Select", key=f"select_{item.id}", value=st.session_state.get(f"select_{item.id}", False))
			st.markdown(f"[Post]({item.origin_url})")

	dl_col1, dl_col2 = st.columns([1, 3])
	with dl_col1:
		download_clicked = st.button("Download Selected", type="secondary")
	with dl_col2:
		download_all_clicked = st.button("Download All")

	if download_all_clicked:
		for it in all_items:
			st.session_state[f"select_{it.id}"] = True
		download_clicked = True

	if download_clicked:
		selected = [it for it in all_items if st.session_state.get(f"select_{it.id}", False)]
		if not selected:
			st.info("No media selected.")
		else:
			progress_bar = progress_placeholder.progress(0, text="Starting downloads...")
			def _dl_progress(done: int, total: int, message: str) -> None:
				pct = int((done / total) * 100) if total else 0
				progress_bar.progress(pct, text=message)
			saved, skipped, base_dir = download_selected_images(
				selected,
				BASE_DOWNLOAD_DIR,
				sleep_seconds_between_downloads=0.5,
				progress_callback=_dl_progress,
				log_callback=_log,
			)
			progress_placeholder.empty()
			# Confirmation with clickable path
			folder_uri = Path(base_dir).resolve().as_uri()
			st.success(f"Saved {saved} file(s), skipped {skipped}. ")
			st.markdown(f"Open folder: [{str(base_dir.resolve())}]({folder_uri})")

with log_expander:
	if st.session_state.get("logs"):
		st.text("\n".join(st.session_state["logs"]))
	else:
		st.caption("No logs yet.")